# preload_data.py
import sys
import os
# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

import pandas as pd
import ta
from binance.client import Client
import joblib
from datetime import datetime, timedelta

# === CONFIGURATION ===
from dotenv import load_dotenv
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY", "")
SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
if not API_KEY or not SECRET_KEY:
    print("[ERROR] Variables BINANCE_API_KEY / BINANCE_SECRET_KEY non définies dans .env")
    sys.exit(1)
PAIRS = ["BTCUSDT"]
TIMEFRAMES = [
    Client.KLINE_INTERVAL_1HOUR,
    Client.KLINE_INTERVAL_4HOUR,
    Client.KLINE_INTERVAL_1DAY
]
BACKTEST_DAYS = 3650  # 10 ans
OUTPUT_DIR = "precomputed"

client = Client(API_KEY, SECRET_KEY)

def calculate_all_indicators(df):
    """Calcule tous les indicateurs utilisés dans le bot."""
    if df.empty:
        return df

    df_work = df.copy()
    df_work['close'] = df_work['close'].ffill().bfill()

    # EMA
    df_work['ema1'] = df_work['close'].ewm(span=14, adjust=False).mean()
    df_work['ema2'] = df_work['close'].ewm(span=26, adjust=False).mean()

    # RSI
    df_work['rsi'] = ta.momentum.RSIIndicator(df_work['close'], window=14).rsi()

    # Stochastic RSI
    rsi_roll = df_work['rsi'].rolling(window=14)
    df_work['lowest_rsi'] = rsi_roll.min()
    df_work['highest_rsi'] = rsi_roll.max()
    rsi_range = df_work['highest_rsi'] - df_work['lowest_rsi']
    df_work['stoch_rsi'] = (df_work['rsi'] - df_work['lowest_rsi']) / rsi_range.replace(0, 1)

    # SMA Long
    df_work['sma_long'] = df_work['close'].rolling(window=200).mean()

    # ADX
    high_low = df_work['high'] - df_work['low']
    high_close = abs(df_work['high'] - df_work['close'].shift(1))
    low_close = abs(df_work['low'] - df_work['close'].shift(1))
    df_work['tr'] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    tr_smooth = df_work['tr'].rolling(window=14).mean()
    df_work['adx'] = ta.trend.ADXIndicator(
        df_work['high'], df_work['low'], df_work['close'], window=14
    ).adx()

    # TRIX
    ema1 = df_work['close'].ewm(span=7).mean()
    ema2 = ema1.ewm(span=7).mean()
    ema3 = ema2.ewm(span=7).mean()
    df_work['TRIX_PCT'] = ema3.pct_change() * 100
    df_work['TRIX_SIGNAL'] = df_work['TRIX_PCT'].rolling(window=15).mean()
    df_work['TRIX_HISTO'] = df_work['TRIX_PCT'] - df_work['TRIX_SIGNAL']

    # Nettoyage
    cols_to_drop = ['lowest_rsi', 'highest_rsi', 'tr']
    df_work.drop(columns=[c for c in cols_to_drop if c in df_work.columns], errors='ignore', inplace=True)
    df_work.dropna(inplace=True)

    return df_work

def fetch_and_precompute():
    start_date = (datetime.today() - timedelta(days=BACKTEST_DAYS)).strftime("%d %B %Y")
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for pair in PAIRS:
        for tf in TIMEFRAMES:
            print(f" Pré-calcul pour {pair} | {tf}...")
            
            # Téléchargement
            klines = client.get_historical_klines(pair, tf, start_date)
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_av', 'trades', 'tb_base_av', 'tb_quote_av', 'ignore'
            ])
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
            df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)

            # Calcul des indicateurs
            df_with_indicators = calculate_all_indicators(df)

            # Sauvegarde
            filename = f"{OUTPUT_DIR}/{pair}_{tf}_full.pkl"
            joblib.dump(df_with_indicators, filename)
            print(f" Sauvegardé : {filename}")

if __name__ == "__main__":
    fetch_and_precompute()