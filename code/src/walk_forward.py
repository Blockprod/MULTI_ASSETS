"""
Walk-Forward Validation & Risk-Adjusted Metrics
================================================
Institutional-grade backtesting validation to prevent overfitting.

Components
----------
- Risk-adjusted performance metrics (Sharpe, Sortino, Calmar, Profit Factor)
- Anchored walk-forward validation with rolling folds
- Out-of-sample (OOS) quality gates

References
----------
- Bailey, Borwein, Lopez de Prado & Zhu (2014): "Pseudo-Mathematics and Financial Charlatanism"
- Lopez de Prado (2018): "Advances in Financial Machine Learning", Ch. 12 (Walk-Forward)
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Optional, Any, Callable
import logging

logger = logging.getLogger("walk_forward")


# ──────────────────────────────────────────────────────────────
# Constants & OOS Quality Gates
# ──────────────────────────────────────────────────────────────
OOS_SHARPE_MIN = 0.5       # Minimum OOS annualized Sharpe
OOS_WIN_RATE_MIN = 45.0    # Minimum OOS win rate (%)
RISK_FREE_RATE = 0.04      # Annual risk-free rate (≈ US T-bills 2024)


# ──────────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────────
def timeframe_to_periods_per_year(tf: str) -> int:
    """Convert timeframe string to annualization factor (bars per year)."""
    mapping = {
        '1m': 525960,
        '5m': 105192,
        '15m': 35064,
        '30m': 17532,
        '1h': 8766,
        '2h': 4383,
        '4h': 2191,
        '6h': 1461,
        '8h': 1096,
        '12h': 730,
        '1d': 365,
        '3d': 122,
        '1w': 52,
    }
    return mapping.get(tf, 8766)


# ──────────────────────────────────────────────────────────────
# Risk-Adjusted Metrics  (P1.2)
# ──────────────────────────────────────────────────────────────
_DEFAULT_METRICS: Dict[str, Any] = {
    'sharpe_ratio': 0.0,
    'sortino_ratio': 0.0,
    'calmar_ratio': 0.0,
    'profit_factor': 0.0,
    'max_consecutive_losses': 0,
    'total_return_pct': 0.0,
    'annual_return_pct': 0.0,
    'annual_volatility': 0.0,
}


def default_risk_metrics() -> Dict[str, Any]:
    """Return a copy of the default (zero) risk metrics dict."""
    return dict(_DEFAULT_METRICS)


def compute_risk_metrics(
    equity_curve: np.ndarray,
    trades_df: Optional[pd.DataFrame] = None,
    periods_per_year: int = 8766,
    risk_free_rate: float = RISK_FREE_RATE,
) -> Dict[str, Any]:
    """
    Compute institutional risk-adjusted performance metrics from a bar-level
    equity curve.

    Parameters
    ----------
    equity_curve : array-like
        Portfolio value at each bar.
    trades_df : DataFrame, optional
        Trade log with columns ``type`` and ``profit`` (sell rows).
    periods_per_year : int
        Number of bars per year (for annualization).
    risk_free_rate : float
        Annual risk-free rate for excess-return calculations.

    Returns
    -------
    dict
        sharpe_ratio, sortino_ratio, calmar_ratio, profit_factor,
        max_consecutive_losses, total_return_pct, annual_return_pct,
        annual_volatility.
    """
    metrics = default_risk_metrics()

    equity = np.asarray(equity_curve, dtype=np.float64)
    if len(equity) < 2 or equity[0] <= 0:
        return metrics

    # ---- Bar-level simple returns ----
    with np.errstate(divide='ignore', invalid='ignore'):
        returns = np.diff(equity) / equity[:-1]
    returns = returns[np.isfinite(returns)]
    if len(returns) < 2:
        return metrics

    # ---- Total & Annual Return ----
    total_return = (equity[-1] / equity[0]) - 1.0
    n_bars = len(equity)
    years = max(n_bars / periods_per_year, 1e-6)

    if total_return > -1.0:
        annual_return = (1.0 + total_return) ** (1.0 / years) - 1.0
    else:
        annual_return = -1.0

    # ---- Annualized Volatility ----
    bar_vol = np.std(returns, ddof=1)
    annual_vol = bar_vol * np.sqrt(periods_per_year)

    # ---- Sharpe Ratio (annualized, excess over risk-free) ----
    rf_per_bar = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = returns - rf_per_bar
    excess_mean = np.mean(excess)
    excess_std = np.std(excess, ddof=1)
    sharpe = (
        (excess_mean / excess_std) * np.sqrt(periods_per_year)
        if excess_std > 1e-12
        else 0.0
    )

    # ---- Sortino Ratio (downside deviation only) ----
    downside = excess[excess < 0]
    if len(downside) > 1:
        downside_std = np.std(downside, ddof=1)
    else:
        downside_std = excess_std  # fallback
    sortino = (
        (excess_mean / downside_std) * np.sqrt(periods_per_year)
        if downside_std > 1e-12
        else 0.0
    )

    # ---- Max Drawdown ----
    cummax = np.maximum.accumulate(equity)
    drawdowns = (cummax - equity) / cummax
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    # ---- Calmar Ratio ----
    calmar = annual_return / max_dd if max_dd > 1e-12 else 0.0

    # ---- Trade-level metrics ----
    profit_factor = 0.0
    max_consec_losses = 0

    if trades_df is not None and not trades_df.empty and 'profit' in trades_df.columns:
        sell_trades = trades_df[trades_df['type'] == 'sell']
        if not sell_trades.empty:
            profits = np.asarray(sell_trades['profit'].values, dtype=np.float64)
            gross_profit = float(np.sum(profits[profits > 0]))
            gross_loss = float(abs(np.sum(profits[profits < 0])))
            if gross_loss > 0:
                profit_factor = gross_profit / gross_loss
            elif gross_profit > 0:
                profit_factor = 999.0  # cap instead of inf

            # Max consecutive losses
            streak = 0
            for p in profits:
                if p < 0:
                    streak += 1
                    max_consec_losses = max(max_consec_losses, streak)
                else:
                    streak = 0

    metrics['sharpe_ratio'] = round(float(sharpe), 4)
    metrics['sortino_ratio'] = round(float(sortino), 4)
    metrics['calmar_ratio'] = round(float(calmar), 4)
    metrics['profit_factor'] = round(float(min(profit_factor, 999.0)), 4)
    metrics['max_consecutive_losses'] = int(max_consec_losses)
    metrics['total_return_pct'] = round(float(total_return * 100), 4)
    metrics['annual_return_pct'] = round(float(annual_return * 100), 4)
    metrics['annual_volatility'] = round(float(annual_vol * 100), 4)

    return metrics


# ──────────────────────────────────────────────────────────────
# Walk-Forward Fold Splitting  (P1.1)
# ──────────────────────────────────────────────────────────────
def split_walk_forward_folds(
    df: pd.DataFrame,
    n_folds: int = 4,
    initial_train_pct: float = 0.40,
    min_train_bars: int = 500,
    min_test_bars: int = 200,
) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
    """
    Split a DataFrame into anchored (expanding-window) walk-forward folds.

    Layout example for 10 000 bars, ``n_folds=4``, ``initial_train_pct=0.40``::

        Fold 1: Train [0 : 4000]  Test [4000 : 5500]
        Fold 2: Train [0 : 5500]  Test [5500 : 7000]
        Fold 3: Train [0 : 7000]  Test [7000 : 8500]
        Fold 4: Train [0 : 8500]  Test [8500 : 10000]

    Parameters
    ----------
    df : DataFrame
        Full dataset with indicators already computed.
    n_folds : int
        Number of folds (≈ quarterly for 5-year data).
    initial_train_pct : float
        Fraction of data reserved for the first training window.
    min_train_bars, min_test_bars : int
        Hard minimums to ensure statistical significance.

    Returns
    -------
    list of (train_df, test_df) tuples
    """
    n = len(df)
    if n < min_train_bars + min_test_bars:
        logger.warning(
            f"Insufficient data for walk-forward: {n} bars "
            f"(need >= {min_train_bars + min_test_bars})"
        )
        return []

    initial_train_end = max(int(n * initial_train_pct), min_train_bars)
    test_pool = n - initial_train_end

    if test_pool < min_test_bars:
        logger.warning(f"Test pool too small: {test_pool} bars")
        return []

    test_window = max(test_pool // n_folds, min_test_bars)

    folds: List[Tuple[pd.DataFrame, pd.DataFrame]] = []
    for fold_idx in range(n_folds):
        test_start = initial_train_end + fold_idx * test_window
        test_end = min(test_start + test_window, n)

        if test_end - test_start < min_test_bars:
            break
        if test_start >= n:
            break

        train_df = df.iloc[:test_start].copy()
        test_df = df.iloc[test_start:test_end].copy()

        folds.append((train_df, test_df))
        logger.info(
            f"WF Fold {fold_idx + 1}/{n_folds}: "
            f"train[0:{test_start}] ({len(train_df)} bars) | "
            f"test[{test_start}:{test_end}] ({len(test_df)} bars)"
        )

    if not folds:
        logger.warning("No valid walk-forward folds could be created")
    return folds


# ──────────────────────────────────────────────────────────────
# OOS Quality Gate
# ──────────────────────────────────────────────────────────────
def validate_oos_result(sharpe: float, win_rate: float) -> bool:
    """
    Check if out-of-sample results pass institutional quality gates.

    Gates
    -----
    - OOS annualized Sharpe  > 0.5
    - OOS Win Rate           > 45 %
    """
    return sharpe > OOS_SHARPE_MIN and win_rate > OOS_WIN_RATE_MIN


# ──────────────────────────────────────────────────────────────
# Walk-Forward Validation Engine  (P1.1)
# ──────────────────────────────────────────────────────────────
def run_walk_forward_validation(
    base_dataframes: Dict[str, pd.DataFrame],
    full_sample_results: List[Dict[str, Any]],
    scenarios: List[Dict[str, Any]],
    backtest_fn: Callable,
    initial_capital: float = 10000.0,
    sizing_mode: str = 'risk',
    top_n: int = 5,
    n_folds: int = 4,
    initial_train_pct: float = 0.40,
) -> Dict[str, Any]:
    """
    Run anchored walk-forward validation for the top-N full-sample configs.

    Workflow
    --------
    1. Rank ``full_sample_results`` by Sharpe ratio, take top-N.
    2. For each top-N config, split its timeframe's data into ``n_folds``
       anchored folds.
    3. For each fold, run the config on both train (IS) and test (OOS).
    4. Average OOS Sharpe and Win Rate across folds.
    5. Select the config with the best average OOS Sharpe that passes
       the quality gates.

    Parameters
    ----------
    base_dataframes : dict
        ``{timeframe: DataFrame}`` with full indicator data already computed.
    full_sample_results : list of dict
        Results from ``run_all_backtests()`` — must contain ``sharpe_ratio``.
    scenarios : list of dict
        Scenario definitions (same as used in run_all_backtests).
    backtest_fn : callable
        ``backtest_from_dataframe(df, ema1, ema2, ..., sizing_mode, periods_per_year)``.
    initial_capital : float
        Starting capital for P&L calculations.
    sizing_mode : str
        Position sizing mode forwarded to ``backtest_fn``.
    top_n : int
        Number of top configs to validate (default 5).
    n_folds : int
        Number of WF folds.
    initial_train_pct : float
        Initial training fraction (expanding window starts here).

    Returns
    -------
    dict
        ``best_wf_config`` : best WF-validated config dict or None
        ``all_wf_results`` : list of per-config WF results
        ``any_passed``     : whether any config passed OOS gates
    """
    # 1. Rank by full-sample Sharpe and take top-N
    ranked = sorted(
        full_sample_results,
        key=lambda x: x.get('sharpe_ratio', 0.0),
        reverse=True,
    )
    top_configs = ranked[:top_n]

    if not top_configs:
        logger.warning("No full-sample results to validate")
        return {'best_wf_config': None, 'all_wf_results': [], 'any_passed': False}

    # Build scenario params lookup
    scenario_params_map = {s['name']: s['params'] for s in scenarios}

    wf_results: List[Dict[str, Any]] = []

    for cfg in top_configs:
        tf = cfg['timeframe']
        ema1, ema2 = cfg['ema_periods']
        scenario_name = cfg['scenario']
        full_df = base_dataframes.get(tf)

        if full_df is None or full_df.empty:
            continue

        ppy = timeframe_to_periods_per_year(tf)

        # Pre-compute EMA columns on full data (avoids warm-up artifacts in slices)
        for period in (ema1, ema2):
            col = f'ema_{period}'
            if col not in full_df.columns:
                full_df[col] = full_df['close'].ewm(span=period, adjust=False).mean()

        # Split into folds
        folds = split_walk_forward_folds(
            full_df, n_folds=n_folds, initial_train_pct=initial_train_pct,
        )
        if not folds:
            logger.warning(f"No WF folds for {tf}; skipping config {scenario_name} EMA({ema1},{ema2})")
            continue

        s_params = scenario_params_map.get(scenario_name, {})
        oos_sharpes: List[float] = []
        oos_win_rates: List[float] = []
        fold_details: List[Dict[str, Any]] = []

        for fold_idx, (train_df, test_df) in enumerate(folds):
            # ---- In-Sample (train) ----
            is_result = backtest_fn(
                df=train_df, ema1_period=ema1, ema2_period=ema2,
                sma_long=s_params.get('sma_long'),
                adx_period=s_params.get('adx_period'),
                trix_length=s_params.get('trix_length'),
                trix_signal=s_params.get('trix_signal'),
                sizing_mode=sizing_mode,
                periods_per_year=ppy,
            )

            # ---- Out-of-Sample (test) ----
            oos_result = backtest_fn(
                df=test_df, ema1_period=ema1, ema2_period=ema2,
                sma_long=s_params.get('sma_long'),
                adx_period=s_params.get('adx_period'),
                trix_length=s_params.get('trix_length'),
                trix_signal=s_params.get('trix_signal'),
                sizing_mode=sizing_mode,
                periods_per_year=ppy,
            )

            oos_s = oos_result.get('sharpe_ratio', 0.0)
            oos_wr = oos_result.get('win_rate', 0.0)
            oos_sharpes.append(oos_s)
            oos_win_rates.append(oos_wr)

            fold_details.append({
                'fold': fold_idx + 1,
                'train_bars': len(train_df),
                'test_bars': len(test_df),
                'is_sharpe': is_result.get('sharpe_ratio', 0.0),
                'is_sortino': is_result.get('sortino_ratio', 0.0),
                'is_win_rate': is_result.get('win_rate', 0.0),
                'is_profit': is_result.get('final_wallet', initial_capital) - initial_capital,
                'oos_sharpe': oos_s,
                'oos_sortino': oos_result.get('sortino_ratio', 0.0),
                'oos_win_rate': oos_wr,
                'oos_profit': oos_result.get('final_wallet', initial_capital) - initial_capital,
                'oos_passed': validate_oos_result(oos_s, oos_wr),
            })

        avg_oos_sharpe = float(np.mean(oos_sharpes)) if oos_sharpes else 0.0
        avg_oos_wr = float(np.mean(oos_win_rates)) if oos_win_rates else 0.0
        passed = validate_oos_result(avg_oos_sharpe, avg_oos_wr)
        pass_rate = float(np.mean([f['oos_passed'] for f in fold_details])) if fold_details else 0.0

        wf_results.append({
            'timeframe': tf,
            'ema_periods': (ema1, ema2),
            'scenario': scenario_name,
            'full_sample_sharpe': cfg.get('sharpe_ratio', 0.0),
            'avg_oos_sharpe': round(avg_oos_sharpe, 4),
            'avg_oos_win_rate': round(avg_oos_wr, 2),
            'passed_oos_gates': passed,
            'pass_rate': round(pass_rate, 2),
            'folds': fold_details,
        })

        logger.info(
            f"WF {scenario_name} EMA({ema1},{ema2}) {tf}: "
            f"FS Sharpe={cfg.get('sharpe_ratio', 0):.2f} → "
            f"OOS Sharpe={avg_oos_sharpe:.2f}, OOS WR={avg_oos_wr:.1f}% "
            f"{'PASS' if passed else 'FAIL'}"
        )

    # 2. Select best WF-validated config
    passed_configs = [c for c in wf_results if c['passed_oos_gates']]
    if passed_configs:
        best = max(passed_configs, key=lambda x: x['avg_oos_sharpe'])
    elif wf_results:
        best = max(wf_results, key=lambda x: x['avg_oos_sharpe'])
        logger.warning(
            "⚠ No config passed OOS gates (Sharpe > %.1f & WR > %.0f%%). "
            "Using best OOS Sharpe as fallback.",
            OOS_SHARPE_MIN, OOS_WIN_RATE_MIN,
        )
    else:
        best = None

    return {
        'best_wf_config': best,
        'all_wf_results': wf_results,
        'any_passed': len(passed_configs) > 0,
    }
