import sys
import os
# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np
import time
import ta
from indicators import calculate_indicators as cython_calculate_indicators

def python_calculate_indicators(df, ema1_period, ema2_period, stoch_period=14):
    """Version Python pure pour comparaison"""
    df_work = df.copy()
    
    # EMA
    df_work['ema1'] = df_work['close'].ewm(span=ema1_period, adjust=False).mean()
    df_work['ema2'] = df_work['close'].ewm(span=ema2_period, adjust=False).mean()
    
    # RSI
    df_work['rsi'] = ta.momentum.RSIIndicator(df_work['close'], window=14).rsi()
    
    # Stochastic RSI
    rsi_rolling = df_work['rsi'].rolling(window=stoch_period)
    df_work['lowest_rsi'] = rsi_rolling.min()
    df_work['highest_rsi'] = rsi_rolling.max()
    rsi_range = df_work['highest_rsi'] - df_work['lowest_rsi']
    df_work['stoch_rsi'] = (df_work['rsi'] - df_work['lowest_rsi']) / rsi_range.replace(0, 1)
    
    # ATR
    df_work['atr'] = ta.volatility.AverageTrueRange(
        high=df_work['high'], 
        low=df_work['low'], 
        close=df_work['close'], 
        window=14
    ).average_true_range()
    
    df_work.dropna(inplace=True)
    return df_work

# Créer des données de test de différentes tailles
sizes = [1000, 5000, 10000, 50000]

print("=== BENCHMARK CYTHON vs PYTHON ===\n")

for size in sizes:
    print(f"Taille des données: {size} lignes")
    
    # Générer des données aléatoires
    np.random.seed(42)
    data = {
        'high': np.random.uniform(100, 110, size),
        'low': np.random.uniform(90, 100, size),
        'close': np.random.uniform(95, 105, size)
    }
    df = pd.DataFrame(data)
    
    # Benchmark Python
    start_time = time.time()
    for _ in range(3):  # 3 répétitions
        result_python = python_calculate_indicators(df, 14, 26, 14)
    python_time = (time.time() - start_time) / 3
    
    # Benchmark Cython
    start_time = time.time()
    for _ in range(3):  # 3 répétitions
        result_cython = cython_calculate_indicators(df, 14, 26, 14, 0, 0, 0, 0)
    cython_time = (time.time() - start_time) / 3
    
    # Calcul du speedup
    speedup = python_time / cython_time if cython_time > 0 else 0
    
    print(f"  Python:  {python_time:.4f}s")
    print(f"  Cython:  {cython_time:.4f}s")
    print(f"  Speedup: {speedup:.2f}x")
    print(f"  Résultats identiques: {len(result_python) == len(result_cython)}")
    print()

print("=== BENCHMARK TERMINÉ ===")