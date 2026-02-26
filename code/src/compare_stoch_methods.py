#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compare three StochRSI calculation methods."""

import sys
import os
# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

from MULTI_SYMBOLS import fetch_historical_data
import pandas as pd
import numpy as np
import ta

# Fetch data
df = fetch_historical_data('BTCUSDC', '1h', '30 November 2025')

# Calculate RSI once
rsi_vals = ta.momentum.RSIIndicator(df['close'], window=14).rsi().values
df['rsi'] = rsi_vals

print("=" * 100)
print("STOCHRSI COMPARISON: RAW vs K-SMOOTHED vs K+D-SMOOTHED")
print("=" * 100)

stoch_period = 14

# METHOD 1: RAW STOCHRSI (NO SMOOTHING)
stoch_raw = np.full_like(rsi_vals, np.nan, dtype=float)
for i in range(stoch_period - 1, len(rsi_vals)):
    window_start = i - stoch_period + 1
    window_end = i + 1
    rsi_window = rsi_vals[window_start:window_end]
    lowest_rsi = np.nanmin(rsi_window)
    highest_rsi = np.nanmax(rsi_window)
    rsi_range = highest_rsi - lowest_rsi
    
    if rsi_range > 0:
        stoch_raw[i] = (rsi_vals[i] - lowest_rsi) / rsi_range
    else:
        stoch_raw[i] = 0.5

# METHOD 2: K-SMOOTHED ONLY (3-period SMA)
stoch_k = pd.Series(stoch_raw).rolling(window=3, min_periods=1).mean().values

# METHOD 3: K+D SMOOTHED (3-period SMA twice)
stoch_d = pd.Series(stoch_k).rolling(window=3, min_periods=1).mean().values

# Show last 10 candles
print("\nLAST 10 CANDLES:")
print(f"{'Index':<8} {'Time':<20} {'RSI':>8} {'RAW':>8} {'K-SMA':>8} {'D-SMA':>8}")
print("-" * 70)

for idx in range(-10, 0):
    ts = df.index[idx]
    time_str = ts.strftime('%Y-%m-%d %H:%M')
    rsi = rsi_vals[idx]
    raw = stoch_raw[idx]
    k = stoch_k[idx]
    d = stoch_d[idx]
    
    print(f"[{idx:3d}]  {time_str}  {rsi:8.2f}  {raw*100:7.2f}%  {k*100:7.2f}%  {d*100:7.2f}%")

print("\n" + "=" * 100)
print(" CURRENT IMPLEMENTATION USES: K-SMA (K column)")
print(" BINANCE SHOWS: 35.12%")
print("   Which matches best above?")
print("=" * 100)
