# cython: language_level=3
# distutils: language = c++
# Moteur de backtest optimisé en Cython pour 30-50x accélération

import numpy as np
cimport numpy as np
cimport cython
import pandas as pd
from libc.math cimport abs, fmax, fmin
from libc.stdlib cimport malloc, free

DTYPE = np.float64
ctypedef np.float64_t DTYPE_t

# Structure pour éviter la création de dicts en boucle (plus rapide)
cdef struct TradeRecord:
    double timestamp
    int trade_type  # 0=BUY, 1=SELL
    double price
    double quantity
    double profit

cdef struct PositionState:
    bint in_position
    double entry_price
    double entry_usd_invested  # Track USD invested at entry
    double max_price
    double trailing_stop
    double stop_loss

# Constantes
DEF ATR_MULTIPLIER = 5.5  # Aligned with backtest_engine_standard.pyx and Python engine (P0.5 audit fix)
DEF ATR_STOP_MULTIPLIER = 3.0
DEF TAKER_FEE = 0.0007
DEF STOCH_THRESHOLD_BUY = 0.8
DEF STOCH_THRESHOLD_SELL = 0.2
DEF ADX_THRESHOLD = 25
DEF SNIPER_LOOKBACK = 4  # Nombre de bougies à analyser
DEF SNIPER_MAX_DEVIATION = 0.02  # 2% d'écart maximum


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
cdef double optimize_entry_price_sniper(double signal_price, 
                                        np.ndarray[DTYPE_t, ndim=1] recent_lows,
                                        int lookback=SNIPER_LOOKBACK):
    """
    Optimisation sniper ultra-rapide en Cython.
    Cherche le meilleur prix d'entrée dans les dernières bougies.
    """
    cdef double best_price = signal_price
    cdef double candle_low, price_diff_pct
    cdef int i
    cdef int n = recent_lows.shape[0]
    cdef int start_idx = max(0, n - lookback)
    
    # Parcourir les dernières bougies
    for i in range(start_idx, n):
        candle_low = recent_lows[i]
        
        # Vérifier l'écart de prix (max 2%)
        price_diff_pct = abs(candle_low - signal_price) / signal_price
        
        if price_diff_pct <= SNIPER_MAX_DEVIATION and candle_low < best_price:
            best_price = candle_low
    
    return best_price


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
def backtest_from_dataframe_fast(
    np.ndarray[DTYPE_t, ndim=1] close_prices,
    np.ndarray[DTYPE_t, ndim=1] open_prices,
    np.ndarray[DTYPE_t, ndim=1] high_prices,
    np.ndarray[DTYPE_t, ndim=1] low_prices,
    np.ndarray[DTYPE_t, ndim=1] ema1_values,
    np.ndarray[DTYPE_t, ndim=1] ema2_values,
    np.ndarray[DTYPE_t, ndim=1] stoch_rsi_values,
    np.ndarray[DTYPE_t, ndim=1] atr_values,
    np.ndarray[DTYPE_t, ndim=1] hv_values=None,
    np.ndarray[DTYPE_t, ndim=1] sma_long_values=None,
    np.ndarray[DTYPE_t, ndim=1] adx_values=None,
    np.ndarray[DTYPE_t, ndim=1] trix_histo_values=None,
    double initial_wallet=10000.0,
    str scenario='StochRSI',
    bint use_sma=False,
    bint use_adx=False,
    bint use_trix=False,
    double atr_filter_multiplier=0.0
) -> dict:
    """
    Moteur de backtest ultra-optimisé en Cython.
    
    Paramètres:
    -----------
    close_prices, high_prices, low_prices : np.ndarray
        Prix OHLC (1D arrays)
    ema1_values, ema2_values : np.ndarray
        EMAs pré-calculées
    stoch_rsi_values : np.ndarray
        StochRSI pré-calculé (0-1)
    atr_values : np.ndarray
        ATR pré-calculé
    sma_long_values, adx_values, trix_histo_values : np.ndarray, optional
        Indicateurs optionnels selon scénario
    initial_wallet : double
        Capital initial en USDC
    scenario : str
        'StochRSI', 'StochRSI_SMA', 'StochRSI_ADX', 'StochRSI_TRIX'
    use_sma, use_adx, use_trix : bool
        Flags pour les indicateurs optionnels
    
    Retourne:
    ---------
    dict avec final_wallet, trades, max_drawdown, win_rate
    """
    
    # Déclarations
    cdef Py_ssize_t n = len(close_prices)
    cdef Py_ssize_t i
    cdef double wallet = initial_wallet
    cdef double usd = initial_wallet
    cdef double coin = 0.0
    cdef double current_price, current_close, next_open, exit_price
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
    cdef int is_winning  # Variable helper pour le test de profit
    cdef double atr_baseline = 100.0
    cdef double atr_threshold = 0.0
    cdef double hv_baseline = 0.0
    cdef double hv_threshold = 0.0
    cdef double atr_sum = 0.0
    cdef double optimized_price, candle_low, price_diff_pct
    cdef Py_ssize_t start_idx, end_idx, j
    
    # Validation minimale
    if n == 0:
        return {
            'final_wallet': 0.0,
            'trades': [],
            'max_drawdown': 0.0,
            'win_rate': 0.0
        }
    
    # Calculer ATR baseline/threshold une seule fois (moyenne simple)
    if n > 0:
        for i in range(n):
            atr_sum += atr_values[i]
            if hv_values is not None:
                hv_baseline += hv_values[i]
        atr_baseline = atr_sum / n
        if hv_values is not None:
            hv_baseline = hv_baseline / n
            if atr_filter_multiplier > 0.0:
                hv_threshold = hv_baseline * atr_filter_multiplier

    # Initialiser position
    position.in_position = False
    position.entry_price = 0.0
    position.entry_usd_invested = 0.0
    position.max_price = 0.0
    position.trailing_stop = 0.0
    position.stop_loss = 0.0
    
    # Boucle principale OPTIMISÉE (cdef = pas de vérification Python)
    for i in range(n):
        current_close = close_prices[i]
        current_price = current_close
        if i + 1 < n:
            next_open = open_prices[i+1]
        else:
            next_open = current_close
        
        # === GESTION POSITION ACTIVE ===
        if position.in_position:
            # Mise à jour trailing stop
            if current_price > position.max_price:
                position.max_price = current_price
                position.trailing_stop = position.max_price - (ATR_MULTIPLIER * atr_values[i])
            
            # Calcul drawdown sur PORTEFEUILLE TOTAL
            current_wallet = usd + (coin * current_price)
            
            # Update peak BEFORE calculating drawdown (same as Python)
            if current_wallet > peak_wallet:
                peak_wallet = current_wallet
            
            # Calculate drawdown from peak
            drawdown = (peak_wallet - current_wallet) / peak_wallet if peak_wallet > 0 else 0.0
            max_drawdown = fmax(max_drawdown, drawdown)
            
            # === CONDITIONS DE VENTE ===
            # Stop-loss is fixed at entry (not recalculated)
            stop_loss_hit = current_price < position.stop_loss
            trailing_stop_hit = current_price < position.trailing_stop
            ema_cross_down = ema2_values[i] > ema1_values[i]
            stoch_high = stoch_rsi_values[i] > STOCH_THRESHOLD_SELL
            
            sell_condition = (stop_loss_hit or trailing_stop_hit or 
                            (ema_cross_down and stoch_high))
            
            # Pas de filtre ADX sur les ventes (comme Python)
            
            if sell_condition:
                # VENTE - différencier stop/trailing vs croisement
                if stop_loss_hit:
                    exit_price = position.stop_loss
                    if low_prices[i] < exit_price:
                        exit_price = fmax(low_prices[i], exit_price)
                    if exit_price > current_price:
                        exit_price = current_price
                elif trailing_stop_hit:
                    exit_price = position.trailing_stop
                    if low_prices[i] < exit_price:
                        exit_price = fmax(low_prices[i], exit_price)
                    if exit_price > current_price:
                        exit_price = current_price
                else:
                    exit_price = next_open

                gross_proceeds = coin * exit_price
                fee = gross_proceeds * TAKER_FEE
                usd = gross_proceeds - fee
                coin = 0.0
                
                # CORRECT profit calculation: final USD - initial USD invested
                trade_profit = usd - position.entry_usd_invested
                
                # Incrémenter TOTAL_TRADES en PREMIER (avant le test)
                total_trades += 1
                
                # Test du profit (utiliser variable helper pour éviter bug Cython avec if imbriqué)
                is_winning = 1 if trade_profit > 0.0 else 0
                winning_trades += is_winning
                
                trades.append({
                    'type': 'SELL',
                    'price': exit_price,
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
            # Conditions de base
            ema_cross_up = ema1_values[i] > ema2_values[i]
            stoch_low = stoch_rsi_values[i] < STOCH_THRESHOLD_BUY
            
            buy_condition = ema_cross_up and stoch_low
            
            # Appliquer filtres supplémentaires selon scénario
            if buy_condition and use_sma and sma_long_values is not None:
                buy_condition = current_price > sma_long_values[i]
            
            if buy_condition and use_adx and adx_values is not None:
                buy_condition = adx_values[i] > ADX_THRESHOLD
            
            if buy_condition and use_trix and trix_histo_values is not None:
                buy_condition = trix_histo_values[i] > 0.0

            if buy_condition and hv_values is not None and atr_filter_multiplier > 0.0:
                buy_condition = hv_values[i] <= hv_threshold
            
            if buy_condition:
                # Exécution au prochain open (proxy) : next_open
                optimized_price = next_open
                
                # ACHAT - Calculate entry with fees
                gross_coin = usd / optimized_price
                fee_in_coin = gross_coin * TAKER_FEE
                coin = gross_coin - fee_in_coin
                
                # Track USD invested (before fees)
                position.entry_usd_invested = usd
                usd = 0.0
                
                if coin > 0:
                    position.in_position = True
                    position.entry_price = optimized_price
                    position.max_price = optimized_price
                    position.trailing_stop = optimized_price - (ATR_MULTIPLIER * atr_values[i])
                    position.stop_loss = optimized_price - (ATR_STOP_MULTIPLIER * atr_values[i])
                    
                    # NE PAS incrémenter total_trades ici (uniquement à la vente)
                    trades.append({
                        'type': 'BUY',
                        'price': optimized_price
                    })
    
    # === CALCUL FINAL ===
    cdef double final_wallet
    if position.in_position:
        final_wallet = usd + (coin * close_prices[n-1])
    else:
        final_wallet = usd
    
    # CRITICAL: Cast explicitly to double to avoid integer division!
    # In C, Py_ssize_t / Py_ssize_t = Py_ssize_t (integer), not float!
    cdef double win_rate = 0.0
    if total_trades > 0:
        win_rate = (<double>winning_trades / <double>total_trades) * 100.0
    
    return {
        'final_wallet': final_wallet,
        'trades': trades,
        'max_drawdown': max_drawdown,
        'win_rate': win_rate,
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'atr_baseline': atr_baseline,
        'hv_baseline': hv_baseline
    }


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
def vectorized_ema(np.ndarray[DTYPE_t, ndim=1] prices, int period) -> np.ndarray:
    """Calcul EMA vectorisé en Cython (ultra rapide)."""
    cdef Py_ssize_t n = len(prices)
    cdef np.ndarray[DTYPE_t, ndim=1] ema = np.empty(n, dtype=DTYPE)
    cdef DTYPE_t alpha = 2.0 / (period + 1)
    cdef Py_ssize_t i
    
    ema[0] = prices[0]
    for i in range(1, n):
        ema[i] = alpha * prices[i] + (1.0 - alpha) * ema[i-1]
    
    return ema


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
def vectorized_rsi(np.ndarray[DTYPE_t, ndim=1] prices) -> np.ndarray:
    """Calcul RSI vectorisé en Cython (ultra rapide)."""
    cdef Py_ssize_t n = len(prices)
    cdef np.ndarray[DTYPE_t, ndim=1] rsi = np.full(n, np.nan, dtype=DTYPE)
    cdef DTYPE_t avg_gain = 0.0, avg_loss = 0.0
    cdef Py_ssize_t i
    cdef DTYPE_t diff, gain, loss, rs
    
    # Calcul moyennes initiales
    for i in range(1, 15):
        diff = prices[i] - prices[i-1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain += gain
        avg_loss += loss
    
    avg_gain /= 14.0
    avg_loss /= 14.0
    
    # Lissage exponentiel
    rsi[14] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss if avg_loss > 0 else 0.0)))
    
    for i in range(15, n):
        diff = prices[i] - prices[i-1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        
        avg_gain = (avg_gain * 13.0 + gain) / 14.0
        avg_loss = (avg_loss * 13.0 + loss) / 14.0
        
        rs = avg_gain / avg_loss if avg_loss > 0 else 0.0
        rsi[i] = 100.0 - (100.0 / (1.0 + rs))
    
    return rsi


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
def vectorized_atr(
    np.ndarray[DTYPE_t, ndim=1] high,
    np.ndarray[DTYPE_t, ndim=1] low,
    np.ndarray[DTYPE_t, ndim=1] close,
    int period=14
) -> np.ndarray:
    """Calcul ATR vectorisé en Cython (ultra rapide)."""
    cdef Py_ssize_t n = len(close)
    cdef np.ndarray[DTYPE_t, ndim=1] atr = np.empty(n, dtype=DTYPE)
    cdef np.ndarray[DTYPE_t, ndim=1] tr = np.empty(n, dtype=DTYPE)
    cdef Py_ssize_t i
    cdef DTYPE_t tr_sum, atr_val
    
    # Calcul True Range
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = fmax(
            high[i] - low[i],
            fmax(abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
        )
    
    # Calcul ATR (moyenne lissée)
    tr_sum = 0.0
    for i in range(period):
        tr_sum += tr[i]
    atr[period-1] = tr_sum / period
    
    for i in range(period, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    
    # Remplir les premières valeurs
    for i in range(period-1):
        atr[i] = atr[period-1]
    
    return atr


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
def vectorized_stoch_rsi(np.ndarray[DTYPE_t, ndim=1] rsi, int period=14) -> np.ndarray:
    """Calcul StochRSI vectorisé en Cython (ultra rapide)."""
    cdef Py_ssize_t n = len(rsi)
    cdef np.ndarray[DTYPE_t, ndim=1] stoch_rsi = np.full(n, np.nan, dtype=DTYPE)
    cdef np.ndarray[DTYPE_t, ndim=1] lowest_rsi = np.empty(n, dtype=DTYPE)
    cdef np.ndarray[DTYPE_t, ndim=1] highest_rsi = np.empty(n, dtype=DTYPE)
    cdef Py_ssize_t i, j
    cdef DTYPE_t rsi_min, rsi_max, rsi_range
    
    # Calcul min/max rolling
    for i in range(period-1, n):
        rsi_min = rsi[i]
        rsi_max = rsi[i]
        for j in range(i-period+1, i+1):
            if rsi[j] < rsi_min:
                rsi_min = rsi[j]
            if rsi[j] > rsi_max:
                rsi_max = rsi[j]
        lowest_rsi[i] = rsi_min
        highest_rsi[i] = rsi_max
    
    # Calcul StochRSI
    for i in range(period-1, n):
        rsi_range = highest_rsi[i] - lowest_rsi[i]
        if rsi_range > 0:
            stoch_rsi[i] = (rsi[i] - lowest_rsi[i]) / rsi_range
        else:
            stoch_rsi[i] = 0.5
    
    return stoch_rsi
