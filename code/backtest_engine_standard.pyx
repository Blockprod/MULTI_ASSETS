# cython: language_level=3
# distutils: language = c++
# Moteur de backtest STANDARD pour MULTI_SYMBOLS.py (sans HV filter)
# P4-CYTHON: risk sizing + partial sells support

import numpy as np
cimport numpy as np
cimport cython
import pandas as pd
from libc.math cimport abs, fmax, fmin, isnan
from libc.stdlib cimport malloc, free

DTYPE = np.float64
ctypedef np.float64_t DTYPE_t

cdef struct TradeRecord:
    double timestamp
    int trade_type
    double price
    double quantity
    double profit

cdef struct PositionState:
    bint in_position
    double entry_price
    double entry_usd_invested
    double max_price
    double trailing_stop
    double stop_loss
    bint partial_taken_1
    bint partial_taken_2
    bint trailing_activated
    double atr_at_entry
    bint breakeven_triggered

# P2-02: Constantes migrées vers paramètres runtime (plus de DEF hardcodés)
# Valeurs par défaut conservées pour rétrocompatibilité


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
def backtest_from_dataframe_fast(
    np.ndarray[DTYPE_t, ndim=1] close_prices,
    np.ndarray[DTYPE_t, ndim=1] high_prices,
    np.ndarray[DTYPE_t, ndim=1] low_prices,
    np.ndarray[DTYPE_t, ndim=1] ema1_values,
    np.ndarray[DTYPE_t, ndim=1] ema2_values,
    np.ndarray[DTYPE_t, ndim=1] stoch_rsi_values,
    np.ndarray[DTYPE_t, ndim=1] atr_values,
    np.ndarray[DTYPE_t, ndim=1] sma_long_values=None,
    np.ndarray[DTYPE_t, ndim=1] adx_values=None,
    np.ndarray[DTYPE_t, ndim=1] trix_histo_values=None,
    np.ndarray[DTYPE_t, ndim=1] open_prices=None,
    np.ndarray[DTYPE_t, ndim=1] volume_values=None,
    np.ndarray[DTYPE_t, ndim=1] vol_sma_values=None,
    double initial_wallet=10000.0,
    str scenario='StochRSI',
    bint use_sma=False,
    bint use_adx=False,
    bint use_trix=False,
    bint use_vol_filter=False,
    double taker_fee=0.0007,
    double slippage_buy=0.0001,
    double slippage_sell=0.0001,
    double atr_multiplier=8.0,  # E-1: trailing activation optimisé
    double atr_stop_multiplier=3.0,
    double stoch_threshold_buy=0.8,
    double stoch_threshold_sell=0.2,
    double adx_threshold=25.0,
    str sizing_mode='risk',  # B-2: risk-based sizing
    double risk_per_trade=0.055,  # B-2: optimisé 5%→5.5% (Calmar max 2.004)
    bint partial_enabled=False,
    double partial_threshold_1=0.02,
    double partial_threshold_2=0.04,
    double partial_pct_1=0.50,
    double partial_pct_2=0.30,
    double min_notional=5.0,
    double stoch_threshold_buy_min=0.05,
    bint breakeven_enabled=True,
    double breakeven_trigger_pct=0.015,
    int cooldown_candles=0,
    np.ndarray[DTYPE_t, ndim=1] mtf_bullish=None,
    bint use_mtf_filter=False
) -> dict:
    """
    Moteur de backtest standard pour MULTI_SYMBOLS.py

    DIFFERENCES avec la version OPTIMIZED:
    - fills au open[i+1] avec slippage (P1-02 + P1-03)
    - Pas de hv_values (pas de filtre HV)
    - Constantes ATR/Stoch/ADX passées en paramètres runtime (P2-02)
    - Risk sizing mode + partial profit taking (P4-CYTHON)
    """

    cdef Py_ssize_t n = len(close_prices)
    cdef Py_ssize_t i
    cdef double wallet = initial_wallet
    cdef double usd = initial_wallet
    cdef double coin = 0.0
    cdef double current_price
    cdef double max_drawdown = 0.0
    cdef double peak_wallet = initial_wallet
    cdef double current_wallet
    cdef double drawdown
    cdef Py_ssize_t winning_trades = 0
    cdef Py_ssize_t total_trades = 0
    cdef double trade_profit
    cdef bint buy_condition, sell_condition
    cdef double fee, gross_proceeds
    cdef PositionState position
    cdef list trades = []
    cdef bint stop_loss_hit, trailing_stop_hit, ema_cross_down, ema_cross_up, stoch_low, stoch_high
    cdef double net_coin, fee_in_coin
    cdef int is_winning
    cdef double atr_baseline = 100.0
    cdef double atr_sum = 0.0
    cdef double fill_price
    # P4-CYTHON: risk sizing variables
    cdef bint is_risk_mode = (sizing_mode == 'risk')
    cdef double actual_cost, stop_distance, risk_amount, qty_by_risk, max_affordable, gross_coin
    # P4-CYTHON: partial sell variables
    cdef double profit_pct, partial_qty, partial_proceeds, position_value
    # B-1: trailing stop alignment variables
    cdef double trailing_distance, new_trailing
    # A-3: cooldown post-stop-loss
    cdef int cooldown_remaining = 0
    cdef bint was_stop_loss_exit = False

    if n == 0:
        return {
            'final_wallet': 0.0,
            'trades': [],
            'max_drawdown': 0.0,
            'win_rate': 0.0
        }

    # Calculer ATR baseline
    if n > 0:
        for i in range(n):
            atr_sum += atr_values[i]
        atr_baseline = atr_sum / n

    position.in_position = False
    position.entry_price = 0.0
    position.entry_usd_invested = 0.0
    position.max_price = 0.0
    position.trailing_stop = 0.0
    position.stop_loss = 0.0
    position.partial_taken_1 = False
    position.partial_taken_2 = False
    position.trailing_activated = False
    position.atr_at_entry = 0.0
    position.breakeven_triggered = False

    # Boucle principale
    for i in range(n):
        current_price = close_prices[i]

        # === GESTION POSITION ACTIVE ===
        if position.in_position:
            # Mise à jour trailing stop (B-1: delayed activation, ATR figé)
            trailing_distance = atr_multiplier * position.atr_at_entry
            if current_price > position.max_price:
                position.max_price = current_price
            if (not position.trailing_activated
                    and current_price >= position.entry_price + trailing_distance):
                position.trailing_activated = True
                position.trailing_stop = position.max_price - trailing_distance
            if position.trailing_activated:
                new_trailing = position.max_price - trailing_distance
                if new_trailing > position.trailing_stop:
                    position.trailing_stop = new_trailing

            # === B-3: BREAK-EVEN STOP ===
            if breakeven_enabled and not position.breakeven_triggered and position.entry_price > 0:
                be_profit_pct = (current_price - position.entry_price) / position.entry_price
                if be_profit_pct >= breakeven_trigger_pct:
                    # Remonter le stop loss au prix d'entrée + slippage
                    be_new_stop = position.entry_price * (1.0 + slippage_buy)
                    if be_new_stop > position.stop_loss:
                        position.stop_loss = be_new_stop
                    position.breakeven_triggered = True

            # === PARTIAL PROFIT TAKING (P4-CYTHON) ===
            if partial_enabled and coin > 0 and position.entry_price > 0:
                position_value = coin * current_price
                # Guard: position assez grosse (3× min_notional)
                if position_value >= min_notional * 3.0:
                    profit_pct = (current_price - position.entry_price) / position.entry_price

                    # Partial take 1
                    if not position.partial_taken_1 and profit_pct >= partial_threshold_1:
                        partial_qty = coin * partial_pct_1
                        if partial_qty * current_price >= min_notional:
                            partial_proceeds = partial_qty * current_price * (1.0 - taker_fee)
                            usd = usd + partial_proceeds
                            coin = coin - partial_qty
                            trades.append({
                                'type': 'partial_sell_1',
                                'price': current_price,
                                'qty': partial_qty,
                                'proceeds': partial_proceeds,
                                'profit_pct': profit_pct,
                            })
                        position.partial_taken_1 = True  # flag True even if blocked

                    # Partial take 2
                    if not position.partial_taken_2 and profit_pct >= partial_threshold_2 and coin > 0:
                        partial_qty = coin * partial_pct_2
                        if partial_qty * current_price >= min_notional:
                            partial_proceeds = partial_qty * current_price * (1.0 - taker_fee)
                            usd = usd + partial_proceeds
                            coin = coin - partial_qty
                            trades.append({
                                'type': 'partial_sell_2',
                                'price': current_price,
                                'qty': partial_qty,
                                'proceeds': partial_proceeds,
                                'profit_pct': profit_pct,
                            })
                        position.partial_taken_2 = True  # flag True even if blocked

            # Calcul drawdown
            current_wallet = usd + (coin * current_price)

            if current_wallet > peak_wallet:
                peak_wallet = current_wallet

            drawdown = (peak_wallet - current_wallet) / peak_wallet if peak_wallet > 0 else 0.0
            max_drawdown = fmax(max_drawdown, drawdown)

            # === CONDITIONS DE VENTE ===
            stop_loss_hit = current_price < position.stop_loss
            trailing_stop_hit = current_price < position.trailing_stop
            ema_cross_down = ema2_values[i] > ema1_values[i]
            stoch_high = stoch_rsi_values[i] > stoch_threshold_sell

            sell_condition = (stop_loss_hit or trailing_stop_hit or
                            (ema_cross_down and stoch_high))

            if sell_condition:
                # VENTE au open[i+1] avec slippage (P1-02 + P1-03)
                if open_prices is not None and i + 1 < n:
                    fill_price = open_prices[i + 1] * (1.0 - slippage_sell)
                else:
                    fill_price = current_price * (1.0 - slippage_sell)
                gross_proceeds = coin * fill_price
                fee = gross_proceeds * taker_fee
                usd = usd + (gross_proceeds - fee)
                coin = 0.0

                trade_profit = usd - position.entry_usd_invested

                total_trades += 1

                is_winning = 1 if trade_profit > 0.0 else 0
                winning_trades += is_winning

                trades.append({
                    'type': 'SELL',
                    'price': fill_price,
                    'profit': trade_profit
                })

                # A-3: set cooldown after stop-loss or breakeven exit
                was_stop_loss_exit = stop_loss_hit
                if was_stop_loss_exit and cooldown_candles > 0:
                    cooldown_remaining = cooldown_candles

                # Reset position
                position.in_position = False
                position.entry_price = 0.0
                position.entry_usd_invested = 0.0
                position.max_price = 0.0
                position.trailing_stop = 0.0
                position.stop_loss = 0.0
                position.partial_taken_1 = False
                position.partial_taken_2 = False
                position.trailing_activated = False
                position.atr_at_entry = 0.0
                position.breakeven_triggered = False
                continue

        # === A-3: COOLDOWN DECREMENT ===
        if not position.in_position and cooldown_remaining > 0:
            cooldown_remaining -= 1

        # === CONDITION D'ACHAT ===
        if not position.in_position and usd > 0:
            ema_cross_up = ema1_values[i] > ema2_values[i]
            stoch_low = stoch_rsi_values[i] < stoch_threshold_buy

            buy_condition = ema_cross_up and stoch_low

            # A-3: block buy during cooldown after stop-loss/breakeven exit
            if buy_condition and cooldown_remaining > 0:
                buy_condition = False

            # P2-08: Stoch RSI buy min guard
            if buy_condition:
                buy_condition = stoch_rsi_values[i] > stoch_threshold_buy_min

            # P0-SL-GUARD: block buy if ATR invalid (NaN or <= 0)
            if buy_condition:
                if isnan(atr_values[i]) or atr_values[i] <= 0:
                    buy_condition = False

            # Filtres additionnels
            if buy_condition and use_sma and sma_long_values is not None:
                buy_condition = current_price > sma_long_values[i]

            if buy_condition and use_adx and adx_values is not None:
                buy_condition = adx_values[i] > adx_threshold

            if buy_condition and use_trix and trix_histo_values is not None:
                buy_condition = trix_histo_values[i] > 0.0

            # A-1: Volume filter — volume > SMA(volume)
            if buy_condition and use_vol_filter and volume_values is not None and vol_sma_values is not None:
                if isnan(volume_values[i]) or isnan(vol_sma_values[i]) or vol_sma_values[i] <= 0:
                    buy_condition = False
                else:
                    buy_condition = volume_values[i] > vol_sma_values[i]

            # A-2: Multi-timeframe filter — 4h trend must be bullish
            if buy_condition and use_mtf_filter and mtf_bullish is not None:
                buy_condition = mtf_bullish[i] > 0.5

            if buy_condition:
                # ACHAT au open[i+1] avec slippage (P1-02 + P1-03)
                if open_prices is not None and i + 1 < n:
                    fill_price = open_prices[i + 1] * (1.0 + slippage_buy)
                else:
                    fill_price = current_price * (1.0 + slippage_buy)

                # === POSITION SIZING (P4-CYTHON) ===
                if is_risk_mode and atr_values[i] > 0 and fill_price > 0:
                    # Risk-based: risk_per_trade of equity per stop_distance
                    stop_distance = atr_stop_multiplier * atr_values[i]
                    if stop_distance > 0:
                        risk_amount = usd * risk_per_trade
                        qty_by_risk = risk_amount / stop_distance
                        max_affordable = (usd * 0.98) / fill_price
                        gross_coin = fmin(max_affordable, qty_by_risk)
                    else:
                        gross_coin = (usd * 0.98) / fill_price
                else:
                    # Baseline: invest 98% of wallet
                    gross_coin = (usd * 0.98) / fill_price if fill_price > 0 else 0.0

                if gross_coin > 0:
                    fee_in_coin = gross_coin * taker_fee
                    coin = gross_coin - fee_in_coin

                    # Deduct only actual cost — preserves uninvested cash (P1-07-FIX)
                    actual_cost = gross_coin * fill_price
                    if actual_cost > usd:
                        actual_cost = usd
                    position.entry_usd_invested = usd
                    usd = usd - actual_cost

                    if coin > 0:
                        position.in_position = True
                        position.entry_price = fill_price
                        position.max_price = fill_price
                        position.trailing_stop = 0.0
                        position.trailing_activated = False
                        position.atr_at_entry = atr_values[i]
                        position.stop_loss = fill_price - (atr_stop_multiplier * atr_values[i])
                        position.partial_taken_1 = False
                        position.partial_taken_2 = False
                        position.breakeven_triggered = False

                        trades.append({
                            'type': 'BUY',
                            'price': fill_price
                        })

    # === CALCUL FINAL ===
    cdef double final_wallet
    if position.in_position:
        final_wallet = usd + (coin * close_prices[n-1])
    else:
        final_wallet = usd

    cdef double win_rate = 0.0
    if total_trades > 0:
        win_rate = (<double>winning_trades / <double>total_trades) * 100.0

    return {
        'final_wallet': final_wallet,
        'trades': trades,
        'max_drawdown': max_drawdown,
        'win_rate': win_rate,
        'total_trades': total_trades,
        'winning_trades': winning_trades
    }
