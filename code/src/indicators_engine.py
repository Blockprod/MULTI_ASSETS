"""
indicators_engine.py — Technical indicator calculation engine.

Extracted from MULTI_SYMBOLS.py (P3-SRP).  Groups all indicator-related
functions: StochRSI, adaptive EMA selection, full indicator pipeline,
Cython delegation, LRU caching, and base-DataFrame preparation.

All TA computations are vectorised (pandas / numpy).  The Cython compiled
indicator engine (``indicators.pyd``) is imported here and used as first
choice when available (C-14).

Public API
----------
- ``compute_stochrsi``
- ``get_optimal_ema_periods``
- ``calculate_indicators``
- ``universal_calculate_indicators``
- ``prepare_base_dataframe``
- ``CYTHON_INDICATORS_AVAILABLE``, ``indicators_cache``, ``_indicators_cache_lock``
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections import OrderedDict
from typing import Callable, Optional, Tuple

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, MACD
from ta.volatility import AverageTrueRange

from bot_config import config
from cache_manager import get_cache_key

logger = logging.getLogger(__name__)


# ─── Cython Indicators Import (C-14) ────────────────────────────────────────
# Moved from MULTI_SYMBOLS.py: single authoritative Cython indicator engine.
# Falls back to Python implementation if compiled .pyd not available.

_BIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin'))
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

import types as _ind_types
_cython_indicators: Optional[_ind_types.ModuleType] = None
try:
    import indicators as _cython_indicators  # noqa: F811
    CYTHON_INDICATORS_AVAILABLE: bool = True
    logger.info("Cython indicators engine loaded (C-14) [indicators_engine].")
except ImportError as _ind_import_err:
    CYTHON_INDICATORS_AVAILABLE = False
    logger.warning(
        "Cython indicators not available (%s) — using Python fallback.",
        _ind_import_err,
    )
    # _cython_indicators stays None


# ─── LRU Indicator Cache ────────────────────────────────────────────────────

_INDICATORS_CACHE_MAX: int = 30
indicators_cache: OrderedDict[str, pd.DataFrame] = OrderedDict()
_indicators_cache_lock = threading.Lock()


# ─── StochRSI ────────────────────────────────────────────────────────────────

def compute_stochrsi(rsi_series: pd.Series, period: int = 14) -> pd.Series:
    """Calcul vectorisé robuste du StochRSI.

    Aligné avec ``indicators.pyx`` (Cython canonical, P3-DUP) :

    - zero-range (marché plat) → 0.5 (signal neutre)
    - pré-période → NaN (pas de signal pendant le warm-up)
    - valeurs clippées dans [0, 1]

    Parameters
    ----------
    rsi_series : pd.Series
        Série RSI source.
    period : int
        Fenêtre glissante pour min/max (défaut 14).

    Returns
    -------
    pd.Series
        Série StochRSI, même index que *rsi_series*.
    """
    rsi_np = rsi_series.to_numpy()
    min_rsi = pd.Series(rsi_np).rolling(window=period, min_periods=period).min().to_numpy()
    max_rsi = pd.Series(rsi_np).rolling(window=period, min_periods=period).max().to_numpy()
    denom = max_rsi - min_rsi
    with np.errstate(divide='ignore', invalid='ignore'):
        stochrsi = np.where(denom != 0, (rsi_np - min_rsi) / denom, 0.5)
    stochrsi = np.clip(stochrsi, 0, 1)
    # P3-DUP: NaN pre-period reste NaN (rolling min_periods=period produit NaN)
    return pd.Series(stochrsi, index=rsi_series.index)


# ─── Adaptive EMA Selection ─────────────────────────────────────────────────

def get_optimal_ema_periods(
    df: pd.DataFrame,
    timeframe: str = '4h',
    symbol: str = 'TRXUSDC',
) -> Tuple[int, int]:
    """Sélectionne les meilleures périodes EMA selon le timeframe et la
    volatilité courante (ATR/Close).

    **Important (C-12, anti-look-ahead-bias)** : ce calcul DOIT porter
    uniquement sur la fenêtre In-Sample (IS).  L'appelant est responsable
    de passer un slice IS ::

        is_df = df.iloc[:int(len(df) * 0.70)]
        ema1, ema2 = get_optimal_ema_periods(is_df, timeframe=tf)

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame (slice IS recommandé).
    timeframe : str
        Intervalle kline (``"1h"``, ``"4h"``, ``"1d"`` …).
    symbol : str
        Paire de trading (pour le logging).

    Returns
    -------
    tuple[int, int]
        ``(ema_fast, ema_slow)`` ajustés dynamiquement.
    """
    try:
        # Calculer la volatilite (ATR/Close)
        atr = AverageTrueRange(
            high=df.get('high', df['close']),
            low=df.get('low', df['close']),
            close=df['close'],
            window=14,
        ).average_true_range()
        volatility = (atr / df['close']).mean()

        timeframe_map = {
            '1m': (5, 13),
            '5m': (7, 17),
            '15m': (9, 21),
            '30m': (12, 26),
            '1h': (14, 26),
            '4h': (26, 50),
            '1d': (50, 200),
        }
        base_ema1, base_ema2 = timeframe_map.get(timeframe, (26, 50))

        # Ajustement dynamique base sur la volatilite
        if volatility > 0.015:  # Haute volatilite
            ema1 = max(5, int(base_ema1 * 0.88))
            ema2 = max(10, int(base_ema2 * 0.88))
        elif volatility < 0.005:  # Basse volatilite
            ema1 = int(base_ema1 * 1.12)
            ema2 = int(base_ema2 * 1.12)
        else:
            ema1, ema2 = base_ema1, base_ema2

        logger.info(
            f"EMA adaptatif pour {symbol} {timeframe}: "
            f"{ema1}/{ema2} (volatilite: {volatility:.4f})"
        )
        return ema1, ema2

    except Exception as e:
        logger.warning(
            f"Erreur optimisation EMA adaptative, utilisant defaut (26, 50): {e}"
        )
        return 26, 50


# ─── Full Indicator Pipeline ────────────────────────────────────────────────

def calculate_indicators(
    df: pd.DataFrame,
    ema1_period: int,
    ema2_period: int,
    stoch_period: int = 14,
    sma_long: Optional[int] = None,
    adx_period: Optional[int] = None,
    trix_length: Optional[int] = None,
    trix_signal: Optional[int] = None,
    *,
    on_error: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """Calcule les indicateurs techniques avec cache LRU et optimisation.

    Pipeline : Cython (C-14) si disponible → sinon Python (``ta`` + pandas).
    Les résultats sont mis en cache thread-safe (``_indicators_cache_lock``).

    Parameters
    ----------
    df : pd.DataFrame
        OHLCV DataFrame source.
    ema1_period, ema2_period : int
        Périodes EMA rapide / lente.
    stoch_period : int
        Période du StochRSI.
    sma_long : int, optional
        Période SMA longue (scénario StochRSI_SMA).
    adx_period : int, optional
        Période ADX (scénario StochRSI_ADX).
    trix_length, trix_signal : int, optional
        Paramètres TRIX (scénario StochRSI_TRIX).
    on_error : callable, optional
        ``on_error(error_message)`` appelé en cas d'exception.

    Returns
    -------
    pd.DataFrame
        DataFrame enrichi avec ``ema1``, ``ema2``, ``rsi``, ``stoch_rsi``,
        ``atr``, ``macd_histogram`` et indicateurs optionnels.  Vide en cas
        d'échec.
    """
    try:
        if df.empty or 'close' not in df.columns:
            raise KeyError("DataFrame vide ou colonne 'close' absente")

        # Preparer les parametres pour le cache
        params = {
            'ema1': ema1_period,
            'ema2': ema2_period,
            'stoch': stoch_period,
            'sma': sma_long,
            'adx': adx_period,
            'trix_len': trix_length,
            'trix_sig': trix_signal,
        }
        cache_key = get_cache_key(
            "indicators",
            f"{df['close'].iloc[-1]}_{len(df)}_{df.index[-1]}",
            params,
        )

        # Lecture thread-safe du cache (LRU : move_to_end on hit)
        try:
            with _indicators_cache_lock:
                if cache_key in indicators_cache:
                    cached_df = indicators_cache[cache_key]
                    if len(cached_df) == len(df.dropna(subset=['close'])):
                        indicators_cache.move_to_end(cache_key)
                        logger.debug("Indicateurs charges depuis le cache memoire")
                        return cached_df.copy()
        except (KeyError, AttributeError, TypeError):
            pass

        # C-14: Deleguer au moteur Cython centralise quand disponible.
        if CYTHON_INDICATORS_AVAILABLE and _cython_indicators is not None:
            try:
                df_cython = _cython_indicators.calculate_indicators(
                    df.copy(),
                    ema1_period,
                    ema2_period,
                    stoch_period,
                    sma_long or 0,
                    adx_period or 0,
                    trix_length or 0,
                    trix_signal or 0,
                )
                if df_cython is not None and not df_cython.empty:
                    try:
                        with _indicators_cache_lock:
                            indicators_cache[cache_key] = df_cython.copy()
                            indicators_cache.move_to_end(cache_key)
                            while len(indicators_cache) > _INDICATORS_CACHE_MAX:
                                indicators_cache.popitem(last=False)
                    except Exception as _exc:
                        logger.debug("[indicators_engine] mise à jour cache Cython échouée: %s", _exc)
                    logger.debug("Indicateurs calcules via Cython (C-14)")
                    return df_cython
            except Exception as _cython_ind_err:
                logger.warning(
                    "Cython indicators failed (%s) — fallback Python.",
                    _cython_ind_err,
                )

        # Copie de travail (Python fallback)
        df_work = df.copy()

        # Nettoyage minimal des NaN sur 'close'
        df_work['close'] = df_work['close'].ffill().bfill()
        if df_work['close'].isna().any():
            logger.warning("Donnees 'close' entierement NaN apres nettoyage")
            return pd.DataFrame()

        # --- RSI (seulement si absent) ---
        if 'rsi' not in df_work.columns:
            df_work['rsi'] = RSIIndicator(df_work['close'], window=14).rsi()

        # --- MACD (OPTIMISATION #7: Filtre Momentum MACD) ---
        try:
            macd_indicator = MACD(
                df_work['close'], window_fast=12, window_slow=26, window_sign=9,
            )
            df_work['macd'] = macd_indicator.macd()
            df_work['macd_signal'] = macd_indicator.macd_signal()
            df_work['macd_histogram'] = macd_indicator.macd_diff()
        except Exception as e:
            logger.warning(f"Erreur calcul MACD: {e}, skipping MACD filter")
            df_work['macd_histogram'] = np.nan

        # --- EMA (adjust=False = methode recursive/online Binance) ---
        df_work['ema1'] = df_work['close'].ewm(span=ema1_period, adjust=False).mean()
        df_work['ema2'] = df_work['close'].ewm(span=ema2_period, adjust=False).mean()

        # --- Stochastic RSI ---
        if 'rsi' in df_work.columns:
            df_work['stoch_rsi'] = compute_stochrsi(df_work['rsi'], period=stoch_period)

        # --- ATR ---
        df_work['atr'] = AverageTrueRange(
            high=df_work.get('high', df_work['close']),
            low=df_work.get('low', df_work['close']),
            close=df_work['close'],
            window=config.atr_period,
        ).average_true_range()

        # --- SMA long ---
        if sma_long:
            df_work['sma_long'] = df_work['close'].rolling(window=sma_long).mean()

        # --- ADX ---
        if adx_period and len(df_work) >= adx_period + 2:
            try:
                df_work['adx'] = ADXIndicator(
                    high=df_work['high'],
                    low=df_work['low'],
                    close=df_work['close'],
                    window=adx_period,
                ).adx()
            except Exception:
                df_work['adx'] = np.nan

        # --- TRIX ---
        if trix_length and trix_signal:
            trix_ema1 = df_work['close'].ewm(span=trix_length, adjust=False).mean()
            trix_ema2 = trix_ema1.ewm(span=trix_length, adjust=False).mean()
            trix_ema3 = trix_ema2.ewm(span=trix_length, adjust=False).mean()
            df_work['TRIX_PCT'] = trix_ema3.pct_change() * 100
            df_work['TRIX_SIGNAL'] = df_work['TRIX_PCT'].rolling(window=trix_signal).mean()
            df_work['TRIX_HISTO'] = df_work['TRIX_PCT'] - df_work['TRIX_SIGNAL']

        # --- Nettoyage final ---
        df_work.dropna(subset=['close', 'rsi', 'atr'], inplace=True)

        # --- Mise en cache LRU ---
        try:
            with _indicators_cache_lock:
                indicators_cache[cache_key] = df_work.copy()
                indicators_cache.move_to_end(cache_key)
                while len(indicators_cache) > _INDICATORS_CACHE_MAX:
                    indicators_cache.popitem(last=False)
            logger.debug(f"Indicateurs mis en cache: {cache_key[:30]}...")
        except (MemoryError, KeyError) as e:
            logger.debug(f"Erreur mise en cache: {e}")

        logger.debug(f"Indicateurs calcules: {len(df_work)} lignes")
        return df_work

    except Exception as e:
        logger.error(f"Erreur calcul indicateurs: {e}", exc_info=True)
        if on_error:
            try:
                on_error(str(e))
            except Exception as _exc:
                logger.debug("[indicators_engine] on_error callback a échoué: %s", _exc)
        return pd.DataFrame()


def universal_calculate_indicators(
    df: pd.DataFrame,
    ema1_period: int,
    ema2_period: int,
    stoch_period: int = 14,
    sma_long: Optional[int] = None,
    adx_period: Optional[int] = None,
    trix_length: Optional[int] = None,
    trix_signal: Optional[int] = None,
    *,
    on_error: Optional[Callable[[str], None]] = None,
) -> pd.DataFrame:
    """Point d'entrée unique pour le calcul d'indicateurs (live + backtest).

    Délègue à ``calculate_indicators()`` après vérification de taille
    minimale (10 lignes).

    Returns
    -------
    pd.DataFrame
        Voir ``calculate_indicators``.
    """
    if df.empty or len(df) < 10:
        return pd.DataFrame()
    return calculate_indicators(
        df,
        ema1_period,
        ema2_period,
        stoch_period=stoch_period,
        sma_long=sma_long,
        adx_period=adx_period,
        trix_length=trix_length,
        trix_signal=trix_signal,
        on_error=on_error,
    )


# ─── Base DataFrame Preparation ─────────────────────────────────────────────

def prepare_base_dataframe(
    pair: str,
    timeframe: str,
    start_date: str,
    stoch_period: int = 14,
    *,
    fetch_data_fn: Optional[Callable[..., pd.DataFrame]] = None,
) -> Optional[pd.DataFrame]:
    """Prépare un DataFrame avec tous les indicateurs de base partagés par
    tous les scénarios de backtest.

    Calcule les EMA pré-définies (14, 25, 26, 45, 50), RSI, ATR et
    StochRSI.  Le résultat est directement consommable par
    ``run_single_backtest_optimized``.

    Parameters
    ----------
    pair : str
        Paire de trading.
    timeframe : str
        Intervalle kline.
    start_date : str
        Date de début.
    stoch_period : int
        Période du StochRSI.
    fetch_data_fn : callable
        ``fetch_data_fn(pair, timeframe, start_date) -> pd.DataFrame``.
        Injecté pour éviter les imports circulaires.

    Returns
    -------
    pd.DataFrame or None
        DataFrame enrichi, ou ``None`` si les données sont vides.

    Raises
    ------
    ValueError
        Si ``fetch_data_fn`` n'est pas fourni.
    """
    if fetch_data_fn is None:
        raise ValueError("fetch_data_fn must be provided")

    df = fetch_data_fn(pair, timeframe, start_date)
    if df.empty:
        return None

    # Calculer TOUS les EMA possibles (adjust=False = methode recursive/online)
    for period in [14, 25, 26, 45, 50]:
        df[f'ema_{period}'] = df['close'].ewm(span=period, adjust=False).mean()

    # Indicateurs communs a tous les scenarios
    df['rsi'] = RSIIndicator(df['close'], window=14).rsi()

    df['atr'] = AverageTrueRange(
        high=df['high'],
        low=df['low'],
        close=df['close'],
        window=config.atr_period,
    ).average_true_range()

    # Stochastic RSI — P3-DUP: compute_stochrsi aligne Cython
    df['stoch_rsi'] = compute_stochrsi(df['rsi'], period=stoch_period)

    # A-1: Volume SMA pour filtre volume relatif
    if 'volume' in df.columns:
        df['vol_sma'] = df['volume'].rolling(window=getattr(config, 'volume_sma_period', 20)).mean()

    # Supprimer NaN uniquement des colonnes essentielles
    df.dropna(subset=['close', 'rsi', 'atr'], inplace=True)
    return df
