"""
backtest_runner.py — Backtest execution engine.

Extracted from MULTI_SYMBOLS.py (P3-SRP).  Groups all backtest-related
functions: single backtest execution, parallel orchestration, Cython
delegation, and result formatting.

The Cython compiled backtest engine (``backtest_engine_standard.pyd``) is
imported here and used as first choice when available (baseline sizing).

Public API
----------
- ``backtest_from_dataframe``
- ``empty_result_dict``
- ``run_single_backtest_optimized``
- ``run_all_backtests``
- ``run_parallel_backtests``
- ``CYTHON_BACKTEST_AVAILABLE``, ``backtest_engine``
"""

from __future__ import annotations

import logging
import math
import os
import random
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, cast, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
from rich.console import Console
from ta.trend import ADXIndicator
from tqdm import tqdm

from bot_config import config
from indicators_engine import get_optimal_ema_periods
from position_sizing import (
    compute_position_size_by_risk,
    compute_position_size_fixed_notional,
    compute_position_size_volatility_parity,
)
from exceptions import SizingError                     # P0-05

logger = logging.getLogger(__name__)
console = Console()


# --- P2-02: Stochastic Slippage Model ----------------------------------------

class BasicSlippageModel:
    """Modèle de slippage stochastique pour la validation OOS (P2-02).

    Ajoute un surcoût aléatoire de 1-3 bps + un impact volume sur chaque
    transaction.  N'est activé qu'en mode OOS (pas en grid search pour ne
    pas ralentir l'optimisation).

    Parameters
    ----------
    min_bps : float
        Spread minimum en points de base (défaut 1 bps = 0.0001).
    max_bps : float
        Spread maximum en points de base (défaut 3 bps = 0.0003).
    volume_impact_factor : float
        Amplificateur d'impact volume : impact += factor × (1 - vol_rank)
        où vol_rank ∈ [0,1] (0 = faible volume, 1 = fort volume).
        Défaut : 0.0002 (2 bps max supplémentaires pour très faible volume).
    seed : int, optional
        Graine pour la reproductibilité des tests.
    """

    def __init__(
        self,
        min_bps: float = 0.0001,
        max_bps: float = 0.0003,
        volume_impact_factor: float = 0.0002,
        seed: Optional[int] = None,
    ) -> None:
        self.min_bps = min_bps
        self.max_bps = max_bps
        self.volume_impact_factor = volume_impact_factor
        self._rng = random.Random(seed)

    def buy_factor(self, volume_rank: float = 0.5) -> float:
        """Retourne le facteur multiplicateur pour un achat (> 1.0)."""
        spread = self._rng.uniform(self.min_bps, self.max_bps)
        vol_impact = self.volume_impact_factor * max(0.0, 1.0 - volume_rank)
        return 1.0 + spread + vol_impact

    def sell_factor(self, volume_rank: float = 0.5) -> float:
        """Retourne le facteur multiplicateur pour une vente (< 1.0)."""
        spread = self._rng.uniform(self.min_bps, self.max_bps)
        vol_impact = self.volume_impact_factor * max(0.0, 1.0 - volume_rank)
        return 1.0 - spread - vol_impact


# --- A-2: Multi-Timeframe Helper ---------------------------------------------

def _compute_mtf_bullish(df_1h: pd.DataFrame, ema_fast: int, ema_slow: int) -> np.ndarray:
    """Compute 4h multi-timeframe bullish trend array aligned to 1h index.

    No look-ahead bias: shift(1) on 4h level ensures only completed 4h
    candles are used for the EMA comparison.

    Parameters
    ----------
    df_1h : pd.DataFrame
        1h OHLCV DataFrame with a DatetimeIndex.
    ema_fast, ema_slow : int
        EMA periods to compute on 4h close.

    Returns
    -------
    np.ndarray[float64]
        1.0 when 4h EMA_fast > EMA_slow (bullish), 0.0 otherwise.
        Same length as df_1h.
    """
    # Resample 1h close to 4h (closed='left', label='left' = default)
    df_4h_close = df_1h['close'].resample('4h').last().dropna()

    # Compute EMAs on 4h
    ema_f = df_4h_close.ewm(span=ema_fast, adjust=False).mean()
    ema_s = df_4h_close.ewm(span=ema_slow, adjust=False).mean()

    # Bullish = fast > slow; shift(1) → only completed 4h candles
    bullish_4h = (ema_f > ema_s).astype(float).shift(1).fillna(0.0)

    # Reindex to 1h with forward fill
    bullish_1h = bullish_4h.reindex(df_1h.index, method='ffill').fillna(0.0)

    result_arr: np.ndarray = np.asarray(bullish_1h.to_numpy(dtype=np.float64), dtype=np.float64)
    return result_arr


# --- Cython Backtest Engine Import -------------------------------------------

_BIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin'))
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

import types as _bt_types
backtest_engine: Optional[_bt_types.ModuleType] = None
try:
    import backtest_engine_standard as backtest_engine  # noqa: F811
    CYTHON_BACKTEST_AVAILABLE: bool = True
    logger.info("Cython backtest engine loaded [backtest_runner].")
except ImportError as e:
    CYTHON_BACKTEST_AVAILABLE = False
    logger.warning(
        "Cython backtest_engine_standard not available (%s), using Python fallback", e
    )
    # backtest_engine stays None

# --- Core Backtest -----------------------------------------------------------

def backtest_from_dataframe(
    df: pd.DataFrame,
    ema1_period: int,
    ema2_period: int,
    sma_long: Optional[int] = None,
    adx_period: Optional[int] = None,
    trix_length: Optional[int] = None,
    trix_signal: Optional[int] = None,
    sizing_mode: str = 'risk',  # P1-07
    partial_enabled: bool = True,  # P2-01: toggle simulation des partiels
    slippage_model: Optional[BasicSlippageModel] = None,  # P2-02: slippage stochastique OOS
    periods_per_year: int = 8766,
    **_kwargs: Any,
) -> Dict[str, Any]:
    """Exécute un backtest à partir d'un DataFrame préparé.

    Utilise le moteur Cython accéléré (30–50×) quand disponible et que
    ``sizing_mode == 'baseline'``.  Pour les modes avancés (``'risk'``,
    ``'fixed_notional'``, ``'volatility_parity'``), utilise la boucle
    Python qui gère explicitement le position sizing.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame OHLCV + indicateurs (doit contenir ``close``, ``atr``,
        ``stoch_rsi`` et les colonnes ``ema_{period}``).
    ema1_period, ema2_period : int
        Périodes EMA rapide / lente.
    sma_long, adx_period : int, optional
        Paramètres scénarios SMA / ADX.
    trix_length, trix_signal : int, optional
        Paramètres scénario TRIX.
    sizing_mode : str
        Mode de position sizing (``'baseline'``, ``'risk'``,
        ``'fixed_notional'``, ``'volatility_parity'``).
    partial_enabled : bool
        Active la simulation des prises de profit partielles (P2-01).
    periods_per_year : int
        Nombre de périodes par an (pour les métriques risk-adjusted).

    Returns
    -------
    dict
        ``{'final_wallet', 'trades', 'max_drawdown', 'win_rate',
        'sharpe_ratio', 'sortino_ratio', 'calmar_ratio', ...}``
    """
    try:
        if df.empty or len(df) < 50:
            return {
                'final_wallet': 0.0,
                'trades': pd.DataFrame(),
                'max_drawdown': 0.0,
                'win_rate': 0.0,
            }

        # Securiser la presence des colonnes EMA dynamiques
        if f'ema_{ema1_period}' not in df.columns:
            df[f'ema_{ema1_period}'] = df['close'].ewm(
                span=ema1_period, adjust=False
            ).mean()
        if f'ema_{ema2_period}' not in df.columns:
            df[f'ema_{ema2_period}'] = df['close'].ewm(
                span=ema2_period, adjust=False
            ).mean()

        # === CYTHON PATH ===
        # P4-CYTHON: Cython engine supports baseline + risk sizing and partials
        if (
            CYTHON_BACKTEST_AVAILABLE
            and backtest_engine is not None
            and sizing_mode in ('baseline', 'risk')
        ):
            try:
                df_work = df.copy()
                df_work['ema1'] = df_work[f'ema_{ema1_period}']
                df_work['ema2'] = df_work[f'ema_{ema2_period}']
                if sma_long:
                    df_work['sma_long'] = df_work['close'].rolling(
                        window=sma_long
                    ).mean()
                if adx_period:
                    if 'adx' in df.columns:
                        df_work['adx'] = df['adx'].values
                    else:
                        df_work['adx'] = ADXIndicator(
                            high=df_work['high'],
                            low=df_work['low'],
                            close=df_work['close'],
                            window=adx_period,
                        ).adx()
                if trix_length and trix_signal:
                    trix_ema1 = df_work['close'].ewm(
                        span=trix_length, adjust=False
                    ).mean()
                    trix_ema2 = trix_ema1.ewm(
                        span=trix_length, adjust=False
                    ).mean()
                    trix_ema3 = trix_ema2.ewm(
                        span=trix_length, adjust=False
                    ).mean()
                    df_work['TRIX_PCT'] = trix_ema3.pct_change() * 100
                    df_work['TRIX_SIGNAL'] = df_work['TRIX_PCT'].rolling(
                        window=trix_signal
                    ).mean()
                    df_work['TRIX_HISTO'] = (
                        df_work['TRIX_PCT'] - df_work['TRIX_SIGNAL']
                    )

                # A-1: Volume filter — SMA du volume
                _use_vol = getattr(config, 'volume_filter_enabled', False)
                if _use_vol and 'volume' in df_work.columns:
                    _vol_period = int(getattr(config, 'volume_sma_period', 20))
                    df_work['vol_sma'] = df_work['volume'].rolling(window=_vol_period).mean()

                # A-2: Multi-timeframe filter — compute 4h EMA trend
                _use_mtf = getattr(config, 'mtf_filter_enabled', False)
                _mtf_bullish = None
                if _use_mtf and hasattr(df_work.index, 'freq') or isinstance(df_work.index, pd.DatetimeIndex):
                    try:
                        _mtf_fast = getattr(config, 'mtf_ema_fast', 18)
                        _mtf_slow = getattr(config, 'mtf_ema_slow', 58)
                        _mtf_bullish = _compute_mtf_bullish(df_work, _mtf_fast, _mtf_slow)
                    except Exception as _mtf_err:
                        logger.warning("A-2 MTF computation failed: %s — filter disabled", _mtf_err)
                        _use_mtf = False

                result = backtest_engine.backtest_from_dataframe_fast(
                    df_work['close'].to_numpy(dtype=np.float64),
                    df_work['high'].to_numpy(dtype=np.float64),
                    df_work['low'].to_numpy(dtype=np.float64),
                    df_work['ema1'].to_numpy(dtype=np.float64),
                    df_work['ema2'].to_numpy(dtype=np.float64),
                    df_work['stoch_rsi'].to_numpy(dtype=np.float64),
                    df_work['atr'].to_numpy(dtype=np.float64),
                    (
                        df_work['sma_long'].to_numpy(dtype=np.float64)
                        if sma_long and 'sma_long' in df_work.columns
                        else None
                    ),
                    (
                        df_work['adx'].to_numpy(dtype=np.float64)
                        if adx_period and 'adx' in df_work.columns
                        else None
                    ),
                    (
                        df_work['TRIX_HISTO'].to_numpy(dtype=np.float64)
                        if trix_length and 'TRIX_HISTO' in df_work.columns
                        else None
                    ),
                    (
                        df_work['open'].to_numpy(dtype=np.float64)
                        if 'open' in df_work.columns
                        else None
                    ),
                    # A-1: volume arrays
                    (
                        df_work['volume'].to_numpy(dtype=np.float64)
                        if _use_vol and 'volume' in df_work.columns
                        else None
                    ),
                    (
                        df_work['vol_sma'].to_numpy(dtype=np.float64)
                        if _use_vol and 'vol_sma' in df_work.columns
                        else None
                    ),
                    config.initial_wallet,
                    'StochRSI',
                    sma_long is not None,
                    adx_period is not None,
                    trix_length is not None,
                    _use_vol and 'vol_sma' in df_work.columns,  # A-1: use_vol_filter
                    config.backtest_taker_fee,
                    config.slippage_buy,
                    config.slippage_sell,
                    atr_multiplier=config.atr_multiplier,
                    atr_stop_multiplier=config.atr_stop_multiplier,
                    stoch_threshold_buy=config.stoch_rsi_buy_max,
                    stoch_threshold_sell=config.stoch_rsi_sell_exit,
                    adx_threshold=config.adx_threshold,
                    sizing_mode=sizing_mode,
                    risk_per_trade=config.risk_per_trade,
                    partial_enabled=partial_enabled,
                    partial_threshold_1=config.partial_threshold_1,
                    partial_threshold_2=config.partial_threshold_2,
                    partial_pct_1=config.partial_pct_1,
                    partial_pct_2=config.partial_pct_2,
                    min_notional=getattr(config, 'backtest_min_notional', 5.0),
                    stoch_threshold_buy_min=config.stoch_rsi_buy_min,
                    breakeven_enabled=getattr(config, 'breakeven_enabled', True),
                    breakeven_trigger_pct=getattr(config, 'breakeven_trigger_pct', 0.015),
                    cooldown_candles=getattr(config, 'stop_loss_cooldown_candles', 0),
                    mtf_bullish=_mtf_bullish if _use_mtf and _mtf_bullish is not None else None,
                    use_mtf_filter=_use_mtf and _mtf_bullish is not None,
                )
                _cython_result = {
                    'final_wallet': result['final_wallet'],
                    'trades': (
                        pd.DataFrame(result['trades'])
                        if result['trades']
                        else pd.DataFrame()
                    ),
                    'max_drawdown': result['max_drawdown'],
                    'win_rate': result['win_rate'],
                }
                # Calculer sharpe_ratio + métriques risk-adjusted à partir des profits
                # de chaque trade SELL. Sans cela, r.get('sharpe_ratio', 0.0) = 0.0
                # et les OOS gates (sharpe > 0.3) bloquent TOUS les achats en permanence.
                try:
                    from walk_forward import compute_risk_metrics as _crm_cy
                    _sell_profits = [
                        t.get('profit', 0.0)
                        for t in (result['trades'] or [])
                        if t.get('type') == 'SELL'
                    ]
                    if _sell_profits:
                        _running = config.initial_wallet
                        _eq_pts = [_running]
                        for _p in _sell_profits:
                            _running += _p
                            _eq_pts.append(_running)
                        _rm = _crm_cy(
                            np.array(_eq_pts),
                            trades_df=None,
                            periods_per_year=periods_per_year,
                            n_bars_total=len(df),
                        )
                        _cython_result.update(_rm)
                        # Bug #1 fix: profit_factor et max_consecutive_losses toujours 0
                        # car trades_df=None est passé à compute_risk_metrics.
                        # On les calcule directement depuis _sell_profits.
                        _gp = sum(p for p in _sell_profits if p > 0)
                        _gl = abs(sum(p for p in _sell_profits if p < 0))
                        _pf = _gp / _gl if _gl > 0 else (999.0 if _gp > 0 else 0.0)
                        _cython_result['profit_factor'] = round(min(_pf, 999.0), 4)
                        _streak, _max_cl = 0, 0
                        for _p in _sell_profits:
                            if _p < 0:
                                _streak += 1
                                _max_cl = max(_max_cl, _streak)
                            else:
                                _streak = 0
                        _cython_result['max_consecutive_losses'] = _max_cl
                        # Source de vérité unique pour max_drawdown (Cython path) :
                        # max(DD inline Cython in-position, DD compute_risk_metrics sur equity trade-level)
                        _metrics_dd_cy = _rm.get('max_drawdown', 0.0)
                        _cython_result['max_drawdown'] = max(
                            _cython_result['max_drawdown'], _metrics_dd_cy
                        )
                except Exception as _exc:
                    logger.debug("[backtest_runner] compute_risk_metrics Cython a échoué: %s", _exc)
                return _cython_result
            except Exception as e:
                logger.warning(
                    f"Cython backtest failed, using Python fallback: {e}"
                )
                traceback.print_exc()

        # === PYTHON FALLBACK ===
        df_work = df.copy()
        if f'ema_{ema1_period}' not in df_work.columns:
            df_work[f'ema_{ema1_period}'] = df_work['close'].ewm(
                span=ema1_period, adjust=False
            ).mean()
        if f'ema_{ema2_period}' not in df_work.columns:
            df_work[f'ema_{ema2_period}'] = df_work['close'].ewm(
                span=ema2_period, adjust=False
            ).mean()
        df_work['ema1'] = df_work[f'ema_{ema1_period}']
        df_work['ema2'] = df_work[f'ema_{ema2_period}']

        if sma_long:
            df_work['sma_long'] = df_work['close'].rolling(window=sma_long).mean()
        if adx_period and 'adx' in df.columns:
            df_work['adx'] = df['adx'].values
        elif adx_period:
            df_work['adx'] = ADXIndicator(
                high=df_work['high'],
                low=df_work['low'],
                close=df_work['close'],
                window=adx_period,
            ).adx()
        if trix_length and trix_signal:
            trix_ema1 = df_work['close'].ewm(
                span=trix_length, adjust=False
            ).mean()
            trix_ema2 = trix_ema1.ewm(
                span=trix_length, adjust=False
            ).mean()
            trix_ema3 = trix_ema2.ewm(
                span=trix_length, adjust=False
            ).mean()
            df_work['TRIX_PCT'] = trix_ema3.pct_change() * 100
            df_work['TRIX_SIGNAL'] = df_work['TRIX_PCT'].rolling(
                window=trix_signal
            ).mean()
            df_work['TRIX_HISTO'] = df_work['TRIX_PCT'] - df_work['TRIX_SIGNAL']

        # A-1: Volume filter — SMA du volume (Python fallback)
        if getattr(config, 'volume_filter_enabled', False) and 'volume' in df_work.columns:
            _vol_period = int(getattr(config, 'volume_sma_period', 20))
            df_work['vol_sma'] = df_work['volume'].rolling(window=_vol_period).mean()

        # A-2: Multi-timeframe filter (Python fallback)
        _use_mtf_py = getattr(config, 'mtf_filter_enabled', False)
        _mtf_bullish_py = None
        if _use_mtf_py and isinstance(df_work.index, pd.DatetimeIndex):
            try:
                _mtf_fast_py = getattr(config, 'mtf_ema_fast', 18)
                _mtf_slow_py = getattr(config, 'mtf_ema_slow', 58)
                _mtf_bullish_py = _compute_mtf_bullish(df_work, _mtf_fast_py, _mtf_slow_py)
            except Exception:
                _use_mtf_py = False

        # P2-02: Précomputer le rang de volume (percentile roulant 50 bars) pour
        # le modèle de slippage stochastique OOS.  0=faible volume, 1=fort volume.
        _vol_rank_arr: Optional[np.ndarray] = None
        if slippage_model is not None and 'volume' in df_work.columns:
            _vol_series = df_work['volume'].rolling(50, min_periods=1).rank(pct=True)
            _vol_rank_arr = _vol_series.to_numpy(dtype=np.float64)

        # --- Python backtest loop ---
        usd = config.initial_wallet
        coin = 0.0
        trades_history: List[Any] = []
        in_position = False
        entry_price = 0.0
        entry_usd_invested = 0.0
        max_price = 0.0
        trailing_stop = 0.0
        trailing_stop_activated = False
        stop_loss = 0.0
        max_drawdown = 0.0
        peak_wallet = config.initial_wallet
        winning_trades = 0
        total_trades = 0
        equity_curve = [config.initial_wallet]

        atr_at_entry = 0.0
        stop_loss_at_entry = 0.0
        trailing_activation_price_at_entry = 0.0
        partial_taken_1 = False
        partial_taken_2 = False
        breakeven_triggered = False
        # A-3: cooldown after stop-loss/breakeven exit
        cooldown_remaining = 0
        _cooldown_candles = getattr(config, 'stop_loss_cooldown_candles', 0)

        for i in range(len(df_work)):
            idx_signal = i
            row_close = df_work['close'].iloc[i]
            row_atr = df_work['atr'].iloc[i]
            index = df_work.index[i]

            # === EQUITY TRACKING — tous les bars (en ET hors position) ===
            # coin=0 quand hors position, donc usd + 0 = wallet cash correct.
            # Cela garantit que le DD entre les trades est toujours capturé.
            current_wallet = usd + (coin * row_close)
            equity_curve.append(current_wallet)
            if current_wallet > peak_wallet:
                peak_wallet = current_wallet
            _bar_dd = (
                (peak_wallet - current_wallet) / peak_wallet
                if peak_wallet > 0 else 0.0
            )
            max_drawdown = max(max_drawdown, _bar_dd)

            if in_position:
                trailing_distance = config.atr_multiplier * atr_at_entry
                if row_close > max_price:
                    max_price = row_close
                if (
                    not trailing_stop_activated
                    and row_close >= trailing_activation_price_at_entry
                ):
                    trailing_stop_activated = True
                    trailing_stop = max_price - trailing_distance
                if trailing_stop_activated:
                    new_trailing = max_price - trailing_distance
                    if new_trailing > trailing_stop:
                        trailing_stop = new_trailing

                # B-3: Break-even stop — remonter SL au prix d'entrée si profit >= seuil
                _be_enabled = getattr(config, 'breakeven_enabled', True)
                if _be_enabled and not breakeven_triggered and entry_price > 0:
                    _be_profit = (row_close - entry_price) / entry_price
                    if _be_profit >= getattr(config, 'breakeven_trigger_pct', 0.015):
                        _be_new_stop = entry_price * (1 + config.slippage_buy)
                        if _be_new_stop > stop_loss_at_entry:
                            stop_loss_at_entry = _be_new_stop
                        breakeven_triggered = True

                # Partial profit taking (P2-01: conditionné par partial_enabled)
                # partial_enabled : vérifier si la position est assez grosse pour
                # des partials sûrs (valeur totale >= 3× min_notional pour absorber
                # 2 partials + le reste). Identique au guard live.
                if partial_enabled:
                    _bt_min_notional = getattr(config, 'backtest_min_notional', 5.0)
                    _partial_ok = (coin * row_close) >= (_bt_min_notional * 3)

                    if in_position and coin > 0 and entry_price > 0 and _partial_ok:
                        profit_pct = (row_close - entry_price) / entry_price
                        if not partial_taken_1 and profit_pct >= config.partial_threshold_1:
                            partial_qty_1 = coin * config.partial_pct_1
                            # MIN_NOTIONAL: bloquer si la valeur de la vente partielle < seuil
                            if partial_qty_1 * row_close >= _bt_min_notional:
                                partial_proceeds_1 = (
                                    partial_qty_1 * row_close * (1 - config.backtest_taker_fee)
                                )
                                usd += partial_proceeds_1
                                coin -= partial_qty_1
                                # P2-01: log partial dans trades_history
                                trades_history.append({
                                    'date': index,
                                    'type': 'partial_sell_1',
                                    'price': row_close,
                                    'qty': partial_qty_1,
                                    'proceeds': partial_proceeds_1,
                                    'profit_pct': profit_pct,
                                })
                            partial_taken_1 = True  # flag True même si bloqué (éviter retry)
                        if (
                            not partial_taken_2
                            and profit_pct >= config.partial_threshold_2
                            and coin > 0
                        ):
                            partial_qty_2 = coin * config.partial_pct_2
                            # MIN_NOTIONAL: bloquer si la valeur de la vente partielle < seuil
                            if partial_qty_2 * row_close >= _bt_min_notional:
                                partial_proceeds_2 = (
                                    partial_qty_2 * row_close * (1 - config.backtest_taker_fee)
                                )
                                usd += partial_proceeds_2
                                coin -= partial_qty_2
                                # P2-01: log partial dans trades_history
                                trades_history.append({
                                    'date': index,
                                    'type': 'partial_sell_2',
                                    'price': row_close,
                                    'qty': partial_qty_2,
                                    'proceeds': partial_proceeds_2,
                                    'profit_pct': profit_pct,
                                })
                            partial_taken_2 = True  # flag True même si bloqué (éviter retry)

                # Exit conditions
                exit_trade = False
                motif_sortie = None
                if row_close <= stop_loss_at_entry:
                    exit_trade = True
                    motif_sortie = 'STOP_LOSS'
                elif trailing_stop_activated and row_close <= trailing_stop:
                    exit_trade = True
                    motif_sortie = 'TRAILING_STOP'
                elif (
                    df_work['ema2'].iloc[i] > df_work['ema1'].iloc[i]
                    and df_work['stoch_rsi'].iloc[i] > config.stoch_rsi_sell_exit  # P2-08
                ):
                    exit_trade = True
                    motif_sortie = 'SIGNAL'

                if exit_trade:
                    if i + 1 < len(df_work):
                        exit_base_price = df_work.iloc[i + 1]['open']
                    else:
                        exit_base_price = row_close
                    optimized_exit_price = exit_base_price * (1 - config.slippage_sell)
                    # P2-02: appliquer le slippage stochastique OOS si activé
                    if slippage_model is not None:
                        _vr = float(_vol_rank_arr[i]) if _vol_rank_arr is not None else 0.5
                        optimized_exit_price = optimized_exit_price * slippage_model.sell_factor(_vr)
                    gross_proceeds = coin * optimized_exit_price
                    fee = gross_proceeds * config.backtest_taker_fee
                    usd = usd + (gross_proceeds - fee)
                    coin = 0.0
                    trade_profit = usd - entry_usd_invested
                    # Correction post-exit : l'exit se fait à open[i+1], pas close[i].
                    # Si le prix a gappé à la baisse (stop-loss), le vrai wallet post-exit
                    # est inférieur à ce qu'on a tracké en haut du bar → on corrige.
                    _post_exit_wallet = usd  # coin=0, tout en cash
                    equity_curve[-1] = min(equity_curve[-1], _post_exit_wallet)
                    _post_exit_dd = (
                        (peak_wallet - _post_exit_wallet) / peak_wallet
                        if peak_wallet > 0 else 0.0
                    )
                    max_drawdown = max(max_drawdown, _post_exit_dd)
                    if trade_profit > 0:
                        winning_trades += 1
                    total_trades += 1
                    trades_history.append({
                        'date': index,
                        'type': 'sell',
                        'price': optimized_exit_price,
                        'profit': trade_profit,
                        'motif': motif_sortie,
                        'stop_loss': f'{stop_loss:.4f} USDC',
                        'trailing_stop': f'{trailing_stop:.4f} USDC',
                    })
                    in_position = False
                    entry_price = 0.0
                    entry_usd_invested = 0.0
                    max_price = 0.0
                    trailing_stop = 0.0
                    stop_loss = 0.0
                    trailing_stop_activated = False
                    breakeven_triggered = False
                    # A-3: set cooldown after stop-loss/breakeven exit
                    if motif_sortie == 'STOP_LOSS' and _cooldown_candles > 0:
                        cooldown_remaining = _cooldown_candles
                    continue

            # A-3: cooldown decrement when not in position
            if not in_position and cooldown_remaining > 0:
                cooldown_remaining -= 1

            # Buy condition
            buy_condition = (
                df_work['ema1'].iloc[idx_signal] > df_work['ema2'].iloc[idx_signal]
                and df_work['stoch_rsi'].iloc[idx_signal] < config.stoch_rsi_buy_max  # P2-08
                and df_work['stoch_rsi'].iloc[idx_signal] > config.stoch_rsi_buy_min  # P2-08
                and usd > 0
            )
            # A-3: block buy during cooldown after stop-loss/breakeven exit
            if buy_condition and cooldown_remaining > 0:
                buy_condition = False
            if sma_long and 'sma_long' in df_work.columns:
                buy_condition &= row_close > df_work['sma_long'].iloc[idx_signal]
            if adx_period and 'adx' in df_work.columns:
                buy_condition &= df_work['adx'].iloc[idx_signal] > config.adx_threshold  # P2-08
            if trix_length and 'TRIX_HISTO' in df_work.columns:
                buy_condition &= df_work['TRIX_HISTO'].iloc[idx_signal] > 0

            # A-1: Volume filter — volume > SMA(volume)
            if getattr(config, 'volume_filter_enabled', False) and 'vol_sma' in df_work.columns:
                _vol = df_work['volume'].iloc[idx_signal]
                _vol_sma = df_work['vol_sma'].iloc[idx_signal]
                if not (isinstance(_vol, float) and isinstance(_vol_sma, float)):
                    buy_condition = False
                elif math.isnan(_vol) or math.isnan(_vol_sma) or _vol_sma <= 0:
                    buy_condition = False
                else:
                    buy_condition &= _vol > _vol_sma

            # A-2: Multi-timeframe filter — 4h trend must be bullish
            if buy_condition and _use_mtf_py and _mtf_bullish_py is not None:
                buy_condition = _mtf_bullish_py[i] > 0.5

            # P0-SL-GUARD: bloquer l'achat si ATR indisponible → SL incalculable
            _atr_invalid = (
                row_atr is None
                or (isinstance(row_atr, float) and math.isnan(row_atr))
                or row_atr <= 0
            )
            if _atr_invalid:
                buy_condition = False

            if buy_condition and not in_position:
                if i + 1 < len(df_work):
                    base_price = df_work.iloc[i + 1]['open']
                else:
                    base_price = row_close
                optimized_price = base_price * (1 + config.slippage_buy)
                # P2-02: appliquer le slippage stochastique OOS si activé
                if slippage_model is not None:
                    _vr = float(_vol_rank_arr[i]) if _vol_rank_arr is not None else 0.5
                    optimized_price = optimized_price * slippage_model.buy_factor(_vr)

                if sizing_mode == 'baseline':
                    gross_coin = (
                        (usd * 0.98) / optimized_price
                        if optimized_price > 0
                        else 0.0
                    )
                elif sizing_mode == 'risk':
                    if row_atr and row_atr > 0 and optimized_price > 0:
                        try:
                            qty_by_risk = compute_position_size_by_risk(
                                equity=usd,
                                atr_value=row_atr,
                                entry_price=optimized_price,
                                risk_pct=config.risk_per_trade,
                                stop_atr_multiplier=config.atr_stop_multiplier,
                            )
                        except SizingError:
                            qty_by_risk = 0.0
                        max_affordable = (usd * 0.98) / optimized_price
                        gross_coin = min(max_affordable, qty_by_risk)
                    else:
                        gross_coin = (
                            (usd * 0.98) / optimized_price
                            if optimized_price > 0
                            else 0.0
                        )
                elif sizing_mode == 'fixed_notional':
                    if optimized_price > 0:
                        notional_per_trade = usd * 0.1
                        try:
                            qty_fixed = compute_position_size_fixed_notional(
                                equity=usd,
                                notional_per_trade_usd=notional_per_trade,
                                entry_price=optimized_price,
                            )
                        except SizingError:
                            qty_fixed = 0.0
                        max_affordable = (usd * 0.98) / optimized_price
                        gross_coin = min(max_affordable, qty_fixed)
                    else:
                        gross_coin = 0.0
                elif sizing_mode == 'volatility_parity':
                    if row_atr and row_atr > 0 and optimized_price > 0:
                        try:
                            qty_vol = compute_position_size_volatility_parity(
                                equity=usd,
                                atr_value=row_atr,
                                entry_price=optimized_price,
                                target_volatility_pct=config.target_volatility_pct,
                            )
                        except SizingError:
                            qty_vol = 0.0
                        max_affordable = (usd * 0.98) / optimized_price
                        gross_coin = min(max_affordable, qty_vol)
                    else:
                        gross_coin = (
                            (usd * 0.98) / optimized_price
                            if optimized_price > 0
                            else 0.0
                        )
                else:
                    gross_coin = (
                        (usd * 0.98) / optimized_price
                        if optimized_price > 0
                        else 0.0
                    )

                if gross_coin and gross_coin > 0:
                    fee_in_coin = gross_coin * config.backtest_taker_fee
                    coin = gross_coin - fee_in_coin
                    # P1-07-FIX: only deduct actual cost of coins purchased.
                    # Previously usd was zeroed entirely, destroying uninvested
                    # cash when sizing_mode != 'baseline' and causing 100% loss.
                    actual_cost = gross_coin * optimized_price
                    if actual_cost > usd:
                        actual_cost = usd  # safety cap
                    entry_usd_invested = usd
                    usd = usd - actual_cost
                    entry_price = optimized_price
                    max_price = optimized_price
                    atr_at_entry = row_atr
                    stop_loss_at_entry = optimized_price - (
                        config.atr_stop_multiplier * atr_at_entry
                    )
                    trailing_activation_price_at_entry = optimized_price + (
                        config.atr_multiplier * atr_at_entry
                    )
                    trailing_stop = 0.0
                    trailing_stop_activated = False
                    partial_taken_1 = False
                    partial_taken_2 = False
                    breakeven_triggered = False
                    trades_history.append({
                        'date': index,
                        'type': 'buy',
                        'price': optimized_price,
                    })
                    in_position = True

        # Final wallet
        win_rate = (
            (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        )
        final_wallet = (
            usd + (coin * df_work['close'].iloc[-1]) if in_position else usd
        )
        equity_curve.append(final_wallet)

        # Compute risk-adjusted metrics
        # n_bars_total = len(df_work) : permet à compute_risk_metrics de calculer
        # _years sur le span réel des données (3 ans) et non sur les bars in-position.
        try:
            from walk_forward import compute_risk_metrics
            risk_metrics = compute_risk_metrics(
                np.array(equity_curve),
                trades_df=(
                    pd.DataFrame(trades_history) if trades_history else None
                ),
                periods_per_year=periods_per_year,
                risk_free_rate=config.risk_free_rate,  # P2-03
                n_bars_total=len(df_work),
            )
        except Exception:
            risk_metrics = {}

        # Source de vérité unique pour max_drawdown :
        # max(inline DD calculé bar-à-bar, DD calculé par compute_risk_metrics sur equity curve)
        # Les deux peuvent légèrement différer selon la résolution ; on prend le plus élevé.
        _metrics_dd = risk_metrics.get('max_drawdown', 0.0)
        final_max_drawdown = max(max_drawdown, _metrics_dd)

        result: Dict[str, Any] = {
            'final_wallet': final_wallet,
            'trades': pd.DataFrame(trades_history),
            'max_drawdown': final_max_drawdown,
            'win_rate': win_rate,
        }
        result.update(risk_metrics)
        # Assurer que max_drawdown dans result reflète la valeur unifiée
        result['max_drawdown'] = final_max_drawdown
        return result

    except Exception as e:
        logger.error(f"Erreur dans backtest_from_dataframe: {e}", exc_info=True)
        return {
            'final_wallet': 0.0,
            'trades': pd.DataFrame(),
            'max_drawdown': 0.0,
            'win_rate': 0.0,
        }


# --- Result Helpers -----------------------------------------------------------

def empty_result_dict(
    timeframe: str, ema1: int, ema2: int, scenario_name: str,
) -> Dict[str, Any]:
    """Retourne un résultat de backtest vide en cas d'erreur.

    Parameters
    ----------
    timeframe : str
        Intervalle kline.
    ema1, ema2 : int
        Périodes EMA.
    scenario_name : str
        Nom du scénario (ex. ``'StochRSI'``).

    Returns
    -------
    dict
        Résultat avec ``final_wallet=0`` et métriques à zéro.
    """
    return {
        'timeframe': timeframe,
        'ema_periods': (ema1, ema2),
        'scenario': scenario_name,
        'initial_wallet': config.initial_wallet,
        'final_wallet': 0.0,
        'trades': pd.DataFrame(),
        'max_drawdown': 0.0,
        'win_rate': 0.0,
        'sharpe_ratio': 0.0,
        'sortino_ratio': 0.0,
        'calmar_ratio': 0.0,
    }


def run_single_backtest_optimized(args: Tuple[Any, ...]) -> Dict[str, Any]:
    """Exécute un backtest unique à partir d'un DataFrame préparé.

    Parameters
    ----------
    args : tuple
        ``(timeframe, ema1, ema2, scenario_dict, base_df, pair_symbol
        [, sizing_mode])``.

    Returns
    -------
    dict
        Résultat complet du backtest.  Voir ``backtest_from_dataframe``.
    """
    if len(args) == 6:
        (timeframe, ema1, ema2, scenario, base_df, _pair_symbol) = args
        sizing_mode = 'risk'  # P1-07: default risk au lieu de baseline
    else:
        _args7 = cast(
            'Tuple[Any, Any, Any, Any, Any, Any, Any]', args
        )
        (timeframe, ema1, ema2, scenario, base_df, pair_symbol, sizing_mode) = _args7
    try:
        result = backtest_from_dataframe(
            df=base_df,
            ema1_period=ema1,
            ema2_period=ema2,
            sma_long=scenario['params'].get('sma_long'),
            adx_period=scenario['params'].get('adx_period'),
            trix_length=scenario['params'].get('trix_length'),
            trix_signal=scenario['params'].get('trix_signal'),
            sizing_mode=sizing_mode,
        )
        return {
            'timeframe': timeframe,
            'ema_periods': (ema1, ema2),
            'scenario': scenario['name'],
            'initial_wallet': config.initial_wallet,
            'final_wallet': result['final_wallet'],
            'trades': result['trades'],
            'max_drawdown': result['max_drawdown'],
            'win_rate': result['win_rate'],
            'sharpe_ratio': result.get('sharpe_ratio', 0.0),
            'sortino_ratio': result.get('sortino_ratio', 0.0),
            'calmar_ratio': result.get('calmar_ratio', 0.0),
        }
    except Exception as e:
        logger.error(f"Erreur backtest parallele: {e}")
        return empty_result_dict(timeframe, ema1, ema2, scenario['name'])


def run_all_backtests(
    backtest_pair: str,
    start_date: str,
    timeframes: List[str],
    sizing_mode: str = 'risk',  # P1-07
    *,
    prepare_base_dataframe_fn: Optional[Callable[..., Optional[pd.DataFrame]]] = None,
) -> List[Dict[str, Any]]:
    """Exécute tous les backtests en parallèle (version optimisée).

    Pour chaque timeframe, prépare un DataFrame de base, génère les
    combinaisons EMA × scénarios et les distribue en ``ThreadPoolExecutor``.

    Parameters
    ----------
    backtest_pair : str
        Paire de trading.
    start_date : str
        Date de début.
    timeframes : list[str]
        Intervalles kline à tester.
    sizing_mode : str
        Mode position sizing.
    prepare_base_dataframe_fn : callable
        ``fn(pair, tf, start_date, stoch_period) -> DataFrame``.
        Injecté depuis MULTI_SYMBOLS.

    Returns
    -------
    list[dict]
        Liste de résultats, un par combinaison (timeframe, EMA, scénario).

    Raises
    ------
    ValueError
        Si ``prepare_base_dataframe_fn`` n'est pas fourni.
    """
    if prepare_base_dataframe_fn is None:
        raise ValueError("prepare_base_dataframe_fn must be provided")

    results: List[Dict[str, Any]] = []

    # Preparer 1 DataFrame par timeframe
    base_dataframes: Dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        df = prepare_base_dataframe_fn(backtest_pair, tf, start_date, 14)
        base_dataframes[tf] = (
            df if df is not None and not df.empty else pd.DataFrame()
        )

    # EMA adaptatives par timeframe
    ema_periods_by_tf: Dict[str, List[Tuple[int, int]]] = {}
    extra_ema_pairs = [(18, 36), (20, 40), (30, 60)]
    for tf, df_tf in base_dataframes.items():
        if df_tf is not None and not df_tf.empty:
            is_end = max(int(len(df_tf) * 0.70), 1)
            adaptive_ema = get_optimal_ema_periods(
                df_tf.iloc[:is_end], timeframe=tf, symbol=backtest_pair
            )
        else:
            adaptive_ema = (26, 50)
        ema_periods_by_tf[tf] = [adaptive_ema, (26, 50)] + extra_ema_pairs

    scenarios = [
        {'name': 'StochRSI', 'params': {'stoch_period': 14}},
        {'name': 'StochRSI_SMA', 'params': {'stoch_period': 14, 'sma_long': 200}},
        {'name': 'StochRSI_ADX', 'params': {'stoch_period': 14, 'adx_period': 14}},
        {
            'name': 'StochRSI_TRIX',
            'params': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15},
        },
    ]

    tasks: List[Tuple[Any, ...]] = []
    for timeframe in timeframes:
        base_df = base_dataframes.get(timeframe, pd.DataFrame())
        if base_df.empty:
            continue
        _is_end_sel = max(int(len(base_df) * 0.70), 1)
        is_df = base_df.iloc[:_is_end_sel].copy()

        # C-14: Recompute ALL indicators on the IS slice only to guarantee
        # structural isolation from OOS data.  Since EMA/RSI/ATR/StochRSI
        # are causal (backward-looking only), the values are mathematically
        # identical to those computed on the full dataset, but this makes
        # the guarantee structural and eliminates any dependency on
        # indicator implementation details.
        for _ema_p in [14, 25, 26, 45, 50]:
            _col = f'ema_{_ema_p}'
            if _col in is_df.columns:
                is_df[_col] = is_df['close'].ewm(span=_ema_p, adjust=False).mean()
        if 'rsi' in is_df.columns:
            from ta.momentum import RSIIndicator as _RSI
            is_df['rsi'] = _RSI(is_df['close'], window=14).rsi()
        if 'atr' in is_df.columns:
            from ta.volatility import AverageTrueRange as _ATR
            is_df['atr'] = _ATR(
                high=is_df['high'], low=is_df['low'],
                close=is_df['close'], window=config.atr_period,
            ).average_true_range()
        if 'stoch_rsi' in is_df.columns and 'rsi' in is_df.columns:
            from indicators_engine import compute_stochrsi as _stochrsi
            is_df['stoch_rsi'] = _stochrsi(is_df['rsi'], period=14)
        is_df.dropna(subset=['close', 'rsi', 'atr'], inplace=True)

        ema_periods = ema_periods_by_tf.get(timeframe, [(26, 50)])
        ema_periods_unique: List[Tuple[int, int]] = []
        for pair in ema_periods:
            if pair not in ema_periods_unique:
                ema_periods_unique.append(pair)
        # Pre-compute ALL EMA columns on is_df before spawning threads
        # to avoid race condition (concurrent writes → duplicate columns).
        _all_ema_periods: Set[int] = set()
        for _e1, _e2 in ema_periods_unique:
            _all_ema_periods.add(_e1)
            _all_ema_periods.add(_e2)
        for _ema_p in _all_ema_periods:
            _col = f'ema_{_ema_p}'
            if _col not in is_df.columns:
                is_df[_col] = is_df['close'].ewm(span=_ema_p, adjust=False).mean()
        for ema1, ema2 in ema_periods_unique:
            for scenario in scenarios:
                tasks.append(
                    (timeframe, ema1, ema2, scenario, is_df, backtest_pair, sizing_mode)
                )

    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        future_to_task = {
            executor.submit(run_single_backtest_optimized, task): task
            for task in tasks
        }
        with tqdm(
            total=len(tasks),
            desc="[BACKTESTS]",
            colour="green",
            bar_format=(
                "{desc}: {percentage:3.0f}%|"
                "\u2588{bar:30}\u2588| "
                "{n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"
            ),
        ) as pbar:
            for future in as_completed(future_to_task):
                try:
                    result = future.result()
                    results.append(result)
                    pbar.update(1)
                except Exception as e:
                    logger.error(f"Erreur future: {e}")
                    pbar.update(1)

    return results


def run_parallel_backtests(
    crypto_pairs: List[Dict[str, str]],
    start_date: str,
    timeframes: List[str],
    sizing_mode: str = 'risk',  # P1-07
    *,
    prepare_base_dataframe_fn: Optional[Callable[..., Optional[pd.DataFrame]]] = None,
) -> Dict[str, Any]:
    """Exécute les backtests multi-paires en parallèle.

    Parameters
    ----------
    crypto_pairs : list[dict]
        ``[{'backtest_pair': 'BTCUSDC', 'real_pair': 'BTCUSDC'}, ...]``
    start_date : str
        Date de début.
    timeframes : list[str]
        Intervalles kline.
    sizing_mode : str
        Mode position sizing.
    prepare_base_dataframe_fn : callable
        Injecté depuis MULTI_SYMBOLS.

    Returns
    -------
    dict
        ``{backtest_pair: {'real_pair': ..., 'results': [...]}, ...}``
    """
    max_workers = min(len(crypto_pairs), config.max_parallel_pairs)  # P2-09
    results_by_pair: Dict[str, Any] = {}

    extra_ema_pairs = [(18, 36), (20, 40), (30, 60)]
    ema_periods_by_tf = {
        tf: [(26, 50), (14, 26)] + extra_ema_pairs for tf in timeframes
    }
    scenarios = [
        {'name': 'StochRSI', 'params': {'stoch_period': 14}},
        {'name': 'StochRSI_SMA', 'params': {'stoch_period': 14, 'sma_long': 200}},
        {'name': 'StochRSI_ADX', 'params': {'stoch_period': 14, 'adx_period': 14}},
        {
            'name': 'StochRSI_TRIX',
            'params': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15},
        },
    ]
    total_tasks = 0
    for tf in timeframes:
        ema_list = ema_periods_by_tf.get(tf, [(26, 50)])
        total_tasks += len(crypto_pairs) * len(ema_list) * len(scenarios)

    console.print(
        f"\n[bold cyan]Lancement de {total_tasks} backtests "
        "avec optimisation sniper...[/bold cyan]"
    )

    def run_single_pair(pair_info: Dict[str, Any]) -> Tuple[str, str, List[Any]]:
        try:
            results = run_all_backtests(
                pair_info["backtest_pair"],
                start_date,
                timeframes,
                sizing_mode=sizing_mode,
                prepare_base_dataframe_fn=prepare_base_dataframe_fn,
            )
            return pair_info["backtest_pair"], pair_info["real_pair"], results
        except Exception as e:
            logger.error(f"Erreur backtest {pair_info['backtest_pair']}: {e}")
            return pair_info["backtest_pair"], pair_info["real_pair"], []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_pair = {
            executor.submit(run_single_pair, pair): pair
            for pair in crypto_pairs
        }
        for future in as_completed(future_to_pair):
            try:
                backtest_pair_name, real_pair, results = future.result()
                results_by_pair[backtest_pair_name] = {
                    'real_pair': real_pair,
                    'results': results,
                }
            except Exception as e:
                logger.error(f"Erreur future: {e}")

    return results_by_pair
