"""
ibkr_backtest.py — Backtest standalone pour les paires Forex IBKR.

Utilise EXACTEMENT le même pipeline que le bot Binance :
  run_all_backtests()           → grid search IS (scénarios × EMA)
  run_walk_forward_validation() → sélection OOS (Sharpe ≥ 0.8, WinRate ≥ 30 %)

Seule différence : le `fetch_data_fn` et le `prepare_base_dataframe_fn`
injectés pointent vers IBKR au lieu de Binance.

Usage :
    # Cache local (si données déjà téléchargées par --live)
    .venv\\Scripts\\python.exe code/src/ibkr/ibkr_backtest.py

    # Depuis IB Gateway (port 4002 actif)
    .venv\\Scripts\\python.exe code/src/ibkr/ibkr_backtest.py --live

    # Depuis CSV local (colonnes : timestamp_ms,open,high,low,close,volume)
    .venv\\Scripts\\python.exe code/src/ibkr/ibkr_backtest.py --csv EURUSD=data/eurusd_1h.csv

Prérequis :
    IBKR_SECRET, IBKR_ACCOUNT  dans .env.ibkr (ou variables d'environnement)
"""
from __future__ import annotations

# ─── Chargement .env (AVANT tout import tiers — même pattern que bot_config) ──
import os as _os
import pathlib as _pathlib
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_pathlib.Path(__file__).parents[3] / ".env")
except ImportError:
    pass

# ─── Placeholders Binance (AVANT tout import tiers) ─────────────────────────
_os.environ.setdefault("BINANCE_API_KEY",    "IBKR_PLACEHOLDER_NOT_USED")
_os.environ.setdefault("BINANCE_SECRET_KEY", _os.environ.get("IBKR_SECRET", "IBKR_PLACEHOLDER_NOT_USED"))

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

# ─── sys.path ────────────────────────────────────────────────────────────────
_IBKR_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR  = os.path.abspath(os.path.join(_IBKR_DIR, ".."))
_BIN_DIR  = os.path.abspath(os.path.join(_IBKR_DIR, "..", "..", "bin"))
_ROOT_DIR = os.path.abspath(os.path.join(_IBKR_DIR, "..", "..", ".."))
for _p in (_SRC_DIR, _BIN_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ─── Pipeline Binance (réutilisé tel quel) ────────────────────────────────────
from backtest_runner import backtest_from_dataframe, run_all_backtests    # noqa: E402
from walk_forward import run_walk_forward_validation                      # noqa: E402
from indicators_engine import prepare_base_dataframe                      # noqa: E402

# ─── Modules IBKR ─────────────────────────────────────────────────────────────
from ibkr_config import IBKRConfig                                        # noqa: E402
from ibkr_data_fetcher import fetch_forex_data                            # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ibkr_backtest")

# ─── Constantes ──────────────────────────────────────────────────────────────

FOREX_PAIRS: List[Dict[str, Any]] = [
    {"ibkr_pair": "EURUSD", "periods_per_year": 6240},
    {"ibkr_pair": "EURGBP", "periods_per_year": 6240},
]

WF_SCENARIOS: List[Dict[str, Any]] = [
    {"name": "StochRSI",      "params": {"stoch_period": 14}},
    {"name": "StochRSI_SMA",  "params": {"stoch_period": 14, "sma_long": 200}},
    {"name": "StochRSI_ADX",  "params": {"stoch_period": 14, "adx_period": 14}},
    {"name": "StochRSI_TRIX", "params": {"stoch_period": 14, "trix_length": 7, "trix_signal": 15}},
]


def _fresh_start_date() -> str:
    """Fenêtre glissante 1095 jours — jamais figée à l'import."""
    return (datetime.today() - timedelta(days=1095)).strftime("%d %b %Y")


# ─── Injection IBKR dans le pipeline Binance ─────────────────────────────────

def _make_ibkr_fetch_fn(
    client: Any,
    ibkr_cfg: IBKRConfig,
    force_refresh: bool = False,
) -> Callable[[str, str, str], pd.DataFrame]:
    """Retourne un fetch_data_fn compatible prepare_base_dataframe.

    Signature attendue par indicators_engine : fn(pair, timeframe, start_date) → DataFrame
    """
    def _fetch(pair: str, timeframe: str, start_date: str) -> pd.DataFrame:
        return fetch_forex_data(
            pair, timeframe, start_date, client,
            cache_dir=ibkr_cfg.cache_dir,
            force_refresh=force_refresh,
        )
    return _fetch


def _make_csv_fetch_fn(csv_map: Dict[str, str]) -> Callable[[str, str, str], pd.DataFrame]:
    """Retourne un fetch_data_fn qui lit depuis un CSV local."""
    def _fetch(pair: str, _tf: str, _start: str) -> pd.DataFrame:
        path = csv_map.get(pair.upper())
        if not path:
            raise FileNotFoundError(f"Aucun CSV fourni pour {pair}")
        df = pd.read_csv(path)
        df.columns = [c.lower().strip() for c in df.columns]
        if "timestamp_ms" in df.columns:
            df.index = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True)
            df = df.drop(columns=["timestamp_ms"])
        elif "timestamp" in df.columns:
            df.index = pd.to_datetime(df["timestamp"], utc=True)
            df = df.drop(columns=["timestamp"])
        else:
            raise ValueError(f"{path} : colonne 'timestamp_ms' ou 'timestamp' manquante")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["open", "high", "low", "close"]).sort_index()
    return _fetch


def _make_ibkr_prepare_fn(
    fetch_fn: Callable[[str, str, str], pd.DataFrame],
) -> Callable[..., Optional[pd.DataFrame]]:
    """Retourne un prepare_base_dataframe_fn compatible run_all_backtests.

    Signature attendue : fn(pair, tf, start_date, stoch_period=14) → DataFrame
    On injecte fetch_fn IBKR à la place de fetch_historical_data Binance.
    """
    def _ibkr_prepare(pair: str, tf: str, start_date: str, stoch_period: int = 14) -> Optional[pd.DataFrame]:
        return prepare_base_dataframe(
            pair, tf, start_date, stoch_period,
            fetch_data_fn=fetch_fn,
        )
    return _ibkr_prepare


# ─── Affichage résultats ──────────────────────────────────────────────────────

def _print_top_is(pair: str, results: List[Dict[str, Any]], top_n: int = 5) -> None:
    sorted_r = sorted(results, key=lambda r: r.get("sharpe_ratio", -999), reverse=True)
    print(f"\n  Top {top_n} configs IS — {pair}:")
    hdr = f"    {'Scénario':<22} {'EMA':>10} {'TF':>4} {'Sharpe':>7} {'WinRate%':>9} {'MaxDD%':>8}"
    print(hdr)
    print("    " + "-" * (len(hdr) - 4))
    for r in sorted_r[:top_n]:
        ema1 = r.get("ema1_period", "?")
        ema2 = r.get("ema2_period", "?")
        tf   = r.get("timeframe", "?")
        sh   = r.get("sharpe_ratio", float("nan"))
        wr   = r.get("win_rate", float("nan")) * 100
        dd   = r.get("max_drawdown", float("nan")) * 100
        sc   = r.get("scenario_name", r.get("scenario", "?"))
        print(f"    {sc:<22} ({ema1:>2},{ema2:>3}) {tf:>4} {sh:>7.3f} {wr:>9.1f} {dd:>8.1f}")


def _print_wf_result(pair: str, wf_result: Dict[str, Any]) -> None:
    best = wf_result.get("best_wf_config")
    any_passed = wf_result.get("any_passed", False)
    print()
    print(f"  Paire : {pair}")
    if not any_passed or best is None:
        print("  OOS gates : ECHEC — aucun scénario valide (trading bloqué)")
        return
    ema1, ema2 = best.get("ema_periods", ("?", "?"))
    print("  OOS gates : PASS")
    print(f"  Meilleur scénario : {best.get('scenario')}")
    print(f"  EMA : ({ema1}, {ema2})  |  Timeframe : {best.get('timeframe')}")
    print(f"  Sharpe OOS moyen  : {best.get('avg_oos_sharpe', 0):.3f}")
    print(f"  WinRate OOS moyen : {best.get('avg_oos_win_rate', 0):.1f}%")
    print(f"  Return OOS moyen  : {best.get('avg_oos_return', 0):.1f}%")


# ─── Point d'entrée ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest IBKR Forex — pipeline identique au bot Binance"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live", action="store_true",
        help="Charge depuis IB Gateway (port 4002 actif requis)",
    )
    mode.add_argument(
        "--csv", action="append", metavar="PAIR=FILE",
        help="CSV local, ex: EURUSD=data/eurusd_1h.csv",
    )
    parser.add_argument(
        "--force-refresh", action="store_true",
        help="Ignore le cache (avec --live)",
    )
    parser.add_argument(
        "--pair", action="append", metavar="PAIR",
        help="Restreindre à une paire, ex: --pair EURUSD",
    )
    args = parser.parse_args()

    csv_map: Dict[str, str] = {}
    if args.csv:
        for entry in args.csv:
            if "=" not in entry:
                parser.error(f"--csv format invalide : '{entry}'")
            k, v = entry.split("=", 1)
            csv_map[k.upper().strip()] = v.strip()

    pairs_filter = {p.upper() for p in args.pair} if args.pair else set()
    ibkr_cfg = IBKRConfig.from_env()

    # Connexion IB Gateway si --live
    client: Any = None
    if args.live:
        from ibkr_client import IBKRForexClient
        client = IBKRForexClient(
            ibkr_cfg.host, ibkr_cfg.port, ibkr_cfg.client_id, ibkr_cfg.account,
        )
        logger.info("Connexion IB Gateway %s:%d ...", ibkr_cfg.host, ibkr_cfg.port)
        client.connect()

    try:
        sep = "=" * 70
        print(f"\n{sep}")
        print("  IBKR FOREX — Backtest (pipeline identique Binance)")
        print(f"  Fenêtre : {_fresh_start_date()} → aujourd'hui (1095 jours)")
        print(sep)

        for pair_def in FOREX_PAIRS:
            pair = pair_def["ibkr_pair"]
            if pairs_filter and pair not in pairs_filter:
                continue

            print(f"\n{'─' * 70}")
            print(f"  Paire : {pair}")
            print(f"{'─' * 70}")

            # ── Construire fetch_fn selon le mode ────────────────────────
            if pair in csv_map:
                fetch_fn: Callable[[str, str, str], pd.DataFrame] = _make_csv_fetch_fn({pair: csv_map[pair]})
                logger.info("[%s] Mode CSV : %s", pair, csv_map[pair])
            elif args.live and client is not None:
                fetch_fn = _make_ibkr_fetch_fn(client, ibkr_cfg, args.force_refresh)
                logger.info("[%s] Mode IB Gateway", pair)
            else:
                # Mode cache : dummy client — fetch_forex_data sert depuis le cache
                from unittest.mock import MagicMock
                dummy: Any = MagicMock()
                dummy.get_historical_klines.return_value = []
                fetch_fn = _make_ibkr_fetch_fn(dummy, ibkr_cfg, force_refresh=False)
                logger.info("[%s] Mode cache local", pair)

            prepare_fn = _make_ibkr_prepare_fn(fetch_fn)
            start_date = _fresh_start_date()

            # ── 1. Grid search IS — run_all_backtests ─────────────────────
            logger.info("[%s] run_all_backtests ...", pair)
            try:
                results = run_all_backtests(
                    pair, start_date, ["1h"],
                    sizing_mode="risk",
                    prepare_base_dataframe_fn=prepare_fn,
                )
            except Exception as exc:
                logger.error("[%s] run_all_backtests ERREUR : %s", pair, exc)
                continue

            if not results:
                logger.warning("[%s] Aucun résultat IS — données manquantes ?", pair)
                logger.warning("[%s] Relancer avec --live ou --csv PAIR=fichier.csv", pair)
                continue

            _print_top_is(pair, results)

            # ── 2. Walk-Forward OOS — run_walk_forward_validation ─────────
            logger.info("[%s] run_walk_forward_validation ...", pair)
            wf_base = {
                "1h": prepare_fn(pair, "1h", start_date) or pd.DataFrame(),
            }
            try:
                wf_result = run_walk_forward_validation(
                    base_dataframes=wf_base,
                    full_sample_results=results,
                    scenarios=WF_SCENARIOS,
                    backtest_fn=backtest_from_dataframe,
                    initial_capital=ibkr_cfg.initial_capital,
                    sizing_mode="risk",
                )
            except Exception as exc:
                logger.error("[%s] run_walk_forward_validation ERREUR : %s", pair, exc)
                continue

            _print_wf_result(pair, wf_result)

        print(f"\n{sep}\n")

    finally:
        if client is not None:
            client.disconnect()
            logger.info("Déconnecté de IB Gateway")


if __name__ == "__main__":
    main()
