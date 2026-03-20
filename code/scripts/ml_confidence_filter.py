"""
ML-08 — ML Confidence Filter Pipeline (offline training)
=========================================================
Trains a LogisticRegression classifier on historical BUY entries
(from trade_journal.jsonl), validates with TimeSeriesSplit, and saves
the model to cache/ml_confidence_model.pkl when AUC > 0.60.

Usage
-----
    .venv\\Scripts\\python.exe code/scripts/ml_confidence_filter.py

Shadow mode: call code/scripts/ml_confidence_filter.py to train/retrain.
The model is loaded lazily in signal_generator.py (shadow mode — never
blocks live signals, only logs predicted probability).

Prerequisites (NON-NEGOTIABLE before production integration):
  - ≥ 200 labelled trades with diverse features (variance > 0)
  - Temporal cross-validation AUC ≥ 0.60
  - Sharpe OOS with filter ≥ Sharpe OOS without filter
"""

from __future__ import annotations

import json
import logging
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ── Path setup ─────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT / "code" / "src"))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("ml_confidence_filter")

_JOURNAL_PATH = _ROOT / "code" / "src" / "logs" / "trade_journal.jsonl"
_MODEL_PATH = _ROOT / "cache" / "ml_confidence_model.pkl"
_MIN_AUC = 0.60
_MIN_TRADES = 200
_PREDICT_THRESHOLD = 0.55  # P(profitable) threshold for live shadow logging


# ── Data loading ────────────────────────────────────────────────
def _load_journal(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Journal not found: {path}")
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    df = pd.DataFrame(records)
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
    return df


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Join BUY→SELL records per pair, compute feature + label."""
    buys = df[df["side"] == "buy"].copy()
    sells = df[df["side"] == "sell"].copy()

    if buys.empty or sells.empty:
        return pd.DataFrame()

    # Sort by timestamp
    buys = buys.sort_values("ts").reset_index(drop=True)
    sells = sells.sort_values("ts").reset_index(drop=True)

    # Join BUY[i] → next SELL[i] per pair (same order as shap_analysis.py)
    pairs_done: list[dict[str, Any]] = []
    for pair in buys["pair"].unique():
        pb = buys[buys["pair"] == pair].reset_index(drop=True)
        ps = sells[sells["pair"] == pair].reset_index(drop=True)
        n = min(len(pb), len(ps))
        for i in range(n):
            b = pb.iloc[i]
            s = ps.iloc[i]
            pnl = s.get("pnl")
            if pnl is None or (isinstance(pnl, float) and np.isnan(pnl)):
                continue
            pairs_done.append({
                "ts": b["ts"],
                "pair": pair,
                "atr": float(b.get("atr", np.nan)),
                "price": float(b.get("price", np.nan)),
                "stop": float(b.get("stop", np.nan)),
                "ema1": float(b.get("ema1", np.nan)),
                "ema2": float(b.get("ema2", np.nan)),
                "equity_before": float(b.get("equity_before", np.nan)),
                "scenario": str(b.get("scenario", "StochRSI")),
                "timeframe": str(b.get("timeframe", "1h")),
                "pnl": float(pnl),
            })

    if not pairs_done:
        return pd.DataFrame()

    result = pd.DataFrame(pairs_done).dropna(subset=["atr", "price", "pnl"])
    result = result[result["price"] > 0]

    # Derived features
    result["atr_pct"] = result["atr"] / result["price"]
    result["stop_dist_pct"] = (result["price"] - result["stop"]) / result["price"]
    result["ema_ratio"] = result["ema1"] / result["ema2"].replace(0, np.nan)

    result["label"] = (result["pnl"] > 0).astype(int)
    return result.sort_values("ts").reset_index(drop=True)


# ── Variance check ──────────────────────────────────────────────
def _check_variance(X: pd.DataFrame) -> tuple[bool, list[str]]:
    """Return (ok, zero_variance_cols)."""
    zero_cols = [c for c in X.columns if X[c].nunique() <= 1]
    return len(zero_cols) == 0, zero_cols


# ── Training pipeline ───────────────────────────────────────────
def _train(df_features: pd.DataFrame, n_splits: int = 5) -> dict[str, Any]:
    """Train LogisticRegression with TimeSeriesSplit; return metrics + model."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler, OneHotEncoder
    from sklearn.compose import ColumnTransformer

    continuous_cols = ["atr_pct", "stop_dist_pct", "ema_ratio", "equity_before"]
    categorical_cols = ["scenario", "timeframe"]
    all_cols = continuous_cols + categorical_cols

    X = df_features[all_cols].copy()
    y = df_features["label"].values

    ok, zero_cols = _check_variance(X[continuous_cols])
    if not ok:
        return {
            "status": "skipped",
            "reason": f"Zero variance in continuous features: {zero_cols}",
            "auc": None,
            "model": None,
        }

    preprocessor = ColumnTransformer([
        ("num", StandardScaler(), continuous_cols),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), categorical_cols),
    ])
    clf = Pipeline([
        ("prep", preprocessor),
        ("lr", LogisticRegression(max_iter=500, random_state=42)),
    ])

    tscv = TimeSeriesSplit(n_splits=n_splits)
    aucs = []
    for train_idx, test_idx in tscv.split(X):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]
        if len(np.unique(y_test)) < 2:
            continue
        try:
            clf.fit(X_train, y_train)
            prob = clf.predict_proba(X_test)[:, 1]
            aucs.append(float(roc_auc_score(y_test, prob)))
        except Exception as e:
            logger.debug("Fold failed: %s", e)

    if not aucs:
        return {"status": "skipped", "reason": "All CV folds failed", "auc": None, "model": None}

    mean_auc = float(np.mean(aucs))

    # Retrain on full data for deployment
    clf.fit(X, y)
    return {
        "status": "trained",
        "auc": mean_auc,
        "auc_per_fold": aucs,
        "n_trades": len(df_features),
        "feature_cols": all_cols,
        "model": clf,
    }


# ── Save / report ───────────────────────────────────────────────
def _save_model(model: Any, feature_cols: list[str], meta: dict[str, Any]) -> None:
    _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "feature_cols": feature_cols,
        "threshold": _PREDICT_THRESHOLD,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
    }
    with open(_MODEL_PATH, "wb") as fh:
        pickle.dump(payload, fh)
    logger.info("Model saved → %s", _MODEL_PATH)


# ── Main ─────────────────────────────────────────────────────────
def main() -> None:
    print("=" * 65)
    print("  ML-08 CONFIDENCE FILTER — Offline Training Pipeline")
    print("=" * 65)

    # 1. Load journal
    logger.info("Loading journal: %s", _JOURNAL_PATH)
    df = _load_journal(_JOURNAL_PATH)
    logger.info("Total records: %d", len(df))

    # 2. Build features
    df_feat = _build_features(df)
    if df_feat.empty:
        logger.warning("No matched BUY→SELL pairs found. Exiting.")
        return

    n_pairs = len(df_feat)
    win_rate = df_feat["label"].mean() * 100
    logger.info("Matched pairs: %d | Win rate: %.1f%%", n_pairs, win_rate)

    print(f"\n── Dataset summary {'─' * 45}")
    print(f"  Matched BUY→SELL pairs : {n_pairs}")
    print(f"  Win rate               : {win_rate:.1f}%")
    print(f"  Date range             : {df_feat['ts'].min().date()} → {df_feat['ts'].max().date()}")
    print(f"  Unique scenarios       : {df_feat['scenario'].nunique()} ({list(df_feat['scenario'].unique())})")
    print(f"  Unique timeframes      : {df_feat['timeframe'].nunique()} ({list(df_feat['timeframe'].unique())})")
    print(f"  ATR pct range          : {df_feat['atr_pct'].min():.4f} → {df_feat['atr_pct'].max():.4f}")

    # 3. Prerequisites check
    if n_pairs < _MIN_TRADES:
        print(f"\n⚠ PREREQUISITE NOT MET: {n_pairs} trades < {_MIN_TRADES} required.")
        print("  Model training skipped. Accumulate more trade data first.")
        return

    # 4. Train
    print(f"\n── Training LogisticRegression (TimeSeriesSplit × 5) {'─' * 10}")
    result = _train(df_feat)

    if result["status"] == "skipped":
        print(f"\n⚠ Training skipped: {result['reason']}")
        print("  Recommendation: wait for WF to explore diverse configs (ML-07 Optuna)")
        return

    auc = result["auc"]
    print(f"\n  Mean AUC (temporal CV) : {auc:.4f}")
    print(f"  Per-fold AUCs          : {[round(a, 3) for a in result['auc_per_fold']]}")
    print(f"  Min AUC threshold      : {_MIN_AUC}")

    if auc < _MIN_AUC:
        print(f"\n⚠ AUC {auc:.3f} < {_MIN_AUC} — model not saved (below threshold).")
        print("  Signal filter NOT activated. Continue accumulating diverse trade data.")
        return

    # 5. Save model
    _save_model(
        model=result["model"],
        feature_cols=result["feature_cols"],
        meta={"n_trades": n_pairs, "win_rate": win_rate, "auc": auc},
    )
    print(f"\n✅ Model saved (AUC={auc:.3f}) → {_MODEL_PATH}")
    print("   Shadow mode: signal_generator.py will log P(profitable) for each BUY signal.")
    print(f"   Threshold for shadow alert: P ≥ {_PREDICT_THRESHOLD}")
    print("\n⚠ DO NOT activate hard filter until:")
    print("  1. Sharpe OOS with filter ≥ Sharpe OOS without filter (3yr backtest)")
    print("  2. Explicit validation reviewed by operator")

    print("=" * 65)


if __name__ == "__main__":
    main()
