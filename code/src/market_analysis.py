"""
market_analysis.py — Market change detection.

Extracted from MULTI_SYMBOLS.py (P3-SRP) to isolate market analysis
(EMA crosses, StochRSI extremes, price records) from trading logic.

Pure function — no mutable global state.  Accepts ``prepare_base_dataframe``
as a callable parameter to avoid circular imports.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List

import pandas as pd

logger = logging.getLogger(__name__)


def detect_market_changes(
    pair: str,
    timeframes: List[str],
    start_date: str,
    prepare_base_dataframe_fn: Callable[..., pd.DataFrame],
) -> Dict[str, Any]:
    """
    Détecte intelligemment les changements IMPORTANTS du marché.

    Parameters
    ----------
    pair : str
        Trading pair symbol (e.g. "BTCUSDC").
    timeframes : list[str]
        List of kline intervals to analyze (e.g. ["1h", "4h", "1d"]).
    start_date : str
        Backtest start date string.
    prepare_base_dataframe_fn : callable
        Function(pair, tf, start_date, stoch_period) -> DataFrame.
        Injected to avoid circular imports with MULTI_SYMBOLS.

    Returns
    -------
    dict
        Keys: ema_crosses, stoch_extremes, trix_changes, price_records, execution_time.
    """
    changes: Dict[str, Any] = {
        'ema_crosses': [],
        'stoch_extremes': [],
        'trix_changes': [],
        'price_records': [],
        'execution_time': datetime.now().strftime("%H:%M:%S"),
    }

    try:
        for tf in timeframes:
            try:
                df = prepare_base_dataframe_fn(pair, tf, start_date, 14)
                if df is None or df.empty or len(df) < 50:
                    continue

                # Récupérer les 2 derniers candles
                if len(df) >= 2:
                    prev_candle = df.iloc[-2]
                    curr_candle = df.iloc[-1]

                    # Détection: EMA Cross (bullish/bearish)
                    ema1_prev = prev_candle.get('ema_26', None)
                    ema2_prev = prev_candle.get('ema_50', None)
                    ema1_curr = curr_candle.get('ema_26', None)
                    ema2_curr = curr_candle.get('ema_50', None)

                    if (ema1_prev is not None and ema2_prev is not None and
                            ema1_curr is not None and ema2_curr is not None):

                        if ema1_prev <= ema2_prev and ema1_curr > ema2_curr:
                            changes['ema_crosses'].append({
                                'timeframe': tf,
                                'type': ' BULLISH CROSS',
                                'ema1': ema1_curr,
                                'ema2': ema2_curr,
                                'price': curr_candle['close'],
                            })
                        elif ema1_prev >= ema2_prev and ema1_curr < ema2_curr:
                            changes['ema_crosses'].append({
                                'timeframe': tf,
                                'type': ' BEARISH CROSS',
                                'ema1': ema1_curr,
                                'ema2': ema2_curr,
                                'price': curr_candle['close'],
                            })

                    # Détection: StochRSI extremes
                    stoch_curr = curr_candle.get('stoch_rsi', None)
                    if stoch_curr is not None:
                        if stoch_curr < 0.2:
                            changes['stoch_extremes'].append({
                                'timeframe': tf,
                                'type': ' OVERSOLD',
                                'value': stoch_curr,
                                'price': curr_candle['close'],
                            })
                        elif stoch_curr > 0.8:
                            changes['stoch_extremes'].append({
                                'timeframe': tf,
                                'type': ' OVERBOUGHT',
                                'value': stoch_curr,
                                'price': curr_candle['close'],
                            })

                    # Détection: Prix record
                    high_price = curr_candle['high']
                    low_price = curr_candle['low']

                    if len(df) >= 20:
                        recent_high = df['high'].iloc[-20:].max()
                        recent_low = df['low'].iloc[-20:].min()

                        if high_price >= recent_high:
                            changes['price_records'].append({
                                'timeframe': tf,
                                'type': '🆕 NEW 20-CANDLE HIGH',
                                'value': high_price,
                                'previous_high': recent_high,
                            })

                        if low_price <= recent_low:
                            changes['price_records'].append({
                                'timeframe': tf,
                                'type': '🆕 NEW 20-CANDLE LOW',
                                'value': low_price,
                                'previous_low': recent_low,
                            })

            except Exception as e:
                logger.debug(f"Erreur détection changements {pair} {tf}: {e}")
                continue

    except Exception as e:
        logger.debug(f"Erreur globale détection changements: {e}")

    return changes
