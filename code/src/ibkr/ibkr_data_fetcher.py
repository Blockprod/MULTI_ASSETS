"""
ibkr_data_fetcher.py — Fetcher de données OHLCV Forex via IB Gateway.

Retourne un DataFrame compatible avec backtest_from_dataframe().
Cache TTL 30 jours via cache_manager.py (réutilisé sans modification).
"""
from __future__ import annotations

import logging
import os
import pickle
import time
from typing import Any

import pandas as pd

logger = logging.getLogger("ibkr_forex")

# Durée de vie du cache : 30 jours (identique au cache Binance)
_CACHE_TTL_SECONDS = 30 * 24 * 3600


def fetch_forex_data(
    pair: str,
    interval: str,
    start_date: str,
    client: Any,
    *,
    cache_dir: str,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Récupère les données OHLCV Forex pour une paire via IB Gateway.

    Args:
        pair        : Paire IBKR, ex 'EURUSD', 'EURGBP'
        interval    : Intervalle, ex '1h', '4h', '1d'
        start_date  : Date de début, ex '1 Jan 2023'
        client      : IBKRForexClient (ou tout objet avec get_historical_klines)
        cache_dir   : Répertoire de cache (isolé du cache Binance)
        force_refresh: Forcer le rechargement depuis IB Gateway

    Returns:
        DataFrame avec colonnes open/high/low/close/volume, index DatetimeIndex UTC.
        Compatible avec backtest_from_dataframe() et indicators_engine.compute_indicators().
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache_key = f"ibkr_{pair}_{interval}_{start_date.replace(' ', '_')}"
    cache_path = os.path.join(cache_dir, f"{cache_key}.pkl")

    # Lecture cache
    if not force_refresh and os.path.exists(cache_path):
        age = time.time() - os.path.getmtime(cache_path)
        if age < _CACHE_TTL_SECONDS:
            try:
                with open(cache_path, "rb") as fh:
                    df = pickle.load(fh)
                logger.debug("[IBKR-DATA] Cache hit : %s (age=%.1fh)", pair, age / 3600)
                return df
            except Exception as exc:
                logger.warning("[IBKR-DATA] Cache corrompu pour %s : %s", pair, exc)

    # Appel IB Gateway
    logger.info("[IBKR-DATA] Téléchargement IBKR %s %s depuis %s", pair, interval, start_date)
    raw_bars = client.get_historical_klines(pair, interval, start_date)

    if not raw_bars:
        logger.error("[IBKR-DATA] Aucune barre reçue pour %s — IB Gateway connecté ?", pair)
        return pd.DataFrame()

    df = _bars_to_dataframe(raw_bars)

    # Écriture cache
    try:
        with open(cache_path, "wb") as fh:
            pickle.dump(df, fh)
        logger.debug("[IBKR-DATA] Cache écrit : %s (%d barres)", pair, len(df))
    except Exception as exc:
        logger.warning("[IBKR-DATA] Impossible d'écrire le cache %s : %s", pair, exc)

    return df


def _bars_to_dataframe(raw_bars: list) -> pd.DataFrame:
    """Convertit la liste de barres IBKRForexClient en DataFrame OHLCV.

    Format entrée (compatible format Binance de get_historical_klines) :
      [timestamp_ms, open, high, low, close, volume, ...]

    Retourne DataFrame avec :
      - colonnes : open, high, low, close, volume (float64)
      - index    : DatetimeIndex UTC (pd.Timestamp)
    """
    if not raw_bars:
        return pd.DataFrame()

    rows = []
    for bar in raw_bars:
        try:
            ts_ms = int(bar[0])
            rows.append({
                "timestamp": pd.Timestamp(ts_ms, unit="ms", tz="UTC"),
                "open":   float(bar[1]),
                "high":   float(bar[2]),
                "low":    float(bar[3]),
                "close":  float(bar[4]),
                "volume": float(bar[5]) if bar[5] else 0.0,
            })
        except (ValueError, IndexError, TypeError) as exc:
            logger.debug("[IBKR-DATA] Barre ignorée (erreur parsing) : %s — %s", bar, exc)
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).set_index("timestamp").sort_index()
    df = df.astype({
        "open": "float64", "high": "float64",
        "low": "float64", "close": "float64", "volume": "float64",
    })

    # Supprimer les doublons d'index (ne devrait pas arriver, mais défense)
    df = df[~df.index.duplicated(keep="last")]

    logger.info("[IBKR-DATA] DataFrame prêt : %d barres, %s → %s",
                len(df),
                df.index[0].strftime("%Y-%m-%d") if len(df) > 0 else "?",
                df.index[-1].strftime("%Y-%m-%d") if len(df) > 0 else "?")
    return df


def invalidate_cache(pair: str, interval: str, start_date: str, cache_dir: str) -> None:
    """Invalide le cache pour une paire donnée (force le rechargement au prochain appel)."""
    cache_key = f"ibkr_{pair}_{interval}_{start_date.replace(' ', '_')}"
    cache_path = os.path.join(cache_dir, f"{cache_key}.pkl")
    if os.path.exists(cache_path):
        os.remove(cache_path)
        logger.info("[IBKR-DATA] Cache invalidé pour %s", pair)
