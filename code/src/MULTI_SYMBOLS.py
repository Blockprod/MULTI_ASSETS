# ─── Standard-library & third-party imports ─────────────────────────────────
import argparse
import json
import locale
import logging
import os
import random  # noqa: F401 — re-export requis (tests patchent ms.random.random)
import schedule
import shutil
import signal
import sys
import threading
import time
import traceback
import warnings
from logging.handlers import RotatingFileHandler
from binance.client import Client
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from rich.console import Console
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict, cast

import pandas as pd

# Suppress DeprecationWarning and pandas 2.2 CoW ChainedAssignmentError
# (root-cause fix is in indicators.pyx; this filter is a safety net).
warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    _chained_err = getattr(pd.errors, 'ChainedAssignmentError', None)
    if _chained_err is not None:
        warnings.filterwarnings("ignore", category=_chained_err)
except AttributeError:
    pass  # pandas < 2.2 n'a pas ChainedAssignmentError

# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

# ─── Imports depuis les modules extraits (Phase 4 + P3-SRP) ─────────────────
from bot_config import (
    config, extract_coin_from_pair,
    set_error_notification_callback, VERBOSE_LOGS,
)
from timestamp_utils import (                          # P3-SRP
    init_timestamp_solution as _init_timestamp_solution,
    check_network_connectivity,
    full_timestamp_resync as _full_timestamp_resync,
    validate_api_connection as _validate_api_connection,
)
from signal_generator import (                         # P3-SRP
    generate_buy_condition_checker,
    generate_sell_condition_checker as _generate_sell_condition_checker,
)
from market_analysis import (                          # P3-SRP
    detect_market_changes as _detect_market_changes,
)
from data_fetcher import (                             # P3-SRP
    get_cached_exchange_info,
    fetch_historical_data as _fetch_historical_data,
    get_binance_trading_fees as _get_binance_trading_fees,
)
from indicators_engine import (                        # P3-SRP
    calculate_indicators as _calculate_indicators,
    universal_calculate_indicators as _universal_calculate_indicators,
    prepare_base_dataframe as _prepare_base_dataframe,
    compute_stochrsi,  # noqa: F401 — re-export: test_indicators_consistency importe depuis MULTI_SYMBOLS
    CYTHON_INDICATORS_AVAILABLE,
)
from backtest_runner import (                          # P3-SRP
    backtest_from_dataframe,
    run_all_backtests as _run_all_backtests,
    run_parallel_backtests as _run_parallel_backtests,
    CYTHON_BACKTEST_AVAILABLE,
)
from trade_helpers import (                            # P3-SRP
    get_sniper_entry_price as _get_sniper_entry_price,
    get_last_sell_trade_usdc as _get_last_sell_trade_usdc,
    get_usdc_from_all_sells_since_last_buy as _get_usdc_from_all_sells,
    check_partial_exits_from_history as _check_partial_exits,
    check_if_order_executed,
    select_best_by_calmar as _select_best_by_calmar,
)
from exceptions import SizingError                     # P0-05
from position_sizing import (  # noqa: F401 — re-exports: test_sizing importe depuis MULTI_SYMBOLS
    compute_position_size_by_risk,
    compute_position_size_fixed_notional,
    compute_position_size_volatility_parity,
)
from email_utils import send_email_alert, send_trading_alert_email
from state_manager import save_state, load_state, set_emergency_halt
from display_ui import (
    display_account_balances_panel, display_market_changes,
    display_results_for_pair, display_backtest_table,
    build_tracking_panel, display_execution_header,
    display_bot_active_banner,
)
from cache_manager import cleanup_expired_cache
from exchange_client import (
    BinanceFinalClient, is_valid_stop_loss_order,
    can_execute_partial_safely,  # noqa: F401 — re-export: tests patchent ms.can_execute_partial_safely
    place_stop_loss_order as _place_stop_loss_order,
    place_exchange_stop_loss as _place_exchange_stop_loss,  # P0-01
    safe_market_buy as _safe_market_buy,
    safe_market_sell as _safe_market_sell,
    get_symbol_filters as _get_symbol_filters_impl,
    _get_coin_balance,
    set_circuit_alert_callback,  # TS-P2-01
)
from email_templates import (
    api_connection_failure_email, data_retrieval_error_email,
    network_error_email, indicator_error_email,
    trading_pair_error_email,
    critical_startup_error_email,
    generic_exception_email,
)

# is_valid_stop_loss_order, can_execute_partial_safely importés depuis exchange_client.py (Phase 4)

# ErrorHandler réel importé depuis error_handler.py (remplace le DummyErrorHandler)
from error_handler import initialize_error_handler
from error_handler import AlertThrottle  # P1-05

# Modules dormants activés (Phase 2)
from trade_journal import log_trade  # noqa: F401 — re-export: tests patchent ms.log_trade
from position_reconciler import (
    _ReconcileDeps,
    _PairStatus,
    _check_pair_vs_exchange as _check_pair_impl,
    _handle_pair_discrepancy as _handle_pair_impl,
    reconcile_positions_with_exchange as _reconcile_impl,
)
from order_manager import (
    _TradingDeps,
    _TradeCtx,
    _sync_entry_state,
    _update_trailing_stop,
    _execute_partial_sells,
    _check_and_execute_stop_loss,
    _handle_dust_cleanup,
    _execute_signal_sell,
    _execute_buy,
)
from backtest_orchestrator import (
    _BacktestDeps,
    _apply_oos_quality_gate,
    _execute_scheduled_trading,
    _execute_live_trading_only,
    _backtest_and_display_results,
)
from constants import (                        # P1-03
    SAVE_THROTTLE_SECONDS,
    MAX_SAVE_FAILURES,
)
from cython_integrity import (                 # P1-01
    verify_cython_integrity as _verify_cython_integrity,
)
from metrics import write_metrics as _write_metrics  # P2-04: observabilité métriques

try:
    # Forcer la console Windows en UTF-8 (code page 65001)
    if os.name == "nt":
        os.system("chcp 65001 >NUL")
        locale.setlocale(locale.LC_ALL, '')
except Exception as _exc:
    logging.getLogger(__name__).debug("[MULTI_SYMBOLS] initialisation locale/console échouée: %s", _exc)

# Configuration du logging
_log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs', 'trading_bot.log')
os.makedirs(os.path.dirname(_log_file), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(_log_file, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'),  # C-10
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
console = Console()

# Log Cython status at startup (after logging is configured)
if CYTHON_BACKTEST_AVAILABLE:
    logger.info("[CYTHON] Moteur backtest Cython chargé avec succès (backtest_engine_standard)")
else:
    logger.warning("[CYTHON] Moteur backtest Cython NON disponible — fallback Python actif")
if CYTHON_INDICATORS_AVAILABLE:
    logger.info("[CYTHON] Moteur indicateurs Cython chargé avec succès")
else:
    logger.warning("[CYTHON] Moteur indicateurs Cython NON disponible — fallback Python actif")

# P1-01: Vérifie l'intégrité SHA256 des .pyd au démarrage
_verify_cython_integrity(alert_fn=send_email_alert)

# Paramètre pour activer/désactiver les logs détaillés (VERBOSE = False pour plus de rapidité)
# (VERBOSE_LOGS importé depuis bot_config)

# Config et config importés depuis bot_config.py (Phase 4)
# La classe Config et config = Config.from_env() sont dans bot_config.py

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Cython modules now loaded in backtest_runner.py and indicators_engine.py (P3-SRP)

# --- Decorators ---
# Décorateurs log_exceptions et retry_with_backoff importés depuis bot_config.py (Phase 4)
# Thread-local _alert_sending est dans bot_config.py

# --- Email Helpers ---
# send_email_alert et send_trading_alert_email importés depuis email_utils.py (Phase 4)

# --- Core Helpers ---
# get_all_tickers_cached et get_spot_balance_usdc importés depuis exchange_client.py (Phase 4)

# extract_coin_from_pair importé depuis bot_config.py (Phase 4)

# --- Display Functions ---
# Fonctions d'affichage extraites dans display_ui.py (Phase 5)
# pair_state est dérivé localement dans chaque fonction via bot_state.setdefault(backtest_pair, {})
# et passé explicitement en paramètre aux fonctions d'affichage (plus de global pair_state).

# --- Binance Client ---
# BinanceFinalClient importé depuis exchange_client.py (Phase 4)

# Initialisation du client
client = BinanceFinalClient(
    config.api_key,
    config.secret_key,
    requests_params={'timeout': config.api_timeout}
)

# Configurer le callback d'erreur pour le decorator log_exceptions (Phase 4)
def _error_notification_handler(fn: str, e: Exception, a: Tuple[Any, ...], kw: Dict[str, Any]) -> None:
    subj, body = generic_exception_email(fn, e, a, kw)
    send_trading_alert_email(subject=subj, body_main=body, client=client)

set_error_notification_callback(_error_notification_handler)

# TS-P2-01: enregistrer le callback d'alerte email pour le circuit breaker
set_circuit_alert_callback(
    lambda msg: send_trading_alert_email(
        subject="[CIRCUIT-BREAKER TS-P2-01] API Binance en quarantaine — achats bloqués",
        body_main=msg,
        client=client,
    )
)

# Timeframes
timeframes = [
    Client.KLINE_INTERVAL_1HOUR,
    Client.KLINE_INTERVAL_4HOUR,
    Client.KLINE_INTERVAL_1DAY
]

# P1-01: Calcul dynamique de start_date — NE PLUS utiliser de variable module-level
# figée à l'import. Utiliser _fresh_start_date() partout.
def _fresh_start_date() -> str:
    """Retourne start_date recalculé à chaque appel (fenêtre glissante)."""
    return (datetime.today() - timedelta(days=config.backtest_days)).strftime("%d %B %Y")

start_date = _fresh_start_date()  # rétrocompatibilité init

# Cache indicateurs et Cython flags importes depuis indicators_engine.py (P3-SRP)


# ─── C-16 : TypedDict pour bot_state ──────────────────────────────────────────
# PairState décrit l'état persisté par paire (ex. bot_state['SOLUSDT']).
# total=False : toutes les clés sont optionnelles (ajoutées progressivement).

class PairState(TypedDict, total=False):
    """État persisté d'une paire de trading dans bot_state (C-16)."""
    # --- Exécution / scheduling ---
    last_run_time: Optional[str]
    last_best_params: Optional[Dict[str, Any]]
    execution_count: int
    last_execution: Optional[str]          # ISO datetime UTC
    # --- Position ---
    last_order_side: Optional[str]         # 'BUY' | 'SELL' | None
    entry_price: Optional[float]
    initial_position_size: Optional[float]
    # in_position supprimé C-06 — utiliser last_order_side == 'BUY'
    # --- Stop-loss / trailing ---
    atr_at_entry: Optional[float]
    stop_loss: Optional[float]
    stop_loss_at_entry: Optional[float]    # fixe 3×ATR
    trailing_activation_price_at_entry: Optional[float]
    trailing_activation_price: Optional[float]
    trailing_stop_activated: bool
    trailing_stop: Optional[float]
    max_price: Optional[float]
    sl_order_id: Optional[Any]             # Binance orderId (str | int)
    sl_exchange_placed: bool               # F-SL: ordre SL placé sur l'exchange
    # --- Prises de profit partielles ---
    partial_enabled: bool
    partial_taken_1: bool
    partial_taken_2: bool
    # --- Break-even ---
    breakeven_triggered: bool              # B-3: break-even stop activé
    # --- Cohérence params entrée/sortie (F-COH) ---
    entry_scenario: Optional[str]
    entry_timeframe: Optional[str]
    entry_ema1: Optional[int]
    entry_ema2: Optional[int]
    buy_timestamp: float                   # F-2: timestamp achat
    # --- Cooldown post-stop (A-3) ---
    _stop_loss_cooldown_until: float
    # --- OOS gates (P0-03) ---
    oos_blocked: bool
    oos_blocked_since: float               # time.time()
    # --- Drawdown kill-switch (ST-P2-02) ---
    drawdown_halted: Optional[bool]        # True si drawdown > max_drawdown_pct enété détecté
    # --- Display / info (écriture externe) ---
    quote_currency: str
    ticker_spot_price: float
    latest_best_params: Optional[Dict[str, Any]]


class BotStateDict(TypedDict, total=False):
    """Structure globale de bot_state (C-16)."""
    emergency_halt: bool
    emergency_halt_reason: str
    _daily_pnl_tracker: Dict[str, Any]
    _state_version: int
    # Les clés dynamiques (noms de paires ex: 'SOLUSDT') ne sont pas
    # représentables dans TypedDict. On utilise BotStateDict pour les
    # clés connues ; l'accès aux paires reste Dict[str, PairState].


# etat du bot — runtime reste Dict[str, Any] ; PairState sert d'annotation locale
bot_state: Dict[str, Any] = {}


def _make_default_pair_state() -> 'PairState':
    """Retourne un PairState frais avec les champs d'initialisation (C-16)."""
    return cast('PairState', {
        'last_run_time': None,
        'last_best_params': None,
        'execution_count': 0,
        'entry_price': None,
        'max_price': None,
        'trailing_stop': None,
        'stop_loss': None,
        'last_execution': None,
        'sl_exchange_placed': False,
    })


# ─── Thread-safety du bot_state (C-01) ────────────────────────────────────────
# RLock global pour serialize save/load du bot_state
_bot_state_lock = threading.RLock()
# Locks par paire : empêchent deux exécutions simultanées sur la même paire
_pair_execution_locks: Dict[str, threading.Lock] = {}
_pair_locks_mutex = threading.Lock()

# Cache pour les indicateurs calculés (lecture/écriture multi-thread protégée)
_indicators_cache_lock = threading.Lock()
indicators_cache: OrderedDict[str, Any] = OrderedDict()


# Paramètres par défaut des scénarios — constante partagée (3 emplacements)
SCENARIO_DEFAULT_PARAMS: Dict[str, Dict[str, Any]] = {
    'StochRSI': {'stoch_period': 14},
    'StochRSI_SMA': {'stoch_period': 14, 'sma_long': 200},
    'StochRSI_ADX': {'stoch_period': 14, 'adx_period': 14},
    'StochRSI_TRIX': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15}
}

# P6-C: liste de scénarios Walk-Forward — source unique de vérité (remplaçait 3 définitions inline identiques)
WF_SCENARIOS: List[Dict[str, Any]] = [
    {'name': 'StochRSI',      'params': {'stoch_period': 14}},
    {'name': 'StochRSI_SMA',  'params': {'stoch_period': 14, 'sma_long': 200}},
    {'name': 'StochRSI_ADX',  'params': {'stoch_period': 14, 'adx_period': 14}},
    {'name': 'StochRSI_TRIX', 'params': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15}},
]

# _cache_dir_initialized remplace par ensure_cache_dir() de cache_manager.py (Phase 4)

# _current_backtest_pair moved to backtest_runner.py (P3-SRP)


# _get_coin_balance imported from exchange_client (C-03)

# --- Order Placement Helpers ---
# Fonctions d'ordres importées depuis exchange_client.py (Phase 4)
# Wrappers pour passer le client global automatiquement
def place_trailing_stop_order(symbol: str, quantity: float, activation_price: float, trailing_delta: float, client_id: Optional[str] = None) -> None:
    # AVERTISSEMENT : TRAILING_STOP_MARKET est un type d'ordre Futures uniquement.
    # Cette fonction NE PEUT PAS être utilisée sur l'API Spot Binance.
    # Elle est conservée pour compatibilité mais soulève une erreur si appelée.
    raise NotImplementedError(
        "TRAILING_STOP_MARKET n'est pas disponible sur Binance Spot. "
        "Utilisez le trailing manuel implémenté dans monitor_and_trade_for_pair()."
    )

def place_stop_loss_order(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """C-05: Adapter — injecte client+send_alert, auto-forward vers exchange_client.
    Tout paramètre ajouté à exchange_client.place_stop_loss_order est automatiquement
    transmis sans modifier ce wrapper (elimination du maintenance double).
    """
    return _place_stop_loss_order(client, *args, send_alert=send_trading_alert_email, **kwargs)

def place_exchange_stop_loss_order(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """C-05: Adapter — injecte client+send_alert, auto-forward vers exchange_client.
    Tout paramètre ajouté à exchange_client.place_exchange_stop_loss est automatiquement
    transmis sans modifier ce wrapper (elimination du maintenance double).
    """
    return _place_exchange_stop_loss(client, *args, send_alert=send_trading_alert_email, **kwargs)

def safe_market_buy(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """C-05: Adapter — injecte client+send_alert, auto-forward vers exchange_client.
    Tout paramètre ajouté à exchange_client.safe_market_buy est automatiquement
    transmis sans modifier ce wrapper (elimination du maintenance double).
    """
    return _safe_market_buy(client, *args, send_alert=send_trading_alert_email, **kwargs)

def safe_market_sell(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """C-05: Adapter — injecte client+send_alert, auto-forward vers exchange_client.
    Tout paramètre ajouté à exchange_client.safe_market_sell est automatiquement
    transmis sans modifier ce wrapper (elimination du maintenance double).
    """
    return _safe_market_sell(client, *args, send_alert=send_trading_alert_email, **kwargs)



# --- Utility Functions (delegated to timestamp_utils.py — P3-SRP) ---
def full_timestamp_resync() -> None:
    _full_timestamp_resync(client)

def validate_api_connection() -> bool:
    return _validate_api_connection(client, send_trading_alert_email, api_connection_failure_email)

def init_timestamp_solution() -> bool:
    return _init_timestamp_solution(client)

# get_cache_key importé depuis cache_manager.py (Phase 4)

# ─── P1-04: Runtime state container ─────────────────────────────────────────
class _BotRuntime:
    """Regroupe tous les états runtime mutables du bot (caches, timestamps, throttles, fees).

    Accès via le singleton _runtime — évite les globals dispersés au niveau module.
    Les constantes de configuration (_SAVE_THROTTLE_SECONDS, _MAX_SAVE_FAILURES) restent
    à niveau module car elles ne sont jamais mutées.
    """

    def __init__(self) -> None:
        # Save throttle state (P0-SAVE)
        self.last_save_time: float = 0.0
        self.save_failure_count: int = 0
        # Backtest scheduling per pair
        self.last_backtest_time: Dict[str, float] = {}
        self.live_best_params: Dict[str, Dict[str, Any]] = {}
        # Alert throttles (1h default, per pair or global)
        self.oos_alert_throttle = AlertThrottle(cooldown=3600)
        self.daily_loss_throttle = AlertThrottle(
            cooldown=getattr(config, 'backtest_throttle_seconds', 3600.0)
        )
        self.sl_missing_throttle = AlertThrottle(cooldown=3600)
        self.drawdown_throttle = AlertThrottle(cooldown=3600)
        # Live trading fees (init from config, updated once after API fetch — P0-01)
        self.taker_fee: float = config.taker_fee
        self.maker_fee: float = config.maker_fee


_runtime = _BotRuntime()

# --- Save throttle: constantes (jamais mutées) ---
_SAVE_THROTTLE_SECONDS: float = SAVE_THROTTLE_SECONDS
_MAX_SAVE_FAILURES: int = MAX_SAVE_FAILURES

def save_bot_state(force: bool = False) -> None:
    """Sauvegarde l'etat du bot (wrapper vers state_manager).

    P0-SAVE: les erreurs de sauvegarde ne sont plus avalées silencieusement.
    Après _MAX_SAVE_FAILURES échecs consécutifs, le kill-switch est activé.
    Throttled à 1 écriture / 5s sauf si force=True (arrêt, crash).
    Thread-safe via _bot_state_lock (C-01).
    """
    now = time.time()
    with _bot_state_lock:
        if not force and (now - _runtime.last_save_time) < _SAVE_THROTTLE_SECONDS:
            return
        try:
            # C-11: rotation automatique .bak avant écriture
            _state_path = os.path.join(config.states_dir, config.state_file)
            if os.path.exists(_state_path):
                try:
                    shutil.copy2(_state_path, _state_path + '.bak')
                    logger.debug("[STATE C-11] Backup créé: %s.bak", _state_path)
                except Exception as _bak_err:
                    logger.warning("[STATE C-11] Backup .bak impossible: %s", _bak_err)
            save_state(bot_state)
            _runtime.last_save_time = now
            if _runtime.save_failure_count > 0:
                logger.info("[SAVE P0-SAVE] Sauvegarde réussie après %d échec(s) consécutif(s)", _runtime.save_failure_count)
            _runtime.save_failure_count = 0
        except Exception as save_err:
            _runtime.save_failure_count += 1
            logger.critical(
                "[SAVE P0-SAVE] ÉCHEC sauvegarde état (%d/%d): %s",
                _runtime.save_failure_count, _MAX_SAVE_FAILURES, save_err,
            )
            # Ne PAS mettre à jour _runtime.last_save_time → force le retry au prochain appel
            if _runtime.save_failure_count >= _MAX_SAVE_FAILURES:
                try:
                    send_trading_alert_email(
                        subject=f"[CRITIQUE P0-SAVE] {_MAX_SAVE_FAILURES} échecs sauvegarde — EMERGENCY HALT",
                        body_main=(
                            f"La sauvegarde de l'état du bot a échoué {_MAX_SAVE_FAILURES} fois de suite.\n\n"
                            f"Dernière erreur: {save_err}\n\n"
                            f"EMERGENCY HALT activé — intervention manuelle requise."
                        ),
                        client=client,
                    )
                except Exception as _e:
                    logger.warning("[SAVE] Email alerte sauvegarde impossible: %s", _e)
            else:
                logger.warning(
                    "[SAVE P0-SAVE] Échec %d/%d — email différé au %dème échec.",
                    _runtime.save_failure_count, _MAX_SAVE_FAILURES, _MAX_SAVE_FAILURES,
                )
            if _runtime.save_failure_count >= _MAX_SAVE_FAILURES:
                set_emergency_halt(
                    bot_state,
                    f"{_MAX_SAVE_FAILURES} échecs consécutifs de sauvegarde à {datetime.now().isoformat()}",
                )

def load_bot_state() -> None:
    """Charge l'etat du bot (wrapper vers state_manager).

    C-04: Ne plus utiliser @log_exceptions — gestion explicite des erreurs
    avec log CRITICAL + alerte email. Le bot continue avec état vide si
    le chargement échoue (la réconciliation API prend le relais).

    Thread-safe via _bot_state_lock (C-01).
    C-05: `oos_blocked` est conservé au chargement — il sera levé uniquement
    lorsqu'un backtest validera les OOS gates. Un redémarrage ne réinitialise
    plus le blocage.
    """
    global bot_state
    try:
        loaded = load_state()
    except Exception as exc:
        logger.critical(
            "[STATE-CRITICAL C-04] Exception lors du chargement de l'état: %s",
            exc, exc_info=True,
        )
        try:
            _error_notification_handler('load_bot_state', exc, (), {})
        except Exception as _e:
            logger.warning("[STATE] Notification erreur impossible: %s", _e)
        loaded = None

    if loaded:
        with _bot_state_lock:
            bot_state = loaded
            # C-06: purger le champ legacy 'in_position' des états existants
            for _pair_key, _pair_val in bot_state.items():
                if isinstance(_pair_val, dict) and 'in_position' in _pair_val:
                    del _pair_val['in_position']
                    logger.debug("[STATE C-06] Clé legacy 'in_position' supprimée pour %s", _pair_key)
            # C-05: oos_blocked n'est PAS purgé au chargement.
            # Le flag sera levé uniquement quand un backtest validera les OOS gates
            # (execute_scheduled_trading ou backtest_and_display_results).
            # Cela évite qu'un redémarrage efface un blocage légitime.
        logger.info("[STATE] bot_state chargé — %d paires.", len(bot_state))
    else:
        logger.critical(
            "[STATE-CRITICAL C-04] load_state() a retourné None — "
            "démarrage avec état vide. Réconciliation API obligatoire."
        )
        try:
            _error_notification_handler(
                'load_bot_state',
                RuntimeError("État vide après chargement — fichier corrompu ou absent"),
                (), {},
            )
        except Exception as _e:
            logger.warning("[STATE] Notification erreur impossible: %s", _e)

# get_symbol_filters: wrapper vers exchange_client.py (Phase 4)
def get_symbol_filters(symbol: str) -> Dict[str, Any]:
    """Wrapper qui passe le client global."""
    return _get_symbol_filters_impl(client, symbol)


# ─── Daily Loss Limit helpers (P5-A) ────────────────────────────────────────

def _get_today_iso() -> str:
    """Retourne la date UTC du jour au format YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _update_daily_pnl(pnl_usdc: float | None) -> None:
    """Enregistre le PnL d'une vente dans le tracker journalier (thread-safe)."""
    if pnl_usdc is None:
        return
    today = _get_today_iso()
    with _bot_state_lock:
        tracker = bot_state.setdefault('_daily_pnl_tracker', {})
        day_entry = tracker.setdefault(today, {'total_pnl': 0.0, 'trade_count': 0})
        day_entry['total_pnl'] += pnl_usdc
        day_entry['trade_count'] += 1
        logger.info(
            "[DAILY-PNL P5-A] %s  PnL=%.2f USDC \u2192 cumul jour: %.2f USDC (%d trade(s))",
            today, pnl_usdc, day_entry['total_pnl'], day_entry['trade_count'],
        )
    save_bot_state()


def _is_daily_loss_limit_reached() -> bool:
    """Retourne True si la perte journalière dépasse daily_loss_limit_pct × initial_wallet."""
    today = _get_today_iso()
    with _bot_state_lock:
        tracker = bot_state.get('_daily_pnl_tracker', {})
        total_pnl = tracker.get(today, {}).get('total_pnl', 0.0)
    limit_usdc = config.initial_wallet * config.daily_loss_limit_pct
    if total_pnl < -limit_usdc:
        logger.warning(
            "[DAILY-LIMIT P5-A] Perte journalière %.2f USDC >= limite -%.2f USDC "
            "(%.1f %% de %.0f USDC initial). Achats bloqués jusqu'à 00:00 UTC.",
            abs(total_pnl), limit_usdc,
            config.daily_loss_limit_pct * 100, config.initial_wallet,
        )
        # C-08: email d'alerte avec cooldown throttlé (AlertThrottle P1-05)
        if _runtime.daily_loss_throttle.check_and_mark():
            send_trading_alert_email(
                subject="[DAILY LOSS LIMIT] Achats bloques — perte journaliere atteinte",
                body_main=(
                    f"La perte journaliere cumulee ({abs(total_pnl):.2f} USDC) a atteint "
                    f"la limite configuree ({limit_usdc:.2f} USDC = "
                    f"{config.daily_loss_limit_pct * 100:.1f}% de {config.initial_wallet:.0f} USDC initials).\n\n"
                    f"Les achats sont bloques jusqu'a 00:00 UTC. Les stops restent actifs."
                ),
                client=client,
            )
        else:
            logger.debug(
                "[DAILY-LIMIT C-08] Alerte email throttled (reste %.0fs)",
                _runtime.daily_loss_throttle.time_remaining(),
            )
        return True
    return False


def _make_reconcile_deps() -> _ReconcileDeps:
    """C-03: Injecte les globaux du module dans les fonctions de position_reconciler."""
    return _ReconcileDeps(
        client=client,
        bot_state=bot_state,
        bot_state_lock=_bot_state_lock,
        save_fn=save_bot_state,
        send_alert_fn=send_trading_alert_email,
        place_sl_fn=place_exchange_stop_loss_order,
        get_exchange_info_fn=get_cached_exchange_info,
    )


def _make_trading_deps() -> _TradingDeps:
    """C-03: Injecte les globaux du module dans les fonctions de order_manager."""
    return _TradingDeps(
        client=client,
        bot_state=bot_state,
        bot_state_lock=_bot_state_lock,
        save_fn=save_bot_state,
        send_alert_fn=send_trading_alert_email,
        place_sl_fn=place_exchange_stop_loss_order,
        market_sell_fn=safe_market_sell,
        market_buy_fn=safe_market_buy,
        update_daily_pnl_fn=_update_daily_pnl,
        is_loss_limit_fn=_is_daily_loss_limit_reached,
        gen_buy_checker_fn=generate_buy_condition_checker,
        gen_sell_checker_fn=generate_sell_condition_checker,
        check_order_executed_fn=check_if_order_executed,
        get_usdc_sells_fn=get_usdc_from_all_sells_since_last_buy,
        get_sniper_entry_fn=get_sniper_entry_price,
        check_partial_exits_fn=check_partial_exits_from_history,
        console=console,
        config=config,
        is_valid_stop_loss_fn=is_valid_stop_loss_order,
    )


def _make_backtest_deps() -> _BacktestDeps:
    """C-03 Phase 3: Injecte les globaux dans les fonctions de backtest_orchestrator."""
    return _BacktestDeps(
        bot_state=bot_state,
        bot_state_lock=_bot_state_lock,
        config=config,
        client=client,
        console=console,
        timeframes=timeframes,
        schedule=schedule,
        save_fn=save_bot_state,
        send_alert_fn=send_trading_alert_email,
        send_email_alert_fn=send_email_alert,
        execute_trades_fn=execute_real_trades,
        run_all_backtests_fn=run_all_backtests,
        prepare_base_dataframe_fn=prepare_base_dataframe,
        display_results_fn=display_results_for_pair,
        display_execution_header_fn=display_execution_header,
        build_tracking_panel_fn=build_tracking_panel,
        display_market_changes_fn=display_market_changes,
        detect_market_changes_fn=detect_market_changes,
        display_backtest_table_fn=display_backtest_table,
        backtest_from_dataframe_fn=backtest_from_dataframe,
        select_best_by_calmar_fn=_select_best_by_calmar,
        make_default_pair_state_fn=_make_default_pair_state,
        last_backtest_time=_runtime.last_backtest_time,
        live_best_params=_runtime.live_best_params,
        oos_alert_last_sent=_oos_alert_last_sent,
        oos_alert_lock=_oos_alert_lock,
        wf_scenarios=WF_SCENARIOS,
        scenario_default_params=SCENARIO_DEFAULT_PARAMS,
    )


def _check_pair_vs_exchange(pair_info: Dict[str, Any]) -> 'Optional[_PairStatus]':
    """C-03 wrapper — délègue à position_reconciler avec les globaux injectés."""
    return _check_pair_impl(pair_info, _make_reconcile_deps())


def _handle_pair_discrepancy(status: '_PairStatus') -> None:
    """C-03 wrapper — délègue à position_reconciler avec les globaux injectés."""
    return _handle_pair_impl(status, _make_reconcile_deps())


def reconcile_positions_with_exchange(crypto_pairs_list: List[Dict[str, Any]]) -> None:
    """Vérifie la cohérence entre bot_state et les positions réelles sur Binance.

    Côté MULTI_SYMBOLS: wrapper qui injecte les globaux dans position_reconciler.
    """
    return _reconcile_impl(crypto_pairs_list, _make_reconcile_deps())


# --- Data Fetching (delegated to data_fetcher.py) ---

def fetch_historical_data(pair_symbol: str, time_interval: str, start_date: str, force_refresh: bool = False) -> pd.DataFrame:
    """Thin wrapper — delegates to data_fetcher with injected globals."""
    return _fetch_historical_data(
        pair_symbol, time_interval, start_date, client,
        force_refresh=force_refresh,
        verbose_logs=VERBOSE_LOGS,
        check_network_fn=check_network_connectivity,
        send_alert_fn=send_trading_alert_email,
        data_error_template_fn=data_retrieval_error_email,
        network_error_template_fn=network_error_email,
    )

# --- Indicator Calculation (delegated to indicators_engine.py) ---

def calculate_indicators(df: pd.DataFrame, ema1_period: int, ema2_period: int, stoch_period: int = 14,
                         sma_long: Optional[int] = None, adx_period: Optional[int] = None,
                         trix_length: Optional[int] = None, trix_signal: Optional[int] = None) -> pd.DataFrame:
    def _on_error(msg: str) -> None:
        try:
            subj, body = indicator_error_email(msg)
            send_trading_alert_email(subject=subj, body_main=body, client=client)
        except Exception as _e:
            logger.warning("[INDICATORS] Email alerte impossible: %s", _e)
    return _calculate_indicators(
        df, ema1_period, ema2_period, stoch_period=stoch_period,
        sma_long=sma_long, adx_period=adx_period,
        trix_length=trix_length, trix_signal=trix_signal,
        on_error=_on_error,
    )

def universal_calculate_indicators(df: pd.DataFrame, ema1_period: int, ema2_period: int,
                                   stoch_period: int = 14, sma_long: Optional[int] = None,
                                   adx_period: Optional[int] = None, trix_length: Optional[int] = None,
                                   trix_signal: Optional[int] = None) -> pd.DataFrame:
    def _on_error(msg: str) -> None:
        try:
            subj, body = indicator_error_email(msg)
            send_trading_alert_email(subject=subj, body_main=body, client=client)
        except Exception as _e:
            logger.warning("[INDICATORS] Email alerte impossible: %s", _e)
    return _universal_calculate_indicators(
        df, ema1_period, ema2_period, stoch_period=stoch_period,
        sma_long=sma_long, adx_period=adx_period,
        trix_length=trix_length, trix_signal=trix_signal,
        on_error=_on_error,
    )

# --- Backtest Functions ---
def prepare_base_dataframe(pair: str, timeframe: str, start_date: str, stoch_period: int = 14) -> Optional[pd.DataFrame]:
    return _prepare_base_dataframe(
        pair, timeframe, start_date, stoch_period=stoch_period,
        fetch_data_fn=fetch_historical_data,
    )

def get_binance_trading_fees(client: Any, symbol: str = 'TRXUSDC') -> Tuple[float, float]:
    """Thin wrapper — delegates to data_fetcher with config fallbacks."""
    return _get_binance_trading_fees(
        client, symbol,
        default_taker=config.taker_fee,
        default_maker=config.maker_fee,
    )

# backtest_from_dataframe, empty_result_dict, run_single_backtest_optimized
# imported directly from backtest_runner.py (P3-SRP)

def run_all_backtests(backtest_pair: str, start_date: str, timeframes: List[str], sizing_mode: str = 'risk') -> List[Dict[str, Any]]:
    return _run_all_backtests(
        backtest_pair, start_date, timeframes,
        sizing_mode=sizing_mode,
        prepare_base_dataframe_fn=prepare_base_dataframe,
    )

def run_parallel_backtests(crypto_pairs: List[Dict[str, str]], start_date: str, timeframes: List[str], sizing_mode: str = 'risk') -> Dict[str, Any]:
    return _run_parallel_backtests(
        crypto_pairs, start_date, timeframes,
        sizing_mode=sizing_mode,
        prepare_base_dataframe_fn=prepare_base_dataframe,
    )

# display_results_for_pair extraite dans display_ui.py (Phase 5)

# --- Live Trading Functions ---
# generate_buy_condition_checker imported from signal_generator.py (P3-SRP)
# generate_sell_condition_checker wrapper  injects config
def generate_sell_condition_checker(best_params: Dict[str, Any]) -> Callable[..., Tuple[bool, Optional[str]]]:
    return _generate_sell_condition_checker(best_params, config=config)

# sync_windows_silently moved to timestamp_utils.py (P3-SRP)

# init_timestamp_solution moved to timestamp_utils.py (P3-SRP)

# check_network_connectivity moved to timestamp_utils.py (P3-SRP)

# --- Trade Helpers (delegated to trade_helpers.py) ---

def get_sniper_entry_price(pair_symbol: str, signal_price: float, max_wait_candles: int = 4) -> float:
    return _get_sniper_entry_price(
        pair_symbol, signal_price, max_wait_candles,
        fetch_data_fn=fetch_historical_data,
        kline_interval_15m=Client.KLINE_INTERVAL_15MINUTE,
    )

def get_last_sell_trade_usdc(real_trading_pair: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    result = _get_last_sell_trade_usdc(real_trading_pair, client)
    return cast(Tuple[Optional[float], Optional[float], Optional[str]], result)

def get_usdc_from_all_sells_since_last_buy(real_trading_pair: str) -> float:
    return _get_usdc_from_all_sells(real_trading_pair, client)

def check_partial_exits_from_history(real_trading_pair: str, entry_price: float) -> Tuple[bool, bool]:
    return _check_partial_exits(real_trading_pair, entry_price, client)

# check_if_order_executed imported directly from trade_helpers.py

# P1-04: Backward-compat aliases for _BacktestDeps (oos_alert_last_sent/lock).
# _runtime.live_best_params / _runtime.last_backtest_time hold the actual dicts.
_oos_alert_last_sent = _runtime.oos_alert_throttle.last_sent   # compat _BacktestDeps
_oos_alert_lock = _runtime.oos_alert_throttle.lock              # compat _BacktestDeps


def _read_live_params(pair: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Lecture thread-safe de _runtime.live_best_params avec fallback."""
    with _bot_state_lock:
        return dict(_runtime.live_best_params.get(pair, fallback))


# ─── P2-05: OOS quality gate centralisée ──────────────────────────────────────

def apply_oos_quality_gate(
    results: List[Dict[str, Any]],
    pair: str,
    *,
    log_tag: str = "C-13",
    unblock_on_pass: bool = True,
    send_alert: bool = False,
    save_force: bool = False,
) -> Tuple[List[Dict[str, Any]], bool]:
    """C-03 Phase 3 wrapper ? delegue a backtest_orchestrator._apply_oos_quality_gate."""
    deps = _make_backtest_deps()
    return _apply_oos_quality_gate(results, pair, deps, log_tag=log_tag,
                                   unblock_on_pass=unblock_on_pass,
                                   send_alert=send_alert, save_force=save_force)

def execute_scheduled_trading(real_trading_pair: str, time_interval: str, best_params: Dict[str, Any], backtest_pair: str, sizing_mode: str) -> None:
    """C-03 Phase 3 wrapper ? delegue a backtest_orchestrator._execute_scheduled_trading."""
    deps = _make_backtest_deps()
    _execute_scheduled_trading(real_trading_pair, time_interval, best_params, backtest_pair, sizing_mode, deps)

def execute_live_trading_only(real_trading_pair: str, backtest_pair: str, sizing_mode: str) -> None:
    """C-03 Phase 3 wrapper ? delegue a backtest_orchestrator._execute_live_trading_only."""
    deps = _make_backtest_deps()
    _execute_live_trading_only(real_trading_pair, backtest_pair, sizing_mode, deps)

def _fetch_balances(real_trading_pair: str) -> Optional[Tuple[Any, str, str, float, float, float, float]]:
    """Récupère les soldes coin + quote pour la paire donnée.

    Returns:
        (account_info, coin_symbol, quote_currency,
         usdc_balance, coin_balance_free, coin_balance_locked, coin_balance)
        ou None si le coin n'est pas trouvé dans le portefeuille.

    Raises:
        BalanceUnavailableError: si l'API Binance échoue (P0-02).
    """
    from exceptions import BalanceUnavailableError  # pylint: disable=import-outside-toplevel
    try:
        account_info = client.get_account()
    except Exception as _api_err:
        raise BalanceUnavailableError(
            f"_fetch_balances: client.get_account() a échoué: {_api_err}"
        ) from _api_err
    coin_symbol, quote_currency = extract_coin_from_pair(real_trading_pair)

    _, usdc_balance_free_val, _, _ = _get_coin_balance(account_info, 'USDC')
    usdc_balance = usdc_balance_free_val

    _coin_found, coin_balance_free, coin_balance_locked, coin_balance = _get_coin_balance(
        account_info, coin_symbol
    )
    if not _coin_found:
        return None

    return (
        account_info, coin_symbol, quote_currency,
        usdc_balance, coin_balance_free, coin_balance_locked, coin_balance,
    )


def _fetch_symbol_filters(real_trading_pair: str) -> Optional[Tuple[float, float, float, float, Any, Any, Any, int]]:
    """Récupère LOT_SIZE et MIN_NOTIONAL pour la paire.

    Returns:
        (min_qty, max_qty, step_size, min_notional,
         min_qty_dec, max_qty_dec, step_size_dec, step_decimals, symbol_info)
        ou None si le symbole ou le filtre LOT_SIZE est introuvable.
    """
    exchange_info = get_cached_exchange_info(client)
    symbol_info = next(
        (s for s in exchange_info['symbols']  # pylint: disable=unsubscriptable-object
         if s['symbol'] == real_trading_pair), None
    )
    if not symbol_info:
        console.print(f"[ERREUR] Informations symbole introuvables pour {real_trading_pair}.")
        return None

    lot_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
    notional_filter = next(
        (f for f in symbol_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'), None
    )
    if not lot_filter:
        console.print("[ERREUR] Filtre LOT_SIZE non trouvé.")
        return None

    min_qty = float(lot_filter['minQty'])
    max_qty = float(lot_filter['maxQty'])
    step_size = float(lot_filter['stepSize'])
    min_notional = float(notional_filter.get('minNotional', '10.0')) if notional_filter else 10.0

    min_qty_dec = Decimal(str(min_qty))
    max_qty_dec = Decimal(str(max_qty))
    step_size_dec = Decimal(str(step_size))
    step_decimals = abs(int(step_size_dec.as_tuple().exponent))

    return (
        min_qty, max_qty, step_size, min_notional,
        min_qty_dec, max_qty_dec, step_size_dec, step_decimals,
    )


def _fetch_indicators(real_trading_pair: str, time_interval: str, best_params: Dict[str, Any]) -> Optional[Tuple[pd.DataFrame, Any, float]]:
    """Récupère les données de marché et calcule les indicateurs.

    Returns:
        (df, row, current_price) ou None si données insuffisantes.
    """
    _start = _fresh_start_date()
    df = fetch_historical_data(real_trading_pair, time_interval, _start, force_refresh=True)
    df = universal_calculate_indicators(
        df,
        best_params.get('ema1_period') or 26,
        best_params.get('ema2_period') or 50,
        stoch_period=best_params.get('stoch_period', 14),
        sma_long=best_params.get('sma_long'),
        adx_period=best_params.get('adx_period'),
        trix_length=best_params.get('trix_length'),
        trix_signal=best_params.get('trix_signal'),
    )

    if df.empty or len(df) < 2:
        logger.error(
            "[TRADING] Données insuffisantes pour %s: %d lignes – cycle ignoré",
            real_trading_pair, len(df),
        )
        return None

    row = df.iloc[-2].copy()  # mutable copy — safe for feature injection below

    # A-2: Multi-timeframe filter — compute 4h EMA trend for the signal row
    if getattr(config, 'mtf_filter_enabled', False) and isinstance(df.index, pd.DatetimeIndex):
        try:
            _ema_fast = getattr(config, 'mtf_ema_fast', 18)
            _ema_slow = getattr(config, 'mtf_ema_slow', 58)
            # Resample 1h → 4h, compute EMAs, shift(1) to avoid look-ahead
            _df_4h_close = df['close'].resample('4h').last().dropna()
            _ema_f_4h = _df_4h_close.ewm(span=_ema_fast, adjust=False).mean()
            _ema_s_4h = _df_4h_close.ewm(span=_ema_slow, adjust=False).mean()
            _bullish_4h = (_ema_f_4h > _ema_s_4h).astype(float).shift(1).fillna(0.0)
            _bullish_1h = _bullish_4h.reindex(df.index, method='ffill').fillna(0.0)
            row['mtf_bullish'] = _bullish_1h.iloc[-2] if len(_bullish_1h) >= 2 else 0.0
        except Exception as _mtf_err:
            logger.warning("[A-2] MTF computation failed: %s — filter disabled for this cycle", _mtf_err)

    # ML-03: Inject ATR median (last 30 days) for adaptive stop multiplier at entry
    if 'atr' in df.columns and isinstance(df.index, pd.DatetimeIndex):
        try:
            _cutoff_30d = df.index[-1] - pd.Timedelta(days=30)
            _atr_median = df.loc[df.index >= _cutoff_30d, 'atr'].dropna().median()
            if pd.notna(_atr_median) and _atr_median > 0:
                row['atr_median_30d'] = float(_atr_median)
        except Exception as _atr_med_err:
            logger.debug("[ML-03] ATR median 30d skipped: %s", _atr_med_err)

    current_price = float(client.get_symbol_ticker(symbol=real_trading_pair)['price'])
    return df, row, current_price


def _sync_order_history(real_trading_pair: str, pair_state: 'PairState') -> Tuple[List[Any], Optional[str]]:
    """Synchronise last_order_side avec l'historique des ordres Binance.

    Returns:
        (orders, last_side)
    """
    orders = client.get_all_orders(symbol=real_trading_pair, limit=20)
    if not isinstance(orders, list):
        orders = [orders] if orders else []
    filled_orders = [o for o in reversed(orders) if o['status'] == 'FILLED']
    last_filled_order = filled_orders[0] if filled_orders else None
    last_side = last_filled_order['side'] if last_filled_order else None

    if last_side and pair_state.get('last_order_side') != last_side:
        pair_state['last_order_side'] = last_side
        if last_side != 'BUY':  # ST-P2-02: réinitialise le flag drawdown à la fermeture de position
            pair_state['drawdown_halted'] = False
        save_bot_state()

    return orders, last_side


def execute_real_trades(real_trading_pair: str, time_interval: str, best_params: Dict[str, Any], backtest_pair: str, sizing_mode: str = 'risk') -> None:
    """
    Exécution complète des trades réels avec gestion totale du cycle achat/vente,
    stop-loss, trailing-stop, sniper entry, envoi d'emails d'alerte et affichage console.
    Stratégie d'origine préservée intégralement.

    Args:
        sizing_mode: Position sizing strategy ('baseline', 'risk', 'fixed_notional', 'volatility_parity')
                    DEFAULT='risk' (P1-07: risk-based ATR stop au lieu de 95% capital)
    """
    # === PROTECTION CONTRE LES EXÉCUTIONS CONCURRENTES PAR PAIRE (C-01 / C-02) ===
    # Non-blocking acquire : si une exécution est déjà en cours pour cette paire,
    # on ignore silencieusement ce cycle pour éviter les double-partiels et les
    # race conditions sur bot_state / pair_state.
    with _pair_locks_mutex:
        if backtest_pair not in _pair_execution_locks:
            _pair_execution_locks[backtest_pair] = threading.Lock()
    _pair_lock = _pair_execution_locks[backtest_pair]
    if not _pair_lock.acquire(blocking=False):
        logger.warning(f"[CONCURRENCE] Exécution concurrente détectée pour {backtest_pair} — cycle ignoré (C-02)")
        return
    try:
        return _execute_real_trades_inner(real_trading_pair, time_interval, best_params, backtest_pair, sizing_mode)
    finally:
        _pair_lock.release()


def _execute_real_trades_inner(real_trading_pair: str, time_interval: str, best_params: Dict[str, Any], backtest_pair: str, sizing_mode: str = 'risk') -> None:
    """Implémentation interne de execute_real_trades (appelée sous per-pair lock).

    P3-04: logique de fetching déléguée aux helpers _fetch_balances, _fetch_symbol_filters,
    _fetch_indicators, _sync_order_history.
    """
    # P0-STOP: emergency halt — tous les achats bloqués si un double échec SL+rollback a eu lieu
    if bot_state.get('emergency_halt'):
        logger.critical(
            "[EMERGENCY HALT] Tous les achats bloqués — raison: %s. "
            "Supprimez la clé 'emergency_halt' du bot_state pour relancer.",
            bot_state.get('emergency_halt_reason', 'inconnue'),
        )
        return

    # ST-P1-01: guard anti-corrélation — limite le nombre de positions longues simultanées
    with _bot_state_lock:
        _open_longs = [
            p for p, s in bot_state.items()
            if isinstance(s, dict) and s.get('last_order_side') == 'BUY'
        ]
    if len(_open_longs) >= config.max_concurrent_long:
        logger.info(
            "Max positions longues atteint (%d/%d) — achat bloqué pour %s",
            len(_open_longs), config.max_concurrent_long, backtest_pair,
        )
        return

    # pair_state dérivé depuis bot_state — les mutations du dict se propagent par référence.
    with _bot_state_lock:
        pair_state: PairState = cast('PairState', bot_state.setdefault(backtest_pair, {}))
    if 'last_order_side' not in pair_state:
        pair_state['last_order_side'] = None

    # EM-P1-04: Alerte si position BUY ouverte sans SL confirmé sur l'exchange
    if pair_state.get('last_order_side') == 'BUY' and not pair_state.get('sl_exchange_placed'):
        if _runtime.sl_missing_throttle.check_and_mark(key=backtest_pair):
            logger.critical(
                "[SL-MANQUANT] %s en position BUY sans sl_exchange_placed=True.",
                backtest_pair,
            )
            try:
                send_trading_alert_email(
                    subject=f"[CRITIQUE] Position sans SL exchange: {backtest_pair}",
                    body_main=(
                        f"La paire {backtest_pair} est en position BUY "
                        f"mais sl_exchange_placed=False.\n\n"
                        f"Le stop-loss n'est pas confirmé sur Binance. "
                        f"Vérifiez manuellement et relancez si nécessaire.\n\n"
                        f"Prix d'entrée  : {pair_state.get('entry_price', 'N/A')}\n"
                        f"Stop prévu     : {pair_state.get('stop_loss', 'N/A')}\n"
                        f"sl_order_id    : {pair_state.get('sl_order_id', 'N/A')}\n"
                    ),
                    client=client,
                )
            except Exception as _sl_alert_err:
                logger.error("[SL-MANQUANT] Email impossible: %s", _sl_alert_err)

    # F-COH: Si position ouverte, verrouiller les params de l’entrée pour le signal de vente.
    # Garantit que le sell est évalué sur le même scenario/TF/EMA que l’achat,
    # même si le WF a sélectionné une stratégie différente entre-temps.
    if pair_state.get('last_order_side') == 'BUY' and pair_state.get('entry_scenario'):
        _f_coh_tf = pair_state.get('entry_timeframe')
        _f_coh_ema1 = pair_state.get('entry_ema1')
        _f_coh_ema2 = pair_state.get('entry_ema2')
        _f_coh_scenario = pair_state.get('entry_scenario')
        if (_f_coh_tf is not None and _f_coh_ema1 is not None
                and _f_coh_ema2 is not None and _f_coh_scenario is not None):
            time_interval = _f_coh_tf
            best_params = {
                **best_params,
                'timeframe': _f_coh_tf,
                'ema1_period': _f_coh_ema1,
                'ema2_period': _f_coh_ema2,
                'scenario': _f_coh_scenario,
            }
            logger.info(
                "[F-COH] Position ouverte — indicateurs verrouillés sur params entrée: %s EMA(%s/%s) %s",
                _f_coh_scenario, _f_coh_ema1,
                _f_coh_ema2, _f_coh_tf,
            )

    # Paramètres stratégiques
    ema1_period = best_params.get('ema1_period') or 26
    ema2_period = best_params.get('ema2_period') or 50
    scenario = best_params.get('scenario', 'StochRSI')

    try:
        # === P3-04: COMPTES & SOLDES (helper) ===
        bal = _fetch_balances(real_trading_pair)
        if bal is None:
            # P0-02: si position BUY ouverte, coin manquant = anomalie critique
            if pair_state.get('last_order_side') == 'BUY':
                logger.critical(
                    "[P0-02] Coin introuvable dans le portefeuille pour %s "
                    "alors que position BUY ouverte. Vérifier l'API ou la réconciliation.",
                    real_trading_pair,
                )
            return
        (account_info, coin_symbol, quote_currency,
         usdc_balance, coin_balance_free, coin_balance_locked, coin_balance) = bal

        # === P3-04: FILTRES PAIRE (helper) ===
        flt = _fetch_symbol_filters(real_trading_pair)
        if flt is None:
            # P0-02: filtre manquant avec position BUY = stop-loss management compromis
            if pair_state.get('last_order_side') == 'BUY':
                logger.critical(
                    "[P0-02] Filtres Binance introuvables pour %s avec position BUY ouverte "
                    "— SL management compromis ce cycle.",
                    real_trading_pair,
                )
            return
        (min_qty, max_qty, step_size, min_notional,
         min_qty_dec, max_qty_dec, step_size_dec, step_decimals) = flt

        # Afficher le panel des soldes
        # Ne montrer les données d'entrée que si position réelle (qty + notional)
        _has_real_position = (
            coin_balance >= min_qty
            and coin_balance * (pair_state.get('entry_price') or 0) >= min_notional
        )
        last_buy_price = pair_state.get('entry_price') if _has_real_position else None
        atr_at_entry = pair_state.get('atr_at_entry') if _has_real_position else None
        display_account_balances_panel(
            account_info, coin_symbol, quote_currency, client, console,
            pair_state=cast(Dict[str, Any], pair_state),
            last_buy_price=last_buy_price, atr_at_entry=atr_at_entry
        )

        # === P3-04: DONNÉES & INDICATEURS (helper) ===
        mkt = _fetch_indicators(real_trading_pair, time_interval, best_params)
        if mkt is None:
            return
        _, row, current_price = mkt

        # EM-P2-05: Alerte drawdown max si PnL non réalisé dépasse le seuil configuré
        if (
            pair_state.get('last_order_side') == 'BUY'
            and pair_state.get('entry_price')
            and config.max_drawdown_pct > 0
        ):
            _entry_p = float(pair_state['entry_price'])
            _drawdown = (current_price - _entry_p) / _entry_p
            if _drawdown < -config.max_drawdown_pct:
                if _runtime.drawdown_throttle.check_and_mark(key=backtest_pair):
                    pair_state['drawdown_halted'] = True  # ST-P2-02: persisté dans bot_state
                    save_bot_state()
                    logger.critical(
                        "[DRAWDOWN] %s — PnL non réalisé: %.2f%% (seuil: -%.0f%%).",
                        backtest_pair, _drawdown * 100, config.max_drawdown_pct * 100,
                    )
                    try:
                        send_trading_alert_email(
                            subject=f"[CRITIQUE] Drawdown max atteint: {backtest_pair}",
                            body_main=(
                                f"La position {backtest_pair} dépasse le seuil de drawdown.\n\n"
                                f"Prix d'entrée   : {_entry_p:.6f} USDC\n"
                                f"Prix actuel     : {current_price:.6f} USDC\n"
                                f"PnL non réalisé : {_drawdown*100:.2f}%\n"
                                f"Seuil configuré : -{config.max_drawdown_pct*100:.0f}%\n\n"
                                f"Le stop-loss devrait couvrir cette situation. "
                                f"Vérifiez l'état du SL sur Binance."
                            ),
                            client=client,
                        )
                    except Exception as _dd_alert_err:
                        logger.error("[DRAWDOWN] Email impossible: %s", _dd_alert_err)

        # === P3-04: HISTORIQUE ORDRES (helper) ===
        orders, last_side = _sync_order_history(real_trading_pair, pair_state)

        # === C-15: BUILD CONTEXT + DELEGATE TO SUB-FUNCTIONS ===
        deps = _make_trading_deps()
        ctx = _TradeCtx(
            real_trading_pair=real_trading_pair, backtest_pair=backtest_pair,
            time_interval=time_interval, sizing_mode=sizing_mode,
            pair_state=pair_state, best_params=best_params,
            ema1_period=ema1_period, ema2_period=ema2_period, scenario=scenario,
            coin_symbol=coin_symbol, quote_currency=quote_currency,
            usdc_balance=usdc_balance, coin_balance_free=coin_balance_free,
            coin_balance_locked=coin_balance_locked, coin_balance=coin_balance,
            current_price=current_price, row=row, orders=orders,
            min_qty=min_qty, max_qty=max_qty, step_size=step_size,
            min_notional=min_notional, min_qty_dec=min_qty_dec,
            max_qty_dec=max_qty_dec, step_size_dec=step_size_dec,
            step_decimals=step_decimals,
        )

        _sync_entry_state(ctx, last_side, deps)
        _update_trailing_stop(ctx, deps)
        _execute_partial_sells(ctx, deps)
        if _check_and_execute_stop_loss(ctx, deps):
            return

        # Initial position size tracking
        if pair_state.get('last_order_side') == 'BUY' and pair_state.get('initial_position_size') is None and ctx.coin_balance > min_qty * 1.01:
            pair_state['initial_position_size'] = ctx.coin_balance
            save_bot_state()

        position_has_crypto = _handle_dust_cleanup(ctx, deps)
        if position_has_crypto:
            _execute_signal_sell(ctx, deps)
        else:
            # TS-P2-02: blocage achats si réconciliation démarrage échouée
            if bot_state.get('reconcile_failed'):
                logger.warning(
                    "[RECONCILE TS-P2-02] Achat bloqué pour %s — réconciliation démarrage échouée. "
                    "Supprimez 'reconcile_failed' du bot_state après vérification manuelle.",
                    backtest_pair,
                )
                return
            _execute_buy(ctx, deps)

    except SizingError as e:
        logger.warning(
            "[BUY SKIP P0-05] %s — SizingError: %s",
            real_trading_pair, e,
        )
        return
    except Exception as e:
        logger.error(f"Erreur inattendue dans execute_real_trades : {e}")
        console.print(f"Erreur lors de l'execution de l'ordre : {e}")
        return  # C-15: prevent fallthrough to old dead code below

def detect_market_changes(pair: str, timeframes: List[str], start_date: str) -> Dict[str, Any]:
    # P3-SRP: delegated to market_analysis.py
    return _detect_market_changes(pair, timeframes, start_date, prepare_base_dataframe)

# display_market_changes extraite dans display_ui.py (Phase 5)

def backtest_and_display_results(backtest_pair: str, real_trading_pair: str, start_date: str, timeframes: List[str], sizing_mode: str = 'risk') -> None:
    """C-03 Phase 3 wrapper ? delegue a backtest_orchestrator._backtest_and_display_results."""
    deps = _make_backtest_deps()
    _backtest_and_display_results(backtest_pair, real_trading_pair, start_date, timeframes, sizing_mode, deps)

if __name__ == "__main__":

    full_timestamp_resync()
    logger.info("Synchronisation complète exécutée au démarrage.")

    # MODE ULTRA-ROBUSTE SANS POPUP - COMPENSATION BINANCE PURE
    logger.info("Bot crypto H24/7 - Mode ultra-robuste avec privileges admin")

    crypto_pairs = [
        {"backtest_pair": "HBARUSDT", "real_pair": "HBARUSDC"},
    ]
    _voluntary_event   = threading.Event()
    _shutdown_verified = threading.Event()

    # P0-SHUT: Définie avant le try pour être toujours accessible dans les handlers except.
    def _verify_all_stops_on_shutdown(reason: str = "unknown", send_email: bool = True) -> None:
        """C-11: Vérifie les stops actifs sur Binance pour chaque position ouverte.

        Envoie un email CRITICAL si une position BUY n'a pas de stop-loss.
        Appelée depuis SIGTERM, KeyboardInterrupt et atexit.
        Si send_email=False (arrêt volontaire CTRL+C), log seulement sans email.
        """
        try:
            pair_lookup = {p['backtest_pair']: p['real_pair'] for p in crypto_pairs}
            for bp, ps in list(bot_state.items()):
                if not isinstance(ps, dict) or ps.get('last_order_side') != 'BUY':
                    continue
                real_sym = pair_lookup.get(bp, bp)
                try:
                    open_orders = client.get_open_orders(symbol=real_sym)
                    stop_types = {'STOP_LOSS', 'STOP_LOSS_LIMIT', 'TAKE_PROFIT',
                                  'TAKE_PROFIT_LIMIT', 'OCO'}
                    has_stop = any(o.get('type', '') in stop_types for o in open_orders)
                    if not has_stop:
                        logger.critical(
                            "[SHUTDOWN C-11] AUCUN stop-loss actif sur Binance pour %s "
                            "(%s) alors que la position est ouverte!",
                            bp, real_sym,
                        )
                        if send_email:
                            try:
                                send_trading_alert_email(
                                    subject=f"[CRITIQUE] Stop manquant au shutdown: {bp}",
                                    body_main=(
                                        f"Le bot s'arrête ({reason}).\n\n"
                                        f"La paire {bp} ({real_sym}) est en position BUY "
                                        f"mais AUCUN stop-loss n'a été trouvé parmi les ordres "
                                        f"ouverts sur Binance.\n\n"
                                        f"ACTION REQUISE: poser un stop manuellement."
                                    ),
                                    client=client,
                                )
                            except Exception as _mail_err:
                                logger.error("[SHUTDOWN C-11] Email critique impossible: %s", _mail_err)
                        else:
                            logger.warning(
                                "[SHUTDOWN C-11] Email supprimé (arrêt volontaire CTRL+C). "
                                "Stop manquant pour %s — vérifiez manuellement.", bp
                            )
                    else:
                        logger.info("[SHUTDOWN C-11] Stop actif confirmé pour %s", bp)
                except Exception as _ord_err:
                    logger.error(
                        "[SHUTDOWN C-11] Impossible de récupérer les ordres pour %s: %s",
                        real_sym, _ord_err,
                    )
        except Exception as _shutdown_check_err:
            logger.error("[SHUTDOWN C-11] Erreur vérification stops: %s", _shutdown_check_err)

    try:
        # SOLUTION ULTRA-ROBUSTE TIMESTAMP
        logger.info("=== INITIALISATION TIMESTAMP ULTRA-ROBUSTE ===")
        if not init_timestamp_solution():
            logger.error("IMPOSSIBLE D'INITIALISER LA SYNCHRONISATION")

        # Re-synchroniser avant chaque session de trading
        client._sync_server_time()  # pylint: disable=protected-access
        logger.info("Synchronisation timestamp pre-trading terminee")

        # Validation de la connexion API au demarrage
        if not validate_api_connection():
            logger.error("Impossible de valider la connexion API. Arret du programme.")
            exit(1)

        # Récupération des frais réels depuis l'API Binance (P0-01: stockés dans _runtime)
        real_taker, real_maker = get_binance_trading_fees(client)
        _runtime.taker_fee = real_taker
        _runtime.maker_fee = real_maker
        logger.info(
            "[P0-01] Frais live Binance: taker=%.5f maker=%.5f",
            _runtime.taker_fee, _runtime.maker_fee,
        )

        # Chargement de l'etat du bot
        load_bot_state()

        # C-03: Réconciliation positions au démarrage — détecte les positions orphelines
        # (achat exécuté avant un crash, état non sauvegardé)
        try:
            reconcile_positions_with_exchange(crypto_pairs)
            with _bot_state_lock:
                bot_state['reconcile_failed'] = False
            save_bot_state(force=True)
        except Exception as reconcile_err:
            logger.error(
                "[RECONCILE TS-P2-02] Erreur lors de la réconciliation: %s",
                reconcile_err, exc_info=True,
            )
            with _bot_state_lock:
                bot_state['reconcile_failed'] = True
            save_bot_state(force=True)
            try:
                send_trading_alert_email(
                    subject="[CRITIQUE TS-P2-02] Réconciliation échouée au démarrage — achats bloqués",
                    body_main=(
                        f"La réconciliation des positions avec Binance a échoué au démarrage du bot.\n\n"
                        f"Erreur : {reconcile_err}\n\n"
                        f"Les achats sont bloqués jusqu'à résolution. "
                        f"Les positions existantes et stops restent surveillés.\n\n"
                        f"Supprimez la clé 'reconcile_failed' du bot_state après vérification manuelle."
                    ),
                    client=client,
                )
            except Exception as _mail_err:
                logger.warning("[RECONCILE] Email alerte impossible: %s", _mail_err)

        logger.info("Script demarre. Planification initiale en cours...")
        # Purge préventive: supprimer toute planification résiduelle
        # Nettoyage renforcé de la planification
        try:
            schedule.clear()
            logger.info("Planification nettoyee au demarrage (schedule.clear())")
        except Exception as _clear_ex:
            logger.debug(f"Echec nettoyage planification au demarrage: {_clear_ex}")

        # Planification du nettoyage du cache tous les 30 jours
        schedule.every(30).days.do(cleanup_expired_cache)
        logger.info("Nettoyage automatique du cache planifié: tous les 30 jours")

        # P1-08: Resynchronisation timestamp périodique (toutes les 30 min)
        # Le drift horloge locale vs serveur Binance s'accumule et provoque
        # des erreurs recvWindow après quelques heures sans resync.
        def _periodic_timestamp_resync() -> None:
            try:
                full_timestamp_resync()
                logger.info("[TIMESTAMP P1-08] Resync périodique OK")
            except Exception as _ts_err:
                logger.warning("[TIMESTAMP P1-08] Resync échouée: %s", _ts_err)
        schedule.every(30).minutes.do(_periodic_timestamp_resync)
        logger.info("[TIMESTAMP P1-08] Resync timestamp planifiée: toutes les 30 min")

        # P2-04: Export métriques toutes les 5 minutes
        def _periodic_metrics_write() -> None:
            try:
                cb = error_handler.circuit_breaker if 'error_handler' in dir() else None
                _write_metrics(
                    bot_state=bot_state,
                    runtime=_runtime,
                    circuit_breaker=cb,
                    pairs=list(crypto_pairs),
                )
            except Exception as _m_err:
                logger.debug("[METRICS P2-04] Échec export: %s", _m_err)
        schedule.every(5).minutes.do(_periodic_metrics_write)
        logger.info("[METRICS P2-04] Export métriques planifié: toutes les 5 min")

        # === NOUVELLE LOGIQUE : backtests optimisés + affichage propre ===
        parser = argparse.ArgumentParser(description='Run backtests and optional sizing mode')
        parser.add_argument('--sizing-mode', choices=['baseline', 'risk', 'fixed_notional', 'volatility_parity'], default=config.sizing_mode, help='Position sizing mode to use for backtests')
        args, unknown = parser.parse_known_args()

        # Exécuter les backtests avec affichage propre (passer sizing_mode)
        # P1-01: start_date recalculé au moment de l'appel
        all_results = run_parallel_backtests(crypto_pairs, _fresh_start_date(), timeframes, sizing_mode=args.sizing_mode)

        # === AFFICHAGE PROPRE, SANS CHEVAUCHEMENT ===
        for backtest_pair, data in all_results.items():
            if not data['results']:
                console.print(f"[red]Aucun résultat pour {backtest_pair}[/red]")
                continue

            # C-07 + C-13 + P2-05: OOS quality gate centralisée
            _pool_loop, _oos_blocked_this_pair = apply_oos_quality_gate(
                data['results'], backtest_pair,
                log_tag="MAIN-LOOP C-03", send_alert=True,
                save_force=True, unblock_on_pass=True,
            )

            # === F-BUG2: Walk-Forward validation at startup (like scheduled/main) ===
            _startup_wf_best = None
            try:
                from walk_forward import run_walk_forward_validation as _run_wf_startup
                _wf_dfs_startup = {}
                _startup_start = _fresh_start_date()
                for _tf_s in timeframes:
                    _df_s = prepare_base_dataframe(backtest_pair, _tf_s, _startup_start, 14)
                    _wf_dfs_startup[_tf_s] = _df_s if _df_s is not None and not _df_s.empty else pd.DataFrame()

                _wf_res_startup = _run_wf_startup(
                    base_dataframes=_wf_dfs_startup,
                    full_sample_results=data['results'],
                    scenarios=WF_SCENARIOS,
                    backtest_fn=backtest_from_dataframe,
                    initial_capital=config.initial_wallet,
                    sizing_mode=args.sizing_mode,
                )

                if _wf_res_startup.get('any_passed'):
                    _startup_wf_best = _wf_res_startup['best_wf_config']
                    logger.info(
                        "[STARTUP F-BUG2] WF-validated: %s EMA(%s,%s) %s — OOS Sharpe=%.2f",
                        _startup_wf_best['scenario'],
                        _startup_wf_best['ema_periods'][0],
                        _startup_wf_best['ema_periods'][1],
                        _startup_wf_best['timeframe'],
                        _startup_wf_best.get('avg_oos_sharpe', 0.0),
                    )
                else:
                    logger.warning("[STARTUP F-BUG2] WF: aucune config OOS validée — fallback conservatif.")
            except Exception as _wf_startup_err:
                logger.warning("[STARTUP F-BUG2] WF validation skipped: %s", _wf_startup_err)

            best_result = _select_best_by_calmar(_pool_loop)
            best_profit = best_result['final_wallet'] - best_result['initial_wallet']

            # Affichage des résultats via la fonction centralisée
            display_results_for_pair(backtest_pair, data['results'])

            # === Exécuter le trading réel avec les meilleurs paramètres ===
            if _startup_wf_best:
                best_params = {
                    'timeframe': _startup_wf_best['timeframe'],
                    'ema1_period': _startup_wf_best['ema_periods'][0],
                    'ema2_period': _startup_wf_best['ema_periods'][1],
                    'scenario': _startup_wf_best['scenario'],
                }
                best_params.update(SCENARIO_DEFAULT_PARAMS.get(_startup_wf_best['scenario'], {}))
            else:
                best_params = {
                    'timeframe': '1d',
                    'ema1_period': 26,
                    'ema2_period': 50,
                    'scenario': 'StochRSI',
                }
                best_params.update(SCENARIO_DEFAULT_PARAMS.get('StochRSI', {}))
                logger.warning(
                    "[STARTUP F-BUG2] Aucun WF valide — paramètres CONSERVATIFS par défaut "
                    "(EMA 26/50, StochRSI, 1d). Achats bloqués par P0-03/oos_blocked."
                )

            # Initialiser l'état du bot pour cette paire
            if backtest_pair not in bot_state:
                bot_state[backtest_pair] = _make_default_pair_state()

            pair_state: PairState = cast('PairState', bot_state[backtest_pair])

            try:
                execute_real_trades(data['real_pair'], best_params['timeframe'], best_params, backtest_pair, sizing_mode=args.sizing_mode)
            except Exception as e:
                logger.error(f"Erreur trading réel {backtest_pair}: {e}")
                subj, body = trading_pair_error_email(backtest_pair, str(e), traceback.format_exc())
                send_email_alert(subject=subj, body=body)

            # === AFFICHAGE DATE/HEURE ET PLANIFICATION ===
            current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            pair_state['last_run_time'] = current_run_time
            pair_state['last_best_params'] = best_params
            pair_state['execution_count'] = pair_state.get('execution_count', 0) + 1

            # CORRECTION: Nettoyer les planifications existantes pour éviter les doublons
            schedule.clear()

            # P1-04: NE PAS mettre à jour _live_best_params avec des params full-sample
            # quand oos_blocked — geler les anciens params.
            if not _oos_blocked_this_pair:
                with _bot_state_lock:
                    _runtime.live_best_params[backtest_pair] = dict(best_params)
                # Marquer le timestamp du backtest initial pour que le 1er horaire ne
                # relance pas immédiatement un backtest complet.
                with _bot_state_lock:
                    _runtime.last_backtest_time[backtest_pair] = time.time()
            else:
                logger.warning(
                    "[MAIN-LOOP P1-04] %s: _live_best_params GELÉS — params full-sample non propagés.",
                    backtest_pair,
                )

            # ── Tâche 1 : backtest + WF + trading → toutes les heures ──────────
            schedule.every(60).minutes.do(
                lambda bp=backtest_pair, rp=data['real_pair'], p0=dict(best_params), sm=args.sizing_mode:
                    execute_scheduled_trading(
                        rp,
                        _read_live_params(bp, p0).get('timeframe', p0.get('timeframe', '4h')),
                        _read_live_params(bp, p0),
                        bp,
                        sm,
                    )
            )

            # ── Tâche 2 : live trading uniquement → toutes les 2 minutes ────────
            schedule.every(2).minutes.do(
                lambda bp=backtest_pair, rp=data['real_pair'], sm=args.sizing_mode:
                    execute_live_trading_only(rp, bp, sm)
            )

            # Afficher le panel de suivi
            console.print(build_tracking_panel(pair_state, current_run_time))
            console.print("\n")

            save_bot_state(force=True)

        logger.info(f"Tâches planifiées actives: {len(schedule.jobs)}")

        # === BOUCLE PRINCIPALE ===
        # C-04: Handler SIGTERM/SIGINT pour graceful shutdown (PM2, taskkill, systemd, Ctrl+C)
        # P3-01: remplacement des closures fragiles par threading.Event
        _shutdown_event     = threading.Event()   # set → le main-loop s'arrête
        _voluntary_event    = threading.Event()   # set → CTRL+C → pas d'email
        _shutdown_verified  = threading.Event()   # set → vérification déjà faite

        def _graceful_shutdown(signum: int, _frame: Any) -> None:
            import signal as _signal  # pylint: disable=reimported
            if signum == _signal.SIGINT:
                _voluntary_event.set()
            _shutdown_event.set()  # main-loop va sortir naturellement
            logger.critical(f"[SHUTDOWN] Signal {signum} reçu — arrêt demandé")

        try:
            signal.signal(signal.SIGTERM, _graceful_shutdown)
            signal.signal(signal.SIGINT, _graceful_shutdown)  # P1-SIGINT
            logger.info("[SHUTDOWN] Handlers SIGTERM + SIGINT enregistrés (C-04 / P1-SIGINT)")
        except (OSError, ValueError) as _sig_err:
            logger.warning(f"[SHUTDOWN] Impossible d'enregistrer signal handlers: {_sig_err}")

        # P0-SHUT: atexit comme filet de sécurité.
        # Ignoré si un handler signal a déjà effectué la vérification (_shutdown_verified).
        import atexit
        def _atexit_verify() -> None:
            if _shutdown_verified.is_set():
                return  # déjà fait
            _shutdown_verified.set()
            _verify_all_stops_on_shutdown(
                reason="atexit",
                send_email=not _voluntary_event.is_set(),
            )
        atexit.register(_atexit_verify)

        display_bot_active_banner(len(schedule.jobs), schedule.next_run(), console)

        logger.info("Bot actif - Surveillance des signaux de trading...")
        logger.info("Initialisation du gestionnaire d'erreurs...")
        error_handler = initialize_error_handler({
            'smtp_server': config.smtp_server,
            'smtp_port': str(config.smtp_port),
            'sender_email': config.sender_email,
            'sender_password': config.smtp_password,
            'recipient_email': config.receiver_email
        })
        logger.info(f"Gestionnaire d'erreurs actif - Mode: {error_handler.circuit_breaker.mode.value}")

        # Initialisation du compteur de vérification réseau
        network_check_counter = 0
        try:
            running_counter = 0
            while not _shutdown_event.is_set():
                try:
                    # Check circuit breaker status
                    if not error_handler.circuit_breaker.is_available():
                        logger.critical(f"[CIRCUIT] Bot en mode pause - Circuit ouvert. Prochaine tentative: {error_handler.circuit_breaker.timeout_seconds}s")
                        _shutdown_event.wait(10)
                        continue

                    # Execute scheduled tasks with error handling
                    try:
                        schedule.run_pending()
                    except Exception as e:
                        should_continue, _ = error_handler.handle_error(
                            error=e,
                            context="schedule.run_pending()",
                            critical=False
                        )
                        if not should_continue:
                            logger.warning("[CIRCUIT] Skipping task execution due to circuit breaker")
                            _shutdown_event.wait(10)
                            continue

                    # Vérification réseau toutes les 5 minutes
                    network_check_counter += 1
                    if network_check_counter >= 5:  # 5 cycles = 5 minutes
                        if not check_network_connectivity():
                            logger.warning("Connectivité réseau perdue...")
                            _shutdown_event.wait(30)
                            continue
                        network_check_counter = 0

                    # Affichage du temps restant avant la prochaine exécution
                    now = datetime.now()
                    next_run = schedule.next_run()
                    if next_run:
                        delta = next_run - now
                        minutes_left = max(0, int(delta.total_seconds() // 60))
                        console.print(f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution dans {minutes_left} min ({next_run.strftime('%H:%M:%S')})")
                    else:
                        console.print(f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution non planifiée")

                    running_counter += 1
                    if running_counter % 1 == 0:  # Toutes les 10 minutes (600s sleep)
                        console.print(f"[RUNNING] {now.strftime('%H:%M:%S')} - running en cours")

                    # Écriture du heartbeat (Phase 2 — watchdog support)
                    try:
                        heartbeat = {
                            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "pid": os.getpid(),
                            "circuit_mode": error_handler.circuit_breaker.mode.value,
                            "error_count": len(error_handler.error_history),
                            "loop_counter": running_counter,
                        }
                        hb_path = os.path.join(os.path.dirname(__file__), "states", "heartbeat.json")
                        os.makedirs(os.path.dirname(hb_path), exist_ok=True)
                        tmp_path = hb_path + ".tmp"
                        with open(tmp_path, "w", encoding="utf-8") as f:
                            json.dump(heartbeat, f)
                        os.replace(tmp_path, hb_path)
                    except Exception as hb_err:
                        logger.error(f"[HEARTBEAT] Erreur écriture: {hb_err}")

                    _shutdown_event.wait(120)

                except Exception as e:
                    # Use error handler to manage main loop exceptions
                    should_continue, _ = error_handler.handle_error(
                        error=e,
                        context="main_loop",
                        critical=True
                    )

                    if not should_continue:
                        logger.critical(f"[CIRCUIT] Main loop paused due to circuit breaker. Waiting {error_handler.circuit_breaker.timeout_seconds}s before retry")
                        _shutdown_event.wait(error_handler.circuit_breaker.timeout_seconds)
                    else:
                        logger.warning("[MAIN_LOOP] Continuing despite error - circuit still available")
                        _shutdown_event.wait(30)
            # P3-01: boucle terminée → nettoyage unique
            logger.info("[SHUTDOWN] Boucle principale terminée — nettoyage")
            save_bot_state(force=True)
            if not _shutdown_verified.is_set():
                _shutdown_verified.set()
                _verify_all_stops_on_shutdown(
                    reason="main_loop_exit",
                    send_email=not _voluntary_event.is_set(),
                )
        except KeyboardInterrupt:
            logger.info("Execution interrompue par l'utilisateur. Arret du script.")
            _voluntary_event.set()
            save_bot_state(force=True)
            if not _shutdown_verified.is_set():
                _shutdown_verified.set()
                _verify_all_stops_on_shutdown(reason="KeyboardInterrupt (inner)", send_email=False)
        except Exception as e:
            error_msg = f"Erreur inattendue au démarrage : {e}"
            logger.error(error_msg)
            try:
                subj, body = critical_startup_error_email(str(e), traceback.format_exc())
                send_email_alert(subject=subj, body=body)
            except Exception as _e:
                logger.warning("[STARTUP] Email erreur démarrage impossible: %s", _e)
            save_bot_state(force=True)
            if not _shutdown_verified.is_set():
                _shutdown_verified.set()
                _verify_all_stops_on_shutdown(reason=f"startup error: {e}")

    except KeyboardInterrupt:
        logger.info("Execution interrompue par l'utilisateur. Arret du script.")
        _voluntary_event.set()
        save_bot_state(force=True)
        if not _shutdown_verified.is_set():
            _shutdown_verified.set()
            try:
                _verify_all_stops_on_shutdown(reason="KeyboardInterrupt (outer)", send_email=False)
            except Exception as _e:
                logger.warning("[SHUTDOWN] Vérification stops abandonnée: %s", _e)
    except Exception as e:
        error_msg = f"Erreur inattendue au démarrage : {e}"
        logger.error(error_msg)
        try:
            subj, body = critical_startup_error_email(str(e), traceback.format_exc())
            send_email_alert(subject=subj, body=body)
        except Exception as _e:
            logger.warning("[SHUTDOWN] Email alerte impossible: %s", _e)
        save_bot_state(force=True)
        if not _shutdown_verified.is_set():
            _shutdown_verified.set()
            try:
                _verify_all_stops_on_shutdown(reason=f"fatal error: {e}")
            except Exception as _e:
                logger.warning("[SHUTDOWN] Vérification stops (fatal): %s", _e)
