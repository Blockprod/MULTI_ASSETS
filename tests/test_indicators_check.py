import os
from binance.client import Client
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import ta

# Charger le fichier .env (s'il existe dans le répertoire de travail)
load_dotenv()

# Autoriser une recherche explicite du fichier .env à la racine du repo si nécessaire
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    load_dotenv(dotenv_path=env_path, override=False)

API_KEY = os.getenv('BINANCE_API_KEY')
API_SECRET = os.getenv('BINANCE_SECRET_KEY')

client = None  # Initialisé uniquement en exécution directe


def _get_client():
    global client
    if client is None:
        if not API_KEY or not API_SECRET:
            print('API keys not trouvées. Placez BINANCE_API_KEY et BINANCE_SECRET_KEY dans votre fichier .env ou variables d\'environnement.')
            raise SystemExit(1)
        client = Client(API_KEY, API_SECRET)
    return client


def fetch_latest(symbol, interval, limit=1000):
    c = _get_client()
    klines = c.get_historical_klines(symbol, interval, limit=limit)
    df = pd.DataFrame(klines, columns=['timestamp','open','high','low','close','volume','close_time','quote_av','trades','tb_base_av','tb_quote_av','ignore'])
    df = df[['timestamp','open','high','low','close','volume']]
    df[['open','high','low','close','volume']] = df[['open','high','low','close','volume']].astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df


def calc_indicators_standard(df):
    out = {}
    close = df['close']
    high = df['high']
    low = df['low']

    # RSI 14
    out['rsi_14'] = ta.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]

    # StochRSI 14 (using RSI series)
    rsi = ta.momentum.RSIIndicator(close, window=14).rsi()
    stoch_period = 14
    if len(rsi) >= stoch_period:
        lowest = rsi.rolling(window=stoch_period).min()
        highest = rsi.rolling(window=stoch_period).max()
        stochrsi = (rsi - lowest) / (highest - lowest)
        out['stochrsi_14'] = stochrsi.iloc[-1]
    else:
        out['stochrsi_14'] = np.nan

    # ATR 14
    out['atr_14'] = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range().iloc[-1]

    # ADX 14
    try:
        out['adx_14'] = ta.trend.ADXIndicator(high=high, low=low, close=close, window=14).adx().iloc[-1]
    except Exception:
        out['adx_14'] = np.nan

    # TRIX (triple EMA) - length 9, signal 9
    trix_len = 9
    ema1 = close.ewm(span=trix_len, adjust=False).mean()
    ema2 = ema1.ewm(span=trix_len, adjust=False).mean()
    ema3 = ema2.ewm(span=trix_len, adjust=False).mean()
    trix_pct = ema3.pct_change() * 100
    trix_signal = trix_pct.rolling(window=9).mean()
    out['trix_histo'] = (trix_pct - trix_signal).iloc[-1]

    # SMA 200 as example
    out['sma_200'] = close.rolling(window=200).mean().iloc[-1] if len(close) >= 200 else np.nan

    # EMAs used by bot: 26 and 50
    out['ema26'] = close.ewm(span=26, adjust=False).mean().iloc[-1]
    out['ema50'] = close.ewm(span=50, adjust=False).mean().iloc[-1]

    return out


def run_for(symbol, interval):
    print('\n' + '='*60)
    print(f'Vérification indicateurs pour {symbol} {interval}')
    print('='*60)
    df = fetch_latest(symbol, interval, limit=1000)
    print(f'Bougies: {len(df)} | Range: {df.index[0]} -> {df.index[-1]}')
    bot_calc = calc_indicators_standard(df)
    for k, v in bot_calc.items():
        print(f'{k:12}: {v}')

if __name__ == "__main__":
    for tf in ['1h','4h','1d']:
        run_for('BTCUSDC', tf)
