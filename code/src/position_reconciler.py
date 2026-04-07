# pylint: disable=trailing-whitespace
"""C-03: Position reconciliation — extrait de MULTI_SYMBOLS.py (God-Object split).

Toutes les fonctions reçoivent leurs dépendances via _ReconcileDeps pour éviter
les imports circulaires et garantir la compatibilité avec les tests (patches ms.*).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from bot_config import config, extract_coin_from_pair
from email_templates import sell_executed_email
from exchange_client import _get_coin_balance, ExchangePort
from trade_journal import log_trade
from state_manager import update_pair_state

logger = logging.getLogger(__name__)


@dataclass
class _ReconcileDeps:
    """Injecte les dépendances runtime de l'orchestrateur (évite les imports circulaires)."""
    client: ExchangePort
    bot_state: Dict[str, Any]
    bot_state_lock: Any              # _bot_state_lock (RLock)
    save_fn: Callable                # save_bot_state
    send_alert_fn: Callable          # send_trading_alert_email
    place_sl_fn: Callable            # place_exchange_stop_loss_order
    get_exchange_info_fn: Callable   # get_cached_exchange_info


@dataclass
class _PairStatus:
    """C-07: Données lues depuis Binance pour une paire — résultat de _check_pair_vs_exchange."""
    backtest_pair: str
    real_pair: str
    coin_symbol: str
    coin_balance: float
    current_price: float
    pair_state: Dict[str, Any]
    has_real_balance: bool
    local_in_position: bool


def _check_pair_vs_exchange(
    pair_info: Dict[str, Any],
    deps: _ReconcileDeps,
) -> Optional[_PairStatus]:
    """C-07: Lecture seule — récupère l'état Binance + bot_state pour une paire.

    Retourne None si les données ne peuvent pas être récupérées (erreur loguée).
    """
    backtest_pair = pair_info.get('backtest_pair', '')
    real_pair = pair_info.get('real_pair', '')
    try:
        coin_symbol, _ = extract_coin_from_pair(real_pair)
    except Exception as e:
        logger.error(f"[RECONCILE] Impossible d'extraire coin/quote pour {real_pair}: {e}")
        return None
    try:
        account_info = deps.client.get_account()
        # C-13: helper centralisé free+locked
        _, _, _, coin_balance = _get_coin_balance(account_info, coin_symbol)
    except Exception as e:
        logger.error(f"[RECONCILE] Impossible de récupérer le solde Binance pour {coin_symbol}: {e}")
        return None
    pair_state: Dict[str, Any] = deps.bot_state.get(backtest_pair, {})
    local_in_position = pair_state.get('last_order_side') == 'BUY'  # C-06

    # Récupérer le prix courant pour évaluer la valeur du solde en USDC
    try:
        _ticker = deps.client.get_symbol_ticker(symbol=real_pair)
        current_price = float(_ticker.get('price', 0))
    except Exception:
        current_price = 0.0

    # Récupérer min_qty et min_notional pour cette paire
    from bot_config import config as _cfg_reconcile
    _min_qty_reconcile = _cfg_reconcile.reconcile_min_qty        # MI-05
    _min_notional_reconcile = _cfg_reconcile.reconcile_min_notional  # MI-05
    try:
        _exchange_info_r = deps.get_exchange_info_fn(deps.client)
        _sym_info_r = next(
            (s for s in _exchange_info_r['symbols']  # pylint: disable=unsubscriptable-object
             if s['symbol'] == real_pair), None
        )
        if _sym_info_r:
            _lot_f = next((f for f in _sym_info_r['filters'] if f['filterType'] == 'LOT_SIZE'), None)
            if _lot_f:
                _min_qty_reconcile = float(_lot_f.get('minQty', 0.01))
            _not_f = next((f for f in _sym_info_r['filters'] if f['filterType'] == 'NOTIONAL'), None)
            if _not_f:
                _min_notional_reconcile = float(_not_f.get('minNotional', 5.0))
    except Exception as _exc:
        logger.debug("[position_reconciler] récupération sym_info échouée: %s", _exc)

    # Position réelle : doit avoir assez de coins ET une valeur au-dessus de MIN_NOTIONAL
    _balance_value_usdc = coin_balance * current_price if current_price > 0 else 0.0
    has_real_balance = (
        coin_balance >= _min_qty_reconcile
        and _balance_value_usdc >= _min_notional_reconcile
    )
    return _PairStatus(
        backtest_pair=backtest_pair,
        real_pair=real_pair,
        coin_symbol=coin_symbol,
        coin_balance=coin_balance,
        current_price=current_price,
        pair_state=pair_state,
        has_real_balance=has_real_balance,
        local_in_position=local_in_position,
    )


def _handle_pair_discrepancy(status: _PairStatus, deps: _ReconcileDeps) -> None:
    """C-07: Traite le résultat de la vérification Binance/bot_state pour une paire."""
    # Aliases locaux pour ne pas modifier le corps (sécurité lors d'un refactor)
    backtest_pair = status.backtest_pair
    real_pair = status.real_pair
    coin_symbol = status.coin_symbol
    coin_balance = status.coin_balance
    _current_price = status.current_price
    pair_state = status.pair_state
    has_real_balance = status.has_real_balance
    local_in_position = status.local_in_position

    if has_real_balance and not local_in_position:
        logger.critical(
            f"[RECONCILE] POSITION ORPHELINE pour {backtest_pair}: "
            f"solde réel {coin_balance:.6f} {coin_symbol} NON enregistré dans bot_state!"
        )
        # Tenter de retrouver entry_price depuis l'historique Binance
        entry_price_restored = None
        try:
            all_orders = deps.client.get_all_orders(symbol=real_pair, limit=50)
            filled_buys = [
                o for o in all_orders
                if o.get('side') == 'BUY' and o.get('status') == 'FILLED'
            ]
            if filled_buys:
                last_buy = filled_buys[-1]
                exec_qty = float(last_buy.get('executedQty', 0) or 1)
                cum_quote = float(last_buy.get('cummulativeQuoteQty', 0) or 0)
                price_field = float(last_buy.get('price', 0) or 0)
                entry_price_restored = price_field if price_field > 0 else (cum_quote / exec_qty if exec_qty > 0 else None)
                logger.info(f"[RECONCILE] entry_price restauré: {entry_price_restored}")
        except Exception as e:
            logger.error(f"[RECONCILE] Impossible de récupérer l'historique d'ordres: {e}")
        # Restaurer l'état minimal
        with deps.bot_state_lock:
            update_pair_state(deps.bot_state, backtest_pair,
                              last_order_side='BUY', partial_taken_1=False, partial_taken_2=False)
            if entry_price_restored:
                update_pair_state(deps.bot_state, backtest_pair, entry_price=entry_price_restored)
        deps.save_fn(force=True)
        # Alerte immédiate
        try:
            deps.send_alert_fn(
                subject=f"[CRITIQUE] Position orpheline détectée au démarrage: {backtest_pair}",
                body_main=(
                    f"Position ouverte Binance ({coin_balance:.6f} {coin_symbol}) "
                    f"non enregistrée dans bot_state.\n\n"
                    f"entry_price restauré: {entry_price_restored}\n\n"
                    f"ACTION REQUISE: vérifier les stops manuellement sur Binance."
                ),
                client=deps.client,
            )
        except Exception as mail_err:
            logger.error(f"[RECONCILE] Envoi email critique impossible: {mail_err}")

    elif not has_real_balance and local_in_position:
        logger.warning(
            f"[RECONCILE] bot_state indique position ouverte pour {backtest_pair} "
            f"mais solde {coin_symbol} est ~0 (balance={coin_balance:.8f}, "
            f"valeur={coin_balance * _current_price:.2f} USDC) — réconciliation complète."
        )

        # Vérifier si le SL exchange a été FILLED (cause la plus probable)
        _sl_oid = pair_state.get('sl_order_id')
        _sl_fill_price = _current_price  # fallback
        _sl_exec_qty = 0.0
        _sl_was_filled = False
        if _sl_oid:
            try:
                _sl_info = deps.client.get_order(symbol=real_pair, orderId=_sl_oid)
                if _sl_info.get('status') == 'FILLED':
                    _sl_was_filled = True
                    _eq = float(_sl_info.get('executedQty', 0))
                    _cq = float(_sl_info.get('cummulativeQuoteQty', 0))
                    if _eq > 0:
                        _sl_exec_qty = _eq
                    if _eq > 0 and _cq > 0:
                        _sl_fill_price = _cq / _eq
                    logger.info(
                        "[RECONCILE] SL exchange %s FILLED — prix %.4f, qty %.8f",
                        _sl_oid, _sl_fill_price, _sl_exec_qty,
                    )
            except Exception as _sl_err:
                logger.warning("[RECONCILE] Impossible de vérifier SL %s: %s", _sl_oid, _sl_err)
        else:
            # Pas de sl_order_id → on ne peut pas confirmer un SL fill
            _sl_was_filled = False
            logger.info("[RECONCILE] Aucun sl_order_id pour %s — reset état sans email", backtest_pair)

        # Email d'alerte si SL a été filled
        if _sl_was_filled:
            _entry_px = pair_state.get('entry_price') or 0
            _pnl_pct = ((_sl_fill_price / _entry_px) - 1) * 100 if _entry_px > 0 else None
            _stop_loss_at = pair_state.get('stop_loss_at_entry')
            _is_ts = pair_state.get('trailing_stop_activated', False)
            _ts = pair_state.get('trailing_stop')
            if _is_ts and _ts:
                _stop_type = "TRAILING-STOP (dynamique)"
                _stop_desc = (
                    f"Prix max atteint : {pair_state.get('max_price', 0):.4f} USDC\n"
                    f"Trailing stop : {_ts:.4f} USDC"
                )
            else:
                _stop_type = "STOP-LOSS (fixe à 3×ATR)"
                _stop_desc = f"Stop-loss fixe : {_stop_loss_at:.4f} USDC" if _stop_loss_at else "N/A"

            extra = (
                f"DETAILS DU STOP (ordre exchange natif — détecté au redémarrage):\n"
                f"{_stop_desc}\n"
                f"Prix d'entree : {_entry_px:.4f} USDC\n"
                f"Timeframe : {pair_state.get('entry_timeframe', 'N/A')}\n"
                f"EMA : {pair_state.get('entry_ema1', '?')}/{pair_state.get('entry_ema2', '?')}\n"
                f"Scenario : {pair_state.get('entry_scenario', 'N/A')}"
            )
            subj, body = sell_executed_email(
                pair=real_pair, qty=_sl_exec_qty,
                price=_sl_fill_price,
                usdc_received=_sl_exec_qty * _sl_fill_price,
                sell_reason=_stop_type, pnl_pct=_pnl_pct,
                extra_details=extra,
            )
            try:
                deps.send_alert_fn(subject=subj, body_main=body, client=deps.client)
                logger.info("[RECONCILE] Email SL exchange envoyé pour %s", backtest_pair)
            except Exception as _email_err:
                logger.error("[RECONCILE] Échec envoi email SL: %s", _email_err)

            # Journal de trading
            try:
                _saved_entry = pair_state.get('entry_price') or 0.0
                _pnl = (_sl_fill_price - _saved_entry) * _sl_exec_qty if _saved_entry else None
                _pnl_pct_j = ((_sl_fill_price / _saved_entry) - 1) if _saved_entry else None
                logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                log_trade(
                    logs_dir=logs_dir, pair=real_pair, side='sell',
                    quantity=_sl_exec_qty, price=_sl_fill_price,
                    fee=_sl_exec_qty * config.taker_fee * _sl_fill_price,
                    scenario=pair_state.get('entry_scenario') or '',
                    timeframe=pair_state.get('entry_timeframe') or '',
                    pnl=_pnl, pnl_pct=_pnl_pct_j,
                    extra={'sell_reason': f'{_stop_type} (reconcile at startup)'},
                )
            except Exception as _j_err:
                logger.error("[RECONCILE] Erreur journal: %s", _j_err)

        # Reset complet du pair_state
        with deps.bot_state_lock:
            if backtest_pair in deps.bot_state:
                update_pair_state(
                    deps.bot_state, backtest_pair,
                    entry_price=None, max_price=None, stop_loss=None,
                    trailing_stop=None, trailing_stop_activated=False,
                    atr_at_entry=None, stop_loss_at_entry=None,
                    trailing_activation_price_at_entry=None,
                    initial_position_size=None,
                    last_order_side='SELL',
                    partial_taken_1=False, partial_taken_2=False,
                    breakeven_triggered=False,
                    entry_scenario=None, entry_timeframe=None,
                    entry_ema1=None, entry_ema2=None,
                    sl_order_id=None, sl_exchange_placed=False,
                )
                # A-3: cooldown post-SL si configuré
                _cd_candles = getattr(config, 'stop_loss_cooldown_candles', 0)
                if _cd_candles > 0 and _sl_was_filled:
                    _tf = pair_state.get('entry_timeframe') or '1h'
                    _TF_SEC: dict[str, int] = {'1m': 60, '5m': 300, '15m': 900, '30m': 1800,
                               '1h': 3600, '4h': 14400, '1d': 86400}
                    _candle_sec = _TF_SEC.get(_tf, 3600)
                    update_pair_state(
                        deps.bot_state, backtest_pair,
                        _stop_loss_cooldown_until=time.time() + (_cd_candles * _candle_sec),
                    )
        deps.save_fn(force=True)
        logger.info("[RECONCILE] État réinitialisé pour %s — prêt pour nouvel achat", backtest_pair)
    else:
        logger.info(
            f"[RECONCILE] {backtest_pair}: cohérent "
            f"(balance={coin_balance:.6f} {coin_symbol}, in_position={local_in_position})"
        )
        # Nettoyer les champs d'entrée stales quand pas de position réelle
        if not local_in_position and backtest_pair in deps.bot_state:
            _ps = deps.bot_state[backtest_pair]
            if _ps.get('entry_price') is not None or _ps.get('stop_loss_at_entry') is not None:
                logger.info("[RECONCILE] Nettoyage des champs d'entrée stales pour %s", backtest_pair)
                with deps.bot_state_lock:
                    update_pair_state(
                        deps.bot_state, backtest_pair,
                        entry_price=None, max_price=None, stop_loss=None,
                        trailing_stop=None, trailing_stop_activated=False,
                        atr_at_entry=None, stop_loss_at_entry=None,
                        trailing_activation_price_at_entry=None,
                        initial_position_size=None,
                        partial_taken_1=False, partial_taken_2=False,
                        breakeven_triggered=False,
                        entry_scenario=None, entry_timeframe=None,
                        entry_ema1=None, entry_ema2=None,
                        sl_order_id=None, sl_exchange_placed=False,
                    )
                deps.save_fn(force=True)
        # C-11: Position ouverte sans stop-loss sur Binance → repose automatique du SL
        if local_in_position:
            sl_price = pair_state.get('stop_loss_at_entry')
            if sl_price:
                try:
                    open_orders = deps.client.get_open_orders(symbol=real_pair)
                    stop_types = {'STOP_LOSS', 'STOP_LOSS_LIMIT', 'TAKE_PROFIT',
                                  'TAKE_PROFIT_LIMIT', 'OCO'}
                    has_stop = any(o.get('type', '') in stop_types for o in open_orders)

                    if not has_stop:
                        logger.warning(
                            "[RECONCILE C-11] Position ouverte pour %s sans stop-loss "
                            "sur Binance — repose automatique du SL à %.8f",
                            backtest_pair, sl_price,
                        )
                        # Snap coin_balance au stepSize
                        try:
                            _exchange_info = deps.get_exchange_info_fn(deps.client)
                            _sym_info = next(
                                (s for s in _exchange_info['symbols']  # pylint: disable=unsubscriptable-object
                                 if s['symbol'] == real_pair),
                                None,
                            )
                            _lot_filter = next(
                                (f for f in _sym_info['filters'] if f['filterType'] == 'LOT_SIZE'),
                                None,
                            ) if _sym_info else None
                            if _lot_filter:
                                _step_dec = Decimal(str(float(_lot_filter['stepSize'])))
                                _qty_dec = (Decimal(str(coin_balance)) // _step_dec) * _step_dec
                                _qty_str = str(_qty_dec)
                            else:
                                _qty_str = f"{coin_balance:.6f}"
                        except Exception as _info_err:
                            logger.warning(
                                "[RECONCILE C-11] stepSize error: %s — using raw balance", _info_err
                            )
                            _qty_str = f"{coin_balance:.6f}"

                        _sl_result = deps.place_sl_fn(real_pair, _qty_str, sl_price)
                        if _sl_result:
                            with deps.bot_state_lock:
                                deps.bot_state.setdefault(backtest_pair, {})['sl_order_id'] = (
                                    _sl_result.get('orderId')
                                )
                            deps.save_fn(force=True)
                            logger.info(
                                "[RECONCILE C-11] SL reposé avec succès pour %s: "
                                "orderId=%s qty=%s stop=%.8f",
                                backtest_pair, _sl_result.get('orderId'), _qty_str, sl_price,
                            )
                        else:
                            logger.error(
                                "[RECONCILE C-11] Échec repose SL pour %s — "
                                "vérifiez manuellement sur Binance.", backtest_pair
                            )
                    else:
                        logger.info(
                            "[RECONCILE C-11] Stop-loss déjà actif sur Binance pour %s ✓",
                            backtest_pair,
                        )
                except Exception as _resl_err:
                    logger.error(
                        "[RECONCILE C-11] Erreur vérification/repose SL pour %s: %s",
                        backtest_pair, _resl_err,
                    )


def reconcile_positions_with_exchange(
    crypto_pairs_list: List[Dict[str, Any]],
    deps: _ReconcileDeps,
) -> None:
    """Vérifie la cohérence entre bot_state et les positions réelles sur Binance.

    Appelé UNE FOIS au démarrage après load_bot_state() pour détecter toute
    position orpheline (ex: achat exécuté avant un crash, état non sauvegardé).
    En cas de divergence, restaure l'état minimal et envoie une alerte CRITICAL. (C-03)
    """
    logger.info("[RECONCILE] Vérification de la cohérence des positions...")
    for pair_info in crypto_pairs_list:
        status = _check_pair_vs_exchange(pair_info, deps)
        if status is not None:
            _handle_pair_discrepancy(status, deps)
    logger.info("[RECONCILE] Vérification terminée.")
