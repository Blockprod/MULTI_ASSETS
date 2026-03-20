"""
shap_analysis.py — SHAP feature importance from trade_journal.jsonl
====================================================================

Trains an XGBoost classifier on features extracted from the trade journal
and computes SHAP values to identify which indicators actually contribute
to trade profitability.

Features used (all available directly in the journal):
  - ema1, ema2          : EMA periods selected by WF
  - ema_ratio           : ema1/ema2 (spread proxy)
  - atr                 : ATR value at entry
  - stop_distance       : entry_price - stop_price (absolute distance)
  - stop_distance_pct   : stop_distance / entry_price
  - risk_reward_implied : expected reward / stop_distance
  - fee_pct             : fee / (qty * price)
  - scenario_*          : one-hot encoded scenario (StochRSI, SMA, ADX, TRIX)
  - timeframe_*         : one-hot encoded timeframe

Label: 1 if pnl > 0 (profitable trade), 0 otherwise.

Uses TimeSeriesSplit for temporal cross-validation (no look-ahead bias).

Usage:
    .venv\\Scripts\\python.exe code/scripts/shap_analysis.py [--no-plot]

Outputs:
    - Text summary of SHAP feature importances
    - Bar chart (matplotlib) if not --no-plot
    - Recommendations for WF scenario simplification

Requirements:
    - trade_journal.jsonl with >= 200 closed trades (side='sell' with pnl)
    - xgboost, shap (pip install xgboost shap)
"""

import os
import sys
import argparse
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_SCRIPT_DIR, "..", "src")
_LOGS_DIR = os.path.join(_SRC_DIR, "logs")

sys.path.insert(0, _SRC_DIR)

from trade_journal import read_journal  # noqa: E402

MIN_TRADES = 100  # minimum matched BUY→SELL pairs (200 recommended for stability)
CV_SPLITS = 5


def _build_features(records: list) -> pd.DataFrame:
    """Extract and engineer features by joining BUY + SELL records per pair.

    BUY records carry entry features (ema1, ema2, atr, stop, price).
    SELL records carry the outcome (pnl). We match each SELL to the most
    recent BUY of the same pair (sorted by timestamp) to build a labelled
    dataset with entry conditions + actual outcome.
    """
    # Sort all records by timestamp (ISO 8601 string sort is fine here)
    sorted_records = sorted(records, key=lambda r: r.get("ts", ""))

    # Track the last BUY entry per pair
    last_buy: dict = {}
    rows = []

    for r in sorted_records:
        side = r.get("side", "")
        pair = r.get("pair", "")

        if side == "buy":
            last_buy[pair] = r

        elif side == "sell" and r.get("pnl") is not None and pair in last_buy:
            buy = last_buy[pair]
            sell = r

            try:
                ema1 = float(buy.get("ema1") or 0)
                ema2 = float(buy.get("ema2") or 0)
                atr = float(buy.get("atr") or 0)
                stop = float(buy.get("stop") or 0)
                entry_price = float(buy.get("price") or 0)
                qty = float(buy.get("qty") or 0)
                fee = float(buy.get("fee") or 0)
                pnl = float(sell["pnl"])
                scenario = str(buy.get("scenario") or "unknown")
                timeframe = str(buy.get("timeframe") or "unknown")
            except (ValueError, TypeError):
                continue

            if entry_price <= 0:
                continue

            stop_distance = abs(entry_price - stop) if stop > 0 and atr > 0 else (atr * 3.0 if atr > 0 else 0.0)
            stop_distance_pct = stop_distance / entry_price if entry_price > 0 else 0.0
            ema_ratio = ema1 / ema2 if ema2 > 0 else 0.0
            fee_pct = fee / (qty * entry_price) if qty > 0 and entry_price > 0 else 0.0

            rows.append({
                "ema1": ema1,
                "ema2": ema2,
                "ema_ratio": ema_ratio,
                "atr_normalised": atr / entry_price if entry_price > 0 and atr > 0 else 0.0,
                "stop_distance_pct": stop_distance_pct,
                "fee_pct": fee_pct,
                "scenario": scenario,
                "timeframe": timeframe,
                "label": 1 if pnl > 0 else 0,
            })

            # Remove the buy so the next sell for this pair won't re-use it
            del last_buy[pair]

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # One-hot encode scenario and timeframe
    df = pd.get_dummies(df, columns=["scenario", "timeframe"], prefix=["sc", "tf"])

    return df


def run_shap_analysis(logs_dir: str, no_plot: bool = False) -> None:
    """Main analysis pipeline."""
    import xgboost as xgb
    import shap
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score

    print("=" * 65)
    print("  SHAP FEATURE IMPORTANCE ANALYSIS")
    print("  Journal:", os.path.join(logs_dir, "trade_journal.jsonl"))
    print("=" * 65)
    print()

    # --- Load data ---
    records = read_journal(logs_dir)
    if not records:
        print("❌ Journal is empty. Run the bot to accumulate trades.")
        return

    df = _build_features(records)
    n_sells = len(df)

    if n_sells < MIN_TRADES:
        print(f"❌ Only {n_sells} matched BUY→SELL pairs (minimum: {MIN_TRADES}).")
        print("   Accumulate more trades before running SHAP analysis.")
        return

    if n_sells < 200:
        print(f"⚠️  {n_sells} matched BUY→SELL pairs (< 200 optimal).")
        print("   Results are indicative — validate with more trades.\n")
    else:
        print(f"✅ {n_sells} matched BUY→SELL pairs loaded.\n")

    # --- Prepare X, y ---
    y = df["label"].values
    X = df.drop(columns=["label"])

    feature_names = list(X.columns)
    n_pos = int(y.sum())
    n_neg = int((1 - y).sum())
    win_rate = n_pos / len(y) * 100

    print("── Dataset ─────────────────────────────────────────────")
    print(f"  Samples    : {len(y)}")
    print(f"  Wins       : {n_pos} ({win_rate:.1f}%)")
    print(f"  Losses     : {n_neg} ({100 - win_rate:.1f}%)")
    print(f"  Features   : {len(feature_names)}")
    print()

    # --- Check feature variance ---
    low_var_features = []
    zero_var_features = []
    for col in X.columns:
        std = float(X[col].std())
        if std == 0.0:
            zero_var_features.append(col)
        elif std < 1e-6:
            low_var_features.append(col)

    if zero_var_features:
        print("⚠️  Zero-variance features detected (constant across all trades):")
        for f in zero_var_features:
            print(f"     - {f} = {X[f].iloc[0]:.6g}")
        print()

    # Bail out if scenario/timeframe categoricals are all constant (no diversity)
    zero_var_categoricals = [f for f in zero_var_features if f.startswith(("sc_", "tf_"))]
    if len(zero_var_categoricals) > 0 and len([f for f in feature_names if f.startswith(("sc_", "tf_"))]) <= len(zero_var_categoricals) + 1:
        print("❌ SHAP analysis not possible: nearly all features are constant.")
        print()
        print("── Diagnosis ───────────────────────────────────────────")
        # Report scenario diversity
        sc_cols = [c for c in feature_names if c.startswith("sc_")]
        tf_cols = [c for c in feature_names if c.startswith("tf_")]
        active_sc = [c.replace("sc_", "") for c in sc_cols if float(X[c].max()) > 0]
        active_tf = [c.replace("tf_", "") for c in tf_cols if float(X[c].max()) > 0]
        print(f"  Scenarios in trade history : {active_sc}")
        print(f"  Timeframes in trade history: {active_tf}")
        print()
        print("  The walk-forward is consistently selecting the same scenario")
        print("  and parameters. SHAP requires variation across multiple")
        print("  scenarios/configs to measure feature importance.")
        print()
        print("  Recommendations:")
        print("  1. Verify WF_SCENARIOS in MULTI_SYMBOLS.py:358-363 includes")
        print("     StochRSI_SMA, StochRSI_ADX, StochRSI_TRIX (not just StochRSI).")
        print("  2. Ensure the walk-forward is running on multiple timeframes.")
        print("  3. Re-run this script after accumulating trades from 2+ scenarios.")
        print()
        print("  💡 Key insight: WF always converges to the same config —")
        print("     either the strategy is very robust (good) or the WF search")
        print("     space is too narrow (consider Optuna via ML-07).")
        print("=" * 65)
        return

    # Handle class imbalance
    scale_pos_weight = n_neg / n_pos if n_pos > 0 else 1.0

    # --- Train XGBoost with temporal CV ---
    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=scale_pos_weight,
        use_label_encoder=False,
        eval_metric="logloss",
        random_state=42,
        n_jobs=-1,
        verbosity=0,
    )

    tscv = TimeSeriesSplit(n_splits=CV_SPLITS)
    cv_scores = cross_val_score(model, X, y, cv=tscv, scoring="roc_auc")

    print(f"── Temporal cross-validation (TimeSeriesSplit, {CV_SPLITS} folds) ──")
    for i, s in enumerate(cv_scores, 1):
        bar = "█" * int(s * 20)
        print(f"  Fold {i}: AUC = {s:.3f}  {bar}")
    print(f"  Mean AUC : {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")
    if cv_scores.mean() < 0.55:
        print("  ⚠️  Mean AUC < 0.55 — model barely above random.")
        print("     Features may not predict profitability reliably.")
    elif cv_scores.mean() < 0.65:
        print("  ⚠️  Mean AUC 0.55-0.65 — weak but non-trivial signal.")
    else:
        print("  ✅ Mean AUC >= 0.65 — features carry useful predictive signal.")
    print()

    # --- Train on full dataset for SHAP ---
    model.fit(X, y)

    # --- SHAP values ---
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)

    # Mean absolute SHAP per feature
    mean_abs_shap = np.abs(shap_values).mean(axis=0)
    importance = pd.Series(mean_abs_shap, index=feature_names).dropna().sort_values(ascending=False)

    if importance.empty:
        print("❌ All SHAP values are NaN — model produced constant predictions.")
        print("   This confirms features lack the variance needed for SHAP analysis.")
        print("   Re-run after accumulating trades from multiple WF scenarios.")
        print("=" * 65)
        return

    print("── SHAP Feature Importance (mean |SHAP value|) ─────────")
    max_importance = importance.iloc[0] if len(importance) > 0 else 1.0
    for feat, val in importance.items():
        bar_len = int((val / max_importance) * 30)
        bar = "█" * bar_len
        print(f"  {feat:<30s} {val:.4f}  {bar}")
    print()

    # --- Recommendations ---
    print("── Recommendations ─────────────────────────────────────")

    # Identify which WF scenarios appear in top features
    scenario_features = {k: v for k, v in importance.items() if str(k).startswith("sc_")}
    tf_features = {k: v for k, v in importance.items() if str(k).startswith("tf_")}

    if scenario_features:
        print("  WF Scenario importance ranking:")
        for sc, val in sorted(scenario_features.items(), key=lambda x: -x[1]):
            label = str(sc).replace("sc_", "")
            indicator = "✅" if val > 0.001 else "⚠️ low"
            print(f"    {indicator}  {label:<35s} SHAP={val:.4f}")
        min_sc = min(scenario_features.values())
        max_sc = max(scenario_features.values())
        if max_sc > 0 and min_sc / max_sc < 0.1:
            worst = min(scenario_features, key=lambda k: scenario_features[k]).replace("sc_", "")
            print(f"\n  💡 Scenario '{worst}' contributes < 10% of top scenario's SHAP.")
            print("     Consider removing it from WF_SCENARIOS in MULTI_SYMBOLS.py:358-363")
            print("     to reduce backtest compute time.")

    if tf_features:
        print()
        print("  Timeframe importance ranking:")
        for tf, val in sorted(tf_features.items(), key=lambda x: -x[1]):
            label = str(tf).replace("tf_", "")
            indicator = "✅" if val > 0.001 else "⚠️ low"
            print(f"    {indicator}  {label:<35s} SHAP={val:.4f}")

    # Check core features
    core_features = ["atr_normalised", "stop_distance_pct", "ema_ratio", "fee_pct"]
    print()
    print("  Core feature signal:")
    for feat in core_features:
        if feat in importance:
            val = importance[feat]
            indicator = "✅" if val > 0.001 else "⚠️ negligible"
            print(f"    {indicator}  {feat:<30s} SHAP={val:.4f}")

    print()
    print("=" * 65)
    print("  NOTE: SHAP measures model importance, not trading causality.")
    print("  Low SHAP does not guarantee a feature is useless in trading.")
    print("  Use these results as input for WF scenario review, not as")
    print("  automatic configuration changes.")
    print("=" * 65)

    # --- Optional plot ---
    if not no_plot:
        try:
            import matplotlib  # type: ignore[import-untyped]
            matplotlib.use("Agg")  # Non-interactive backend
            import matplotlib.pyplot as plt  # type: ignore[import-untyped]

            fig, ax = plt.subplots(figsize=(10, 6))
            importance.head(15).sort_values().plot(
                kind="barh", ax=ax, color="steelblue"
            )
            ax.set_title("SHAP Feature Importance — MULTI_ASSETS Trade Journal")
            ax.set_xlabel("Mean |SHAP value|")
            ax.axvline(x=0.001, color="red", linestyle="--", alpha=0.5, label="1e-3 threshold")
            ax.legend()
            plt.tight_layout()

            plot_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "shap_importance.png"
            )
            plt.savefig(plot_path, dpi=150, bbox_inches="tight")
            print(f"\n📊 Plot saved to: {plot_path}")
        except Exception as e:
            print(f"\n⚠️  Could not save plot: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SHAP feature importance from trade journal")
    parser.add_argument("logs_dir", nargs="?", default=_LOGS_DIR, help="Path to logs directory")
    parser.add_argument("--no-plot", action="store_true", help="Skip matplotlib plot generation")
    args = parser.parse_args()

    if not os.path.isdir(args.logs_dir):
        print(f"❌ Logs directory not found: {args.logs_dir}")
        sys.exit(1)

    run_shap_analysis(args.logs_dir, no_plot=args.no_plot)
