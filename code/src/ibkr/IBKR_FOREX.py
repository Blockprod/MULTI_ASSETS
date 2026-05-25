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
# Forex : Sharpe ≥ 0.0 = rendement positif ajusté au risque (paper trading acceptable).
# Fenêtre IS réduite à 2 ans pour plus de poids au régime récent.
_os.environ.setdefault("OOS_SHARPE_MIN", "0.0")            # Forex : Sharpe ≥ 0 (vs 0.8 crypto)
_os.environ.setdefault("OOS_WIN_RATE_MIN", "25.0")         # Forex (vs 30.0 crypto)
# Aligner le risk/trade backtest sur le live IBKR (défaut Binance = 5%, IBKR = 5.5%)
_os.environ.setdefault("RISK_PER_TRADE", "0.055")           # Alignement backtest ↔ live IBKR
# Les vars email (communes aux deux bots)
# SENDER_EMAIL, RECEIVER_EMAIL, GOOGLE_MAIL_PASSWORD doivent être dans .env.ibkr

# ─── Imports standard ─────────────────────────────────────────────────────────
import logging
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
    safe_forex_short_open, place_forex_stop_buy, safe_forex_cover,
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

# ─── Throttle alertes email erreur prix (max 1/30 min par paire) ─────────────
_price_error_last_sent: Dict[str, float] = {}

# ─── Throttle alertes email reconnexion (max 1/5 min) ───────────────────────────
_reconnect_alert_last_sent: Dict[str, float] = {}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fresh_start_date() -> str:
    """Fenêtre glissante 730 jours (2 ans) — jamais figée à l'import (règle absolue).

    Réduit à 2 ans (vs 3 ans Binance) pour donner plus de poids au régime récent
    et augmenter le ratio trades/fold OOS lors du Walk-Forward.
    """
    return (datetime.today() - timedelta(days=730)).strftime("%d %b %Y")


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
                "min_price": None,
                "partial_taken_1": False,
                "partial_taken_2": False,
                "buy_timestamp": 0.0,
                "breakeven_activated": False,
            }
        else:
            # Rétro-compat : ajouter les champs manquants pour les états existants
            ps = bot_state[pair]
            if "min_price" not in ps:
                ps["min_price"] = None
            if "breakeven_activated" not in ps:
                ps["breakeven_activated"] = False


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
            allow_short=ibkr_cfg.allow_short,
            prepare_base_dataframe_fn=prepare_fn,
        )
    except Exception as exc:
        logger.error("[IBKR] %s — run_all_backtests ERREUR : %s", pair, exc)
        return None

    if not results:
        logger.warning("[IBKR] %s — aucun résultat backtest IS", pair)
        return None

    # Stocker le IS best pour l'affichage 2 min même quand OOS échoue.
    # Tri explicite par profit IS décroissant : run_all_backtests retourne les
    # résultats dans l'ordre de complétion des futures (non déterministe).
    _is_sorted = sorted(
        results,
        key=lambda r: r.get("final_wallet", 0.0) - r.get("initial_wallet", 0.0),
        reverse=True,
    )
    with _ibkr_state_lock:
        _live_is_best_params[pair] = _is_sorted[0]

    # Afficher le tableau IS identique au bot Binance
    try:
        import sys as _sys
        import os as _os
        _src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        if _src_dir not in _sys.path:
            _sys.path.insert(0, _src_dir)
        from display_ui import display_results_for_pair
        display_results_for_pair(pair, results, start_date_override=start_date)
    except Exception as _disp_exc:
        logger.debug("[IBKR] Affichage tableau IS ignoré : %s", _disp_exc)

    # 2. Walk-Forward OOS — informatif + gate prioritaire si validé
    # Stratégie : WF valide → config WF retournée (OOS est la validation la plus robuste).
    # WF échoue → fallback validation IS (folds ~15-20 trades → Sharpe trop bruité pour gater).
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
            n_folds=2,  # 2 folds × ~136j 4h ≈ 11-15 trades/fold (vs 3×90j ≈ 7 trades — trop bruité)
        )
        if wf_result.get("any_passed"):
            best = wf_result["best_wf_config"]
            logger.info(
                "[IBKR] %s — WF validé : %s EMA(%s,%s) Sharpe OOS=%.3f",
                pair, best.get("scenario"), best.get("ema_periods", ["?", "?"])[0],
                best.get("ema_periods", ["?", "?"])[1], best.get("avg_oos_sharpe", 0.0),
            )
            return best
        else:
            logger.info("[IBKR] %s — WF OOS non concluant — validation IS en cours", pair)
    except Exception as exc:
        logger.error("[IBKR] %s — run_walk_forward_validation ERREUR : %s", pair, exc)

    # 3. Validation IS — fallback quand WF OOS insuffisant (≤ 20 trades/fold)
    # Critères : IS Profit > 0, IS WinRate ≥ 25%, IS Sharpe > 0
    is_best = _is_sorted[0]
    _is_profit = is_best.get("final_wallet", 0.0) - is_best.get("initial_wallet", 0.0)
    _is_wr = is_best.get("win_rate", 0.0)       # pourcentage ex: 31.03 (pas décimal)
    _is_sharpe = is_best.get("sharpe_ratio", 0.0)

    if not (_is_profit > 0 and _is_wr >= 35.0 and _is_sharpe >= 0.5):
        _block_detail = "achat et SHORT bloqués" if ibkr_cfg.allow_short else "achat bloqué"
        logger.warning(
            "[IBKR] %s — IS gates FAIL : profit=%.0f$, WR=%.1f%%, Sharpe=%.2f "
            "— %s jusqu'au prochain cycle",
            pair, _is_profit, _is_wr, _is_sharpe, _block_detail,
        )
        return None

    best = {
        "scenario": is_best.get("scenario", "StochRSI"),
        "ema_periods": list(is_best.get("ema_periods", [18, 58])),
        "timeframe": is_best.get("timeframe", "1h"),
        "avg_oos_sharpe": _is_sharpe,
        "validation_mode": "IS",
    }
    logger.info(
        "[IBKR] %s — IS validé (WF non concluant) : %s EMA(%s,%s) Sharpe IS=%.3f WR=%.1f%%",
        pair, best["scenario"], best["ema_periods"][0],
        best["ema_periods"][1], _is_sharpe, _is_wr,
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
    return True, "[OK] Signal d'achat valide"


def _ok_mark(cond: bool) -> str:
    """✔ vert / ✘ rouge pour les panneaux Rich."""
    return "[bold green]\u2714 OK[/bold green]" if cond else "[bold red]\u2718 NOK[/bold red]"


def _check_ibkr_sell_signal(
    last: "Any",
    scenario: str,
    ibkr_cfg: "IBKRConfig",
) -> "Tuple[bool, str]":
    """Évalue la condition de sortie LONG (stoch > sell_exit seuil).

    Retourne (signal_valide, raison_détaillée).
    """
    stoch = float(last.get("stoch_rsi", 0.0) or 0.0)
    threshold = ibkr_cfg.stoch_rsi_sell_exit

    if stoch > threshold:
        return True, f"[OK] StochRSI ({stoch:.3f}) > {threshold:.2f} — sortie LONG"
    return False, f"StochRSI ({stoch:.3f}) \u2264 {threshold:.2f} — maintien LONG"


def _check_ibkr_short_signal(
    last: "Any",
    scenario: str,
    current_price: float,
    ibkr_cfg: "IBKRConfig",
) -> "Tuple[bool, str]":
    """Évalue les conditions d'entrée SHORT Forex — miroir inversé du signal BUY.

    Signaux requis : EMA1 < EMA2 (tendance baissière) ET stoch > 0.80
    (surachat, retournement attendu à la baisse) + filtres scénario inversés.

    Retourne (signal_valide, raison_détaillée).
    """
    ema1 = float(last.get("ema1", 0.0) or 0.0)
    ema2 = float(last.get("ema2", 0.0) or 0.0)
    stoch = float(last.get("stoch_rsi", 0.0) or 0.0)
    threshold = ibkr_cfg.stoch_rsi_short_entry

    if not (ema1 < ema2):
        return False, f"EMA1 ({ema1:.5f}) \u2265 EMA2 ({ema2:.5f}) — pas baissier"
    if not (stoch > threshold):
        return False, f"StochRSI ({stoch:.3f}) \u2264 {threshold:.2f} (pas en surachat)"

    if scenario == "StochRSI_SMA":
        sma_long = last.get("sma_long")
        if sma_long is not None and current_price > float(sma_long):
            return False, f"Prix ({current_price:.5f}) > SMA200 ({float(sma_long):.5f}) — filtre haussier"
    if scenario == "StochRSI_ADX":
        adx = float(last.get("adx", 0.0) or 0.0)
        if adx < 25.0:
            return False, f"ADX ({adx:.2f}) < 25 — tendance trop faible"
    if scenario == "StochRSI_TRIX":
        trix_histo = last.get("TRIX_HISTO")
        trix_val = float(trix_histo) if trix_histo is not None else float("nan")
        if trix_histo is None or trix_val >= 0:
            return False, f"TRIX_HISTO ({trix_val:.5f}) \u2265 0 — pas baissier"

    return True, "[OK] Signal SHORT valide"


def _check_ibkr_cover_signal(
    last: "Any",
    scenario: str,
    ibkr_cfg: "IBKRConfig",
) -> "Tuple[bool, str]":
    """Évalue la condition de rachat SHORT (cover) : stoch < cover_exit seuil.

    Retourne (signal_valide, raison_détaillée).
    """
    stoch = float(last.get("stoch_rsi", 1.0) or 1.0)
    threshold = ibkr_cfg.stoch_rsi_cover_exit

    if stoch < threshold:
        return True, f"[OK] StochRSI ({stoch:.3f}) < {threshold:.2f} — rachat SHORT"
    return False, f"StochRSI ({stoch:.3f}) \u2265 {threshold:.2f} — maintien SHORT"


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
    *,
    paper_mode: bool = False,
    is_connected: bool = True,
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

        _conn_str = (
            "[bold green]Connecté[/bold green]"
            if is_connected
            else "[bold red]Déconnecté — reconnexion au prochain cycle[/bold red]"
        )
        grid.add_row("Statut IB Gateway", _conn_str)
        grid.add_row("", "")
        grid.add_row("Dernière exécution", last_exec_dt.strftime("%Y-%m-%d %H:%M:%S"))
        grid.add_row("Temps écoulé", elapsed_str)
        grid.add_row("", "")
        _mode_tag = "Paper" if paper_mode else "Live"
        grid.add_row("Mode de planification", f"{_mode_tag}: 2 min | Backtest+WF: 60 min")
        grid.add_row(
            "Prochaine exécution",
            f"{_mode_tag} toutes les 2 min ({next_exec_dt.strftime('%H:%M:%S')})",
        )

        con.print(Panel(
            grid,
            title="[bold white]SUIVI D\u2019EXÉCUTION & PLANIFICATION AUTOMATIQUE[/bold white]",
            border_style="dim",
            padding=(1, 2),
        ))
    except Exception as _err:
        logger.debug("[IBKR] _display_ibkr_planning_panel erreur : %s", _err)


# ─── Trailing stop ATR ────────────────────────────────────────────────────────

def _update_ibkr_trailing_stop(
    pair: str,
    pair_state: "Dict[str, Any]",
    current_price: float,
    atr: float,
    client: "IBKRForexClient",
    ibkr_cfg: "IBKRConfig",
) -> None:
    """Met à jour le trailing stop ATR pour LONG et SHORT.

    LONG : high-water mark (max_price), activation à +trailing_activation×ATR,
           trailing = max_price - trailing×ATR, breakeven à +breakeven_pct.
    SHORT: low-water mark (min_price), activation à -trailing_activation×ATR,
           trailing = min_price + trailing×ATR.

    Annule l'ancien SL exchange et en place un nouveau si le trailing bouge.
    """
    if atr <= 0 or current_price <= 0:
        return

    entry_price = float(pair_state.get("entry_price") or 0.0)
    qty = float(pair_state.get("quantity") or 0.0)
    if entry_price <= 0 or qty <= 0:
        return

    is_short = pair_state.get("last_order_side") == "SHORT"
    trailing_dist = ibkr_cfg.atr_multiplier_trailing * atr
    activation_dist = ibkr_cfg.trailing_activation_multiplier * atr
    current_sl = float(pair_state.get("stop_loss") or 0.0)
    old_sl_id = pair_state.get("sl_order_id")

    if not is_short:
        # ── LONG ──────────────────────────────────────────────────────────────
        max_p = float(pair_state.get("max_price") or entry_price)
        if current_price > max_p:
            max_p = current_price
            with _ibkr_state_lock:
                pair_state["max_price"] = max_p

        # Breakeven
        if (not pair_state.get("breakeven_activated")
                and current_price >= entry_price * (1 + ibkr_cfg.breakeven_pct)):
            new_sl = entry_price
            if new_sl > current_sl:
                with _ibkr_state_lock:
                    pair_state["breakeven_activated"] = True
                    pair_state["stop_loss"] = new_sl
                if old_sl_id:
                    cancel_forex_order(client, pair, old_sl_id)
                sl_result = place_forex_stop_loss(client, pair, qty, new_sl)
                with _ibkr_state_lock:
                    pair_state["sl_order_id"] = sl_result["sl_order_id"] if sl_result else None
                    pair_state["sl_exchange_placed"] = sl_result is not None
                logger.info("[IBKR] %s Breakeven SL → %.5f", pair, new_sl)
                try:
                    send_email_alert(
                        f"[IBKR-FOREX] Breakeven activé — {pair}",
                        f"Stop-loss relevé au breakeven (prix d'entrée).\n"
                        f"Nouveau SL : {new_sl:.5f}\n"
                        f"Prix actuel : {current_price:.5f}",
                    )
                except Exception as _mail_exc:
                    logger.warning("[IBKR] Email Breakeven %s ERREUR : %s", pair, _mail_exc)

        # Trailing activation
        if current_price >= entry_price + activation_dist:
            new_trailing = max_p - trailing_dist
            if not pair_state.get("trailing_stop_activated") or new_trailing > (pair_state.get("trailing_stop") or 0.0):
                with _ibkr_state_lock:
                    pair_state["trailing_stop_activated"] = True
                    pair_state["trailing_stop"] = new_trailing
                    old_id = pair_state.get("sl_order_id")
                    pair_state["stop_loss"] = new_trailing
                if old_id:
                    cancel_forex_order(client, pair, old_id)
                sl_result = place_forex_stop_loss(client, pair, qty, new_trailing)
                with _ibkr_state_lock:
                    pair_state["sl_order_id"] = sl_result["sl_order_id"] if sl_result else None
                    pair_state["sl_exchange_placed"] = sl_result is not None
                logger.info("[IBKR] %s Trailing LONG SL → %.5f", pair, new_trailing)
                try:
                    send_email_alert(
                        f"[IBKR-FOREX] Trailing LONG activé — {pair}",
                        f"Trailing stop LONG mis à jour.\n"
                        f"Nouveau SL trailing : {new_trailing:.5f}\n"
                        f"Prix actuel : {current_price:.5f}",
                    )
                except Exception as _mail_exc:
                    logger.warning("[IBKR] Email Trailing LONG %s ERREUR : %s", pair, _mail_exc)

    else:
        # ── SHORT ─────────────────────────────────────────────────────────────
        min_p = float(pair_state.get("min_price") or entry_price)
        if current_price < min_p:
            min_p = current_price
            with _ibkr_state_lock:
                pair_state["min_price"] = min_p

        # Trailing activation (below entry)
        if current_price <= entry_price - activation_dist:
            new_trailing = min_p + trailing_dist
            cur_trailing = pair_state.get("trailing_stop") or float("inf")
            if not pair_state.get("trailing_stop_activated") or new_trailing < cur_trailing:
                with _ibkr_state_lock:
                    pair_state["trailing_stop_activated"] = True
                    pair_state["trailing_stop"] = new_trailing
                    old_id = pair_state.get("sl_order_id")
                    pair_state["stop_loss"] = new_trailing
                if old_id:
                    cancel_forex_order(client, pair, old_id)
                sl_result = place_forex_stop_buy(client, pair, qty, new_trailing)
                with _ibkr_state_lock:
                    pair_state["sl_order_id"] = sl_result["sl_order_id"] if sl_result else None
                    pair_state["sl_exchange_placed"] = sl_result is not None
                logger.info("[IBKR] %s Trailing SHORT SL → %.5f", pair, new_trailing)
                try:
                    send_email_alert(
                        f"[IBKR-FOREX] Trailing SHORT activé — {pair}",
                        f"Trailing stop SHORT mis à jour.\n"
                        f"Nouveau SL trailing : {new_trailing:.5f}\n"
                        f"Prix actuel : {current_price:.5f}",
                    )
                except Exception as _mail_exc:
                    logger.warning("[IBKR] Email Trailing SHORT %s ERREUR : %s", pair, _mail_exc)


# ─── Partiels ─────────────────────────────────────────────────────────────────

def _execute_ibkr_partial_exit(
    pair: str,
    pair_state: "Dict[str, Any]",
    current_price: float,
    client: "IBKRForexClient",
    ibkr_cfg: "IBKRConfig",
) -> None:
    """Exécute les sorties partielles (2 niveaux) pour LONG et SHORT.

    Niveaux : partial_threshold_1 (défaut 2%) → vend partial_pct_1 (50%)
              partial_threshold_2 (défaut 4%) → vend partial_pct_2 (30%)

    Re-place le SL sur la quantité restante après chaque partiel.
    """
    entry_price = float(pair_state.get("entry_price") or 0.0)
    qty = float(pair_state.get("quantity") or 0.0)
    if entry_price <= 0 or qty <= 0 or current_price <= 0:
        return

    is_short = pair_state.get("last_order_side") == "SHORT"

    if is_short:
        profit_pct = (entry_price - current_price) / entry_price
    else:
        profit_pct = (current_price - entry_price) / entry_price

    # 1er partiel
    if (not pair_state.get("partial_taken_1")
            and profit_pct >= ibkr_cfg.partial_threshold_1):
        partial_qty = _round_to_lot_ibkr(qty * ibkr_cfg.partial_pct_1)
        if partial_qty > 0:
            if is_short:
                result = safe_forex_cover(client, pair, partial_qty, reason="PARTIAL-1")
            else:
                result = safe_forex_sell(client, pair, partial_qty, reason="PARTIAL-1")
            if result:
                remaining_qty = qty - partial_qty
                old_sl_id = pair_state.get("sl_order_id")
                if old_sl_id:
                    cancel_forex_order(client, pair, old_sl_id)
                sl_price = float(pair_state.get("stop_loss") or 0.0)
                if sl_price > 0 and remaining_qty > 0:
                    if is_short:
                        sl_result = place_forex_stop_buy(client, pair, remaining_qty, sl_price)
                    else:
                        sl_result = place_forex_stop_loss(client, pair, remaining_qty, sl_price)
                    with _ibkr_state_lock:
                        pair_state["sl_order_id"] = sl_result["sl_order_id"] if sl_result else None
                with _ibkr_state_lock:
                    pair_state["quantity"] = remaining_qty
                    pair_state["partial_taken_1"] = True
                logger.info(
                    "[IBKR] %s PARTIAL-1 %.0f unités @%.5f (profit=%.2f%%)",
                    pair, partial_qty, current_price, profit_pct * 100,
                )
                try:
                    send_email_alert(
                        f"[IBKR-FOREX] PARTIAL-1 {pair} @{current_price:.5f}",
                        f"Vente partielle 1 : {partial_qty:.0f} unités\n"
                        f"Prix : {current_price:.5f}\n"
                        f"Profit : {profit_pct * 100:.2f}%\n"
                        f"Restant : {remaining_qty:.0f} unités",
                    )
                except Exception as _mail_exc:
                    logger.warning("[IBKR] Email PARTIAL-1 %s ERREUR : %s", pair, _mail_exc)

    # 2e partiel
    if (not pair_state.get("partial_taken_2")
            and pair_state.get("partial_taken_1")
            and profit_pct >= ibkr_cfg.partial_threshold_2):
        qty_now = float(pair_state.get("quantity") or 0.0)
        partial_qty = _round_to_lot_ibkr(qty_now * ibkr_cfg.partial_pct_2)
        if partial_qty > 0:
            if is_short:
                result = safe_forex_cover(client, pair, partial_qty, reason="PARTIAL-2")
            else:
                result = safe_forex_sell(client, pair, partial_qty, reason="PARTIAL-2")
            if result:
                remaining_qty = qty_now - partial_qty
                old_sl_id = pair_state.get("sl_order_id")
                if old_sl_id:
                    cancel_forex_order(client, pair, old_sl_id)
                sl_price = float(pair_state.get("stop_loss") or 0.0)
                if sl_price > 0 and remaining_qty > 0:
                    if is_short:
                        sl_result = place_forex_stop_buy(client, pair, remaining_qty, sl_price)
                    else:
                        sl_result = place_forex_stop_loss(client, pair, remaining_qty, sl_price)
                    with _ibkr_state_lock:
                        pair_state["sl_order_id"] = sl_result["sl_order_id"] if sl_result else None
                with _ibkr_state_lock:
                    pair_state["quantity"] = remaining_qty
                    pair_state["partial_taken_2"] = True
                logger.info(
                    "[IBKR] %s PARTIAL-2 %.0f unités @%.5f (profit=%.2f%%)",
                    pair, partial_qty, current_price, profit_pct * 100,
                )
                try:
                    send_email_alert(
                        f"[IBKR-FOREX] PARTIAL-2 {pair} @{current_price:.5f}",
                        f"Vente partielle 2 : {partial_qty:.0f} unités\n"
                        f"Prix : {current_price:.5f}\n"
                        f"Profit : {profit_pct * 100:.2f}%\n"
                        f"Restant : {remaining_qty:.0f} unités",
                    )
                except Exception as _mail_exc:
                    logger.warning("[IBKR] Email PARTIAL-2 %s ERREUR : %s", pair, _mail_exc)


def _round_to_lot_ibkr(qty: float, lot_size: float = 20_000.0) -> float:
    """Arrondit la quantité au lot IBKR inférieur (helper interne)."""
    import math
    return math.floor(qty / lot_size) * lot_size if lot_size > 0 else qty


# ─── Panneau Rich SHORT ──────────────────────────────────────────────────────

def _display_ibkr_short_entry_panel(
    pair: str,
    current_price: float,
    last: "Any",
    short_signal: bool,
    short_reason: str,
    con: "Console",
    best: "Optional[Dict[str, Any]]" = None,
) -> None:
    """Panneau Rich des conditions d'ENTRÉE SHORT — affiché en mode informatif (OOS bloqué).

    Symétrique de _display_ibkr_buy_panel : montre EMA1<EMA2, StochRSI>80%
    et les filtres scénario inversés. NE PAS confondre avec _display_ibkr_short_panel
    qui affiche les conditions de SORTIE (cover) d'une position déjà ouverte.
    """
    try:
        ema_periods = best.get("ema_periods", ["?", "?"]) if best else ["?", "?"]
        ema1_p = ema_periods[0] if isinstance(ema_periods, (list, tuple)) and len(ema_periods) > 0 else "?"
        ema2_p = ema_periods[1] if isinstance(ema_periods, (list, tuple)) and len(ema_periods) > 1 else "?"
        tf = best.get("timeframe", "?") if best else "?"
        scenario = best.get("scenario", "StochRSI") if best else "StochRSI"
        strategy_label = f"{scenario} EMA({ema1_p}/{ema2_p}) {tf}"

        ema1 = float(last.get("ema1", 0.0) or 0.0)
        ema2 = float(last.get("ema2", 0.0) or 0.0)
        stoch = float(last.get("stoch_rsi", float("nan")) or float("nan"))
        threshold = 0.80

        grid = Table(
            title="[bold white]Scan conditions d'entrée SHORT[/bold white]",
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
            f"EMA{ema1_p} < EMA{ema2_p}",
            _ok_mark(ema1 < ema2),
            f"EMA{ema1_p}={ema1:.5f}  EMA{ema2_p}={ema2:.5f}",
        )
        grid.add_row(
            f"StochRSI > {threshold * 100:.0f}%",
            _ok_mark(not (stoch != stoch) and stoch > threshold),
            f"{stoch * 100:.1f}%",
        )
        grid.add_row("StochRSI actuel", "", f"[bold white]{stoch * 100:.2f}[/bold white]")

        if scenario == "StochRSI_SMA":
            sma = last.get("sma_long")
            sma_f = float(sma) if sma is not None else None
            grid.add_row(
                "Prix < SMA200",
                _ok_mark(sma_f is not None and current_price < sma_f),
                f"SMA200={sma_f:.5f}" if sma_f is not None else "N/A",
            )
        if scenario == "StochRSI_ADX":
            adx = float(last.get("adx", 0.0) or 0.0)
            grid.add_row("ADX > 25", _ok_mark(adx > 25.0), f"ADX={adx:.2f}")
        if scenario == "StochRSI_TRIX":
            trix = last.get("TRIX_HISTO")
            trix_f = float(trix) if trix is not None else None
            grid.add_row(
                "TRIX_HISTO < 0",
                _ok_mark(trix_f is not None and trix_f < 0),
                f"TRIX={trix_f:.5f}" if trix_f is not None else "N/A",
            )

        grid.add_row("", "", "")
        grid.add_row(
            "OOS gates",
            "[bold red]✘ BLOQUÉ[/bold red]",
            "Entrée SHORT suspendue — analyse IS (informatif)",
        )

        panel_title = (
            f"[bold magenta]SIGNAL SHORT [{pair}] — ENTRÉE DÉTECTÉE (OOS bloqué)[/bold magenta]"
            if short_signal else
            f"[bold yellow]SCAN SHORT [{pair}] — OOS BLOQUÉ (informatif)[/bold yellow]"
        )
        con.print(Panel(
            grid,
            title=panel_title,
            border_style="magenta" if short_signal else "yellow",
            padding=(1, 2),
        ))
    except Exception as _panel_err:
        logger.debug("[IBKR] _display_ibkr_short_entry_panel erreur : %s", _panel_err)


def _display_ibkr_short_panel(
    pair: str,
    current_price: float,
    last: "Any",
    entry_price: float,
    qty: float,
    cover_signal: bool,
    con: "Console",
    best: "Optional[Dict[str, Any]]" = None,
) -> None:
    """Panneau Rich des conditions de rachat SHORT IBKR."""
    try:
        stoch = float(last.get("stoch_rsi", float("nan")) or float("nan"))
        pnl_latent = (entry_price - current_price) * qty if entry_price and qty else 0.0
        pnl_pct = (
            (entry_price - current_price) / entry_price * 100
            if entry_price and entry_price > 0 else 0.0
        )

        grid = Table(
            title="[bold white]Analyse des conditions de rachat SHORT[/bold white]",
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
        grid.add_row("Prix d'entrée SHORT", "", f"{entry_price:.5f}")
        grid.add_row("StochRSI < 20%", _ok_mark(stoch < 0.20), f"{stoch * 100:.1f}%")

        _pnl_color = "bold green" if pnl_latent >= 0 else "bold red"
        grid.add_row(
            "PnL latent SHORT", "",
            f"[{_pnl_color}]{pnl_latent:+.2f} € ({pnl_pct:+.2f}%)[/{_pnl_color}]",
        )

        panel_title = (
            f"[bold magenta]SIGNAL COVER [{pair}] — CONDITIONS REMPLIES[/bold magenta]"
            if cover_signal else
            f"[bold yellow]SCAN SHORT [{pair}] — EN POSITION SHORT (pas de signal)[/bold yellow]"
        )
        con.print(Panel(
            grid,
            title=panel_title,
            border_style="magenta" if cover_signal else "yellow",
            padding=(1, 2),
        ))
    except Exception as _panel_err:
        logger.debug("[IBKR] _display_ibkr_short_panel erreur : %s", _panel_err)


# ─── Signal BUY/SELL partagé (60 min + 2 min) ────────────────────────────────

def _execute_pair_signal(
    pair: str,
    best: "Dict[str, Any]",
    last: "Any",  # pd.Series — bougie fermée iloc[-2]
    client: "IBKRForexClient",
    ibkr_cfg: "IBKRConfig",
) -> None:
    """Évalue le signal et exécute les ordres BUY/SELL/SHORT/COVER.

    Appelée depuis _process_pair (cycle 60 min) ET _live_process_pair (cycle 2 min).
    Branch A (Flat)  : évalue BUY ou SHORT entry selon ibkr_cfg.allow_short
    Branch B (LONG)  : trailing + partiels + sell signal → SELL
    Branch C (SHORT) : trailing + partiels + cover signal → COVER
    """
    with _ibkr_state_lock:
        pair_state = bot_state.get(pair, {})
        last_side = pair_state.get("last_order_side")
        in_long = last_side in ("BUY", "LONG")
        in_short = last_side == "SHORT"
        in_position = in_long or in_short

    current_price = get_current_price(client, pair)
    if current_price <= 0:
        logger.error("[IBKR] %s — prix actuel invalide (%.5f)", pair, current_price)
        _now = time.time()
        if _now - _price_error_last_sent.get(pair, 0.0) >= 1800:  # throttle 30 min
            _price_error_last_sent[pair] = _now
            try:
                send_email_alert(
                    f"[IBKR-FOREX] Prix invalide {pair}",
                    f"Prix actuel invalide ({current_price:.5f}) pour {pair}.\n"
                    f"Vérifier la connexion IBKR ou les heures de marché.",
                )
            except Exception as _mail_exc:
                logger.warning("[IBKR] Email prix invalide %s ERREUR : %s", pair, _mail_exc)
        return

    scenario = best.get("scenario", "StochRSI")
    atr = float(last.get("atr", 0.0) or 0.0)

    # ── Branch A : Flat — évaluation entrée ──────────────────────────────────
    if not in_position and not pair_state.get("oos_blocked", False):
        # Limite perte journalière
        _today_str = datetime.utcnow().strftime("%Y-%m-%d")
        with _ibkr_state_lock:
            if bot_state.get("daily_pnl_date") != _today_str:
                bot_state["daily_pnl"] = 0.0
                bot_state["daily_pnl_date"] = _today_str
            _daily_pnl = bot_state.get("daily_pnl", 0.0)
        _daily_loss_limit = -ibkr_cfg.daily_loss_limit_pct * ibkr_cfg.initial_capital
        _daily_blocked = _daily_pnl <= _daily_loss_limit

        # Évaluer signaux BUY et SHORT
        buy_signal, buy_reason = _check_ibkr_buy_signal(last, scenario, current_price)
        short_signal, short_reason = (
            _check_ibkr_short_signal(last, scenario, current_price, ibkr_cfg)
            if ibkr_cfg.allow_short else (False, "SHORT désactivé")
        )

        if _daily_blocked:
            buy_signal = False
            short_signal = False
            buy_reason = f"⚠ Daily loss limit ({_daily_pnl:.2f}€ ≤ {_daily_loss_limit:.2f}€)"

        _display_ibkr_buy_panel(pair, current_price, last, best, buy_signal, buy_reason, console)

        if _daily_blocked:
            return

        # ── Entrée LONG ──────────────────────────────────────────────────────
        if buy_signal:
            nav = get_account_nav(client)
            if nav <= 0:
                logger.error("[IBKR] %s — NAV invalide (%.2f), BUY annulé", pair, nav)
                return
            qty = _compute_position_size(
                nav, ibkr_cfg.risk_per_trade, atr, current_price,
                atr_stop_multiplier=ibkr_cfg.atr_multiplier_sl,
            )
            quote_qty = qty * current_price
            quote_qty = min(quote_qty, ibkr_cfg.max_position_usd)
            buy_result = safe_forex_buy(client, pair, quote_qty, current_price=current_price)
            if buy_result:
                entry_price = buy_result["entry_price"]
                real_qty = buy_result["quantity"]
                sl_price = max(entry_price - ibkr_cfg.atr_multiplier_sl * atr, 0.0) if atr else entry_price * 0.98
                sl_result = place_forex_stop_loss(client, pair, real_qty, sl_price)
                with _ibkr_state_lock:
                    pair_state["last_order_side"] = "BUY"
                    pair_state["entry_price"] = entry_price
                    pair_state["quantity"] = real_qty
                    pair_state["stop_loss"] = sl_price
                    pair_state["sl_order_id"] = sl_result["sl_order_id"] if sl_result else None
                    pair_state["sl_exchange_placed"] = sl_result is not None
                    pair_state["max_price"] = entry_price
                    pair_state["min_price"] = None
                    pair_state["buy_timestamp"] = time.time()
                    pair_state["partial_taken_1"] = False
                    pair_state["partial_taken_2"] = False
                    pair_state["trailing_stop_activated"] = False
                    pair_state["trailing_stop"] = None
                    pair_state["breakeven_activated"] = False
                _save_state(ibkr_cfg, force=True)
                logger.info("[IBKR] %s BUY exécuté : qty=%.0f @%.5f SL=%.5f", pair, real_qty, entry_price, sl_price)
                try:
                    send_email_alert(
                        f"[IBKR-FOREX] BUY {pair} @{entry_price:.5f}",
                        f"Quantité : {real_qty:.0f}\nStop-loss : {sl_price:.5f}\nNAV : {nav:.2f} €",
                    )
                except Exception as mail_exc:
                    logger.warning("[IBKR] Email BUY %s ERREUR : %s", pair, mail_exc)

        # ── Entrée SHORT ─────────────────────────────────────────────────────
        elif short_signal:
            nav = get_account_nav(client)
            if nav <= 0:
                logger.error("[IBKR] %s — NAV invalide (%.2f), SHORT annulé", pair, nav)
                return
            qty = _compute_position_size(
                nav, ibkr_cfg.risk_per_trade, atr, current_price,
                atr_stop_multiplier=ibkr_cfg.atr_multiplier_sl,
            )
            quote_qty = qty * current_price
            quote_qty = min(quote_qty, ibkr_cfg.max_position_usd)
            short_result = safe_forex_short_open(client, pair, quote_qty, current_price=current_price)
            if short_result:
                entry_price = short_result["entry_price"]
                real_qty = short_result["quantity"]
                sl_price = entry_price + ibkr_cfg.atr_multiplier_sl * atr if atr else entry_price * 1.02
                sl_result = place_forex_stop_buy(client, pair, real_qty, sl_price)
                with _ibkr_state_lock:
                    pair_state["last_order_side"] = "SHORT"
                    pair_state["entry_price"] = entry_price
                    pair_state["quantity"] = real_qty
                    pair_state["stop_loss"] = sl_price
                    pair_state["sl_order_id"] = sl_result["sl_order_id"] if sl_result else None
                    pair_state["sl_exchange_placed"] = sl_result is not None
                    pair_state["max_price"] = None
                    pair_state["min_price"] = entry_price
                    pair_state["buy_timestamp"] = time.time()
                    pair_state["partial_taken_1"] = False
                    pair_state["partial_taken_2"] = False
                    pair_state["trailing_stop_activated"] = False
                    pair_state["trailing_stop"] = None
                    pair_state["breakeven_activated"] = False
                _save_state(ibkr_cfg, force=True)
                logger.info("[IBKR] %s SHORT ouvert : qty=%.0f @%.5f SL=%.5f", pair, real_qty, entry_price, sl_price)
                try:
                    send_email_alert(
                        f"[IBKR-FOREX] SHORT {pair} @{entry_price:.5f}",
                        f"Quantité : {real_qty:.0f}\nStop-loss : {sl_price:.5f}\nNAV : {nav:.2f} €",
                    )
                except Exception as mail_exc:
                    logger.warning("[IBKR] Email SHORT %s ERREUR : %s", pair, mail_exc)

    # ── Branch B : En LONG ───────────────────────────────────────────────────
    elif in_long:
        entry_price = float(pair_state.get("entry_price") or 0.0)
        qty = float(pair_state.get("quantity") or 0.0)

        # Trailing stop + partiels
        if atr > 0:
            _update_ibkr_trailing_stop(pair, pair_state, current_price, atr, client, ibkr_cfg)
        _execute_ibkr_partial_exit(pair, pair_state, current_price, client, ibkr_cfg)

        # Signal de sortie LONG
        sell_signal, sell_reason = _check_ibkr_sell_signal(last, scenario, ibkr_cfg)

        _display_ibkr_sell_panel(
            pair, current_price, last, entry_price, qty, sell_signal, console, best=best
        )

        if sell_signal:
            qty_now = float(pair_state.get("quantity") or 0.0)
            sl_oid = pair_state.get("sl_order_id")
            if sl_oid:
                cancel_forex_order(client, pair, sl_oid)
            sell_result = safe_forex_sell(client, pair, qty_now, reason="SIGNAL")
            if sell_result:
                exit_price = sell_result["exit_price"]
                pnl = (exit_price - entry_price) * qty_now
                with _ibkr_state_lock:
                    pair_state["last_order_side"] = "SELL"
                    pair_state["entry_price"] = None
                    pair_state["quantity"] = None
                    pair_state["stop_loss"] = None
                    pair_state["sl_order_id"] = None
                    pair_state["sl_exchange_placed"] = False
                    pair_state["max_price"] = None
                    pair_state["trailing_stop_activated"] = False
                    pair_state["trailing_stop"] = None
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

    # ── Branch C : En SHORT ──────────────────────────────────────────────────
    elif in_short:
        entry_price = float(pair_state.get("entry_price") or 0.0)
        qty = float(pair_state.get("quantity") or 0.0)

        # Trailing stop + partiels
        if atr > 0:
            _update_ibkr_trailing_stop(pair, pair_state, current_price, atr, client, ibkr_cfg)
        _execute_ibkr_partial_exit(pair, pair_state, current_price, client, ibkr_cfg)

        # Signal de rachat SHORT
        cover_signal, cover_reason = _check_ibkr_cover_signal(last, scenario, ibkr_cfg)

        _display_ibkr_short_panel(
            pair, current_price, last, entry_price, qty, cover_signal, console, best=best
        )

        if cover_signal:
            qty_now = float(pair_state.get("quantity") or 0.0)
            sl_oid = pair_state.get("sl_order_id")
            if sl_oid:
                cancel_forex_order(client, pair, sl_oid)
            cover_result = safe_forex_cover(client, pair, qty_now, reason="SIGNAL")
            if cover_result:
                exit_price = cover_result["exit_price"]
                pnl = (entry_price - exit_price) * qty_now  # SHORT: profit si prix baisse
                with _ibkr_state_lock:
                    pair_state["last_order_side"] = "COVER"
                    pair_state["entry_price"] = None
                    pair_state["quantity"] = None
                    pair_state["stop_loss"] = None
                    pair_state["sl_order_id"] = None
                    pair_state["sl_exchange_placed"] = False
                    pair_state["min_price"] = None
                    pair_state["trailing_stop_activated"] = False
                    pair_state["trailing_stop"] = None
                    _today_str = datetime.utcnow().strftime("%Y-%m-%d")
                    if bot_state.get("daily_pnl_date") != _today_str:
                        bot_state["daily_pnl"] = 0.0
                        bot_state["daily_pnl_date"] = _today_str
                    bot_state["daily_pnl"] = bot_state.get("daily_pnl", 0.0) + pnl
                _save_state(ibkr_cfg, force=True)
                logger.info("[IBKR] %s COVER : @%.5f PnL=%.2f €", pair, exit_price, pnl)
                try:
                    send_email_alert(
                        f"[IBKR-FOREX] COVER {pair} @{exit_price:.5f}",
                        f"PnL estimé : {pnl:+.2f} €\nNAV : {get_account_nav(client):.2f} €",
                    )
                except Exception as mail_exc:
                    logger.warning("[IBKR] Email COVER %s ERREUR : %s", pair, mail_exc)


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
                logger.warning(
                    "[IBKR] %s — OOS bloqué (session précédente) — recalcul en cours...",
                    pair,
                )

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
    if datetime.now().weekday() >= 5:  # 5=samedi, 6=dimanche — marché Forex fermé
        logger.info("[IBKR] Cycle backtest/WF ignoré — weekend (marché fermé)")
        return

    logger.info("[IBKR] ─── Cycle trading démarré ───")

    if bot_state.get("emergency_halt", False):
        reason = bot_state.get("emergency_halt_reason", "inconnu")
        logger.critical("[IBKR] EMERGENCY HALT actif — reason: %s", reason)
        return

    _was_disconnected = not client.is_connected()
    try:
        client.ensure_connected()
    except Exception as exc:
        logger.error("[IBKR] Reconnexion échouée : %s", exc)
        _now = time.time()
        if _now - _reconnect_alert_last_sent.get("fail", 0.0) >= 300:
            _reconnect_alert_last_sent["fail"] = _now
            try:
                send_email_alert(
                    "[IBKR-FOREX] Connexion IB Gateway impossible",
                    f"Reconnexion échouée après plusieurs tentatives.\n"
                    f"Détail : {exc}\n"
                    f"Vérifier que IB Gateway est actif.",
                )
            except Exception as _mail_exc:
                logger.debug("[IBKR] Email reconnexion ERREUR : %s", _mail_exc)
        return
    if _was_disconnected:
        logger.info("[IBKR] Reconnecté à IB Gateway")
        try:
            send_email_alert(
                "[IBKR-FOREX] Reconnexion IB Gateway réussie",
                "Connexion IB Gateway rétablie.\nLe bot reprend ses cycles normalement.",
            )
        except Exception as _mail_exc:
            logger.debug("[IBKR] Email reconnexion réussie ERREUR : %s", _mail_exc)

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


# ─── Détection SL-FILL exchange-native ───────────────────────────────────────

def _check_and_handle_sl_hit(
    pair: str,
    pair_state: "Dict[str, Any]",
    client: "IBKRForexClient",
    ibkr_cfg: "IBKRConfig",
) -> bool:
    """Vérifie si l'ordre SL exchange a été exécuté (FILLED) par IBKR.

    Appelé à chaque cycle live (2 min). Si le SL est FILLED :
      - Réinitialise pair_state (position fermée)
      - Met à jour daily_pnl
      - Envoie un email d'alerte
      - Retourne True

    Retourne False si aucun SL détecté ou si la vérification échoue.
    """
    sl_order_id = pair_state.get("sl_order_id")
    sl_placed = pair_state.get("sl_exchange_placed", False)
    last_side = pair_state.get("last_order_side")

    if not sl_order_id or not sl_placed or last_side not in ("BUY", "SHORT"):
        return False

    try:
        order = client.get_order(orderId=int(sl_order_id))
        if order.get("status") != "FILLED":
            return False

        # SL exécuté — extraire les données de fill
        entry_price = float(pair_state.get("entry_price") or 0.0)
        sl_fill_price = float(order.get("price") or pair_state.get("stop_loss") or 0.0)
        qty = float(order.get("executedQty") or pair_state.get("quantity") or 0.0)

        if last_side == "BUY":
            pnl = (sl_fill_price - entry_price) * qty
        else:  # SHORT
            pnl = (entry_price - sl_fill_price) * qty

        _today_str = datetime.utcnow().strftime("%Y-%m-%d")
        with _ibkr_state_lock:
            pair_state["last_order_side"] = "SL-FILL"
            pair_state["entry_price"] = None
            pair_state["quantity"] = None
            pair_state["stop_loss"] = None
            pair_state["sl_order_id"] = None
            pair_state["sl_exchange_placed"] = False
            pair_state["max_price"] = None
            pair_state["min_price"] = None
            pair_state["trailing_stop_activated"] = False
            pair_state["trailing_stop"] = None
            pair_state["breakeven_activated"] = False
            pair_state["partial_taken_1"] = False
            pair_state["partial_taken_2"] = False
            if bot_state.get("daily_pnl_date") != _today_str:
                bot_state["daily_pnl"] = 0.0
                bot_state["daily_pnl_date"] = _today_str
            bot_state["daily_pnl"] = float(bot_state.get("daily_pnl") or 0.0) + pnl

        _save_state(ibkr_cfg, force=True)
        logger.info(
            "[IBKR] %s SL-FILL détecté : @%.5f PnL=%.2f € (side=%s)",
            pair, sl_fill_price, pnl, last_side,
        )
        try:
            send_email_alert(
                f"[IBKR-FOREX] SL-FILL {pair} @{sl_fill_price:.5f}",
                f"Stop-loss exécuté sur {pair}.\n"
                f"Direction     : {last_side}\n"
                f"Prix d'entrée : {entry_price:.5f}\n"
                f"Prix SL fill  : {sl_fill_price:.5f}\n"
                f"Quantité      : {qty:.0f}\n"
                f"PnL estimé    : {pnl:+.2f} €",
            )
        except Exception as _mail_exc:
            logger.warning("[IBKR] Email SL-FILL %s ERREUR : %s", pair, _mail_exc)

        return True

    except Exception as _sl_check_err:
        logger.debug("[IBKR] %s — vérification SL-FILL ignorée : %s", pair, _sl_check_err)
        return False


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

        # ─── Détection SL-FILL (ordre SL exchange exécuté par IBKR) ─────────────
        # Si le SL a fire sur IBKR, on ferme la position côté état, on envoie
        # l'email et on skip ce cycle pour éviter une ré-entrée immédiate.
        if _check_and_handle_sl_hit(pair, pair_state, client, ibkr_cfg):
            with _ibkr_state_lock:
                in_position = (pair_state.get("last_order_side") == "BUY")
            return  # skip panels + signal — prochain cycle = état propre

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
            if best is None:
                _oos_tag = " [IS \u2014 OOS non valid\u00e9]"
            elif best.get("validation_mode") == "IS":
                _oos_tag = " [IS valid\u00e9]"
            else:
                _oos_tag = ""
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
                    # Afficher aussi les conditions SHORT en mode informatif
                    if _is_disp_only and ibkr_cfg.allow_short:
                        _short_sig, _short_reason = _check_ibkr_short_signal(
                            last, _scenario, _disp_price, ibkr_cfg
                        )
                        _display_ibkr_short_entry_panel(
                            pair, _disp_price, last,
                            short_signal=_short_sig,
                            short_reason=_short_reason,
                            con=console,
                            best=_best_disp,
                        )
            except Exception as _cond_err:
                logger.debug("[IBKR-LIVE] %s \u2014 affichage conditions ignor\u00e9 : %s", pair, _cond_err)
        _run_signal = True
        # oos_blocked bloque les nouveaux achats mais pas le monitoring d'une
        # position déjà ouverte (les exits restent actifs).
        if oos_blocked and not in_position:
            _oos_block_detail = "achat et SHORT bloqués" if ibkr_cfg.allow_short else "achat bloqué"
            logger.info(
                "[IBKR-LIVE] %s \u2014 OOS gates non valid\u00e9es, %s (2 min)", pair, _oos_block_detail,
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
        _display_ibkr_planning_panel(
            _now_exec, _next_exec, console,
            paper_mode=ibkr_cfg.paper_mode,
            is_connected=client.is_connected(),
        )

        with _ibkr_state_lock:
            bot_state[pair]["last_live_time"] = datetime.utcnow().isoformat() + "Z"
        _save_state(ibkr_cfg)
    finally:
        lock.release()


def _live_trading_job(client: "IBKRForexClient", ibkr_cfg: "IBKRConfig") -> None:
    """Cycle live toutes les 2 minutes : signal + ordres sans backtest.

    Identique à _dispatch_live_parallel du bot Binance (execute_live_trading_only).
    """
    if datetime.now().weekday() >= 5:  # 5=samedi, 6=dimanche — marché Forex fermé
        logger.debug("[IBKR] Cycle signal ignoré — weekend (marché fermé)")
        return

    logger.info("[IBKR] ─── Cycle signal (2 min) ───")

    if bot_state.get("emergency_halt", False):
        reason = bot_state.get("emergency_halt_reason", "inconnu")
        logger.critical("[IBKR] EMERGENCY HALT actif — reason: %s", reason)
        return

    _was_disconnected = not client.is_connected()
    try:
        client.ensure_connected()
    except Exception as exc:
        logger.error("[IBKR] Reconnexion échouée (live) : %s", exc)
        _now = time.time()
        if _now - _reconnect_alert_last_sent.get("fail", 0.0) >= 300:
            _reconnect_alert_last_sent["fail"] = _now
            try:
                send_email_alert(
                    "[IBKR-FOREX] Connexion IB Gateway impossible",
                    f"Reconnexion échouée après plusieurs tentatives.\n"
                    f"Détail : {exc}\n"
                    f"Vérifier que IB Gateway est actif.",
                )
            except Exception as _mail_exc:
                logger.debug("[IBKR] Email reconnexion ERREUR : %s", _mail_exc)
        return
    if _was_disconnected:
        logger.info("[IBKR] Reconnecté à IB Gateway (cycle live)")
        try:
            send_email_alert(
                "[IBKR-FOREX] Reconnexion IB Gateway réussie",
                "Connexion IB Gateway rétablie.\nLe bot reprend ses cycles normalement.",
            )
        except Exception as _mail_exc:
            logger.debug("[IBKR] Email reconnexion réussie ERREUR : %s", _mail_exc)

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

    # Email de démarrage — permet de vérifier que le SMTP fonctionne
    try:
        _mode_str = "PAPER" if ibkr_cfg.paper_mode else "LIVE"
        send_email_alert(
            f"[IBKR-FOREX] Bot démarré — mode {_mode_str}",
            f"Connexion IB Gateway OK (port {ibkr_cfg.port})\n"
            f"Mode : {_mode_str}\n"
            f"Capital : {ibkr_cfg.initial_capital:.0f} €\n"
            f"allow_short : {ibkr_cfg.allow_short}\n"
            f"Scheduler : {ibkr_cfg.schedule_interval_minutes} min (WF+backtest) / 2 min (signal)",
        )
    except Exception as _mail_exc:
        logger.warning("[IBKR] Email démarrage ERREUR : %s", _mail_exc)

    # Planifier le job
    interval = ibkr_cfg.schedule_interval_minutes
    logger.info("[IBKR] Scheduler : toutes les %d minutes (backtest+WF) + toutes les 2 minutes (live)", interval)

    # Exécuter immédiatement au démarrage (avant d'enregistrer le scheduler,
    # pour éviter que le job 2-min soit déjà "en retard" si _trading_job dépasse 2 min)
    _trading_job(client, ibkr_cfg)
    _live_trading_job(client, ibkr_cfg)  # premier cycle live sans attendre 2 min

    # Enregistrer les tâches planifiées APRÈS les appels initiaux,
    # de sorte que next_run = now + interval (pas de double déclenchement)
    # Tâche 1 : backtest + WF + signal → toutes les 60 minutes
    schedule.every(interval).minutes.do(_trading_job, client=client, ibkr_cfg=ibkr_cfg)
    # Tâche 2 : signal live uniquement → toutes les 2 minutes
    schedule.every(2).minutes.do(_live_trading_job, client=client, ibkr_cfg=ibkr_cfg)

    # Boucle infinie
    try:
        while True:
            if bot_state.get("emergency_halt", False):
                logger.critical("[IBKR] EMERGENCY HALT — arrêt du scheduler")
                try:
                    send_email_alert(
                        "[IBKR-FOREX] EMERGENCY HALT",
                        "Le bot s'est arrêté en urgence (emergency_halt=True).\nVérifier les logs.",
                    )
                except Exception:
                    pass
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
