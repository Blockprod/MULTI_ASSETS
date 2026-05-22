"""
IBKR_FOREX.py — Orchestrateur principal du bot Forex IBKR.

Stratégies : EUR/USD et EUR/GBP — Paper Trading H24/7
Capital     : 10 000 € (paper)
Exchange    : IB Gateway — port 4002 (paper), clientId=3
Scheduler   : schedule.every(60).minutes

NOTE: Ce fichier doit être lancé en premier ou via Windows Task Scheduler.
      NE JAMAIS importer ni modifier les modules Binance depuis ce fichier.

⚠️  Les vars BINANCE_API_KEY / BINANCE_SECRET_KEY sont positionnées comme
    placeholders AVANT tous les imports tiers, uniquement pour permettre
    le chargement de backtest_runner / walk_forward / indicators_engine
    qui transitent par bot_config. Aucun appel API Binance n'est effectué.
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

# ─── Positionnement des placeholders BINANCE (AVANT tout import tiers) ────────
_os.environ.setdefault("BINANCE_API_KEY", "IBKR_PLACEHOLDER_NOT_USED")
_os.environ.setdefault(
    "BINANCE_SECRET_KEY",
    _os.environ.get("IBKR_SECRET", "IBKR_PLACEHOLDER_NOT_USED"),
)
# Aligner initial_wallet backtest avec le capital IBKR (config.initial_wallet lu à l'import)
_os.environ.setdefault("INITIAL_WALLET", _os.environ.get("IBKR_INITIAL_CAPITAL", "10000.0"))
# Frais backtest adaptés au forex IBKR (vs 0.07 % Binance crypto).
# Les frais forex réels sont : commission IBKR ~0.002 % + spread ~0.01 % ≈ 0.03 % aller-retour.
# Utiliser ces valeurs dans ce processus uniquement (bot IBKR = processus séparé du bot Binance).
_os.environ.setdefault("BACKTEST_TAKER_FEE", "0.00015")   # ~1.5 pip spread EUR/USD
_os.environ.setdefault("BACKTEST_MAKER_FEE", "0.00005")   # ~0.5 pip (ordres limites)
# OOS gates adaptés au forex (crypto : Sharpe ≥ 0.8, WR ≥ 30 % — trop strict).
# Forex : Sharpe annualisé 0.3 est considéré correct. WR ≥ 25 % est réaliste.
_os.environ.setdefault("OOS_SHARPE_MIN", "0.3")            # Forex (vs 0.8 crypto)
_os.environ.setdefault("OOS_WIN_RATE_MIN", "25.0")         # Forex (vs 30.0 crypto)
# Aligner le risk/trade backtest sur le live IBKR (défaut Binance = 5%, IBKR = 5.5%)
_os.environ.setdefault("RISK_PER_TRADE", "0.055")           # Alignement backtest ↔ live IBKR
# Les vars email (communes aux deux bots)
# SENDER_EMAIL, RECEIVER_EMAIL, GOOGLE_MAIL_PASSWORD doivent être dans .env.ibkr

# ─── Imports standard ─────────────────────────────────────────────────────────
import json
import logging
import math
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional, Tuple

import schedule
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# ─── sys.path : ajouter le répertoire src/ et bin/ ──────────────────────────
_IBKR_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.abspath(os.path.join(_IBKR_DIR, ".."))
_BIN_DIR = os.path.abspath(os.path.join(_IBKR_DIR, "..", "..", "bin"))
_ROOT_DIR = os.path.abspath(os.path.join(_IBKR_DIR, "..", "..", ".."))

for _p in (_SRC_DIR, _BIN_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ─── Imports modules réutilisables (passent par bot_config avec placeholder) ─
from backtest_runner import backtest_from_dataframe, run_all_backtests   # noqa: E402
from walk_forward import run_walk_forward_validation                     # noqa: E402
from indicators_engine import prepare_base_dataframe, calculate_indicators  # noqa: E402
from email_utils import send_email_alert                                 # noqa: E402

# ─── Imports modules IBKR (entièrement isolés) ───────────────────────────────
from ibkr_config import IBKRConfig                                       # noqa: E402
from ibkr_client import IBKRForexClient                                  # noqa: E402
from ibkr_data_fetcher import fetch_forex_data                           # noqa: E402
from ibkr_state_manager import (                                         # noqa: E402
    save_ibkr_state, load_ibkr_state, write_heartbeat, IBKRStateError,
)
from ibkr_order_manager_forex import (                                   # noqa: E402
    safe_forex_buy, safe_forex_sell, place_forex_stop_loss,
    cancel_forex_order, get_current_price, get_account_nav,
)

# ─── Logging ─────────────────────────────────────────────────────────────────
_LOG_DIR = os.path.join(_ROOT_DIR, "code", "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        RotatingFileHandler(
            os.path.join(_LOG_DIR, "ibkr_forex.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("ibkr_forex")
console = Console()

# ─── Paires Forex tradées ─────────────────────────────────────────────────────
FOREX_PAIRS: List[Dict[str, Any]] = [
    {"ibkr_pair": "EURUSD", "periods_per_year": 6240},
    {"ibkr_pair": "GBPUSD", "periods_per_year": 6240},  # remplacé EURGBP (range-bound, WR IS 21%)
]

# ─── Scénarios Walk-Forward (identiques à MULTI_SYMBOLS pour cohérence) ──────
WF_SCENARIOS: List[Dict[str, Any]] = [
    {"name": "StochRSI",      "params": {"stoch_period": 14}},
    {"name": "StochRSI_SMA",  "params": {"stoch_period": 14, "sma_long": 200}},
    {"name": "StochRSI_ADX",  "params": {"stoch_period": 14, "adx_period": 14}},
    {"name": "StochRSI_TRIX", "params": {"stoch_period": 14, "trix_length": 7, "trix_signal": 15}},
    {"name": "StochRSI_DipBuy", "params": {"stoch_period": 14, "stoch_buy_max": 0.30}},
]

# ─── Thread-safety ────────────────────────────────────────────────────────────
_ibkr_state_lock = threading.RLock()
_pair_execution_locks: Dict[str, threading.Lock] = {}
_pair_locks_mutex = threading.Lock()

# ─── État runtime (bot_state) ─────────────────────────────────────────────────
bot_state: Dict[str, Any] = {}

# ─── Cache cycle live 2 min (identique _runtime.live_best_params Binance) ────────────────────
_live_best_params: Dict[str, Optional[Dict[str, Any]]] = {}     # OOS validé — mis à jour chaque 60 min
_live_is_best_params: Dict[str, Optional[Dict[str, Any]]] = {}  # IS best — fallback affichage quand OOS bloqué
_pair_last_indicators: Dict[str, Any] = {}                       # pd.Series.copy() chaque 60 min

# ─── Compteur d'échecs sauvegarde (kill-switch) ──────────────────────────────
_save_failure_count = 0
_MAX_SAVE_FAILURES = 3


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fresh_start_date() -> str:
    """Fenêtre glissante 1095 jours — jamais figée à l'import (règle absolue)."""
    return (datetime.today() - timedelta(days=1095)).strftime("%d %b %Y")


def _get_pair_lock(pair: str) -> threading.Lock:
    with _pair_locks_mutex:
        if pair not in _pair_execution_locks:
            _pair_execution_locks[pair] = threading.Lock()
        return _pair_execution_locks[pair]


def _ensure_pair_state(pair: str) -> None:
    """Initialise l'état d'une paire si elle est absente du bot_state."""
    with _ibkr_state_lock:
        if pair not in bot_state:
            bot_state[pair] = {
                "last_order_side": None,
                "entry_price": None,
                "quantity": None,
                "stop_loss": None,
                "sl_order_id": None,
                "sl_exchange_placed": False,
                "oos_blocked": False,
                "oos_blocked_since": 0.0,
                "last_run_time": None,
                "execution_count": 0,
                "last_best_params": None,
                "trailing_stop_activated": False,
                "trailing_stop": None,
                "max_price": None,
                "partial_taken_1": False,
                "partial_taken_2": False,
                "buy_timestamp": 0.0,
            }


def _save_state(ibkr_config: IBKRConfig, *, force: bool = False) -> None:
    """Sauvegarde bot_state + heartbeat avec gestion kill-switch (3 échecs)."""
    global _save_failure_count
    try:
        with _ibkr_state_lock:
            state_copy = dict(bot_state)
        save_ibkr_state(
            state_copy,
            states_dir=ibkr_config.states_dir,
            state_file=ibkr_config.state_file,
            ibkr_secret=ibkr_config.ibkr_secret,
            force=force,
        )
        write_heartbeat(ibkr_config.states_dir, extra={"emergency_halt": bot_state.get("emergency_halt", False)})
        _save_failure_count = 0
    except IBKRStateError as exc:
        _save_failure_count += 1
        logger.error("[IBKR] Échec sauvegarde #%d : %s", _save_failure_count, exc)
        if _save_failure_count >= _MAX_SAVE_FAILURES:
            with _ibkr_state_lock:
                bot_state["emergency_halt"] = True
                bot_state["emergency_halt_reason"] = f"3 échecs consécutifs save_state : {exc}"
            logger.critical("[IBKR] EMERGENCY HALT — 3 sauvegardes échouées !")


def _compute_position_size(
    nav: float,
    risk_pct: float,
    atr_value: float,
    entry_price: float,
    atr_stop_multiplier: float = 3.0,
) -> float:
    """Calcule la quantité de devise base à acheter (risk-based sizing).

    Identique à position_sizing.compute_position_size_by_risk mais sans bot_config.
    """
    if atr_value <= 0 or entry_price <= 0 or nav <= 0:
        return 0.0
    stop_distance = atr_stop_multiplier * atr_value
    if stop_distance <= 0:
        return 0.0
    risk_amount = nav * risk_pct
    qty = risk_amount / stop_distance
    return qty


def _scenario_params(scenario_name: str) -> Dict[str, Any]:
    """Retourne les params du scénario identifié par son nom dans WF_SCENARIOS."""
    for sc in WF_SCENARIOS:
        if sc["name"] == scenario_name:
            return sc.get("params", {})
    return {}


# ─── Sélection du meilleur scénario (WF + OOS) ───────────────────────────────

def _make_prepare_fn(df_by_tf: "Dict[str, Any]") -> "Any":
    """Retourne un prepare_base_dataframe_fn qui sert le DataFrame du timeframe demandé.

    Accepte un dict {tf: DataFrame} pour supporter 1h et 4h (resampling sans re-fetch).
    run_all_backtests attend fn(pair, tf, start_date, stoch_period) → DataFrame.
    """
    def _prepare(pair: str, tf: str, start_date: str, stoch_period: int = 14) -> "Any":
        # Sélectionner le DataFrame du timeframe demandé, fallback sur "1h"
        _df = df_by_tf.get(tf)
        df = _df if _df is not None else df_by_tf.get("1h")
        if df is None:
            return None
        return prepare_base_dataframe(
            pair, tf, start_date, stoch_period,
            fetch_data_fn=lambda _pair, _tf, _start: df.copy(),
        )
    return _prepare


def _select_best_scenario(
    pair: str,
    df_by_tf: "Dict[str, Any]",  # {tf: pd.DataFrame}
    ibkr_cfg: IBKRConfig,
    periods_per_year: int,
) -> "Optional[Dict[str, Any]]":
    """Exécute le pipeline complet run_all_backtests + run_walk_forward_validation.

    Accepte un dict de DataFrames par timeframe (ex: {"1h": df_1h, "4h": df_4h}).
    Le timeframe 4h est obtenu par resampling depuis le 1h sans requête IBKR
    supplémentaire — lève le plafond bucket WF (top-2/tf) de 2 à 4 candidats.
      1. run_all_backtests  — grid search IS sur tous scénarios × EMA × timeframes
      2. run_walk_forward_validation — sélection OOS (Sharpe ≥ 0.3, WinRate ≥ 25 % forex)
    Retourne le meilleur config OOS ou None si aucun ne passe les gates.
    """
    prepare_fn = _make_prepare_fn(df_by_tf)
    start_date = _fresh_start_date()
    timeframes = list(df_by_tf.keys())

    # 1. Grid search IS — même appel que MULTI_SYMBOLS
    try:
        results = run_all_backtests(
            pair, start_date, timeframes,
            sizing_mode="risk",
            leverage=ibkr_cfg.max_leverage,
            prepare_base_dataframe_fn=prepare_fn,
        )
    except Exception as exc:
        logger.error("[IBKR] %s — run_all_backtests ERREUR : %s", pair, exc)
        return None

    if not results:
        logger.warning("[IBKR] %s — aucun résultat backtest IS", pair)
        return None

    # Stocker le IS best pour l'affichage 2 min même quand OOS échoue
    with _ibkr_state_lock:
        _live_is_best_params[pair] = results[0]

    # Afficher le tableau IS identique au bot Binance
    try:
        import sys as _sys
        import os as _os
        _src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        if _src_dir not in _sys.path:
            _sys.path.insert(0, _src_dir)
        from display_ui import display_results_for_pair
        display_results_for_pair(pair, results)
    except Exception as _disp_exc:
        logger.debug("[IBKR] Affichage tableau IS ignoré : %s", _disp_exc)

    # 2. Walk-Forward OOS — même appel que MULTI_SYMBOLS
    import pandas as _pd
    wf_base: Dict[str, Any] = {}
    for _tf in timeframes:
        _df_wf = prepare_fn(pair, _tf, start_date)
        wf_base[_tf] = _df_wf if _df_wf is not None and not _df_wf.empty else _pd.DataFrame()
    try:
        wf_result = run_walk_forward_validation(
            base_dataframes=wf_base,
            full_sample_results=results,
            scenarios=WF_SCENARIOS,
            backtest_fn=backtest_from_dataframe,
            initial_capital=ibkr_cfg.initial_capital,
            sizing_mode="risk",
            leverage=ibkr_cfg.max_leverage,
            top_per_tf=4,   # IBKR Forex: 4 candidats/TF vs 2 par défaut (Binance)
            n_folds=3,  # Folds plus larges (91j 4h) vs 4 (68j) — plus de trades/fold
        )
    except Exception as exc:
        logger.error("[IBKR] %s — run_walk_forward_validation ERREUR : %s", pair, exc)
        return None

    if not wf_result.get("any_passed"):
        logger.warning(
            "[IBKR] %s — OOS gates FAIL : aucun scénario validé (Sharpe≥0.3, WR≥25%%) "
            "— achat bloqué jusqu'au prochain cycle",
            pair,
        )
        return None

    best = wf_result["best_wf_config"]
    logger.info(
        "[IBKR] %s — meilleur scénario : %s EMA(%s,%s) Sharpe OOS=%.3f",
        pair, best.get("scenario"), best.get("ema_periods", ["?", "?"])[0],
        best.get("ema_periods", ["?", "?"])[1], best.get("avg_oos_sharpe", 0.0),
    )
    return best


# ─── Helpers signal : checker + panneaux Rich ────────────────────────────────

def _check_ibkr_buy_signal(
    last: "Any",
    scenario: str,
    current_price: float,
    stoch_buy_min: float = 0.05,
    stoch_buy_max: float = 0.80,
) -> "Tuple[bool, str]":
    """Évalue les conditions BUY Forex — même logique que le backtest.

    Retourne (signal_valide, raison_détaillée).  Analogue à
    signal_generator.generate_buy_condition_checker du bot Binance.
    """
    ema1 = float(last.get("ema1", 0.0) or 0.0)
    ema2 = float(last.get("ema2", 0.0) or 0.0)
    stoch = float(last.get("stoch_rsi", 1.0) or 1.0)

    if not (ema1 > ema2):
        return False, f"EMA1 ({ema1:.5f}) \u2264 EMA2 ({ema2:.5f})"
    if not (stoch < stoch_buy_max):
        return False, f"StochRSI ({stoch:.3f}) \u2265 {stoch_buy_max:.2f} (trop haut)"
    if not (stoch > stoch_buy_min):
        return False, f"StochRSI ({stoch:.3f}) \u2264 {stoch_buy_min:.2f} (trop bas)"

    if scenario == "StochRSI_SMA":
        sma_long = last.get("sma_long")
        if sma_long is not None and current_price < float(sma_long):
            return False, f"Prix ({current_price:.5f}) < SMA200 ({float(sma_long):.5f})"
    if scenario == "StochRSI_ADX":
        adx = float(last.get("adx", 0.0) or 0.0)
        if adx < 25.0:
            return False, f"ADX ({adx:.2f}) < 25"
    if scenario == "StochRSI_TRIX":
        trix_histo = last.get("TRIX_HISTO")
        trix_val = float(trix_histo) if trix_histo is not None else float("nan")
        if trix_histo is None or trix_val <= 0:
            return False, f"TRIX_HISTO ({trix_val:.5f}) \u2264 0"
    if scenario == "StochRSI_DipBuy":
        # Retour à la moyenne : uniquement en zone de survente profonde (< 0.30).
        # Le seuil 0.30 reflète stoch_buy_max=0.30 utilisé dans le backtest.
        if not (stoch < 0.30):
            return False, f"StochRSI ({stoch:.3f}) \u2265 0.30 (seuil DipBuy non atteint)"

    return True, "[OK] Signal d'achat valide"


def _ok_mark(cond: bool) -> str:
    """✔ vert / ✘ rouge pour les panneaux Rich."""
    return "[bold green]\u2714 OK[/bold green]" if cond else "[bold red]\u2718 NOK[/bold red]"


def _display_ibkr_buy_panel(
    pair: str,
    current_price: float,
    last: "Any",
    best: "Dict[str, Any]",
    buy_signal: bool,
    buy_reason: str,
    con: "Console",
    *,
    oos_blocked: bool = False,
) -> None:
    """Panneau Rich des conditions d'achat IBKR — affiché à chaque cycle live.

    Analogue à display_ui.display_buy_signal_panel du bot Binance.
    """
    try:
        ema_periods = best.get("ema_periods", ["?", "?"])
        ema1_p = ema_periods[0] if isinstance(ema_periods, (list, tuple)) and len(ema_periods) > 0 else "?"
        ema2_p = ema_periods[1] if isinstance(ema_periods, (list, tuple)) and len(ema_periods) > 1 else "?"
        tf = best.get("timeframe", "?")
        scenario = best.get("scenario", "StochRSI")
        strategy_label = f"{scenario} EMA({ema1_p}/{ema2_p}) {tf}"

        ema1 = float(last.get("ema1", 0.0) or 0.0)
        ema2 = float(last.get("ema2", 0.0) or 0.0)
        stoch = float(last.get("stoch_rsi", float("nan")) or float("nan"))

        grid = Table(
            title="[bold white]Analyse des conditions d'achat[/bold white]",
            title_justify="left",
            box=None, show_header=False, pad_edge=False,
            show_edge=False, padding=(0, 1),
        )
        grid.add_column("condition", width=28, no_wrap=True, style="bold white")
        grid.add_column("result", width=14, no_wrap=True)
        grid.add_column("detail", style="dim")

        grid.add_row("Stratégie active", "", f"[bold cyan]{strategy_label}[/bold cyan]")
        grid.add_row("Prix actuel", "", f"[white]{current_price:.5f}[/white]")
        grid.add_row(
            f"EMA{ema1_p} > EMA{ema2_p}",
            _ok_mark(ema1 > ema2),
            f"EMA{ema1_p}={ema1:.5f}  EMA{ema2_p}={ema2:.5f}",
        )
        grid.add_row("StochRSI < 80%", _ok_mark(stoch < 0.8), f"{stoch * 100:.1f}%")
        grid.add_row("StochRSI > 5%",  _ok_mark(stoch > 0.05), f"{stoch * 100:.1f}%")
        grid.add_row("StochRSI actuel", "", f"[bold white]{stoch * 100:.2f}[/bold white]")

        if scenario == "StochRSI_ADX":
            adx = float(last.get("adx", 0.0) or 0.0)
            grid.add_row("ADX > 25", _ok_mark(adx > 25.0), f"ADX={adx:.2f}")
        if scenario == "StochRSI_SMA":
            sma = last.get("sma_long")
            sma_f = float(sma) if sma is not None else None
            grid.add_row(
                "Prix > SMA200",
                _ok_mark(sma_f is not None and current_price > sma_f),
                f"SMA200={sma_f:.5f}" if sma_f is not None else "N/A",
            )
        if scenario == "StochRSI_TRIX":
            trix = last.get("TRIX_HISTO")
            trix_f = float(trix) if trix is not None else None
            grid.add_row(
                "TRIX_HISTO > 0",
                _ok_mark(trix_f is not None and trix_f > 0),
                f"TRIX={trix_f:.5f}" if trix_f is not None else "N/A",
            )

        grid.add_row("", "", "")
        grid.add_row("", "", f"[dim italic]{buy_reason}[/dim italic]")

        if oos_blocked:
            grid.add_row("", "", "")
            grid.add_row(
                "OOS gates",
                "[bold red]\u2718 BLOQU\u00c9[/bold red]",
                "[dim]Achat suspendu — analyse IS (informatif)[/dim]",
            )

        if oos_blocked:
            panel_title = f"[bold yellow]ANALYSE IS [{pair}] \u2014 OOS NON VALID\u00c9 (informatif)[/bold yellow]"
            border = "yellow"
        elif buy_signal:
            panel_title = f"[bold green]SIGNAL D'ACHAT [{pair}] \u2014 CONDITIONS REMPLIES[/bold green]"
            border = "green"
        else:
            panel_title = f"[bold yellow]SIGNAL D'ACHAT [{pair}] \u2014 CONDITIONS NON REMPLIES[/bold yellow]"
            border = "yellow"
        con.print(Panel(
            grid,
            title=panel_title,
            border_style=border,
            padding=(1, 2),
        ))
    except Exception as _panel_err:
        logger.debug("[IBKR] _display_ibkr_buy_panel erreur : %s", _panel_err)


def _display_ibkr_sell_panel(
    pair: str,
    current_price: float,
    last: "Any",
    entry_price: float,
    qty: float,
    sell_signal: bool,
    con: "Console",
    best: "Optional[Dict[str, Any]]" = None,
) -> None:
    """Panneau Rich des conditions de vente IBKR — affiché à chaque cycle live.

    Analogue à display_ui.display_sell_signal_panel du bot Binance.
    """
    try:
        stoch = float(last.get("stoch_rsi", float("nan")) or float("nan"))
        pnl_latent = (current_price - entry_price) * qty if entry_price and qty else 0.0
        pnl_pct = (
            (current_price - entry_price) / entry_price * 100
            if entry_price and entry_price > 0 else 0.0
        )

        grid = Table(
            title="[bold white]Analyse des conditions de vente[/bold white]",
            title_justify="left",
            box=None, show_header=False, pad_edge=False,
            show_edge=False, padding=(0, 1),
        )
        grid.add_column("condition", width=28, no_wrap=True, style="bold white")
        grid.add_column("result", width=14, no_wrap=True)
        grid.add_column("detail", style="dim")

        if best:
            ep = best.get("ema_periods", ["?", "?"])
            ema1_p = ep[0] if isinstance(ep, (list, tuple)) and len(ep) > 0 else "?"
            ema2_p = ep[1] if isinstance(ep, (list, tuple)) and len(ep) > 1 else "?"
            tf = best.get("timeframe", "?")
            scenario = best.get("scenario", "StochRSI")
            grid.add_row("Stratégie active", "", f"[bold cyan]{scenario} EMA({ema1_p}/{ema2_p}) {tf}[/bold cyan]")

        grid.add_row("Prix actuel",   "", f"[white]{current_price:.5f}[/white]")
        grid.add_row("Prix d'entrée", "", f"{entry_price:.5f}")
        grid.add_row("StochRSI > 40%", _ok_mark(stoch > 0.4), f"{stoch * 100:.1f}%")

        _pnl_color = "bold green" if pnl_latent >= 0 else "bold red"
        grid.add_row(
            "PnL latent", "",
            f"[{_pnl_color}]{pnl_latent:+.2f} \u20ac ({pnl_pct:+.2f}%)[/{_pnl_color}]",
        )

        panel_title = (
            f"[bold magenta]SIGNAL DE VENTE [{pair}] \u2014 CONDITIONS REMPLIES[/bold magenta]"
            if sell_signal else
            f"[bold blue]SCAN VENTE [{pair}] \u2014 EN POSITION (pas de signal)[/bold blue]"
        )
        con.print(Panel(
            grid,
            title=panel_title,
            border_style="magenta" if sell_signal else "blue",
            padding=(1, 2),
        ))
    except Exception as _panel_err:
        logger.debug("[IBKR] _display_ibkr_sell_panel erreur : %s", _panel_err)


def _display_ibkr_forex_balance_panel(
    pair: str,
    nav: float,
    current_price: float,
    in_position: bool,
    pair_state: Dict[str, Any],
    con: "Console",
) -> None:
    """Panneau Rich compte IBKR — analogue 'SOLDES DE TRADING' du bot Binance.

    Affiché à chaque cycle live 2 min dans _live_process_pair.
    """
    try:
        base = pair[:3]   # EUR, GBP
        quote = pair[3:]  # USD

        grid = Table(
            box=None, show_header=False, pad_edge=False,
            show_edge=False, padding=(0, 2),
        )
        grid.add_column("label", width=32, no_wrap=True, style="dim")
        grid.add_column("value", style="bold white")

        grid.add_row("Paire Forex", pair)
        grid.add_row("Devise de cotation", quote)
        grid.add_row("", "")
        grid.add_row(
            "Capital de trading",
            f"[bold cyan]{nav:,.2f} \u20ac[/bold cyan]" if nav > 0 else "[dim]N/A[/dim]",
        )
        grid.add_row(
            f"Prix {pair} actuel",
            f"[white]{current_price:.5f} {quote}[/white]",
        )
        grid.add_row("", "")

        if in_position:
            entry = pair_state.get("entry_price") or 0.0
            qty = pair_state.get("quantity") or 0.0
            pnl = (current_price - entry) * qty if entry > 0 else 0.0
            pnl_pct = (current_price - entry) / entry * 100 if entry > 0 else 0.0
            pnl_color = "bold green" if pnl >= 0 else "bold red"
            grid.add_row("Statut", "[bold green]EN POSITION (BUY)[/bold green]")
            grid.add_row("Prix d'entr\u00e9e", f"{entry:.5f} {quote}")
            grid.add_row("Quantit\u00e9", f"{qty:,.0f} {base}")
            grid.add_row(
                "PnL latent",
                f"[{pnl_color}]{pnl:+.2f} \u20ac ({pnl_pct:+.2f}%)[/{pnl_color}]",
            )
        else:
            grid.add_row("Statut", "[dim]Hors position[/dim]")

        con.print(Panel(
            grid,
            title="[bold white]SOLDES IBKR FOREX[/bold white]",
            border_style="blue",
            padding=(1, 2),
        ))
    except Exception as _err:
        logger.debug("[IBKR] _display_ibkr_forex_balance_panel erreur : %s", _err)


def _display_ibkr_planning_panel(
    last_exec_dt: datetime,
    next_exec_dt: datetime,
    con: "Console",
) -> None:
    """Panneau Rich planification — analogue 'SUIVI D\u2019EXECUTION' du bot Binance.

    Affiché à chaque cycle live 2 min, après l'évaluation des signaux.
    """
    try:
        elapsed = datetime.now() - last_exec_dt
        elapsed_str = str(elapsed).split(".")[0]

        grid = Table(
            box=None, show_header=False, pad_edge=False,
            show_edge=False, padding=(0, 2),
        )
        grid.add_column("label", width=32, no_wrap=True, style="dim")
        grid.add_column("value", style="bold white")

        grid.add_row("Derni\u00e8re ex\u00e9cution", last_exec_dt.strftime("%Y-%m-%d %H:%M:%S"))
        grid.add_row("Temps \u00e9coul\u00e9", elapsed_str)
        grid.add_row("", "")
        grid.add_row("Mode de planification", "Live: 2 min | Backtest+WF: 60 min")
        grid.add_row(
            "Prochaine ex\u00e9cution",
            f"Live toutes les 2 min ({next_exec_dt.strftime('%H:%M:%S')})",
        )

        con.print(Panel(
            grid,
            title="[bold white]SUIVI D\u2019EX\u00c9CUTION & PLANIFICATION AUTOMATIQUE[/bold white]",
            border_style="dim",
            padding=(1, 2),
        ))
    except Exception as _err:
        logger.debug("[IBKR] _display_ibkr_planning_panel erreur : %s", _err)


# ─── Signal BUY/SELL partagé (60 min + 2 min) ────────────────────────────────

def _execute_pair_signal(
    pair: str,
    best: "Dict[str, Any]",
    last: "Any",  # pd.Series — bougie fermée iloc[-2]
    client: "IBKRForexClient",
    ibkr_cfg: "IBKRConfig",
) -> None:
    """Évalue le signal et exécute les ordres BUY/SELL.

    Appelée depuis _process_pair (cycle 60 min) ET _live_process_pair (cycle 2 min).
    Identique au pattern execute_live_trading_only du bot Binance.
    """
    with _ibkr_state_lock:
        pair_state = bot_state.get(pair, {})
        in_position = (pair_state.get("last_order_side") == "BUY")

    current_price = get_current_price(client, pair)
    if current_price <= 0:
        logger.error("[IBKR] %s — prix actuel invalide (%.5f)", pair, current_price)
        return

    scenario = best.get("scenario", "StochRSI")

    # ── BUY ──────────────────────────────────────────────────────────────────
    if not in_position and not pair_state.get("oos_blocked", False):
        # 1. Évaluer le signal en premier (pour affichage systématique)
        buy_signal, buy_reason = _check_ibkr_buy_signal(last, scenario, current_price)

        # 2. Vérification limite perte journalière
        _today_str = datetime.utcnow().strftime("%Y-%m-%d")
        with _ibkr_state_lock:
            if bot_state.get("daily_pnl_date") != _today_str:
                bot_state["daily_pnl"] = 0.0
                bot_state["daily_pnl_date"] = _today_str
            _daily_pnl = bot_state.get("daily_pnl", 0.0)
        _daily_loss_limit = -ibkr_cfg.daily_loss_limit_pct * ibkr_cfg.initial_capital
        _daily_blocked = _daily_pnl <= _daily_loss_limit
        if _daily_blocked:
            logger.warning(
                "[IBKR] %s — daily loss limit atteint (PnL=%.2f \u2264 %.2f), BUY bloqué",
                pair, _daily_pnl, _daily_loss_limit,
            )
            buy_reason = f"\u26a0 Daily loss limit ({_daily_pnl:.2f}\u20ac \u2264 {_daily_loss_limit:.2f}\u20ac)"
            buy_signal = False

        # 3. Panneau Rich — affiché à chaque cycle (signal ou non)
        _display_ibkr_buy_panel(pair, current_price, last, best, buy_signal, buy_reason, console)

        if _daily_blocked:
            return

        # 4. Exécuter l'achat si signal valide
        if buy_signal:
            nav = get_account_nav(client)
            if nav <= 0:
                logger.error("[IBKR] %s — NAV invalide (%.2f), BUY annulé", pair, nav)
                return
            atr = last.get("atr", 0.0)
            qty = _compute_position_size(
                nav, ibkr_cfg.risk_per_trade, float(atr) if atr else 0.0, current_price,
            )
            quote_qty = qty * current_price
            buy_result = safe_forex_buy(client, pair, quote_qty, current_price=current_price)
            if buy_result:
                entry_price = buy_result["entry_price"]
                real_qty = buy_result["quantity"]
                sl_price = entry_price - 3.0 * float(atr) if atr else entry_price * 0.98
                sl_price = max(sl_price, 0.0)
                sl_result = place_forex_stop_loss(client, pair, real_qty, sl_price)
                with _ibkr_state_lock:
                    pair_state["last_order_side"] = "BUY"
                    pair_state["entry_price"] = entry_price
                    pair_state["quantity"] = real_qty
                    pair_state["stop_loss"] = sl_price
                    pair_state["sl_order_id"] = sl_result["sl_order_id"] if sl_result else None
                    pair_state["sl_exchange_placed"] = sl_result is not None
                    pair_state["max_price"] = entry_price
                    pair_state["buy_timestamp"] = time.time()
                    pair_state["partial_taken_1"] = False
                    pair_state["partial_taken_2"] = False
                _save_state(ibkr_cfg, force=True)
                logger.info(
                    "[IBKR] %s BUY exécuté : qty=%.0f @%.5f SL=%.5f",
                    pair, real_qty, entry_price, sl_price,
                )
                try:
                    send_email_alert(
                        f"[IBKR-FOREX] BUY {pair} @{entry_price:.5f}",
                        f"Quantité : {real_qty:.0f}\nStop-loss : {sl_price:.5f}\n"
                        f"NAV : {nav:.2f} €",
                    )
                except Exception as mail_exc:
                    logger.warning("[IBKR] Email BUY %s ERREUR : %s", pair, mail_exc)

    # ── SELL ─────────────────────────────────────────────────────────────────
    elif in_position:
        entry_price = pair_state.get("entry_price", 0.0) or 0.0
        qty = pair_state.get("quantity", 0.0) or 0.0
        max_price = pair_state.get("max_price", current_price)
        atr = last.get("atr", 0.0)

        if current_price > (max_price or 0):
            with _ibkr_state_lock:
                pair_state["max_price"] = current_price

        stoch_rsi_val = last.get("stoch_rsi", 0.0)
        sell_signal = stoch_rsi_val > 0.4  # stoch_rsi_sell_exit

        # Panneau Rich — affiché à chaque cycle (signal ou non)
        _display_ibkr_sell_panel(
            pair, current_price, last, entry_price, qty, sell_signal, console, best=best
        )

        if sell_signal:
            with _ibkr_state_lock:
                sl_oid = pair_state.get("sl_order_id")
            if sl_oid:
                cancel_forex_order(client, pair, sl_oid)
            sell_result = safe_forex_sell(client, pair, qty, reason="SIGNAL")
            if sell_result:
                exit_price = sell_result["exit_price"]
                pnl = (exit_price - entry_price) * qty
                with _ibkr_state_lock:
                    pair_state["last_order_side"] = "SELL"
                    pair_state["entry_price"] = None
                    pair_state["quantity"] = None
                    pair_state["stop_loss"] = None
                    pair_state["sl_order_id"] = None
                    pair_state["sl_exchange_placed"] = False
                    pair_state["max_price"] = None
                    # Mise à jour du PnL journalier (pour daily_loss_limit)
                    _today_str = datetime.utcnow().strftime("%Y-%m-%d")
                    if bot_state.get("daily_pnl_date") != _today_str:
                        bot_state["daily_pnl"] = 0.0
                        bot_state["daily_pnl_date"] = _today_str
                    bot_state["daily_pnl"] = bot_state.get("daily_pnl", 0.0) + pnl
                _save_state(ibkr_cfg, force=True)
                logger.info("[IBKR] %s SELL : @%.5f PnL=%.2f €", pair, exit_price, pnl)
                try:
                    send_email_alert(
                        f"[IBKR-FOREX] SELL {pair} @{exit_price:.5f}",
                        f"PnL estimé : {pnl:+.2f} €\nNAV : {get_account_nav(client):.2f} €",
                    )
                except Exception as mail_exc:
                    logger.warning("[IBKR] Email SELL %s ERREUR : %s", pair, mail_exc)


# ─── Core trading loop par paire ──────────────────────────────────────────────

def _process_pair(
    pair_def: Dict[str, Any],
    client: IBKRForexClient,
    ibkr_cfg: IBKRConfig,
) -> None:
    """Exécute un cycle complet de trading pour une paire Forex.

    - Récupération données
    - Walk-Forward + OOS gates
    - Signal d'achat/vente
    - Placement d'ordres + SL
    - Persistance état
    """
    pair = pair_def["ibkr_pair"]
    periods_per_year = pair_def["periods_per_year"]

    with _get_pair_lock(pair):
        _ensure_pair_state(pair)

        with _ibkr_state_lock:
            pair_state = bot_state[pair]
            if bot_state.get("emergency_halt", False):
                logger.warning("[IBKR] %s — EMERGENCY HALT actif, trading suspendu", pair)
                return
            if pair_state.get("oos_blocked", False):
                logger.warning("[IBKR] %s — OOS blocked, achat bloqué", pair)

        # ── 1. Données OHLCV ──────────────────────────────────────────────
        try:
            df = fetch_forex_data(
                pair,
                interval="1h",
                start_date=_fresh_start_date(),
                client=client,
                cache_dir=ibkr_cfg.cache_dir,
            )
        except Exception as exc:
            logger.error("[IBKR] %s — fetch_forex_data ERREUR : %s", pair, exc)
            return

        if df is None or df.empty or len(df) < 200:
            logger.warning("[IBKR] %s — données insuffisantes (%d barres)", pair, len(df) if df is not None else 0)
            return

        # ── 1b. Resampling 4h depuis le 1h (zéro requête IBKR supplémentaire) ─
        # Lève le plafond bucket WF : top-2/tf × 2 timeframes = 4 candidats WF.
        import pandas as _pd
        try:
            _df_4h = (
                df
                .resample("4h")
                .agg({"open": "first", "high": "max", "low": "min",
                      "close": "last", "volume": "sum"})
                .dropna(subset=["close"])
            )
        except Exception as _resample_exc:
            logger.debug("[IBKR] %s — resampling 4h ignoré : %s", pair, _resample_exc)
            _df_4h = None
        df_by_tf: Dict[str, Any] = {"1h": df}
        if _df_4h is not None and len(_df_4h) >= 100:
            df_by_tf["4h"] = _df_4h
            logger.debug("[IBKR] %s — timeframe 4h ajouté (%d barres)", pair, len(_df_4h))

        # ── 2. Walk-Forward + sélection scénario ─────────────────────────
        best = _select_best_scenario(pair, df_by_tf, ibkr_cfg, periods_per_year)

        with _ibkr_state_lock:
            oos_blocked_before = pair_state.get("oos_blocked", False)

        if best is None:
            with _ibkr_state_lock:
                pair_state["oos_blocked"] = True
                pair_state["oos_blocked_since"] = time.time()
            _save_state(ibkr_cfg, force=True)
            return

        with _ibkr_state_lock:
            pair_state["oos_blocked"] = False
            pair_state["last_best_params"] = best

        # ── 3. Calculer indicateurs pour signal temps-réel ────────────────
        try:
            ema_periods = best.get("ema_periods", [18, 58])
            params = _scenario_params(best.get("scenario", ""))
            # Utiliser le timeframe du meilleur config pour le signal live
            _best_tf = best.get("timeframe", "1h")
            _df_signal = df_by_tf.get(_best_tf)
            _df_for_signal = _df_signal if _df_signal is not None else df_by_tf["1h"]
            df_ind = calculate_indicators(
                _df_for_signal.copy(),
                ema1_period=ema_periods[0],
                ema2_period=ema_periods[1],
                stoch_period=params.get("stoch_period", 14),
                sma_long=params.get("sma_long"),
                adx_period=params.get("adx_period"),
                trix_length=params.get("trix_length"),
                trix_signal=params.get("trix_signal"),
            )
        except Exception as exc:
            logger.error("[IBKR] %s — calculate_indicators ERREUR : %s", pair, exc)
            return

        if df_ind.empty:
            return

        last = df_ind.iloc[-2]  # bougie fermée — identique au bot Binance (iloc[-2])

        # Mise en cache pour le cycle live 2 minutes (identique _runtime.live_best_params Binance)
        with _ibkr_state_lock:
            _live_best_params[pair] = best
            _pair_last_indicators[pair] = last.copy()

        # ── 4-5. Signal + ordres ──────────────────────────────────────────
        _execute_pair_signal(pair, best, last, client, ibkr_cfg)

        # ── 6. Mise à jour état ───────────────────────────────────────────
        with _ibkr_state_lock:
            pair_state["last_run_time"] = datetime.utcnow().isoformat() + "Z"
            pair_state["execution_count"] = pair_state.get("execution_count", 0) + 1

        _save_state(ibkr_cfg)


# ─── Job principal schedulé ───────────────────────────────────────────────────

def _trading_job(client: IBKRForexClient, ibkr_cfg: IBKRConfig) -> None:
    """Exécute un cycle de trading sur toutes les paires Forex."""
    logger.info("[IBKR] ─── Cycle trading démarré ───")

    if bot_state.get("emergency_halt", False):
        reason = bot_state.get("emergency_halt_reason", "inconnu")
        logger.critical("[IBKR] EMERGENCY HALT actif — reason: %s", reason)
        return

    try:
        client.ensure_connected()
    except Exception as exc:
        logger.error("[IBKR] Reconnexion échouée : %s", exc)
        return

    for pair_def in FOREX_PAIRS:
        try:
            _process_pair(pair_def, client, ibkr_cfg)
        except Exception as exc:
            logger.error(
                "[IBKR] %s — exception non interceptée dans _process_pair : %s\n%s",
                pair_def["ibkr_pair"], exc, traceback.format_exc(),
            )

    write_heartbeat(ibkr_cfg.states_dir)
    logger.info("[IBKR] ─── Cycle terminé ───")


# ─── Cycle live 2 minutes (signal uniquement, sans backtest) ─────────────────

def _live_process_pair(
    pair_def: Dict[str, Any],
    client: "IBKRForexClient",
    ibkr_cfg: "IBKRConfig",
) -> None:
    """Cycle live 2 min : signal + ordres en utilisant les params WF mis en cache.

    Identique au pattern execute_live_trading_only du bot Binance :
    - N'exécute PAS de backtest (données déjà calculées lors du cycle 60 min)
    - Rafraîchit les indicateurs depuis les OHLCV récents (via cache — pas de nouvelle
      requête IBKR si le cache est frais) pour avoir StochRSI/ATR à jour
    - Non-bloquant : skip si le cycle 60 min tient encore le lock
    - oos_blocked bloque uniquement les nouveaux BUY ; les positions ouvertes
      continuent d'être surveillées pour les exits
    """
    pair = pair_def["ibkr_pair"]

    lock = _get_pair_lock(pair)
    if not lock.acquire(blocking=False):
        logger.debug("[IBKR-LIVE] %s — cycle 60 min en cours, skip live", pair)
        return
    try:
        _ensure_pair_state(pair)

        with _ibkr_state_lock:
            if bot_state.get("emergency_halt", False):
                return
            pair_state = bot_state[pair]
            in_position = (pair_state.get("last_order_side") == "BUY")
            oos_blocked = pair_state.get("oos_blocked", False)
            best = _live_best_params.get(pair)
            last = _pair_last_indicators.get(pair)

        # ─── IS best fallback + calcul indicateurs si cache absent ───────────────
        _best_disp = best if best is not None else _live_is_best_params.get(pair)
        _is_disp_only = (best is None and _best_disp is not None)
        # Quand OOS échoue, last est None (non calculé par _process_pair).
        # Calculer les indicateurs maintenant (depuis cache OHLCV) pour que le
        # panneau conditions soit toujours affiché -- identique au bot Binance.
        if last is None and _best_disp is not None:
            try:
                _ema_p = _best_disp.get("ema_periods", [18, 58])
                _params_d = _scenario_params(_best_disp.get("scenario", ""))
                _tf_d = _best_disp.get("timeframe", "1h")
                _df_d = fetch_forex_data(
                    pair, interval="1h", start_date=_fresh_start_date(),
                    client=client, cache_dir=ibkr_cfg.cache_dir,
                )
                if _df_d is not None and len(_df_d) >= 50:
                    if _tf_d == "4h":
                        _df_d = (
                            _df_d.resample("4h")
                            .agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"})
                            .dropna(subset=["close"])
                        )
                        if len(_df_d) < 50:
                            _df_d = None
                    if _df_d is not None:
                        _df_ind_d = calculate_indicators(
                            _df_d.copy(),
                            ema1_period=_ema_p[0],
                            ema2_period=_ema_p[1],
                            stoch_period=_params_d.get("stoch_period", 14),
                            sma_long=_params_d.get("sma_long"),
                            adx_period=_params_d.get("adx_period"),
                            trix_length=_params_d.get("trix_length"),
                            trix_signal=_params_d.get("trix_signal"),
                        )
                        if not _df_ind_d.empty:
                            last = _df_ind_d.iloc[-2]
                            with _ibkr_state_lock:
                                _pair_last_indicators[pair] = last.copy()
            except Exception as _ind_disp_err:
                logger.debug(
                    "[IBKR-LIVE] %s \u2014 calcul indicateurs affichage ignor\u00e9 : %s",
                    pair, _ind_disp_err,
                )

        # ─── [LIVE-ONLY] log ─────────────────────────────────────────────────
        _now_str = datetime.now().strftime("%H:%M:%S")
        _log_cfg = best if best is not None else _live_is_best_params.get(pair)
        if _log_cfg is not None:
            _ep = _log_cfg.get("ema_periods", ["?", "?"])
            _ep0 = _ep[0] if isinstance(_ep, (list, tuple)) and len(_ep) > 0 else "?"
            _ep1 = _ep[1] if isinstance(_ep, (list, tuple)) and len(_ep) > 1 else "?"
            _oos_tag = "" if best is not None else " [IS \u2014 OOS non valid\u00e9]"
            logger.info(
                "[LIVE-ONLY] %s @ %s \u2014 %s EMA(%s/%s) %s%s",
                pair, _now_str,
                _log_cfg.get("scenario", "?"), _ep0, _ep1,
                _log_cfg.get("timeframe", "1h"), _oos_tag,
            )
        else:
            logger.info("[LIVE-ONLY] %s @ %s \u2014 attente initialisation (cycle 60 min)", pair, _now_str)

        # ─── Panneau soldes IBKR (affiché à chaque cycle, avant les guards) ──
        _disp_price = 0.0
        try:
            _disp_price = get_current_price(client, pair)
        except Exception as _price_err:
            logger.debug("[IBKR-LIVE] %s \u2014 prix live indisponible : %s", pair, _price_err)
        # Fallback : utiliser le close des indicateurs si prix live indisponible
        if _disp_price <= 0 and last is not None:
            try:
                _close_fb = float(last.get("close", 0.0) or 0.0)
                if _close_fb > 0:
                    _disp_price = _close_fb
            except Exception:
                pass
        try:
            _display_ibkr_forex_balance_panel(
                pair, ibkr_cfg.initial_capital, _disp_price, in_position, pair_state, console
            )
        except Exception as _disp_err:
            logger.debug("[IBKR-LIVE] %s \u2014 affichage balance ignor\u00e9 : %s", pair, _disp_err)

        # ─── Panneau conditions BUY/SELL (affiché si params disponibles) ───────
        if _best_disp is not None and last is not None:
            try:
                _scenario = _best_disp.get("scenario", "StochRSI")
                if in_position:
                    _entry_px = float(pair_state.get("entry_price") or 0.0)
                    _qty = float(pair_state.get("quantity") or 0.0)
                    _stoch_val = float(last.get("stoch_rsi", 0.0) or 0.0)
                    _sell_sig = _stoch_val > 0.4
                    _display_ibkr_sell_panel(
                        pair, _disp_price, last, _entry_px, _qty, _sell_sig, console, best=_best_disp
                    )
                else:
                    _buy_sig, _buy_reason = _check_ibkr_buy_signal(
                        last, _scenario, _disp_price
                    )
                    _display_ibkr_buy_panel(
                        pair, _disp_price, last, _best_disp, _buy_sig, _buy_reason, console,
                        oos_blocked=_is_disp_only,
                    )
            except Exception as _cond_err:
                logger.debug("[IBKR-LIVE] %s \u2014 affichage conditions ignor\u00e9 : %s", pair, _cond_err)
        _run_signal = True
        # oos_blocked bloque les nouveaux achats mais pas le monitoring d'une
        # position déjà ouverte (les exits restent actifs).
        if oos_blocked and not in_position:
            logger.info(
                "[IBKR-LIVE] %s \u2014 OOS gates non valid\u00e9es, achat bloqu\u00e9 (2 min)", pair,
            )
            _run_signal = False

        if _run_signal and (best is None or last is None):
            logger.info(
                "[IBKR-LIVE] %s \u2014 params non encore initialis\u00e9s (en attente du cycle 60 min)",
                pair,
            )
            _run_signal = False

        if _run_signal:
            assert best is not None and last is not None  # garanti par les guards ci-dessus
            # ── Rafraîchir les indicateurs depuis les OHLCV récents ──────────
            # Identique à execute_real_trades du bot Binance : recalcul à chaque
            # cycle pour avoir StochRSI, ATR et EMAs sur la dernière bougie fermée.
            # fetch_forex_data utilise le cache (TTL 30 j) — requête IBKR uniquement
            # si la dernière bougie 1h est absente du cache.
            try:
                df_fresh = fetch_forex_data(
                    pair,
                    interval="1h",
                    start_date=_fresh_start_date(),
                    client=client,
                    cache_dir=ibkr_cfg.cache_dir,
                )
                if df_fresh is not None and len(df_fresh) >= 50:
                    ema_periods = best.get("ema_periods", [18, 58])
                    params = _scenario_params(best.get("scenario", ""))
                    best_tf = best.get("timeframe", "1h")
                    if best_tf == "4h":
                        import pandas as _pd
                        df_signal: Any = (
                            df_fresh
                            .resample("4h")
                            .agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"})
                            .dropna(subset=["close"])
                        )
                        if len(df_signal) < 50:
                            df_signal = df_fresh
                    else:
                        df_signal = df_fresh
                    df_ind = calculate_indicators(
                        df_signal.copy(),
                        ema1_period=ema_periods[0],
                        ema2_period=ema_periods[1],
                        stoch_period=params.get("stoch_period", 14),
                        sma_long=params.get("sma_long"),
                        adx_period=params.get("adx_period"),
                        trix_length=params.get("trix_length"),
                        trix_signal=params.get("trix_signal"),
                    )
                    if not df_ind.empty:
                        last = df_ind.iloc[-2]  # bougie fermée (identique bot Binance)
                        with _ibkr_state_lock:
                            _pair_last_indicators[pair] = last.copy()
            except Exception as exc:
                logger.debug(
                    "[IBKR-LIVE] %s \u2014 rafra\u00eechissement indicateurs ignor\u00e9, utilisation cache : %s",
                    pair, exc,
                )

            _execute_pair_signal(pair, best, last, client, ibkr_cfg)

        # ─── Panneau planification (affiché à chaque cycle) ──────────────────
        _now_exec = datetime.now()
        _next_exec = _now_exec + timedelta(minutes=2)
        _display_ibkr_planning_panel(_now_exec, _next_exec, console)

        with _ibkr_state_lock:
            bot_state[pair]["last_live_time"] = datetime.utcnow().isoformat() + "Z"
        _save_state(ibkr_cfg)
    finally:
        lock.release()


def _live_trading_job(client: "IBKRForexClient", ibkr_cfg: "IBKRConfig") -> None:
    """Cycle live toutes les 2 minutes : signal + ordres sans backtest.

    Identique à _dispatch_live_parallel du bot Binance (execute_live_trading_only).
    """
    logger.info("[IBKR] ─── Cycle signal (2 min) ───")

    if bot_state.get("emergency_halt", False):
        reason = bot_state.get("emergency_halt_reason", "inconnu")
        logger.critical("[IBKR] EMERGENCY HALT actif — reason: %s", reason)
        return

    try:
        client.ensure_connected()
    except Exception as exc:
        logger.error("[IBKR] Reconnexion échouée (live) : %s", exc)
        return

    for pair_def in FOREX_PAIRS:
        try:
            _live_process_pair(pair_def, client, ibkr_cfg)
        except Exception as exc:
            logger.error(
                "[IBKR] %s — exception dans _live_process_pair : %s\n%s",
                pair_def["ibkr_pair"], exc, traceback.format_exc(),
            )

    write_heartbeat(ibkr_cfg.states_dir)
    logger.info("[IBKR] ─── Cycle signal terminé ───")


# ─── Initialisation ───────────────────────────────────────────────────────────

def _init_bot(ibkr_cfg: IBKRConfig) -> None:
    """Charge l'état persisté et initialise les structures de données."""
    global bot_state
    try:
        loaded = load_ibkr_state(
            states_dir=ibkr_cfg.states_dir,
            state_file=ibkr_cfg.state_file,
            ibkr_secret=ibkr_cfg.ibkr_secret,
        )
        with _ibkr_state_lock:
            bot_state.update(loaded)
        logger.info("[IBKR] État chargé : %d clés", len(bot_state))
    except IBKRStateError as exc:
        logger.error("[IBKR] Impossible de charger l'état (HMAC mismatch ?) : %s", exc)
        logger.warning("[IBKR] Démarrage avec état vide")

    # Initialiser les paires manquantes
    for pair_def in FOREX_PAIRS:
        _ensure_pair_state(pair_def["ibkr_pair"])

    _save_state(ibkr_cfg, force=True)


# ─── Point d'entrée ───────────────────────────────────────────────────────────

def main() -> None:
    """Point d'entrée principal du bot IBKR Forex.

    Charge la config, se connecte à IB Gateway, puis lance le scheduler.
    """
    logger.info("[IBKR] ===================================================")
    logger.info("[IBKR]   IBKR FOREX BOT -- demarrage")
    logger.info("[IBKR]   Mode : %s", "PAPER" if os.environ.get("IBKR_PAPER_MODE", "true").lower() == "true" else "LIVE")
    logger.info("[IBKR] ===================================================")

    # Charger la config depuis les env vars
    ibkr_cfg = IBKRConfig.from_env()
    logger.info("[IBKR] Config chargée : %r", ibkr_cfg)

    # Connexion IB Gateway
    client = IBKRForexClient(ibkr_cfg.host, ibkr_cfg.port, ibkr_cfg.client_id, ibkr_cfg.account)
    try:
        client.connect()
    except Exception as exc:
        logger.critical("[IBKR] Connexion IB Gateway impossible : %s", exc)
        raise SystemExit(1) from exc

    # Chargement état + init paires
    _init_bot(ibkr_cfg)

    # Planifier le job
    interval = ibkr_cfg.schedule_interval_minutes
    logger.info("[IBKR] Scheduler : toutes les %d minutes (backtest+WF) + toutes les 2 minutes (live)", interval)
    # Tâche 1 : backtest + WF + signal → toutes les 60 minutes (identique _dispatch_scheduled_parallel Binance)
    schedule.every(interval).minutes.do(_trading_job, client=client, ibkr_cfg=ibkr_cfg)
    # Tâche 2 : signal live uniquement → toutes les 2 minutes (identique _dispatch_live_parallel Binance)
    schedule.every(2).minutes.do(_live_trading_job, client=client, ibkr_cfg=ibkr_cfg)

    # Exécuter immédiatement au démarrage
    _trading_job(client, ibkr_cfg)
    _live_trading_job(client, ibkr_cfg)  # premier cycle live sans attendre 2 min

    # Boucle infinie
    try:
        while True:
            if bot_state.get("emergency_halt", False):
                logger.critical("[IBKR] EMERGENCY HALT — arrêt du scheduler")
                break
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("[IBKR] Arrêt manuel (KeyboardInterrupt)")
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        _save_state(ibkr_cfg, force=True)
        logger.info("[IBKR] Bot arrêté proprement")


if __name__ == "__main__":
    main()
