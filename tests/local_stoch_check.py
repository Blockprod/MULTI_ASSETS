#!/usr/bin/env python3
"""
Standalone check for RSI + StochRSI using the same method as the bot.
Run from workspace root:
  python MULTI_INDICATORS_USDC\local_stoch_check.py
"""
import os
import sys
from binance.client import Client
import pandas as pd
import numpy as np
import ta

PAIR = os.getenv('TEST_PAIR', 'BTCUSDC')
INTERVAL = Client.KLINE_INTERVAL_1HOUR
LIMIT = 1000
STOCH_PERIOD = 14


def fetch_klines(pair, interval, limit=1000):
    client = Client(os.getenv('BINANCE_API_KEY'), os.getenv('BINANCE_SECRET_KEY'))
    kl = client.get_klines(symbol=pair, interval=interval, limit=limit)
    if not kl:
        print('No klines')
        sys.exit(1)
    df = pd.DataFrame(kl, columns=[
        'open_time','open','high','low','close','volume','close_time','quote_av','trades','tb_base_av','tb_quote_av','ignore'
    ])
    df = df[['open_time','open','high','low','close','volume']]
    df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
    df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df


def compute_stochrsi_from_rsi_series(rsi_series: pd.Series, stoch_period: int = 14):
    # vector loop equivalent to the bot: for each i compute rolling min/max over last stoch_period RSI values
    rsi_vals = rsi_series.values
    n = len(rsi_vals)
    stoch = np.full(n, np.nan)
    if n >= stoch_period:
        for i in range(stoch_period - 1, n):
            window = rsi_vals[i - stoch_period + 1: i + 1]
            rsi_low = np.min(window)
            rsi_high = np.max(window)
            rsi_range = rsi_high - rsi_low
            if rsi_range > 0:
                stoch[i] = (rsi_vals[i] - rsi_low) / rsi_range
            else:
                stoch[i] = 0.5
    return pd.Series(stoch, index=rsi_series.index)


def compute_rsi_wilder_series(prices: pd.Series, period: int = 14) -> pd.Series:
    prices = prices.astype('float64').values
    n = len(prices)
    if n <= period:
        return pd.Series([np.nan] * n, index=pd.RangeIndex(n))
    deltas = np.diff(prices)
    seed = deltas[:period]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else np.inf
    rsi = np.empty(n)
    rsi[:] = np.nan
    rsi_val = 100.0 - (100.0 / (1.0 + rs)) if np.isfinite(rs) else 100.0
    rsi[period] = rsi_val
    avg_gain = up
    avg_loss = down
    for i in range(period + 1, n):
        delta = deltas[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else np.inf
        rsi[i] = 100.0 - (100.0 / (1.0 + rs)) if np.isfinite(rs) else 100.0
    return pd.Series(rsi, index=prices.index if hasattr(prices, 'index') else None)


if __name__ == '__main__':
    print(f'Fetching {LIMIT} klines for {PAIR} {INTERVAL}')
    df = fetch_klines(PAIR, INTERVAL, LIMIT)
    print(f'Retrieved {len(df)} candles from {df.index[0]} to {df.index[-1]}')

    df['rsi'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
    # Also compute Wilder RSI for comparison
    try:
        rsi_wilder = compute_rsi_wilder_series(df['close'], period=14)
        df['rsi_wilder'] = rsi_wilder.values
    except Exception:
        df['rsi_wilder'] = df['rsi']

    # compute StochRSI raw/K/D using ta RSI
    df['stoch_rsi_raw'] = compute_stochrsi_from_rsi_series(df['rsi'], stoch_period=STOCH_PERIOD)
    df['stoch_k'] = df['stoch_rsi_raw'].rolling(window=3, min_periods=1).mean()
    df['stoch_d'] = df['stoch_k'].rolling(window=3, min_periods=1).mean()

    # compute StochRSI raw/K/D using Wilder RSI
    df['stoch_raw_wilder'] = compute_stochrsi_from_rsi_series(df['rsi_wilder'], stoch_period=STOCH_PERIOD)
    df['stoch_k_wilder'] = df['stoch_raw_wilder'].rolling(window=3, min_periods=1).mean()
    df['stoch_d_wilder'] = df['stoch_k_wilder'].rolling(window=3, min_periods=1).mean()
    # Also compute EMA-based smoothing (some platforms use EMA instead of SMA for %K/%D)
    df['stoch_k_ema'] = df['stoch_rsi_raw'].ewm(span=3, adjust=False).mean()
    df['stoch_d_ema'] = df['stoch_k_ema'].ewm(span=3, adjust=False).mean()
    df['stoch_k_wilder_ema'] = df['stoch_raw_wilder'].ewm(span=3, adjust=False).mean()
    df['stoch_d_wilder_ema'] = df['stoch_k_wilder_ema'].ewm(span=3, adjust=False).mean()
    # Variant: compute StochRSI using previous period (exclude current) for min/max
    def stoch_exclusive(series: pd.Series, period: int = STOCH_PERIOD):
        vals = series.values
        n = len(vals)
        out = np.full(n, np.nan)
        for i in range(period, n):
            window = vals[i - period:i]  # exclude current
            low = np.min(window)
            high = np.max(window)
            denom = high - low
            out[i] = (series.iloc[i] - low) / denom if denom != 0 else 0.5
        return pd.Series(out, index=series.index)

    df['stoch_raw_excl'] = stoch_exclusive(df['rsi'], STOCH_PERIOD)
    df['stoch_k_excl_sma'] = df['stoch_raw_excl'].rolling(window=3, min_periods=1).mean()
    df['stoch_d_excl_sma'] = df['stoch_k_excl_sma'].rolling(window=3, min_periods=1).mean()
    df['stoch_k_excl_ema'] = df['stoch_raw_excl'].ewm(span=3, adjust=False).mean()
    df['stoch_d_excl_ema'] = df['stoch_k_excl_ema'].ewm(span=3, adjust=False).mean()

    used_idx = len(df) - 2
    used_row = df.iloc[used_idx]
    ts = df.index[used_idx]
    print('\n=== USED CANDLE (closed) ===')
    print('index:', used_idx)
    print('timestamp:', ts)
    print('open:', used_row['open'])
    print('close:', used_row['close'])
    print('\nRSI for used candle: {:.6f}'.format(used_row['rsi']))
    stoch_raw_val = used_row['stoch_rsi_raw']
    stoch_k_val = used_row['stoch_k']
    stoch_d_val = used_row['stoch_d']
    print('StochRSI raw: {:.6f} -> {:.2f}%'.format(stoch_raw_val, stoch_raw_val * 100 if not np.isnan(stoch_raw_val) else float('nan')))
    print('StochRSI %K (SMA3): {:.6f} -> {:.2f}%'.format(stoch_k_val, stoch_k_val * 100 if not np.isnan(stoch_k_val) else float('nan')))
    print('StochRSI %D (SMA3): {:.6f} -> {:.2f}%'.format(stoch_d_val, stoch_d_val * 100 if not np.isnan(stoch_d_val) else float('nan')))

    # print RSI window used
    start_idx = max(0, used_idx - STOCH_PERIOD + 1)
    rsi_window = df['rsi'].iloc[start_idx:used_idx + 1]
    print('\nRSI window values (most recent last):')
    print(rsi_window.tolist())
    print('min:', rsi_window.min(), 'max:', rsi_window.max(), 'denom:', rsi_window.max() - rsi_window.min())

    # Also print last few rows (ta.RSI and Wilder RSI sets)
    print('\nLast 6 rows (close, rsi, stoch_rsi_raw, stoch_k, stoch_d):')
    print(df[['close','rsi','stoch_rsi_raw','stoch_k','stoch_d']].tail(6).to_string())
    print('\nLast 6 rows (close, rsi_wilder, stoch_raw_wilder, stoch_k_wilder, stoch_d_wilder):')
    print(df[['close','rsi_wilder','stoch_raw_wilder','stoch_k_wilder','stoch_d_wilder']].tail(6).to_string())
    print('\nEMA-smoothed %K/%D (ta.RSI):')
    print(df[['stoch_k_ema','stoch_d_ema']].tail(6).to_string())
    print('\nEMA-smoothed %K/%D (Wilder RSI):')
    print(df[['stoch_k_wilder_ema','stoch_d_wilder_ema']].tail(6).to_string())

    print('\nDone.')
    # Brute-force search over common variants to match Binance reported values
    target_k = 10.72 / 100.0
    target_d = 14.98 / 100.0

    def compute_variant(rsi_series, include_current=True, k_method='sma', k_period=3, d_method='sma', d_period=3):
        series = rsi_series
        n = len(series)
        stoch = np.full(n, np.nan)
        period = STOCH_PERIOD
        for i in range(n):
            if include_current:
                if i - period + 1 < 0:
                    continue
                window = series.iloc[i - period + 1:i + 1].values
            else:
                if i - period < 0:
                    continue
                window = series.iloc[i - period:i].values
            low = np.min(window)
            high = np.max(window)
            denom = high - low
            stoch[i] = (series.iloc[i] - low) / denom if denom != 0 else 0.5
        stoch = pd.Series(stoch, index=series.index)
        if k_method == 'sma':
            k = stoch.rolling(window=k_period, min_periods=1).mean()
        else:
            k = stoch.ewm(span=k_period, adjust=False).mean()
        if d_method == 'sma':
            d = k.rolling(window=d_period, min_periods=1).mean()
        else:
            d = k.ewm(span=d_period, adjust=False).mean()
        return k, d

    candidates = []
    rsi_options = {'ta': df['rsi'], 'wilder': df['rsi_wilder']}
    for rsi_name, rsi_series in rsi_options.items():
        for include_current in (True, False):
            for k_method in ('sma', 'ema'):
                for k_period in (1,2,3,4,5):
                    for d_method in ('sma', 'ema'):
                        for d_period in (1,2,3,4,5):
                            k, d = compute_variant(rsi_series, include_current, k_method, k_period, d_method, d_period)
                            val_k = k.iloc[used_idx] if not np.isnan(k.iloc[used_idx]) else np.nan
                            val_d = d.iloc[used_idx] if not np.isnan(d.iloc[used_idx]) else np.nan
                            if np.isnan(val_k) or np.isnan(val_d):
                                continue
                            err = abs(val_k - target_k) + abs(val_d - target_d)
                            candidates.append((err, rsi_name, include_current, k_method, k_period, d_method, d_period, val_k, val_d))

    candidates.sort(key=lambda x: x[0])
    print('\nTop 8 candidate parameterizations (err, rsi, include_current, k_method, k_period, d_method, d_period, val_k, val_d):')
    for row in candidates[:8]:
        err, rsi_name, include_current, k_method, k_period, d_method, d_period, val_k, val_d = row
        print(f"err={err:.6f}, rsi={rsi_name}, include_current={include_current}, k={k_method}{k_period}, d={d_method}{d_period}, %K={val_k*100:.2f}, %D={val_d*100:.2f}")
