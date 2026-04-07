#!/usr/bin/env python
"""
bench_optimization.py — Script A/B test pour optimiser les paramètres backtest.

Usage:
    python tests/bench_optimization.py [--param <name>] [--values <v1,v2,...>]

Exemples:
    python tests/bench_optimization.py --param stoch_rsi_sell_exit --values 0.2,0.3,0.4,0.5,0.6,0.7
    python tests/bench_optimization.py --param risk_per_trade --values 0.03,0.05,0.07,0.10
    python tests/bench_optimization.py --param partial_threshold_1 --values 0.02,0.03,0.04,0.05

Créé dans le cadre du plan d'optimisation (D-1 / C-1).
"""

import argparse
import os
import sys
import time

# --- Path setup ---
_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))
_BIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'code', 'bin'))
sys.path.insert(0, _SRC_DIR)
sys.path.insert(0, _BIN_DIR)

import numpy as np
import pandas as pd
import pickle
from ta.volatility import AverageTrueRange
from ta.momentum import RSIIndicator

from bot_config import config
from backtest_runner import backtest_from_dataframe, CYTHON_BACKTEST_AVAILABLE
from indicators_engine import compute_stochrsi

# Default grids per parameter
DEFAULT_GRIDS = {
    'stoch_rsi_sell_exit': [0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
    'risk_per_trade': [0.03, 0.05, 0.07, 0.10, 0.15],
    'partial_threshold_1': [0.02, 0.03, 0.04, 0.05],
    'partial_threshold_2': [0.04, 0.06, 0.08, 0.10],
    'partial_pct_1': [0.30, 0.40, 0.50, 0.60],
    'partial_pct_2': [0.20, 0.25, 0.30, 0.40],
    'atr_stop_multiplier': [2.0, 2.5, 3.0, 3.5, 4.0],
    'atr_multiplier': [4.0, 4.5, 5.0, 5.5, 6.0, 7.0],
    'stoch_rsi_buy_max': [0.6, 0.7, 0.8, 0.9],
}

# Configs to benchmark: (timeframe, ema1, ema2, scenario_name, scenario_params)
BENCHMARK_CONFIGS = [
    ('1h', 20, 40, 'StochRSI_TRIX', {'trix_length': 9, 'trix_signal': 21}),
    ('1h', 18, 36, 'StochRSI_TRIX', {'trix_length': 9, 'trix_signal': 21}),
    ('1h', 20, 40, 'StochRSI', {}),
    ('1h', 18, 36, 'StochRSI', {}),
    ('1h', 26, 50, 'StochRSI_TRIX', {'trix_length': 9, 'trix_signal': 21}),
]


def load_cached_data(pair: str, timeframe: str) -> pd.DataFrame:
    """Load data from cache without needing a Binance client."""
    # Try code/src/cache first, then root cache/
    for cache_dir in [
        os.path.join(_SRC_DIR, 'cache'),
        os.path.join(os.path.dirname(__file__), '..', 'cache'),
    ]:
        if not os.path.exists(cache_dir):
            continue
        for f in os.listdir(cache_dir):
            if f.startswith(f"{pair}_{timeframe}_") and f.endswith('.pkl'):
                cache_file = os.path.join(cache_dir, f)
                try:
                    with open(cache_file, 'rb') as fh:
                        df = pickle.load(fh)
                    if df is not None and not df.empty:
                        return df
                except Exception:
                    continue
    raise RuntimeError(
        f"No cached data for {pair}_{timeframe}. Run the bot once first to populate cache."
    )


def prepare_df(pair: str, timeframe: str) -> pd.DataFrame:
    """Load and prepare a DataFrame with indicators."""
    df = load_cached_data(pair, timeframe)

    # Trim to backtest_days
    if len(df) > 0:
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=config.backtest_days)
        df = df[df.index >= cutoff].copy()

    # EMA basiques
    for period in [12, 14, 18, 20, 22, 25, 26, 30, 36, 40, 44, 45, 50, 60, 176]:
        col = f'ema_{period}'
        if col not in df.columns:
            df[col] = df['close'].ewm(span=period, adjust=False).mean()

    # Indicateurs
    if 'rsi' not in df.columns:
        df['rsi'] = RSIIndicator(df['close'], window=14).rsi()
    if 'atr' not in df.columns:
        df['atr'] = AverageTrueRange(
            high=df['high'], low=df['low'], close=df['close'],
            window=config.atr_period,
        ).average_true_range()
    if 'stoch_rsi' not in df.columns:
        df['stoch_rsi'] = compute_stochrsi(df['rsi'], period=14)

    df.dropna(subset=['close', 'atr', 'stoch_rsi'], inplace=True)
    return df


def run_benchmark(param_name: str, values: list, pair: str = 'ONDOUSDC'):  # MI-01
    """Run backtests for each value of the parameter and print comparison."""
    print(f"\n{'='*80}")
    print(f"  BENCHMARK: {param_name}")
    print(f"  Grille: {values}")
    print(f"  Paire: {pair} | Cython: {CYTHON_BACKTEST_AVAILABLE}")
    print(f"{'='*80}\n")

    # Save original value
    original_value = getattr(config, param_name)
    print(f"  Valeur actuelle: {param_name} = {original_value}\n")

    # Load data for each timeframe needed
    timeframes_needed = set(c[0] for c in BENCHMARK_CONFIGS)
    dataframes = {}
    for tf in timeframes_needed:
        print(f"  Chargement données {pair} {tf}...", end=' ')
        dataframes[tf] = prepare_df(pair, tf)
        print(f"OK ({len(dataframes[tf])} candles)")

    # Results storage
    results = []  # list of dicts: {value, config_name, pnl, wr, dd, calmar, wallet}

    for val in values:
        setattr(config, param_name, val)
        config_results = []

        t0 = time.perf_counter()
        for tf, ema1, ema2, scenario_name, params in BENCHMARK_CONFIGS:
            df = dataframes[tf]
            result = backtest_from_dataframe(
                df=df,
                ema1_period=ema1,
                ema2_period=ema2,
                sma_long=params.get('sma_long'),
                adx_period=params.get('adx_period'),
                trix_length=params.get('trix_length'),
                trix_signal=params.get('trix_signal'),
                sizing_mode='risk',
                partial_enabled=True,
            )
            pnl = result['final_wallet'] - config.initial_wallet
            wr = result['win_rate']
            dd = result['max_drawdown'] * 100
            calmar = result.get('calmar_ratio', 0.0)
            config_results.append({
                'value': val,
                'config': f"{tf} {ema1}/{ema2} {scenario_name}",
                'pnl': pnl,
                'wr': wr,
                'dd': dd,
                'calmar': calmar,
                'wallet': result['final_wallet'],
            })

        elapsed = time.perf_counter() - t0

        # Average across configs
        avg_pnl = np.mean([r['pnl'] for r in config_results])
        avg_wr = np.mean([r['wr'] for r in config_results])
        avg_dd = np.mean([r['dd'] for r in config_results])
        avg_calmar = np.mean([r['calmar'] for r in config_results])
        best_pnl = max(r['pnl'] for r in config_results)
        best_config = max(config_results, key=lambda r: r['pnl'])['config']

        results.append({
            'value': val,
            'avg_pnl': avg_pnl,
            'avg_wr': avg_wr,
            'avg_dd': avg_dd,
            'avg_calmar': avg_calmar,
            'best_pnl': best_pnl,
            'best_config': best_config,
            'elapsed': elapsed,
            'details': config_results,
        })
        print(f"  {param_name}={val:<6} | Avg PnL: ${avg_pnl:>10,.2f} | "
              f"Avg WR: {avg_wr:>5.1f}% | Avg DD: {avg_dd:>5.1f}% | "
              f"Calmar: {avg_calmar:>5.2f} | {elapsed:.1f}s")

    # Restore original value
    setattr(config, param_name, original_value)

    # === RESULTS TABLE ===
    print(f"\n{'='*80}")
    print(f"  RÉSULTATS COMPARATIFS — {param_name}")
    print(f"{'='*80}")
    print(f"\n  {'Value':<10} {'Avg PnL':>12} {'Avg WR':>8} {'Avg DD':>8} "
          f"{'Calmar':>8} {'Best PnL':>12} {'Best Config':<25}")
    print(f"  {'-'*10} {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*12} {'-'*25}")

    for r in results:
        marker = ' ★' if r == max(results, key=lambda x: x['avg_calmar']) else '  '
        print(f"{marker}{r['value']:<9} ${r['avg_pnl']:>10,.2f} {r['avg_wr']:>7.1f}% "
              f"{r['avg_dd']:>7.1f}% {r['avg_calmar']:>7.2f} "
              f"${r['best_pnl']:>10,.2f} {r['best_config']:<25}")

    # Winner
    winner = max(results, key=lambda x: x['avg_calmar'])
    baseline = next((r for r in results if r['value'] == original_value), results[0])

    print(f"\n  ★ GAGNANT (Calmar max): {param_name} = {winner['value']}")
    print(f"    Avg PnL: ${winner['avg_pnl']:>,.2f} (baseline: ${baseline['avg_pnl']:>,.2f})")
    print(f"    Avg WR:  {winner['avg_wr']:.1f}% (baseline: {baseline['avg_wr']:.1f}%)")
    print(f"    Avg DD:  {winner['avg_dd']:.1f}% (baseline: {baseline['avg_dd']:.1f}%)")
    print(f"    Calmar:  {winner['avg_calmar']:.2f} (baseline: {baseline['avg_calmar']:.2f})")

    # Validation rule: must improve at least 2 of 3 metrics
    improvements = 0
    if winner['avg_pnl'] > baseline['avg_pnl']:
        improvements += 1
    if winner['avg_wr'] > baseline['avg_wr']:
        improvements += 1
    if winner['avg_dd'] < baseline['avg_dd']:
        improvements += 1

    if improvements >= 2:
        print(f"\n  ✅ VALIDATION: {improvements}/3 métriques améliorées — "
              f"RECOMMANDATION: {param_name} = {winner['value']}")
    else:
        print(f"\n  ⚠️ VALIDATION: seulement {improvements}/3 métriques améliorées — "
              f"conserver {param_name} = {original_value}")

    # Detail per config for the winner
    print(f"\n  Détail du gagnant ({param_name}={winner['value']}):")
    for d in winner['details']:
        print(f"    {d['config']:<30} PnL: ${d['pnl']:>10,.2f} | "
              f"WR: {d['wr']:>5.1f}% | DD: {d['dd']:>5.1f}%")

    print()
    return winner['value'], results


def main():
    parser = argparse.ArgumentParser(description='Benchmark optimization A/B test')
    parser.add_argument('--param', type=str, default='stoch_rsi_sell_exit',
                        help='Parameter name to optimize')
    parser.add_argument('--values', type=str, default=None,
                        help='Comma-separated values to test (e.g., 0.2,0.4,0.5)')
    parser.add_argument('--pair', type=str, default='ONDOUSDC',  # MI-07
                        help='Trading pair (USDC quote uniquement)')
    args = parser.parse_args()

    if args.values:
        values = [float(v) for v in args.values.split(',')]
    elif args.param in DEFAULT_GRIDS:
        values = DEFAULT_GRIDS[args.param]
    else:
        print(f"No default grid for '{args.param}'. Use --values.")
        sys.exit(1)

    if not hasattr(config, args.param):
        print(f"Parameter '{args.param}' not found in bot_config.Config")
        sys.exit(1)

    run_benchmark(args.param, values, pair=args.pair)


if __name__ == '__main__':
    main()
