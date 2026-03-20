"""
pair_scorer.py — Momentum & volume ranking for crypto pairs (shadow mode)
=========================================================================

Reads cached OHLCV pickle files from the cache/ directory and computes:
  - Momentum rank (20-day price return)
  - Volume rank (20-day average volume in USDC)
  - Composite score = 0.6 × momentum_rank + 0.4 × volume_rank

This script is SHADOW MODE only — it logs and displays rankings without
modifying the crypto_pairs list in MULTI_SYMBOLS.py.

Use this output to identify:
  - Pairs in sideways/declining momentum (low score → fewer entries expected)
  - Pairs with increasing momentum (high score → bot naturally favours them)

Usage:
    .venv\\Scripts\\python.exe code/scripts/pair_scorer.py [--cache-dir CACHE_DIR]

Outputs:
    - Ranked table printed to stdout
    - No changes to any production configuration

Note: Results are only as current as the most recent cache files.
      Cache TTL is 30 days (bot_config.cache_manager).
      Run during active bot session for most recent data.
"""

import os
import argparse
import pickle
import re
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_SCRIPT_DIR, "..", "..")
_DEFAULT_CACHE_DIR = os.path.join(_ROOT, "cache")

# Configuration
MOMENTUM_DAYS = 20
VOLUME_DAYS = 20
MOMENTUM_WEIGHT = 0.6
VOLUME_WEIGHT = 0.4
MIN_CANDLES = 40  # minimum candles required to compute reliable metrics


def _parse_pair_from_filename(filename: str) -> tuple[str, str]:
    """Extract (pair_symbol, interval) from cache filename."""
    # Pattern: SYMBOL_INTERVAL_*.pkl  e.g. BTCUSDC_1h_01_January_2023.pkl
    m = re.match(r'^([A-Z]+)_([0-9]+[mhd])_', filename)
    if m:
        return m.group(1), m.group(2)
    return filename.replace(".pkl", ""), "unknown"


def _load_cache_file(path: str) -> Optional[pd.DataFrame]:
    """Load a pickle cache file and return a clean DataFrame."""
    try:
        with open(path, "rb") as f:
            df = pickle.load(f)
        if not isinstance(df, pd.DataFrame) or df.empty:
            return None
        required_cols = {"close", "volume"}
        if not required_cols.issubset(df.columns):
            return None
        return df
    except Exception:
        return None


def _compute_metrics(df: pd.DataFrame, interval: str) -> dict:
    """Compute momentum and volume metrics for a single pair."""
    # Determine candles per day based on interval
    candles_per_day = {
        "1m": 1440, "5m": 288, "15m": 96, "30m": 48,
        "1h": 24, "2h": 12, "4h": 6, "6h": 4, "8h": 3, "12h": 2, "1d": 1,
    }.get(interval, 24)

    momentum_candles = MOMENTUM_DAYS * candles_per_day
    volume_candles = VOLUME_DAYS * candles_per_day

    if len(df) < MIN_CANDLES:
        return {}

    close = df["close"]
    volume = df["volume"]

    # Use only the recent window
    n_momentum = min(momentum_candles, len(df) - 1)
    n_volume = min(volume_candles, len(df))

    price_start = float(close.iloc[-(n_momentum + 1)])
    price_end = float(close.iloc[-1])
    momentum = (price_end - price_start) / price_start if price_start > 0 else 0.0

    avg_volume = float(volume.iloc[-n_volume:].mean())
    avg_volume_usdc = avg_volume * price_end  # approximate notional

    # Cache freshness
    last_ts = df.index[-1]
    if hasattr(last_ts, "to_pydatetime"):
        last_ts = last_ts.to_pydatetime()
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - last_ts).total_seconds() / 86400

    return {
        "momentum_20d": momentum,
        "avg_volume_usdc": avg_volume_usdc,
        "last_price": price_end,
        "cache_age_days": age_days,
        "candles": len(df),
    }


def score_pairs(cache_dir: str) -> None:
    """Load all cache files, compute metrics, rank, and print report."""
    cache_dir = os.path.abspath(cache_dir)

    if not os.path.isdir(cache_dir):
        print(f"❌ Cache directory not found: {cache_dir}")
        return

    pkl_files = sorted(f for f in os.listdir(cache_dir) if f.endswith(".pkl"))

    if not pkl_files:
        print("❌ No cache files found. Let the bot run a full cycle first.")
        return

    print("=" * 70)
    print("  PAIR MOMENTUM & VOLUME SCORING REPORT (shadow mode)")
    print(f"  Cache dir : {cache_dir}")
    print(f"  Date      : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 70)
    print()

    results = []

    for fname in pkl_files:
        path = os.path.join(cache_dir, fname)
        pair, interval = _parse_pair_from_filename(fname)
        df = _load_cache_file(path)
        if df is None:
            print(f"  ⚠️  Skipped (unreadable): {fname}")
            continue

        metrics = _compute_metrics(df, interval)
        if not metrics:
            print(f"  ⚠️  Skipped (insufficient data): {fname}")
            continue

        metrics["pair"] = pair
        metrics["interval"] = interval
        metrics["filename"] = fname
        results.append(metrics)

    if not results:
        print("❌ No valid cache data found.")
        return

    df_results = pd.DataFrame(results)

    # --- Rank metrics (percentile rank within population) ---
    if len(df_results) > 1:
        df_results["momentum_rank"] = df_results["momentum_20d"].rank(pct=True)
        df_results["volume_rank"] = df_results["avg_volume_usdc"].rank(pct=True)
    else:
        # Single pair — rank is trivially 1.0 (no comparison possible)
        df_results["momentum_rank"] = 1.0
        df_results["volume_rank"] = 1.0

    df_results["composite_score"] = (
        MOMENTUM_WEIGHT * df_results["momentum_rank"]
        + VOLUME_WEIGHT * df_results["volume_rank"]
    )

    df_results = df_results.sort_values("composite_score", ascending=False)

    # --- Display table ---
    print(f"{'Rank':<5} {'Pair':<12} {'Interval':<9} {'Momentum 20d':>12} {'Avg Vol (USDC)':>15} {'Score':>7} {'Cache Age':>10}")
    print("-" * 70)

    for rank, (_, row) in enumerate(df_results.iterrows(), 1):
        momentum_pct = row["momentum_20d"] * 100
        momentum_str = f"{momentum_pct:+.1f}%"
        vol_str = f"{row['avg_volume_usdc']:,.0f}"
        score_str = f"{row['composite_score']:.3f}"
        age_str = f"{row['cache_age_days']:.1f}d"

        # Visual indicator
        if row["composite_score"] >= 0.7:
            indicator = "✅"
        elif row["composite_score"] >= 0.4:
            indicator = "⚠️ "
        else:
            indicator = "🔴"

        print(
            f"{rank:<5} {row['pair']:<12} {row['interval']:<9} "
            f"{momentum_str:>12} {vol_str:>15} {score_str:>7} {age_str:>10}  {indicator}"
        )

    print()

    # --- Summary ---
    n = len(df_results)
    low_score_pairs = df_results[df_results["composite_score"] < 0.4]["pair"].tolist()
    high_score_pairs = df_results[df_results["composite_score"] >= 0.7]["pair"].tolist()

    print("── Summary ─────────────────────────────────────────────────────")
    print(f"  Total pairs analysed : {n}")

    if high_score_pairs:
        print(f"  ✅ High momentum (score ≥ 0.7): {high_score_pairs}")
    if low_score_pairs:
        print(f"  🔴 Low momentum (score < 0.4) : {low_score_pairs}")
        print()
        print("  Recommendation: pairs with score < 0.4 are likely in sideways")
        print("  or declining momentum. Consider monitoring their WF Sharpe OOS")
        print("  before adding more pairs of the same type.")

    if n < 3:
        print()
        print("  ℹ️  Only {n} pair(s) in cache. Ranking is informative only.")
        print("     Add more pairs to crypto_pairs in MULTI_SYMBOLS.py:1298")
        print("     and let the bot run a full cycle to populate the cache.")

    # --- Stale cache warning ---
    stale = df_results[df_results["cache_age_days"] > 1.0]
    if not stale.empty:
        print()
        print(f"  ⚠️  {len(stale)} file(s) with cache > 1 day old.")
        print("     Run the bot to refresh or use force_refresh=True in fetch.")

    print()
    print("=" * 70)
    print("  ⚠️  SHADOW MODE: this report does not modify crypto_pairs.")
    print("     All production configuration remains unchanged.")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pair momentum & volume scoring from cache")
    parser.add_argument(
        "--cache-dir",
        default=_DEFAULT_CACHE_DIR,
        help=f"Path to cache directory (default: {_DEFAULT_CACHE_DIR})",
    )
    args = parser.parse_args()

    score_pairs(args.cache_dir)
