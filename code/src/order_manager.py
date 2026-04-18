# pylint: disable=trailing-whitespace
"""C-03 Phase 2: Moteur de trading — extrait de MULTI_SYMBOLS.py (God-Object split).

Toutes les fonctions reçoivent leurs dépendances via _TradingDeps pour
- éviter les imports circulaires
- garantir la compatibilité avec les tests (patches ms.*)

Signature type: fn(ctx: _TradeCtx, deps: _TradingDeps, ...) -> ...
"""
from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from bot_config import config
from display_ui import display_closure_panel, display_sell_signal_panel, display_buy_signal_panel
from email_templates import buy_executed_email, sell_executed_email
from exchange_client import _get_coin_balance, can_execute_partial_safely, ExchangePort
from state_manager import set_emergency_halt
from position_sizing import (
    compute_position_size_by_risk,  # kept for potential future use
)
from trade_journal import log_trade
from wal_logger import wal_write, OP_BUY_INTENT, OP_BUY_CONFIRMED, OP_SL_PLACED
from constants import (
    QTY_OVERSHOOT_TOLERANCE as _QTY_OVERSHOOT_TOLERANCE,
    SL_MAX_RETRIES,
    SL_BACKOFF_BASE,
    TIMEFRAME_SECONDS,
)

logger = __import__('logging').getLogger(__name__)


@dataclass
class _TradingDeps:
    """Injecte les dépendances runtime du moteur de trading (évite les imports circulaires).

    _make_trading_deps() dans MULTI_SYMBOLS.py lit les globaux au moment de l'appel,
    ce qui garantit que les patches de tests (monkeypatch.setattr(ms, ...)) sont
    effectifs dans les fonctions extraites.
    """
    # Runtime state
    client: ExchangePort
    bot_state: Dict[str, Any]
    bot_state_lock: Any              # _bot_state_lock (RLock)
    # Callables (patched in tests at ms.* level)
    save_fn: Callable                      # save_bot_state
    send_alert_fn: Callable                # send_trading_alert_email
    place_sl_fn: Callable                  # place_exchange_stop_loss_order
    market_sell_fn: Callable               # safe_market_sell
    market_buy_fn: Callable                # safe_market_buy
    update_daily_pnl_fn: Callable          # _update_daily_pnl
    is_loss_limit_fn: Callable             # _is_daily_loss_limit_reached
    gen_buy_checker_fn: Callable           # generate_buy_condition_checker
    gen_sell_checker_fn: Callable          # generate_sell_condition_checker
    check_order_executed_fn: Callable      # check_if_order_executed
    get_usdc_sells_fn: Callable            # get_usdc_from_all_sells_since_last_buy
    get_sniper_entry_fn: Callable          # get_sniper_entry_price
    check_partial_exits_fn: Callable       # check_partial_exits_from_history
    console: Any                           # Rich console
    config: Any                            # Config singleton (injectable for tests)
    is_valid_stop_loss_fn: Callable        # is_valid_stop_loss_order
    buy_allocation_lock: Any = None        # _usdc_allocation_lock (Lock) — sérialise les achats multi-paires


def _restore_exchange_sl(ctx: '_TradeCtx', deps: '_TradingDeps', label: str) -> None:
    """Re-place le SL exchange après une vente partielle bloquée ou échouée.

    Doit être appelé dans TOUS les chemins d'échec de _execute_one_partial
    pour garantir que la position ne reste jamais sans SL.
    """
    ps = ctx.pair_state
    if ps.get('sl_exchange_placed'):
        return  # SL déjà actif
    _sl_price = ps.get('stop_loss_at_entry') or ps.get('stop_loss')
    if not _sl_price or ctx.coin_balance <= float(ctx.min_qty_dec):
        logger.warning("[%s] Impossible de restaurer SL: prix=%s, balance=%s", label, _sl_price, ctx.coin_balance)
        return
    try:
        _remaining_dec = Decimal(str(ctx.coin_balance))
        _remaining_rounded = (_remaining_dec // ctx.step_size_dec) * ctx.step_size_dec
        _remaining_str = f"{_remaining_rounded:.{ctx.step_decimals}f}"
        _sl_result = deps.place_sl_fn(
            symbol=ctx.real_trading_pair,
            quantity=_remaining_str,
            stop_price=float(_sl_price),
        )
        ps['sl_order_id'] = _sl_result.get('orderId')
        ps['sl_exchange_placed'] = True
        deps.save_fn()
        logger.info(
            "[%s] SL exchange RESTAURÉ après échec vente (qty=%s, stop=%.4f, orderId=%s)",
            label, _remaining_str, _sl_price, ps['sl_order_id'],
        )
    except Exception as _sl_err:
        logger.error("[%s] CRITIQUE: Échec restauration SL après vente bloquée: %s", label, _sl_err)


def _cancel_exchange_sl(ctx: '_TradeCtx', deps: '_TradingDeps') -> None:
    """F-1: Annule l'ordre SL exchange avant vente signal/partielle pour libérer les coins lockés.

    Après annulation, rafraîchit coin_balance_free depuis l'API pour refléter le unlock.
    """
    ps = ctx.pair_state
    sl_order_id = ps.get('sl_order_id')
    if not sl_order_id:
        return
    try:
        deps.client.cancel_order(symbol=ctx.real_trading_pair, orderId=sl_order_id)
        logger.info(
            "[SL-CANCEL F-1] Ordre SL exchange annulé (orderId=%s) avant vente signal/partielle",
            sl_order_id,
        )
        ps['sl_order_id'] = None
        ps['sl_exchange_placed'] = False
        deps.save_fn()
        # Rafraîchir les balances après annulation
        account_info = deps.client.get_account()
        _found, coin_free, coin_locked, coin_total = _get_coin_balance(account_info, ctx.coin_symbol)
        ctx.coin_balance_free = coin_free
        ctx.coin_balance_locked = coin_locked
        ctx.coin_balance = coin_total
    except Exception as e:
        logger.warning("[SL-CANCEL F-1] Échec annulation SL (orderId=%s): %s", sl_order_id, e)
        # P1: l'échec d'annulation SL garde les coins verrouillés → la vente signal risque d'échouer
        try:
            deps.send_alert_fn(
                subject=f"[ALERTE P1] Échec annulation SL exchange — {ctx.real_trading_pair}",
                body_main=(
                    f"L'annulation de l'ordre stop-loss exchange a échoué.\n\n"
                    f"Paire : {ctx.real_trading_pair}\n"
                    f"orderId : {sl_order_id}\n"
                    f"Erreur : {e}\n\n"
                    f"Les coins peuvent rester verrouillés → vente signal potentiellement bloquée.\n"
                    f"Vérifier les ordres ouverts sur Binance."
                ),
                client=deps.client,
            )
        except Exception as _e:
            logger.warning("[SL-CANCEL] Email alerte impossible: %s", _e)


@dataclass
class _TradeCtx:
    """Contexte partagé entre les sous-fonctions de _execute_real_trades_inner (C-15)."""
    real_trading_pair: str
    backtest_pair: str
    time_interval: str
    sizing_mode: str
    pair_state: Dict[str, Any]  # PairState TypedDict from MULTI_SYMBOLS
    best_params: Dict[str, Any]
    ema1_period: int
    ema2_period: int
    scenario: str
    coin_symbol: str
    quote_currency: str
    usdc_balance: float
    coin_balance_free: float
    coin_balance_locked: float
    coin_balance: float
    current_price: float
    row: Any
    orders: List[Any]
    min_qty: float
    max_qty: float
    step_size: float
    min_notional: float
    min_qty_dec: Decimal
    max_qty_dec: Decimal
    step_size_dec: Decimal
    step_decimals: int


def _sync_entry_state(ctx: '_TradeCtx', last_side: Optional[str], deps: '_TradingDeps') -> None:
    """C-15: Synchronise les variables d'entrée après détection d'un BUY FILLED."""
    if last_side != 'BUY':
        return
    last_buy_order = next(
        (o for o in reversed(ctx.orders) if o['status'] == 'FILLED' and o['side'] == 'BUY'),
        None,
    )
    if not last_buy_order:
        return
    executed_qty = float(last_buy_order.get('executedQty', 0))
    price = float(last_buy_order.get('price', 0))
    if price == 0.0 and executed_qty > 0:
        price = float(last_buy_order.get('cummulativeQuoteQty', 0)) / executed_qty

    atr_value = ctx.row.get('atr')
    atr_stop_multiplier = deps.config.atr_stop_multiplier
    # ML-03: Adaptive ATR stop multiplier at entry — scale with current vs 30d median volatility.
    # Falls back to config value if atr_median_30d is not available in row.
    _atr_median_30d = ctx.row.get('atr_median_30d') if hasattr(ctx.row, 'get') else None
    if (
        _atr_median_30d is not None
        and _atr_median_30d > 0
        and atr_value is not None
        and atr_value > 0
    ):
        _vol_ratio = atr_value / _atr_median_30d
        atr_stop_multiplier = atr_stop_multiplier * (_vol_ratio ** 0.5)
        atr_stop_multiplier = max(1.5, min(5.0, atr_stop_multiplier))
    atr_multiplier = deps.config.atr_multiplier
    ps = ctx.pair_state
    # Set entry variables ONLY if not already set (never update after entry)
    if atr_value is not None and price > 0:
        if ps.get('atr_at_entry') is None:
            ps['atr_at_entry'] = atr_value
        if ps.get('entry_price') is None:
            ps['entry_price'] = price
        if ps.get('stop_loss_at_entry') is None:
            ps['stop_loss_at_entry'] = price - atr_stop_multiplier * atr_value
            ps['stop_loss'] = ps['stop_loss_at_entry']
        if ps.get('trailing_activation_price_at_entry') is None:
            ps['trailing_activation_price_at_entry'] = price + atr_multiplier * atr_value
            ps['trailing_activation_price'] = ps['trailing_activation_price_at_entry']
        # === TRACKER L'ÉTAT DE LA POSITION ===
        ps['last_order_side'] = 'BUY'
        deps.save_fn()


def _update_trailing_stop(ctx: '_TradeCtx', deps: '_TradingDeps') -> None:
    """C-15: Met à jour l'activation et le niveau du trailing stop."""
    ps = ctx.pair_state
    if ps.get('last_order_side') != 'BUY' or ctx.coin_balance <= 0:
        return

    entry_price = ps.get('entry_price')
    atr_at_entry = ps.get('atr_at_entry')
    trailing_activation_price = ps.get('trailing_activation_price_at_entry')
    trailing_activated = ps.get('trailing_stop_activated', False)
    max_price = ps.get('max_price')
    if max_price is None:
        max_price = entry_price if entry_price is not None else ctx.current_price

    # Protection : si trailing_activation_price n'existe pas, le recalculer
    if trailing_activation_price is None and entry_price and atr_at_entry:
        trailing_activation_price = entry_price + (deps.config.atr_multiplier * atr_at_entry)
        ps['trailing_activation_price_at_entry'] = trailing_activation_price
        ps['trailing_activation_price'] = trailing_activation_price
        deps.save_fn()
        logger.info(f"[TRAILING] Prix d'activation recalculé: {trailing_activation_price:.4f}")

    # Mise à jour du max_price
    if max_price is None:
        max_price = ctx.current_price
    if max_price is not None and ctx.current_price is not None and ctx.current_price > max_price:
        max_price = ctx.current_price

    # === ACTIVATION DU TRAILING (quand prix >= entry + 5×ATR) ===
    if not trailing_activated and trailing_activation_price:
        if trailing_activation_price is not None and ctx.current_price is not None and ctx.current_price >= trailing_activation_price:
            trailing_activated = True
            logger.info(f"[TRAILING] ⚡ ACTIVÉ à {ctx.current_price:.4f} (seuil: {trailing_activation_price:.4f})")
            # Initialiser le trailing stop
            trailing_distance = deps.config.atr_multiplier * atr_at_entry if atr_at_entry else None
            if trailing_distance:
                trailing_stop_val = max_price - trailing_distance
                ps['trailing_stop'] = trailing_stop_val
                logger.info(f"[TRAILING] Stop initial: {trailing_stop_val:.4f}")
            # EM-P2-03: email d'activation (1 fois, throttle naturel via trailing_stop_activated)
            try:
                deps.send_alert_fn(
                    subject=f"Trailing stop activé — {ctx.backtest_pair}",
                    body_main=(
                        f"Le trailing stop est activé pour {ctx.backtest_pair}.\n\n"
                        f"Prix d'activation : {trailing_activation_price:.6f} USDC\n"
                        f"Prix actuel       : {ctx.current_price:.6f} USDC\n"
                        f"Stop initial      : {ps.get('trailing_stop', 'N/A')}\n"
                    ),
                    client=deps.client,
                )
            except Exception as _trail_err:
                logger.warning("[TRAILING] Email activation impossible: %s", _trail_err)

    # Mise à jour du trailing stop SI activé
    if trailing_activated and atr_at_entry is not None and max_price is not None:
        trailing_distance = deps.config.atr_multiplier * atr_at_entry
        new_trailing = max_price - trailing_distance
        current_trailing = ps.get('trailing_stop', 0)
        # Le trailing ne peut que monter (protection des gains)
        if new_trailing is not None and current_trailing is not None and new_trailing > current_trailing:
            ps['trailing_stop'] = new_trailing
            logger.info(f"[TRAILING] Nouveau stop : {new_trailing:.4f} (max: {max_price:.4f})")

    # B-3: Break-even stop — remonter stop_loss_at_entry au prix d'entrée dès que
    # le profit atteint breakeven_trigger_pct. Identique au backtest (backtest_runner.py).
    _be_enabled = getattr(config, 'breakeven_enabled', True)
    if _be_enabled and not ps.get('breakeven_triggered', False) and entry_price and entry_price > 0:
        if ctx.current_price is not None:
            _be_profit = (ctx.current_price - entry_price) / entry_price
            if _be_profit >= getattr(config, 'breakeven_trigger_pct', 0.02):
                _be_new_stop = entry_price * (1 + deps.config.slippage_buy)
                _current_sl = ps.get('stop_loss_at_entry') or 0
                if _be_new_stop > _current_sl:
                    ps['stop_loss_at_entry'] = _be_new_stop
                    ps['stop_loss'] = _be_new_stop
                    logger.info(
                        "[B-3 BREAKEVEN] Stop remonté au prix d'entrée + slippage : %.4f "
                        "(profit %.2f%% >= seuil %.1f%%)",
                        _be_new_stop, _be_profit * 100,
                        getattr(config, 'breakeven_trigger_pct', 0.02) * 100,
                    )
                ps['breakeven_triggered'] = True
                # EM-P2-03: email activation breakeven (1 fois, throttle naturel via breakeven_triggered)
                try:
                    deps.send_alert_fn(
                        subject=f"Breakeven activ\u00e9 \u2014 {ctx.backtest_pair}",
                        body_main=(
                            f"Le stop breakeven est activ\u00e9 pour {ctx.backtest_pair}.\n\n"
                            f"Prix d'entr\u00e9e  : {entry_price:.6f} USDC\n"
                            f"Nouveau SL     : {_be_new_stop:.6f} USDC\n"
                            f"Profit actuel  : {_be_profit*100:.2f}%\n"
                        ),
                        client=deps.client,
                    )
                except Exception as _be_err:
                    logger.warning("[BREAKEVEN] Email activation impossible: %s", _be_err)
                deps.save_fn()

    ps.update({
        'trailing_stop_activated': trailing_activated,
        'max_price': max_price,
    })
    deps.save_fn()


def _execute_partial_sells(ctx: '_TradeCtx', deps: '_TradingDeps') -> None:
    """Exécute les prises de profit partielles basées sur profit_pct.

    Logique miroir du backtest (backtest_runner.py L337-358) :
    - PARTIAL-1 : profit_pct >= deps.config.partial_threshold_1 → vendre deps.config.partial_pct_1
    - PARTIAL-2 : profit_pct >= deps.config.partial_threshold_2 → vendre deps.config.partial_pct_2 du reste

    Appelé AVANT _check_and_execute_stop_loss pour respecter l'ordre du backtest.
    Inclut la synchronisation API des flags partiels.
    """
    ps = ctx.pair_state
    if ps.get('last_order_side') != 'BUY' or ctx.coin_balance_free <= 0:
        return

    entry_price = ps.get('entry_price')
    if not entry_price or entry_price <= 0 or ctx.current_price is None:
        return

    # === SYNCHRONISATION AVEC L'HISTORIQUE API ===
    # P5-DASH-4: NEVER downgrade True→False. API detection is a heuristic
    # that can miss sells (fills split across order book). Local flags set
    # by _execute_one_partial are ground truth. Only upgrade False→True.
    try:
        api_partial_1, api_partial_2 = deps.check_partial_exits_fn(ctx.real_trading_pair, entry_price)
        state_partial_1 = ps.get('partial_taken_1', False)
        state_partial_2 = ps.get('partial_taken_2', False)
        # Upgrade only: API detected a partial that local state missed
        _changed = False
        if api_partial_1 and not state_partial_1:
            ps['partial_taken_1'] = True
            _changed = True
            logger.info("[SYNC] API détecte PARTIAL-1 manquant localement → corrigé")
        if api_partial_2 and not state_partial_2:
            ps['partial_taken_2'] = True
            _changed = True
            logger.info("[SYNC] API détecte PARTIAL-2 manquant localement → corrigé")
        if _changed:
            deps.save_fn()
        logger.debug(
            "[SYNC] État final : PARTIAL-1=%s, PARTIAL-2=%s (API: P1=%s, P2=%s)",
            ps.get('partial_taken_1'), ps.get('partial_taken_2'),
            api_partial_1, api_partial_2,
        )
    except Exception as e:
        logger.error(f"[SYNC] Erreur synchronisation API : {e}")

    partial_enabled = ps.get('partial_enabled', True)
    if not partial_enabled:
        logger.debug("[PARTIAL] Mode partials désactivé pour cette position (taille insuffisante)")
        return

    profit_pct = (ctx.current_price - entry_price) / entry_price

    # === PARTIAL-1 ===
    if not ps.get('partial_taken_1', False) and profit_pct >= deps.config.partial_threshold_1:
        # F-1: Annuler le SL exchange pour libérer les coins lockés avant vente partielle
        _cancel_exchange_sl(ctx, deps)
        _execute_one_partial(ctx, deps, partial_number=1, sell_pct=deps.config.partial_pct_1, profit_pct=profit_pct)

    # === PARTIAL-2 (vérifie sur le solde mis à jour après PARTIAL-1) ===
    if not ps.get('partial_taken_2', False) and profit_pct >= deps.config.partial_threshold_2 and ctx.coin_balance > 0:
        # F-1: Annuler le SL exchange si pas déjà fait (cas PARTIAL-2 sans PARTIAL-1)
        _cancel_exchange_sl(ctx, deps)
        _execute_one_partial(ctx, deps, partial_number=2, sell_pct=deps.config.partial_pct_2, profit_pct=profit_pct)


def _execute_one_partial(ctx: '_TradeCtx', deps: '_TradingDeps', *, partial_number: int, sell_pct: float, profit_pct: float) -> None:
    """Exécute une vente partielle (PARTIAL-1 ou PARTIAL-2).

    Parameters
    ----------
    partial_number : 1 ou 2
    sell_pct : fraction du solde libre à vendre (ex. 0.50 = 50 %)
    profit_pct : profit_pct actuel (pour le logging/email)
    """
    ps = ctx.pair_state
    label = f"PARTIAL-{partial_number}"
    flag_key = f"partial_taken_{partial_number}"
    threshold = deps.config.partial_threshold_1 if partial_number == 1 else deps.config.partial_threshold_2

    qty_to_sell = ctx.coin_balance * sell_pct  # F-1: use coin_balance (not free) — SL cancelled before call

    # Arrondir selon les règles d'échange
    quantity_decimal = Decimal(str(qty_to_sell))
    quantity_rounded = (quantity_decimal // ctx.step_size_dec) * ctx.step_size_dec
    if quantity_rounded < ctx.min_qty_dec:
        quantity_rounded = quantity_decimal
    if quantity_rounded > ctx.max_qty_dec:
        quantity_rounded = ctx.max_qty_dec

    # Vérifier LOT_SIZE et MIN_NOTIONAL
    notional_value = float(quantity_rounded) * ctx.current_price

    if quantity_rounded < ctx.min_qty_dec or notional_value < ctx.min_notional:
        # Montant trop faible → marquer comme pris pour éviter retry infini
        if quantity_rounded < ctx.min_qty_dec:
            logger.warning(f"[{label}] Vente bloquée : Quantité {quantity_rounded} < min_qty {ctx.min_qty_dec}")
        if notional_value < ctx.min_notional:
            logger.warning(f"[{label}] Vente bloquée : Valeur {notional_value:.2f} USDC < MIN_NOTIONAL {ctx.min_notional:.2f} USDC")
        # F-3: Email d'alerte sur vente partielle bloquée
        try:
            deps.send_alert_fn(
                subject=f"[ALERTE] {label} BLOQUÉE — {ctx.real_trading_pair}",
                body_main=(
                    f"Vente partielle {label} bloquée pour {ctx.real_trading_pair}.\n\n"
                    f"Quantité: {quantity_rounded} (min_qty: {ctx.min_qty_dec})\n"
                    f"Valeur: {notional_value:.2f} USDC (min: {ctx.min_notional:.2f})\n"
                    f"Profit: {profit_pct * 100:.1f}%\n"
                    f"Flag {flag_key} = True pour éviter retry."
                ),
                client=deps.client,
            )
        except Exception as _email_err:
            logger.error(f"[{label}] Échec envoi email vente bloquée: {_email_err}")
        if partial_number == 1:
            ps['partial_taken_1'] = True
        else:
            ps['partial_taken_2'] = True
        deps.save_fn()
        logger.info(f"[{label}] Montant trop faible — Flag mis à True pour éviter retry")
        _restore_exchange_sl(ctx, deps, label)
        return

    try:
        qty_str = f"{quantity_rounded:.{ctx.step_decimals}f}"
        sell_order = deps.market_sell_fn(symbol=ctx.real_trading_pair, quantity=qty_str)

        if sell_order and sell_order.get('status') == 'FILLED':
            executed_price = ctx.current_price
            total_usdc_received = float(qty_str) * executed_price

            logger.info(f"[{label}] Vente exécutée et confirmée : {qty_str} {ctx.coin_symbol} (profit {profit_pct * 100:.1f}%)")

            if partial_number == 1:
                ps['partial_taken_1'] = True
            else:
                ps['partial_taken_2'] = True
            ps['last_execution'] = datetime.now(timezone.utc).isoformat()
            deps.save_fn()
            logger.info(f"[{label}] Flag mis à jour : {flag_key} = True")

            # Email de vente partielle
            sell_type_desc = f"Prise de profit partielle {partial_number} (+{threshold * 100:.0f}%)"
            position_closed = f"{sell_pct * 100:.0f}%"

            extra = f"Timeframe : {ctx.time_interval}\nEMA : {ctx.ema1_period}/{ctx.ema2_period}\nScenario : {ctx.scenario}"
            subj, body = sell_executed_email(
                pair=ctx.real_trading_pair, qty=float(qty_str), price=executed_price,
                usdc_received=total_usdc_received, sell_reason=sell_type_desc,
                pnl_pct=profit_pct * 100,
                extra_details=extra
            )
            if deps.is_valid_stop_loss_fn(ctx.real_trading_pair, qty_str, executed_price):
                try:
                    deps.send_alert_fn(subject=subj, body_main=body, client=deps.client)
                    logger.info(f"[{label}] E-mail d'alerte envoyé pour la vente partielle")
                except Exception as e:
                    logger.error(f"[{label}] L'envoi de l'e-mail a echoué : {e}")
            else:
                logger.warning(f"[{label}] Email NON ENVOYÉ : paramètres invalides")

            # Rafraîchir le balance après vente partielle
            account_info = deps.client.get_account()
            _, ctx.coin_balance_free, ctx.coin_balance_locked, ctx.coin_balance = _get_coin_balance(account_info, ctx.coin_symbol)

            # P5-DASH: mettre à jour initial_position_size pour refléter la position restante
            ps['initial_position_size'] = ctx.coin_balance

            # F-1: Replacer SL exchange sur la quantité restante après partiel
            _sl_price = ps.get('stop_loss_at_entry')
            if _sl_price and ctx.coin_balance > ctx.min_qty:
                try:
                    _remaining_dec = Decimal(str(ctx.coin_balance))
                    _remaining_rounded = (_remaining_dec // ctx.step_size_dec) * ctx.step_size_dec
                    _remaining_str = f"{_remaining_rounded:.{ctx.step_decimals}f}"
                    _sl_result = deps.place_sl_fn(
                        symbol=ctx.real_trading_pair,
                        quantity=_remaining_str,
                        stop_price=float(_sl_price),
                    )
                    ps['sl_order_id'] = _sl_result.get('orderId')
                    ps['sl_exchange_placed'] = True
                    deps.save_fn()
                    logger.info(
                        "[%s] SL exchange replacé (qty=%s, stop=%.4f, orderId=%s)",
                        label, _remaining_str, _sl_price, ps['sl_order_id'],
                    )
                except Exception as _sl_err:
                    logger.warning("[%s] Échec replacement SL exchange: %s", label, _sl_err)

            # Journal de trading
            try:
                logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                _entry_px = ps.get('entry_price') or 0
                _pnl = (float(executed_price) - _entry_px) * float(qty_str) if _entry_px else None
                _pnl_pct = ((float(executed_price) / _entry_px) - 1) if _entry_px else None
                _buy_ts = ps.get('buy_timestamp')
                _duration_s = (time.time() - _buy_ts) if _buy_ts else None
                log_trade(
                    logs_dir=logs_dir,
                    pair=ctx.real_trading_pair,
                    side='sell',
                    quantity=float(qty_str),
                    price=float(executed_price),
                    fee=float(qty_str) * deps.config.taker_fee * float(executed_price),
                    scenario=ctx.scenario,
                    timeframe=ctx.time_interval,
                    pnl=_pnl,
                    pnl_pct=_pnl_pct,
                    extra={'sell_reason': label, 'position_closed': position_closed, 'duration_s': _duration_s},
                )
            except Exception as journal_err:
                logger.error(f"[JOURNAL] Erreur écriture vente partielle: {journal_err}")
        else:
            logger.warning(f"[{label}] Ordre de vente non FILLED — tentative échouée")
            try:
                _status = sell_order.get('status', 'UNKNOWN') if sell_order else 'None'
                deps.send_alert_fn(
                    subject=f"[ALERTE] {label} NON FILLED — {ctx.real_trading_pair}",
                    body_main=(
                        f"Ordre de vente partielle {label} non exécuté.\n\n"
                        f"Paire : {ctx.real_trading_pair}\n"
                        f"Quantité : {qty_str}\n"
                        f"Statut : {_status}\n"
                        f"Prix courant : {ctx.current_price:.4f} USDC\n\n"
                        f"Action : le bot réessaiera au prochain cycle."
                    ),
                    client=deps.client,
                )
            except Exception as _email_err:
                logger.error(f"[{label}] Échec envoi email vente non-filled: {_email_err}")
            _restore_exchange_sl(ctx, deps, label)

    except Exception as e:
        logger.error(f"[{label}] Erreur lors de l'exécution de la vente partielle: {e}")
        _restore_exchange_sl(ctx, deps, label)



def _handle_exchange_sl_fill(
    ctx: '_TradeCtx',
    deps: '_TradingDeps',
    is_trailing_stop: bool,
    stop_loss_fixed: 'Optional[float]',
    trailing_stop: 'Optional[float]',
) -> bool:
    """P0-04: Coins locked — check exchange SL order status, reconcile if FILLED.

    Called when coin_balance_free < min_qty (coins are inside a SL limit order on Binance).
    Returns True in all cases (position handled by Binance or just reconciled).
    """
    ps = ctx.pair_state
    sl_oid = ps.get('sl_order_id')
    _sl_filled = False
    _sl_fill_price: float = ctx.current_price
    _sl_exec_qty: float = 0.0

    if sl_oid:
        try:
            _sl_info = deps.client.get_order(
                symbol=ctx.real_trading_pair, orderId=sl_oid,
            )
            _sl_status = _sl_info.get('status', '')
            if _sl_status == 'FILLED':
                _sl_filled = True
                _eq = float(_sl_info.get('executedQty', 0))
                _cq = float(_sl_info.get('cummulativeQuoteQty', 0))
                if _eq > 0:
                    _sl_exec_qty = _eq
                if _eq > 0 and _cq > 0:
                    _sl_fill_price = _cq / _eq
                logger.info(
                    "[SL-EXCHANGE] Ordre SL %s FILLED — prix moyen %.4f USDC, qty %.8f",
                    sl_oid, _sl_fill_price, _sl_exec_qty,
                )
            else:
                logger.info(
                    "[STOP-LOSS SOFTWARE] Ordre SL exchange %s statut=%s — "
                    "vente gérée automatiquement par Binance.",
                    sl_oid, _sl_status,
                )
        except Exception as _sl_check_err:
            logger.warning(
                "[SL-EXCHANGE] Impossible de vérifier l'ordre SL %s: %s",
                sl_oid, _sl_check_err,
            )
    else:
        # Pas de sl_order_id — inférer depuis les balances
        if ctx.coin_balance_locked < ctx.min_qty:
            _sl_filled = True
            logger.warning(
                "[SL-EXCHANGE] Aucun sl_order_id mais coins absents "
                "(free=%.8f, locked=%.8f) — SL probablement FILLED.",
                ctx.coin_balance_free, ctx.coin_balance_locked,
            )

    if not _sl_filled:
        # Binance gère la vente — attendre le prochain cycle
        return True

    # === SL exchange FILLED — réconciliation complète ===
    executed_price = _sl_fill_price
    total_usdc_received = _sl_exec_qty * executed_price if _sl_exec_qty else 0.0

    if is_trailing_stop:
        stop_type = "TRAILING-STOP (dynamique)"
        stop_desc = (
            f"Prix max atteint : {ps.get('max_price', 0):.4f} USDC\n"
            f"Trailing stop : {trailing_stop:.4f} USDC"
        )
    else:
        stop_type = "STOP-LOSS (fixe à 3×ATR)"
        stop_desc = f"Stop-loss fixe : {stop_loss_fixed:.4f} USDC"

    # Email d'alerte SL exchange
    _entry_px_sl = ps.get('entry_price') or 0
    _pnl_pct_sl = ((executed_price / _entry_px_sl) - 1) * 100 if _entry_px_sl > 0 else None
    if deps.is_valid_stop_loss_fn(ctx.real_trading_pair, str(_sl_exec_qty or 1), executed_price):
        extra = (
            f"DETAILS DU STOP (ordre exchange natif):\n{stop_desc}\n"
            f"Prix d'entree : {_entry_px_sl:.4f} USDC\n"
            f"Timeframe : {ctx.time_interval}\n"
            f"EMA : {ctx.ema1_period}/{ctx.ema2_period}\n"
            f"Scenario : {ctx.scenario}"
        )
        subj, body = sell_executed_email(
            pair=ctx.real_trading_pair, qty=_sl_exec_qty or 0,
            price=executed_price, usdc_received=total_usdc_received,
            sell_reason=stop_type, pnl_pct=_pnl_pct_sl,
            extra_details=extra,
        )
        try:
            deps.send_alert_fn(subject=subj, body_main=body, client=deps.client)
            logger.info("[SL-EXCHANGE] E-mail d'alerte envoyé pour SL exchange")
        except Exception as _email_err:
            logger.error("[SL-EXCHANGE] Échec envoi e-mail: %s", _email_err)
    else:
        logger.warning(
            "[SL-EXCHANGE] Email NON ENVOYÉ : paramètres invalides "
            "(symbol=%s, qty=%s, price=%s)",
            ctx.real_trading_pair, _sl_exec_qty, executed_price,
        )

    # Capturer entry_price AVANT le reset pour le journal PnL
    _saved_entry_price = ps.get('entry_price') or 0.0

    # Reset complet de l'état
    ps.update({
        'entry_price': None, 'max_price': None, 'stop_loss': None,
        'trailing_stop': None, 'trailing_stop_activated': False,
        'atr_at_entry': None, 'stop_loss_at_entry': None,
        'trailing_activation_price_at_entry': None,
        'initial_position_size': None,
        'last_order_side': 'SELL',
        'breakeven_triggered': False,
        'entry_scenario': None, 'entry_timeframe': None,
        'entry_ema1': None, 'entry_ema2': None,
        'sl_order_id': None, 'sl_exchange_placed': False,
    })

    # A-3: cooldown post-stop-loss
    _cd_candles = getattr(config, 'stop_loss_cooldown_candles', 0)
    if _cd_candles > 0:
        _candle_sec = TIMEFRAME_SECONDS.get(ctx.time_interval, 3600)
        ps['_stop_loss_cooldown_until'] = time.time() + (_cd_candles * _candle_sec)
        logger.info(
            "[A-3 COOLDOWN] Post-stop-loss exchange: %d bougies x %ds = %.1fh",
            _cd_candles, _candle_sec, (_cd_candles * _candle_sec) / 3600,
        )

    deps.save_fn(force=True)

    # Journal de trading
    if _sl_exec_qty > 0:
        try:
            logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
            _pnl = (executed_price - _saved_entry_price) * _sl_exec_qty if _saved_entry_price else None
            _pnl_pct_j = ((executed_price / _saved_entry_price) - 1) if _saved_entry_price else None
            deps.update_daily_pnl_fn(_pnl)
            _buy_ts_sl = ps.get('buy_timestamp')
            _duration_s_sl = (time.time() - _buy_ts_sl) if _buy_ts_sl else None
            log_trade(
                logs_dir=logs_dir,
                pair=ctx.real_trading_pair,
                side='sell',
                quantity=_sl_exec_qty,
                price=executed_price,
                fee=_sl_exec_qty * deps.config.taker_fee * executed_price,
                scenario=ctx.scenario,
                timeframe=ctx.time_interval,
                pnl=_pnl,
                pnl_pct=_pnl_pct_j,
                extra={'sell_reason': f'{stop_type} (exchange-filled)', 'duration_s': _duration_s_sl},
            )
        except Exception as _journal_err:
            logger.error("[JOURNAL] Erreur écriture vente SL exchange: %s", _journal_err)

    # Affichage panel de clôture
    if is_trailing_stop:
        stop_loss_info = f"{trailing_stop:.4f} USDC (dynamique : trailing)"
    else:
        stop_loss_info = f"{stop_loss_fixed:.4f} USDC (fixe à l'entrée)"
    display_closure_panel(stop_loss_info, ctx.current_price, ctx.coin_symbol, ctx.coin_balance, deps.console)

    ps['last_execution'] = datetime.now(timezone.utc).isoformat()
    deps.save_fn()
    return True


def _handle_manual_sl_trigger(
    ctx: '_TradeCtx',
    deps: '_TradingDeps',
    effective_stop: float,
    is_trailing_stop: bool,
    stop_loss_fixed: 'Optional[float]',
    trailing_stop: 'Optional[float]',
) -> bool:
    """P0-04: Coins free — execute market-sell immediately for stop-loss.

    Called when coin_balance_free >= min_qty (no pending exchange SL order).
    Returns True always (position was triggered, whether or not the sell succeeded).
    """
    ps = ctx.pair_state

    # Calculer la quantité à vendre (coins disponibles)
    quantity_decimal = Decimal(str(ctx.coin_balance_free))
    quantity_rounded = (quantity_decimal // ctx.step_size_dec) * ctx.step_size_dec
    if quantity_rounded < ctx.min_qty_dec:
        quantity_rounded = quantity_decimal
    if quantity_rounded > ctx.max_qty_dec:
        quantity_rounded = ctx.max_qty_dec

    order_value = float(quantity_rounded) * ctx.current_price

    if quantity_rounded >= ctx.min_qty_dec and order_value >= ctx.min_notional:
        qty_str = f"{quantity_rounded:.{ctx.step_decimals}f}"
        stop_loss_order = deps.market_sell_fn(symbol=ctx.real_trading_pair, quantity=qty_str)

        if stop_loss_order and stop_loss_order.get('status') == 'FILLED':
            logger.info("[STOP-LOSS] Vente exécutée et confirmée : %s %s", qty_str, ctx.coin_symbol)
            executed_price = ctx.current_price
            total_usdc_received = float(qty_str) * executed_price

            if is_trailing_stop:
                stop_type = "TRAILING-STOP (dynamique)"
                stop_desc = (
                    f"Prix max atteint : {ps.get('max_price', 0):.4f} USDC\n"
                    f"Trailing stop : {trailing_stop:.4f} USDC"
                )
            else:
                stop_type = "STOP-LOSS (fixe à 3×ATR)"
                stop_desc = f"Stop-loss fixe : {stop_loss_fixed:.4f} USDC"

            # === EMAIL STOP-LOSS ===
            if deps.is_valid_stop_loss_fn(ctx.real_trading_pair, qty_str, executed_price):
                _entry_px_sl = ps.get('entry_price') or 0
                _pnl_pct_sl = ((executed_price / _entry_px_sl) - 1) * 100 if _entry_px_sl > 0 else None
                extra = (
                    f"DETAILS DU STOP:\n{stop_desc}\n"
                    f"Prix d'entree : {ps.get('entry_price', 0):.4f} USDC\n"
                    f"Timeframe : {ctx.time_interval}\n"
                    f"EMA : {ctx.ema1_period}/{ctx.ema2_period}\n"
                    f"Scenario : {ctx.scenario}"
                )
                subj, body = sell_executed_email(
                    pair=ctx.real_trading_pair, qty=float(qty_str), price=executed_price,
                    usdc_received=total_usdc_received, sell_reason=stop_type,
                    pnl_pct=_pnl_pct_sl, extra_details=extra,
                )
                try:
                    deps.send_alert_fn(subject=subj, body_main=body, client=deps.client)
                    logger.info("[STOP-LOSS] E-mail d'alerte envoyé pour la vente")
                except Exception as e:
                    logger.error("[STOP-LOSS] L'envoi de l'e-mail a échoué : %s", e)
            else:
                logger.warning(
                    "[STOP-LOSS] Email NON ENVOYÉ : paramètres invalides "
                    "(symbol=%s, qty=%s, price=%s)",
                    ctx.real_trading_pair, qty_str, executed_price,
                )

            # Capturer entry_price AVANT le reset pour le journal PnL
            _saved_entry_price = ps.get('entry_price') or 0.0

            # Reset entry variables after closure
            ps.update({
                'entry_price': None, 'max_price': None, 'stop_loss': None,
                'trailing_stop': None, 'trailing_stop_activated': False,
                'atr_at_entry': None, 'stop_loss_at_entry': None,
                'trailing_activation_price_at_entry': None,
                'last_order_side': 'SELL',
                'breakeven_triggered': False,
                'entry_scenario': None, 'entry_timeframe': None,
                'entry_ema1': None, 'entry_ema2': None,
            })

            # A-3: cooldown post-stop-loss
            _cd_candles = getattr(config, 'stop_loss_cooldown_candles', 0)
            if _cd_candles > 0:
                _candle_sec = TIMEFRAME_SECONDS.get(ctx.time_interval, 3600)
                ps['_stop_loss_cooldown_until'] = time.time() + (_cd_candles * _candle_sec)
                logger.info(
                    "[A-3 COOLDOWN] Post-stop-loss : %d bougies x %ds = %.1fh",
                    _cd_candles, _candle_sec, (_cd_candles * _candle_sec) / 3600,
                )
            deps.save_fn()

            # Journal de trading
            try:
                logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                _exec_price = float(executed_price)
                _qty = float(qty_str)
                _pnl = (_exec_price - _saved_entry_price) * _qty if _saved_entry_price and _exec_price else None
                _pnl_pct = ((_exec_price / _saved_entry_price) - 1) if _saved_entry_price and _exec_price else None
                deps.update_daily_pnl_fn(_pnl)
                _buy_ts_m = ps.get('buy_timestamp')
                _duration_s_m = (time.time() - _buy_ts_m) if _buy_ts_m else None
                log_trade(
                    logs_dir=logs_dir,
                    pair=ctx.real_trading_pair,
                    side='sell',
                    quantity=_qty,
                    price=_exec_price,
                    fee=_qty * deps.config.taker_fee * _exec_price,
                    scenario=ctx.scenario,
                    timeframe=ctx.time_interval,
                    pnl=_pnl,
                    pnl_pct=_pnl_pct,
                    extra={'sell_reason': stop_type, 'duration_s': _duration_s_m},
                )
            except Exception as journal_err:
                logger.error("[JOURNAL] Erreur écriture vente stop: %s", journal_err)

        else:
            # NOT FILLED
            _status = stop_loss_order.get('status', 'UNKNOWN') if stop_loss_order else 'None'
            logger.warning("[STOP-LOSS] Ordre de vente SL non FILLED (statut=%s)", _status)
            try:
                deps.send_alert_fn(
                    subject=f"[ALERTE] Stop-Loss NON FILLED — {ctx.real_trading_pair}",
                    body_main=(
                        f"Ordre de vente stop-loss (software) non exécuté.\n\n"
                        f"Paire : {ctx.real_trading_pair}\n"
                        f"Quantité : {qty_str}\n"
                        f"Stop effectif : {effective_stop:.4f} USDC\n"
                        f"Prix courant : {ctx.current_price:.4f} USDC\n"
                        f"Statut : {_status}\n\n"
                        f"ATTENTION : position toujours exposée sans protection.\n"
                        f"Action : le bot réessaiera au prochain cycle."
                    ),
                    client=deps.client,
                )
            except Exception as _email_err:
                logger.error("[STOP-LOSS] Échec envoi email SL non-filled: %s", _email_err)

    # Affichage panel de clôture — toujours (même si la vente a échoué)
    if is_trailing_stop:
        stop_loss_info = f"{trailing_stop:.4f} USDC (dynamique : trailing)"
    else:
        stop_loss_info = f"{stop_loss_fixed:.4f} USDC (fixe à l'entrée)"
    display_closure_panel(stop_loss_info, ctx.current_price, ctx.coin_symbol, ctx.coin_balance, deps.console)

    ps['last_execution'] = datetime.now(timezone.utc).isoformat()
    deps.save_fn()
    return True


def _reconcile_zero_balance_sl(ctx: '_TradeCtx', deps: '_TradingDeps') -> bool:
    """Reconcile pair_state when balance=0 but last_order_side='BUY'.

    The SL was filled by Binance (or position closed externally) but
    _handle_exchange_sl_fill was never reached because coin_balance was already 0
    when the bot checked.  This resets pair_state so P0-BUY no longer blocks.

    Returns True (position handled — caller should ``return``).
    """
    ps = ctx.pair_state
    sl_oid = ps.get('sl_order_id')
    _fill_price: float = ctx.current_price
    _fill_qty: float = 0.0
    _sl_was_filled = False
    _sl_fill_ts: float = 0.0  # epoch seconds — actual Binance fill time

    # ── Query Binance for SL order status ──────────────────────────
    if sl_oid:
        try:
            sl_info = deps.client.get_order(
                symbol=ctx.real_trading_pair, orderId=sl_oid,
            )
            sl_status = sl_info.get('status', '')
            if sl_status == 'FILLED':
                _sl_was_filled = True
                eq = float(sl_info.get('executedQty', 0))
                cq = float(sl_info.get('cummulativeQuoteQty', 0))
                _ut = sl_info.get('updateTime', 0)
                if _ut:
                    _sl_fill_ts = float(_ut) / 1000.0  # ms → s
                if eq > 0:
                    _fill_qty = eq
                if eq > 0 and cq > 0:
                    _fill_price = cq / eq
                logger.info(
                    "[SL-RECONCILE] Ordre SL %s FILLED — prix moyen %.4f, qty %.8f",
                    sl_oid, _fill_price, _fill_qty,
                )
            else:
                logger.warning(
                    "[SL-RECONCILE] SL %s status=%s mais balance=0 — "
                    "position fermée hors SL.",
                    sl_oid, sl_status,
                )
        except Exception as e:
            logger.warning("[SL-RECONCILE] Erreur vérification SL %s: %s", sl_oid, e)
    else:
        logger.warning(
            "[SL-RECONCILE] %s — Pas de sl_order_id et balance=0 — "
            "position fermée hors bot.",
            ctx.backtest_pair,
        )

    # ── Log & email alert ──────────────────────────────────────────
    _entry_px = ps.get('entry_price') or 0
    _pnl_pct = ((_fill_price / _entry_px) - 1) * 100 if _entry_px > 0 else None
    logger.critical(
        "[SL-RECONCILE] %s — balance=0, last_order_side='BUY' (entry=%.4f). "
        "SL filled=%s. Reset → SELL.",
        ctx.backtest_pair, _entry_px, _sl_was_filled,
    )

    if _sl_was_filled and _fill_qty > 0:
        _usdc_received = _fill_qty * _fill_price
        stop_loss_fixed = ps.get('stop_loss_at_entry')
        trailing_stop = ps.get('trailing_stop', 0)
        trailing_activated = ps.get('trailing_stop_activated', False)
        is_trailing_stop = (
            trailing_activated
            and trailing_stop is not None
            and stop_loss_fixed is not None
            and trailing_stop > (stop_loss_fixed or 0)
        )
        stop_type = (
            "TRAILING-STOP (dynamique)" if is_trailing_stop
            else "STOP-LOSS (fixe à 3×ATR)"
        )
        subj, body = sell_executed_email(
            pair=ctx.real_trading_pair, qty=_fill_qty,
            price=_fill_price, usdc_received=_usdc_received,
            sell_reason=f"{stop_type} — réconciliation balance=0",
            pnl_pct=_pnl_pct,
            extra_details=(
                f"Prix d'entree : {_entry_px:.4f} USDC\n"
                f"Timeframe : {ctx.time_interval}\n"
                f"Scenario : {ctx.scenario}\n"
                f"Détecté via réconciliation runtime (balance=0)"
            ),
        )
        try:
            deps.send_alert_fn(subject=subj, body_main=body, client=deps.client)
        except Exception as _email_err:
            logger.error("[SL-RECONCILE] Échec envoi email: %s", _email_err)

    # ── Complete state reset (mirrors _handle_exchange_sl_fill) ────
    _saved_entry_price = ps.get('entry_price') or 0.0
    ps.update({
        'entry_price': None, 'max_price': None, 'stop_loss': None,
        'trailing_stop': None, 'trailing_stop_activated': False,
        'atr_at_entry': None, 'stop_loss_at_entry': None,
        'trailing_activation_price_at_entry': None,
        'initial_position_size': None,
        'last_order_side': 'SELL',
        'breakeven_triggered': False,
        'entry_scenario': None, 'entry_timeframe': None,
        'entry_ema1': None, 'entry_ema2': None,
        'sl_order_id': None, 'sl_exchange_placed': False,
    })

    # A-3: cooldown post-stop-loss (use actual fill time, not current time)
    _cd_candles = getattr(config, 'stop_loss_cooldown_candles', 0)
    if _cd_candles > 0:
        _candle_sec = TIMEFRAME_SECONDS.get(ctx.time_interval, 3600)
        _cd_base = _sl_fill_ts if _sl_fill_ts > 0 else time.time()
        _cd_until = _cd_base + (_cd_candles * _candle_sec)
        if _cd_until > time.time():
            ps['_stop_loss_cooldown_until'] = _cd_until
            logger.info(
                "[A-3 COOLDOWN] SL-reconcile: %.0f min restantes (basé sur fill time)",
                (_cd_until - time.time()) / 60,
            )
        else:
            logger.info(
                "[A-3 COOLDOWN] SL-reconcile: cooldown déjà expiré (fill il y a %.1fh)",
                (time.time() - _cd_base) / 3600,
            )

    deps.save_fn(force=True)

    # Trade journal
    _j_qty = _fill_qty or (ps.get('initial_position_size') or 0)
    if _j_qty > 0:
        try:
            logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
            _pnl = (_fill_price - _saved_entry_price) * _j_qty if _saved_entry_price else None
            _pnl_pct_j = ((_fill_price / _saved_entry_price) - 1) if _saved_entry_price else None
            deps.update_daily_pnl_fn(_pnl)
            _buy_ts = ps.get('buy_timestamp')
            _dur = (time.time() - _buy_ts) if _buy_ts else None
            log_trade(
                logs_dir=logs_dir,
                pair=ctx.real_trading_pair,
                side='sell',
                quantity=_j_qty,
                price=_fill_price,
                fee=_j_qty * deps.config.taker_fee * _fill_price,
                scenario=ctx.scenario,
                timeframe=ctx.time_interval,
                pnl=_pnl,
                pnl_pct=_pnl_pct_j,
                extra={'sell_reason': 'SL_EXCHANGE_RECONCILE_ZERO_BAL', 'duration_s': _dur},
            )
        except Exception as _journal_err:
            logger.error("[SL-RECONCILE] Erreur journal: %s", _journal_err)

    display_closure_panel(
        f"Réconciliation SL (balance=0)",
        ctx.current_price, ctx.coin_symbol, ctx.coin_balance, deps.console,
    )
    ps['last_execution'] = datetime.now(timezone.utc).isoformat()
    deps.save_fn()
    return True


def _check_and_execute_stop_loss(ctx: '_TradeCtx', deps: '_TradingDeps') -> bool:
    """C-15: Vérifie et exécute le stop-loss/trailing. Retourne True si position fermée.

    P0-04: Dispatcher — calcule le stop effectif, puis délègue à :
      _handle_exchange_sl_fill (coins verrouillés dans ordre exchange)
      _handle_manual_sl_trigger (coins libres, market-sell immédiat)
      _reconcile_zero_balance_sl (balance=0, SL rempli par Binance)
    """
    ps = ctx.pair_state
    if ps.get('last_order_side') != 'BUY':
        return False

    # ── Balance = 0 but state says BUY → SL filled or position closed externally
    if ctx.coin_balance <= 0:
        return _reconcile_zero_balance_sl(ctx, deps)

    stop_loss_fixed = ps.get('stop_loss_at_entry')  # Stop-loss FIXE à 3×ATR
    trailing_stop = ps.get('trailing_stop', 0)       # Trailing (si activé)
    trailing_activated = ps.get('trailing_stop_activated', False)

    # Déterminer le niveau de stop effectif
    effective_stop = stop_loss_fixed
    is_trailing_stop = False
    if (trailing_activated and trailing_stop is not None and stop_loss_fixed is not None
            and trailing_stop > (stop_loss_fixed or 0)):
        effective_stop = trailing_stop
        is_trailing_stop = True

    if effective_stop is None or ctx.current_price is None or ctx.current_price > effective_stop:
        return False

    # === Prix <= effective_stop — déléguer au handler approprié ===
    if ctx.coin_balance_free < ctx.min_qty:
        # Coins verrouillés dans un ordre SL exchange (STOP_LOSS_LIMIT pending)
        return _handle_exchange_sl_fill(ctx, deps, is_trailing_stop, stop_loss_fixed, trailing_stop)

    # Coins libres — exécuter le market-sell immédiatement
    return _handle_manual_sl_trigger(ctx, deps, effective_stop, is_trailing_stop, stop_loss_fixed, trailing_stop)


def _handle_dust_cleanup(ctx: '_TradeCtx', deps: '_TradingDeps') -> bool:
    """C-15: Détecte et nettoie les résidus (dust). Retourne position_has_crypto."""
    ps = ctx.pair_state
    # Position réelle = assez de coins ET (valeur >= min_notional OU position intentionnelle BUY)
    # Quand last_order_side='SELL' mais du dust subsiste au-dessus de min_qty,
    # la valeur notional détermine si c'est tradeable ou non.
    _notional_value = ctx.coin_balance * ctx.current_price if ctx.current_price > 0 else 0.0
    position_has_crypto = (
        ctx.coin_balance > ctx.min_qty
        and (
            _notional_value >= ctx.min_notional
            or ps.get('last_order_side') == 'BUY'
        )
    )

    # === DÉTECTION ET NETTOYAGE FORCÉ DES RÉSIDUS (DUST) ===
    # Dust = coins présents mais non tradeable (soit < min_qty, soit < min_notional sans position BUY)
    has_dust = (
        ctx.coin_balance > ctx.min_qty * 0.01
        and not position_has_crypto
        and ctx.coin_balance_locked < ctx.min_qty
    )

    if has_dust:
        logger.warning(f"[DUST] Résidu détecté : {ctx.coin_balance:.8f} {ctx.coin_symbol} (entre 1% et 98% de min_qty)")

        # IMPORTANT: Vérifier si la valeur totale du résidu respecte MIN_NOTIONAL
        dust_notional_value = ctx.coin_balance * ctx.current_price
        if dust_notional_value < ctx.min_notional:
            logger.warning(f"[DUST] Valeur du résidu ({dust_notional_value:.2f} USDC) < MIN_NOTIONAL ({ctx.min_notional:.2f} USDC)")
            logger.warning(f"[DUST] Impossible de vendre le résidu - Binance refuse les ordres < {ctx.min_notional:.2f} USDC")
            logger.info("[DUST] Résidu ignoré (position considérée comme fermée)")
            # Reset état : nettoyer les champs d'entrée stales (quel que soit last_order_side)
            _stale_fields = (
                ps.get('entry_price') is not None
                or ps.get('stop_loss_at_entry') is not None
                or ps.get('last_order_side') == 'BUY'
            )
            if _stale_fields:
                # P0-BUY: log CRITICAL si on reset un état BUY — c'est anormal et doit être investigué
                if ps.get('last_order_side') == 'BUY':
                    logger.critical(
                        "[DUST P0-BUY] %s — RESET last_order_side BUY→SELL sur dust intradable "
                        "(balance=%.8f, notional=%.2f, min_notional=%.2f). "
                        "Vérifier si un achat réel a été perdu.",
                        ctx.backtest_pair, ctx.coin_balance, dust_notional_value, ctx.min_notional,
                    )
                ps.update({
                    'entry_price': None, 'max_price': None, 'stop_loss': None,
                    'trailing_stop': None, 'trailing_stop_activated': False,
                    'atr_at_entry': None, 'stop_loss_at_entry': None,
                    'trailing_activation_price_at_entry': None,
                    'initial_position_size': None,
                    'last_order_side': 'SELL',
                    'partial_taken_1': False, 'partial_taken_2': False,
                    'breakeven_triggered': False,
                    'entry_scenario': None, 'entry_timeframe': None,
                    'entry_ema1': None, 'entry_ema2': None,
                    'sl_order_id': None, 'sl_exchange_placed': False,
                })
                deps.save_fn(force=True)
                logger.info("[DUST] État pair_state réinitialisé (dust intradable, position considérée fermée)")
        else:
            logger.info("[DUST] Tentative de vente forcée du résidu pour débloquer le trading...")

            try:
                qty_str = f"{ctx.coin_balance:.{ctx.step_decimals}f}"
                dust_sell_order = deps.market_sell_fn(symbol=ctx.real_trading_pair, quantity=qty_str)

                if dust_sell_order and dust_sell_order.get('status') == 'FILLED':
                    logger.info(f"[DUST] Vente réussie et confirmée du résidu : {qty_str} {ctx.coin_symbol}")
                    # Reset complet de l'état après nettoyage
                    ps.update({
                        'entry_price': None, 'max_price': None, 'stop_loss': None,
                        'trailing_stop': None, 'trailing_stop_activated': False,
                        'atr_at_entry': None, 'stop_loss_at_entry': None,
                        'trailing_activation_price_at_entry': None,
                        'initial_position_size': None,
                        'last_order_side': 'SELL',
                        'partial_taken_1': False,
                        'partial_taken_2': False,
                        'breakeven_triggered': False,
                        'entry_scenario': None, 'entry_timeframe': None,
                        'entry_ema1': None, 'entry_ema2': None,
                        'sl_order_id': None, 'sl_exchange_placed': False,
                    })
                    deps.save_fn()

                    # Rafraîchir le solde
                    account_info = deps.client.get_account()
                    # C-13: helper centralisé free+locked
                    _, ctx.coin_balance_free, ctx.coin_balance_locked, ctx.coin_balance = _get_coin_balance(account_info, ctx.coin_symbol)
                    position_has_crypto = ctx.coin_balance > ctx.min_qty

                    logger.info(f"[DUST] Position nettoyée. Nouveau solde : {ctx.coin_balance:.8f} {ctx.coin_symbol}")
                else:
                    logger.warning("[DUST] Vente du résidu échouée - Binance refuse")
                    logger.info("[DUST] Continuant quand même (résidu < min_qty = position fermée)")
            except Exception as e:
                logger.error(f"[DUST] Erreur lors de la tentative de vente : {e}")
                logger.info("[DUST] Continuant quand même (résidu < min_qty = position fermée)")

    return position_has_crypto


def _execute_signal_sell(ctx: '_TradeCtx', deps: '_TradingDeps') -> None:
    """C-15: Exécute les ventes sur signal final (SIGNAL = vente 100 % du reste).

    Note : les prises de profit partielles (PARTIAL-1, PARTIAL-2) sont désormais
    gérées par _execute_partial_sells() appelée avant _check_and_execute_stop_loss,
    conformément à l'ordre du backtest.
    La synchronisation API des flags partiels est aussi dans _execute_partial_sells().
    """
    ps = ctx.pair_state
    atr_stop_multiplier = deps.config.atr_stop_multiplier

    entry_price_for_panel = ps.get('entry_price') or ctx.row.get('close')
    ps.setdefault('stop_loss_at_entry', entry_price_for_panel - atr_stop_multiplier * (ctx.row.get('atr') or 0.0) if entry_price_for_panel and ctx.row.get('atr') else None)
    ps.setdefault('atr_at_entry', ctx.row.get('atr'))
    ps.setdefault('max_price', entry_price_for_panel)

    check_sell_signal = deps.gen_sell_checker_fn(ctx.best_params)
    sell_triggered, sell_reason = check_sell_signal(ctx.row, ctx.coin_balance, entry_price_for_panel, ctx.current_price, ctx.row.get('atr'))

    # F-2: Protection min hold time — bloquer la vente signal si l'achat est trop récent.
    # On attend au moins 1 bougie complète pour éviter d'acheter et vendre dans la même bougie
    # (incohérence typique lors d'un changement de timeframe WF entre deux exécutions).
    _min_hold = TIMEFRAME_SECONDS.get(ctx.time_interval, 3600)  # default 1h
    _buy_ts = ps.get('buy_timestamp', 0)
    if sell_triggered and sell_reason == 'SIGNAL' and _buy_ts > 0:
        _held_seconds = time.time() - _buy_ts
        if _held_seconds < _min_hold:
            _remaining_min = (_min_hold - _held_seconds) / 60
            logger.info(
                "[SELL BLOCKED F-2] Vente signal bloquée — position ouverte depuis %.0f min, "
                "min hold = %.0f min (1 bougie %s). Encore %.0f min à attendre.",
                _held_seconds / 60, _min_hold / 60, ctx.time_interval, _remaining_min,
            )
            sell_triggered = False

    # === EXÉCUTION VENTE SIGNAL (100 % du reste) ===
    if sell_triggered and sell_reason == 'SIGNAL':
        try:
            # F-1: Annuler le SL exchange pour libérer les coins lockés avant vente
            _cancel_exchange_sl(ctx, deps)
            qty_to_sell = ctx.coin_balance

            if qty_to_sell and qty_to_sell > 0:
                # Arrondir la quantité selon les règles d'échange
                quantity_decimal = Decimal(str(qty_to_sell))
                quantity_rounded = (quantity_decimal // ctx.step_size_dec) * ctx.step_size_dec
                if quantity_rounded < ctx.min_qty_dec:
                    quantity_rounded = quantity_decimal
                if quantity_rounded > ctx.max_qty_dec:
                    quantity_rounded = ctx.max_qty_dec

                notional_value = float(quantity_rounded) * ctx.current_price

                if quantity_rounded >= ctx.min_qty_dec and notional_value >= ctx.min_notional:
                    qty_str = f"{quantity_rounded:.{ctx.step_decimals}f}"
                    sell_order = deps.market_sell_fn(symbol=ctx.real_trading_pair, quantity=qty_str)
                    if sell_order and sell_order.get('status') == 'FILLED':
                        logger.info(f"[SIGNAL] Vente exécutée et confirmée : {qty_str} {ctx.coin_symbol}")

                        executed_price = ctx.current_price
                        total_usdc_received = float(qty_str) * executed_price

                        sell_type_desc = "Signal de vente (croisement baissier)"
                        position_closed = "100% (solde restant)"

                        # Reset état complet après vente SIGNAL
                        ps.update({
                            'entry_price': None, 'max_price': None, 'stop_loss': None,
                            'trailing_stop': None, 'trailing_stop_activated': False,
                            'atr_at_entry': None, 'stop_loss_at_entry': None,
                            'trailing_activation_price_at_entry': None,
                            'initial_position_size': None,
                            'last_order_side': None,
                            'partial_taken_1': False,
                            'partial_taken_2': False,
                            'breakeven_triggered': False,  # B-3
                            # F-COH: libérer le verrou des params d’entrée
                            'entry_scenario': None, 'entry_timeframe': None,
                            'entry_ema1': None, 'entry_ema2': None,
                        })
                        deps.save_fn()
                        logger.info("[SIGNAL] État réinitialisé après vente complète")

                        # === EMAIL VENTE RÉUSSIE ===
                        # Capturer le pnl avant le reset de pair_state (entry_price = None après)
                        _pnl_pct_signal = ((executed_price / entry_price_for_panel) - 1) * 100 if entry_price_for_panel and entry_price_for_panel > 0 else None
                        _pnl_usdc_signal = (float(executed_price) - entry_price_for_panel) * float(qty_str) if entry_price_for_panel and entry_price_for_panel > 0 else None
                        deps.update_daily_pnl_fn(_pnl_usdc_signal)
                        extra = f"Timeframe : {ctx.time_interval}\nEMA : {ctx.ema1_period}/{ctx.ema2_period}\nScenario : {ctx.scenario}"
                        subj, body = sell_executed_email(
                            pair=ctx.real_trading_pair, qty=float(qty_str), price=executed_price,
                            usdc_received=total_usdc_received, sell_reason=sell_type_desc,
                            pnl_pct=_pnl_pct_signal,
                            extra_details=extra
                        )
                        if deps.is_valid_stop_loss_fn(ctx.real_trading_pair, qty_str, executed_price):
                            try:
                                deps.send_alert_fn(subject=subj, body_main=body, client=deps.client)
                                logger.info("[SIGNAL] E-mail d'alerte envoyé pour la vente")
                            except Exception as e:
                                logger.error(f"[SIGNAL] L'envoi de l'e-mail a echoué : {e}")
                        else:
                            logger.warning(f"[SIGNAL] Email NON ENVOYÉ : paramètres invalides (symbol={ctx.real_trading_pair}, qty={qty_str}, price={executed_price})")

                        # Rafraîchir le balance après vente
                        account_info = deps.client.get_account()
                        _, ctx.coin_balance, _, _ = _get_coin_balance(account_info, ctx.coin_symbol)

                        # Journal de trading
                        try:
                            logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                            _entry_px = ps.get('entry_price') or 0
                            _pnl = (float(executed_price) - _entry_px) * float(qty_str) if _entry_px and executed_price and qty_str else None
                            _pnl_pct = ((float(executed_price) / _entry_px) - 1) if _entry_px and executed_price else None
                            _buy_ts_sig = ps.get('buy_timestamp')
                            _duration_s_sig = (time.time() - _buy_ts_sig) if _buy_ts_sig else None
                            _, _equity_after, _, _ = _get_coin_balance(account_info, 'USDC')
                            log_trade(
                                logs_dir=logs_dir,
                                pair=ctx.real_trading_pair,
                                side='sell',
                                quantity=float(qty_str) if qty_str else 0,
                                price=float(executed_price) if executed_price else 0,
                                fee=float(qty_str or 0) * deps.config.taker_fee * float(executed_price or 0),
                                scenario=ctx.scenario,
                                timeframe=ctx.time_interval,
                                pnl=_pnl,
                                pnl_pct=_pnl_pct,
                                equity_after=_equity_after,
                                extra={'sell_reason': 'SIGNAL', 'position_closed': position_closed, 'duration_s': _duration_s_sig},
                            )
                        except Exception as journal_err:
                            logger.error(f"[JOURNAL] Erreur écriture vente: {journal_err}")
                    else:
                        # Signal sell order sent but NOT FILLED
                        _status = sell_order.get('status', 'UNKNOWN') if sell_order else 'None'
                        logger.warning(f"[SIGNAL] Ordre de vente non FILLED (statut={_status})")
                        try:
                            deps.send_alert_fn(
                                subject=f"[ALERTE] Vente signal NON FILLED — {ctx.real_trading_pair}",
                                body_main=(
                                    f"Ordre de vente signal non exécuté.\n\n"
                                    f"Paire : {ctx.real_trading_pair}\n"
                                    f"Quantité : {qty_str}\n"
                                    f"Statut : {_status}\n"
                                    f"Prix courant : {ctx.current_price:.4f} USDC\n\n"
                                    f"Action : le bot réessaiera au prochain cycle."
                                ),
                                client=deps.client,
                            )
                        except Exception as _email_err:
                            logger.error(f"[SIGNAL] Échec envoi email vente non-filled: {_email_err}")
                else:
                    if quantity_rounded < ctx.min_qty_dec:
                        logger.warning(f"[SIGNAL] Vente bloquée : Quantité {quantity_rounded} < min_qty {ctx.min_qty_dec}")
                    if notional_value < ctx.min_notional:
                        logger.warning(f"[SIGNAL] Vente bloquée : Valeur {notional_value:.2f} USDC < MIN_NOTIONAL {ctx.min_notional:.2f} USDC")

                    # F-3: Email d'alerte sur vente bloquée
                    try:
                        deps.send_alert_fn(
                            subject=f"[ALERTE] Vente signal BLOQUÉE — {ctx.real_trading_pair}",
                            body_main=(
                                f"Vente signal bloquée pour {ctx.real_trading_pair}.\n\n"
                                f"Quantité tentée: {quantity_rounded} (min_qty: {ctx.min_qty_dec})\n"
                                f"Valeur notionnelle: {notional_value:.2f} USDC (min: {ctx.min_notional:.2f})\n"
                                f"Solde total: {ctx.coin_balance:.8f} {ctx.coin_symbol}\n"
                                f"Solde libre: {ctx.coin_balance_free:.8f} {ctx.coin_symbol}\n"
                                f"Solde locké: {ctx.coin_balance_locked:.8f} {ctx.coin_symbol}\n\n"
                                f"Action requise: vérifier les ordres ouverts sur Binance."
                            ),
                            client=deps.client,
                        )
                    except Exception as _email_err:
                        logger.error(f"[SIGNAL] Échec envoi email vente bloquée: {_email_err}")

                        # Si un reliquat < min_qty subsiste, tenter une vente finale
                        remaining_dec = Decimal(str(ctx.coin_balance))
                        if remaining_dec > 0 and remaining_dec < (ctx.min_qty_dec * _QTY_OVERSHOOT_TOLERANCE):
                            try:
                                qty_str_remaining = f"{remaining_dec:.{ctx.step_decimals}f}"
                                logger.info(f"[SELL] Reliquat détecté ({qty_str_remaining}), tentative de vente finale")
                                dust_sell_order = deps.market_sell_fn(symbol=ctx.real_trading_pair, quantity=qty_str_remaining)
                                if dust_sell_order and dust_sell_order.get('status') == 'FILLED':
                                    logger.info(f"[SELL] Reliquat vendu avec succès : {qty_str_remaining} {ctx.coin_symbol}")
                                    ctx.coin_balance = 0.0
                                    ps.update({
                                        'entry_price': None, 'max_price': None, 'stop_loss': None,
                                        'trailing_stop': None, 'trailing_stop_activated': False,
                                        'atr_at_entry': None, 'stop_loss_at_entry': None,
                                        'trailing_activation_price_at_entry': None,
                                        'initial_position_size': None,
                                        'last_order_side': None,
                                        'partial_taken_1': False,
                                        'partial_taken_2': False,
                                        'breakeven_triggered': False,  # B-3
                                        # F-COH: libérer le verrou des params d’entrée
                                        'entry_scenario': None, 'entry_timeframe': None,
                                        'entry_ema1': None, 'entry_ema2': None,
                                    })
                                    deps.save_fn()
                                else:
                                    logger.warning("[SELL] Reliquat non vendu (< min_qty)")
                            except Exception as e:
                                logger.error(f"[SELL] Erreur lors de la vente du reliquat: {e}")
        except Exception as e:
            logger.error(f"[VENTE] Erreur lors de l'exécution : {e}")

    # Afficher panel VENTE
    display_sell_signal_panel(
        row=ctx.row, coin_balance=ctx.coin_balance, pair_state=ps,
        sell_triggered=sell_triggered, console=deps.console, coin_symbol=ctx.coin_symbol,
        sell_reason=sell_reason, best_params=ctx.best_params,
    )


def _compute_buy_quantity(
    sizing_mode: str,
    usdc_for_buy: float,
    usdc_balance: float,
    entry_price: float,
    atr_value: Optional[float],
    min_qty_dec: Decimal,
    max_qty_dec: Decimal,
    step_size_dec: Decimal,
    step_decimals: int,
    risk_per_trade: float = 0.01,
    atr_stop_multiplier: float = 1.5,
) -> Optional[Tuple[Decimal, str, float]]:
    """C-02: Calcule la quantité d'achat — modes baseline et risk.

    Fonction pure — aucun side-effect.
    Retourne (quantity_rounded, qty_str, quote_amount) ou None si quantité < min_qty.

    Modes:
    - baseline: qty = min(usdc_for_buy, usdc_balance) * 0.98 / entry_price
    - risk: qty = min(risk_qty, affordable_qty) où risk_qty = (capital * risk_per_trade) / (atr_stop_multiplier * ATR)
    """
    effective_capital = min(usdc_for_buy, usdc_balance)

    if sizing_mode == 'risk' and atr_value and atr_value > 0 and entry_price > 0:
        qty_by_risk = compute_position_size_by_risk(
            equity=effective_capital,
            atr_value=atr_value,
            entry_price=entry_price,
            risk_pct=risk_per_trade,
            stop_atr_multiplier=atr_stop_multiplier,
        )
        max_affordable = (effective_capital * config.position_size_cushion) / entry_price
        gross_coin = min(max_affordable, qty_by_risk)
    elif sizing_mode not in ('baseline', 'risk'):
        logger.warning("[BUY] sizing_mode='%s' inconnu — fallback baseline", sizing_mode)
        gross_coin = (effective_capital * config.position_size_cushion) / entry_price if entry_price > 0 else 0.0
    else:
        # baseline (or risk with invalid ATR → fallback)
        gross_coin = (effective_capital * config.position_size_cushion) / entry_price if entry_price > 0 else 0.0

    # Arrondir selon les règles d'échange
    quantity_decimal = Decimal(str(gross_coin))
    quantity_rounded = (quantity_decimal // step_size_dec) * step_size_dec
    if quantity_rounded < min_qty_dec:
        quantity_rounded = quantity_decimal
    if quantity_rounded > max_qty_dec:
        quantity_rounded = max_qty_dec

    if quantity_rounded >= min_qty_dec:
        qty_str = f"{quantity_rounded:.{step_decimals}f}"
        quote_amount = float(quantity_rounded) * entry_price
        return (quantity_rounded, qty_str, quote_amount)
    return None



def _validate_buy_preconditions(
    ctx: '_TradeCtx',
    deps: '_TradingDeps',
    buy_condition: bool,
    usdc_for_buy: float,
) -> bool:
    """P0-03: Vérifie toutes les gardes pré-achat.

    Returns True si l'achat peut procéder, False avec log explicite si bloqué.
    Couvre : OOS gates, cooldown A-3, anti-double-buy, limite perte journalière,
    buy_condition, capital et guard ATR.
    """
    ps = ctx.pair_state

    # P0-BUY: guard explicite — si l'état dit qu'on a déjà acheté, ne JAMAIS re-acheter
    if ps.get('last_order_side') == 'BUY':
        logger.info(
            "[BUY BLOCKED P0-BUY] %s — last_order_side='BUY' dans pair_state, achat bloqué",
            ctx.backtest_pair,
        )
        return False

    if ps.get('oos_blocked'):
        logger.warning(
            "[BUY BLOCKED P0-03] %s — OOS gates non passées depuis %s. "
            "Seule la gestion des stops est active.",
            ctx.backtest_pair,
            time.ctime(ps.get('oos_blocked_since', 0)),
        )
        return False

    if ps.get('_stop_loss_cooldown_until', 0) > time.time():
        _cd_remaining = ps.get('_stop_loss_cooldown_until', 0.0) - time.time()
        logger.info(
            "[BUY BLOCKED A-3] %s — Cooldown post-stop-loss actif (%.0f min restantes)",
            ctx.backtest_pair, _cd_remaining / 60,
        )
        return False

    if ctx.orders and deps.check_order_executed_fn(ctx.orders, 'BUY'):
        logger.warning(
            "[BUY] Anti-double-buy: dernier ordre détecté comme BUY FILLED – achat ignoré pour ce cycle"
        )
        return False

    if deps.is_loss_limit_fn():
        logger.warning(
            "[BUY BLOCKED P5-A] %s — Limite perte journalière atteinte. "
            "Aucun nouvel achat jusqu'à 00:00 UTC.",
            ctx.backtest_pair,
        )
        return False

    if not buy_condition or usdc_for_buy <= 0:
        return False

    # ATR guard: bloquer l'achat si ATR absent/NaN/<=0 -> stop-loss incalculable
    atr_value = ctx.row.get('atr', None)
    _atr_invalid = (
        atr_value is None
        or (isinstance(atr_value, float) and math.isnan(atr_value))
        or float(atr_value) <= 0
    )
    if _atr_invalid:
        logger.warning(
            "[BUY BLOCKED P0-SL-GUARD] %s — ATR indisponible (atr=%s). "
            "Impossible de calculer un stop-loss. Achat refusé.",
            ctx.real_trading_pair, atr_value,
        )
        return False

    return True


def _compute_and_validate_quantity(
    ctx: '_TradeCtx',
    deps: '_TradingDeps',
    usdc_for_buy: float,
) -> 'Optional[Tuple[Decimal, str, float, float, float]]':
    """P0-03: Optimise le prix d'entrée (sniper) et calcule la taille de position.

    Précondition : ATR déjà validé par _validate_buy_preconditions.
    Returns (quantity_rounded, qty_str, quote_amount, entry_price, atr_value) ou None.
    """
    atr_value = ctx.row.get('atr', None)  # validé par _validate_buy_preconditions
    entry_price = ctx.current_price

    # Optimisation sniper: chercher le meilleur prix d'entrée sur les 15min récents
    optimized_entry = deps.get_sniper_entry_fn(ctx.real_trading_pair, entry_price)
    if optimized_entry < entry_price:
        logger.info(
            "[BUY] Prix sniper optimisé: %.6f (vs spot %.6f, gain %.2f%%)",
            optimized_entry,
            entry_price,
            (entry_price - optimized_entry) / entry_price * 100,
        )
        entry_price = optimized_entry

    # C-02: Sizing délégué à _compute_buy_quantity (fonction pure, testable)
    qty_result = _compute_buy_quantity(
        sizing_mode=ctx.sizing_mode,
        usdc_for_buy=usdc_for_buy,
        usdc_balance=ctx.usdc_balance,
        entry_price=entry_price,
        atr_value=atr_value,
        min_qty_dec=ctx.min_qty_dec,
        max_qty_dec=ctx.max_qty_dec,
        step_size_dec=ctx.step_size_dec,
        step_decimals=ctx.step_decimals,
        risk_per_trade=deps.config.risk_per_trade,
        atr_stop_multiplier=deps.config.atr_stop_multiplier,
    )
    if qty_result is None:
        logger.warning("[BUY] Quantité calculée < min_qty, achat annulé")
        return None

    quantity_rounded, qty_str, quote_amount = qty_result
    return (quantity_rounded, qty_str, quote_amount, entry_price, atr_value)


def _place_buy_and_verify(
    ctx: '_TradeCtx',
    deps: '_TradingDeps',
    qty_str: str,
    quote_amount: float,
    entry_price: float,
    atr_value: float,
    quantity_rounded: 'Decimal',
    usdc_for_buy: float,
) -> 'Optional[str]':
    """P0-03: Place l'ordre d'achat, traite le fill, envoie l'email et met à jour pair_state.

    Returns actual_qty_str si l'ordre est FILLED, None sinon.
    """
    ps = ctx.pair_state

    logger.info("[BUY] Sizing mode: %s", ctx.sizing_mode)
    logger.info("[BUY] Quantité calculée: %s %s (~%.2f USDC)", qty_str, ctx.coin_symbol, quote_amount)

    # WAL B-05: enregistrer l'intent avant l'appel Binance
    wal_write(OP_BUY_INTENT, ctx.real_trading_pair,
              qty_str=qty_str, entry_price=entry_price, atr_value=atr_value,
              sl_price=entry_price - (deps.config.atr_stop_multiplier * atr_value) if atr_value else None)

    buy_order = deps.market_buy_fn(symbol=ctx.real_trading_pair, quoteOrderQty=quote_amount)

    if not buy_order or buy_order.get('status') != 'FILLED':
        _status = buy_order.get('status', 'UNKNOWN') if buy_order else 'None'
        logger.warning("[BUY] Ordre d'achat non FILLED (statut=%s)", _status)
        try:
            deps.send_alert_fn(
                subject=f"[ALERTE] Achat NON FILLED — {ctx.real_trading_pair}",
                body_main=(
                    f"Ordre d'achat non exécuté.\n\n"
                    f"Paire : {ctx.real_trading_pair}\n"
                    f"Quantité : {qty_str}\n"
                    f"Montant : {quote_amount:.2f} USDC\n"
                    f"Statut : {_status}\n"
                    f"Prix courant : {ctx.current_price:.4f} USDC\n\n"
                    f"Action : le bot réessaiera au prochain cycle."
                ),
                client=deps.client,
            )
        except Exception as _email_err:
            logger.error("[BUY] Échec envoi email achat non-filled: %s", _email_err)
        return None

    # Quantité nette réellement reçue (commission déduite)
    try:
        _exec_raw = Decimal(str(buy_order.get('executedQty', qty_str)))
        _fills = buy_order.get('fills', [])
        _commission = sum(
            Decimal(str(f.get('commission', '0')))
            for f in _fills
            if str(f.get('commissionAsset', '')).upper() == ctx.coin_symbol.upper()
        )
        _net_qty = _exec_raw - _commission
        _exec_snapped = (_net_qty // ctx.step_size_dec) * ctx.step_size_dec
        actual_qty_str = f"{_exec_snapped:.{ctx.step_decimals}f}"
        if _commission > 0:
            logger.info(
                "[BUY] Commission déduite en %s: %s -> quantité nette: %s",
                ctx.coin_symbol, _commission, actual_qty_str,
            )
    except Exception:
        actual_qty_str = qty_str

    logger.info("[BUY] Achat exécuté et confirmé : %s %s", actual_qty_str, ctx.coin_symbol)
    _actual_spent = float(buy_order.get('cummulativeQuoteQty', usdc_for_buy))
    logger.info("[BUY] Capital réellement dépensé : %.2f USDC (disponible: %.2f)", _actual_spent, usdc_for_buy)
    # WAL B-05: confirmer le fill
    wal_write(OP_BUY_CONFIRMED, ctx.real_trading_pair,
              order_id=buy_order.get('orderId'), actual_qty_str=actual_qty_str)
    logger.info("[BUY] Quantité réellement exécutée : %s %s", buy_order.get('executedQty', 'N/A'), ctx.coin_symbol)

    # === EMAIL ACHAT RÉUSSI ===
    _sl_at_entry = entry_price - (deps.config.atr_stop_multiplier * atr_value) if atr_value else None
    _sl_str = f"{_sl_at_entry:.4f} USDC" if _sl_at_entry else "N/A"
    _usdc_after = max(ctx.usdc_balance - quote_amount, 0.0)
    _sl_dist_email = entry_price - float(_sl_at_entry) if _sl_at_entry else None
    if _sl_dist_email and _sl_dist_email > 0 and usdc_for_buy > 0:
        _effective_risk_pct = float(actual_qty_str) * _sl_dist_email / usdc_for_buy * 100
    else:
        _effective_risk_pct = deps.config.risk_per_trade * 100
    extra = (
        f"Timeframe : {ctx.time_interval}\nEMA : {ctx.ema1_period}/{ctx.ema2_period}"
        f"\nScenario : {ctx.scenario}"
        f"\nStop-Loss : {_sl_str}"
        f"\nRisque max : {_effective_risk_pct:.1f}% du capital"
    )
    subj, body = buy_executed_email(
        pair=ctx.real_trading_pair,
        qty=float(actual_qty_str),
        price=entry_price,
        usdc_spent=quote_amount,
        usdc_balance_after=_usdc_after,
        extra_details=extra,
    )
    try:
        deps.send_alert_fn(subject=subj, body_main=body, client=deps.client)
        logger.info("[BUY] E-mail d'alerte envoyé pour l'achat")
    except Exception as e:
        logger.error("[BUY] L'envoi de l'e-mail a échoué : %s", e)

    # Vérifier si la position permet des partials sûrs
    can_partial = can_execute_partial_safely(
        coin_balance=float(quantity_rounded),
        current_price=entry_price,
        min_notional=ctx.min_notional,
    )
    ps.update({
        'entry_price': entry_price,
        'atr_at_entry': atr_value,
        'stop_loss_at_entry': entry_price - (deps.config.atr_stop_multiplier * atr_value) if atr_value else None,
        'stop_loss': entry_price - (deps.config.atr_stop_multiplier * atr_value) if atr_value else None,  # P1-SL: initialiser stop_loss dès l'entrée
        'trailing_activation_price_at_entry': (
            entry_price + (deps.config.atr_multiplier * atr_value)
            if atr_value
            else entry_price * (1 + deps.config.trailing_activation_pct)
        ),
        'max_price': entry_price,
        'trailing_stop_activated': False,
        'initial_position_size': float(actual_qty_str),
        'last_order_side': 'BUY',
        'partial_enabled': can_partial,
        'partial_taken_1': False,
        'partial_taken_2': False,
        'breakeven_triggered': False,  # B-3: réinitialiser à l'achat
        'buy_timestamp': time.time(),  # F-2: timestamp achat pour min hold time
        # F-COH: verrouiller les params d'entrée pour garantir la cohérence
        # signal achat <-> signal vente (même scenario/TF/EMA jusqu'à clôture)
        'entry_scenario': ctx.scenario,
        'entry_timeframe': ctx.time_interval,
        'entry_ema1': ctx.ema1_period,
        'entry_ema2': ctx.ema2_period,
    })
    deps.save_fn(force=True)  # P0-BUY: état critique post-achat, forcer la persistence

    return actual_qty_str


def _rollback_on_sl_failure(
    ctx: '_TradeCtx',
    deps: '_TradingDeps',
    actual_qty_str: str,
    reason_err: 'Optional[Exception]',
) -> None:
    """P0-03: Exécute le market-sell de rollback quand le placement SL échoue.

    Si le rollback lui-même échoue, active l'EMERGENCY HALT et envoie une alerte critique.
    """
    ps = ctx.pair_state
    try:
        deps.market_sell_fn(symbol=ctx.real_trading_pair, quantity=actual_qty_str)
        ps.update({
            'entry_price': None,
            'last_order_side': 'SELL',
            'sl_order_id': None,
            'sl_exchange_placed': False,
        })
        deps.save_fn()
        logger.critical(
            "[SL-ORDER P0-STOP] Rollback (market-sell) exécuté pour %s.",
            ctx.real_trading_pair,
        )
    except Exception as _rollback_err:
        # P0-STOP: Double échec (SL + rollback) -> EMERGENCY HALT
        logger.critical(
            "[SL-ORDER P0-STOP] ROLLBACK AUSSI ÉCHOUÉ pour %s: %s — "
            "ACTIVATION EMERGENCY HALT — POSITION EXPOSÉE SANS PROTECTION !",
            ctx.real_trading_pair, _rollback_err,
        )
        with deps.bot_state_lock:
            set_emergency_halt(
                deps.bot_state,
                f"Double échec SL+rollback pour {ctx.real_trading_pair} à "
                f"{datetime.now(timezone.utc).isoformat()}",
            )
        deps.save_fn(force=True)
        try:
            deps.send_alert_fn(
                subject=f"[EMERGENCY HALT] Position exposée {ctx.real_trading_pair}",
                body_main=(
                    f"ALERTE CRITIQUE: Le stop-loss ET le market-sell de rollback "
                    f"ont tous deux échoué.\n\n"
                    f"Paire: {ctx.real_trading_pair}\n"
                    f"Erreur SL: {reason_err}\n"
                    f"Erreur rollback: {_rollback_err}\n\n"
                    f"EMERGENCY HALT ACTIVÉ — Tous les achats sont bloqués.\n"
                    f"ACTION REQUISE: Vérifier la position manuellement sur Binance "
                    f"et supprimer la clé 'emergency_halt' du deps.bot_state pour relancer."
                ),
                client=deps.client,
            )
        except Exception as _e:
            logger.warning("[SL-ORDER] Email emergency halt impossible: %s", _e)


def _place_sl_after_buy(
    ctx: '_TradeCtx',
    deps: '_TradingDeps',
    actual_qty_str: str,
) -> bool:
    """P0-03: Place le STOP_LOSS_LIMIT exchange après achat (3 tentatives).

    Si le placement échoue, déclenche le rollback via _rollback_on_sl_failure.
    Gère aussi le cas SL-GUARD (stop_loss_at_entry nul = bug amont).
    Returns True si le SL est confirmé sur l'exchange, False si rollback déclenché.
    """
    ps = ctx.pair_state
    _sl_price = ps.get('stop_loss_at_entry')

    if _sl_price and float(_sl_price) > 0:
        _sl_placed = False
        _sl_last_err: Optional[Exception] = None
        for _sl_attempt in range(SL_MAX_RETRIES):
            try:
                _sl_result = deps.place_sl_fn(
                    symbol=ctx.real_trading_pair,
                    quantity=actual_qty_str,
                    stop_price=float(_sl_price),
                )
                ps['sl_order_id'] = _sl_result.get('orderId')
                ps['sl_exchange_placed'] = True
                deps.save_fn()
                # WAL B-05: confirmer le placement SL
                wal_write(OP_SL_PLACED, ctx.real_trading_pair,
                          sl_order_id=ps['sl_order_id'], stop_price=float(_sl_price))
                logger.info(
                    "[SL-ORDER P0-01] Stop-loss exchange placé (tentative %d): orderId=%s stop=%.8f",
                    _sl_attempt + 1, ps['sl_order_id'], _sl_price,
                )
                _sl_placed = True
                break
            except Exception as _sl_err:
                _sl_last_err = _sl_err
                logger.warning(
                    "[SL-ORDER P0-STOP] Tentative %d/3 échec SL pour %s: %s",
                    _sl_attempt + 1, ctx.real_trading_pair, _sl_err,
                )
                if _sl_attempt < SL_MAX_RETRIES - 1:
                    time.sleep(SL_BACKOFF_BASE * (2 ** _sl_attempt) + random.random())

        if not _sl_placed:
            logger.critical(
                "[SL-ORDER P0-STOP] ÉCHEC placement stop-loss après 3 tentatives pour %s: %s — "
                "ROLLBACK: market-sell d'urgence en cours.",
                ctx.real_trading_pair, _sl_last_err,
            )
            try:
                deps.send_alert_fn(
                    subject=f"[CRITIQUE P0-STOP] Stop-loss non placé {ctx.real_trading_pair}",
                    body_main=(
                        f"Le stop-loss exchange n'a pas pu être placé après 3 tentatives\n"
                        f"Paire: {ctx.real_trading_pair}\nDernière erreur: {_sl_last_err}\n\n"
                        f"ROLLBACK: market-sell d'urgence en cours."
                    ),
                    client=deps.client,
                )
            except Exception as _e:
                logger.warning("[SL-ORDER] Email alerte SL impossible: %s", _e)
            _rollback_on_sl_failure(ctx, deps, actual_qty_str, _sl_last_err)
            return False
        return True

    else:
        # P0-SL-GUARD: Ce chemin ne devrait JAMAIS être atteint grâce au guard
        # dans _validate_buy_preconditions. Si on arrive ici, c'est un bug.
        logger.critical(
            "[SL-ORDER P0-SL-GUARD] stop_loss_at_entry non défini pour %s "
            "(atr=%s). BUG: le guard pré-achat n'a pas bloqué. ROLLBACK.",
            ctx.real_trading_pair, ps.get('atr_at_entry'),
        )
        _rollback_on_sl_failure(ctx, deps, actual_qty_str, None)
        try:
            deps.send_alert_fn(
                subject=f"[CRITIQUE P0-SL-GUARD] ATR null après achat {ctx.real_trading_pair}",
                body_main=(
                    f"Le stop-loss n'a pas pu être calculé (ATR absent après achat).\n"
                    f"Paire: {ctx.real_trading_pair}\n"
                    f"ATR: {ps.get('atr_at_entry')}\n\n"
                    f"Rollback market-sell tenté automatiquement."
                ),
                client=deps.client,
            )
        except Exception as _e:
            logger.warning("[SL-GUARD] Email alerte rollback impossible: %s", _e)
        return False


def _execute_buy(ctx: '_TradeCtx', deps: '_TradingDeps') -> None:
    """C-15: Exécute un achat avec position sizing et placement SL.

    P0-03: Orchestrateur — délègue à :
      _validate_buy_preconditions -> _compute_and_validate_quantity ->
      _place_buy_and_verify -> _place_sl_after_buy -> _rollback_on_sl_failure
    """
    ps = ctx.pair_state

    # === CONDITIONS ACHAT (vérifier SEULEMENT si position fermée) ===
    check_buy_signal = deps.gen_buy_checker_fn(ctx.best_params)
    buy_condition, buy_reason = check_buy_signal(ctx.row, ctx.usdc_balance)
    usdc_balance_for_display = ctx.usdc_balance  # snapshot avant éventuel achat

    # === CAPITAL DISPONIBLE POUR ACHAT ===
    usdc_for_buy = deps.get_usdc_sells_fn(ctx.real_trading_pair)

    if usdc_for_buy <= 0:
        logger.error(
            "[BUY] ERREUR : Aucun capital disponible ! USDC des ventes = %.2f USDC", usdc_for_buy
        )
        logger.error("[BUY] Aucune vente trouvée dans l'historique depuis le dernier achat")
        logger.warning("[BUY] Conditions d'achat remplies mais TRADING BLOQUÉ - Capital insuffisant")
    else:
        logger.info("[BUY] Capital disponible (ventes depuis dernier BUY) : %.2f USDC", usdc_for_buy)

    # === GARDES PRÉ-ACHAT (OOS, cooldown, anti-double-buy, loss limit, ATR) ===
    if not _validate_buy_preconditions(ctx, deps, buy_condition, usdc_for_buy):
        display_buy_signal_panel(
            row=ctx.row, usdc_balance=usdc_balance_for_display, best_params=ctx.best_params,
            scenario=ctx.scenario, buy_condition=buy_condition, console=deps.console,
            pair_state=ps, buy_reason=buy_reason,
        )
        return

    # MULTI-PAIR: sérialiser UNIQUEMENT l'allocation USDC (fetch → sizing → buy).
    # Dès que le buy est FILLED, le USDC est dépensé côté Binance → on relâche
    # le lock pour que les autres paires puissent acheter sans attendre le SL/journal.
    _alloc_lock = deps.buy_allocation_lock
    actual_qty_str = None
    quantity_rounded = None
    entry_price = 0.0
    atr_value = 0.0

    # ── Section critique : fetch balance → sizing → place buy ──
    if _alloc_lock is not None:
        _alloc_lock.acquire()
    try:
        # Re-fetch le capital réel sous le lock (un autre thread a pu acheter entre-temps)
        usdc_for_buy = deps.get_usdc_sells_fn(ctx.real_trading_pair)
        if usdc_for_buy <= 0:
            logger.warning("[BUY] Capital épuisé après acquisition du lock — achat annulé")
            return

        # Refresh USDC balance sous le lock (garde l'ancienne si le fetch échoue ou retourne 0)
        try:
            _fresh_acc = deps.client.get_account()
            _, _fresh_usdc, _, _ = _get_coin_balance(_fresh_acc, 'USDC')
            if _fresh_usdc > 0:
                ctx.usdc_balance = _fresh_usdc
        except Exception as _refresh_err:
            logger.warning("[BUY] Refresh balance échoué sous lock: %s", _refresh_err)

        # === SIZING ===
        qty_data = _compute_and_validate_quantity(ctx, deps, usdc_for_buy)
        if qty_data is not None:
            quantity_rounded, qty_str, quote_amount, entry_price, atr_value = qty_data

            # === PLACE BUY (market order → fill immédiat) ===
            actual_qty_str = _place_buy_and_verify(
                ctx, deps, qty_str, quote_amount, entry_price, atr_value, quantity_rounded, usdc_for_buy,
            )
    except Exception as e:
        logger.error("[ACHAT] Erreur lors de l'allocation/achat : %s", e)
    finally:
        if _alloc_lock is not None:
            _alloc_lock.release()

    # ── Hors lock : SL + journal + refresh (les autres paires peuvent acheter) ──
    if actual_qty_str is not None and quantity_rounded is not None:
        try:
            # === STOP LOSS (P0-01 / P0-STOP) ===
            _place_sl_after_buy(ctx, deps, actual_qty_str)

            # === JOURNAL DE TRADING ===
            try:
                logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                log_trade(
                    logs_dir=logs_dir,
                    pair=ctx.real_trading_pair,
                    side='buy',
                    quantity=float(quantity_rounded),
                    price=entry_price,
                    fee=float(quantity_rounded) * deps.config.taker_fee * entry_price,
                    slippage=deps.config.slippage_buy,
                    scenario=ctx.scenario,
                    timeframe=ctx.time_interval,
                    ema1=ctx.best_params.get('ema1_period'),
                    ema2=ctx.best_params.get('ema2_period'),
                    atr_value=atr_value,
                    stop_price=ps.get('stop_loss_at_entry'),
                    equity_before=ctx.usdc_balance,
                )
            except Exception as journal_err:
                logger.error("[JOURNAL] Erreur écriture achat: %s", journal_err)

            # Rafraîchir le solde USDC après achat
            account_info = deps.client.get_account()
            _, ctx.usdc_balance, _, _ = _get_coin_balance(account_info, 'USDC')
        except Exception as e:
            logger.error("[POST-BUY] Erreur SL/journal : %s", e)

    # Afficher panel ACHAT (avec le solde AVANT achat si achat exécuté)
    display_buy_signal_panel(
        row=ctx.row, usdc_balance=usdc_balance_for_display, best_params=ctx.best_params,
        scenario=ctx.scenario, buy_condition=buy_condition, console=deps.console,
        pair_state=ps, buy_reason=buy_reason,
    )
