"""
trade_helpers.py — Trade analysis helper functions.

Extracted from MULTI_SYMBOLS.py (P3-SRP).  Groups pure or near-pure helper
functions used by the live trading engine : order history analysis, sniper
entry optimisation, partial-exit detection, and Calmar-based selection.

Public API
----------
- ``get_sniper_entry_price``
- ``get_last_sell_trade_usdc``
- ``get_usdc_from_all_sells_since_last_buy``
- ``check_partial_exits_from_history``
- ``check_if_order_executed``
- ``select_best_by_calmar``
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from bot_config import extract_coin_from_pair, retry_with_backoff
from constants import (
    PARTIAL_1_PROFIT_PCT,
    PARTIAL_2_PROFIT_PCT,
    PARTIAL_1_QTY_MIN,
    PARTIAL_1_QTY_MAX,
    PARTIAL_2_QTY_MIN,
    PARTIAL_2_QTY_MAX,
    SNIPER_BAND_PCT,
)

logger = logging.getLogger(__name__)


# --- Sniper Entry Optimisation -----------------------------------------------

def get_sniper_entry_price(
    pair_symbol: str,
    signal_price: float,
    max_wait_candles: int = 4,
    *,
    fetch_data_fn: Optional[Callable[..., pd.DataFrame]] = None,
    kline_interval_15m: str = '15m',
) -> float:
    """Optimise le prix d'entrée via la timeframe 15 min (mode *sniper*).

    Scanne les ``max_wait_candles`` dernières bougies 15 min et retourne le
    meilleur *low* dans une bande de ±2 % autour du signal.

    Parameters
    ----------
    pair_symbol : str
        Paire de trading.
    signal_price : float
        Prix au moment du signal.
    max_wait_candles : int
        Nombre de bougies 15 min à analyser.
    fetch_data_fn : callable, optional
        ``fetch_data_fn(pair, interval, start) -> DataFrame``.
    kline_interval_15m : str
        Constante intervalle Binance pour 15 min.

    Returns
    -------
    float
        Prix d'entrée optimisé (≤ ``signal_price``).
    """
    try:
        if fetch_data_fn is None:
            return signal_price
        df_15m = fetch_data_fn(pair_symbol, kline_interval_15m, "1 day ago")
        if df_15m.empty or len(df_15m) < 20:
            return signal_price

        recent_candles = df_15m.tail(max_wait_candles)
        best_price = signal_price

        for _, candle in recent_candles.iterrows():
            candle_price = candle['low']
            price_diff_pct = abs(candle_price - signal_price) / signal_price * 100
            if price_diff_pct <= SNIPER_BAND_PCT and candle_price < best_price:
                best_price = candle_price

        if best_price < signal_price:
            improvement = (signal_price - best_price) / signal_price * 100
            logger.debug(
                f"Optimisation sniper: amelioration de {improvement:.2f}%% "
                f"(prix: {best_price:.8f})"
            )

        return best_price

    except Exception as e:
        logger.debug(f"Erreur optimisation entree sniper: {e}")
        return signal_price


# --- Trade History Analysis --------------------------------------------------

@retry_with_backoff(max_retries=3, base_delay=1.0)
def get_last_sell_trade_usdc(
    real_trading_pair: str,
    client: Any,
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Retourne les détails du dernier ordre SELL exécuté.

    Agrège les fills multi-trades d'un même ``orderId``.

    Parameters
    ----------
    real_trading_pair : str
        Paire de trading.
    client : BinanceFinalClient
        Client Binance.

    Returns
    -------
    tuple[float | None, float | None, str | None]
        ``(amount_usdc, fee, fee_asset)`` ou ``(None, None, None)``
        si aucun SELL trouvé.
    """
    try:
        coin_symbol, quote_currency = extract_coin_from_pair(real_trading_pair)
        trades = client.get_my_trades(symbol=real_trading_pair, limit=100)
        logger.debug(
            f"Trades recuperes pour {coin_symbol}/{quote_currency} : "
            f"{len(trades) if trades else 0} trades"
        )
        if not trades:
            logger.warning("Aucun trade trouve pour cette paire.")
            return None, None, None

        aggregated_sells: Dict[int, float] = {}
        aggregated_commissions: Dict[int, float] = {}
        commission_assets: Dict[int, str] = {}
        for trade in trades:
            if (
                'isBuyer' not in trade
                or 'quoteQty' not in trade
                or 'orderId' not in trade
            ):
                logger.error(f"Trade mal forme : {trade}")
                continue
            if not trade['isBuyer'] and float(trade['quoteQty']) > 0:
                order_id = trade['orderId']
                quote_qty = float(trade['quoteQty'])
                commission = float(trade.get('commission', 0))
                commission_asset = trade.get('commissionAsset', 'UNKNOWN')
                if order_id not in aggregated_sells:
                    aggregated_sells[order_id] = 0.0
                    aggregated_commissions[order_id] = 0.0
                    commission_assets[order_id] = commission_asset
                aggregated_sells[order_id] += quote_qty
                aggregated_commissions[order_id] += commission

        for trade in reversed(trades):
            oid = trade['orderId']
            if oid in aggregated_sells:
                total_amount = aggregated_sells[oid]
                total_fee = aggregated_commissions[oid]
                fee_asset = commission_assets[oid]
                logger.debug(
                    f"Derniere vente : Order ID={oid}, "
                    f"Montant={total_amount:.8f} {quote_currency}, "
                    f"Frais={total_fee:.8f} {fee_asset}"
                )
                return total_amount, total_fee, fee_asset
        logger.info("Aucune vente valide trouvee.")
        return None, None, None
    except Exception as e:
        logger.error(f"Erreur recuperation derniere vente : {e}")
        return None, None, None


def get_usdc_from_all_sells_since_last_buy(
    real_trading_pair: str,
    client: Any,
) -> float:
    """Retourne le montant total USDC net de toutes les ventes depuis le
    dernier BUY.  Utilisé pour calculer le capital disponible.

    Parameters
    ----------
    real_trading_pair : str
        Paire de trading.
    client : BinanceFinalClient
        Client Binance.

    Returns
    -------
    float
        Montant net USDC (0.0 si aucun trade trouvé).
    """
    try:
        _, quote_currency = extract_coin_from_pair(real_trading_pair)
        trades = client.get_my_trades(symbol=real_trading_pair, limit=500)

        if not trades:
            logger.warning(f"[CAPITAL] Aucun trade trouve pour {real_trading_pair}")
            return 0.0

        last_buy_time = None
        for trade in reversed(trades):
            if trade.get('isBuyer', False):
                last_buy_time = trade['time']
                logger.info(
                    f"[CAPITAL] Dernier BUY trouve a "
                    f"{datetime.fromtimestamp(last_buy_time / 1000).strftime('%Y-%m-%d %H:%M:%S')}"
                )
                break

        if last_buy_time is None:
            logger.warning("[CAPITAL] Aucun BUY trouve dans l'historique")
            return 0.0

        total_usdc = 0.0
        sell_count = 0
        for trade in trades:
            if not trade.get('isBuyer') and trade['time'] > last_buy_time:
                quote_qty = float(trade.get('quoteQty', 0))
                commission = float(trade.get('commission', 0))
                commission_asset = trade.get('commissionAsset', '')
                if commission_asset == quote_currency:
                    net_usdc = quote_qty - commission
                else:
                    net_usdc = quote_qty
                total_usdc += net_usdc
                sell_count += 1

        logger.info(
            f"[CAPITAL] {sell_count} ventes trouvees depuis dernier BUY = "
            f"{total_usdc:.2f} {quote_currency}"
        )
        return total_usdc

    except Exception as e:
        logger.error(f"[CAPITAL] Erreur lors de la recuperation : {e}")
        return 0.0


def check_partial_exits_from_history(
    real_trading_pair: str,
    entry_price: float,
    client: Any,
) -> Tuple[bool, bool]:
    """Vérifie dans l'historique Binance si les ventes partielles ont déjà
    été exécutées.  Reconstruit l'état réel depuis l'API.

    - PARTIAL-1 : ~50 % du BUY vendu à ≥ entry_price × 1.02
    - PARTIAL-2 : ~30 % du BUY vendu à ≥ entry_price × 1.04 (après P1)

    Parameters
    ----------
    real_trading_pair : str
        Paire de trading.
    entry_price : float
        Prix d'entrée de la position.
    client : BinanceFinalClient
        Client Binance.

    Returns
    -------
    tuple[bool, bool]
        ``(partial_1_executed, partial_2_executed)``
    """
    try:
        coin_symbol, _ = extract_coin_from_pair(real_trading_pair)
        trades = client.get_my_trades(symbol=real_trading_pair, limit=500)

        if not trades or entry_price is None or entry_price <= 0:
            return False, False

        last_buy_time = None
        last_buy_qty = 0.0
        for trade in reversed(trades):
            if trade.get('isBuyer', False):
                last_buy_time = trade['time']
                last_buy_qty += float(trade.get('qty', 0))
                break

        if last_buy_time is None:
            logger.debug("[PARTIAL-CHECK] Aucun BUY trouve dans l'historique")
            return False, False

        partial_threshold_1 = entry_price * PARTIAL_1_PROFIT_PCT  # +2%
        partial_threshold_2 = entry_price * PARTIAL_2_PROFIT_PCT  # +4%

        sells_after_buy = []
        for trade in trades:
            if not trade.get('isBuyer') and trade['time'] > last_buy_time:
                sells_after_buy.append({
                    'qty': float(trade.get('qty', 0)),
                    'price': float(trade.get('price', 0)),
                    'time': trade['time'],
                })

        if not sells_after_buy:
            logger.debug("[PARTIAL-CHECK] Aucune vente apres le dernier BUY")
            return False, False

        sells_after_buy.sort(key=lambda x: x['time'])

        partial_1_detected = False
        partial_2_detected = False

        for sell in sells_after_buy:
            sell_qty = sell['qty']
            sell_price = sell['price']

            if (
                not partial_1_detected
                and PARTIAL_1_QTY_MIN <= (sell_qty / last_buy_qty) <= PARTIAL_1_QTY_MAX
                and sell_price >= partial_threshold_1
            ):
                partial_1_detected = True
                logger.info(
                    f"[PARTIAL-CHECK] PARTIAL-1 detecte : {sell_qty:.8f} "
                    f"{coin_symbol} a {sell_price:.4f} USDC (~50%%)"
                )
            elif (
                not partial_2_detected
                and PARTIAL_2_QTY_MIN <= (sell_qty / last_buy_qty) <= PARTIAL_2_QTY_MAX
                and sell_price >= partial_threshold_2
                and partial_1_detected
            ):
                partial_2_detected = True
                logger.info(
                    f"[PARTIAL-CHECK] PARTIAL-2 detecte : {sell_qty:.8f} "
                    f"{coin_symbol} a {sell_price:.4f} USDC (~30%%)"
                )

        logger.info(
            f"[PARTIAL-CHECK] Etat depuis API : "
            f"PARTIAL-1={partial_1_detected}, PARTIAL-2={partial_2_detected}"
        )
        return partial_1_detected, partial_2_detected

    except Exception as e:
        logger.error(f"[PARTIAL-CHECK] Erreur lors de la verification : {e}")
        return False, False


# --- Pure Helpers ------------------------------------------------------------

def check_if_order_executed(orders: List[Dict[str, Any]], order_type: str) -> bool:
    """Vérifie si un ordre donné (achat ou vente) a déjà été exécuté.

    Regarde uniquement le *dernier* ordre de la liste.

    Parameters
    ----------
    orders : list[dict]
        Liste d'ordres Binance.
    order_type : str
        ``'BUY'`` ou ``'SELL'``.

    Returns
    -------
    bool
        ``True`` si le dernier ordre est ``FILLED`` et du type demandé.
    """
    if orders:
        last_order = orders[-1]
        last_order_type = last_order['side']
        last_order_status = last_order['status']
        if last_order_status == 'FILLED' and last_order_type == order_type:
            return True
    return False


def select_best_by_calmar(pool: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sélectionne le meilleur résultat de backtest par ratio de Calmar
    (ROI / max_drawdown).  Centralise la logique de sélection (P2-03 / C-07).

    Parameters
    ----------
    pool : list[dict]
        Liste de résultats de backtest.  Chaque dict doit contenir
        ``'final_wallet'``, ``'initial_wallet'`` et ``'max_drawdown'``.

    Returns
    -------
    dict
        Résultat avec le meilleur ratio de Calmar.
    """
    def _key(x: dict) -> float:
        roi = (
            (x['final_wallet'] - x['initial_wallet'])
            / max(x['initial_wallet'], 1.0)
        )
        dd = max(x.get('max_drawdown', 0.001), 0.001)
        return roi / dd

    return max(pool, key=_key)
