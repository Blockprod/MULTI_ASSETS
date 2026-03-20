"""
regime_hmm.py — Hidden Markov Model market regime detection (shadow mode)
=========================================================================

Trains a Gaussian HMM on 4h OHLCV returns from cache and identifies
current market regime states (typically: bull / bear / ranging).

This script is SHADOW MODE only — it logs the detected regime alongside
the existing MTF EMA18/58 filter signal but does NOT modify any
production configuration or signal logic.

Intended use:
  - Run manually or via cron after each daily close
  - Compare HMM regime vs EMA filter agreement/divergence over 60+ days
  - After 60 days of observation, decide whether to inject HMM confidence
    score into signal_generator.py:90 (see ML-08 or manual integration)

Features used for HMM:
  - log_return     : log(close[t] / close[t-1])
  - volume_change  : log(volume[t] / volume[t-1]), bounded
  - range_pct      : (high - low) / close (intraday volatility proxy)

Usage:
    .venv\\Scripts\\python.exe code/scripts/regime_hmm.py [--n-states 3] [--pair SOLUSDC] [--interval 4h]
    .venv\\Scripts\\python.exe code/scripts/regime_hmm.py --all-cache

Outputs:
    - HMM training summary
    - Current regime state + confidence
    - Comparison vs MTF EMA18/58 filter
    - Historical regime sequence (last 30 periods)
    - No production changes

Requirements:
    - hmmlearn (pip install hmmlearn)
    - At least MIN_CANDLES in cache for training
"""

import os
import sys
import argparse
import pickle
import warnings
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_SCRIPT_DIR, "..", "..")
_DEFAULT_CACHE_DIR = os.path.join(_ROOT, "cache")

MIN_CANDLES = 200
N_ITER = 200
RANDOM_STATE = 42
HISTORY_DISPLAY_PERIODS = 30

# EMA periods matching MTF filter defaults from bot_config.py
MTF_EMA_FAST = 18
MTF_EMA_SLOW = 58


def _load_cache_file(cache_dir: str, pair: str, interval: str) -> Optional[pd.DataFrame]:
    """Load OHLCV cache file for (pair, interval). Returns None if not found."""
    for fname in os.listdir(cache_dir):
        if fname.startswith(f"{pair}_{interval}_") and fname.endswith(".pkl"):
            path = os.path.join(cache_dir, fname)
            try:
                with open(path, "rb") as f:
                    df = pickle.load(f)
                if isinstance(df, pd.DataFrame) and not df.empty:
                    required = {"open", "high", "low", "close", "volume"}
                    if required.issubset(df.columns):
                        return df
            except Exception:
                pass
    return None


def _list_cache_pairs(cache_dir: str) -> list[tuple[str, str]]:
    """List all (pair, interval) combinations available in cache."""
    import re
    pairs = []
    for fname in sorted(os.listdir(cache_dir)):
        if fname.endswith(".pkl"):
            m = re.match(r'^([A-Z]+)_([0-9]+[mhd])_', fname)
            if m:
                pairs.append((m.group(1), m.group(2)))
    return pairs


def _build_features(df: pd.DataFrame) -> np.ndarray:
    """Build 3-feature matrix for HMM training."""
    log_return = np.log(df["close"] / df["close"].shift(1)).fillna(0.0)
    vol_change = np.log(
        df["volume"].replace(0, np.nan) / df["volume"].shift(1).replace(0, np.nan)
    ).fillna(0.0).clip(-3.0, 3.0)
    range_pct = ((df["high"] - df["low"]) / df["close"].replace(0, np.nan)).fillna(0.0)

    X = np.column_stack([log_return, vol_change, range_pct])
    # Drop first row (NaN from shift)
    return X[1:]


def _compute_mtf_ema_signal(df: pd.DataFrame) -> pd.Series:
    """Compute EMA18/58 on 4h data (mirrors backtest_runner.py MTF logic).

    Returns boolean Series: True = bullish (EMA_fast > EMA_slow), shifted
    by 1 candle to avoid look-ahead bias.
    """
    ema_fast = df["close"].ewm(span=MTF_EMA_FAST, adjust=False).mean()
    ema_slow = df["close"].ewm(span=MTF_EMA_SLOW, adjust=False).mean()
    signal = (ema_fast > ema_slow).shift(1).fillna(False)
    return signal


def _label_states(means: np.ndarray) -> list[str]:
    """Label HMM states by mean log return: bull > 0, bear < 0, ranging ≈ 0."""
    n = len(means)
    returns = means[:, 0]  # first feature = log_return
    labels = []
    for i in range(n):
        r = returns[i]
        if r > 0.002:
            labels.append("bull")
        elif r < -0.002:
            labels.append("bear")
        else:
            labels.append("ranging")
    return labels


def run_hmm_analysis(
    cache_dir: str,
    pair: str,
    interval: str,
    n_states: int = 3,
) -> None:
    """Train HMM and display regime report for a single pair/interval."""
    from hmmlearn import hmm

    print("=" * 65)
    print(f"  HMM REGIME DETECTION — {pair} / {interval}")
    print(f"  States: {n_states}  |  Features: log_return, vol_change, range_pct")
    print("=" * 65)
    print()

    # --- Load data ---
    df = _load_cache_file(cache_dir, pair, interval)
    if df is None:
        print(f"❌ No cache file found for {pair}/{interval} in {cache_dir}")
        print("   Let the bot populate the cache first.")
        return

    if len(df) < MIN_CANDLES:
        print(f"❌ Only {len(df)} candles (minimum: {MIN_CANDLES} required for HMM).")
        return

    cache_age = (datetime.now(timezone.utc).replace(tzinfo=timezone.utc) - df.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)).total_seconds() / 86400
    print(f"  Data: {len(df)} candles | Last: {df.index[-1]} | Age: {cache_age:.1f}d")
    if cache_age > 2:
        print(f"  ⚠️ Cache is {cache_age:.1f} days old. Run bot to refresh.")
    print()

    # --- Build features ---
    X = _build_features(df)

    # --- Train HMM (Gaussian) ---
    model = hmm.GaussianHMM(
        n_components=n_states,
        covariance_type="diag",
        n_iter=N_ITER,
        random_state=RANDOM_STATE,
        verbose=False,
    )

    try:
        model.fit(X)
    except Exception as e:
        print(f"❌ HMM training failed: {e}")
        return

    if not model.monitor_.converged:
        print("⚠️  HMM did not converge. Try more iterations or fewer states.")

    # --- Decode states ---
    state_seq = model.predict(X)
    state_probs = model.predict_proba(X)

    state_labels = _label_states(model.means_)
    print("── State characterisation ─────────────────────────────")
    for i in range(n_states):
        label = state_labels[i]
        freq = (state_seq == i).mean() * 100
        mean_ret = model.means_[i, 0] * 100
        mean_vol = model.means_[i, 1]
        print(f"  State {i} [{label:>8s}]: freq={freq:.1f}% | mean_log_ret={mean_ret:+.3f}% | vol_change={mean_vol:+.3f}")
    print()

    # --- Current regime ---
    current_state = int(state_seq[-1])
    current_probs = state_probs[-1]
    current_label = state_labels[current_state]
    current_conf = float(current_probs[current_state])

    print("── Current regime ──────────────────────────────────────")
    print(f"  State      : {current_state} [{current_label}]")
    print(f"  Confidence : {current_conf:.1%}")
    for i in range(n_states):
        bar = "█" * int(current_probs[i] * 20)
        print(f"  P(state {i}) : {current_probs[i]:.3f}  {bar}")
    print()

    # HMM confidence as injection signal: P(bull state)
    bull_states = [i for i, lb in enumerate(state_labels) if lb == "bull"]
    hmm_signal_confidence = float(sum(current_probs[i] for i in bull_states))
    hmm_bullish = hmm_signal_confidence >= 0.65

    print(f"  HMM bullish confidence : {hmm_signal_confidence:.3f}")
    print(f"  HMM signal (≥0.65)     : {'✅ BULLISH' if hmm_bullish else '🔴 NOT BULLISH'}")
    print()

    # --- Compare with MTF EMA filter ---
    print("── Comparison vs MTF EMA18/58 filter ──────────────────")
    mtf_signal = _compute_mtf_ema_signal(df)
    # Align with X (drop first row)
    mtf_signal_aligned = mtf_signal.iloc[1:]

    # Last periods comparison
    n_compare = min(HISTORY_DISPLAY_PERIODS, len(state_seq))
    recent_states = state_seq[-n_compare:]
    recent_mtf = mtf_signal_aligned.iloc[-n_compare:].values
    recent_labels = [state_labels[s] for s in recent_states]

    # Agreement rate
    hmm_bullish_hist = np.array([state_labels[s] == "bull" for s in recent_states])
    agreement_rate = float((hmm_bullish_hist == recent_mtf).mean())
    print(f"  Agreement rate (last {n_compare} periods): {agreement_rate:.1%}")

    current_mtf = bool(mtf_signal.iloc[-2])  # shift(1) so use -2
    print(f"  Current MTF EMA signal : {'✅ BULLISH' if current_mtf else '🔴 BEARISH'}")
    print(f"  Current HMM signal     : {'✅ BULLISH' if hmm_bullish else '🔴 NOT BULLISH'}")

    if current_mtf != hmm_bullish:
        print()
        print("  ⚡ DIVERGENCE: HMM and MTF EMA disagree on current regime!")
        print("     This is the kind of signal worth logging for 60-day validation.")
    else:
        print()
        print("  ✓  HMM and MTF EMA agree on current regime.")
    print()

    # --- Historical regime summary (last N periods) ---
    print(f"── Historical regimes (last {n_compare} periods) ───────────────")
    print(f"  {'Period':<6}  {'HMM':<10}  {'MTF EMA':<10}  {'Agree'}")
    print("  " + "-" * 40)
    for i in range(n_compare):
        idx = -(n_compare - i)
        ts = df.index[idx + 1] if abs(idx) < len(df) else "?"
        hmm_lbl = recent_labels[i]
        mtf_lbl = "bullish" if recent_mtf[i] else "bearish"
        hmm_is_bull = hmm_lbl == "bull"
        agree = "✓" if (hmm_is_bull == recent_mtf[i]) else "✗"
        print(f"  {str(ts)[:16]:<17}  {hmm_lbl:<10}  {mtf_lbl:<10}  {agree}")
    print()

    print("=" * 65)
    print("  SHADOW MODE: HMM regime logged — no production changes.")
    print("  Observe agreement vs MTF EMA for ≥ 60 days before integration.")
    print("  Injection point (when ready): signal_generator.py:90 (mtf_bullish)")
    print("=" * 65)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HMM market regime detection (shadow mode)")
    parser.add_argument("--pair", default="SOLUSDC", help="Pair symbol (e.g. SOLUSDC)")
    parser.add_argument("--interval", default="4h", help="Candle interval (e.g. 4h)")
    parser.add_argument("--n-states", type=int, default=3, choices=[2, 3, 4], help="Number of HMM states")
    parser.add_argument("--cache-dir", default=_DEFAULT_CACHE_DIR, help="Path to cache directory")
    parser.add_argument(
        "--all-cache",
        action="store_true",
        help="Run analysis on all pairs found in cache directory",
    )
    args = parser.parse_args()

    cache_dir = os.path.abspath(args.cache_dir)

    if not os.path.isdir(cache_dir):
        print(f"❌ Cache directory not found: {cache_dir}")
        sys.exit(1)

    if args.all_cache:
        pairs = _list_cache_pairs(cache_dir)
        if not pairs:
            print("❌ No cache files found.")
            sys.exit(1)
        for pair, interval in pairs:
            run_hmm_analysis(cache_dir, pair, interval, args.n_states)
            print()
    else:
        run_hmm_analysis(cache_dir, args.pair, args.interval, args.n_states)
