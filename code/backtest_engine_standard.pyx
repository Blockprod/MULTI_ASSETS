# cython: language_level=3
# distutils: language = c++
# Moteur de backtest STANDARD pour MULTI_SYMBOLS.py (sans HV filter, sans open_prices)

import numpy as np
cimport numpy as np
cimport cython
import pandas as pd
from libc.math cimport abs, fmax, fmin
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

DEF ATR_MULTIPLIER = 5.5   # Restore best-performing ATR multiplier
DEF ATR_STOP_MULTIPLIER = 3.0
DEF TAKER_FEE = 0.0007
DEF STOCH_THRESHOLD_BUY = 0.8
DEF STOCH_THRESHOLD_SELL = 0.2
DEF ADX_THRESHOLD = 25


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
    double initial_wallet=10000.0,
    str scenario='StochRSI',
    bint use_sma=False,
    bint use_adx=False,
    bint use_trix=False
) -> dict:
    """
    Moteur de backtest standard pour MULTI_SYMBOLS.py
    
    DIFFERENCES avec la version OPTIMIZED:
    - Pas de open_prices (fills au close)
    - Pas de hv_values (pas de filtre HV)
    - Signature simplifiée
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
    
    # Boucle principale
    for i in range(n):
        current_price = close_prices[i]
        
        # === GESTION POSITION ACTIVE ===
        if position.in_position:
            # Mise à jour trailing stop
            if current_price > position.max_price:
                position.max_price = current_price
                position.trailing_stop = position.max_price - (ATR_MULTIPLIER * atr_values[i])
            
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
            stoch_high = stoch_rsi_values[i] > STOCH_THRESHOLD_SELL
            
            sell_condition = (stop_loss_hit or trailing_stop_hit or 
                            (ema_cross_down and stoch_high))
            
            if sell_condition:
                # VENTE au close (pas de open_prices dans cette version)
                gross_proceeds = coin * current_price
                fee = gross_proceeds * TAKER_FEE
                usd = gross_proceeds - fee
                coin = 0.0
                
                trade_profit = usd - position.entry_usd_invested
                
                total_trades += 1
                
                is_winning = 1 if trade_profit > 0.0 else 0
                winning_trades += is_winning
                
                trades.append({
                    'type': 'SELL',
                    'price': current_price,
                    'profit': trade_profit
                })
                
                # Reset position
                position.in_position = False
                position.entry_price = 0.0
                position.entry_usd_invested = 0.0
                position.max_price = 0.0
                position.trailing_stop = 0.0
                position.stop_loss = 0.0
                continue
        
        # === CONDITION D'ACHAT ===
        if not position.in_position and usd > 0:
            ema_cross_up = ema1_values[i] > ema2_values[i]
            stoch_low = stoch_rsi_values[i] < STOCH_THRESHOLD_BUY
            
            buy_condition = ema_cross_up and stoch_low
            
            # Filtres additionnels
            if buy_condition and use_sma and sma_long_values is not None:
                buy_condition = current_price > sma_long_values[i]
            
            if buy_condition and use_adx and adx_values is not None:
                buy_condition = adx_values[i] > ADX_THRESHOLD
            
            if buy_condition and use_trix and trix_histo_values is not None:
                buy_condition = trix_histo_values[i] > 0.0
            
            if buy_condition:
                # ACHAT au close (pas de sniper)
                gross_coin = usd / current_price
                fee_in_coin = gross_coin * TAKER_FEE
                coin = gross_coin - fee_in_coin
                
                position.entry_usd_invested = usd
                usd = 0.0
                
                if coin > 0:
                    position.in_position = True
                    position.entry_price = current_price
                    position.max_price = current_price
                    position.trailing_stop = current_price - (ATR_MULTIPLIER * atr_values[i])
                    position.stop_loss = current_price - (ATR_STOP_MULTIPLIER * atr_values[i])
                    
                    trades.append({
                        'type': 'BUY',
                        'price': current_price
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
