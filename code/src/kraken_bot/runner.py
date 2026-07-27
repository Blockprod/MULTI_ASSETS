# ruff: noqa: E402
# ─── Standard-library & third-party imports ─────────────────────────────────
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from rich.console import Console
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, TypedDict, cast

import pandas as pd

os.environ["BROKER"] = "KRAKEN"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")


def _configure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

_KRAKEN_RUNTIME_DIR = Path(__file__).resolve().parent
_KRAKEN_SRC_DIR = _KRAKEN_RUNTIME_DIR.parent
_KRAKEN_BIN_DIR = (_KRAKEN_RUNTIME_DIR.parent.parent / 'bin').resolve()
_KRAKEN_DEFAULT_DATA_DIR = _KRAKEN_RUNTIME_DIR.parents[2] / "data"
if _KRAKEN_DEFAULT_DATA_DIR.exists():
    os.environ.setdefault("KRAKEN_HISTORICAL_DATA_DIR", str(_KRAKEN_DEFAULT_DATA_DIR))
_KRAKEN_LOCAL_MODULES = {
    "backtest_orchestrator",
    "backtest_runner",
    "broker_models",
    "bot_config",
    "cache_manager",
    "constants",
    "correlation_guard",
    "cython_integrity",
    "data_fetcher",
    "display_ui",
    "email_templates",
    "email_utils",
    "error_handler",
    "exceptions",
    "exchange_client",
    "indicators_engine",
    "kraken_client",
    "market_analysis",
    "metrics",
    "order_manager",
    "position_reconciler",
    "position_sizing",
    "signal_generator",
    "state_manager",
    "strategy_policy",
    "timestamp_utils",
    "trade_helpers",
    "trade_journal",
    "wal_logger",
    "walk_forward",
    "watchdog",
}

_HEARTBEAT_WRITE_RETRIES = 5
_heartbeat_write_failures = 0


def _write_runtime_heartbeat(hb_path: str, heartbeat: Mapping[str, Any]) -> None:
    """Write heartbeat JSON robustly on Windows.

    Windows can temporarily deny os.replace when another process reads the
    heartbeat or when two bot instances race. Use a per-process temp file and
    retry before surfacing the failure.
    """
    hb_dir = os.path.dirname(hb_path)
    os.makedirs(hb_dir, exist_ok=True)
    last_error: Optional[BaseException] = None
    for attempt in range(_HEARTBEAT_WRITE_RETRIES):
        tmp_path = f"{hb_path}.{os.getpid()}.{threading.get_ident()}.{attempt}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(heartbeat, f)
            os.replace(tmp_path, hb_path)
            return
        except OSError as exc:
            last_error = exc
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            winerror = getattr(exc, "winerror", None)
            if winerror not in {5, 32, 33} and not isinstance(exc, PermissionError):
                raise
            time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise OSError(f"Impossible d'ecrire le heartbeat: {hb_path}")


def _same_path(left: str, right: Path) -> bool:
    try:
        return Path(left or os.curdir).resolve() == right
    except OSError:
        return False


def _ensure_kraken_runtime_imports() -> None:
    runtime_path = str(_KRAKEN_RUNTIME_DIR)
    bin_path = str(_KRAKEN_BIN_DIR)
    sys.path[:] = [
        p for p in sys.path
        if (
            not _same_path(p, _KRAKEN_RUNTIME_DIR)
            and not _same_path(p, _KRAKEN_SRC_DIR)
            and not _same_path(p, _KRAKEN_BIN_DIR)
        )
    ]
    sys.path.insert(0, runtime_path)
    sys.path.insert(1, bin_path)
    for module_name in _KRAKEN_LOCAL_MODULES:
        module = sys.modules.get(module_name)
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        try:
            module_path = Path(str(module_file)).resolve()
        except OSError:
            sys.modules.pop(module_name, None)
            continue
        if _KRAKEN_RUNTIME_DIR not in module_path.parents and module_path.parent != _KRAKEN_RUNTIME_DIR:
            sys.modules.pop(module_name, None)


def _kraken_module_path(module_name: str) -> Path | None:
    module = sys.modules.get(module_name)
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return None
    try:
        return Path(str(module_file)).resolve()
    except OSError:
        return None


def _assert_kraken_module_origins() -> None:
    wrong_modules: list[str] = []
    loaded_modules: list[str] = []
    for module_name in sorted(_KRAKEN_LOCAL_MODULES):
        module_path = _kraken_module_path(module_name)
        if module_path is None:
            continue
        loaded_modules.append(f"{module_name}={module_path}")
        if _KRAKEN_RUNTIME_DIR not in module_path.parents and module_path.parent != _KRAKEN_RUNTIME_DIR:
            wrong_modules.append(f"{module_name}={module_path}")
    if wrong_modules:
        raise SystemExit(
            "[KRAKEN-IMPORTS] Runtime mixte refuse; modules hors code/src/kraken_bot: "
            + "; ".join(wrong_modules)
        )
    logger.info("[KRAKEN-IMPORTS] Modules locaux Kraken verifies: %s", " | ".join(loaded_modules))


def _path_is_kraken_runtime(path: Path) -> bool:
    return _KRAKEN_RUNTIME_DIR in path.parents or path.parent == _KRAKEN_RUNTIME_DIR


def _object_source_path(obj: Any) -> Path | None:
    code = getattr(obj, "__code__", None)
    filename = getattr(code, "co_filename", None)
    if not filename:
        module_name = getattr(obj, "__module__", "")
        module = sys.modules.get(str(module_name))
        filename = getattr(module, "__file__", None)
    if not filename:
        return None
    try:
        return Path(str(filename)).resolve()
    except OSError:
        return None


def _assert_kraken_object_origins(objects: Mapping[str, Any]) -> None:
    wrong_objects: list[str] = []
    loaded_objects: list[str] = []
    for object_name, obj in objects.items():
        object_path = _object_source_path(obj)
        if object_path is None:
            continue
        loaded_objects.append(f"{object_name}={object_path}")
        if not _path_is_kraken_runtime(object_path):
            wrong_objects.append(f"{object_name}={object_path}")
    if wrong_objects:
        raise SystemExit(
            "[KRAKEN-IMPORTS] Runtime mixte refuse; objets lies hors code/src/kraken_bot: "
            + "; ".join(wrong_objects)
        )
    logger.info("[KRAKEN-IMPORTS] Objets runtime Kraken verifies: %s", " | ".join(loaded_objects))


_ensure_kraken_runtime_imports()

# Suppress DeprecationWarning and pandas 2.2 CoW ChainedAssignmentError
# (root-cause fix is in indicators.pyx; this filter is a safety net).
warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    _chained_err = getattr(pd.errors, 'ChainedAssignmentError', None)
    if _chained_err is not None:
        warnings.filterwarnings("ignore", category=_chained_err)
except AttributeError:
    pass  # pandas < 2.2 n'a pas ChainedAssignmentError

# Le dossier bin/ reste disponible pour les modules Cython; les modules Kraken
# locaux gardent la priorite sur les modules Binance racine.


class Client:
    KLINE_INTERVAL_15MINUTE = '15m'
    KLINE_INTERVAL_1HOUR = '1h'
    KLINE_INTERVAL_4HOUR = '4h'
    KLINE_INTERVAL_1DAY = '1d'

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
from email_utils import (
    send_email_alert,
    send_email_alert_with_fallback,
    send_trading_alert_email,
    write_alert_unsent_fallback,
)
from state_manager import save_state, load_state, set_emergency_halt
from display_ui import (
    display_account_balances_panel, display_market_changes,
    display_results_for_pair, display_backtest_table,
    build_tracking_panel, display_execution_header,
    display_bot_active_banner,
)
from cache_manager import cleanup_expired_cache
from exchange_client import (
    ExchangePort, is_valid_stop_loss_order,
    can_execute_partial_safely,  # noqa: F401 — re-export: tests patchent ms.can_execute_partial_safely
    place_stop_loss_order as _place_stop_loss_order,
    place_exchange_stop_loss as _place_exchange_stop_loss,  # P0-01
    safe_market_buy as _safe_market_buy,
    safe_market_sell as _safe_market_sell,
    get_symbol_filters as _get_symbol_filters_impl,
    _get_coin_balance,
    set_circuit_alert_callback,  # TS-P2-01
)
from broker_models import BrokerConfig
from kraken_client import KrakenSpotClient
from email_templates import (
    api_connection_failure_email, data_retrieval_error_email,
    network_error_email, indicator_error_email,
    critical_startup_error_email,
    generic_exception_email,
    sell_executed_email,
)

# is_valid_stop_loss_order, can_execute_partial_safely importés depuis exchange_client.py (Phase 4)

# ErrorHandler réel importé depuis error_handler.py (remplace le DummyErrorHandler)
from error_handler import initialize_error_handler
from error_handler import AlertThrottle  # P1-05

# Modules dormants activés (Phase 2)
from trade_journal import log_trade  # noqa: F401 — re-export: tests patchent ms.log_trade
from wal_logger import wal_replay, wal_clear
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
    TIMEFRAME_SECONDS,
)
from strategy_policy import (
    StrategySnapshot,
    atr_median_window,
    compute_mtf_bullish,
    periods_per_year_for_timeframe,
)
from cython_integrity import (                 # P1-01
    verify_cython_integrity as _verify_cython_integrity,
)
from metrics import write_metrics as _write_metrics  # P2-04: observabilité métriques
from correlation_guard import check_correlation_guard, feed_candle  # P2-3: anti-corrélation systémique

_ensure_kraken_runtime_imports()

try:
    # Forcer la console Windows en UTF-8 (code page 65001)
    if os.name == "nt":
        os.system("chcp 65001 >NUL")
        locale.setlocale(locale.LC_ALL, '')
    _configure_utf8_stdio()
except Exception as _exc:
    logging.getLogger(__name__).debug("[MULTI_SYMBOLS] initialisation locale/console échouée: %s", _exc)

# Configuration du logging
_broker_name_for_files = str(getattr(config, 'broker', 'KRAKEN')).upper()
_log_filename = 'kraken_trading_bot.log' if _broker_name_for_files == 'KRAKEN' else 'trading_bot.log'
_log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'logs', _log_filename)
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


class _KrakenEmailErrorLogHandler(logging.Handler):
    """Send throttled email alerts for unexpected ERROR/CRITICAL log records."""

    _sending = threading.local()

    _SKIP_PATTERNS = (
        "Aucun resultat ne passe les OOS gates",
        "Aucun résultat ne passe les OOS gates",
        "ACHATS BLOQUES",
        "ACHATS BLOQU",
        "[BUY BLOCKED CAPITAL]",
        "Erreur envoi email",
        "[ALERT] Failed to send email",
        "[EMAIL-FALLBACK]",
    )

    def __init__(self, cooldown_seconds: float) -> None:
        super().__init__(level=logging.ERROR)
        self.cooldown_seconds = max(float(cooldown_seconds), 0.0)
        self._last_sent: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _should_skip(self, message: str) -> bool:
        return any(pattern in message for pattern in self._SKIP_PATTERNS)

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._sending, "active", False):
            return
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        if self._should_skip(message):
            return

        key = f"{record.name}|{record.levelname}|{message[:240]}"
        now = time.time()
        with self._lock:
            if now - self._last_sent.get(key, 0.0) < self.cooldown_seconds:
                return
            self._last_sent[key] = now

        subject = f"[KRAKEN {record.levelname}] {record.name}"
        body = (
            "Une entree ERROR/CRITICAL a ete detectee dans le runtime Kraken.\n\n"
            f"Logger: {record.name}\n"
            f"Niveau: {record.levelname}\n"
            f"Module: {record.pathname}:{record.lineno}\n"
            f"Heure locale: {datetime.now().isoformat(timespec='seconds')}\n\n"
            f"Message:\n{message}"
        )
        try:
            self._sending.active = True
            send_email_alert_with_fallback(subject, body)
        except Exception:
            self.handleError(record)
        finally:
            self._sending.active = False


def _install_kraken_email_error_log_handler() -> None:
    root_logger = logging.getLogger()
    if any(isinstance(handler, _KrakenEmailErrorLogHandler) for handler in root_logger.handlers):
        return
    cooldown = float(os.getenv(
        "EMAIL_LOG_ERROR_COOLDOWN_SECONDS",
        str(getattr(config, "email_cooldown_seconds", 300)),
    ))
    root_logger.addHandler(_KrakenEmailErrorLogHandler(cooldown))
    logger.info("[EMAIL] Alerte automatique ERROR/CRITICAL activee (cooldown %.0fs)", cooldown)


_assert_kraken_module_origins()
_assert_kraken_object_origins(
    {
        "display_account_balances_panel": display_account_balances_panel,
        "load_state": load_state,
        "save_state": save_state,
        "_place_exchange_stop_loss": _place_exchange_stop_loss,
        "_reconcile_impl": _reconcile_impl,
        "_execute_buy": _execute_buy,
        "_execute_scheduled_trading": _execute_scheduled_trading,
        "_write_metrics": _write_metrics,
        "_verify_cython_integrity": _verify_cython_integrity,
    }
)

RECONCILE_REQUIRED_MARKER = os.path.join(config.states_dir, "reconcile_required.json")


def _reconcile_required_marker_exists() -> bool:
    return os.path.exists(RECONCILE_REQUIRED_MARKER)


def _clear_reconcile_required_marker() -> None:
    try:
        if os.path.exists(RECONCILE_REQUIRED_MARKER):
            os.remove(RECONCILE_REQUIRED_MARKER)
            logger.info("[RECONCILE] Marker reconcile_required supprime apres validation exchange.")
    except Exception as exc:
        logger.warning("[RECONCILE] Impossible de supprimer le marker reconcile_required: %s", exc)


def _write_reconcile_required_marker(reason: str, source: str = "runner") -> None:
    """Persist a marker forcing manual exchange reconciliation before new buys."""
    try:
        os.makedirs(config.states_dir, exist_ok=True)
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "source": source,
        }
        with open(RECONCILE_REQUIRED_MARKER, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        logger.critical("[RECONCILE] Marker reconcile_required ecrit: %s", RECONCILE_REQUIRED_MARKER)
    except Exception as exc:
        logger.critical("[RECONCILE] Impossible d'ecrire reconcile_required: %s", exc)

# Log Cython status at startup (after logging is configured)
if CYTHON_BACKTEST_AVAILABLE:
    logger.info("[CYTHON] Moteur backtest Cython chargé avec succès (backtest_engine_standard)")
else:
    logger.warning("[CYTHON] Moteur backtest Cython NON disponible — achats bloqués si BACKTEST_ENGINE_MODE=cython_required")
logger.info("[LIVE_ENGINE] Python only — Cython réservé aux backtests/WF.")
if CYTHON_INDICATORS_AVAILABLE:
    logger.info("[CYTHON] Moteur indicateurs Cython chargé avec succès")
else:
    logger.warning("[CYTHON] Moteur indicateurs Cython NON disponible — fallback Python actif")

# P1-01: Vérifie l'intégrité SHA256 des .pyd au démarrage
_cython_integrity_ok = _verify_cython_integrity(alert_fn=cast(Callable[[str, str], None], send_email_alert))
if config.bot_mode == 'LIVE' and not _cython_integrity_ok:
    raise SystemExit("[CYTHON] Integrity check failed in LIVE mode; startup stopped.")

# Paramètre pour activer/désactiver les logs détaillés (VERBOSE = False pour plus de rapidité)
# (VERBOSE_LOGS importé depuis bot_config)

# Config et config importés depuis bot_config.py (Phase 4)
# La classe Config et config = Config.from_env() sont dans bot_config.py

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
# Do not prepend code/src here: Kraken local modules must remain first on sys.path.

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

# --- Kraken Client ---

EXCHANGE_NAME = 'KRAKEN'


def exchange_label() -> str:
    return 'Kraken' if EXCHANGE_NAME == 'KRAKEN' else 'Binance'

_KRAKEN_API_URL = str(getattr(config, 'kraken_api_url', os.getenv('KRAKEN_API_URL', 'https://api.kraken.com')))
_KRAKEN_WS_URL = str(getattr(config, 'kraken_ws_url', os.getenv('KRAKEN_WS_URL', 'wss://ws-auth.kraken.com/v2')))
_KRAKEN_ENABLE_NATIVE_TRAILING = bool(getattr(config, 'enable_native_trailing', False))
_KRAKEN_STRICT_PAIR_VALIDATION = bool(getattr(config, 'strict_pair_validation', True))

# Initialisation du client Kraken uniquement.
client = KrakenSpotClient(
    BrokerConfig(
        broker='KRAKEN',
        api_key=config.api_key,
        api_secret=config.secret_key,
        api_url=_KRAKEN_API_URL,
        ws_url=_KRAKEN_WS_URL,
        enable_native_trailing=_KRAKEN_ENABLE_NATIVE_TRAILING,
        strict_pair_validation=_KRAKEN_STRICT_PAIR_VALIDATION,
    ),
    requests_params={
        'timeout': config.api_timeout,
        'public_page_delay': float(os.getenv('KRAKEN_PUBLIC_PAGE_DELAY', '1.0')),
    },
)

# Configurer le callback d'erreur pour le decorator log_exceptions (Phase 4)
def _error_notification_handler(fn: str, e: Exception, a: Tuple[Any, ...], kw: Dict[str, Any]) -> None:
    subj, body = generic_exception_email(fn, e, a, kw)
    send_trading_alert_email(subject=subj, body_main=body, client=client)

set_error_notification_callback(_error_notification_handler)

# TS-P2-01: enregistrer le callback d'alerte email pour le circuit breaker
set_circuit_alert_callback(
    lambda msg: send_trading_alert_email(
        subject=f"[CIRCUIT-BREAKER TS-P2-01] API {EXCHANGE_NAME} en quarantaine — achats bloqués",
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
    start = datetime.today() - timedelta(days=config.backtest_days)
    if EXCHANGE_NAME == 'KRAKEN':
        return start.strftime("%Y-%m-%d")
    return start.strftime("%d %B %Y")

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
    atr_stop_multiplier_at_entry: Optional[float]
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
    _sl_cooldown_timeframe: str            # TF de la stratégie au moment du SL (survit au restart)
    # --- OOS gates (P0-03) ---
    oos_blocked: bool
    oos_blocked_since: float               # time.time()
    # --- Drawdown kill-switch (ST-P2-02) ---
    drawdown_halted: Optional[bool]        # True si drawdown > max_drawdown_pct enété détecté
    # --- StochRSI seuils optimisés par paire (STOCH-OPT) ---
    stoch_buy_min: float
    stoch_buy_max: float
    stoch_sell_exit: float
    # --- WF validation status (I-3) ---
    wf_fallback: bool                          # True si aucune config OOS validée au démarrage
    wf_status: str
    wf_block_reason: Optional[str]
    wf_last_attempt_at: Optional[str]
    entries_ready: bool
    # --- Display / info (écriture externe) ---
    quote_currency: str
    ticker_spot_price: float
    latest_best_params: Optional[Dict[str, Any]]
    next_live_run_at: Optional[str]
    next_hourly_run_at: Optional[str]
    display_next_live: Optional[str]
    display_next_wf: Optional[str]
    live_cycle_running: bool
    scheduler_ready: bool
    system_status: Optional[str]
    system_status_detail: Optional[str]
    active_strategy: Optional[Dict[str, Any]]
    entry_strategy: Optional[Dict[str, Any]]
    broker: str
    broker_symbol: Optional[str]
    broker_order_id: Optional[str]
    broker_client_order_id: Optional[str]
    broker_pair_status: Optional[str]
    broker_pair_candidates: Optional[List[Dict[str, str]]]
    history_status: Optional[Dict[str, Dict[str, Any]]]
    effective_backtest_fee_taker: Optional[float]
    effective_backtest_fee_maker: Optional[float]
    effective_live_fee_taker: Optional[float]
    effective_live_fee_maker: Optional[float]
    kraken_fee_source: Optional[str]
    kraken_fee_api_taker: Optional[float]
    kraken_fee_api_maker: Optional[float]
    kraken_fee_error: Optional[str]


class ScheduleStatus(TypedDict):
    next_live_run_at: Optional[datetime]
    next_hourly_run_at: Optional[datetime]
    live_cycle_running: bool
    scheduler_ready: bool
    display_next_live: str
    display_next_wf: str
    system_status: str
    system_status_detail: str


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
_BOT_STATE_GLOBAL_KEYS = {
    'emergency_halt',
    'emergency_halt_reason',
    '_daily_pnl_tracker',
    '_state_version',
    'reconcile_failed',
    'reconcile_failed_reason',
    'kraken_preflight',
    'kraken_private_api_ok',
    'stoch_params',
}


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
        'wf_fallback': True,
        'wf_status': 'pending_revalidation',
        'wf_block_reason': 'revalidation WF requise',
        'wf_last_attempt_at': None,
        'entries_ready': False,
        'next_live_run_at': None,
        'next_hourly_run_at': None,
        'display_next_live': 'Initialisation scheduler',
        'display_next_wf': 'Initialisation scheduler',
        'live_cycle_running': False,
        'scheduler_ready': False,
        'system_status': None,
        'system_status_detail': None,
        'broker': EXCHANGE_NAME.lower(),
        'broker_symbol': None,
        'broker_order_id': None,
        'broker_client_order_id': None,
        'broker_pair_status': None,
        'broker_pair_candidates': None,
        'history_status': None,
        'effective_backtest_fee_taker': None,
        'effective_backtest_fee_maker': None,
        'effective_live_fee_taker': None,
        'effective_live_fee_maker': None,
        'kraken_fee_source': None,
        'kraken_fee_api_taker': None,
        'kraken_fee_api_maker': None,
        'kraken_fee_error': None,
    })


# ─── Thread-safety du bot_state (C-01) ────────────────────────────────────────
# RLock global pour serialize save/load du bot_state
_bot_state_lock = threading.RLock()
# Locks par paire : empêchent deux exécutions simultanées sur la même paire
_pair_execution_locks: Dict[str, threading.Lock] = {}
_pair_locks_mutex = threading.Lock()
# Lock global d'allocation quote : serialise fetch-balance -> compute-qty -> place-order
# pour empecher deux paires d'acheter avec le meme cash en parallele.
_usdc_allocation_lock = threading.Lock()

# Cache pour les indicateurs calculés (lecture/écriture multi-thread protégée)
_indicators_cache_lock = threading.Lock()
indicators_cache: OrderedDict[str, Any] = OrderedDict()

# Dernier solde cash connu — mis a jour a chaque fetch_balances, lu par le heartbeat
_last_usdc_balance: float | None = None

_USD_EQUIVALENT_QUOTES = ("USDC", "USD")


def _usd_equivalent_cash_balance(account_info: Dict[str, Any]) -> float:
    total = 0.0
    for quote in _USD_EQUIVALENT_QUOTES:
        _, free, _, _ = _get_coin_balance(account_info, quote)
        total += free
    return total


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
    # AVERTISSEMENT : le runtime Kraken conserve le trailing jumeau Binance par
    # annulation/recreation du stop-loss exchange, pas par trailing natif.
    raise NotImplementedError(
        "Trailing natif non active pour Kraken Spot dans ce runtime. "
        "Utilisez le trailing manuel par deplacement du stop-loss exchange."
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
    if EXCHANGE_NAME == 'KRAKEN':
        def _kraken_api_connection_failure(error: str) -> Tuple[str, str]:
            return (
                "[CRIT] ERREUR Connexion API Kraken",
                (
                    "=== ECHEC DE CONNEXION API KRAKEN ===\n\n"
                    "Le bot n'a pas pu etablir une connexion avec l'API publique Kraken.\n\n"
                    f"Erreur rencontree: {str(error)[:150]}\n\n"
                    "Les achats restent bloques tant que le preflight n'est pas OK."
                ),
            )
        return _validate_api_connection(client, send_trading_alert_email, _kraken_api_connection_failure)
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
        self.entries_ready: Dict[str, bool] = {}
        self.last_scheduled_trade_time: Dict[str, float] = {}
        self.next_live_run_at: Optional[datetime] = None
        self.next_hourly_run_at: Optional[datetime] = None
        self.live_cycle_running: bool = False
        self.scheduler_ready: bool = False
        self.bootstrap_display_active: bool = False
        self.bootstrap_current_pair: Optional[str] = None
        self.bootstrap_current_phase: str = 'idle'
        # Alert throttles (1h default, per pair or global)
        self.oos_alert_throttle = AlertThrottle(cooldown=3600)
        self.daily_loss_throttle = AlertThrottle(
            cooldown=getattr(config, 'backtest_throttle_seconds', 3600.0)
        )
        self.sl_missing_throttle = AlertThrottle(cooldown=3600)
        self.drawdown_throttle = AlertThrottle(cooldown=3600)
        self.buy_block_throttle = AlertThrottle(cooldown=600)
        # SL polling counters per pair (SL-POLL — détection fill en temps réel)
        self.sl_poll_counters: Dict[str, int] = {}
        # OOS fail streak counters per pair (OOS-STREAK — N=6 cycles before hard block)
        self.oos_fail_streaks: Dict[str, int] = {}
        # Live trading fees (init from config, updated once after API fetch — P0-01)
        self.taker_fee: float = config.taker_fee
        self.maker_fee: float = config.maker_fee
        self.kraken_fees_confirmed: bool = False


_runtime = _BotRuntime()


def _wf_fold_label() -> str:
    folds = int(getattr(config, 'oos_min_folds', 3) or 3)
    return f"{folds}/{folds}"


# --- Save throttle: constantes (jamais mutées) ---
_SAVE_THROTTLE_SECONDS: float = SAVE_THROTTLE_SECONDS
_MAX_SAVE_FAILURES: int = MAX_SAVE_FAILURES


def _local_naive(dt_value: datetime) -> datetime:
    if dt_value.tzinfo is not None:
        return dt_value.astimezone().replace(tzinfo=None)
    return dt_value


def _schedule_now(current_run_time: Optional[str] = None) -> datetime:
    if current_run_time:
        try:
            return datetime.strptime(current_run_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return datetime.now()


def _future_or_none(dt_value: Optional[datetime], now: datetime) -> Optional[datetime]:
    if dt_value is None:
        return None
    candidate = _local_naive(dt_value)
    return candidate if candidate > now else None


def _format_schedule_dt(dt_value: Optional[datetime]) -> Optional[str]:
    return dt_value.strftime("%Y-%m-%d %H:%M:%S") if dt_value else None


def _live_panel_output_enabled() -> bool:
    return not bool(_runtime.bootstrap_display_active)


def _pair_state_items_for_runtime_status() -> List[Tuple[str, Dict[str, Any]]]:
    pair_items: List[Tuple[str, Dict[str, Any]]] = []
    pair_state_markers = {
        'wf_status',
        'entries_ready',
        'active_strategy',
        'entry_strategy',
        'last_order_side',
        'broker_pair_status',
    }
    for key, value in bot_state.items():
        if not isinstance(key, str) or key.startswith('_') or key in _BOT_STATE_GLOBAL_KEYS:
            continue
        if not isinstance(value, dict):
            continue
        if not any(marker in value for marker in pair_state_markers):
            continue
        pair_items.append((key, cast(Dict[str, Any], value)))
    return pair_items


def _runtime_system_status() -> Tuple[str, str]:
    if bot_state.get('emergency_halt'):
        return (
            "EMERGENCY_HALT / NO-BUY",
            str(bot_state.get('emergency_halt_reason') or 'reconciliation manuelle requise'),
        )

    preflight = bot_state.get('kraken_preflight') if isinstance(bot_state, dict) else None
    if bot_state.get('kraken_private_api_ok') is False:
        detail = "Balance/OpenOrders/ClosedOrders KO"
        if isinstance(preflight, Mapping):
            detail = str(
                preflight.get('permission_error')
                or preflight.get('nonce_error')
                or bot_state.get('reconcile_failed_reason')
                or detail
            )
        return "KRAKEN PRIVATE API KO", detail
    if bot_state.get('reconcile_failed'):
        return (
            "KRAKEN DEGRADE",
            str(bot_state.get('reconcile_failed_reason') or 'reconciliation exchange echouee'),
        )

    public_circuit_remaining = _kraken_public_circuit_open_seconds()
    if public_circuit_remaining > 0:
        return (
            "KRAKEN PUBLIC DATA DEGRADED",
            f"circuit OHLC public ouvert encore {public_circuit_remaining:.0f}s",
        )

    pair_items = _pair_state_items_for_runtime_status()
    if not pair_items:
        return "OK", ""

    tradable_pairs: List[str] = []
    protection_only_pairs: List[str] = []
    for pair, state in pair_items:
        params = dict(_runtime.live_best_params.get(pair, {}))
        if is_pair_tradable(
            state,
            bool(_runtime.entries_ready.get(pair, bool(state.get('entries_ready', False)))),
            params,
        ):
            tradable_pairs.append(pair)
        else:
            protection_only_pairs.append(pair)

    total_pairs = len(pair_items)
    tradable_count = len(tradable_pairs)
    if tradable_count == 0:
        return (
            "PROTECTION_ONLY / NO-BUY",
            f"aucune paire Kraken avec WF courant valide {_wf_fold_label()}",
        )
    if tradable_count < total_pairs:
        detail = (
            f"tradable={','.join(tradable_pairs)} | "
            f"protection_only={','.join(protection_only_pairs)}"
        )
        return f"PARTIAL_TRADABLE {tradable_count}/{total_pairs}", detail
    return f"TRADABLE {tradable_count}/{total_pairs}", f"toutes les paires Kraken WF {_wf_fold_label()} valides"


def _is_reconcile_resolvable_halt_reason(reason: str) -> bool:
    reason_lower = reason.lower()
    return any(
        token in reason_lower
        for token in (
            "buy sans sl",
            "ordre buy",
            "rollback",
            "stop-loss",
            "repair sl",
        )
    )


def _has_unprotected_buy_state_unlocked() -> bool:
    return any(
        state.get('last_order_side') == 'BUY' and state.get('sl_exchange_placed') is not True
        for _, state in _pair_state_items_for_runtime_status()
    )


def _clear_resolved_emergency_halt_after_reconcile() -> bool:
    """Clear stale position-protection halts only after exchange reconciliation is safe."""
    with _bot_state_lock:
        if not bot_state.get('emergency_halt'):
            return True
        reason = str(bot_state.get('emergency_halt_reason') or '')
        if not _is_reconcile_resolvable_halt_reason(reason):
            logger.critical("[EMERGENCY HALT] Halt conserve apres reconcile: raison non auto-resoluble: %s", reason)
            return False
        if _has_unprotected_buy_state_unlocked():
            logger.critical("[EMERGENCY HALT] Halt conserve apres reconcile: position BUY sans SL encore presente.")
            return False
        bot_state.pop('emergency_halt', None)
        bot_state.pop('emergency_halt_reason', None)
    logger.warning("[EMERGENCY HALT CLEARED] Leve apres reconciliation exchange reussie: aucune position BUY sans SL.")
    return True


def _kraken_public_circuit_open_seconds() -> float:
    try:
        open_until = float(getattr(client, '_public_circuit_open_until', 0.0) or 0.0)
        return max(0.0, open_until - time.time())
    except Exception:
        return 0.0


def _kraken_tradable_pair_count() -> int:
    with _bot_state_lock:
        return sum(
            1 for value in bot_state.values()
            if isinstance(value, dict)
            and value.get('broker_pair_status') == 'online'
            and bool(value.get('entries_ready'))
        )


def _get_schedule_status(current_run_time: Optional[str] = None) -> ScheduleStatus:
    """Return display-safe scheduler data; never exposes a past next-run timestamp."""
    now = _schedule_now(current_run_time)
    live_cycle_running = bool(_runtime.live_cycle_running)
    scheduler_ready = bool(_runtime.scheduler_ready)
    next_live = _future_or_none(_runtime.next_live_run_at, now)
    next_hourly = _future_or_none(_runtime.next_hourly_run_at, now)

    if live_cycle_running:
        display_next_live = "Cycle Live en cours"
    elif next_live is not None:
        display_next_live = _format_schedule_dt(next_live) or "Initialisation scheduler"
    else:
        display_next_live = "Initialisation scheduler"

    if next_hourly is not None:
        display_next_wf = _format_schedule_dt(next_hourly) or "Initialisation scheduler"
    elif scheduler_ready:
        display_next_wf = "Cycle Backtest+WF en cours"
    else:
        display_next_wf = "Initialisation scheduler"

    system_status, system_status_detail = _runtime_system_status()

    return {
        'next_live_run_at': None if live_cycle_running else next_live,
        'next_hourly_run_at': next_hourly,
        'live_cycle_running': live_cycle_running,
        'scheduler_ready': scheduler_ready,
        'display_next_live': display_next_live,
        'display_next_wf': display_next_wf,
        'system_status': system_status,
        'system_status_detail': system_status_detail,
    }


def _attach_schedule_status(
    pair_state: Dict[str, Any],
    current_run_time: Optional[str] = None,
) -> Dict[str, Any]:
    status = _get_schedule_status(current_run_time)
    pair_state['next_live_run_at'] = _format_schedule_dt(status['next_live_run_at'])
    pair_state['next_hourly_run_at'] = _format_schedule_dt(status['next_hourly_run_at'])
    pair_state['display_next_live'] = status['display_next_live']
    pair_state['display_next_wf'] = status['display_next_wf']
    pair_state['live_cycle_running'] = status['live_cycle_running']
    pair_state['scheduler_ready'] = status['scheduler_ready']
    pair_state['system_status'] = status['system_status']
    pair_state['system_status_detail'] = status['system_status_detail']
    return pair_state


def _build_tracking_panel_with_schedule(pair_state: Dict[str, Any], current_run_time: str):
    _attach_schedule_status(pair_state, current_run_time)
    return build_tracking_panel(pair_state, current_run_time)


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
        if EXCHANGE_NAME == 'KRAKEN' and str(getattr(config, 'bot_mode', '')).upper() == 'LIVE':
            raise SystemExit(
                "[STATE-CRITICAL] Etat Kraken invalide en LIVE; demarrage refuse. "
                "Corriger/restaurer le fichier d'etat avant de relancer."
            ) from exc
        logger.critical(
            "[STATE-CRITICAL C-04] Demarrage hors LIVE avec etat vide apres erreur load_state. "
            "Reconciliation API obligatoire."
        )
        loaded = {}

    if loaded is not None:
        with _bot_state_lock:
            bot_state.clear()
            bot_state.update(loaded)
            # C-06: purger le champ legacy 'in_position' des états existants
            for _pair_key, _pair_val in bot_state.items():
                if isinstance(_pair_val, dict) and 'in_position' in _pair_val:
                    del _pair_val['in_position']
                if isinstance(_pair_val, dict):
                    for _display_only_key in ('pair_symbol', 'backtest_pair', 'real_trading_pair'):
                        _pair_val.pop(_display_only_key, None)
                if (
                    isinstance(_pair_val, dict)
                    and not str(_pair_key).startswith('_')
                    and str(_pair_key) not in _BOT_STATE_GLOBAL_KEYS
                ):
                    _pair_val.setdefault('broker', EXCHANGE_NAME.lower())
            # C-05: oos_blocked n'est PAS purgé au chargement.
            # Le flag sera levé uniquement quand un backtest validera les OOS gates
            # (execute_scheduled_trading ou backtest_and_display_results).
            # Cela évite qu'un redémarrage efface un blocage légitime.
        _pair_state_count = sum(
            1 for _key, _value in bot_state.items()
            if (
                isinstance(_key, str)
                and isinstance(_value, dict)
                and not _key.startswith('_')
                and _key not in _BOT_STATE_GLOBAL_KEYS
            )
        )
        logger.info(
            "[STATE] bot_state chargé — %d entrées, %d pair_state(s) persisté(s).",
            len(bot_state), _pair_state_count,
        )
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
        if EXCHANGE_NAME == 'KRAKEN' and str(getattr(config, 'bot_mode', '')).upper() == 'LIVE':
            raise SystemExit(
                "[STATE-CRITICAL] Etat Kraken absent/invalide en LIVE; demarrage refuse."
            )

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


# P2-EQUITY: date du dernier refresh equity (évite de le recalculer à chaque cycle de 2 min)
_equity_last_date: str = ''


def _refresh_starting_equity_if_new_day() -> None:
    """Recalcule starting_equity au premier cycle de chaque jour UTC.

    Cette valeur sert de référence journalière pour le daily loss limit et le
    calcul du delta versus départ dans le dashboard. Pour une position déjà
    ouverte, la valorisation de référence est ancrée sur le prix d'entrée,
    tandis que l'equity live du dashboard reste mark-to-market au prix spot.
    Persiste dans _daily_pnl_tracker['starting_equity'] pour le daily loss limit.
    """
    global _equity_last_date
    today = _get_today_iso()
    if _equity_last_date == today:
        return  # déjà calculé pour aujourd'hui
    try:
        _acc = client.get_account()
        _usd_cash = _usd_equivalent_cash_balance(_acc)
        _total = _usd_cash
        # Itérer sur les positions ouvertes (last_order_side='BUY') dans bot_state
        with _bot_state_lock:
            _open_pairs = [
                (bp, ps) for bp, ps in bot_state.items()
                if isinstance(ps, dict) and ps.get('last_order_side') == 'BUY'
            ]
        for _bp, _ps in _open_pairs:
            try:
                _coin, _ = extract_coin_from_pair(_bp)
            except ValueError:
                _coin = _bp.replace('USDT', '').replace('USDC', '').replace('USD', '')
            _found, _, _, _bal = _get_coin_balance(_acc, _coin)
            if _found and _bal > 0:
                # Estimation via entry_price (fiable) — pas de ticker pour éviter
                # d'utiliser backtest_pair qui peut différer du real_pair
                _entry = _ps.get('entry_price', 0)
                if _entry and _entry > 0:
                    _total += _bal * _entry
        with _bot_state_lock:
            _tracker = bot_state.setdefault('_daily_pnl_tracker', {})
            _tracker['starting_equity'] = round(_total, 2)
        save_bot_state(force=True)
        _equity_last_date = today
        logger.info(
            "[P2-EQUITY] Equity de reference jour %s: %.2f USD-equivalent "
            "(cash USD/USDC libre=%.2f + positions valorisees au prix d'entree)",
            today, _total, _usd_cash,
        )
    except Exception as _err:
        logger.warning("[P2-EQUITY] Refresh equity échoué: %s — conserve l'ancien", _err, exc_info=True)


def _is_daily_loss_limit_reached() -> bool:
    """Retourne True si la perte journalière dépasse daily_loss_limit_pct × starting_equity du jour.

    L'equity de référence est recalculée au premier appel de chaque jour UTC
    (USDC libre + valeur des positions ouvertes). Fallback sur config.initial_wallet
    si aucune equity n'a encore été enregistrée.
    """
    today = _get_today_iso()
    with _bot_state_lock:
        tracker = bot_state.get('_daily_pnl_tracker', {})
        total_pnl = tracker.get(today, {}).get('total_pnl', 0.0)
        equity_base = tracker.get('starting_equity', config.initial_wallet)
    limit_usdc = equity_base * config.daily_loss_limit_pct
    if total_pnl < -limit_usdc:
        logger.warning(
            "[DAILY-LIMIT P5-A] Perte journalière %.2f USDC >= limite -%.2f USDC "
            "(%.1f %% de %.0f USDC equity). Achats bloqués jusqu'à 00:00 UTC.",
            abs(total_pnl), limit_usdc,
            config.daily_loss_limit_pct * 100, equity_base,
        )
        # C-08: email d'alerte avec cooldown throttlé (AlertThrottle P1-05)
        if _runtime.daily_loss_throttle.check_and_mark():
            send_trading_alert_email(
                subject="[DAILY LOSS LIMIT] Achats bloques — perte journaliere atteinte",
                body_main=(
                    f"La perte journaliere cumulee ({abs(total_pnl):.2f} USDC) a atteint "
                    f"la limite configuree ({limit_usdc:.2f} USDC = "
                    f"{config.daily_loss_limit_pct * 100:.1f}% de {equity_base:.0f} USDC equity).\n\n"
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
        client=cast(ExchangePort, client),
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
        client=cast(ExchangePort, client),
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
        get_usdc_sells_fn=get_quote_from_last_sell_for_next_buy,
        get_sniper_entry_fn=get_sniper_entry_price,
        check_partial_exits_fn=check_partial_exits_from_history,
        console=console,
        config=config,
        is_valid_stop_loss_fn=lambda symbol, quantity, stop_price, *args, **kwargs: cast(Any, is_valid_stop_loss_order)(
            cast(ExchangePort, client),
            symbol,
            quantity,
            stop_price,
            *args,
            **kwargs,
        ),
        buy_allocation_lock=_usdc_allocation_lock,
    )


def _make_backtest_deps() -> _BacktestDeps:
    """C-03 Phase 3: Injecte les globaux dans les fonctions de backtest_orchestrator."""
    return _BacktestDeps(
        bot_state=bot_state,
        bot_state_lock=_bot_state_lock,
        config=config,
        client=cast(ExchangePort, client),
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
        build_tracking_panel_fn=_build_tracking_panel_with_schedule,
        display_market_changes_fn=display_market_changes,
        detect_market_changes_fn=detect_market_changes,
        display_backtest_table_fn=display_backtest_table,
        backtest_from_dataframe_fn=backtest_from_dataframe,
        select_best_by_calmar_fn=_select_best_by_calmar,
        make_default_pair_state_fn=_make_default_pair_state,
        build_snapshot_fn=_snapshot_from_wf,
        publish_snapshot_fn=_publish_strategy_snapshot,
        parity_backtest_fn=_run_strategy_snapshot_backtest,
        last_backtest_time=_runtime.last_backtest_time,
        live_best_params=_runtime.live_best_params,
        entries_ready=_runtime.entries_ready,
        last_scheduled_trade_time=_runtime.last_scheduled_trade_time,
        oos_alert_last_sent=_oos_alert_last_sent,
        oos_alert_lock=_oos_alert_lock,
        wf_scenarios=WF_SCENARIOS,
        scenario_default_params=SCENARIO_DEFAULT_PARAMS,
        live_panel_enabled_fn=_live_panel_output_enabled,
        describe_live_mode_fn=describe_live_execution_mode,
    )


def _check_pair_vs_exchange(pair_info: Dict[str, Any]) -> 'Optional[_PairStatus]':
    """C-03 wrapper — délègue à position_reconciler avec les globaux injectés."""
    return _check_pair_impl(pair_info, _make_reconcile_deps())


def _handle_pair_discrepancy(status: '_PairStatus') -> None:
    """C-03 wrapper — délègue à position_reconciler avec les globaux injectés."""
    return _handle_pair_impl(status, _make_reconcile_deps())


def reconcile_positions_with_exchange(crypto_pairs_list: List[Dict[str, Any]]) -> None:
    """Vérifie la cohérence entre bot_state et les positions réelles sur Kraken.

    Côté MULTI_SYMBOLS: wrapper qui injecte les globaux dans position_reconciler.
    """
    return _reconcile_impl(crypto_pairs_list, _make_reconcile_deps())


# --- Data Fetching (delegated to data_fetcher.py) ---

def _df_utc_iso(value: Any) -> str:
    try:
        ts = cast(Any, pd.Timestamp(value))
        if ts.tzinfo is None:
            ts = ts.tz_localize('UTC')
        else:
            ts = ts.tz_convert('UTC')
        return str(ts.isoformat())
    except Exception:
        return ""


def _record_kraken_history_status(pair_symbol: str, time_interval: str, start_date: str, df: pd.DataFrame) -> None:
    if df is None or df.empty or not hasattr(client, "get_history_status"):
        return
    try:
        status_obj = cast(Any, client).get_history_status(pair_symbol, time_interval)
        status_dict: Dict[str, Any] = dict(status_obj.as_dict()) if status_obj is not None else {}
        pair_cfg = cast(Any, client).resolve_pair(pair_symbol)
        bars_required = int(status_dict.get('bars_required') or (1150 if time_interval == '1d' else 1500))
        bars_available = int(len(df))
        raw_status_bars = int(status_dict.get('bars_available') or 0)
        source = 'cache' if status_obj is None or raw_status_bars != bars_available else str(status_dict.get('source', 'api'))
        status_dict.update({
            'pair_key': pair_symbol,
            'broker_symbol': getattr(pair_cfg, 'broker_symbol', pair_symbol),
            'timeframe': time_interval,
            'requested_start': str(start_date),
            'oldest_available': _df_utc_iso(df.index[0]),
            'newest_available': _df_utc_iso(df.index[-1]),
            'bars_available': bars_available,
            'bars_required': bars_required,
            'source': source,
            'eligible': bars_available >= bars_required,
            'history_depth_limited': bars_available < bars_required,
        })
        with _bot_state_lock:
            ps = cast(Dict[str, Any], bot_state.setdefault(pair_symbol, _make_default_pair_state()))
            history = ps.setdefault('history_status', {})
            if not isinstance(history, dict):
                history = {}
                ps['history_status'] = history
            history[time_interval] = status_dict
            ps['broker_symbol'] = status_dict['broker_symbol']
    except Exception as exc:
        logger.debug("[KRAKEN-HISTORY] Diagnostic non enregistré pour %s %s: %s", pair_symbol, time_interval, exc)


def _history_start_override(pair_symbol: str) -> Optional[str]:
    with _bot_state_lock:
        ps = bot_state.get(pair_symbol, {})
        history = ps.get('history_status') if isinstance(ps, dict) else None
    if not isinstance(history, Mapping):
        return None
    oldest_values = [
        str(item.get('oldest_available'))
        for item in history.values()
        if isinstance(item, Mapping) and item.get('oldest_available')
    ]
    if not oldest_values:
        return None
    try:
        oldest_dt = min(cast(Any, pd.Timestamp(value)) for value in oldest_values)
        return str(oldest_dt.strftime("%Y-%m-%d"))
    except Exception:
        return min(oldest_values)[:10]


def _kraken_display_symbol_for_pair(backtest_pair: str, pair_state: Mapping[str, Any]) -> str:
    broker_symbol = pair_state.get('broker_symbol')
    if broker_symbol:
        return str(broker_symbol)
    if hasattr(client, "resolve_pair"):
        try:
            pair_cfg = cast(Any, client).resolve_pair(backtest_pair)
            resolved_symbol = getattr(pair_cfg, 'broker_symbol', None)
            if resolved_symbol:
                return str(resolved_symbol)
        except Exception:
            pass
    try:
        base, quote = extract_coin_from_pair(backtest_pair)
        return f"{base}/{quote}"
    except ValueError:
        pass
    return backtest_pair


def _kraken_history_status_for_panel(backtest_pair: str, pair_state: Mapping[str, Any]) -> Dict[str, Any]:
    existing = pair_state.get('history_status')
    if isinstance(existing, dict) and existing:
        return dict(existing)
    if not hasattr(client, "get_history_status"):
        return {}
    history: Dict[str, Any] = {}
    for tf in timeframes:
        try:
            status_obj = cast(Any, client).get_history_status(backtest_pair, tf)
            if status_obj is None:
                continue
            history[tf] = dict(status_obj.as_dict())
        except Exception:
            continue
    return history


def _build_kraken_panel_state(backtest_pair: str, pair_state: Mapping[str, Any]) -> Dict[str, Any]:
    panel_state = dict(pair_state)
    broker_symbol = _kraken_display_symbol_for_pair(backtest_pair, panel_state)
    panel_state['pair_symbol'] = backtest_pair
    panel_state['backtest_pair'] = backtest_pair
    panel_state['real_trading_pair'] = backtest_pair
    panel_state['broker_symbol'] = broker_symbol
    history_status = _kraken_history_status_for_panel(backtest_pair, panel_state)
    if history_status:
        panel_state['history_status'] = history_status
    if _runtime.kraken_fees_confirmed and panel_state.get('kraken_fee_source') == 'api':
        panel_state.setdefault('effective_backtest_fee_taker', float(config.backtest_taker_fee))
        panel_state.setdefault('effective_backtest_fee_maker', float(config.backtest_maker_fee))
        panel_state.setdefault('effective_live_fee_taker', float(config.taker_fee))
        panel_state.setdefault('effective_live_fee_maker', float(config.maker_fee))
    return panel_state


def fetch_historical_data(pair_symbol: str, time_interval: str, start_date: str, force_refresh: bool = False) -> pd.DataFrame:
    """Thin wrapper — delegates to data_fetcher with injected globals."""
    df = _fetch_historical_data(
        pair_symbol, time_interval, start_date, client,
        force_refresh=force_refresh,
        verbose_logs=VERBOSE_LOGS,
        check_network_fn=check_network_connectivity,
        send_alert_fn=send_trading_alert_email,
        data_error_template_fn=data_retrieval_error_email,
        network_error_template_fn=network_error_email,
    )
    _record_kraken_history_status(pair_symbol, time_interval, start_date, df)
    return df

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
    """Thin wrapper for legacy callers; Kraken errors are not downgraded to fallback fees."""
    return _get_binance_trading_fees(
        client, symbol,
        default_taker=config.taker_fee,
        default_maker=config.maker_fee,
    )

# backtest_from_dataframe, empty_result_dict, run_single_backtest_optimized
# imported directly from backtest_runner.py (P3-SRP)

def run_all_backtests(
    backtest_pair: str,
    start_date: str,
    timeframes: List[str],
    sizing_mode: str = 'risk',
    *,
    stoch_thresholds: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    return _run_all_backtests(
        backtest_pair, start_date, timeframes,
        sizing_mode=sizing_mode,
        prepare_base_dataframe_fn=prepare_base_dataframe,
        stoch_thresholds=stoch_thresholds,
    )

def run_parallel_backtests(
    crypto_pairs: List[Dict[str, str]],
    start_date: str,
    timeframes: List[str],
    sizing_mode: str = 'risk',
    *,
    strategy_snapshots: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return _run_parallel_backtests(
        crypto_pairs, start_date, timeframes,
        sizing_mode=sizing_mode,
        prepare_base_dataframe_fn=prepare_base_dataframe,
        strategy_snapshots=strategy_snapshots,
    )

# display_results_for_pair extraite dans display_ui.py (Phase 5)

# --- Live Trading Functions ---
# generate_buy_condition_checker imported from signal_generator.py (P3-SRP)
# generate_sell_condition_checker wrapper  injects config
def generate_sell_condition_checker(best_params: Dict[str, Any], stoch_sell_exit: Optional[float] = None) -> Callable[..., Tuple[bool, Optional[str]]]:
    return _generate_sell_condition_checker(best_params, config=config, stoch_sell_exit=stoch_sell_exit)

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

def get_quote_from_last_sell_for_next_buy(
    real_trading_pair: str,
    since_timestamp_ms: Optional[int] = None,
) -> float:
    del since_timestamp_ms
    amount, _fee, _fee_asset = get_last_sell_trade_usdc(real_trading_pair)
    return float(amount or 0.0)

def get_usdc_from_all_sells_since_last_buy(
    real_trading_pair: str,
    since_timestamp_ms: Optional[int] = None,
) -> float:
    return _get_usdc_from_all_sells(real_trading_pair, client, since_timestamp_ms=since_timestamp_ms)

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
        params = dict(_runtime.live_best_params.get(pair, fallback))
        pair_state = bot_state.get(pair, {})
        persisted_ready = (
            bool(pair_state.get('entries_ready', False))
            if isinstance(pair_state, dict)
            else False
        )
        params['_entries_ready'] = bool(_runtime.entries_ready.get(pair, persisted_ready))
        return params


def is_pair_tradable(
    pair_state: Mapping[str, Any],
    runtime_entries_ready: bool,
    best_params: Mapping[str, Any],
) -> bool:
    entries_ready = bool(
        best_params.get('_entries_ready')
        or pair_state.get('entries_ready')
        or runtime_entries_ready
    )
    if not entries_ready:
        return False
    if str(pair_state.get('broker_pair_status') or 'online') != 'online':
        return False
    if str(pair_state.get('wf_status') or '') != 'validated':
        return False
    active = pair_state.get('active_strategy')
    if not isinstance(active, Mapping):
        return False
    requested = int(active.get('wf_folds_requested') or getattr(config, 'oos_min_folds', 4))
    completed = int(active.get('wf_folds_completed') or 0)
    return (
        bool(active.get('wf_validated'))
        and requested == int(getattr(config, 'oos_min_folds', 4))
        and completed == requested
    )


def describe_live_execution_mode(
    pair: str,
    pair_state: Mapping[str, Any],
    best_params: Mapping[str, Any],
) -> Dict[str, str]:
    if is_pair_tradable(
        pair_state,
        bool(_runtime.entries_ready.get(pair, False)),
        best_params,
    ):
        return {'mode': 'TRADABLE', 'reason': ''}
    status = str(pair_state.get('wf_status') or 'pending_revalidation')
    reason = str(pair_state.get('wf_block_reason') or 'snapshot courant non revalide')
    if str(pair_state.get('broker_pair_status') or 'online') != 'online':
        status = str(pair_state.get('broker_pair_status'))
        reason = f"paire Kraken indisponible: {status}"
    return {'mode': 'PROTECTION_ONLY', 'reason': reason.replace('4/4/4', _wf_fold_label())}


_WF_STATUSES = {
    'pending_revalidation',
    'validated',
    'data_insufficient',
    'oos_failed',
    'publication_failed',
    'protection_only',
    'broker_pair_unavailable',
}


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wf_result_status_and_reason(wf_result: Mapping[str, Any]) -> Tuple[str, Optional[str]]:
    if wf_result.get('any_passed'):
        return 'validated', None
    if wf_result.get('reject_reason'):
        return 'data_insufficient', str(wf_result.get('reject_reason'))
    if wf_result.get('wf_timeout'):
        return 'data_insufficient', 'optuna_timeout'
    diagnostics = wf_result.get('timeframe_diagnostics')
    eligible = list(wf_result.get('eligible_timeframes') or [])
    if isinstance(diagnostics, Mapping):
        rejected = [
            str(value.get('reject_reason'))
            for value in diagnostics.values()
            if isinstance(value, Mapping) and value.get('reject_reason')
        ]
    else:
        rejected = []
    if eligible:
        return 'oos_failed', (
            "OOS gates non validés pour " + ", ".join(str(tf) for tf in eligible)
        )
    if rejected:
        return 'data_insufficient', "; ".join(rejected)
    return 'data_insufficient', "aucun timeframe n'a produit les folds WF requis"


def _set_pair_wf_state(
    pair: str,
    status: str,
    reason: Optional[str],
    *,
    entries_ready: bool,
) -> None:
    if status not in _WF_STATUSES:
        status = 'pending_revalidation'
    ps = cast(Dict[str, Any], bot_state.setdefault(pair, _make_default_pair_state()))
    ps['wf_status'] = status
    ps['wf_block_reason'] = None if entries_ready else reason
    ps['wf_last_attempt_at'] = _utc_iso_now()
    ps['entries_ready'] = entries_ready
    ps['wf_fallback'] = not entries_ready
    ps['runtime_phase'] = 'tradable' if entries_ready and status == 'validated' else 'protection_only'
    ps['live_execution_mode'] = 'TRADABLE' if entries_ready and status == 'validated' else 'PROTECTION_ONLY'
    ps['wf_session_status'] = 'validated' if entries_ready and status == 'validated' else 'not_validated'
    _runtime.entries_ready[pair] = entries_ready


def _record_wf_result(
    pair: str,
    wf_result: Mapping[str, Any],
    *,
    log_tag: str,
    save: bool = False,
) -> Tuple[str, Optional[str]]:
    status, reason = _wf_result_status_and_reason(wf_result)
    with _bot_state_lock:
        _set_pair_wf_state(pair, status, reason, entries_ready=(status == 'validated'))
    if status == 'validated':
        logger.info("[%s] WF_VALIDATED %s — folds stricts %s.", log_tag, pair, _wf_fold_label())
    elif status == 'data_insufficient':
        logger.warning("[%s] WF_BLOCKED_DATA %s — %s", log_tag, pair, reason)
    else:
        logger.warning("[%s] WF_BLOCKED_OOS %s — %s", log_tag, pair, reason)
    if save:
        save_bot_state(force=True)
    return status, reason


def _mark_protection_only(pair: str, reason: str, *, save: bool = False) -> None:
    with _bot_state_lock:
        _set_pair_wf_state(pair, 'protection_only', reason, entries_ready=False)
    if save:
        save_bot_state(force=True)


def _extract_kraken_fee_row(pair: str) -> Dict[str, Any]:
    fees = cast(Any, client).get_trade_fee(symbol=pair)
    if not fees:
        raise RuntimeError(f"TradeVolume Kraken vide pour {pair}")
    row = dict(fees[0])
    if row.get('source') != 'api':
        raise RuntimeError(f"Frais Kraken non confirmes API pour {pair}")
    taker = float(row['takerCommission'])
    maker = float(row['makerCommission'])
    if taker < 0 or maker < 0:
        raise RuntimeError(f"Frais Kraken invalides pour {pair}: taker={taker} maker={maker}")
    row['pair'] = pair
    row['taker'] = taker
    row['maker'] = maker
    return row


def _apply_kraken_fee_parity_to_config(fee_rows: Optional[List[Dict[str, Any]]] = None) -> None:
    """Use verified Kraken API fees for Kraken backtests so WF/live economics match."""
    if EXCHANGE_NAME != 'KRAKEN':
        return
    if fee_rows is None:
        logger.debug("[KRAKEN-FEES] Adoption des frais API reportee.")
        return
    if not fee_rows:
        raise RuntimeError("Aucun frais Kraken API verifie; aucun fallback autorise")
    taker_fee = max(float(row['taker']) for row in fee_rows)
    maker_fee = max(float(row['maker']) for row in fee_rows)
    config.update_live_fees(taker_fee, maker_fee)
    _runtime.taker_fee = taker_fee
    _runtime.maker_fee = maker_fee
    _runtime.kraken_fees_confirmed = True
    object.__setattr__(config, 'backtest_taker_fee', float(config.taker_fee))
    object.__setattr__(config, 'backtest_maker_fee', float(config.maker_fee))
    logger.info(
        "[KRAKEN-FEES] Frais API reels adoptes: taker=%.5f maker=%.5f pairs=%s",
        config.backtest_taker_fee,
        config.backtest_maker_fee,
        ",".join(str(row.get('pair')) for row in fee_rows),
    )


def _persist_kraken_fee_parity(pair_configs: List[Dict[str, str]], fee_rows: List[Dict[str, Any]]) -> None:
    if EXCHANGE_NAME != 'KRAKEN':
        return
    fees_by_pair = {str(row.get('pair')): row for row in fee_rows}
    with _bot_state_lock:
        for pair_info in pair_configs:
            pair = pair_info['backtest_pair']
            ps = cast(Dict[str, Any], bot_state.setdefault(pair, _make_default_pair_state()))
            fee_row = fees_by_pair.get(pair, {})
            ps['effective_backtest_fee_taker'] = float(config.backtest_taker_fee)
            ps['effective_backtest_fee_maker'] = float(config.backtest_maker_fee)
            ps['effective_live_fee_taker'] = float(config.taker_fee)
            ps['effective_live_fee_maker'] = float(config.maker_fee)
            ps['kraken_fee_source'] = 'api'
            ps['kraken_fee_api_taker'] = float(fee_row.get('taker', config.taker_fee))
            ps['kraken_fee_api_maker'] = float(fee_row.get('maker', config.maker_fee))
            ps['kraken_fee_error'] = None


def _require_kraken_real_fees(pair_configs: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Keep only pairs whose Kraken fees were confirmed by the private API."""
    if EXCHANGE_NAME != 'KRAKEN':
        return pair_configs
    fee_rows: List[Dict[str, Any]] = []
    tradable_pairs: List[Dict[str, str]] = []
    for pair_info in pair_configs:
        pair = pair_info['backtest_pair']
        try:
            fee_row = _extract_kraken_fee_row(pair)
            fee_rows.append(fee_row)
            tradable_pairs.append(pair_info)
            logger.info(
                "[KRAKEN-FEES] %s frais API confirmes: taker=%.4f%% maker=%.4f%%",
                pair,
                float(fee_row['taker']) * 100.0,
                float(fee_row['maker']) * 100.0,
            )
        except Exception as exc:
            reason = f"Frais Kraken API indisponibles pour {pair}: {exc}; aucun fallback autorise"
            with _bot_state_lock:
                ps = cast(Dict[str, Any], bot_state.setdefault(pair, _make_default_pair_state()))
                ps['kraken_fee_source'] = 'api_error'
                ps['kraken_fee_error'] = reason
                ps['effective_backtest_fee_taker'] = None
                ps['effective_backtest_fee_maker'] = None
                ps['effective_live_fee_taker'] = None
                ps['effective_live_fee_maker'] = None
                _set_pair_wf_state(pair, 'protection_only', reason, entries_ready=False)
            logger.critical("[KRAKEN-FEES] %s", reason)
            try:
                send_trading_alert_email(
                    subject=f"[KRAKEN] Frais API indisponibles — {pair}",
                    body_main=reason,
                    client=client,
                )
            except Exception as mail_exc:
                logger.warning("[KRAKEN-FEES] Email frais impossible: %s", mail_exc)
    if fee_rows:
        _apply_kraken_fee_parity_to_config(fee_rows)
        _persist_kraken_fee_parity(tradable_pairs, fee_rows)
    else:
        logger.critical("[KRAKEN-FEES] Aucune paire avec frais API reels; trading Kraken bloque.")
    save_bot_state(force=True)
    return tradable_pairs


def _validate_broker_pairs(crypto_pairs_list: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Validate broker pair availability and mark unavailable pairs protection-only."""
    if EXCHANGE_NAME != 'KRAKEN':
        return crypto_pairs_list
    kraken_client = cast(KrakenSpotClient, client)
    valid_pairs: List[Dict[str, str]] = []
    changed = False
    for pair_info in crypto_pairs_list:
        backtest_pair = pair_info['backtest_pair']
        try:
            candidates = kraken_client.list_pair_candidates(backtest_pair)
            pair_cfg = kraken_client.resolve_pair(backtest_pair)
            if pair_cfg.status != 'online':
                raise ValueError(f"status={pair_cfg.status}")
            with _bot_state_lock:
                ps = cast(Dict[str, Any], bot_state.setdefault(backtest_pair, _make_default_pair_state()))
                ps['broker'] = 'kraken'
                ps['broker_symbol'] = pair_cfg.broker_symbol
                ps['broker_pair_status'] = 'online'
                ps['broker_pair_candidates'] = candidates
            valid_pairs.append(pair_info)
            changed = True
            logger.info(
                "[KRAKEN-PREFLIGHT] %s -> %s (%s) valide",
                backtest_pair, pair_cfg.broker_symbol, pair_cfg.broker_pair_id,
            )
        except Exception:
            try:
                candidates = kraken_client.list_pair_candidates(backtest_pair)
            except Exception:
                candidates = []
            candidate_text = ", ".join(
                f"{item.get('broker_symbol')} status={item.get('status')}"
                for item in candidates
            ) or "aucun candidat"
            reason = f"Paire Kraken indisponible via API Spot: {backtest_pair}; candidats: {candidate_text}"
            with _bot_state_lock:
                ps = cast(Dict[str, Any], bot_state.setdefault(backtest_pair, _make_default_pair_state()))
                ps['broker'] = 'kraken'
                ps['broker_symbol'] = None
                ps['broker_pair_status'] = 'broker_pair_unavailable'
                ps['broker_pair_candidates'] = candidates
                _set_pair_wf_state(
                    backtest_pair,
                    'broker_pair_unavailable',
                    reason,
                    entries_ready=False,
                )
            changed = True
            logger.critical("[KRAKEN-PREFLIGHT] %s", reason)
            try:
                send_trading_alert_email(
                    subject=f"[KRAKEN] Paire indisponible — {backtest_pair}",
                    body_main=(
                        f"{reason}\n\n"
                        "Aucun achat ne sera autorise pour cette paire. "
                        "Aucun fallback automatique vers une autre quote n'est effectue."
                    ),
                    client=client,
                )
            except Exception as mail_exc:
                logger.warning("[KRAKEN-PREFLIGHT] Email paire indisponible impossible: %s", mail_exc)
    if changed:
        save_bot_state(force=True)
    return valid_pairs


def _kraken_permission_hint() -> str:
    return (
        "Permissions Kraken requises: Requete fonds, consulter ordres/transactions "
        "ouverts, consulter ordres/transactions clotures, creer/modifier ordres, "
        "annuler/cloturer ordres. Ne pas cocher Depot, Retrait, Gains, Registre ou Export."
    )


def _run_kraken_private_preflight(crypto_pairs_list: List[Dict[str, str]]) -> bool:
    """Run Kraken private API preflight and block entries when private reads fail."""
    if EXCHANGE_NAME != 'KRAKEN' or not hasattr(client, 'preflight_private_api'):
        return True
    try:
        result = cast(Any, client).preflight_private_api()
    except Exception as exc:
        logger.critical("[KRAKEN-PREFLIGHT] Private API KO: %s", exc)
        result = {
            'public_ok': False,
            'balance_ok': False,
            'open_orders_ok': False,
            'closed_orders_ok': False,
            'private_api_ok': False,
            'permission_error': str(exc),
            'nonce_error': str(exc) if 'nonce' in str(exc).lower() else None,
            'tradable': False,
        }
    result_any = cast(Any, result)
    if hasattr(result_any, 'as_dict'):
        result_dict = dict(result_any.as_dict())
    else:
        result_dict = dict(result_any)

    private_api_ok = bool(result_dict.get('private_api_ok'))
    with _bot_state_lock:
        bot_state['kraken_preflight'] = result_dict
        bot_state['kraken_private_api_ok'] = private_api_ok
        if not private_api_ok:
            bot_state['reconcile_failed'] = True
            reason = (
                "KRAKEN PRIVATE API KO: Balance/OpenOrders/ClosedOrders requis. "
                + _kraken_permission_hint()
            )
            bot_state['reconcile_failed_reason'] = reason
            for pair_info in crypto_pairs_list:
                pair = pair_info['backtest_pair']
                ps = cast(Dict[str, Any], bot_state.setdefault(pair, _make_default_pair_state()))
                if ps.get('broker_pair_status') == 'broker_pair_unavailable':
                    continue
                if ps.get('last_order_side') == 'BUY':
                    ps['entries_ready'] = False
                    _runtime.entries_ready[pair] = False
                    continue
                _set_pair_wf_state(pair, 'protection_only', reason, entries_ready=False)
    save_bot_state(force=True)

    if not private_api_ok:
        logger.critical(
            "[KRAKEN-PREFLIGHT] KRAKEN PRIVATE API KO — public_ok=%s balance_ok=%s "
            "open_orders_ok=%s closed_orders_ok=%s. %s",
            result_dict.get('public_ok'),
            result_dict.get('balance_ok'),
            result_dict.get('open_orders_ok'),
            result_dict.get('closed_orders_ok'),
            _kraken_permission_hint(),
        )
        if _runtime.oos_alert_throttle.check_and_mark(key='kraken-private-api-ko'):
            try:
                send_trading_alert_email(
                    subject="[KRAKEN] Private API KO — achats bloques",
                    body_main=(
                        "Le preflight prive Kraken a echoue.\n\n"
                        f"Resultat: {json.dumps(result_dict, ensure_ascii=False)}\n\n"
                        f"{_kraken_permission_hint()}\n\n"
                        "Les achats restent bloques. Le heartbeat et le dashboard restent actifs."
                    ),
                    client=client,
                )
            except Exception as mail_exc:
                logger.warning("[KRAKEN-PREFLIGHT] Email private API KO impossible: %s", mail_exc)
        return False

    logger.info("[KRAKEN-PREFLIGHT] API publique et privee OK — trading conditionnel autorisable apres WF %s.", _wf_fold_label())
    return True


def _log_entries_blocked(pair: str, pair_state: Mapping[str, Any]) -> None:
    status = str(pair_state.get('wf_status') or 'pending_revalidation')
    reason = str(pair_state.get('wf_block_reason') or 'snapshot courant non revalidé')
    key = f"{pair}:{status}:{reason}"
    if _runtime.buy_block_throttle.check_and_mark(key=key):
        logger.warning(
            "[BUY BLOCKED WF-READY] %s — Tradable=NON status=%s reason=%s",
            pair, status, reason,
        )
    else:
        logger.debug(
            "[BUY BLOCKED WF-READY] %s throttled — status=%s reason=%s",
            pair, status, reason,
        )


def _send_exit_alert_with_fallback(subject: str, body: str) -> None:
    try:
        sent = send_trading_alert_email(
            subject=subject,
            body_main=body,
            client=client,
        )
        if sent is False:
            write_alert_unsent_fallback(subject, body)
            logger.error("[EMAIL_FALLBACK_EXIT] Alerte sortie non envoyée: %s", subject)
    except Exception as exc:
        write_alert_unsent_fallback(subject, body)
        logger.error("[EMAIL_FALLBACK_EXIT] Alerte sortie non envoyée: %s (%s)", subject, exc)


def _strategy_data_end(dataframes: Dict[str, pd.DataFrame], timeframe: str) -> str:
    df = dataframes.get(timeframe)
    if df is None or df.empty:
        return ""
    value: Any = df.index[-1]
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


def _snapshot_from_wf(
    pair: str,
    wf_config: Dict[str, Any],
    dataframes: Dict[str, pd.DataFrame],
) -> StrategySnapshot:
    return StrategySnapshot.from_wf_config(
        pair,
        wf_config,
        data_end_time=_strategy_data_end(dataframes, str(wf_config['timeframe'])),
    )


def _legacy_strategy_snapshot(pair: str, pair_state: Dict[str, Any]) -> Optional[StrategySnapshot]:
    """Build a protection-only snapshot from pre-v2 state."""
    params = pair_state.get('last_best_params') or pair_state.get('latest_best_params') or {}
    if pair_state.get('last_order_side') == 'BUY':
        params = {
            **params,
            'scenario': pair_state.get('entry_scenario') or params.get('scenario'),
            'timeframe': pair_state.get('entry_timeframe') or params.get('timeframe'),
            'ema1_period': pair_state.get('entry_ema1') or params.get('ema1_period'),
            'ema2_period': pair_state.get('entry_ema2') or params.get('ema2_period'),
        }
    try:
        scenario = str(params.get('scenario') or 'StochRSI')
        defaults = SCENARIO_DEFAULT_PARAMS.get(scenario, {})
        return StrategySnapshot(
            pair=pair,
            timeframe=str(params.get('timeframe') or '1h'),
            ema1_period=int(params.get('ema1_period') or 26),
            ema2_period=int(params.get('ema2_period') or 50),
            scenario=scenario,
            stoch_buy_min=float(pair_state.get('stoch_buy_min', config.stoch_rsi_buy_min)),
            stoch_buy_max=float(pair_state.get('stoch_buy_max', config.stoch_rsi_buy_max)),
            stoch_sell_exit=float(pair_state.get('stoch_sell_exit', config.stoch_rsi_sell_exit)),
            stoch_period=int(params.get('stoch_period', defaults.get('stoch_period', 14))),
            sma_long=params.get('sma_long', defaults.get('sma_long')),
            adx_period=params.get('adx_period', defaults.get('adx_period')),
            trix_length=params.get('trix_length', defaults.get('trix_length')),
            trix_signal=params.get('trix_signal', defaults.get('trix_signal')),
            wf_method='legacy-protection',
            wf_validated=False,
        )
    except (TypeError, ValueError) as exc:
        logger.warning("[STRATEGY-MIGRATION] %s legacy snapshot invalide: %s", pair, exc)
        return None


def _migrate_and_hydrate_strategies(pair_configs: List[Dict[str, str]]) -> None:
    """Migrate v1 state and hydrate protection params with entries disabled."""
    changed = False
    with _bot_state_lock:
        if 'stoch_params' in bot_state:
            del bot_state['stoch_params']
            changed = True
        for item in pair_configs:
            pair = item['backtest_pair']
            ps = cast(Dict[str, Any], bot_state.setdefault(pair, _make_default_pair_state()))
            snapshot: Optional[StrategySnapshot] = None
            raw_active = ps.get('active_strategy')
            if isinstance(raw_active, dict):
                try:
                    snapshot = StrategySnapshot.from_dict(raw_active)
                except (TypeError, ValueError) as exc:
                    logger.warning("[STRATEGY-MIGRATION] %s active_strategy rejetée: %s", pair, exc)
            if snapshot is None:
                snapshot = _legacy_strategy_snapshot(pair, ps)
                if snapshot is not None:
                    ps['active_strategy'] = snapshot.to_dict()
                    changed = True
            if ps.get('last_order_side') == 'BUY' and not isinstance(ps.get('entry_strategy'), dict):
                entry_snapshot = _legacy_strategy_snapshot(pair, ps)
                if entry_snapshot is not None:
                    ps['entry_strategy'] = entry_snapshot.to_dict()
                    snapshot = entry_snapshot
                    changed = True
            elif ps.get('last_order_side') == 'BUY' and isinstance(ps.get('entry_strategy'), dict):
                try:
                    snapshot = StrategySnapshot.from_dict(cast(Dict[str, Any], ps['entry_strategy']))
                except (TypeError, ValueError) as exc:
                    logger.warning("[STRATEGY-MIGRATION] %s entry_strategy rejetée: %s", pair, exc)
            if snapshot is not None:
                _runtime.live_best_params[pair] = snapshot.as_best_params()
            if ps.get('broker_pair_status') == 'broker_pair_unavailable':
                reason = ps.get('wf_block_reason') or f"Paire {pair} indisponible via API Spot Kraken"
                ps['wf_status'] = 'broker_pair_unavailable'
                ps['wf_block_reason'] = reason
                ps['wf_last_attempt_at'] = _utc_iso_now()
                ps['entries_ready'] = False
                ps['wf_fallback'] = True
                _runtime.entries_ready[pair] = False
                continue
            ps['wf_status'] = 'protection_only'
            ps['wf_block_reason'] = f"snapshot persistant en protection seule jusqu au prochain WF {_wf_fold_label()}"
            ps['wf_last_attempt_at'] = _utc_iso_now()
            ps['entries_ready'] = False
            ps['wf_fallback'] = True
            _runtime.entries_ready[pair] = False
    if changed:
        save_bot_state(force=True)
    logger.info("[STRATEGY] Snapshots hydratés en mode protection; achats bloqués jusqu'au WF courant.")


def _publish_strategy_snapshot(snapshot: StrategySnapshot) -> bool:
    """Persist first, then atomically expose a validated snapshot to live entries."""
    if not snapshot.wf_validated or snapshot.wf_folds_completed != snapshot.wf_folds_requested:
        logger.warning("[STRATEGY] Publication refusée pour %s: snapshot WF incomplet", snapshot.pair)
        with _bot_state_lock:
            _set_pair_wf_state(
                snapshot.pair,
                'publication_failed',
                'snapshot WF incomplet',
                entries_ready=False,
            )
        return False
    with _bot_state_lock:
        ps = cast(Dict[str, Any], bot_state.setdefault(snapshot.pair, _make_default_pair_state()))
        previous = dict(ps)
        ps['active_strategy'] = snapshot.to_dict()
        ps['last_best_params'] = snapshot.as_best_params()
        ps['wf_fallback'] = False
        ps.pop('oos_blocked', None)
        ps.pop('oos_blocked_since', None)
        ps['stoch_buy_min'] = snapshot.stoch_buy_min
        ps['stoch_buy_max'] = snapshot.stoch_buy_max
        ps['stoch_sell_exit'] = snapshot.stoch_sell_exit
        ps['wf_status'] = 'validated'
        ps['wf_block_reason'] = None
        ps['wf_last_attempt_at'] = _utc_iso_now()
        ps['entries_ready'] = False
        before_failures = _runtime.save_failure_count
        save_bot_state(force=True)
        if _runtime.save_failure_count > before_failures:
            ps.clear()
            ps.update(previous)
            _set_pair_wf_state(
                snapshot.pair,
                'publication_failed',
                'échec de persistence du snapshot validé',
                entries_ready=False,
            )
            logger.critical("[STRATEGY] Publication annulée pour %s: persistence échouée", snapshot.pair)
            try:
                send_trading_alert_email(
                    subject=f"[CRITIQUE STRATEGY] Snapshot non persisté — {snapshot.pair}",
                    body_main=(
                        f"Le snapshot {snapshot.snapshot_id} n'a pas pu être persisté.\n"
                        "Les achats restent bloqués; la protection des positions continue."
                    ),
                    client=client,
                )
            except Exception as alert_exc:
                logger.warning("[STRATEGY] Alerte publication impossible: %s", alert_exc)
            return False
        _runtime.live_best_params[snapshot.pair] = snapshot.as_best_params()
        _runtime.entries_ready[snapshot.pair] = True
        ps['entries_ready'] = True
        _runtime.last_backtest_time[snapshot.pair] = time.time()
    logger.info(
        "[STRATEGY] %s snapshot=%s publié — %s EMA(%d/%d) %s, Stoch %.2f/%.2f/%.2f, folds=%d/%d",
        snapshot.pair, snapshot.snapshot_id, snapshot.scenario,
        snapshot.ema1_period, snapshot.ema2_period, snapshot.timeframe,
        snapshot.stoch_buy_min, snapshot.stoch_buy_max, snapshot.stoch_sell_exit,
        snapshot.wf_folds_completed, snapshot.wf_folds_requested,
    )
    return True


def _run_strategy_snapshot_backtest(
    snapshot: StrategySnapshot,
    dataframes: Dict[str, pd.DataFrame],
    sizing_mode: str,
) -> Optional[Dict[str, Any]]:
    """Audit the exact published snapshot on the full available sample."""
    df = dataframes.get(snapshot.timeframe)
    if df is None or df.empty:
        logger.warning("[PARITY-BACKTEST] %s: dataframe %s absent", snapshot.pair, snapshot.timeframe)
        return None
    result = backtest_from_dataframe(
        df=df,
        ema1_period=snapshot.ema1_period,
        ema2_period=snapshot.ema2_period,
        sma_long=snapshot.sma_long,
        adx_period=snapshot.adx_period,
        trix_length=snapshot.trix_length,
        trix_signal=snapshot.trix_signal,
        sizing_mode=sizing_mode,
        periods_per_year=periods_per_year_for_timeframe(snapshot.timeframe),
        stoch_buy_min_override=snapshot.stoch_buy_min,
        stoch_buy_max_override=snapshot.stoch_buy_max,
        stoch_sell_exit_override=snapshot.stoch_sell_exit,
    )
    logger.info(
        "[PARITY-BACKTEST] %s snapshot=%s final=%.2f Sharpe=%.3f DD=%.2f%%",
        snapshot.pair,
        snapshot.snapshot_id,
        float(result.get('final_wallet', 0.0)),
        float(result.get('sharpe_ratio', 0.0)),
        float(result.get('max_drawdown', 0.0)),
    )
    console.print(
        f"[bold cyan][PARITY-BACKTEST][/bold cyan] {snapshot.pair} "
        f"snapshot={snapshot.snapshot_id} | final={float(result.get('final_wallet', 0.0)):.2f} "
        f"| Sharpe={float(result.get('sharpe_ratio', 0.0)):.3f} "
        f"| DD={float(result.get('max_drawdown', 0.0)):.2f}%"
    )
    return result


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


def _dispatch_live_pairs(pair_configs: List[Dict[str, str]], sizing_mode: str) -> None:
    """Run one live protection/trading cycle for every configured pair."""
    if not pair_configs:
        return
    if len(pair_configs) == 1:
        item = pair_configs[0]
        execute_live_trading_only(item['real_pair'], item['backtest_pair'], sizing_mode)
        return
    with ThreadPoolExecutor(max_workers=len(pair_configs), thread_name_prefix='live') as pool:
        futures = {
            pool.submit(
                execute_live_trading_only,
                item['real_pair'],
                item['backtest_pair'],
                sizing_mode,
            ): item['backtest_pair']
            for item in pair_configs
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                logger.error("[LIVE-WORKER] %s error: %s", futures[future], exc)


class _LiveTradingWorker:
    """Managed monotonic worker that remains active during the long bootstrap."""

    def __init__(
        self,
        pair_configs: List[Dict[str, str]],
        sizing_mode: str,
        stop_event: threading.Event,
        interval_seconds: float,
    ) -> None:
        self._pair_configs = list(pair_configs)
        self._sizing_mode = sizing_mode
        self._stop_event = stop_event
        self._interval_seconds = max(1.0, interval_seconds)
        self._thread = threading.Thread(
            target=self._run,
            name='kraken-live-monitor',
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        next_tick = time.monotonic()
        while not self._stop_event.is_set():
            _runtime.live_cycle_running = True
            _runtime.next_live_run_at = None
            try:
                _dispatch_live_pairs(self._pair_configs, self._sizing_mode)
            except Exception as exc:
                logger.exception("[LIVE-WORKER] cycle global échoué: %s", exc)
            finally:
                _runtime.live_cycle_running = False
            next_tick += self._interval_seconds
            delay = max(0.0, next_tick - time.monotonic())
            _runtime.next_live_run_at = datetime.now() + timedelta(seconds=delay)
            if self._stop_event.wait(delay):
                break

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

    _, usdc_balance_free_val, _, _ = _get_coin_balance(account_info, quote_currency)
    usdc_balance = usdc_balance_free_val
    global _last_usdc_balance
    _last_usdc_balance = usdc_balance

    _coin_found, coin_balance_free, coin_balance_locked, coin_balance = _get_coin_balance(
        account_info, coin_symbol
    )
    if not _coin_found:
        coin_balance_free = 0.0
        coin_balance_locked = 0.0
        coin_balance = 0.0

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


def _repair_missing_exchange_sl_for_open_position(
    real_trading_pair: str,
    backtest_pair: str,
    pair_state: 'PairState',
    coin_symbol: str,
    coin_balance: float,
    min_qty_dec: Decimal,
    step_size_dec: Decimal,
    step_decimals: int,
) -> bool:
    """Ensure a BUY state has a confirmed exchange stop before buy logic runs."""
    if pair_state.get('last_order_side') != 'BUY' or pair_state.get('sl_exchange_placed'):
        return True

    stop_types = {'STOP_LOSS', 'STOP_LOSS_LIMIT', 'TAKE_PROFIT', 'TAKE_PROFIT_LIMIT'}
    try:
        open_orders = client.get_open_orders(symbol=real_trading_pair)
        if not isinstance(open_orders, list):
            open_orders = []
        existing_stop = next(
            (
                order for order in open_orders
                if order.get('side') == 'SELL' and order.get('type') in stop_types
            ),
            None,
        )
        if existing_stop:
            with _bot_state_lock:
                pair_state['sl_order_id'] = existing_stop.get('orderId')
                pair_state['sl_exchange_placed'] = True
            save_bot_state(force=True)
            logger.critical(
                "[SL-REPAIR] %s BUY sans sl_exchange_placed: stop ouvert rattache orderId=%s.",
                backtest_pair, existing_stop.get('orderId'),
            )
            return True
    except Exception as exc:
        logger.warning("[SL-REPAIR] get_open_orders impossible pour %s: %s", backtest_pair, exc)

    api_key = getattr(client, 'api_key', None)
    api_secret = getattr(client, 'api_secret', None)
    if not isinstance(api_key, str) or not isinstance(api_secret, (str, bytes, bytearray)):
        logger.warning(
            "[SL-REPAIR] Client exchange non initialise ou mocke pour %s; repair SL saute.",
            backtest_pair,
        )
        return True

    sl_price = pair_state.get('stop_loss') or pair_state.get('stop_loss_at_entry')
    if not sl_price or coin_balance <= float(min_qty_dec):
        reason = (
            f"BUY sans SL reparable impossible pour {backtest_pair}: "
            f"stop={sl_price}, balance={coin_balance} {coin_symbol}"
        )
        with _bot_state_lock:
            set_emergency_halt(bot_state, reason)
        _write_reconcile_required_marker(reason, source="sl_repair")
        save_bot_state(force=True)
        return False

    qty_dec = (Decimal(str(coin_balance)) // step_size_dec) * step_size_dec
    if qty_dec < min_qty_dec:
        reason = f"BUY sans SL reparable impossible pour {backtest_pair}: qty={qty_dec} < minQty={min_qty_dec}"
        with _bot_state_lock:
            set_emergency_halt(bot_state, reason)
        _write_reconcile_required_marker(reason, source="sl_repair")
        save_bot_state(force=True)
        return False

    qty_str = f"{qty_dec:.{step_decimals}f}"
    try:
        result = place_exchange_stop_loss_order(
            symbol=real_trading_pair,
            quantity=qty_str,
            stop_price=float(sl_price),
        )
        with _bot_state_lock:
            pair_state['sl_order_id'] = result.get('orderId')
            pair_state['sl_exchange_placed'] = True
        save_bot_state(force=True)
        logger.critical(
            "[SL-REPAIR] SL exchange replace au boot/live pour %s qty=%s stop=%s orderId=%s.",
            backtest_pair, qty_str, sl_price, result.get('orderId'),
        )
        return True
    except Exception as exc:
        reason = f"Echec repair SL pour {backtest_pair}: {exc}"
        with _bot_state_lock:
            set_emergency_halt(bot_state, reason)
        _write_reconcile_required_marker(reason, source="sl_repair")
        save_bot_state(force=True)
        try:
            send_trading_alert_email(
                subject=f"[EMERGENCY HALT] Repair SL impossible: {backtest_pair}",
                body_main=(
                    f"Position BUY sans stop-loss exchange confirme.\n\n"
                    f"Paire: {backtest_pair}\n"
                    f"Quantite: {qty_str} {coin_symbol}\n"
                    f"Stop: {sl_price}\n"
                    f"Erreur: {exc}\n\n"
                    "Achats bloques via emergency_halt."
                ),
                client=client,
            )
        except Exception as alert_exc:
            logger.warning("[SL-REPAIR] Email impossible: %s", alert_exc)
        return False


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

    if df.empty:
        logger.error(
            "[TRADING] Données insuffisantes pour %s: %d lignes – cycle ignoré",
            real_trading_pair, len(df),
        )
        return None

    row = df.iloc[-1].copy()  # mutable copy; data_fetcher removes Kraken's current unclosed candle

    # A-2: Multi-timeframe filter — compute 4h EMA trend for the signal row
    row['_data_stale'] = False
    if isinstance(df.index, pd.DatetimeIndex):
        try:
            _last_ts = pd.Timestamp(cast(Any, df.index[-1]))
            if _last_ts.tzinfo is None:
                _last_ts = _last_ts.tz_localize('UTC')
            else:
                _last_ts = _last_ts.tz_convert('UTC')
            _age_s = (pd.Timestamp.now(tz='UTC') - _last_ts).total_seconds()
            _tf_s = TIMEFRAME_SECONDS.get(time_interval, 3600)
            _stale_threshold = max(_tf_s * 2.5, _tf_s + 300)
            if _age_s > _stale_threshold:
                row['_data_stale'] = True
                logger.critical(
                    "[DATA STALE] %s %s derniere bougie age=%.0fs (seuil=%.0fs). "
                    "Achats et signaux strategy bloques; protection position maintenue.",
                    real_trading_pair, time_interval, _age_s, _stale_threshold,
                )
        except Exception as _stale_err:
            row['_data_stale'] = True
            logger.warning("[DATA STALE] Freshness check impossible pour %s: %s", real_trading_pair, _stale_err)

    if getattr(config, 'mtf_filter_enabled', False) and isinstance(df.index, pd.DatetimeIndex):
        try:
            _ema_fast = getattr(config, 'mtf_ema_fast', 18)
            _ema_slow = getattr(config, 'mtf_ema_slow', 58)
            _bullish_aligned = compute_mtf_bullish(df, _ema_fast, _ema_slow)
            _mtf_val = _bullish_aligned.iloc[-1] if len(_bullish_aligned) >= 1 else 0.0
            row['mtf_bullish'] = _mtf_val
        except Exception as _mtf_err:
            logger.warning("[A-2] MTF computation failed: %s — filter disabled for this cycle", _mtf_err)

    # ML-03: Inject ATR median (last 30 days) for adaptive stop multiplier at entry
    if 'atr' in df.columns:
        try:
            _periods_per_year = periods_per_year_for_timeframe(time_interval)
            _atr_window = atr_median_window(_periods_per_year)
            _atr_medians = df['atr'].rolling(window=_atr_window, min_periods=10).median()
            _atr_median = _atr_medians.iloc[-1]
            if pd.notna(_atr_median) and _atr_median > 0:
                row['atr_median_30d'] = float(_atr_median)
        except Exception as _atr_med_err:
            logger.debug("[ML-03] ATR median 30d skipped: %s", _atr_med_err)

    current_price = float(client.get_symbol_ticker(symbol=real_trading_pair)['price'])
    return df, row, current_price


def _sync_order_history(
    real_trading_pair: str,
    pair_state: 'PairState',
    *,
    coin_balance: Optional[float] = None,
    min_qty: Optional[float] = None,
) -> Tuple[List[Any], Optional[str]]:
    """Synchronise last_order_side avec l'historique des ordres Kraken.

    P5-DASH: ne pas écraser BUY→SELL quand la position est encore ouverte
    (partial sell). entry_price != None signifie que la position n'a pas été
    entièrement clôturée (signal sell et SL reset entry_price à None).

    Returns:
        (orders, last_side)
    """
    orders = client.get_all_orders(symbol=real_trading_pair, limit=20)
    if not isinstance(orders, list):
        orders = [orders] if orders else []
    filled_orders = [o for o in reversed(orders) if o['status'] == 'FILLED']
    last_filled_order = filled_orders[0] if filled_orders else None
    last_side = last_filled_order['side'] if last_filled_order else None
    effective_last_side = last_side

    has_exchange_position = None
    if coin_balance is not None:
        try:
            min_qty_value = float(min_qty or 0.0)
            has_exchange_position = float(coin_balance) >= max(min_qty_value, 0.0)
        except (TypeError, ValueError):
            has_exchange_position = None

    if last_side == 'BUY' and has_exchange_position is False:
        logger.info(
            "[SYNC] Dernier ordre historique BUY ignore pour %s: balance exchange=%.8f < min_qty=%.8f",
            real_trading_pair,
            float(coin_balance or 0.0),
            float(min_qty or 0.0),
        )
        effective_last_side = None

    if effective_last_side and pair_state.get('last_order_side') != effective_last_side:
        # P5-DASH: guard partial sell — position still open if entry_price is set
        if (effective_last_side == 'SELL'
                and pair_state.get('last_order_side') == 'BUY'
                and pair_state.get('entry_price') is not None
                and has_exchange_position is not False):
            logger.debug(
                "[SYNC] Partial sell detected for %s — keeping last_order_side='BUY'",
                real_trading_pair,
            )
        else:
            pair_state['last_order_side'] = effective_last_side
            if last_side != 'BUY':  # ST-P2-02: réinitialise le flag drawdown à la fermeture de position
                pair_state['drawdown_halted'] = False
            save_bot_state()

    return orders, effective_last_side


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
    # P0-STOP: emergency halt blocks entries only; protection and exits must keep running.
    if bot_state.get('emergency_halt'):
        logger.critical(
            "[EMERGENCY HALT] Achats bloqués, surveillance position maintenue — raison: %s.",
            bot_state.get('emergency_halt_reason', 'inconnue'),
        )
        best_params = {**best_params, '_entries_ready': False}

    # P2-EQUITY: recalculer l'equity de référence au premier cycle de chaque jour UTC
    # Defer equity refresh until after the no-buy gate to avoid private Balance
    # calls when entries are blocked and no local position is open.

    # pair_state dérivé depuis bot_state — les mutations du dict se propagent par référence.
    with _bot_state_lock:
        pair_state: PairState = cast('PairState', bot_state.setdefault(backtest_pair, {}))
    if 'last_order_side' not in pair_state:
        pair_state['last_order_side'] = None

    _entries_ready_pre_balance = bool(
        best_params.get('_entries_ready')
        or pair_state.get('entries_ready')
        or _runtime.entries_ready.get(backtest_pair, False)
    )
    _has_local_open_state = pair_state.get('last_order_side') == 'BUY'
    if (
        getattr(config, 'bot_mode', 'DEMO') == 'LIVE'
        and not _entries_ready_pre_balance
        and not _has_local_open_state
    ):
        with _bot_state_lock:
            pair_state['entries_ready'] = False
            _runtime.entries_ready[backtest_pair] = False
        _log_entries_blocked(backtest_pair, pair_state)
        return

    # EM-P1-04: Alerte si position BUY ouverte sans SL confirmé sur l'exchange
    if pair_state.get('last_order_side') == 'BUY' and not pair_state.get('sl_exchange_placed'):
        # F-SL-FIX: Auto-correction — si sl_order_id existe, verifier sur Kraken avant d'alerter
        _sl_oid_check = pair_state.get('sl_order_id')
        _sl_auto_corrected = False
        if _sl_oid_check and str(_sl_oid_check).isdigit():
            try:
                _open_ords = client.get_open_orders(symbol=real_trading_pair)
                _stop_types = {'STOP_LOSS', 'STOP_LOSS_LIMIT'}
                _has_active_sl = any(
                    str(o.get('orderId')) == str(_sl_oid_check) and o.get('type', '') in _stop_types
                    for o in _open_ords
                )
                if _has_active_sl:
                    with _bot_state_lock:
                        pair_state['sl_exchange_placed'] = True
                    save_bot_state(force=True)
                    logger.info(
                        "[SL-MANQUANT F-SL-FIX] Auto-correction: SL orderId=%s actif sur %s "
                        "pour %s — sl_exchange_placed corrigé à True.",
                        _sl_oid_check, exchange_label(), backtest_pair,
                    )
                    _sl_auto_corrected = True
            except Exception as _sl_check_err:
                logger.warning(
                    "[SL-MANQUANT F-SL-FIX] Vérification %s impossible pour %s: %s",
                    exchange_label(), backtest_pair, _sl_check_err,
                )
        if not _sl_auto_corrected and _runtime.sl_missing_throttle.check_and_mark(key=backtest_pair):
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
                        f"Le stop-loss n'est pas confirmé sur {exchange_label()}. "
                        f"Vérifiez manuellement et relancez si nécessaire.\n\n"
                        f"Prix d'entrée  : {pair_state.get('entry_price', 'N/A')}\n"
                        f"Stop prévu     : {pair_state.get('stop_loss', 'N/A')}\n"
                        f"sl_order_id    : {pair_state.get('sl_order_id', 'N/A')}\n"
                    ),
                    client=client,
                )
            except Exception as _sl_alert_err:
                logger.error("[SL-MANQUANT] Email impossible: %s", _sl_alert_err)

    # SL-POLL: vérifie à chaque cycle si le SL exchange actif a été exécuté.
    # Ce check passe avant _sync_order_history pour ne pas perdre l'alerte de sortie.
    if (
        pair_state.get('last_order_side') == 'BUY'
        and pair_state.get('sl_exchange_placed')
        and pair_state.get('sl_order_id')
    ):
        _poll_cnt = _runtime.sl_poll_counters.get(backtest_pair, 0) + 1
        _runtime.sl_poll_counters[backtest_pair] = _poll_cnt
        _sl_oid_poll = pair_state.get('sl_order_id')
        try:
            _sl_info_poll = client.get_order(
                symbol=real_trading_pair, orderId=_sl_oid_poll
            )
            if str(_sl_info_poll.get('status', '')).upper() == 'FILLED':
                def _as_float_poll(value: Any) -> float:
                    try:
                        return float(value or 0.0)
                    except (TypeError, ValueError):
                        return 0.0

                _sl_eq_poll = _as_float_poll(_sl_info_poll.get('executedQty'))
                _sl_cq_poll = _as_float_poll(_sl_info_poll.get('cummulativeQuoteQty'))
                _sl_price_poll = (_sl_cq_poll / _sl_eq_poll) if _sl_eq_poll > 0 else 0.0
                _sl_ut_poll = _sl_info_poll.get('updateTime', 0)
                _sl_fill_ts_poll = float(_sl_ut_poll) / 1000.0 if _sl_ut_poll else 0.0

                _entry_tf_poll = pair_state.get('entry_timeframe') or '1h'
                _entry_price_poll = _as_float_poll(pair_state.get('entry_price'))
                _stop_loss_fixed_poll = _as_float_poll(pair_state.get('stop_loss_at_entry'))
                _trailing_stop_poll = _as_float_poll(pair_state.get('trailing_stop'))
                _max_price_poll = _as_float_poll(pair_state.get('max_price'))
                _entry_scenario_poll = (
                    pair_state.get('entry_scenario') or "Multi-Timeframe EMA/StochRSI"
                )
                _is_trailing_poll = (
                    bool(pair_state.get('trailing_stop_activated'))
                    and _trailing_stop_poll > 0
                    and (
                        _stop_loss_fixed_poll <= 0
                        or _trailing_stop_poll > _stop_loss_fixed_poll
                    )
                )
                _stop_type_poll = (
                    "TRAILING-STOP (dynamique)"
                    if _is_trailing_poll
                    else "STOP-LOSS exchange"
                )
                _pnl_pct_poll = (
                    ((_sl_price_poll - _entry_price_poll) / _entry_price_poll) * 100.0
                    if _entry_price_poll > 0 and _sl_price_poll > 0
                    else None
                )
                _extra_poll = (
                    f"Ordre SL {exchange_label():<8}: {_sl_oid_poll}\n"
                    f"Type stop         : {_stop_type_poll}\n"
                )
                if _is_trailing_poll:
                    _extra_poll += (
                        f"Prix entree       : {_entry_price_poll:.4f} USDC\n"
                        f"Prix max atteint  : {_max_price_poll:.4f} USDC\n"
                        f"Niveau trailing   : {_trailing_stop_poll:.4f} USDC\n"
                        f"Stop initial      : {_stop_loss_fixed_poll:.4f} USDC\n"
                    )

                logger.info(
                    '[SL-POLL] %s orderId=%s FILLED détecté en temps réel pour %s — '
                    'prix %.6g USDC, qty %.8f',
                    _stop_type_poll, _sl_oid_poll, backtest_pair, _sl_price_poll, _sl_eq_poll,
                )
                logger.info(
                    "[EXIT-DETECTED] %s — reason=%s source=exchange_sl orderId=%s price=%.8g qty=%.8f",
                    backtest_pair, _stop_type_poll, _sl_oid_poll, _sl_price_poll, _sl_eq_poll,
                )
                # Reset pair_state (même logique que position_reconciler.py L.284-296)
                with _bot_state_lock:
                    pair_state['last_order_side'] = 'SELL'
                    pair_state['sl_order_id'] = None
                    pair_state['sl_exchange_placed'] = False
                    pair_state['entry_price'] = None
                    pair_state['max_price'] = None
                    pair_state['stop_loss'] = None
                    pair_state['trailing_stop'] = None
                    pair_state['trailing_stop_activated'] = False
                    pair_state['atr_at_entry'] = None
                    pair_state['stop_loss_at_entry'] = None
                    pair_state['trailing_activation_price_at_entry'] = None
                    pair_state['initial_position_size'] = None
                    pair_state['partial_taken_1'] = False
                    pair_state['partial_taken_2'] = False
                    pair_state['breakeven_triggered'] = False
                    pair_state['entry_scenario'] = None
                    pair_state['entry_timeframe'] = None
                    pair_state['entry_ema1'] = None
                    pair_state['entry_ema2'] = None
                    pair_state['entry_strategy'] = None
                # A-3: cooldown post-SL si configuré
                _sl_cd_candles = getattr(config, 'stop_loss_cooldown_candles', 0)
                if _sl_cd_candles > 0:
                    _TF_SEC_POLL: Dict[str, int] = {
                        '1m': 60, '5m': 300, '15m': 900, '30m': 1800,
                        '1h': 3600, '4h': 14400, '1d': 86400,
                    }
                    _cd_sec_poll = _TF_SEC_POLL.get(_entry_tf_poll, 3600)
                    _cd_base_poll = _sl_fill_ts_poll if _sl_fill_ts_poll > 0 else time.time()
                    _cd_until_poll = _cd_base_poll + (_sl_cd_candles * _cd_sec_poll)
                    if _cd_until_poll > time.time():
                        with _bot_state_lock:
                            pair_state['_stop_loss_cooldown_until'] = _cd_until_poll  # type: ignore[typeddict-unknown-key]
                _runtime.sl_poll_counters[backtest_pair] = 0
                save_bot_state(force=True)
                try:
                    subj, body = sell_executed_email(
                        pair=backtest_pair,
                        qty=_sl_eq_poll,
                        price=_sl_price_poll,
                        usdc_received=_sl_cq_poll,
                        sell_reason=f"{_stop_type_poll} - exchange FILLED",
                        pnl_pct=_pnl_pct_poll,
                        strategy=_entry_scenario_poll,
                        extra_details=_extra_poll,
                    )
                    _send_exit_alert_with_fallback(subj, body)
                except Exception as _sl_poll_mail_err:
                    logger.error('[SL-POLL] Email impossible: %s', _sl_poll_mail_err)
                return
        except Exception as _sl_poll_err:
            logger.debug('[SL-POLL] Erreur polling SL pour %s: %s', backtest_pair, _sl_poll_err)

    # F-COH: Si position ouverte, verrouiller les params de l’entrée pour le signal de vente.
    # Garantit que le sell est évalué sur le même scenario/TF/EMA que l’achat,
    # même si le WF a sélectionné une stratégie différente entre-temps.
    _entry_strategy_raw = pair_state.get('entry_strategy')
    if pair_state.get('last_order_side') == 'BUY' and isinstance(_entry_strategy_raw, dict):
        try:
            _entry_snapshot = StrategySnapshot.from_dict(_entry_strategy_raw)
            best_params = {**best_params, **_entry_snapshot.as_best_params()}
            time_interval = _entry_snapshot.timeframe
            logger.info(
                "[F-COH] Position ouverte — snapshot entrée verrouillé: %s (%s)",
                _entry_snapshot.snapshot_id, _entry_snapshot.timeframe,
            )
        except (TypeError, ValueError) as _entry_snapshot_err:
            logger.error("[F-COH] entry_strategy invalide pour %s: %s", backtest_pair, _entry_snapshot_err)
    elif pair_state.get('last_order_side') == 'BUY' and pair_state.get('entry_scenario'):
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
        _refresh_starting_equity_if_new_day()

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
                    "[P0-02] Filtres %s introuvables pour %s avec position BUY ouverte "
                    "— SL management compromis ce cycle.",
                    exchange_label(), real_trading_pair,
                )
            return
        (min_qty, max_qty, step_size, min_notional,
         min_qty_dec, max_qty_dec, step_size_dec, step_decimals) = flt

        if not _repair_missing_exchange_sl_for_open_position(
            real_trading_pair,
            backtest_pair,
            pair_state,
            coin_symbol,
            coin_balance,
            min_qty_dec,
            step_size_dec,
            step_decimals,
        ):
            return

        _entries_ready_now = bool(
            best_params.get('_entries_ready')
            or pair_state.get('entries_ready')
            or _runtime.entries_ready.get(backtest_pair, False)
        )
        _has_open_state = pair_state.get('last_order_side') == 'BUY'
        if (
            getattr(config, 'bot_mode', 'DEMO') == 'LIVE'
            and not _entries_ready_now
            and not _has_open_state
            and coin_balance < min_qty
        ):
            with _bot_state_lock:
                pair_state['entries_ready'] = False
                _runtime.entries_ready[backtest_pair] = False
            _log_entries_blocked(backtest_pair, pair_state)
            return

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
        df, row, current_price = mkt

        # P2-3: Alimenter le guard corrélation avec la dernière bougie fermée
        if isinstance(df.index, pd.DatetimeIndex) and len(df) >= 1 and 'close' in df.columns:
            feed_candle(
                real_trading_pair,
                cast(pd.Timestamp, df.index[-1]).timestamp(),
                float(df['close'].iloc[-1]),
            )

        # EM-P2-05: Alerte drawdown max si PnL non réalisé dépasse le seuil configuré
        if (
            pair_state.get('last_order_side') == 'BUY'
            and pair_state.get('entry_price')
            and config.max_drawdown_pct > 0
        ):
            _entry_p = float(pair_state.get('entry_price') or 0.0)
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
                                f"Vérifiez l'état du SL sur {exchange_label()}."
                            ),
                            client=client,
                        )
                    except Exception as _dd_alert_err:
                        logger.error("[DRAWDOWN] Email impossible: %s", _dd_alert_err)

        # === P3-04: HISTORIQUE ORDRES (helper) ===
        orders, last_side = _sync_order_history(
            real_trading_pair,
            pair_state,
            coin_balance=coin_balance,
            min_qty=min_qty,
        )

        # === C-15: BUILD CONTEXT + DELEGATE TO SUB-FUNCTIONS ===
        deps = _make_trading_deps()
        ctx = _TradeCtx(
            real_trading_pair=real_trading_pair, backtest_pair=backtest_pair,
            time_interval=time_interval, sizing_mode=sizing_mode,
            pair_state=cast(Dict[str, Any], pair_state), best_params=best_params,
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
        if position_has_crypto is None:
            # P0-DUST: reset dust BUY→SELL dans ce cycle — pas d'achat immédiat,
            # on skippe ce cycle pour éviter un BUY juste après le reset.
            logger.info(
                "[DUST P0-BUY] %s — cycle skipé après reset dust pour éviter un achat immédiat.",
                backtest_pair,
            )
            return
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
            # P2-3: corrélation systémique — bloquer si >0.85 avec paire en position
            _corr_ok, _corr_reason = check_correlation_guard(real_trading_pair, bot_state)
            if not _corr_ok:
                logger.warning(
                    "[CORR-GUARD] Achat bloqué pour %s — %s",
                    real_trading_pair, _corr_reason,
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

    # OOS-STREAK (scheduled): suit les cycles consécutifs avec oos_blocked=True.
    # Renforce la persistance du blocage (prévient un unblock par gate IS seule).
    _OOS_FAIL_STREAK_N = 6
    with _bot_state_lock:
        _ps_streak = cast(Dict[str, Any], bot_state.get(backtest_pair, {}))
        if _ps_streak.get('oos_blocked'):
            _streak_s = _runtime.oos_fail_streaks.get(backtest_pair, 0) + 1
            _runtime.oos_fail_streaks[backtest_pair] = _streak_s
            if _streak_s >= _OOS_FAIL_STREAK_N:
                logger.critical(
                    '[OOS-STREAK] %s — %d cycles consécutifs oos_blocked=True. '
                    'Blocage renforcé (WF OOS en échec prolongé).',
                    backtest_pair, _streak_s,
                )
                # Forcer oos_blocked_since récent pour garder la trace du début de streak
                if 'oos_blocked_since' not in _ps_streak:
                    _ps_streak['oos_blocked_since'] = _ps_streak.get('oos_blocked_since', 0)
        else:
            _prev_s = _runtime.oos_fail_streaks.get(backtest_pair, 0)
            if _prev_s > 0:
                logger.info(
                    '[OOS-STREAK] %s — oos_blocked levé. Streak %d réinitialisé.',
                    backtest_pair, _prev_s,
                )
            _runtime.oos_fail_streaks[backtest_pair] = 0

if __name__ == "__main__":

    _install_kraken_email_error_log_handler()
    full_timestamp_resync()
    logger.info("Synchronisation complète exécutée au démarrage.")

    # MODE ULTRA-ROBUSTE SANS POPUP - COMPENSATION KRAKEN PURE
    logger.info("Bot crypto H24/7 - Mode ultra-robuste avec privileges admin")

    crypto_pairs = [
        {"backtest_pair": "XRPUSDC",  "real_pair": "XRPUSDC"},
        {"backtest_pair": "ONDOUSD",  "real_pair": "ONDOUSD"},
        {"backtest_pair": "CROUSDC",  "real_pair": "CROUSDC"},
    ]
    _voluntary_event   = threading.Event()
    _shutdown_verified = threading.Event()
    _shutdown_event    = threading.Event()
    _live_worker: Optional[_LiveTradingWorker] = None

    # P0-SHUT: Définie avant le try pour être toujours accessible dans les handlers except.
    def _verify_all_stops_on_shutdown(reason: str = "unknown", send_email: bool = True) -> None:
        """C-11: Verifie les stops actifs sur Kraken pour chaque position ouverte.

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
                            "[SHUTDOWN C-11] AUCUN stop-loss actif sur Kraken pour %s "
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
                                        f"ouverts sur Kraken.\n\n"
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

        # Kraken: les frais reels sont obligatoires et resolus par paire apres preflight.
        # Aucun fallback de frais n'est autorise pour le runtime Kraken.
        if EXCHANGE_NAME == 'KRAKEN':
            logger.info("[KRAKEN-FEES] Recuperation des frais API differee apres resolution des paires.")
            real_taker, real_maker = _runtime.taker_fee, _runtime.maker_fee
        else:
            real_taker, real_maker = get_binance_trading_fees(client)
        if abs(real_taker - _runtime.taker_fee) > 1e-6 or abs(real_maker - _runtime.maker_fee) > 1e-6:
            # P2.1 + auto-adoption: si écart > 30%, utiliser les frais réels API
            if real_taker > _runtime.taker_fee * 1.3:
                _old_taker = _runtime.taker_fee
                config.update_live_fees(real_taker, real_maker)
                _exchange_label = exchange_label()
                _fee_cause = (
                    "frais broker superieurs a la configuration"
                    if EXCHANGE_NAME == 'KRAKEN'
                    else "remise BNB expiree ?"
                )
                logger.warning(
                    "[P0-01] Frais taker reels (%.4f%%) superieurs de >30%% aux frais config (%.4f%%) "
                    "-- %s Frais live mis a jour: taker=%.5f maker=%.5f (source: API %s, auto-adopte)",
                    real_taker * 100, _old_taker * 100,
                    _fee_cause, real_taker, real_maker, _exchange_label,
                )
                try:
                    send_email_alert(
                        subject=f"[BOT] Frais {_exchange_label} auto-adoptes",
                        body=(
                            f"Frais taker API {_exchange_label} : {real_taker * 100:.4f}%\n"
                            f"Frais taker config      : {_old_taker * 100:.4f}%\n"
                            f"Écart : +{(real_taker / _old_taker - 1) * 100:.1f}%\n\n"
                            f"Cause probable          : {_fee_cause}\n"
                            f"Les frais live ont été mis à jour automatiquement pour cette session.\n"
                            f"Mettre à jour TAKER_FEE dans .env pour rendre la correction permanente."
                        ),
                    )
                except Exception as _mail_err:
                    logger.warning("[P0-01] Email frais non envoyé: %s", _mail_err)
            else:
                logger.info(
                    "[P0-01] Frais live utilisés: taker=%.5f maker=%.5f (source: config; API %s=%.5f/%.5f, écart mineur)",
                    _runtime.taker_fee, _runtime.maker_fee,
                    exchange_label(), real_taker, real_maker,
                )
        elif EXCHANGE_NAME != 'KRAKEN':
            logger.info(
                "[P0-01] Frais live utilisés: taker=%.5f maker=%.5f (source: config, alignes API %s)",
                _runtime.taker_fee, _runtime.maker_fee,
                exchange_label(),
            )
        _apply_kraken_fee_parity_to_config()

        # Chargement de l'etat du bot
        load_bot_state()

        # Legacy global thresholds are intentionally ignored. Strategy v2 is per-pair.
        with _bot_state_lock:
            _saved_stoch = bot_state.get('stoch_params')
        if isinstance(_saved_stoch, dict):
            logger.info("[STRATEGY-MIGRATION] stoch_params global détecté; migration per-pair différée après réconciliation.")

        # D-06: persister starting_equity pour le dashboard (daily loss limit)
        # Placeholder — recalculée après réconciliation avec l'equity réelle
        with _bot_state_lock:
            _tracker = bot_state.setdefault('_daily_pnl_tracker', {})
            if 'starting_equity' not in _tracker:
                _tracker['starting_equity'] = config.initial_wallet

        # STATE-PURGE: supprimer les pair_states stale (paires retirées de la config)
        _active_pairs_set = {p['backtest_pair'] for p in crypto_pairs}
        _known_global = {'emergency_halt', 'emergency_halt_reason', '_daily_pnl_tracker',
                         '_state_version', 'reconcile_failed', 'reconcile_failed_reason',
                         'kraken_preflight', 'kraken_private_api_ok', 'stoch_params'}
        with _bot_state_lock:
            _stale_keys = [
                k for k, v in list(bot_state.items())
                if isinstance(v, dict) and k not in _known_global and k not in _active_pairs_set
            ]
            for _stale_key in _stale_keys:
                del bot_state[_stale_key]
                logger.info("[STATE-PURGE] Pair_state stale supprimé: %s (paire non configurée)", _stale_key)
        if _stale_keys:
            save_bot_state(force=True)

        crypto_pairs = _validate_broker_pairs(crypto_pairs)
        crypto_pairs = _require_kraken_real_fees(crypto_pairs)
        _kraken_private_ready = _run_kraken_private_preflight(crypto_pairs)

        # B-05: WAL replay — détecter les intents BUY sans confirmation avant réconciliation
        try:
            _wal_unconfirmed = wal_replay()
            if _wal_unconfirmed:
                logger.warning(
                    "[WAL] %d paire(s) avec intent non confirme — reconciliation prioritaire: %s",
                    len(_wal_unconfirmed), _wal_unconfirmed,
                )
        except Exception as _wal_err:
            logger.warning("[WAL] Erreur replay WAL au demarrage: %s", _wal_err)

        # C-03: Réconciliation positions au démarrage — détecte les positions orphelines
        # (achat exécuté avant un crash, état non sauvegardé)
        _reconcile_marker_present = _reconcile_required_marker_exists()
        if _reconcile_marker_present:
            logger.critical(
                "[RECONCILE] Marker reconcile_required detecte: reconciliation exchange obligatoire avant achats."
            )

        try:
            if not _kraken_private_ready:
                logger.critical(
                    "[KRAKEN-PREFLIGHT] Reconciliation exchange sautee: API privee Kraken KO. "
                    "Achats bloques; heartbeat/dashboard actifs."
                )
            else:
                reconcile_positions_with_exchange(crypto_pairs)
                with _bot_state_lock:
                    bot_state['reconcile_failed'] = False
                    bot_state.pop('reconcile_failed_reason', None)
                _reconcile_halt_resolved = _clear_resolved_emergency_halt_after_reconcile()
                save_bot_state(force=True)
                if _reconcile_marker_present and _reconcile_halt_resolved:
                    _clear_reconcile_required_marker()
                elif _reconcile_marker_present:
                    logger.critical(
                        "[RECONCILE] Marker reconcile_required conserve: emergency_halt encore actif apres reconcile."
                    )
                # B-05: nettoyer le WAL après réconciliation réussie
                try:
                    wal_clear()
                except Exception as _wal_clear_err:
                    logger.warning("[WAL] Erreur nettoyage WAL post-reconciliation: %s", _wal_clear_err)

                # P2-EQUITY: calculer l'equity réelle au démarrage
                _refresh_starting_equity_if_new_day()
        except Exception as reconcile_err:
            logger.error(
                "[RECONCILE TS-P2-02] Erreur lors de la réconciliation: %s",
                reconcile_err, exc_info=True,
            )
            with _bot_state_lock:
                bot_state['reconcile_failed'] = True
                bot_state['reconcile_failed_reason'] = f"{exchange_label()} reconciliation failed: {reconcile_err}"
            save_bot_state(force=True)
            try:
                send_trading_alert_email(
                    subject="[CRITIQUE TS-P2-02] Réconciliation échouée au démarrage — achats bloqués",
                    body_main=(
                        f"La réconciliation des positions avec {exchange_label()} a échoué au démarrage du bot.\n\n"
                        f"Erreur : {reconcile_err}\n\n"
                        f"Les achats sont bloqués jusqu'à résolution. "
                        f"Les positions existantes et stops restent surveillés.\n\n"
                        f"Supprimez la clé 'reconcile_failed' du bot_state après vérification manuelle."
                    ),
                    client=client,
                )
            except Exception as _mail_err:
                logger.warning("[RECONCILE] Email alerte impossible: %s", _mail_err)

        parser = argparse.ArgumentParser(description='Run backtests and optional sizing mode')
        parser.add_argument(
            '--sizing-mode',
            choices=['baseline', 'risk', 'fixed_notional', 'volatility_parity'],
            default=config.sizing_mode,
            help='Position sizing mode to use for backtests',
        )
        args, unknown = parser.parse_known_args()
        _pair_configs: List[Dict[str, str]] = [dict(item) for item in crypto_pairs]
        _sizing_mode = args.sizing_mode
        if (
            config.bot_mode == 'LIVE'
            and _sizing_mode == 'baseline'
            and not bool(getattr(config, 'allow_baseline_live', False))
        ):
            raise SystemExit(
                "SIZING_MODE=baseline interdit en LIVE Kraken sans ALLOW_BASELINE_LIVE=true"
            )

        _migrate_and_hydrate_strategies(_pair_configs)
        # Le worker Live démarre avant les backtests pour protéger toute position
        # existante, mais ses panneaux restent silencieux pendant le bootstrap.
        _runtime.bootstrap_display_active = True
        _runtime.bootstrap_current_phase = 'protection_worker_start'
        _live_worker = _LiveTradingWorker(
            _pair_configs,
            _sizing_mode,
            _shutdown_event,
            float(config.schedule_interval_minutes) * 60.0,
        )
        _live_worker.start()
        logger.info(
            "[LIVE-WORKER] Surveillance démarrée avant bootstrap — intervalle=%ds, achats bloqués.",
            int(float(config.schedule_interval_minutes) * 60.0),
        )

        logger.info("Script demarre. Planification initiale en cours...")
        # Purge préventive: supprimer toute planification résiduelle
        # Nettoyage renforcé de la planification
        try:
            schedule.clear()
            _runtime.scheduler_ready = False
            _runtime.next_hourly_run_at = None
            logger.info("Planification nettoyee au demarrage (schedule.clear())")
        except Exception as _clear_ex:
            logger.debug(f"Echec nettoyage planification au demarrage: {_clear_ex}")

        # Planification du nettoyage du cache tous les 30 jours
        schedule.every(30).days.do(cleanup_expired_cache)
        logger.info("Nettoyage automatique du cache planifié: tous les 30 jours")

        # P1-08: Resynchronisation timestamp périodique (toutes les 30 min)
        # Le drift horloge locale vs serveur Kraken s'accumule et provoque
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
                    pairs=[p['backtest_pair'] for p in crypto_pairs],
                )
            except Exception as _m_err:
                logger.debug("[METRICS P2-04] Échec export: %s", _m_err)
        schedule.every(5).minutes.do(_periodic_metrics_write)
        logger.info("[METRICS P2-04] Export métriques planifié: toutes les 5 min")

        # D-10: écriture initiale immédiate pour que le dashboard ait les fees dès le démarrage
        _periodic_metrics_write()

        # === NOUVELLE LOGIQUE : backtests optimisés + affichage propre ===
        # Exécuter les backtests avec affichage propre (passer sizing_mode)
        # P1-01: start_date recalculé au moment de l'appel
        with _bot_state_lock:
            _startup_strategy_snapshots = {
                pair['backtest_pair']: dict(bot_state.get(pair['backtest_pair'], {}).get('active_strategy', {}))
                for pair in crypto_pairs
            }
        all_results = run_parallel_backtests(
            crypto_pairs,
            _fresh_start_date(),
            timeframes,
            sizing_mode=args.sizing_mode,
            strategy_snapshots=_startup_strategy_snapshots,
        )

        # === AFFICHAGE PROPRE, SANS CHEVAUCHEMENT ===
        _runtime.bootstrap_current_pair = None
        _runtime.bootstrap_current_phase = 'backtest_display'
        for backtest_pair, data in all_results.items():
            _runtime.bootstrap_current_pair = backtest_pair
            if not data['results']:
                _reason_no_results = "aucun resultat backtest"
                with _bot_state_lock:
                    _set_pair_wf_state(
                        backtest_pair,
                        'data_insufficient',
                        _reason_no_results,
                        entries_ready=False,
                    )
                logger.warning("[STARTUP-WF] WF_BLOCKED_DATA %s — %s", backtest_pair, _reason_no_results)
                logger.info(
                    "[BOOTSTRAP_PAIR_DONE] %s trading=PROTECTION_ONLY reason=%s",
                    backtest_pair,
                    _reason_no_results,
                )
                console.print(f"[red]Aucun résultat pour {backtest_pair}[/red]")
                continue

            # C-07 + C-13 + P2-05: OOS quality gate centralisée
            _pool_loop, _oos_blocked_this_pair = apply_oos_quality_gate(
                data['results'], backtest_pair,
                log_tag="MAIN-LOOP C-03", send_alert=False,
                save_force=True, unblock_on_pass=True,
            )

            display_results_for_pair(
                backtest_pair,
                data['results'],
                start_date_override=_history_start_override(backtest_pair),
            )
            logger.info("[BOOTSTRAP_BACKTEST_DONE] %s", backtest_pair)
            with _bot_state_lock:
                _bt_state = cast(Dict[str, Any], bot_state.setdefault(backtest_pair, _make_default_pair_state()))
                _bt_state['backtest_display_status'] = 'terminated'
                _bt_state['runtime_phase'] = 'wf_validation'

            # === F-BUG2: Walk-Forward validation at startup (like scheduled/main) ===
            _startup_wf_best = None
            _startup_snapshot: Optional[StrategySnapshot] = None
            try:
                _runtime.bootstrap_current_phase = 'wf_optuna'
                from walk_forward import (
                    run_walk_forward_optuna as _run_wf_optuna_startup,
                )
                _wf_dfs_startup = {}
                _startup_start = _fresh_start_date()
                for _tf_s in timeframes:
                    _df_s = prepare_base_dataframe(backtest_pair, _tf_s, _startup_start, 14)
                    _wf_dfs_startup[_tf_s] = _df_s if _df_s is not None and not _df_s.empty else pd.DataFrame()

                logger.info(
                    "[STARTUP STOCH] Seuils optimisés dans les folds IS Optuna; "
                    "grid full-sample exclu du pipeline LIVE."
                )

                # ML-07: Optuna en priorité (espace EMA + scenario continu)
                _wf_res_startup = _run_wf_optuna_startup(
                    base_dataframes=_wf_dfs_startup,
                    scenarios=WF_SCENARIOS,
                    backtest_fn=backtest_from_dataframe,
                    initial_capital=config.initial_wallet,
                    sizing_mode=args.sizing_mode,
                    n_folds=config.oos_min_folds,
                    n_trials=100,
                    required_folds=config.oos_min_folds,
                    progress_pair=backtest_pair,
                    timeout_seconds=getattr(config, 'wf_optuna_timeout_seconds', 1800),
                    progress_trials=getattr(config, 'wf_optuna_progress_trials', 10),
                    progress_seconds=getattr(config, 'wf_optuna_progress_seconds', 60),
                )

                if _wf_res_startup.get('any_passed'):
                    _startup_wf_best = _wf_res_startup['best_wf_config']
                    _startup_snapshot = _snapshot_from_wf(
                        backtest_pair, _startup_wf_best, _wf_dfs_startup
                    )
                    logger.debug(
                        "[STARTUP-DIAG] WF validated candidate: %s EMA(%s,%s) %s — OOS Sharpe=%.2f",
                        _startup_wf_best['scenario'],
                        _startup_wf_best['ema_periods'][0],
                        _startup_wf_best['ema_periods'][1],
                        _startup_wf_best['timeframe'],
                        _startup_wf_best.get('avg_oos_sharpe', 0.0),
                    )
                    logger.info("[STARTUP-WF] WF_VALIDATED %s — publication snapshot requise avant achats.", backtest_pair)
                else:
                    _record_wf_result(
                        backtest_pair,
                        _wf_res_startup,
                        log_tag='STARTUP-WF',
                    )
                    logger.debug("[STARTUP-DIAG] WF: aucune config OOS validée — protection seule.")
            except Exception as _wf_startup_err:
                logger.warning(
                    "[STARTUP-WF] WF_BLOCKED_DATA %s — exception WF startup: %s",
                    backtest_pair,
                    _wf_startup_err,
                )
                with _bot_state_lock:
                    _set_pair_wf_state(
                        backtest_pair,
                        'data_insufficient',
                        f"exception WF startup: {_wf_startup_err}",
                        entries_ready=False,
                    )

            best_result = _select_best_by_calmar(_pool_loop)
            best_profit = best_result['final_wallet'] - best_result['initial_wallet']

            # Backtest table already printed before WF to keep startup order clear.

            # === Exécuter le trading réel avec les meilleurs paramètres ===
            if _startup_wf_best:
                best_params = cast(StrategySnapshot, _startup_snapshot).as_best_params()
            else:
                # I2 startup: Fallback IS champion (max trades, ≥5) — identique à backtest_orchestrator.py
                # IMPORTANT: trades peut être un DataFrame — ne pas faire 'trades or []' (bool ambiguity)
                def _n_trades(r: dict) -> int:
                    t = r.get('trades')
                    return len(t) if t is not None else 0
                _fb_s = sorted(_pool_loop, key=_n_trades, reverse=True)
                _fb_s = _fb_s[0] if _fb_s else None
                _n_fb_s = _n_trades(_fb_s) if _fb_s is not None else 0
                if _fb_s is not None and _n_fb_s >= 5:
                    best_params = {
                        'timeframe': _fb_s['timeframe'],
                        'ema1_period': _fb_s['ema_periods'][0],
                        'ema2_period': _fb_s['ema_periods'][1],
                        'scenario': _fb_s['scenario'],
                    }
                    best_params.update(SCENARIO_DEFAULT_PARAMS.get(_fb_s['scenario'], {}))
                    logger.debug(
                        "[STARTUP-DIAG] Champion IS conservé en protection seule (non tradable): %s %s EMA(%d/%d) — %d trades IS.",
                        _fb_s['scenario'], _fb_s['timeframe'],
                        _fb_s['ema_periods'][0], _fb_s['ema_periods'][1],
                        _n_fb_s,
                    )
                else:
                    # Aucun IS champion viable — defaults conservatifs (dernier recours)
                    best_params = {
                        'timeframe': '1d',
                        'ema1_period': 26,
                        'ema2_period': 50,
                        'scenario': 'StochRSI',
                    }
                    best_params.update(SCENARIO_DEFAULT_PARAMS.get('StochRSI', {}))
                    logger.debug(
                        "[STARTUP-DIAG] Aucun WF valide ni IS champion viable — protection seule: StochRSI EMA(26/50) 1d non tradable.",
                    )
                    # Email alert uniquement sur fallback conservatif (config non validée OOS)
                    try:
                        send_email_alert(
                            subject=f"[{backtest_pair}] Protection seule — fallback non tradable",
                            body=(
                                f"Paire : {backtest_pair}\n"
                                f"Aucune configuration OOS validée lors du démarrage.\n"
                                f"Protection seule : StochRSI EMA(26/50) 1d (defaults conservatifs, non tradable).\n\n"
                                f"Action recommandée : vérifier les OOS gates (walk_forward.py)."
                            ),
                        )
                    except Exception as _mail_err:
                        logger.warning("[STARTUP-DIAG] Email alert non envoyé: %s", _mail_err)

            # Initialiser l'état du bot pour cette paire
            if backtest_pair not in bot_state:
                with _bot_state_lock:
                    if backtest_pair not in bot_state:
                        bot_state[backtest_pair] = _make_default_pair_state()

            pair_state: PairState = cast('PairState', bot_state[backtest_pair])

            if _startup_snapshot is not None:
                if not _publish_strategy_snapshot(_startup_snapshot):
                    with _bot_state_lock:
                        _runtime.entries_ready[backtest_pair] = False
                    _startup_wf_best = None
                else:
                    _run_strategy_snapshot_backtest(
                        _startup_snapshot, _wf_dfs_startup, args.sizing_mode
                    )

            # OOS-STREAK (startup): track WF OOS result — source la plus fiable.
            _OOS_FAIL_STREAK_N = 6
            if _startup_wf_best is None:
                _oos_streak_val = _runtime.oos_fail_streaks.get(backtest_pair, 0) + 1
                _runtime.oos_fail_streaks[backtest_pair] = _oos_streak_val
                if _oos_streak_val >= _OOS_FAIL_STREAK_N:
                    logger.critical(
                        '[OOS-STREAK] %s — %d cycles WF consécutifs sans config OOS valide. '
                        'oos_blocked forcé en dur.',
                        backtest_pair, _oos_streak_val,
                    )
                    with _bot_state_lock:
                        pair_state['oos_blocked'] = True
                        if 'oos_blocked_since' not in cast(Dict[str, Any], pair_state):
                            cast(Dict[str, Any], pair_state)['oos_blocked_since'] = time.time()
                    save_bot_state(force=True)
            else:
                _prev_streak_val = _runtime.oos_fail_streaks.get(backtest_pair, 0)
                if _prev_streak_val > 0:
                    logger.info(
                        '[OOS-STREAK] %s — WF OOS repassé. Streak %d réinitialisé.',
                        backtest_pair, _prev_streak_val,
                    )
                _runtime.oos_fail_streaks[backtest_pair] = 0

            logger.info(
                "[STARTUP] %s bootstrap terminé; exécution des ordres laissée au worker Live.",
                backtest_pair,
            )

            # === AFFICHAGE DATE/HEURE ET PLANIFICATION ===
            current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Afficher le panel de suivi (avant la mise à jour last_run_time — P4.2: fix "Temps écoulé 0:00:00")
            _attach_schedule_status(cast(Dict[str, Any], pair_state), current_run_time)
            _panel_state = _build_kraken_panel_state(
                backtest_pair,
                cast(Mapping[str, Any], pair_state),
            )
            console.print(build_tracking_panel(_panel_state, current_run_time))
            console.print("\n")

            # P4.2: mettre à jour last_run_time APRES le panel pour que le prochain cycle
            # affiche le vrai delta écoulé (current_run_time - last_run_time_cycle_précédent)
            pair_state['last_run_time'] = current_run_time
            pair_state['last_execution'] = current_run_time  # D-10: dashboard Last Cycle
            pair_state['last_best_params'] = best_params
            pair_state['execution_count'] = pair_state.get('execution_count', 0) + 1

            save_bot_state(force=True)
            _pair_mode = describe_live_execution_mode(
                backtest_pair,
                cast(Mapping[str, Any], pair_state),
                cast(Mapping[str, Any], best_params),
            )
            logger.info(
                "[BOOTSTRAP_PAIR_DONE] %s trading=%s reason=%s",
                backtest_pair,
                _pair_mode.get('mode', 'PROTECTION_ONLY'),
                _pair_mode.get('reason', ''),
            )
            _runtime.bootstrap_current_phase = 'pair_done'

        _bootstrap_tradable: List[str] = []
        _bootstrap_protection: List[str] = []
        for _pair_cfg in crypto_pairs:
            _bp = _pair_cfg['backtest_pair']
            with _bot_state_lock:
                _state_snapshot = dict(bot_state.get(_bp, {}))
                _params_snapshot = dict(
                    _runtime.live_best_params.get(
                        _bp,
                        cast(Dict[str, Any], _state_snapshot.get('last_best_params', {})),
                    )
                )
            _mode_snapshot = describe_live_execution_mode(_bp, _state_snapshot, _params_snapshot)
            if _mode_snapshot.get('mode') == 'TRADABLE':
                _bootstrap_tradable.append(_bp)
            else:
                _bootstrap_protection.append(_bp)
        logger.info(
            "[BOOTSTRAP_COMPLETE] pairs=%d tradable=%d protection_only=%d tradable_pairs=%s protection_only_pairs=%s",
            len(crypto_pairs),
            len(_bootstrap_tradable),
            len(_bootstrap_protection),
            ",".join(_bootstrap_tradable) or "-",
            ",".join(_bootstrap_protection) or "-",
        )

        _runtime.bootstrap_current_pair = None
        _runtime.bootstrap_current_phase = 'complete'
        _runtime.bootstrap_display_active = False

        def _dispatch_scheduled_parallel() -> None:
            """Dispatch le backtest + WF + trading de toutes les paires en parallèle (toutes les 60 min)."""
            n_pairs = len(_pair_configs)
            if n_pairs == 0:
                return
            if n_pairs == 1:
                pc = _pair_configs[0]
                bp = pc['backtest_pair']
                p0 = _read_live_params(bp, {})
                tf = p0.get('timeframe', '4h')
                execute_scheduled_trading(pc['real_pair'], tf, p0, bp, _sizing_mode)
                return
            with ThreadPoolExecutor(max_workers=n_pairs, thread_name_prefix='sched') as pool:
                futures = {}
                for pc in _pair_configs:
                    bp = pc['backtest_pair']
                    p0 = _read_live_params(bp, {})
                    tf = p0.get('timeframe', '4h')
                    f = pool.submit(
                        execute_scheduled_trading,
                        pc['real_pair'], tf, p0, bp, _sizing_mode,
                    )
                    futures[f] = bp
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception as _e:
                        logger.error("[PARALLEL] %s scheduled error: %s", futures[f], _e)

        # ── Tâche groupée 1 : backtest + WF + trading → aligné sur la bougie H:00:30 UTC ──
        # A-02: threading.Timer ciblant H:00:30 UTC (30s après la clôture de la bougie horaire)
        # Évite le drift de schedule.every(60).minutes qui se décale de la clôture réelle.
        def _schedule_next_hourly_cycle() -> None:
            import datetime as _dt
            _now_utc = _dt.datetime.now(_dt.timezone.utc)
            _next = _now_utc.replace(minute=0, second=30, microsecond=0)
            if _next <= _now_utc:
                _next += _dt.timedelta(hours=1)
            _delay_s = (_next - _now_utc).total_seconds()
            _runtime.next_hourly_run_at = _next.astimezone()
            _runtime.scheduler_ready = True

            def _run_and_reschedule() -> None:
                try:
                    _dispatch_scheduled_parallel()
                except Exception as _sched_err:
                    logger.error("[A-02] Erreur cycle horaire: %s", _sched_err)
                _schedule_next_hourly_cycle()

            _t = threading.Timer(_delay_s, _run_and_reschedule)
            _t.daemon = True
            _t.start()
            logger.info(
                "[A-02] Prochain cycle backtest+WF+trading planifié à %s UTC (dans %.0fs)",
                _next.strftime("%H:%M:%S"), _delay_s,
            )

        _schedule_next_hourly_cycle()
        logger.info(
            "[PARALLEL] %d paire(s) configurée(s) — exécution parallèle activée",
            len(_pair_configs),
        )
        logger.info(f"Tâches planifiées actives: {len(schedule.jobs)}")

        # === BOUCLE PRINCIPALE ===
        # C-04: Handler SIGTERM/SIGINT pour graceful shutdown (taskkill, Ctrl+C)
        # P3-01: remplacement des closures fragiles par threading.Event
        def _graceful_shutdown(signum: int, _frame: Any) -> None:
            import signal as _signal  # pylint: disable=reimported
            try:
                signal_name = _signal.Signals(signum).name
            except ValueError:
                signal_name = "UNKNOWN"
            if signum == _signal.SIGINT:
                _voluntary_event.set()
                logger.info(
                    "[SHUTDOWN] Signal %s (%s) reçu — arrêt volontaire demandé",
                    signum, signal_name,
                )
            elif signum == _signal.SIGTERM:
                logger.warning(
                    "[SHUTDOWN] Signal %s (%s) reçu — arrêt demandé",
                    signum, signal_name,
                )
            else:
                logger.critical(
                    "[SHUTDOWN] Signal %s (%s) reçu — arrêt demandé",
                    signum, signal_name,
                )
            _shutdown_event.set()  # main-loop va sortir naturellement

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

        display_bot_active_banner(
            len(schedule.jobs),
            _runtime.next_live_run_at,
            console,
            next_hourly_run=_runtime.next_hourly_run_at,
            schedule_status=_get_schedule_status(),
        )

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
        if bot_state.get('emergency_halt'):
            logger.critical(
                "[EMERGENCY HALT] Runtime Kraken en halt actif - %s",
                bot_state.get('emergency_halt_reason', 'reconciliation manuelle requise'),
            )

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
                    schedule_status = _get_schedule_status(now.strftime("%Y-%m-%d %H:%M:%S"))
                    next_run = schedule_status['next_live_run_at']
                    runtime_mode = "EMERGENCY_HALT" if bot_state.get('emergency_halt') else "RUNNING"
                    runtime_detail = (
                        "achats bloques - reconciliation requise"
                        if runtime_mode == "EMERGENCY_HALT"
                        else "running en cours"
                    )
                    if next_run:
                        delta = next_run - now
                        seconds_left = max(0, int(delta.total_seconds()))
                        minutes_left = 0 if seconds_left == 0 else max(1, (seconds_left + 59) // 60)
                        console.print(f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif ({runtime_mode}) | Prochaine execution dans {minutes_left} min ({next_run.strftime('%H:%M:%S')})")
                    else:
                        console.print(
                            f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif ({runtime_mode}) | "
                            f"{schedule_status['display_next_live']}"
                        )

                    running_counter += 1
                    if running_counter % 1 == 0:  # Toutes les 10 minutes (600s sleep)
                        console.print(f"[{runtime_mode}] {now.strftime('%H:%M:%S')} - {runtime_detail}")

                    # Écriture du heartbeat (Phase 2 — watchdog support)
                    try:
                        # Rafraichir le solde cash USD/USDC a chaque cycle pour le dashboard
                        try:
                            _acc = client.get_account()
                            _last_usdc_balance = _usd_equivalent_cash_balance(_acc)
                        except Exception:
                            pass  # solde conservé depuis le dernier fetch réussi
                        heartbeat = {
                            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "broker": EXCHANGE_NAME.lower(),
                            "pid": os.getpid(),
                            "circuit_mode": error_handler.circuit_breaker.mode.value,
                            "error_count": len(error_handler.error_history),
                            "loop_counter": running_counter,
                            "usdc_balance": _last_usdc_balance,
                        }
                        hb_filename = "heartbeat_kraken.json" if EXCHANGE_NAME == "KRAKEN" else "heartbeat.json"
                        hb_path = os.path.join(os.path.dirname(__file__), "states", hb_filename)
                        _write_runtime_heartbeat(hb_path, heartbeat)
                        if _heartbeat_write_failures:
                            logger.info(
                                "[HEARTBEAT] Ecriture retablie apres %d echec(s).",
                                _heartbeat_write_failures,
                            )
                            _heartbeat_write_failures = 0

                    except Exception as hb_err:
                        _heartbeat_write_failures += 1
                        if _heartbeat_write_failures >= 3:
                            logger.error(
                                "[HEARTBEAT] Erreur ecriture persistante (%d echecs): %s",
                                _heartbeat_write_failures,
                                hb_err,
                            )
                        else:
                            logger.warning(
                                "[HEARTBEAT] Ecriture reportee (%d/%d): %s",
                                _heartbeat_write_failures,
                                3,
                                hb_err,
                            )

                    # Attente réactive en tranches de 1s — CTRL+C répond dans la seconde
                    # sur Windows (Event.wait(120) bloque jusqu'à 2 min avant de vérifier)
                    _t0 = time.monotonic()
                    while not _shutdown_event.wait(1) and time.monotonic() - _t0 < 120:
                        pass

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
            if _live_worker is not None:
                _live_worker.stop()
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
            _shutdown_event.set()
            if _live_worker is not None:
                _live_worker.stop()
            save_bot_state(force=True)
            if not _shutdown_verified.is_set():
                _shutdown_verified.set()
                _verify_all_stops_on_shutdown(reason="KeyboardInterrupt (inner)", send_email=False)
        except Exception as e:
            error_msg = f"Erreur inattendue au démarrage : {e}"
            logger.error(error_msg)
            _shutdown_event.set()
            if _live_worker is not None:
                _live_worker.stop()
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
        _shutdown_event.set()
        if _live_worker is not None:
            _live_worker.stop()
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
        _shutdown_event.set()
        if _live_worker is not None:
            _live_worker.stop()
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
