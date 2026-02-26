# cython: language_level=3
# distutils: language = c++
# cython: boundscheck=False, wraparound=False, cdivision=True

import numpy as np
cimport numpy as np
cimport cython
import pandas as pd
from libc.math cimport abs

DTYPE = np.float64
ctypedef np.float64_t DTYPE_t

@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
def calculate_indicators(
    df: pd.DataFrame,
    int ema1_period,
    int ema2_period,
    int stoch_period=14,
    int sma_long=0,
    int adx_period=0,
    int trix_length=0,
    int trix_signal=0
) -> pd.DataFrame:
    # Déclarations cdef en premier
    cdef np.ndarray[DTYPE_t, ndim=1] close
    cdef np.ndarray[DTYPE_t, ndim=1] high
    cdef np.ndarray[DTYPE_t, ndim=1] low
    cdef DTYPE_t min_rsi, max_rsi, rsi_range, sma_sum, trix_sum
    cdef Py_ssize_t j
    
    # Validation robuste
    if df is None or df.empty:
        raise ValueError("DataFrame vide ou None")
    
    required_cols = ['close', 'high', 'low']
    for col in required_cols:
        if col not in df.columns:
            raise KeyError(f"Colonne '{col}' manquante")
    
    # Extraction sécurisée des arrays
    try:
        close_series = df['close']
        high_series = df['high'] 
        low_series = df['low']
        
        if close_series.empty or high_series.empty or low_series.empty:
            raise ValueError("Series vides")
            
        close = close_series.astype(np.float64).values
        high = high_series.astype(np.float64).values
        low = low_series.astype(np.float64).values
    except Exception as e:
        raise ValueError(f"Erreur extraction données: {e}")
    cdef Py_ssize_t n = len(close)
    
    if n == 0:
        raise ValueError("Aucune donnée")



    cdef np.ndarray[DTYPE_t, ndim=1] ema1 = np.empty(n, dtype=DTYPE)
    cdef np.ndarray[DTYPE_t, ndim=1] ema2 = np.empty(n, dtype=DTYPE)
    cdef np.ndarray[DTYPE_t, ndim=1] rsi = np.empty(n, dtype=DTYPE)
    cdef np.ndarray[DTYPE_t, ndim=1] stoch_rsi = np.empty(n, dtype=DTYPE)
    cdef np.ndarray[DTYPE_t, ndim=1] atr = np.empty(n, dtype=DTYPE)
    cdef np.ndarray[DTYPE_t, ndim=1] sma = np.empty(n, dtype=DTYPE) if sma_long > 0 else None
    cdef np.ndarray[DTYPE_t, ndim=1] adx = np.empty(n, dtype=DTYPE) if adx_period > 0 else None
    cdef np.ndarray[DTYPE_t, ndim=1] trix_histo = np.empty(n, dtype=DTYPE) if trix_length > 0 and trix_signal > 0 else None

    cdef DTYPE_t alpha1 = 2.0 / (ema1_period + 1)
    cdef DTYPE_t alpha2 = 2.0 / (ema2_period + 1)
    cdef Py_ssize_t i
    cdef DTYPE_t gain, loss, avg_gain, avg_loss, rs
    cdef DTYPE_t high_rsi, low_rsi
    cdef DTYPE_t tr, plus_dm, minus_dm, tr_sum, plus_dm_sum, minus_dm_sum
    cdef DTYPE_t trix_pct, trix_signal_val
    cdef np.ndarray[DTYPE_t, ndim=1] temp_trix = np.empty(n, dtype=DTYPE) if trix_length > 0 else None
    cdef np.ndarray[DTYPE_t, ndim=1] temp_trix_signal = np.empty(n, dtype=DTYPE) if trix_length > 0 else None
    cdef np.ndarray[DTYPE_t, ndim=1] ema2_trix = np.empty(n, dtype=DTYPE) if trix_length > 0 else None
    cdef np.ndarray[DTYPE_t, ndim=1] ema3_trix = np.empty(n, dtype=DTYPE) if trix_length > 0 else None

    ema1[0] = close[0]
    ema2[0] = close[0]
    for i in range(1, n):
        ema1[i] = alpha1 * close[i] + (1 - alpha1) * ema1[i - 1]
        ema2[i] = alpha2 * close[i] + (1 - alpha2) * ema2[i - 1]

    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, n):
        diff = close[i] - close[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        if i <= 14:
            avg_gain += gain
            avg_loss += loss
            if i == 14:
                avg_gain /= 14
                avg_loss /= 14
                rs = avg_gain / avg_loss if avg_loss != 0 else 100.0
                rsi[i] = 100.0 - (100.0 / (1.0 + rs))
        else:
            avg_gain = (avg_gain * 13 + gain) / 14
            avg_loss = (avg_loss * 13 + loss) / 14
            rs = avg_gain / avg_loss if avg_loss != 0 else 100.0
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
        if i < 14:
            rsi[i] = np.nan

    # Stochastic RSI optimisé avec min/max tracking (évite O(n²))
    cdef DTYPE_t[:] rsi_view = rsi
    cdef DTYPE_t[:] stoch_view = stoch_rsi
    
    if stoch_period < n:
        # Initialiser le premier min/max
        min_rsi = rsi[0]
        max_rsi = rsi[0]
        
        # Glissant window avec tracking O(n)
        for i in range(1, stoch_period):
            if rsi[i] < min_rsi:
                min_rsi = rsi[i]
            if rsi[i] > max_rsi:
                max_rsi = rsi[i]
        
        # Première fenêtre complète
        rsi_range = max_rsi - min_rsi
        stoch_rsi[stoch_period - 1] = (rsi[stoch_period - 1] - min_rsi) / rsi_range if rsi_range != 0 else 0.5
        
        # Fenêtres glissantes suivantes (O(n) au lieu de O(n²))
        for i in range(stoch_period, n):
            # Ajouter le nouvel élément
            new_val = rsi[i]
            if new_val < min_rsi:
                min_rsi = new_val
            if new_val > max_rsi:
                max_rsi = new_val
            
            # Recalculer min/max si l'élément supprimé était extrême
            if rsi[i - stoch_period] == min_rsi or rsi[i - stoch_period] == max_rsi:
                min_rsi = rsi[i - stoch_period + 1]
                max_rsi = rsi[i - stoch_period + 1]
                for j in range(i - stoch_period + 2, i + 1):
                    if rsi[j] < min_rsi:
                        min_rsi = rsi[j]
                    if rsi[j] > max_rsi:
                        max_rsi = rsi[j]
            
            rsi_range = max_rsi - min_rsi
            stoch_rsi[i] = (rsi[i] - min_rsi) / rsi_range if rsi_range != 0 else 0.5
        
        # Marquer les premiers éléments comme NaN
        for i in range(stoch_period - 1):
            stoch_rsi[i] = np.nan

    for i in range(n):
        if i == 0:
            atr[i] = 0.0
        else:
            tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
            if i <= 14:
                atr[i] = tr if i == 14 else 0.0
            else:
                atr[i] = (atr[i - 1] * 13 + tr) / 14

    # SMA optimisé avec fenêtre glissante (O(n) au lieu de O(n²))
    if sma_long > 0:
        # Initialiser la somme de la première fenêtre
        sma_sum = 0.0
        for i in range(sma_long):
            sma_sum += close[i]
        
        # Première fenêtre complète
        sma[sma_long - 1] = sma_sum / sma_long
        
        # Fenêtres glissantes suivantes (très rapide)
        for i in range(sma_long, n):
            # Ajouter nouvel élément, retirer ancien
            sma_sum = sma_sum + close[i] - close[i - sma_long]
            sma[i] = sma_sum / sma_long
        
        # Marquer les premiers éléments comme NaN
        for i in range(sma_long - 1):
            sma[i] = np.nan

    if adx_period > 0:
        tr_sum = 0.0
        plus_dm_sum = 0.0
        minus_dm_sum = 0.0
        for i in range(1, n):
            high_low = high[i] - low[i]
            high_close = abs(high[i] - close[i - 1])
            low_close = abs(low[i] - close[i - 1])
            tr = max(high_low, high_close, low_close)
            plus_dm = high[i] - high[i - 1] if high[i] - high[i - 1] > low[i - 1] - low[i] else 0.0
            minus_dm = low[i - 1] - low[i] if low[i - 1] - low[i] > high[i] - high[i - 1] else 0.0
            if i <= adx_period:
                tr_sum += tr
                plus_dm_sum += plus_dm
                minus_dm_sum += minus_dm
                if i == adx_period:
                    tr_sum /= adx_period
                    plus_dm_sum /= adx_period
                    minus_dm_sum /= adx_period
                    plus_di = 100 * plus_dm_sum / tr_sum if tr_sum != 0 else 0.0
                    minus_di = 100 * minus_dm_sum / tr_sum if tr_sum != 0 else 0.0
                    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if plus_di + minus_di != 0 else 0.0
                    adx[i] = dx
                else:
                    adx[i] = np.nan
            else:
                tr_sum = (tr_sum * (adx_period - 1) + tr) / adx_period
                plus_dm_sum = (plus_dm_sum * (adx_period - 1) + plus_dm) / adx_period
                minus_dm_sum = (minus_dm_sum * (adx_period - 1) + minus_dm) / adx_period
                plus_di = 100 * plus_dm_sum / tr_sum if tr_sum != 0 else 0.0
                minus_di = 100 * minus_dm_sum / tr_sum if tr_sum != 0 else 0.0
                dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if plus_di + minus_di != 0 else 0.0
                adx[i] = (adx[i - 1] * (adx_period - 1) + dx) / adx_period

    if trix_length > 0 and trix_signal > 0:
        alpha_trix = 2.0 / (trix_length + 1)
        temp_trix[0] = close[0]
        for i in range(1, n):
            temp_trix[i] = alpha_trix * close[i] + (1 - alpha_trix) * temp_trix[i - 1]
        ema2_trix[0] = temp_trix[0]
        for i in range(1, n):
            ema2_trix[i] = alpha_trix * temp_trix[i] + (1 - alpha_trix) * ema2_trix[i - 1]
        ema3_trix[0] = ema2_trix[0]
        for i in range(1, n):
            ema3_trix[i] = alpha_trix * ema2_trix[i] + (1 - alpha_trix) * ema3_trix[i - 1]
        
        # Calculer TRIX% vectorisé
        for i in range(1, n):
            trix_pct = (ema3_trix[i] - ema3_trix[i - 1]) / ema3_trix[i - 1] * 100 if ema3_trix[i - 1] != 0 else 0.0
            temp_trix_signal[i] = trix_pct
        
        # TRIX signal optimisé avec fenêtre glissante (O(n) au lieu de O(n²))
        # Initialiser la somme de la première fenêtre
        trix_sum = 0.0
        for i in range(trix_signal):
            trix_sum += temp_trix_signal[i]
        
        # Première fenêtre complète
        trix_histo[trix_signal - 1] = temp_trix_signal[trix_signal - 1] - (trix_sum / trix_signal)
        
        # Fenêtres glissantes suivantes
        for i in range(trix_signal, n):
            trix_sum = trix_sum + temp_trix_signal[i] - temp_trix_signal[i - trix_signal]
            trix_signal_val = trix_sum / trix_signal
            trix_histo[i] = temp_trix_signal[i] - trix_signal_val
        
        # Marquer les premiers éléments comme NaN
        for i in range(trix_signal - 1):
            trix_histo[i] = np.nan

    # Création sécurisée du DataFrame résultat avec données disponibles
    try:
        result_data = {
            'high': df['high'].values, 
            'low': df['low'].values,
            'close': df['close'].values,
            'ema1': ema1,
            'ema2': ema2,
            'rsi': rsi,
            'stoch_rsi': stoch_rsi,
            'atr': atr
        }
        
        # Ajouter 'open' seulement si disponible
        if 'open' in df.columns:
            result_data['open'] = df['open'].values
            
        result_df = pd.DataFrame(result_data)
        
        # Restaurer l'index si possible
        if len(result_df) == len(df):
            result_df.index = df.index
    except Exception as e:
        raise ValueError(f"Erreur création DataFrame résultat: {e}")
    # Ajout des indicateurs optionnels
    try:
        if sma_long > 0:
            result_df['sma_long'] = sma
        if adx_period > 0:
            result_df['adx'] = adx
        if trix_length > 0 and trix_signal > 0:
            result_df['TRIX_HISTO'] = trix_histo
    except Exception as e:
        raise ValueError(f"Erreur ajout indicateurs optionnels: {e}")

    result_df.dropna(inplace=True)

    return result_df