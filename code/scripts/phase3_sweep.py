#!/usr/bin/env python3
"""
phase3_sweep.py — Backtests comparatifs Phase 3 (audit restrictivité)

Sweeps :
  T1 : Filtre MTF  — Off / Soft-50% (nouveau) / Hard-block (ancien)
  T2 : oos_decay_min gate  — analyse folds IS/OOS rolling
  T3 : atr_multiplier sweep — [4.5, 3.5, 3.0, 2.5, 2.0]
  T4 : oos_min_trades gate — analyse folds IS/OOS rolling

Usage :
  .venv\\Scripts\\python.exe code/scripts/phase3_sweep.py
  .venv\\Scripts\\python.exe code/scripts/phase3_sweep.py --pair SOLUSDC --tf 4h
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import pickle

# ── Chemin vers code/src ──────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "code" / "src"))

# Désactiver les logs verbeux des modules internes
logging.disable(logging.WARNING)

from bot_config import config
from cache_manager import get_cache_path, safe_cache_read
from indicators_engine import prepare_base_dataframe
import backtest_runner
import cache_manager as _cm

# Forcer le répertoire cache vers le bon chemin absolu (indépendant du CWD)
# Config est frozen → object.__setattr__ pour contourner la guard
_CACHE_DIR = str(_ROOT / "cache")
object.__setattr__(config, 'cache_dir', _CACHE_DIR)
_cm._effective_cache_dir = _CACHE_DIR
_cm._cache_dir_initialized = True

logging.disable(logging.NOTSET)
logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────
SCENARIO_BASE = {"name": "StochRSI", "params": {"stoch_period": 14}}
EMA1, EMA2 = 14, 25


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cache_fetch(pair: str, timeframe: str, start_date: str) -> pd.DataFrame:
    """Lit les données OHLCV depuis le cache local sans TTL (pickle direct)."""
    import os
    _dir = _cm._effective_cache_dir
    # Cherche le premier fichier correspondant au pair+timeframe
    prefix = f"{pair}_{timeframe}_"
    for fname in os.listdir(_dir):
        if fname.startswith(prefix) and fname.endswith(".pkl"):
            fpath = os.path.join(_dir, fname)
            try:
                with open(fpath, "rb") as fh:
                    df = pickle.load(fh)
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return df
            except Exception as e:
                raise RuntimeError(f"Erreur lecture cache {fpath}: {e}") from e
    raise RuntimeError(
        f"Cache introuvable pour {pair} {timeframe}. "
        "Lancez le bot une première fois pour peupler le cache, "
        f"ou vérifiez que cache/{pair}_{timeframe}_*.pkl existe."
    )


def _load_prepared_df(pair: str, tf: str) -> pd.DataFrame:
    """Charge et prépare le DataFrame (indicateurs calculés)."""
    print(f"  Chargement données {pair} {tf} depuis cache …", end=" ", flush=True)
    df = prepare_base_dataframe(
        pair=pair, timeframe=tf, start_date="1 January 2021", stoch_period=14,
        fetch_data_fn=lambda p, t, s: _cache_fetch(p, t, s),
    )
    if df is None or df.empty:
        raise RuntimeError(f"DataFrame vide après préparation pour {pair} {tf}.")
    print(f"{len(df):,} bougies ({df.index[0].date()} → {df.index[-1].date()})")
    return df


@contextlib.contextmanager
def _patch_config(**kwargs: Any):
    """Context manager : surcharge temporaire des attributs config.
    Config est frozen → object.__setattr__ pour contourner la guard.
    """
    saved = {k: getattr(config, k) for k in kwargs if hasattr(config, k)}
    for k, v in kwargs.items():
        object.__setattr__(config, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            object.__setattr__(config, k, v)


@contextlib.contextmanager
def _force_python_path():
    """Force l'utilisation du chemin Python (désactive Cython temporairement)."""
    orig = backtest_runner.CYTHON_BACKTEST_AVAILABLE
    backtest_runner.CYTHON_BACKTEST_AVAILABLE = False
    try:
        yield
    finally:
        backtest_runner.CYTHON_BACKTEST_AVAILABLE = orig


def _run_bt(df: pd.DataFrame, **cfg_overrides: Any) -> Dict[str, float]:
    """Lance un backtest avec les overrides config et retourne les métriques clés."""
    with _patch_config(**cfg_overrides):
        result = backtest_runner.backtest_from_dataframe(
            df=df,
            ema1_period=EMA1,
            ema2_period=EMA2,
            sizing_mode="risk",
        )
    trades = result.get("trades", pd.DataFrame())
    if not trades.empty:
        if 'type' in trades.columns:
            n_trades = len(trades[trades['type'] == 'sell'])
        else:
            # Cython path : trades sans colonne 'type', chaque ligne = 1 trade complété
            n_trades = len(trades)
    else:
        n_trades = 0
    return {
        "n_trades":   n_trades,
        "sharpe":     round(result.get("sharpe_ratio", 0.0), 3),
        "calmar":     round(result.get("calmar_ratio",  0.0), 3),
        "win_rate":   round(result.get("win_rate",      0.0), 1),
        "drawdown":   round(result.get("max_drawdown",  0.0), 3),
        "ret_pct":    round(
            (result.get("final_wallet", config.initial_wallet) / config.initial_wallet - 1) * 100,
            1,
        ),
    }


def _run_bt_python(df: pd.DataFrame, **cfg_overrides: Any) -> Dict[str, float]:
    """Même chose mais force le moteur Python (pour le filtre MTF soft)."""
    with _force_python_path():
        return _run_bt(df, **cfg_overrides)


# ── Affichage tables ──────────────────────────────────────────────────────────

def _print_table(
    title: str,
    rows: List[Dict[str, Any]],
    cols: List[str],
    highlight_col: Optional[str] = None,
) -> None:
    """Affiche un tableau ASCII simple sans dépendances Rich."""
    print(f"\n{'═'*70}")
    print(f"  {title}")
    print(f"{'═'*70}")
    col_widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    header = "  " + "  ".join(str(c).ljust(col_widths[c]) for c in cols)
    print(header)
    print("  " + "  ".join("─" * col_widths[c] for c in cols))
    best_sharpe = max((r.get("sharpe", 0) for r in rows), default=0)
    for row in rows:
        marker = " ◄ NEW" if highlight_col and row.get(highlight_col) else ""
        is_best = row.get("sharpe", 0) == best_sharpe and "sharpe" in cols
        prefix = "► " if is_best else "  "
        print(prefix + "  ".join(str(row.get(c, "")).ljust(col_widths[c]) for c in cols) + marker)


# ── T1 : MTF filter comparison ────────────────────────────────────────────────

def sweep_t1(df: pd.DataFrame) -> None:
    print("\n[T1] Filtre MTF — Off vs Hard-block (ancien) vs Soft-50% (nouveau)")
    print("     Moteur : Python (Cython désactivé pour comparaison homogène)")
    rows = []
    configs = [
        ("Off (pas de filtre)",    dict(mtf_filter_enabled=False)),
        ("Hard-block (ancien)",    dict(mtf_filter_enabled=True,  mtf_soft_factor=0.0)),
        ("Soft-50%  (nouveau) ◄", dict(mtf_filter_enabled=True,  mtf_soft_factor=0.5)),
    ]
    for label, overrides in configs:
        m = _run_bt_python(df, **overrides)
        rows.append({"Config": label, **m})
    _print_table(
        "T1 — Impact filtre MTF 4h (moteur Python, SOLUSDT 1h, 5 ans)",
        rows,
        ["Config", "n_trades", "sharpe", "calmar", "win_rate", "drawdown", "ret_pct"],
    )


# ── T3 : ATR multiplier sweep ─────────────────────────────────────────────────

def sweep_t3(df: pd.DataFrame) -> None:
    print("\n[T3] Sweep atr_multiplier (seuil activation trailing stop)")
    rows = []
    for val in [4.5, 3.5, 3.0, 2.5, 2.0]:
        marker = " ◄" if val == 3.0 else ("  (ancien)" if val == 4.5 else "")
        m = _run_bt(df, atr_multiplier=val, mtf_filter_enabled=False)
        rows.append({"atr_mult": f"{val:.1f}{marker}", **m})
    _print_table(
        "T3 — Sweep atr_multiplier (SOLUSDT 1h, 5 ans, MTF off)",
        rows,
        ["atr_mult", "n_trades", "sharpe", "calmar", "win_rate", "drawdown", "ret_pct"],
    )


# ── T2 + T4 : Analyse folds IS/OOS (rolling) ─────────────────────────────────

def _compute_decay(df: pd.DataFrame, is_slice: pd.DataFrame, oos_slice: pd.DataFrame) -> Dict[str, float]:
    """Calcule IS Sharpe, OOS Sharpe, decay ratio et n_trades OOS."""
    def _metrics(slice_df: pd.DataFrame) -> Dict[str, float]:
        if len(slice_df) < 200:
            return {"sharpe": 0.0, "n_trades": 0}
        try:
            r = backtest_runner.backtest_from_dataframe(
                df=slice_df, ema1_period=EMA1, ema2_period=EMA2, sizing_mode="risk"
            )
            trades = r.get("trades", pd.DataFrame())
            n = len(trades[trades["type"] == "sell"]) if not trades.empty else 0
            return {"sharpe": r.get("sharpe_ratio", 0.0), "n_trades": n}
        except Exception:
            return {"sharpe": 0.0, "n_trades": 0}

    is_m = _metrics(is_slice)
    oos_m = _metrics(oos_slice)
    decay = oos_m["sharpe"] / is_m["sharpe"] if is_m["sharpe"] > 0 else 0.0
    return {
        "is_sharpe":  is_m["sharpe"],
        "oos_sharpe": oos_m["sharpe"],
        "decay":      decay,
        "oos_trades": oos_m["n_trades"],
    }


def sweep_t2_t4(df: pd.DataFrame) -> None:
    print("\n[T2/T4] Analyse folds rolling IS/OOS (5 folds, ~1 an OOS chacun)")

    # Candles par an selon le timeframe détecté depuis l'index temporel
    if len(df) > 10 and isinstance(df.index, pd.DatetimeIndex):
        total_days = (df.index[-1] - df.index[0]).days or 1
        candles_per_year = int(len(df) / (total_days / 365.25))
    else:
        candles_per_year = 2190  # fallback 4h default
    one_year_c  = candles_per_year
    three_year_c = 3 * candles_per_year
    total_c = len(df)

    folds = []
    for i in range(5):
        oos_end   = total_c - i * one_year_c
        oos_start = oos_end - one_year_c
        is_start  = oos_start - three_year_c
        if is_start < 0:
            break
        is_slice  = df.iloc[is_start:oos_start]
        oos_slice = df.iloc[oos_start:oos_end]
        folds.append((i + 1, is_slice, oos_slice))

    print(f"     {len(folds)} folds construits. Calcul en cours …")
    fold_results = []
    for fold_id, is_s, oos_s in folds:
        print(f"       Fold {fold_id} …", end=" ", flush=True)
        r = _compute_decay(df, is_s, oos_s)
        fold_results.append({"Fold": fold_id, **r})
        print(f"IS Sharpe={r['is_sharpe']:.2f} / OOS Sharpe={r['oos_sharpe']:.2f} / decay={r['decay']:.2f} / OOS trades={r['oos_trades']}")

    # T2 — Combien de folds passent le gate decay_min ?
    decay_thresholds = [0.10, 0.15, 0.20, 0.30, 0.40]
    t2_rows = []
    for thr in decay_thresholds:
        passing = sum(1 for r in fold_results if r["decay"] >= thr and r["oos_sharpe"] > 0)
        marker = " ◄ NEW" if thr == 0.20 else (" (ancien)" if thr == 0.40 else "")
        t2_rows.append({
            "oos_decay_min": f"{thr:.2f}{marker}",
            "folds_passing": f"{passing}/{len(fold_results)}",
            "pass_pct":      f"{passing/len(fold_results)*100:.0f}%" if fold_results else "N/A",
        })
    _print_table(
        "T2 — Gate oos_decay_min : folds valides / total",
        t2_rows,
        ["oos_decay_min", "folds_passing", "pass_pct"],
    )

    # T4 — Combien de folds passent le gate oos_min_trades ?
    trades_thresholds = [5, 8, 10, 15]
    t4_rows = []
    for thr in trades_thresholds:
        passing = sum(1 for r in fold_results if r["oos_trades"] >= thr)
        marker = " ◄ NEW" if thr == 8 else (" (ancien)" if thr == 15 else "")
        t4_rows.append({
            "oos_min_trades": f"{thr}{marker}",
            "folds_passing": f"{passing}/{len(fold_results)}",
            "pass_pct":      f"{passing/len(fold_results)*100:.0f}%" if fold_results else "N/A",
            "median_oos_trades": (
                int(np.median([r["oos_trades"] for r in fold_results]))
                if fold_results else 0
            ),
        })
    _print_table(
        "T4 — Gate oos_min_trades : folds valides / total",
        t4_rows,
        ["oos_min_trades", "folds_passing", "pass_pct", "median_oos_trades"],
    )

    if not fold_results:
        print("\n  Pas assez de donn\u00e9es pour les folds IS/OOS.")
        return
    # Résumé
    print(f"\n  M\u00e9diane OOS trades  : {int(np.median([r['oos_trades'] for r in fold_results]))}")
    print(f"  M\u00e9diane decay ratio : {np.median([r['decay'] for r in fold_results]):.3f}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3 — sweeps comparatifs backtests")
    parser.add_argument("--pair", default="SOLUSDT", help="Paire à tester (défaut: SOLUSDT)")
    parser.add_argument("--tf",   default="4h",      help="Timeframe (défaut: 4h)")
    parser.add_argument("--t1",   action="store_true", help="Sweeper uniquement T1")
    parser.add_argument("--t3",   action="store_true", help="Sweeper uniquement T3")
    parser.add_argument("--t24",  action="store_true", help="Sweeper uniquement T2/T4")
    args = parser.parse_args()

    run_all = not (args.t1 or args.t3 or args.t24)

    print(f"\n{'█'*70}")
    print(f"  PHASE 3 — Sweeps comparatifs  |  {args.pair} {args.tf}")
    print(f"{'█'*70}")

    df = _load_prepared_df(args.pair, args.tf)

    if run_all or args.t1:
        sweep_t1(df)
    if run_all or args.t3:
        sweep_t3(df)
    if run_all or args.t24:
        sweep_t2_t4(df)

    print(f"\n{'─'*70}")
    print("  Terminé. Valeurs de référence Phase 1 appliquées dans bot_config.py :")
    print("    atr_multiplier=3.0 | oos_decay_min=0.20 | oos_min_trades=8")
    print("    breakeven_trigger_pct=0.015 | stop_loss_cooldown_candles=5")
    print(f"{'─'*70}\n")


if __name__ == "__main__":
    main()
