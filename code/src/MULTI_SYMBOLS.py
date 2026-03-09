# ─── Standard-library & third-party imports ─────────────────────────────────
import argparse
import json
import locale
import logging
import os
import random
import schedule
import signal
import sys
import threading
import time
import traceback
import warnings
from binance.client import Client
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from rich.console import Console
from rich.panel import Panel
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict, Union, cast

import numpy as np
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
from position_sizing import (
    compute_position_size_by_risk,
    compute_position_size_fixed_notional,
    compute_position_size_volatility_parity,
)
from exceptions import SizingError                     # P0-05
from email_utils import send_email_alert, send_trading_alert_email
from state_manager import save_state, load_state
from display_ui import (
    PANEL_WIDTH,
    display_buy_signal_panel, display_sell_signal_panel,
    display_account_balances_panel, display_market_changes,
    display_results_for_pair, display_backtest_table,
    build_tracking_panel,
    display_closure_panel, display_execution_header,
    display_bot_active_banner,
)
from cache_manager import cleanup_expired_cache
from exchange_client import (
    BinanceFinalClient, is_valid_stop_loss_order, can_execute_partial_safely,
    place_stop_loss_order as _place_stop_loss_order,
    place_exchange_stop_loss as _place_exchange_stop_loss,  # P0-01
    safe_market_buy as _safe_market_buy,
    safe_market_sell as _safe_market_sell,
    get_symbol_filters as _get_symbol_filters_impl,
)
from email_templates import (
    buy_executed_email, sell_executed_email,
    api_connection_failure_email, data_retrieval_error_email,
    network_error_email, indicator_error_email,
    trading_execution_error_email, trading_pair_error_email,
    critical_startup_error_email,
    generic_exception_email,
)

# is_valid_stop_loss_order, can_execute_partial_safely importés depuis exchange_client.py (Phase 4)

# ErrorHandler réel importé depuis error_handler.py (remplace le DummyErrorHandler)
from error_handler import initialize_error_handler

# Modules dormants activés (Phase 2)
from trade_journal import log_trade

try:
    # Forcer la console Windows en UTF-8 (code page 65001)
    if os.name == "nt":
        os.system("chcp 65001 >NUL")
        locale.setlocale(locale.LC_ALL, '')
except Exception:
    pass

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log', encoding='utf-8'),
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
    in_position: bool                       # legacy — lecture seule
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


def compute_stochrsi(rsi_series: pd.Series, period: int = 14) -> pd.Series:
    """Stochastic RSI : (RSI - min) / (max - min) sur une fenêtre glissante.

    Retourne NaN pour les period-1 premières valeurs.
    Retourne 0.5 lorsque la plage est nulle (RSI plat).
    Valeurs dans [0, 1].
    """
    vals = rsi_series.to_numpy(dtype=float, na_value=np.nan)
    result = np.full(len(vals), np.nan)
    for i in range(period - 1, len(vals)):
        window = vals[i - period + 1: i + 1]
        lo = float(np.min(window))
        hi = float(np.max(window))
        rng = hi - lo
        result[i] = 0.5 if rng == 0.0 else (vals[i] - lo) / rng
    return pd.Series(result, index=rsi_series.index)


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


def _get_coin_balance(account_info: Dict[str, Any], asset: str) -> Tuple[bool, float, float, float]:
    """Retourne (found, free, locked, total) pour un asset depuis account_info.

    C-13: helper centralisé — élimine la duplication free+locked.
    found:  True si l'asset existe dans les balances.
    free / locked / total : floats (0.0 si not found).
    """
    bal = next(
        (b for b in account_info.get('balances', []) if b['asset'] == asset),
        None,
    )
    if bal is None:
        return False, 0.0, 0.0, 0.0
    free = float(bal.get('free', 0))
    locked = float(bal.get('locked', 0))
    return True, free, locked, free + locked

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

def place_stop_loss_order(symbol: str, quantity: Union[float, str], stop_price: float, client_id: Optional[str] = None) -> Dict[str, Any]:
    return _place_stop_loss_order(client, symbol, float(quantity), stop_price, client_id, send_alert=send_trading_alert_email)

def place_exchange_stop_loss_order(symbol: str, quantity: str, stop_price: float) -> Dict[str, Any]:  # P0-01 wrapper
    """Place un STOP_LOSS (market trigger) exchange immédiatement après un achat (C-02)."""
    return _place_exchange_stop_loss(client, symbol, quantity, stop_price, send_alert=send_trading_alert_email)

def safe_market_buy(symbol: str, quoteOrderQty: float, max_retries: int = 4) -> Dict[str, Any]:
    return _safe_market_buy(client, symbol, quoteOrderQty, max_retries, send_alert=send_trading_alert_email)

def safe_market_sell(symbol: str, quantity: Union[float, str], max_retries: int = 4) -> Dict[str, Any]:
    return _safe_market_sell(client, symbol, quantity, max_retries, send_alert=send_trading_alert_email)


def _cancel_exchange_sl(ctx: '_TradeCtx') -> None:
    """F-1: Annule l'ordre SL exchange avant vente signal/partielle pour libérer les coins lockés.

    Après annulation, rafraîchit coin_balance_free depuis l'API pour refléter le unlock.
    """
    ps = ctx.pair_state
    sl_order_id = ps.get('sl_order_id')
    if not sl_order_id:
        return
    try:
        client.cancel_order(symbol=ctx.real_trading_pair, orderId=sl_order_id)
        logger.info(
            "[SL-CANCEL F-1] Ordre SL exchange annulé (orderId=%s) avant vente signal/partielle",
            sl_order_id,
        )
        ps['sl_order_id'] = None
        ps['sl_exchange_placed'] = False
        save_bot_state()
        # Rafraîchir les balances après annulation
        account_info = client.get_account()
        _found, coin_free, coin_locked, coin_total = _get_coin_balance(account_info, ctx.coin_symbol)
        ctx.coin_balance_free = coin_free
        ctx.coin_balance_locked = coin_locked
        ctx.coin_balance = coin_total
    except Exception as e:
        logger.warning("[SL-CANCEL F-1] Échec annulation SL (orderId=%s): %s", sl_order_id, e)
        # P1: l'échec d'annulation SL garde les coins verrouillés → la vente signal risque d'échouer
        try:
            send_trading_alert_email(
                subject=f"[ALERTE P1] Échec annulation SL exchange — {ctx.real_trading_pair}",
                body_main=(
                    f"L'annulation de l'ordre stop-loss exchange a échoué.\n\n"
                    f"Paire : {ctx.real_trading_pair}\n"
                    f"orderId : {sl_order_id}\n"
                    f"Erreur : {e}\n\n"
                    f"Les coins peuvent rester verrouillés → vente signal potentiellement bloquée.\n"
                    f"Vérifier les ordres ouverts sur Binance."
                ),
                client=client,
            )
        except Exception as _e:
            logger.warning("[SL-CANCEL] Email alerte impossible: %s", _e)

# --- Utility Functions (delegated to timestamp_utils.py — P3-SRP) ---
def full_timestamp_resync() -> None:
    _full_timestamp_resync(client)

def validate_api_connection() -> bool:
    return _validate_api_connection(client, send_trading_alert_email, api_connection_failure_email)

def init_timestamp_solution() -> bool:
    return _init_timestamp_solution(client)

# get_cache_key importé depuis cache_manager.py (Phase 4)

# --- Save throttle: évite les écritures disque excessives (max 1x / 5s) ---
_last_save_time: float = 0.0
_SAVE_THROTTLE_SECONDS: float = 5.0
_save_failure_count: int = 0  # P0-SAVE: compteur d'échecs consécutifs
_MAX_SAVE_FAILURES: int = 3   # P0-SAVE: seuil pour emergency halt

def save_bot_state(force: bool = False) -> None:
    """Sauvegarde l'etat du bot (wrapper vers state_manager).

    P0-SAVE: les erreurs de sauvegarde ne sont plus avalées silencieusement.
    Après _MAX_SAVE_FAILURES échecs consécutifs, le kill-switch est activé.
    Throttled à 1 écriture / 5s sauf si force=True (arrêt, crash).
    Thread-safe via _bot_state_lock (C-01).
    """
    global _last_save_time, _save_failure_count
    now = time.time()
    with _bot_state_lock:
        if not force and (now - _last_save_time) < _SAVE_THROTTLE_SECONDS:
            return
        try:
            save_state(bot_state)
            _last_save_time = now
            if _save_failure_count > 0:
                logger.info("[SAVE P0-SAVE] Sauvegarde réussie après %d échec(s) consécutif(s)", _save_failure_count)
            _save_failure_count = 0
        except Exception as save_err:
            _save_failure_count += 1
            logger.critical(
                "[SAVE P0-SAVE] ÉCHEC sauvegarde état (%d/%d): %s",
                _save_failure_count, _MAX_SAVE_FAILURES, save_err,
            )
            # Ne PAS mettre à jour _last_save_time → force le retry au prochain appel
            try:
                send_trading_alert_email(
                    subject=f"[CRITIQUE P0-SAVE] Échec sauvegarde état ({_save_failure_count}/{_MAX_SAVE_FAILURES})",
                    body_main=(
                        f"La sauvegarde de l'état du bot a échoué.\n\n"
                        f"Erreur: {save_err}\n"
                        f"Échecs consécutifs: {_save_failure_count}/{_MAX_SAVE_FAILURES}\n\n"
                        f"{'EMERGENCY HALT sera activé au prochain échec!' if _save_failure_count >= _MAX_SAVE_FAILURES - 1 else 'Le bot continue mais l état n est pas persisté.'}"
                    ),
                    client=client,
                )
            except Exception as _e:
                logger.warning("[SAVE] Email alerte sauvegarde impossible: %s", _e)
            if _save_failure_count >= _MAX_SAVE_FAILURES:
                bot_state['emergency_halt'] = True
                bot_state['emergency_halt_reason'] = (
                    f"{_MAX_SAVE_FAILURES} échecs consécutifs de sauvegarde à {datetime.now().isoformat()}"
                )
                logger.critical(
                    "[SAVE P0-SAVE] EMERGENCY HALT activé — %d échecs consécutifs de sauvegarde",
                    _save_failure_count,
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
    """Retourne True si la perte journali\u00e8re d\u00e9passe daily_loss_limit_pct \u00d7 initial_wallet."""
    today = _get_today_iso()
    with _bot_state_lock:
        tracker = bot_state.get('_daily_pnl_tracker', {})
        total_pnl = tracker.get(today, {}).get('total_pnl', 0.0)
    limit_usdc = config.initial_wallet * config.daily_loss_limit_pct
    if total_pnl < -limit_usdc:
        logger.warning(
            "[DAILY-LIMIT P5-A] Perte journali\u00e8re %.2f USDC \u2265 limite \u2212%.2f USDC "
            "(%.1f %% de %.0f USDC initial). Achats bloqu\u00e9s jusqu'\u00e0 00:00 UTC.",
            abs(total_pnl), limit_usdc,
            config.daily_loss_limit_pct * 100, config.initial_wallet,
        )
        return True
    return False


def reconcile_positions_with_exchange(crypto_pairs_list: List[Dict[str, Any]]) -> None:
    """Vérifie la cohérence entre bot_state et les positions réelles sur Binance.

    Appelé UNE FOIS au démarrage après load_bot_state() pour détecter toute
    position orpheline (ex: achat exécuté avant un crash, état non sauvegardé).
    En cas de divergence, restaure l'état minimal et envoie une alerte CRITICAL. (C-03)
    """
    logger.info("[RECONCILE] Vérification de la cohérence des positions...")
    for pair_info in crypto_pairs_list:
        backtest_pair = pair_info.get('backtest_pair', '')
        real_pair = pair_info.get('real_pair', '')
        try:
            coin_symbol, _ = extract_coin_from_pair(real_pair)
        except Exception as e:
            logger.error(f"[RECONCILE] Impossible d'extraire coin/quote pour {real_pair}: {e}")
            continue
        try:
            account_info = client.get_account()
            # C-13: helper centralisé free+locked
            _, _, _, coin_balance = _get_coin_balance(account_info, coin_symbol)
        except Exception as e:
            logger.error(f"[RECONCILE] Impossible de récupérer le solde Binance pour {coin_symbol}: {e}")
            continue
        pair_state: PairState = cast('PairState', bot_state.get(backtest_pair, {}))
        local_in_position = (
            pair_state.get('in_position', False)
            or pair_state.get('last_order_side') == 'BUY'
        )

        # Récupérer le prix courant pour évaluer la valeur du solde en USDC
        try:
            _ticker = client.get_symbol_ticker(symbol=real_pair)
            _current_price = float(_ticker.get('price', 0))
        except Exception:
            _current_price = 0.0

        # Récupérer min_qty et min_notional pour cette paire
        _min_qty_reconcile = 0.01  # fallback
        _min_notional_reconcile = 5.0  # fallback
        try:
            _exchange_info_r = get_cached_exchange_info(client)
            _sym_info_r = next(
                (s for s in _exchange_info_r['symbols']  # pylint: disable=unsubscriptable-object
                 if s['symbol'] == real_pair), None
            )
            if _sym_info_r:
                _lot_f = next((f for f in _sym_info_r['filters'] if f['filterType'] == 'LOT_SIZE'), None)
                if _lot_f:
                    _min_qty_reconcile = float(_lot_f.get('minQty', 0.01))
                _not_f = next((f for f in _sym_info_r['filters'] if f['filterType'] == 'NOTIONAL'), None)
                if _not_f:
                    _min_notional_reconcile = float(_not_f.get('minNotional', 5.0))
        except Exception:
            pass

        # Position réelle : doit avoir assez de coins ET une valeur au-dessus de MIN_NOTIONAL
        _balance_value_usdc = coin_balance * _current_price if _current_price > 0 else 0.0
        has_real_balance = (
            coin_balance >= _min_qty_reconcile
            and _balance_value_usdc >= _min_notional_reconcile
        )

        if has_real_balance and not local_in_position:
            logger.critical(
                f"[RECONCILE] POSITION ORPHELINE pour {backtest_pair}: "
                f"solde réel {coin_balance:.6f} {coin_symbol} NON enregistré dans bot_state!"
            )
            # Tenter de retrouver entry_price depuis l'historique Binance
            entry_price_restored = None
            try:
                all_orders = client.get_all_orders(symbol=real_pair, limit=50)
                filled_buys = [
                    o for o in all_orders
                    if o.get('side') == 'BUY' and o.get('status') == 'FILLED'
                ]
                if filled_buys:
                    last_buy = filled_buys[-1]
                    exec_qty = float(last_buy.get('executedQty', 0) or 1)
                    cum_quote = float(last_buy.get('cummulativeQuoteQty', 0) or 0)
                    price_field = float(last_buy.get('price', 0) or 0)
                    entry_price_restored = price_field if price_field > 0 else (cum_quote / exec_qty if exec_qty > 0 else None)
                    logger.info(f"[RECONCILE] entry_price restauré: {entry_price_restored}")
            except Exception as e:
                logger.error(f"[RECONCILE] Impossible de récupérer l'historique d'ordres: {e}")
            # Restaurer l'état minimal
            with _bot_state_lock:
                bot_state.setdefault(backtest_pair, {})
                bot_state[backtest_pair]['last_order_side'] = 'BUY'
                bot_state[backtest_pair]['partial_taken_1'] = False
                bot_state[backtest_pair]['partial_taken_2'] = False
                if entry_price_restored:
                    bot_state[backtest_pair]['entry_price'] = entry_price_restored
            save_bot_state(force=True)
            # Alerte immédiate
            try:
                send_trading_alert_email(
                    subject=f"[CRITIQUE] Position orpheline détectée au démarrage: {backtest_pair}",
                    body_main=(
                        f"Position ouverte Binance ({coin_balance:.6f} {coin_symbol}) "
                        f"non enregistrée dans bot_state.\n\n"
                        f"entry_price restauré: {entry_price_restored}\n\n"
                        f"ACTION REQUISE: vérifier les stops manuellement sur Binance."
                    ),
                    client=client,
                )
            except Exception as mail_err:
                logger.error(f"[RECONCILE] Envoi email critique impossible: {mail_err}")

        elif not has_real_balance and local_in_position:
            logger.warning(
                f"[RECONCILE] bot_state indique position ouverte pour {backtest_pair} "
                f"mais solde {coin_symbol} est ~0 (balance={coin_balance:.8f}, "
                f"valeur={_balance_value_usdc:.2f} USDC) — réconciliation complète."
            )

            # Vérifier si le SL exchange a été FILLED (cause la plus probable)
            _sl_oid = pair_state.get('sl_order_id')
            _sl_fill_price = _current_price  # fallback
            _sl_exec_qty = 0.0
            _sl_was_filled = False
            if _sl_oid:
                try:
                    _sl_info = client.get_order(symbol=real_pair, orderId=_sl_oid)
                    if _sl_info.get('status') == 'FILLED':
                        _sl_was_filled = True
                        _eq = float(_sl_info.get('executedQty', 0))
                        _cq = float(_sl_info.get('cummulativeQuoteQty', 0))
                        if _eq > 0:
                            _sl_exec_qty = _eq
                        if _eq > 0 and _cq > 0:
                            _sl_fill_price = _cq / _eq
                        logger.info(
                            "[RECONCILE] SL exchange %s FILLED — prix %.4f, qty %.8f",
                            _sl_oid, _sl_fill_price, _sl_exec_qty,
                        )
                except Exception as _sl_err:
                    logger.warning("[RECONCILE] Impossible de vérifier SL %s: %s", _sl_oid, _sl_err)
            else:
                # Pas de sl_order_id → on ne peut pas confirmer un SL fill
                _sl_was_filled = False
                logger.info("[RECONCILE] Aucun sl_order_id pour %s — reset état sans email", backtest_pair)

            # Email d'alerte si SL a été filled
            if _sl_was_filled:
                _entry_px = pair_state.get('entry_price') or 0
                _pnl_pct = ((_sl_fill_price / _entry_px) - 1) * 100 if _entry_px > 0 else None
                _stop_loss_at = pair_state.get('stop_loss_at_entry')
                _is_ts = pair_state.get('trailing_stop_activated', False)
                _ts = pair_state.get('trailing_stop')
                if _is_ts and _ts:
                    _stop_type = "TRAILING-STOP (dynamique)"
                    _stop_desc = (
                        f"Prix max atteint : {pair_state.get('max_price', 0):.4f} USDC\n"
                        f"Trailing stop : {_ts:.4f} USDC"
                    )
                else:
                    _stop_type = "STOP-LOSS (fixe à 3×ATR)"
                    _stop_desc = f"Stop-loss fixe : {_stop_loss_at:.4f} USDC" if _stop_loss_at else "N/A"

                extra = (
                    f"DETAILS DU STOP (ordre exchange natif — détecté au redémarrage):\n"
                    f"{_stop_desc}\n"
                    f"Prix d'entree : {_entry_px:.4f} USDC\n"
                    f"Timeframe : {pair_state.get('entry_timeframe', 'N/A')}\n"
                    f"EMA : {pair_state.get('entry_ema1', '?')}/{pair_state.get('entry_ema2', '?')}\n"
                    f"Scenario : {pair_state.get('entry_scenario', 'N/A')}"
                )
                subj, body = sell_executed_email(
                    pair=real_pair, qty=_sl_exec_qty,
                    price=_sl_fill_price,
                    usdc_received=_sl_exec_qty * _sl_fill_price,
                    sell_reason=_stop_type, pnl_pct=_pnl_pct,
                    extra_details=extra,
                )
                try:
                    send_trading_alert_email(subject=subj, body_main=body, client=client)
                    logger.info("[RECONCILE] Email SL exchange envoyé pour %s", backtest_pair)
                except Exception as _email_err:
                    logger.error("[RECONCILE] Échec envoi email SL: %s", _email_err)

                # Journal de trading
                try:
                    _saved_entry = pair_state.get('entry_price') or 0.0
                    _pnl = (_sl_fill_price - _saved_entry) * _sl_exec_qty if _saved_entry else None
                    _pnl_pct_j = ((_sl_fill_price / _saved_entry) - 1) if _saved_entry else None
                    logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                    log_trade(
                        logs_dir=logs_dir, pair=real_pair, side='sell',
                        quantity=_sl_exec_qty, price=_sl_fill_price,
                        fee=_sl_exec_qty * config.taker_fee * _sl_fill_price,
                        scenario=pair_state.get('entry_scenario') or '',
                        timeframe=pair_state.get('entry_timeframe') or '',
                        pnl=_pnl, pnl_pct=_pnl_pct_j,
                        extra={'sell_reason': f'{_stop_type} (reconcile at startup)'},
                    )
                except Exception as _j_err:
                    logger.error("[RECONCILE] Erreur journal: %s", _j_err)

            # Reset complet du pair_state
            with _bot_state_lock:
                if backtest_pair in bot_state:
                    bot_state[backtest_pair].update({
                        'entry_price': None, 'max_price': None, 'stop_loss': None,
                        'trailing_stop': None, 'trailing_stop_activated': False,
                        'atr_at_entry': None, 'stop_loss_at_entry': None,
                        'trailing_activation_price_at_entry': None,
                        'initial_position_size': None,
                        'last_order_side': 'SELL',
                        'partial_taken_1': False, 'partial_taken_2': False,
                        'breakeven_triggered': False,
                        'entry_scenario': None, 'entry_timeframe': None,
                        'entry_ema1': None, 'entry_ema2': None,
                        'sl_order_id': None, 'sl_exchange_placed': False,
                    })
                    # A-3: cooldown post-SL si configuré
                    _cd_candles = getattr(config, 'stop_loss_cooldown_candles', 0)
                    if _cd_candles > 0 and _sl_was_filled:
                        _tf = pair_state.get('entry_timeframe') or '1h'
                        _TF_SEC: dict[str, int] = {'1m': 60, '5m': 300, '15m': 900, '30m': 1800,
                                   '1h': 3600, '4h': 14400, '1d': 86400}
                        _candle_sec = _TF_SEC.get(_tf, 3600)
                        bot_state[backtest_pair]['_stop_loss_cooldown_until'] = (
                            time.time() + (_cd_candles * _candle_sec)
                        )
            save_bot_state(force=True)
            logger.info("[RECONCILE] État réinitialisé pour %s — prêt pour nouvel achat", backtest_pair)
        else:
            logger.info(
                f"[RECONCILE] {backtest_pair}: cohérent "
                f"(balance={coin_balance:.6f} {coin_symbol}, in_position={local_in_position})"
            )
            # Nettoyer les champs d'entrée stales quand pas de position réelle
            if not local_in_position and backtest_pair in bot_state:
                _ps = bot_state[backtest_pair]
                if _ps.get('entry_price') is not None or _ps.get('stop_loss_at_entry') is not None:
                    logger.info("[RECONCILE] Nettoyage des champs d'entrée stales pour %s", backtest_pair)
                    with _bot_state_lock:
                        _ps.update({
                            'entry_price': None, 'max_price': None, 'stop_loss': None,
                            'trailing_stop': None, 'trailing_stop_activated': False,
                            'atr_at_entry': None, 'stop_loss_at_entry': None,
                            'trailing_activation_price_at_entry': None,
                            'initial_position_size': None,
                            'partial_taken_1': False, 'partial_taken_2': False,
                            'breakeven_triggered': False,
                            'entry_scenario': None, 'entry_timeframe': None,
                            'entry_ema1': None, 'entry_ema2': None,
                            'sl_order_id': None, 'sl_exchange_placed': False,
                        })
                    save_bot_state(force=True)
            # C-11: Position ouverte sans stop-loss sur Binance → repose automatique du SL
            if local_in_position:
                sl_price = pair_state.get('stop_loss_at_entry')
                if sl_price:
                    try:
                        open_orders = client.get_open_orders(symbol=real_pair)
                        stop_types = {'STOP_LOSS', 'STOP_LOSS_LIMIT', 'TAKE_PROFIT',
                                      'TAKE_PROFIT_LIMIT', 'OCO'}
                        has_stop = any(o.get('type', '') in stop_types for o in open_orders)

                        if not has_stop:
                            logger.warning(
                                "[RECONCILE C-11] Position ouverte pour %s sans stop-loss "
                                "sur Binance — repose automatique du SL à %.8f",
                                backtest_pair, sl_price,
                            )
                            # Snap coin_balance au stepSize
                            try:
                                _exchange_info = get_cached_exchange_info(client)
                                _sym_info = next(
                                    (s for s in _exchange_info['symbols']  # pylint: disable=unsubscriptable-object
                                     if s['symbol'] == real_pair),
                                    None,
                                )
                                _lot_filter = next(
                                    (f for f in _sym_info['filters'] if f['filterType'] == 'LOT_SIZE'),
                                    None,
                                ) if _sym_info else None
                                if _lot_filter:
                                    _step_dec = Decimal(str(float(_lot_filter['stepSize'])))
                                    _qty_dec = (Decimal(str(coin_balance)) // _step_dec) * _step_dec
                                    _qty_str = str(_qty_dec)
                                else:
                                    _qty_str = f"{coin_balance:.6f}"
                            except Exception as _info_err:
                                logger.warning(
                                    "[RECONCILE C-11] stepSize error: %s — using raw balance", _info_err
                                )
                                _qty_str = f"{coin_balance:.6f}"

                            _sl_result = place_exchange_stop_loss_order(real_pair, _qty_str, sl_price)
                            if _sl_result:
                                with _bot_state_lock:
                                    bot_state.setdefault(backtest_pair, {})['sl_order_id'] = (
                                        _sl_result.get('orderId')
                                    )
                                save_bot_state(force=True)
                                logger.info(
                                    "[RECONCILE C-11] SL reposé avec succès pour %s: "
                                    "orderId=%s qty=%s stop=%.8f",
                                    backtest_pair, _sl_result.get('orderId'), _qty_str, sl_price,
                                )
                            else:
                                logger.error(
                                    "[RECONCILE C-11] Échec repose SL pour %s — "
                                    "vérifiez manuellement sur Binance.", backtest_pair
                                )
                        else:
                            logger.info(
                                "[RECONCILE C-11] Stop-loss déjà actif sur Binance pour %s ✓",
                                backtest_pair,
                            )
                    except Exception as _resl_err:
                        logger.error(
                            "[RECONCILE C-11] Erreur vérification/repose SL pour %s: %s",
                            backtest_pair, _resl_err,
                        )
    logger.info("[RECONCILE] Vérification terminée.")

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

# Throttle pour ne pas re-lancer les backtests a chaque cycle planifie
_last_backtest_time: Dict[str, float] = {}
# P3-02: utilise config.backtest_throttle_seconds (défaut 3600s)

# Cache mutable des params actifs par paire — mis à jour à chaque exécution schedulée.
# Permet à la lambda de toujours lire les params les plus récents (WF ou IS-Calmar)
# sans être figée sur le snapshot pris au moment du schedule.every(...).
_live_best_params: Dict[str, Dict[str, Any]] = {}


def _read_live_params(pair: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Lecture thread-safe de _live_best_params avec fallback."""
    with _bot_state_lock:
        return dict(_live_best_params.get(pair, fallback))


# ─── P2-05: OOS quality gate centralisée ──────────────────────────────────────

# Cooldown per-pair pour les alertes email OOS (évite le spam toutes les 2 min)
_oos_alert_last_sent: Dict[str, float] = {}
_oos_alert_lock = threading.Lock()  # P3-A: protège _oos_alert_last_sent contre les accès concurrents

def apply_oos_quality_gate(
    results: List[Dict[str, Any]],
    pair: str,
    *,
    log_tag: str = "C-13",
    unblock_on_pass: bool = True,
    send_alert: bool = False,
    save_force: bool = False,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Filtre *results* par les OOS quality gates et met à jour bot_state.

    P2-05: logique extraite de 3 sites dupliqués (SCHEDULED, MAIN, MAIN-LOOP).

    Returns
    -------
    (selection_pool, oos_blocked)
        *selection_pool* est le sous-ensemble OOS-valide, ou tout le pool en dégradé.
        *oos_blocked* est True si aucun résultat n'a passé les gates.
    """
    try:
        from walk_forward import validate_oos_result as _validate_oos
        valid = [
            r for r in results
            if _validate_oos(r.get('sharpe_ratio', 0.0), r.get('win_rate', 0.0))
        ]
    except Exception as _imp_err:
        logger.warning("[%s] validate_oos_result indisponible: %s", log_tag, _imp_err)
        valid = []  # situation dégradée → bloquer

    if valid:
        pool = valid
        blocked = False
        if unblock_on_pass:
            with _bot_state_lock:
                ps = bot_state.setdefault(pair, {})
                was_blocked = ps.pop('oos_blocked', None) is not None
                ps.pop('oos_blocked_since', None)
            if was_blocked:
                save_bot_state()
                logger.info(
                    "[%s] Blocage P0-03 levé — %d/%d résultats passent les OOS gates.",
                    log_tag, len(valid), len(results),
                )
            else:
                logger.info(
                    "[%s] %d/%d résultats passent les OOS gates.",
                    log_tag, len(valid), len(results),
                )
        else:
            logger.info(
                "[%s] %d/%d résultats passent les OOS gates.",
                log_tag, len(valid), len(results),
            )
    else:
        pool = results
        blocked = True
        with _bot_state_lock:
            ps = bot_state.setdefault(pair, {})
            ps['oos_blocked'] = True
            ps['oos_blocked_since'] = time.time()
        save_bot_state(force=save_force)
        logger.critical(
            "[%s] Aucun résultat ne passe les OOS gates "
            "(Sharpe > %.1f & WR > %.0f%%) — ACHATS BLOQUÉS pour %s.",
            log_tag, config.oos_sharpe_min, config.oos_win_rate_min, pair,
        )
        if send_alert:
            # Cooldown: n'envoyer l'alerte qu'une fois par backtest_throttle_seconds (défaut 1h)
            _now_oos = time.time()
            _cooldown_oos = getattr(config, 'backtest_throttle_seconds', 3600.0)
            with _oos_alert_lock:
                _last_sent = _oos_alert_last_sent.get(pair, 0.0)
            if (_now_oos - _last_sent) >= _cooldown_oos:
                try:
                    send_trading_alert_email(
                        subject=f"[ALERTE {log_tag}] OOS gates non passées — achats bloqués {pair}",
                        body_main=(
                            f"Aucun résultat backtest ne passe les OOS gates pour {pair}.\n"
                            f"Critères: Sharpe > {config.oos_sharpe_min} ET WinRate > {config.oos_win_rate_min}%\n\n"
                            f"Les nouveaux achats sont bloqués. La gestion des stops reste active."
                        ),
                        client=client,
                    )
                    with _oos_alert_lock:
                        _oos_alert_last_sent[pair] = _now_oos
                except Exception as _alert_err:
                    logger.error("[%s] Envoi alerte OOS impossible: %s", log_tag, _alert_err)
            else:
                logger.info(
                    "[%s] Alerte OOS throttled pour %s (cooldown %.0fs, reste %.0fs)",
                    log_tag, pair, _cooldown_oos, _cooldown_oos - (_now_oos - _last_sent),
                )

    return pool, blocked


# _select_best_by_calmar imported from trade_helpers.py as _select_best_by_calmar

def execute_scheduled_trading(real_trading_pair: str, time_interval: str, best_params: Dict[str, Any], backtest_pair: str, sizing_mode: str) -> None:
    """Wrapper pour les exécutions planifiées avec affichage complet (identique au démarrage)."""
    try:
        # === MESSAGE VISUEL DE DEMARRAGE ===
        logger.info(f"[SCHEDULED] DEBUT execution planifiee - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        display_execution_header(backtest_pair, real_trading_pair, time_interval, console)

        # Force flush de la console
        sys.stdout.flush()
        logger.info("[SCHEDULED] Header affiché, debut des backtests...")

        # Re-faire le backtest pour obtenir les paramètres les plus à jour
        # THROTTLE: ne re-backtester que toutes les heures (pas à chaque cycle de 2 min)
        _now = time.time()
        with _bot_state_lock:  # P1-06: protéger _last_backtest_time contre accès concurrents
            _last_bt = _last_backtest_time.get(backtest_pair, 0)
        _time_since_last = _now - _last_bt

        if _time_since_last < config.backtest_throttle_seconds:
            _remaining = int((config.backtest_throttle_seconds - _time_since_last) / 60)
            logger.info(f"[SCHEDULED] Backtest throttlé pour {backtest_pair} — prochain dans ~{_remaining} min. Utilisation des anciens paramètres.")
        else:
            logger.info(f"[SCHEDULED] Re-backtest de {backtest_pair} pour obtenir les paramètres les plus à jour...")

            # Calculer les dates dynamiquement
            today = datetime.today()
            dynamic_start_date = (today - timedelta(days=config.backtest_days)).strftime("%d %B %Y")
            logger.info(f"[SCHEDULED] Backtest dates: {dynamic_start_date} -> {today.strftime('%d %B %Y')}")

            # Re-exécuter le backtest et AFFICHER les résultats
            logger.info("[SCHEDULED] Lancement des backtests...")
            try:
                # Utiliser la liste de timeframes déjà définie en global (pas d'attribut config.timeframes)
                backtest_results = run_all_backtests(
                    backtest_pair,
                    dynamic_start_date,
                    timeframes,
                    sizing_mode=sizing_mode
                )
            except Exception as backtest_err:
                logger.error(f"[SCHEDULED] ERREUR backtest {backtest_pair}: {backtest_err}")
                logger.error(f"[SCHEDULED] Traceback backtest: {traceback.format_exc()}")
                console.print(f"[red][SCHEDULED] Erreur backtest {backtest_pair} : {backtest_err}[/red]")
                backtest_results = None

            if backtest_results:
                with _bot_state_lock:  # P1-06
                    _last_backtest_time[backtest_pair] = time.time()
                logger.info(f"[SCHEDULED] {len(backtest_results)} resultats de backtest recus")

                # C-07 + C-13 + P2-05: OOS quality gate centralisée
                _selection_pool, _ = apply_oos_quality_gate(
                    backtest_results, backtest_pair,
                    log_tag="SCHEDULED C-13", send_alert=True,
                )

                # P2-01: Walk-Forward OOS validation pour la sélection planifiée.
                # Élimine le biais look-ahead : params choisis sur perf OOS, pas IS.
                _sched_wf_best = None
                try:
                    from walk_forward import run_walk_forward_validation as _run_wf_sched
                    _wf_dfs_sched = {}
                    for _tf_s in timeframes:
                        _df_s = prepare_base_dataframe(backtest_pair, _tf_s, dynamic_start_date, 14)
                        _wf_dfs_sched[_tf_s] = _df_s if _df_s is not None and not _df_s.empty else pd.DataFrame()
                    _wf_res_sched = _run_wf_sched(
                        base_dataframes=_wf_dfs_sched,
                        full_sample_results=backtest_results,
                        scenarios=WF_SCENARIOS,
                        backtest_fn=backtest_from_dataframe,
                        initial_capital=config.initial_wallet,
                        sizing_mode=sizing_mode,
                    )
                    if _wf_res_sched.get('any_passed'):
                        _sched_wf_best = _wf_res_sched['best_wf_config']
                        logger.info(
                            "[SCHEDULED P2-01] Sélection Walk-Forward OOS: %s EMA(%s,%s) %s — "
                            "OOS Sharpe=%.2f.",
                            _sched_wf_best['scenario'],
                            _sched_wf_best['ema_periods'][0],
                            _sched_wf_best['ema_periods'][1],
                            _sched_wf_best['timeframe'],
                            _sched_wf_best.get('avg_oos_sharpe', 0.0),
                        )
                    else:
                        logger.info("[SCHEDULED P2-01] Aucun résultat WF valide — fallback IS-Calmar.")
                except Exception as _wf_sched_err:
                    logger.warning("[SCHEDULED P2-01] WF validation skipped: %s", _wf_sched_err)

                best_result = _select_best_by_calmar(_selection_pool)
                best_profit = best_result['final_wallet'] - best_result['initial_wallet']

                logger.info(f"[SCHEDULED] Meilleur resultat IS: {best_result['scenario']} sur {best_result['timeframe']} | Profit IS: ${best_profit:,.2f}")

                # === AFFICHAGE DES RESULTATS ===
                try:
                    display_results_for_pair(backtest_pair, backtest_results)
                    logger.info(f"[SCHEDULED] Résultats affichés pour {backtest_pair}")
                    sys.stdout.flush()
                except Exception as display_err:
                    logger.error(f"[SCHEDULED] Erreur affichage résultats: {str(display_err)}")

                # P2-01: utiliser WF OOS config si disponible, sinon IS-Calmar
                if _sched_wf_best:
                    updated_best_params = {
                        'timeframe': _sched_wf_best['timeframe'],
                        'ema1_period': _sched_wf_best['ema_periods'][0],
                        'ema2_period': _sched_wf_best['ema_periods'][1],
                        'scenario': _sched_wf_best['scenario'],
                    }
                else:
                    updated_best_params = {
                        'timeframe': best_result['timeframe'],
                        'ema1_period': best_result['ema_periods'][0],
                        'ema2_period': best_result['ema_periods'][1],
                        'scenario': best_result['scenario'],
                    }
                updated_best_params.update(SCENARIO_DEFAULT_PARAMS.get(updated_best_params.get('scenario', 'StochRSI'), {}))

                # Vérifier si les paramètres ont changé
                if updated_best_params != best_params:
                    logger.info(f"[SCHEDULED] CHANGEMENT DETECTE - Anciens params: {best_params}")
                    logger.info(f"[SCHEDULED] Nouveaux params: {updated_best_params}")
                    best_params = updated_best_params
                else:
                    logger.info(f"[SCHEDULED] Parametres inchanges pour {backtest_pair}")
            else:
                logger.warning(f"[SCHEDULED] Aucun resultat de backtest pour {backtest_pair}, utilisation des anciens parametres")
                console.print(f"[yellow][SCHEDULED] Aucun résultat de backtest pour {backtest_pair} – affichage sauté[/yellow]")
                # C-06: alerte email sur échec backtest — le bot continue avec anciens params
                try:
                    send_trading_alert_email(
                        subject=f"[ALERTE] Backtest échoué pour {backtest_pair}",
                        body_main=(
                            f"Le backtest de {backtest_pair} n'a retourné aucun résultat.\n"
                            f"Le bot continue avec les anciens paramètres: {best_params}\n\n"
                            f"Vérifier les logs pour plus de détails."
                        ),
                        client=client,
                    )
                except Exception as _alert_err:
                    logger.error(f"[SCHEDULED] Envoi alerte échec backtest impossible: {_alert_err}")

        # Exécuter le trading avec les paramètres mis à jour
        try:
            logger.info(f"[SCHEDULED] Appel execute_real_trades avec {best_params['scenario']} sur {best_params['timeframe']} + sizing_mode='{sizing_mode}'...")
            execute_real_trades(real_trading_pair, best_params['timeframe'], best_params, backtest_pair, sizing_mode=sizing_mode)
            logger.info("[SCHEDULED] execute_real_trades complété avec succès")
        except Exception as trade_error:
            logger.error(f"[SCHEDULED] Erreur dans execute_real_trades: {str(trade_error)}")
            logger.error(f"[SCHEDULED] Traceback: {traceback.format_exc()}")
            try:
                send_trading_alert_email(
                    subject=f"[ALERTE P1] Erreur execute_real_trades — {backtest_pair}",
                    body_main=(
                        f"Une exception non gérée s'est produite dans execute_real_trades.\n\n"
                        f"Paire : {backtest_pair}\n"
                        f"Erreur : {type(trade_error).__name__}: {str(trade_error)[:300]}\n\n"
                        f"Traceback (tronqué) :\n{traceback.format_exc()[:500]}"
                    ),
                    client=client,
                )
            except Exception as _e:
                logger.warning("[SCHEDULED] Email alerte trade impossible: %s", _e)

        # === AFFICHAGE PANEL - SUIVI & PLANIFICATION ===
        logger.info("[SCHEDULED] Affichage des informations de suivi...")

        # Assurer l'initialisation par defaut de l'etat de la paire
        pair_state: PairState = cast('PairState', bot_state.setdefault(backtest_pair, {}))
        # IMPORTANT : Ne pas réinitialiser last_order_side s'il existe déjà
        if 'last_order_side' not in pair_state:
            pair_state['last_order_side'] = None
        current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Mettre à jour l'état
        pair_state['last_run_time'] = current_run_time
        save_bot_state()

        # Persister les params actifs pour que la lambda les lise au prochain cycle.
        with _bot_state_lock:
            _live_best_params[backtest_pair] = dict(best_params)

        # Afficher le panel de suivi
        logger.info("[SCHEDULED] Création et affichage du panel de suivi...")
        try:
            console.print(build_tracking_panel(pair_state, current_run_time))
            console.print("\n")
            sys.stdout.flush()
            logger.info(f"[SCHEDULED] Exécution planifiée COMPLETEE pour {backtest_pair}")
        except Exception as tracking_err:
            logger.error(f"[SCHEDULED] Erreur affichage tracking panel: {str(tracking_err)}")

    except Exception as e:
        logger.error(f"[SCHEDULED] Erreur GLOBALE execution planifiee {backtest_pair}: {str(e)}")
        logger.error(f"[SCHEDULED] Traceback complet: {traceback.format_exc()}")
        try:
            send_trading_alert_email(
                subject=f"[CRITIQUE P1] Erreur globale scheduled — {backtest_pair}",
                body_main=(
                    f"La tâche planifiée (backtest+WF+trade) a planté globalement.\n\n"
                    f"Paire : {backtest_pair}\n"
                    f"Erreur : {type(e).__name__}: {str(e)[:300]}\n\n"
                    f"Traceback (tronqué) :\n{traceback.format_exc()[:500]}\n\n"
                    f"Le bot continue mais cette exécution a été ignorée."
                ),
                client=client,
            )
        except Exception as _e:
            logger.warning("[SCHEDULED] Email alerte globale impossible: %s", _e)


def execute_live_trading_only(real_trading_pair: str, backtest_pair: str, sizing_mode: str) -> None:
    """Exécution live uniquement sans backtest — planifiée toutes les 2 minutes.

    Lit _live_best_params (mis à jour par execute_scheduled_trading toutes les heures)
    et appelle directement execute_real_trades sans aucun backtest ni WF.
    """
    try:
        current_params = _read_live_params(backtest_pair, {})
        if not current_params or 'timeframe' not in current_params:
            logger.warning(f"[LIVE-ONLY] {backtest_pair}: paramètres non disponibles, skip.")
            return

        tf = current_params['timeframe']
        logger.info(
            "[LIVE-ONLY] %s @ %s — %s EMA(%s/%s) %s",
            backtest_pair,
            datetime.now().strftime('%H:%M:%S'),
            current_params.get('scenario'),
            current_params.get('ema1_period'),
            current_params.get('ema2_period'),
            tf,
        )

        execute_real_trades(real_trading_pair, tf, current_params, backtest_pair, sizing_mode=sizing_mode)

        pair_state: PairState = cast('PairState', bot_state.setdefault(backtest_pair, {}))
        current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pair_state['last_run_time'] = current_run_time
        save_bot_state()

        try:
            console.print(build_tracking_panel(pair_state, current_run_time))
            console.print("\n")
            sys.stdout.flush()
        except Exception as _panel_err:
            logger.error(f"[LIVE-ONLY] Erreur panel tracking: {_panel_err}")

    except Exception as e:
        logger.error(f"[LIVE-ONLY] Erreur {backtest_pair}: {e}")
        logger.error(f"[LIVE-ONLY] Traceback: {traceback.format_exc()}")
        try:
            send_trading_alert_email(
                subject=f"[ALERTE P1] Erreur live-only — {backtest_pair}",
                body_main=(
                    f"La tâche live-only (2 min) a planté.\n\n"
                    f"Paire : {backtest_pair}\n"
                    f"Erreur : {type(e).__name__}: {str(e)[:300]}\n\n"
                    f"Traceback (tronqué) :\n{traceback.format_exc()[:500]}\n\n"
                    f"Le bot continue mais ce cycle de trading a été ignoré."
                ),
                client=client,
            )
        except Exception as _e:
            logger.warning("[LIVE-ONLY] Email alerte impossible: %s", _e)


# [REMOVED] Duplicate imports and logger re-initialization were here — already defined at top of file

# ===================================================================
#  C-15: Sous-fonctions extraites de _execute_real_trades_inner
# ===================================================================

@dataclass
class _TradeCtx:
    """Contexte partagé entre les sous-fonctions de _execute_real_trades_inner (C-15)."""
    real_trading_pair: str
    backtest_pair: str
    time_interval: str
    sizing_mode: str
    pair_state: PairState
    best_params: Dict[str, Any]
    ema1_period: int
    ema2_period: int
    scenario: str
    coin_symbol: str
    quote_currency: str
    usdc_balance: float
    coin_balance_free: float
    coin_balance_locked: float
    coin_balance: float
    current_price: float
    row: Any
    orders: List[Any]
    min_qty: float
    max_qty: float
    step_size: float
    min_notional: float
    min_qty_dec: Decimal
    max_qty_dec: Decimal
    step_size_dec: Decimal
    step_decimals: int


def _sync_entry_state(ctx: '_TradeCtx', last_side: Optional[str]) -> None:
    """C-15: Synchronise les variables d'entrée après détection d'un BUY FILLED."""
    if last_side != 'BUY':
        return
    last_buy_order = next(
        (o for o in reversed(ctx.orders) if o['status'] == 'FILLED' and o['side'] == 'BUY'),
        None,
    )
    if not last_buy_order:
        return
    executed_qty = float(last_buy_order.get('executedQty', 0))
    price = float(last_buy_order.get('price', 0))
    if price == 0.0 and executed_qty > 0:
        price = float(last_buy_order.get('cummulativeQuoteQty', 0)) / executed_qty

    atr_value = ctx.row.get('atr')
    atr_stop_multiplier = config.atr_stop_multiplier
    atr_multiplier = config.atr_multiplier
    ps = ctx.pair_state
    # Set entry variables ONLY if not already set (never update after entry)
    if atr_value is not None and price > 0:
        if ps.get('atr_at_entry') is None:
            ps['atr_at_entry'] = atr_value
        if ps.get('entry_price') is None:
            ps['entry_price'] = price
        if ps.get('stop_loss_at_entry') is None:
            ps['stop_loss_at_entry'] = price - atr_stop_multiplier * atr_value
            ps['stop_loss'] = ps['stop_loss_at_entry']
        if ps.get('trailing_activation_price_at_entry') is None:
            ps['trailing_activation_price_at_entry'] = price + atr_multiplier * atr_value
            ps['trailing_activation_price'] = ps['trailing_activation_price_at_entry']
        # === TRACKER L'ÉTAT DE LA POSITION ===
        ps['last_order_side'] = 'BUY'
        save_bot_state()


def _update_trailing_stop(ctx: '_TradeCtx') -> None:
    """C-15: Met à jour l'activation et le niveau du trailing stop."""
    ps = ctx.pair_state
    if ps.get('last_order_side') != 'BUY' or ctx.coin_balance <= 0:
        return

    entry_price = ps.get('entry_price')
    atr_at_entry = ps.get('atr_at_entry')
    trailing_activation_price = ps.get('trailing_activation_price_at_entry')
    trailing_activated = ps.get('trailing_stop_activated', False)
    max_price = ps.get('max_price')
    if max_price is None:
        max_price = entry_price if entry_price is not None else ctx.current_price

    # Protection : si trailing_activation_price n'existe pas, le recalculer
    if trailing_activation_price is None and entry_price and atr_at_entry:
        trailing_activation_price = entry_price + (config.atr_multiplier * atr_at_entry)
        ps['trailing_activation_price_at_entry'] = trailing_activation_price
        ps['trailing_activation_price'] = trailing_activation_price
        save_bot_state()
        logger.info(f"[TRAILING] Prix d'activation recalculé: {trailing_activation_price:.4f}")

    # Mise à jour du max_price
    if max_price is None:
        max_price = ctx.current_price
    if max_price is not None and ctx.current_price is not None and ctx.current_price > max_price:
        max_price = ctx.current_price

    # === ACTIVATION DU TRAILING (quand prix >= entry + 5×ATR) ===
    if not trailing_activated and trailing_activation_price:
        if trailing_activation_price is not None and ctx.current_price is not None and ctx.current_price >= trailing_activation_price:
            trailing_activated = True
            logger.info(f"[TRAILING] ⚡ ACTIVÉ à {ctx.current_price:.4f} (seuil: {trailing_activation_price:.4f})")
            # Initialiser le trailing stop
            trailing_distance = config.atr_multiplier * atr_at_entry if atr_at_entry else None
            if trailing_distance:
                trailing_stop_val = max_price - trailing_distance
                ps['trailing_stop'] = trailing_stop_val
                logger.info(f"[TRAILING] Stop initial: {trailing_stop_val:.4f}")

    # Mise à jour du trailing stop SI activé
    if trailing_activated and atr_at_entry is not None and max_price is not None:
        trailing_distance = config.atr_multiplier * atr_at_entry
        new_trailing = max_price - trailing_distance
        current_trailing = ps.get('trailing_stop', 0)
        # Le trailing ne peut que monter (protection des gains)
        if new_trailing is not None and current_trailing is not None and new_trailing > current_trailing:
            ps['trailing_stop'] = new_trailing
            logger.info(f"[TRAILING] Nouveau stop : {new_trailing:.4f} (max: {max_price:.4f})")

    # B-3: Break-even stop — remonter stop_loss_at_entry au prix d'entrée dès que
    # le profit atteint breakeven_trigger_pct. Identique au backtest (backtest_runner.py).
    _be_enabled = getattr(config, 'breakeven_enabled', True)
    if _be_enabled and not ps.get('breakeven_triggered', False) and entry_price and entry_price > 0:
        if ctx.current_price is not None:
            _be_profit = (ctx.current_price - entry_price) / entry_price
            if _be_profit >= getattr(config, 'breakeven_trigger_pct', 0.02):
                _be_new_stop = entry_price * (1 + config.slippage_buy)
                _current_sl = ps.get('stop_loss_at_entry') or 0
                if _be_new_stop > _current_sl:
                    ps['stop_loss_at_entry'] = _be_new_stop
                    logger.info(
                        "[B-3 BREAKEVEN] Stop remonté au prix d'entrée + slippage : %.4f "
                        "(profit %.2f%% >= seuil %.1f%%)",
                        _be_new_stop, _be_profit * 100,
                        getattr(config, 'breakeven_trigger_pct', 0.02) * 100,
                    )
                ps['breakeven_triggered'] = True
                save_bot_state()

    ps.update({
        'trailing_stop_activated': trailing_activated,
        'max_price': max_price,
    })
    save_bot_state()


def _execute_partial_sells(ctx: '_TradeCtx') -> None:
    """Exécute les prises de profit partielles basées sur profit_pct.

    Logique miroir du backtest (backtest_runner.py L337-358) :
    - PARTIAL-1 : profit_pct >= config.partial_threshold_1 → vendre config.partial_pct_1
    - PARTIAL-2 : profit_pct >= config.partial_threshold_2 → vendre config.partial_pct_2 du reste

    Appelé AVANT _check_and_execute_stop_loss pour respecter l'ordre du backtest.
    Inclut la synchronisation API des flags partiels.
    """
    ps = ctx.pair_state
    if ps.get('last_order_side') != 'BUY' or ctx.coin_balance_free <= 0:
        return

    entry_price = ps.get('entry_price')
    if not entry_price or entry_price <= 0 or ctx.current_price is None:
        return

    # === SYNCHRONISATION AVEC L'HISTORIQUE API (SOURCE DE VÉRITÉ) ===
    try:
        api_partial_1, api_partial_2 = check_partial_exits_from_history(ctx.real_trading_pair, entry_price)
        state_partial_1 = ps.get('partial_taken_1', False)
        state_partial_2 = ps.get('partial_taken_2', False)
        if api_partial_1 != state_partial_1 or api_partial_2 != state_partial_2:
            logger.warning(f"[SYNC] Désynchronisation détectée ! Local: P1={state_partial_1}, P2={state_partial_2}")
            logger.warning(f"[SYNC] API (source de vérité): P1={api_partial_1}, P2={api_partial_2}")
            ps['partial_taken_1'] = api_partial_1
            ps['partial_taken_2'] = api_partial_2
            save_bot_state()
            logger.info(f"[SYNC] Flags synchronisés : PARTIAL-1={api_partial_1}, PARTIAL-2={api_partial_2}")
        else:
            logger.debug(f"[SYNC] État cohérent : PARTIAL-1={state_partial_1}, PARTIAL-2={state_partial_2}")
    except Exception as e:
        logger.error(f"[SYNC] Erreur synchronisation API : {e}")

    partial_enabled = ps.get('partial_enabled', True)
    if not partial_enabled:
        logger.debug("[PARTIAL] Mode partials désactivé pour cette position (taille insuffisante)")
        return

    profit_pct = (ctx.current_price - entry_price) / entry_price

    # === PARTIAL-1 ===
    if not ps.get('partial_taken_1', False) and profit_pct >= config.partial_threshold_1:
        # F-1: Annuler le SL exchange pour libérer les coins lockés avant vente partielle
        _cancel_exchange_sl(ctx)
        _execute_one_partial(ctx, partial_number=1, sell_pct=config.partial_pct_1, profit_pct=profit_pct)

    # === PARTIAL-2 (vérifie sur le solde mis à jour après PARTIAL-1) ===
    if not ps.get('partial_taken_2', False) and profit_pct >= config.partial_threshold_2 and ctx.coin_balance > 0:
        # F-1: Annuler le SL exchange si pas déjà fait (cas PARTIAL-2 sans PARTIAL-1)
        _cancel_exchange_sl(ctx)
        _execute_one_partial(ctx, partial_number=2, sell_pct=config.partial_pct_2, profit_pct=profit_pct)


def _execute_one_partial(ctx: '_TradeCtx', *, partial_number: int, sell_pct: float, profit_pct: float) -> None:
    """Exécute une vente partielle (PARTIAL-1 ou PARTIAL-2).

    Parameters
    ----------
    partial_number : 1 ou 2
    sell_pct : fraction du solde libre à vendre (ex. 0.50 = 50 %)
    profit_pct : profit_pct actuel (pour le logging/email)
    """
    ps = ctx.pair_state
    label = f"PARTIAL-{partial_number}"
    flag_key = f"partial_taken_{partial_number}"
    threshold = config.partial_threshold_1 if partial_number == 1 else config.partial_threshold_2

    qty_to_sell = ctx.coin_balance * sell_pct  # F-1: use coin_balance (not free) — SL cancelled before call

    # Arrondir selon les règles d'échange
    quantity_decimal = Decimal(str(qty_to_sell))
    quantity_rounded = (quantity_decimal // ctx.step_size_dec) * ctx.step_size_dec
    if quantity_rounded < ctx.min_qty_dec:
        quantity_rounded = quantity_decimal
    if quantity_rounded > ctx.max_qty_dec:
        quantity_rounded = ctx.max_qty_dec

    # Vérifier LOT_SIZE et MIN_NOTIONAL
    notional_value = float(quantity_rounded) * ctx.current_price

    if quantity_rounded < ctx.min_qty_dec or notional_value < ctx.min_notional:
        # Montant trop faible → marquer comme pris pour éviter retry infini
        if quantity_rounded < ctx.min_qty_dec:
            logger.warning(f"[{label}] Vente bloquée : Quantité {quantity_rounded} < min_qty {ctx.min_qty_dec}")
        if notional_value < ctx.min_notional:
            logger.warning(f"[{label}] Vente bloquée : Valeur {notional_value:.2f} USDC < MIN_NOTIONAL {ctx.min_notional:.2f} USDC")
        # F-3: Email d'alerte sur vente partielle bloquée
        try:
            send_trading_alert_email(
                subject=f"[ALERTE] {label} BLOQUÉE — {ctx.real_trading_pair}",
                body_main=(
                    f"Vente partielle {label} bloquée pour {ctx.real_trading_pair}.\n\n"
                    f"Quantité: {quantity_rounded} (min_qty: {ctx.min_qty_dec})\n"
                    f"Valeur: {notional_value:.2f} USDC (min: {ctx.min_notional:.2f})\n"
                    f"Profit: {profit_pct * 100:.1f}%\n"
                    f"Flag {flag_key} = True pour éviter retry."
                ),
                client=client,
            )
        except Exception as _email_err:
            logger.error(f"[{label}] Échec envoi email vente bloquée: {_email_err}")
        if partial_number == 1:
            ps['partial_taken_1'] = True
        else:
            ps['partial_taken_2'] = True
        save_bot_state()
        logger.info(f"[{label}] Montant trop faible — Flag mis à True pour éviter retry")
        return

    try:
        qty_str = f"{quantity_rounded:.{ctx.step_decimals}f}"
        sell_order = safe_market_sell(symbol=ctx.real_trading_pair, quantity=qty_str)

        if sell_order and sell_order.get('status') == 'FILLED':
            executed_price = ctx.current_price
            total_usdc_received = float(qty_str) * executed_price

            logger.info(f"[{label}] Vente exécutée et confirmée : {qty_str} {ctx.coin_symbol} (profit {profit_pct * 100:.1f}%)")

            if partial_number == 1:
                ps['partial_taken_1'] = True
            else:
                ps['partial_taken_2'] = True
            save_bot_state()
            logger.info(f"[{label}] Flag mis à jour : {flag_key} = True")

            # Email de vente partielle
            sell_type_desc = f"Prise de profit partielle {partial_number} (+{threshold * 100:.0f}%)"
            position_closed = f"{sell_pct * 100:.0f}%"

            extra = f"Timeframe : {ctx.time_interval}\nEMA : {ctx.ema1_period}/{ctx.ema2_period}\nScenario : {ctx.scenario}"
            subj, body = sell_executed_email(
                pair=ctx.real_trading_pair, qty=float(qty_str), price=executed_price,
                usdc_received=total_usdc_received, sell_reason=sell_type_desc,
                pnl_pct=profit_pct * 100,
                extra_details=extra
            )
            if is_valid_stop_loss_order(ctx.real_trading_pair, qty_str, executed_price):
                try:
                    send_trading_alert_email(subject=subj, body_main=body, client=client)
                    logger.info(f"[{label}] E-mail d'alerte envoyé pour la vente partielle")
                except Exception as e:
                    logger.error(f"[{label}] L'envoi de l'e-mail a echoué : {e}")
            else:
                logger.warning(f"[{label}] Email NON ENVOYÉ : paramètres invalides")

            # Rafraîchir le balance après vente partielle
            account_info = client.get_account()
            _, ctx.coin_balance_free, ctx.coin_balance_locked, ctx.coin_balance = _get_coin_balance(account_info, ctx.coin_symbol)

            # F-1: Replacer SL exchange sur la quantité restante après partiel
            _sl_price = ps.get('stop_loss_at_entry')
            if _sl_price and ctx.coin_balance > ctx.min_qty:
                try:
                    _remaining_dec = Decimal(str(ctx.coin_balance))
                    _remaining_rounded = (_remaining_dec // ctx.step_size_dec) * ctx.step_size_dec
                    _remaining_str = f"{_remaining_rounded:.{ctx.step_decimals}f}"
                    _sl_result = place_exchange_stop_loss_order(
                        symbol=ctx.real_trading_pair,
                        quantity=_remaining_str,
                        stop_price=float(_sl_price),
                    )
                    ps['sl_order_id'] = _sl_result.get('orderId')
                    ps['sl_exchange_placed'] = True
                    save_bot_state()
                    logger.info(
                        "[%s] SL exchange replacé (qty=%s, stop=%.4f, orderId=%s)",
                        label, _remaining_str, _sl_price, ps['sl_order_id'],
                    )
                except Exception as _sl_err:
                    logger.warning("[%s] Échec replacement SL exchange: %s", label, _sl_err)

            # Journal de trading
            try:
                logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                _entry_px = ps.get('entry_price') or 0
                _pnl = (float(executed_price) - _entry_px) * float(qty_str) if _entry_px else None
                _pnl_pct = ((float(executed_price) / _entry_px) - 1) if _entry_px else None
                log_trade(
                    logs_dir=logs_dir,
                    pair=ctx.real_trading_pair,
                    side='sell',
                    quantity=float(qty_str),
                    price=float(executed_price),
                    fee=float(qty_str) * config.taker_fee * float(executed_price),
                    scenario=ctx.scenario,
                    timeframe=ctx.time_interval,
                    pnl=_pnl,
                    pnl_pct=_pnl_pct,
                    extra={'sell_reason': label, 'position_closed': position_closed},
                )
            except Exception as journal_err:
                logger.error(f"[JOURNAL] Erreur écriture vente partielle: {journal_err}")
        else:
            logger.warning(f"[{label}] Ordre de vente non FILLED — tentative échouée")
            try:
                _status = sell_order.get('status', 'UNKNOWN') if sell_order else 'None'
                send_trading_alert_email(
                    subject=f"[ALERTE] {label} NON FILLED — {ctx.real_trading_pair}",
                    body_main=(
                        f"Ordre de vente partielle {label} non exécuté.\n\n"
                        f"Paire : {ctx.real_trading_pair}\n"
                        f"Quantité : {qty_str}\n"
                        f"Statut : {_status}\n"
                        f"Prix courant : {ctx.current_price:.4f} USDC\n\n"
                        f"Action : le bot réessaiera au prochain cycle."
                    ),
                    client=client,
                )
            except Exception as _email_err:
                logger.error(f"[{label}] Échec envoi email vente non-filled: {_email_err}")

    except Exception as e:
        logger.error(f"[{label}] Erreur lors de l'exécution de la vente partielle: {e}")


def _check_and_execute_stop_loss(ctx: '_TradeCtx') -> bool:
    """C-15: Vérifie et exécute le stop-loss/trailing. Retourne True si position fermée."""
    ps = ctx.pair_state
    if ps.get('last_order_side') != 'BUY' or ctx.coin_balance <= 0:
        return False

    stop_loss_fixed = ps.get('stop_loss_at_entry')  # Stop-loss FIXE à 3×ATR
    trailing_stop = ps.get('trailing_stop', 0)  # Trailing (si activé)
    trailing_activated = ps.get('trailing_stop_activated', False)

    # Déterminer le niveau de stop effectif
    effective_stop = stop_loss_fixed
    is_trailing_stop = False
    if trailing_activated and trailing_stop is not None and stop_loss_fixed is not None and trailing_stop > (stop_loss_fixed or 0):
        effective_stop = trailing_stop
        is_trailing_stop = True

    if effective_stop is None or ctx.current_price is None or ctx.current_price > effective_stop:
        return False

    # === Prix <= effective_stop — exécuter le stop-loss ===
    # Si les coins sont verrouillés dans un ordre SL exchange (STOP_LOSS_LIMIT pending),
    # Binance gère la vente automatiquement — ne pas tenter un market-sell en doublon.
    # MAIS il faut vérifier si l'ordre a DÉJÀ été FILLED pour réconcilier l'état.
    executed_price: Optional[float] = None
    if ctx.coin_balance_free < ctx.min_qty:
        sl_oid = ps.get('sl_order_id')
        _sl_filled = False
        _sl_fill_price = ctx.current_price  # fallback
        _sl_exec_qty = 0.0

        # Vérifier le statut réel de l'ordre SL sur l'exchange
        if sl_oid:
            try:
                _sl_info = client.get_order(
                    symbol=ctx.real_trading_pair, orderId=sl_oid,
                )
                _sl_status = _sl_info.get('status', '')
                if _sl_status == 'FILLED':
                    _sl_filled = True
                    _eq = float(_sl_info.get('executedQty', 0))
                    _cq = float(_sl_info.get('cummulativeQuoteQty', 0))
                    if _eq > 0:
                        _sl_exec_qty = _eq
                    if _eq > 0 and _cq > 0:
                        _sl_fill_price = _cq / _eq
                    logger.info(
                        "[SL-EXCHANGE] Ordre SL %s FILLED — prix moyen %.4f USDC, qty %.8f",
                        sl_oid, _sl_fill_price, _sl_exec_qty,
                    )
                else:
                    logger.info(
                        "[STOP-LOSS SOFTWARE] Ordre SL exchange %s statut=%s — "
                        "vente gérée automatiquement par Binance.",
                        sl_oid, _sl_status,
                    )
            except Exception as _sl_check_err:
                logger.warning(
                    "[SL-EXCHANGE] Impossible de vérifier l'ordre SL %s: %s",
                    sl_oid, _sl_check_err,
                )
        else:
            # Pas de sl_order_id — vérifier si balance indique une clôture
            if ctx.coin_balance_locked < ctx.min_qty:
                _sl_filled = True
                logger.warning(
                    "[SL-EXCHANGE] Aucun sl_order_id mais coins absents "
                    "(free=%.8f, locked=%.8f) — SL probablement FILLED.",
                    ctx.coin_balance_free, ctx.coin_balance_locked,
                )

        if not _sl_filled:
            return True

        # === SL exchange FILLED — réconciliation complète ===
        executed_price = _sl_fill_price  # SL always has a fill price
        total_usdc_received = _sl_exec_qty * executed_price if _sl_exec_qty else 0.0

        if is_trailing_stop:
            stop_type = "TRAILING-STOP (dynamique)"
            stop_desc = (
                f"Prix max atteint : {ps.get('max_price', 0):.4f} USDC\n"
                f"Trailing stop : {trailing_stop:.4f} USDC"
            )
        else:
            stop_type = "STOP-LOSS (fixe à 3×ATR)"
            stop_desc = f"Stop-loss fixe : {stop_loss_fixed:.4f} USDC"

        # Email d'alerte SL exchange
        _entry_px_sl = ps.get('entry_price') or 0
        _pnl_pct_sl = ((executed_price / _entry_px_sl) - 1) * 100 if _entry_px_sl > 0 else None
        if is_valid_stop_loss_order(ctx.real_trading_pair, str(_sl_exec_qty or 1), executed_price):
            extra = (
                f"DETAILS DU STOP (ordre exchange natif):\n{stop_desc}\n"
                f"Prix d'entree : {_entry_px_sl:.4f} USDC\n"
                f"Timeframe : {ctx.time_interval}\n"
                f"EMA : {ctx.ema1_period}/{ctx.ema2_period}\n"
                f"Scenario : {ctx.scenario}"
            )
            subj, body = sell_executed_email(
                pair=ctx.real_trading_pair, qty=_sl_exec_qty or 0,
                price=executed_price, usdc_received=total_usdc_received,
                sell_reason=stop_type, pnl_pct=_pnl_pct_sl,
                extra_details=extra,
            )
            try:
                send_trading_alert_email(subject=subj, body_main=body, client=client)
                logger.info("[SL-EXCHANGE] E-mail d'alerte envoyé pour SL exchange")
            except Exception as _email_err:
                logger.error("[SL-EXCHANGE] Échec envoi e-mail: %s", _email_err)
        else:
            logger.warning(
                "[SL-EXCHANGE] Email NON ENVOYÉ : paramètres invalides "
                "(symbol=%s, qty=%s, price=%s)",
                ctx.real_trading_pair, _sl_exec_qty, executed_price,
            )

        # Capturer entry_price AVANT le reset pour le journal PnL
        _saved_entry_price = ps.get('entry_price') or 0.0

        # Reset complet de l'état — identique au path SL software
        ps.update({
            'entry_price': None, 'max_price': None, 'stop_loss': None,
            'trailing_stop': None, 'trailing_stop_activated': False,
            'atr_at_entry': None, 'stop_loss_at_entry': None,
            'trailing_activation_price_at_entry': None,
            'initial_position_size': None,
            'last_order_side': 'SELL',
            'breakeven_triggered': False,
            'entry_scenario': None, 'entry_timeframe': None,
            'entry_ema1': None, 'entry_ema2': None,
            'sl_order_id': None, 'sl_exchange_placed': False,
        })

        # A-3: cooldown post-stop-loss
        _cd_candles = getattr(config, 'stop_loss_cooldown_candles', 0)
        if _cd_candles > 0:
            _TF_SEC = {'1m': 60, '5m': 300, '15m': 900, '30m': 1800,
                       '1h': 3600, '4h': 14400, '1d': 86400}
            _candle_sec = _TF_SEC.get(ctx.time_interval, 3600)
            ps['_stop_loss_cooldown_until'] = time.time() + (_cd_candles * _candle_sec)
            logger.info(
                "[A-3 COOLDOWN] Post-stop-loss exchange: %d bougies x %ds = %.1fh",
                _cd_candles, _candle_sec, (_cd_candles * _candle_sec) / 3600,
            )

        save_bot_state(force=True)

        # Journal de trading
        if _sl_exec_qty > 0:
            try:
                logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                _pnl = (executed_price - _saved_entry_price) * _sl_exec_qty if _saved_entry_price else None
                _pnl_pct_j = ((executed_price / _saved_entry_price) - 1) if _saved_entry_price else None
                _update_daily_pnl(_pnl)
                log_trade(
                    logs_dir=logs_dir,
                    pair=ctx.real_trading_pair,
                    side='sell',
                    quantity=_sl_exec_qty,
                    price=executed_price,
                    fee=_sl_exec_qty * config.taker_fee * executed_price,
                    scenario=ctx.scenario,
                    timeframe=ctx.time_interval,
                    pnl=_pnl,
                    pnl_pct=_pnl_pct_j,
                    extra={'sell_reason': f'{stop_type} (exchange-filled)'},
                )
            except Exception as _journal_err:
                logger.error("[JOURNAL] Erreur écriture vente SL exchange: %s", _journal_err)

        # Affichage panel de clôture
        if is_trailing_stop:
            stop_loss_info = f"{trailing_stop:.4f} USDC (dynamique : trailing)"
        else:
            stop_loss_info = f"{stop_loss_fixed:.4f} USDC (fixe à l'entrée)"
        display_closure_panel(stop_loss_info, ctx.current_price, ctx.coin_symbol, ctx.coin_balance, console)

        ps['last_execution'] = datetime.now(timezone.utc).isoformat()
        save_bot_state()
        return True

    # Exécution vente immédiate sur stop-loss (coins libres)
    quantity_decimal = Decimal(str(ctx.coin_balance_free))
    quantity_rounded = (quantity_decimal // ctx.step_size_dec) * ctx.step_size_dec
    if quantity_rounded < ctx.min_qty_dec:
        quantity_rounded = quantity_decimal
    if quantity_rounded > ctx.max_qty_dec:
        quantity_rounded = ctx.max_qty_dec

    # Vérification min_notional (valeur de l'ordre >= min_notional)
    order_value = float(quantity_rounded) * ctx.current_price
    executed_price = None
    _saved_entry_price = 0.0
    stop_type = 'STOP-LOSS'
    qty_str: str = ""

    if quantity_rounded >= ctx.min_qty_dec and order_value >= ctx.min_notional:
        qty_str = f"{quantity_rounded:.{ctx.step_decimals}f}"
        stop_loss_order = safe_market_sell(symbol=ctx.real_trading_pair, quantity=qty_str)
        if stop_loss_order and stop_loss_order.get('status') == 'FILLED':
            logger.info(f"[STOP-LOSS] Vente exécutée et confirmée : {qty_str} {ctx.coin_symbol}")

            # Récupérer le prix d'exécution
            executed_price = ctx.current_price
        else:
            # SL software sell NOT FILLED
            _status = stop_loss_order.get('status', 'UNKNOWN') if stop_loss_order else 'None'
            logger.warning(f"[STOP-LOSS] Ordre de vente SL non FILLED (statut={_status})")
            try:
                send_trading_alert_email(
                    subject=f"[ALERTE] Stop-Loss NON FILLED — {ctx.real_trading_pair}",
                    body_main=(
                        f"Ordre de vente stop-loss (software) non exécuté.\n\n"
                        f"Paire : {ctx.real_trading_pair}\n"
                        f"Quantité : {qty_str}\n"
                        f"Stop effectif : {effective_stop:.4f} USDC\n"
                        f"Prix courant : {ctx.current_price:.4f} USDC\n"
                        f"Statut : {_status}\n\n"
                        f"ATTENTION : position toujours exposée sans protection.\n"
                        f"Action : le bot réessaiera au prochain cycle."
                    ),
                    client=client,
                )
            except Exception as _email_err:
                logger.error(f"[STOP-LOSS] Échec envoi email SL non-filled: {_email_err}")

        if stop_loss_order and stop_loss_order.get('status') == 'FILLED':
            executed_price = executed_price or ctx.current_price
            total_usdc_received = float(qty_str) * executed_price

            # Déterminer le type de stop-loss
            if is_trailing_stop:
                stop_type = "TRAILING-STOP (dynamique)"
                stop_desc = f"Prix max atteint : {ps.get('max_price', 0):.4f} USDC\nTrailing stop : {trailing_stop:.4f} USDC"
            else:
                stop_type = "STOP-LOSS (fixe à 3×ATR)"
                stop_desc = f"Stop-loss fixe : {stop_loss_fixed:.4f} USDC"

            # === EMAIL STOP-LOSS (sécurisé) ===
            if is_valid_stop_loss_order(ctx.real_trading_pair, qty_str, executed_price):
                _entry_px_sl = ps.get('entry_price') or 0
                _pnl_pct_sl = ((executed_price / _entry_px_sl) - 1) * 100 if _entry_px_sl > 0 else None
                extra = f"DETAILS DU STOP:\n{stop_desc}\nPrix d'entree : {ps.get('entry_price', 0):.4f} USDC\nTimeframe : {ctx.time_interval}\nEMA : {ctx.ema1_period}/{ctx.ema2_period}\nScenario : {ctx.scenario}"
                subj, body = sell_executed_email(
                    pair=ctx.real_trading_pair, qty=float(qty_str), price=executed_price,
                    usdc_received=total_usdc_received, sell_reason=stop_type,
                    pnl_pct=_pnl_pct_sl,
                    extra_details=extra
                )
                try:
                    send_trading_alert_email(subject=subj, body_main=body, client=client)
                    logger.info("[STOP-LOSS] E-mail d'alerte envoye pour la vente")
                except Exception as e:
                    logger.error(f"[STOP-LOSS] L'envoi de l'e-mail a echoue : {e}")
            else:
                logger.warning(f"[STOP-LOSS] Email NON ENVOYÉ : paramètres invalides (symbol={ctx.real_trading_pair}, qty={qty_str}, price={executed_price})")

            # Capturer entry_price AVANT le reset pour le journal PnL
            _saved_entry_price = ps.get('entry_price') or 0.0

            # Reset entry variables after closure
            ps.update({
                'entry_price': None, 'max_price': None, 'stop_loss': None,
                'trailing_stop': None, 'trailing_stop_activated': False,
                'atr_at_entry': None, 'stop_loss_at_entry': None,
                'trailing_activation_price_at_entry': None,
                'last_order_side': 'SELL',
                'breakeven_triggered': False,  # B-3: réinitialiser pour la prochaine position
                # F-COH: libérer le verrou des params d’entrée
                'entry_scenario': None, 'entry_timeframe': None,
                'entry_ema1': None, 'entry_ema2': None,
            })
            # A-3: set cooldown after stop-loss — durée basée sur la durée réelle de la bougie
            _cd_candles = getattr(config, 'stop_loss_cooldown_candles', 0)
            if _cd_candles > 0:
                _TF_SEC = {'1m': 60, '5m': 300, '15m': 900, '30m': 1800,
                           '1h': 3600, '4h': 14400, '1d': 86400}
                _candle_sec = _TF_SEC.get(ctx.time_interval, 3600)
                ps['_stop_loss_cooldown_until'] = time.time() + (_cd_candles * _candle_sec)
                logger.info(
                    "[A-3 COOLDOWN] Post-stop-loss : %d bougies x %ds = %.1fh",
                    _cd_candles, _candle_sec, (_cd_candles * _candle_sec) / 3600,
                )
            save_bot_state()

        # Journal de trading (Phase 2) — STOP-LOSS / TRAILING
        if executed_price:
            try:
                logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                _exec_price = float(executed_price)
                _qty = float(qty_str) if qty_str else 0.0
                _pnl = (_exec_price - _saved_entry_price) * _qty if _saved_entry_price and _exec_price else None
                _pnl_pct = ((_exec_price / _saved_entry_price) - 1) if _saved_entry_price and _exec_price else None
                _update_daily_pnl(_pnl)
                log_trade(
                    logs_dir=logs_dir,
                    pair=ctx.real_trading_pair,
                    side='sell',
                    quantity=_qty,
                    price=_exec_price,
                    fee=_qty * config.taker_fee * _exec_price,
                    scenario=ctx.scenario,
                    timeframe=ctx.time_interval,
                    pnl=_pnl,
                    pnl_pct=_pnl_pct,
                    extra={'sell_reason': stop_type},
                )
            except Exception as journal_err:
                logger.error(f"[JOURNAL] Erreur écriture vente stop: {journal_err}")

    # Affichage explicite de la nature du stop-loss utilisé
    if is_trailing_stop:
        stop_loss_info = f"{trailing_stop:.4f} USDC (dynamique : trailing)"
    else:
        stop_loss_info = f"{stop_loss_fixed:.4f} USDC (fixe à l'entrée)"

    display_closure_panel(stop_loss_info, ctx.current_price, ctx.coin_symbol, ctx.coin_balance, console)
    ps['last_execution'] = datetime.now(timezone.utc).isoformat()
    save_bot_state()
    return True


def _handle_dust_cleanup(ctx: '_TradeCtx') -> bool:
    """C-15: Détecte et nettoie les résidus (dust). Retourne position_has_crypto."""
    ps = ctx.pair_state
    # Position réelle = assez de coins ET (valeur >= min_notional OU position intentionnelle BUY)
    # Quand last_order_side='SELL' mais du dust subsiste au-dessus de min_qty,
    # la valeur notional détermine si c'est tradeable ou non.
    _notional_value = ctx.coin_balance * ctx.current_price if ctx.current_price > 0 else 0.0
    position_has_crypto = (
        ctx.coin_balance > ctx.min_qty
        and (
            _notional_value >= ctx.min_notional
            or ps.get('last_order_side') == 'BUY'
        )
    )

    # === DÉTECTION ET NETTOYAGE FORCÉ DES RÉSIDUS (DUST) ===
    # Dust = coins présents mais non tradeable (soit < min_qty, soit < min_notional sans position BUY)
    has_dust = (
        ctx.coin_balance > ctx.min_qty * 0.01
        and not position_has_crypto
        and ctx.coin_balance_locked < ctx.min_qty
    )

    if has_dust:
        logger.warning(f"[DUST] Résidu détecté : {ctx.coin_balance:.8f} {ctx.coin_symbol} (entre 1% et 98% de min_qty)")

        # IMPORTANT: Vérifier si la valeur totale du résidu respecte MIN_NOTIONAL
        dust_notional_value = ctx.coin_balance * ctx.current_price
        if dust_notional_value < ctx.min_notional:
            logger.warning(f"[DUST] Valeur du résidu ({dust_notional_value:.2f} USDC) < MIN_NOTIONAL ({ctx.min_notional:.2f} USDC)")
            logger.warning(f"[DUST] Impossible de vendre le résidu - Binance refuse les ordres < {ctx.min_notional:.2f} USDC")
            logger.info("[DUST] Résidu ignoré (position considérée comme fermée)")
            # Reset état : nettoyer les champs d'entrée stales (quel que soit last_order_side)
            _stale_fields = (
                ps.get('entry_price') is not None
                or ps.get('stop_loss_at_entry') is not None
                or ps.get('last_order_side') == 'BUY'
            )
            if _stale_fields:
                ps.update({
                    'entry_price': None, 'max_price': None, 'stop_loss': None,
                    'trailing_stop': None, 'trailing_stop_activated': False,
                    'atr_at_entry': None, 'stop_loss_at_entry': None,
                    'trailing_activation_price_at_entry': None,
                    'initial_position_size': None,
                    'last_order_side': 'SELL',
                    'partial_taken_1': False, 'partial_taken_2': False,
                    'breakeven_triggered': False,
                    'entry_scenario': None, 'entry_timeframe': None,
                    'entry_ema1': None, 'entry_ema2': None,
                    'sl_order_id': None, 'sl_exchange_placed': False,
                })
                save_bot_state(force=True)
                logger.info("[DUST] État pair_state réinitialisé (dust intradable, position considérée fermée)")
        else:
            logger.info("[DUST] Tentative de vente forcée du résidu pour débloquer le trading...")

            try:
                qty_str = f"{ctx.coin_balance:.{ctx.step_decimals}f}"
                dust_sell_order = safe_market_sell(symbol=ctx.real_trading_pair, quantity=qty_str)

                if dust_sell_order and dust_sell_order.get('status') == 'FILLED':
                    logger.info(f"[DUST] Vente réussie et confirmée du résidu : {qty_str} {ctx.coin_symbol}")
                    # Reset complet de l'état après nettoyage
                    ps.update({
                        'entry_price': None, 'max_price': None, 'stop_loss': None,
                        'trailing_stop': None, 'trailing_stop_activated': False,
                        'atr_at_entry': None, 'stop_loss_at_entry': None,
                        'trailing_activation_price_at_entry': None,
                        'initial_position_size': None,
                        'last_order_side': 'SELL',
                        'partial_taken_1': False,
                        'partial_taken_2': False,
                        'breakeven_triggered': False,
                        'entry_scenario': None, 'entry_timeframe': None,
                        'entry_ema1': None, 'entry_ema2': None,
                        'sl_order_id': None, 'sl_exchange_placed': False,
                    })
                    save_bot_state()

                    # Rafraîchir le solde
                    account_info = client.get_account()
                    # C-13: helper centralisé free+locked
                    _, ctx.coin_balance_free, ctx.coin_balance_locked, ctx.coin_balance = _get_coin_balance(account_info, ctx.coin_symbol)
                    position_has_crypto = ctx.coin_balance > ctx.min_qty

                    logger.info(f"[DUST] Position nettoyée. Nouveau solde : {ctx.coin_balance:.8f} {ctx.coin_symbol}")
                else:
                    logger.warning("[DUST] Vente du résidu échouée - Binance refuse")
                    logger.info("[DUST] Continuant quand même (résidu < min_qty = position fermée)")
            except Exception as e:
                logger.error(f"[DUST] Erreur lors de la tentative de vente : {e}")
                logger.info("[DUST] Continuant quand même (résidu < min_qty = position fermée)")

    return position_has_crypto


def _execute_signal_sell(ctx: '_TradeCtx') -> None:
    """C-15: Exécute les ventes sur signal final (SIGNAL = vente 100 % du reste).

    Note : les prises de profit partielles (PARTIAL-1, PARTIAL-2) sont désormais
    gérées par _execute_partial_sells() appelée avant _check_and_execute_stop_loss,
    conformément à l'ordre du backtest.
    La synchronisation API des flags partiels est aussi dans _execute_partial_sells().
    """
    ps = ctx.pair_state
    atr_stop_multiplier = config.atr_stop_multiplier

    entry_price_for_panel = ps.get('entry_price') or ctx.row.get('close')
    ps.setdefault('stop_loss_at_entry', entry_price_for_panel - atr_stop_multiplier * (ctx.row.get('atr') or 0.0) if entry_price_for_panel and ctx.row.get('atr') else None)
    ps.setdefault('atr_at_entry', ctx.row.get('atr'))
    ps.setdefault('max_price', entry_price_for_panel)

    check_sell_signal = generate_sell_condition_checker(ctx.best_params)
    sell_triggered, sell_reason = check_sell_signal(ctx.row, ctx.coin_balance, entry_price_for_panel, ctx.current_price, ctx.row.get('atr'))

    # F-2: Protection min hold time — bloquer la vente signal si l'achat est trop récent.
    # On attend au moins 1 bougie complète pour éviter d'acheter et vendre dans la même bougie
    # (incohérence typique lors d'un changement de timeframe WF entre deux exécutions).
    _TF_SECONDS = {'1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400}
    _min_hold = _TF_SECONDS.get(ctx.time_interval, 3600)  # default 1h
    _buy_ts = ps.get('buy_timestamp', 0)
    if sell_triggered and sell_reason == 'SIGNAL' and _buy_ts > 0:
        _held_seconds = time.time() - _buy_ts
        if _held_seconds < _min_hold:
            _remaining_min = (_min_hold - _held_seconds) / 60
            logger.info(
                "[SELL BLOCKED F-2] Vente signal bloquée — position ouverte depuis %.0f min, "
                "min hold = %.0f min (1 bougie %s). Encore %.0f min à attendre.",
                _held_seconds / 60, _min_hold / 60, ctx.time_interval, _remaining_min,
            )
            sell_triggered = False

    # === EXÉCUTION VENTE SIGNAL (100 % du reste) ===
    if sell_triggered and sell_reason == 'SIGNAL':
        try:
            # F-1: Annuler le SL exchange pour libérer les coins lockés avant vente
            _cancel_exchange_sl(ctx)
            qty_to_sell = ctx.coin_balance

            if qty_to_sell and qty_to_sell > 0:
                # Arrondir la quantité selon les règles d'échange
                quantity_decimal = Decimal(str(qty_to_sell))
                quantity_rounded = (quantity_decimal // ctx.step_size_dec) * ctx.step_size_dec
                if quantity_rounded < ctx.min_qty_dec:
                    quantity_rounded = quantity_decimal
                if quantity_rounded > ctx.max_qty_dec:
                    quantity_rounded = ctx.max_qty_dec

                notional_value = float(quantity_rounded) * ctx.current_price

                if quantity_rounded >= ctx.min_qty_dec and notional_value >= ctx.min_notional:
                    qty_str = f"{quantity_rounded:.{ctx.step_decimals}f}"
                    sell_order = safe_market_sell(symbol=ctx.real_trading_pair, quantity=qty_str)
                    if sell_order and sell_order.get('status') == 'FILLED':
                        logger.info(f"[SIGNAL] Vente exécutée et confirmée : {qty_str} {ctx.coin_symbol}")

                        executed_price = ctx.current_price
                        total_usdc_received = float(qty_str) * executed_price

                        sell_type_desc = "Signal de vente (croisement baissier)"
                        position_closed = "100% (solde restant)"

                        # Reset état complet après vente SIGNAL
                        ps.update({
                            'entry_price': None, 'max_price': None, 'stop_loss': None,
                            'trailing_stop': None, 'trailing_stop_activated': False,
                            'atr_at_entry': None, 'stop_loss_at_entry': None,
                            'trailing_activation_price_at_entry': None,
                            'initial_position_size': None,
                            'last_order_side': None,
                            'partial_taken_1': False,
                            'partial_taken_2': False,
                            'breakeven_triggered': False,  # B-3
                            # F-COH: libérer le verrou des params d’entrée
                            'entry_scenario': None, 'entry_timeframe': None,
                            'entry_ema1': None, 'entry_ema2': None,
                        })
                        save_bot_state()
                        logger.info("[SIGNAL] État réinitialisé après vente complète")

                        # === EMAIL VENTE RÉUSSIE ===
                        # Capturer le pnl avant le reset de pair_state (entry_price = None après)
                        _pnl_pct_signal = ((executed_price / entry_price_for_panel) - 1) * 100 if entry_price_for_panel and entry_price_for_panel > 0 else None
                        _pnl_usdc_signal = (float(executed_price) - entry_price_for_panel) * float(qty_str) if entry_price_for_panel and entry_price_for_panel > 0 else None
                        _update_daily_pnl(_pnl_usdc_signal)
                        extra = f"Timeframe : {ctx.time_interval}\nEMA : {ctx.ema1_period}/{ctx.ema2_period}\nScenario : {ctx.scenario}"
                        subj, body = sell_executed_email(
                            pair=ctx.real_trading_pair, qty=float(qty_str), price=executed_price,
                            usdc_received=total_usdc_received, sell_reason=sell_type_desc,
                            pnl_pct=_pnl_pct_signal,
                            extra_details=extra
                        )
                        if is_valid_stop_loss_order(ctx.real_trading_pair, qty_str, executed_price):
                            try:
                                send_trading_alert_email(subject=subj, body_main=body, client=client)
                                logger.info("[SIGNAL] E-mail d'alerte envoyé pour la vente")
                            except Exception as e:
                                logger.error(f"[SIGNAL] L'envoi de l'e-mail a echoué : {e}")
                        else:
                            logger.warning(f"[SIGNAL] Email NON ENVOYÉ : paramètres invalides (symbol={ctx.real_trading_pair}, qty={qty_str}, price={executed_price})")

                        # Rafraîchir le balance après vente
                        account_info = client.get_account()
                        _, ctx.coin_balance, _, _ = _get_coin_balance(account_info, ctx.coin_symbol)

                        # Journal de trading
                        try:
                            logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                            _entry_px = ps.get('entry_price') or 0
                            _pnl = (float(executed_price) - _entry_px) * float(qty_str) if _entry_px and executed_price and qty_str else None
                            _pnl_pct = ((float(executed_price) / _entry_px) - 1) if _entry_px and executed_price else None
                            log_trade(
                                logs_dir=logs_dir,
                                pair=ctx.real_trading_pair,
                                side='sell',
                                quantity=float(qty_str) if qty_str else 0,
                                price=float(executed_price) if executed_price else 0,
                                fee=float(qty_str or 0) * config.taker_fee * float(executed_price or 0),
                                scenario=ctx.scenario,
                                timeframe=ctx.time_interval,
                                pnl=_pnl,
                                pnl_pct=_pnl_pct,
                                extra={'sell_reason': 'SIGNAL', 'position_closed': position_closed},
                            )
                        except Exception as journal_err:
                            logger.error(f"[JOURNAL] Erreur écriture vente: {journal_err}")
                    else:
                        # Signal sell order sent but NOT FILLED
                        _status = sell_order.get('status', 'UNKNOWN') if sell_order else 'None'
                        logger.warning(f"[SIGNAL] Ordre de vente non FILLED (statut={_status})")
                        try:
                            send_trading_alert_email(
                                subject=f"[ALERTE] Vente signal NON FILLED — {ctx.real_trading_pair}",
                                body_main=(
                                    f"Ordre de vente signal non exécuté.\n\n"
                                    f"Paire : {ctx.real_trading_pair}\n"
                                    f"Quantité : {qty_str}\n"
                                    f"Statut : {_status}\n"
                                    f"Prix courant : {ctx.current_price:.4f} USDC\n\n"
                                    f"Action : le bot réessaiera au prochain cycle."
                                ),
                                client=client,
                            )
                        except Exception as _email_err:
                            logger.error(f"[SIGNAL] Échec envoi email vente non-filled: {_email_err}")
                else:
                    if quantity_rounded < ctx.min_qty_dec:
                        logger.warning(f"[SIGNAL] Vente bloquée : Quantité {quantity_rounded} < min_qty {ctx.min_qty_dec}")
                    if notional_value < ctx.min_notional:
                        logger.warning(f"[SIGNAL] Vente bloquée : Valeur {notional_value:.2f} USDC < MIN_NOTIONAL {ctx.min_notional:.2f} USDC")

                    # F-3: Email d'alerte sur vente bloquée
                    try:
                        send_trading_alert_email(
                            subject=f"[ALERTE] Vente signal BLOQUÉE — {ctx.real_trading_pair}",
                            body_main=(
                                f"Vente signal bloquée pour {ctx.real_trading_pair}.\n\n"
                                f"Quantité tentée: {quantity_rounded} (min_qty: {ctx.min_qty_dec})\n"
                                f"Valeur notionnelle: {notional_value:.2f} USDC (min: {ctx.min_notional:.2f})\n"
                                f"Solde total: {ctx.coin_balance:.8f} {ctx.coin_symbol}\n"
                                f"Solde libre: {ctx.coin_balance_free:.8f} {ctx.coin_symbol}\n"
                                f"Solde locké: {ctx.coin_balance_locked:.8f} {ctx.coin_symbol}\n\n"
                                f"Action requise: vérifier les ordres ouverts sur Binance."
                            ),
                            client=client,
                        )
                    except Exception as _email_err:
                        logger.error(f"[SIGNAL] Échec envoi email vente bloquée: {_email_err}")

                        # Si un reliquat < min_qty subsiste, tenter une vente finale
                        remaining_dec = Decimal(str(ctx.coin_balance))
                        if remaining_dec > 0 and remaining_dec < (ctx.min_qty_dec * Decimal('1.02')):
                            try:
                                qty_str_remaining = f"{remaining_dec:.{ctx.step_decimals}f}"
                                logger.info(f"[SELL] Reliquat détecté ({qty_str_remaining}), tentative de vente finale")
                                dust_sell_order = safe_market_sell(symbol=ctx.real_trading_pair, quantity=qty_str_remaining)
                                if dust_sell_order and dust_sell_order.get('status') == 'FILLED':
                                    logger.info(f"[SELL] Reliquat vendu avec succès : {qty_str_remaining} {ctx.coin_symbol}")
                                    ctx.coin_balance = 0.0
                                    ps.update({
                                        'entry_price': None, 'max_price': None, 'stop_loss': None,
                                        'trailing_stop': None, 'trailing_stop_activated': False,
                                        'atr_at_entry': None, 'stop_loss_at_entry': None,
                                        'trailing_activation_price_at_entry': None,
                                        'initial_position_size': None,
                                        'last_order_side': None,
                                        'partial_taken_1': False,
                                        'partial_taken_2': False,
                                        'breakeven_triggered': False,  # B-3
                                        # F-COH: libérer le verrou des params d’entrée
                                        'entry_scenario': None, 'entry_timeframe': None,
                                        'entry_ema1': None, 'entry_ema2': None,
                                    })
                                    save_bot_state()
                                else:
                                    logger.warning("[SELL] Reliquat non vendu (< min_qty)")
                            except Exception as e:
                                logger.error(f"[SELL] Erreur lors de la vente du reliquat: {e}")
        except Exception as e:
            logger.error(f"[VENTE] Erreur lors de l'exécution : {e}")

    # Afficher panel VENTE
    display_sell_signal_panel(
        row=ctx.row, coin_balance=ctx.coin_balance, pair_state=ps,
        sell_triggered=sell_triggered, console=console, coin_symbol=ctx.coin_symbol,
        sell_reason=sell_reason, best_params=ctx.best_params,
    )


def _execute_buy(ctx: '_TradeCtx') -> None:
    """C-15: Exécute un achat avec position sizing et placement SL."""
    ps = ctx.pair_state

    # === CONDITIONS ACHAT (vérifier SEULEMENT si position fermée) ===
    check_buy_signal = generate_buy_condition_checker(ctx.best_params)
    buy_condition, buy_reason = check_buy_signal(ctx.row, ctx.usdc_balance)
    usdc_balance_for_display = ctx.usdc_balance  # snapshot avant éventuel achat

    # === CAPITAL DISPONIBLE POUR ACHAT ===
    usdc_for_buy = get_usdc_from_all_sells_since_last_buy(ctx.real_trading_pair)

    if usdc_for_buy <= 0:
        logger.error(f"[BUY] ERREUR : Aucun capital disponible ! USDC des ventes = {usdc_for_buy:.2f} USDC")
        logger.error("[BUY] Aucune vente trouvée dans l'historique depuis le dernier achat")
        logger.warning("[BUY] Conditions d'achat remplies mais TRADING BLOQUÉ - Capital insuffisant")
    else:
        logger.info(f"[BUY] Capital disponible (ventes depuis dernier BUY) : {usdc_for_buy:.2f} USDC")

    # === EXÉCUTION ACHAT SI CONDITIONS REMPLIES (POSITION SIZING IDENTIQUE AU BACKTEST) ===
    # Sécurité anti-double-buy: si le dernier ordre est déjà un BUY FILLED, on skip ce cycle
    # P0-03: bloquer les nouveaux achats si OOS gates non passées
    if ps.get('oos_blocked'):
        logger.warning(
            "[BUY BLOCKED P0-03] %s — OOS gates non passées depuis %s. "
            "Seule la gestion des stops est active.",
            ctx.backtest_pair,
            time.ctime(ps.get('oos_blocked_since', 0)),
        )
    # A-3: Block buy during cooldown after stop-loss/breakeven exit
    elif ps.get('_stop_loss_cooldown_until', 0) > time.time():
        _cd_remaining = ps.get('_stop_loss_cooldown_until', 0.0) - time.time()
        logger.info(
            "[BUY BLOCKED A-3] %s — Cooldown post-stop-loss actif (%.0f min restantes)",
            ctx.backtest_pair, _cd_remaining / 60,
        )
    elif ctx.orders and check_if_order_executed(ctx.orders, 'BUY'):
        logger.warning("[BUY] Anti-double-buy: dernier ordre détecté comme BUY FILLED – achat ignoré pour ce cycle")
    elif _is_daily_loss_limit_reached():
        logger.warning(
            "[BUY BLOCKED P5-A] %s — Limite perte journalière atteinte. Aucun nouvel achat jusqu'à 00:00 UTC.",
            ctx.backtest_pair,
        )
    elif buy_condition and usdc_for_buy > 0:
        try:
            # POSITION SIZING - 100% IDENTIQUE AU BACKTEST
            atr_value = ctx.row.get('atr', None)
            entry_price = ctx.current_price

            # P0-SL-GUARD: Bloquer l'achat si ATR absent/NaN/≤0 → stop-loss incalculable
            import math
            _atr_invalid = (
                atr_value is None
                or (isinstance(atr_value, float) and math.isnan(atr_value))
                or float(atr_value) <= 0
            )
            if _atr_invalid:
                logger.warning(
                    "[BUY BLOCKED P0-SL-GUARD] %s — ATR indisponible (atr=%s). "
                    "Impossible de calculer un stop-loss. Achat refusé.",
                    ctx.real_trading_pair, atr_value,
                )
                return

            # Optimisation sniper: chercher le meilleur prix d'entrée sur les 15min récents
            optimized_entry = get_sniper_entry_price(ctx.real_trading_pair, entry_price)
            if optimized_entry < entry_price:
                logger.info(f"[BUY] Prix sniper optimisé: {optimized_entry:.6f} (vs spot {entry_price:.6f}, gain {(entry_price - optimized_entry) / entry_price * 100:.2f}%)")
                entry_price = optimized_entry

            if ctx.sizing_mode == 'baseline':
                effective_capital = min(usdc_for_buy, ctx.usdc_balance) * 0.98
                gross_coin = effective_capital / entry_price
            elif ctx.sizing_mode == 'risk':
                # RISK-BASED: % risk avec ATR stop-loss
                if atr_value and atr_value > 0:
                    qty_by_risk = compute_position_size_by_risk(
                        equity=usdc_for_buy,
                        atr_value=atr_value,
                        entry_price=entry_price,
                        risk_pct=config.risk_per_trade,
                        stop_atr_multiplier=config.atr_stop_multiplier
                    )
                    effective_capital = min(usdc_for_buy, ctx.usdc_balance) * 0.98
                    max_affordable = effective_capital / entry_price
                    gross_coin = min(max_affordable, qty_by_risk)
                else:
                    logger.warning("[BUY] ATR invalide, fallback baseline")
                    effective_capital = min(usdc_for_buy, ctx.usdc_balance) * 0.98
                    gross_coin = effective_capital / entry_price
            elif ctx.sizing_mode == 'fixed_notional':
                # Fixed notional: montant USD fixe par trade
                notional_per_trade = usdc_for_buy * 0.1  # 10% du capital pour ce trade
                qty_fixed = compute_position_size_fixed_notional(
                    equity=usdc_for_buy,
                    notional_per_trade_usd=notional_per_trade,
                    entry_price=entry_price
                )
                effective_capital = min(usdc_for_buy, ctx.usdc_balance) * 0.98
                max_affordable = effective_capital / entry_price
                gross_coin = min(max_affordable, qty_fixed)
            elif ctx.sizing_mode == 'volatility_parity':
                # Volatility parity: volatilité fixe du P&L
                if atr_value and atr_value > 0:
                    qty_vol = compute_position_size_volatility_parity(
                        equity=usdc_for_buy,
                        atr_value=atr_value,
                        entry_price=entry_price,
                        target_volatility_pct=0.02
                    )
                    effective_capital = min(usdc_for_buy, ctx.usdc_balance) * 0.98
                    max_affordable = effective_capital / entry_price
                    gross_coin = min(max_affordable, qty_vol)
                else:
                    logger.warning("[BUY] ATR invalide, fallback baseline")
                    effective_capital = min(usdc_for_buy, ctx.usdc_balance) * 0.98
                    gross_coin = effective_capital / entry_price
            else:
                logger.warning(f"[BUY] sizing_mode inconnu '{ctx.sizing_mode}', fallback baseline")
                effective_capital = min(usdc_for_buy, ctx.usdc_balance) * 0.98
                gross_coin = effective_capital / entry_price

            # Arrondir selon les règles d'échange
            quantity_decimal = Decimal(str(gross_coin))
            quantity_rounded = (quantity_decimal // ctx.step_size_dec) * ctx.step_size_dec
            if quantity_rounded < ctx.min_qty_dec:
                quantity_rounded = quantity_decimal
            if quantity_rounded > ctx.max_qty_dec:
                quantity_rounded = ctx.max_qty_dec

            if quantity_rounded >= ctx.min_qty_dec:
                qty_str = f"{quantity_rounded:.{ctx.step_decimals}f}"
                quote_amount = float(quantity_rounded) * entry_price

                logger.info(f"[BUY] Sizing mode: {ctx.sizing_mode}")
                logger.info(f"[BUY] Quantité calculée: {qty_str} {ctx.coin_symbol} (~{quote_amount:.2f} USDC)")

                buy_order = safe_market_buy(symbol=ctx.real_trading_pair, quoteOrderQty=quote_amount)
                if buy_order and buy_order.get('status') == 'FILLED':
                    # Quantité nette réellement reçue
                    try:
                        _exec_raw = Decimal(str(buy_order.get('executedQty', qty_str)))
                        _fills = buy_order.get('fills', [])
                        _commission = sum(
                            Decimal(str(f.get('commission', '0')))
                            for f in _fills
                            if str(f.get('commissionAsset', '')).upper() == ctx.coin_symbol.upper()
                        )
                        _net_qty = _exec_raw - _commission
                        _exec_snapped = (_net_qty // ctx.step_size_dec) * ctx.step_size_dec
                        actual_qty_str = f"{_exec_snapped:.{ctx.step_decimals}f}"
                        if _commission > 0:
                            logger.info(
                                "[BUY] Commission déduite en %s: %s → quantité nette: %s",
                                ctx.coin_symbol, _commission, actual_qty_str,
                            )
                    except Exception:
                        actual_qty_str = qty_str
                    logger.info(f"[BUY] Achat exécuté et confirmé : {actual_qty_str} {ctx.coin_symbol}")
                    logger.info(f"[BUY] Capital utilisé : {usdc_for_buy:.2f} USDC (provenant des ventes)")
                    logger.info(f"[BUY] Quantité réellement exécutée : {buy_order.get('executedQty', 'N/A')} {ctx.coin_symbol}")

                    # === EMAIL ACHAT RÉUSSI ===
                    _sl_at_entry = entry_price - (config.atr_stop_multiplier * atr_value) if atr_value else None
                    _sl_str = f"{_sl_at_entry:.4f} USDC" if _sl_at_entry else "N/A"
                    # Solde USDC après achat = solde avant - montant réellement dépensé
                    # (ctx.usdc_balance sera mis à jour par le refresh API plus bas, trop tard)
                    _usdc_after = max(ctx.usdc_balance - quote_amount, 0.0)
                    # Risque effectif au SL (peut être < risque cible si capital insuffisant pour sizing plein)
                    _sl_dist_email = entry_price - float(_sl_at_entry) if _sl_at_entry else None
                    if _sl_dist_email and _sl_dist_email > 0 and usdc_for_buy > 0:
                        _effective_risk_pct = float(actual_qty_str) * _sl_dist_email / usdc_for_buy * 100
                    else:
                        _effective_risk_pct = config.risk_per_trade * 100
                    extra = (
                        f"Timeframe : {ctx.time_interval}\nEMA : {ctx.ema1_period}/{ctx.ema2_period}"
                        f"\nScenario : {ctx.scenario}"
                        f"\nStop-Loss : {_sl_str}"
                        f"\nRisque max : {_effective_risk_pct:.1f}% du capital"
                    )
                    subj, body = buy_executed_email(
                        pair=ctx.real_trading_pair, qty=float(actual_qty_str), price=entry_price,
                        usdc_spent=quote_amount, usdc_balance_after=_usdc_after,
                        extra_details=extra
                    )
                    try:
                        send_trading_alert_email(subject=subj, body_main=body, client=client)
                        logger.info("[BUY] E-mail d'alerte envoye pour l'achat")
                    except Exception as e:
                        logger.error(f"[BUY] L'envoi de l'e-mail a echoue : {e}")

                    # Vérifier si la position permet des partials sûrs
                    can_partial = can_execute_partial_safely(
                        coin_balance=float(quantity_rounded),
                        current_price=entry_price,
                        min_notional=ctx.min_notional
                    )
                    ps.update({
                        'entry_price': entry_price,
                        'atr_at_entry': atr_value,
                        'stop_loss_at_entry': entry_price - (config.atr_stop_multiplier * atr_value) if atr_value else None,
                        'trailing_activation_price_at_entry': entry_price + (config.atr_multiplier * atr_value) if atr_value else entry_price * (1 + config.trailing_activation_pct),
                        'max_price': entry_price,
                        'trailing_stop_activated': False,
                        'initial_position_size': float(quantity_rounded),
                        'last_order_side': 'BUY',
                        'partial_enabled': can_partial,
                        'partial_taken_1': False,
                        'partial_taken_2': False,
                        'breakeven_triggered': False,  # B-3: réinitialiser à l'achat
                        'buy_timestamp': time.time(),  # F-2: timestamp achat pour min hold time
                        # F-COH: verrouiller les params d’entrée pour garantir la cohérence
                        # signal achat ↔ signal vente (même scenario/TF/EMA jusqu’à clôture)
                        'entry_scenario': ctx.scenario,
                        'entry_timeframe': ctx.time_interval,
                        'entry_ema1': ctx.ema1_period,
                        'entry_ema2': ctx.ema2_period,
                    })
                    save_bot_state()

                    # P0-01: Placer immédiatement un STOP_LOSS_LIMIT sur l'exchange après achat
                    # P0-STOP: retry SL 3x avant rollback, kill-switch si double échec
                    _sl_price = ps.get('stop_loss_at_entry')
                    if _sl_price and float(_sl_price) > 0:
                        _sl_placed = False
                        _sl_last_err = None
                        for _sl_attempt in range(3):
                            try:
                                _sl_result = place_exchange_stop_loss_order(
                                    symbol=ctx.real_trading_pair,
                                    quantity=actual_qty_str,
                                    stop_price=float(_sl_price),
                                )
                                ps['sl_order_id'] = _sl_result.get('orderId')
                                ps['sl_exchange_placed'] = True
                                save_bot_state()
                                logger.info(
                                    "[SL-ORDER P0-01] Stop-loss exchange placé (tentative %d): orderId=%s stop=%.8f",
                                    _sl_attempt + 1, ps['sl_order_id'], _sl_price,
                                )
                                _sl_placed = True
                                break
                            except Exception as _sl_err:
                                _sl_last_err = _sl_err
                                logger.warning(
                                    "[SL-ORDER P0-STOP] Tentative %d/3 échec SL pour %s: %s",
                                    _sl_attempt + 1, ctx.real_trading_pair, _sl_err,
                                )
                                if _sl_attempt < 2:
                                    time.sleep(1.5 * (2 ** _sl_attempt) + random.random())

                        if not _sl_placed:
                            logger.critical(
                                "[SL-ORDER P0-STOP] ÉCHEC placement stop-loss après 3 tentatives pour %s: %s — "
                                "ROLLBACK: market-sell d'urgence en cours.",
                                ctx.real_trading_pair, _sl_last_err,
                            )
                            try:
                                send_trading_alert_email(
                                    subject=f"[CRITIQUE P0-STOP] Stop-loss non placé {ctx.real_trading_pair}",
                                    body_main=(
                                        f"Le stop-loss exchange n'a pas pu être placé après 3 tentatives\n"
                                        f"Paire: {ctx.real_trading_pair}\nDernière erreur: {_sl_last_err}\n\n"
                                        f"ROLLBACK: market-sell d'urgence en cours."
                                    ),
                                    client=client,
                                )
                            except Exception as _e:
                                logger.warning("[SL-ORDER] Email alerte SL impossible: %s", _e)
                            try:
                                safe_market_sell(symbol=ctx.real_trading_pair, quantity=actual_qty_str)
                                ps.update({
                                    'entry_price': None,
                                    'last_order_side': 'SELL',
                                    'sl_order_id': None,
                                    'sl_exchange_placed': False,
                                })
                                save_bot_state()
                                logger.critical(
                                    "[SL-ORDER P0-STOP] Rollback (market-sell) exécuté pour %s.",
                                    ctx.real_trading_pair,
                                )
                            except Exception as _rollback_err:
                                # P0-STOP: Double échec (SL + rollback) → EMERGENCY HALT
                                logger.critical(
                                    "[SL-ORDER P0-STOP] ROLLBACK AUSSI ÉCHOUÉ pour %s: %s — "
                                    "ACTIVATION EMERGENCY HALT — POSITION EXPOSÉE SANS PROTECTION !",
                                    ctx.real_trading_pair, _rollback_err,
                                )
                                with _bot_state_lock:
                                    bot_state['emergency_halt'] = True
                                    bot_state['emergency_halt_reason'] = (
                                        f"Double échec SL+rollback pour {ctx.real_trading_pair} "
                                        f"à {datetime.now().isoformat()}"
                                    )
                                save_bot_state(force=True)
                                try:
                                    send_trading_alert_email(
                                        subject=f"[EMERGENCY HALT] Position exposée {ctx.real_trading_pair}",
                                        body_main=(
                                            f"ALERTE CRITIQUE: Le stop-loss ET le market-sell de rollback "
                                            f"ont tous deux échoué.\n\n"
                                            f"Paire: {ctx.real_trading_pair}\n"
                                            f"Erreur SL: {_sl_last_err}\n"
                                            f"Erreur rollback: {_rollback_err}\n\n"
                                            f"EMERGENCY HALT ACTIVÉ — Tous les achats sont bloqués.\n"
                                            f"ACTION REQUISE: Vérifier la position manuellement sur Binance "
                                            f"et supprimer la clé 'emergency_halt' du bot_state pour relancer."
                                        ),
                                        client=client,
                                    )
                                except Exception as _e:
                                    logger.warning("[SL-ORDER] Email emergency halt impossible: %s", _e)
                    else:
                        # P0-SL-GUARD: Ce chemin ne devrait JAMAIS être atteint grâce au guard
                        # en amont. Si on arrive ici, c'est un bug — rollback immédiat.
                        logger.critical(
                            "[SL-ORDER P0-SL-GUARD] stop_loss_at_entry non défini pour %s "
                            "(atr=%s). BUG: le guard pré-achat n'a pas bloqué. ROLLBACK.",
                            ctx.real_trading_pair, ps.get('atr_at_entry'),
                        )
                        try:
                            safe_market_sell(symbol=ctx.real_trading_pair, quantity=actual_qty_str)
                            ps.update({
                                'entry_price': None,
                                'last_order_side': 'SELL',
                                'sl_order_id': None,
                                'sl_exchange_placed': False,
                            })
                            save_bot_state()
                            logger.critical(
                                "[SL-ORDER P0-SL-GUARD] Rollback (market-sell) exécuté pour %s.",
                                ctx.real_trading_pair,
                            )
                        except Exception as _guard_err:
                            logger.critical(
                                "[SL-ORDER P0-SL-GUARD] ROLLBACK ÉCHOUÉ pour %s: %s — "
                                "POSITION EXPOSÉE SANS STOP-LOSS !",
                                ctx.real_trading_pair, _guard_err,
                            )
                            with _bot_state_lock:
                                bot_state['emergency_halt'] = True
                                bot_state['emergency_halt_reason'] = (
                                    f"SL impossible + rollback échoué pour {ctx.real_trading_pair} "
                                    f"à {datetime.now().isoformat()}"
                                )
                            save_bot_state(force=True)
                        try:
                            send_trading_alert_email(
                                subject=f"[CRITIQUE P0-SL-GUARD] ATR null après achat {ctx.real_trading_pair}",
                                body_main=(
                                    f"Le stop-loss n'a pas pu être calculé (ATR absent après achat).\n"
                                    f"Paire: {ctx.real_trading_pair}\n"
                                    f"ATR: {ps.get('atr_at_entry')}\n\n"
                                    f"Rollback market-sell tenté automatiquement."
                                ),
                                client=client,
                            )
                        except Exception as _e:
                            logger.warning("[SL-GUARD] Email alerte rollback impossible: %s", _e)

                    # Journal de trading (Phase 2)
                    try:
                        logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                        log_trade(
                            logs_dir=logs_dir,
                            pair=ctx.real_trading_pair,
                            side='buy',
                            quantity=float(quantity_rounded),
                            price=entry_price,
                            fee=float(quantity_rounded) * config.taker_fee * entry_price,
                            slippage=config.slippage_buy,
                            scenario=ctx.scenario,
                            timeframe=ctx.time_interval,
                            ema1=ctx.best_params.get('ema1_period'),
                            ema2=ctx.best_params.get('ema2_period'),
                            atr_value=atr_value,
                            stop_price=ps.get('stop_loss_at_entry'),
                            equity_before=ctx.usdc_balance,
                        )
                    except Exception as journal_err:
                        logger.error(f"[JOURNAL] Erreur écriture achat: {journal_err}")

                    # Rafraîchir le balance après achat
                    account_info = client.get_account()
                    # C-13: helper centralisé
                    _, ctx.usdc_balance, _, _ = _get_coin_balance(account_info, 'USDC')
                else:
                    # Buy order sent but NOT FILLED
                    _status = buy_order.get('status', 'UNKNOWN') if buy_order else 'None'
                    logger.warning(f"[BUY] Ordre d'achat non FILLED (statut={_status})")
                    try:
                        send_trading_alert_email(
                            subject=f"[ALERTE] Achat NON FILLED — {ctx.real_trading_pair}",
                            body_main=(
                                f"Ordre d'achat non exécuté.\n\n"
                                f"Paire : {ctx.real_trading_pair}\n"
                                f"Quantité : {qty_str}\n"
                                f"Montant : {quote_amount:.2f} USDC\n"
                                f"Statut : {_status}\n"
                                f"Prix courant : {ctx.current_price:.4f} USDC\n\n"
                                f"Action : le bot réessaiera au prochain cycle."
                            ),
                            client=client,
                        )
                    except Exception as _email_err:
                        logger.error(f"[BUY] Échec envoi email achat non-filled: {_email_err}")
            else:
                logger.warning(f"[BUY] Quantité {quantity_rounded} < min_qty {ctx.min_qty_dec}, achat annulé")
        except Exception as e:
            logger.error(f"[ACHAT] Erreur lors de l'exécution : {e}")

    # Afficher panel ACHAT (avec le solde AVANT achat si achat exécuté)
    display_buy_signal_panel(
        row=ctx.row, usdc_balance=usdc_balance_for_display, best_params=ctx.best_params,
        scenario=ctx.scenario, buy_condition=buy_condition, console=console,
        pair_state=ps, buy_reason=buy_reason
    )


# ──────────────────────────────────────────────────────────────────────────────
# P3-04: helpers extraits de _execute_real_trades_inner pour réduire la complexité
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_balances(real_trading_pair: str) -> Optional[Tuple[Any, str, str, float, float, float, float]]:
    """Récupère les soldes coin + quote pour la paire donnée.

    Returns:
        (account_info, coin_symbol, quote_currency,
         usdc_balance, coin_balance_free, coin_balance_locked, coin_balance)
        ou None si le coin n'est pas trouvé.
    """
    account_info = client.get_account()
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

    row = df.iloc[-2]

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
            # Inject into the row as a mutable copy
            row = row.copy()
            row['mtf_bullish'] = _bullish_1h.iloc[-2] if len(_bullish_1h) >= 2 else 0.0
        except Exception as _mtf_err:
            logger.warning("[A-2] MTF computation failed: %s — filter disabled for this cycle", _mtf_err)

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

    # pair_state dérivé depuis bot_state — les mutations du dict se propagent par référence.
    with _bot_state_lock:
        pair_state: PairState = cast('PairState', bot_state.setdefault(backtest_pair, {}))
    if 'last_order_side' not in pair_state:
        pair_state['last_order_side'] = None

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
            return
        (account_info, coin_symbol, quote_currency,
         usdc_balance, coin_balance_free, coin_balance_locked, coin_balance) = bal

        # === P3-04: FILTRES PAIRE (helper) ===
        flt = _fetch_symbol_filters(real_trading_pair)
        if flt is None:
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

        # === P3-04: HISTORIQUE ORDRES (helper) ===
        orders, last_side = _sync_order_history(real_trading_pair, pair_state)

        # === C-15: BUILD CONTEXT + DELEGATE TO SUB-FUNCTIONS ===
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

        _sync_entry_state(ctx, last_side)
        _update_trailing_stop(ctx)
        _execute_partial_sells(ctx)
        if _check_and_execute_stop_loss(ctx):
            return

        # Initial position size tracking
        if pair_state.get('last_order_side') == 'BUY' and pair_state.get('initial_position_size') is None and ctx.coin_balance > min_qty * 1.01:
            pair_state['initial_position_size'] = ctx.coin_balance
            save_bot_state()

        position_has_crypto = _handle_dust_cleanup(ctx)
        if position_has_crypto:
            _execute_signal_sell(ctx)
        else:
            _execute_buy(ctx)

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
    """
    Effectue les backtests pour differents timeframes, affiche les resultats,
    et identifie les meilleurs parametres pour le trading en temps reel.

    IMPORTANT: start_date sera recalcule dynamiquement a chaque appel pour toujours
    utiliser une fenetre glissante de 5 ans depuis aujourd'hui.
    """
    # Recalculer start_date dynamiquement a chaque execution (fenetre glissante 5 ans)
    dynamic_start_date = (datetime.today() - timedelta(days=config.backtest_days)).strftime("%d %B %Y")

    console = Console()

    # DETECTION INTELLIGENTE DES CHANGEMENTS DE MARCHE
    console.print("\n[bold cyan][ANALYZE] Analyse des changements du marche...[/bold cyan]")
    market_changes = detect_market_changes(backtest_pair, timeframes, dynamic_start_date)
    display_market_changes(market_changes, backtest_pair, console=console)

    logger.info(f"Backtest period: 5 years from today | Start date: {dynamic_start_date}")

    # COMPENSATION BINANCE ULTRA-ROBUSTE A CHAQUE BACKTEST
    logger.info("Compensation timestamp Binance ultra-robuste active")

    logger.info("Debut des backtests...")

    if backtest_pair not in bot_state:
        bot_state[backtest_pair] = _make_default_pair_state()

    pair_state: PairState = cast('PairState', bot_state[backtest_pair])

    try:
        results = run_all_backtests(backtest_pair, dynamic_start_date, timeframes, sizing_mode=sizing_mode)
    except Exception as e:
        logger.error(f"Une erreur est survenue pendant les backtests : {e}")
        return

    if not results:
        logger.error("Aucune donnee de backtest n'a ete generee")
        return

    # === WALK-FORWARD VALIDATION (Phase 2) ===
    wf_result: Dict[str, Any] = {}
    try:
        from walk_forward import run_walk_forward_validation
        # Recréer base_dataframes pour WF (données déjà en cache)
        wf_base_dataframes = {}
        for tf in timeframes:
            df_wf = prepare_base_dataframe(backtest_pair, tf, dynamic_start_date, 14)
            wf_base_dataframes[tf] = df_wf if df_wf is not None and not df_wf.empty else pd.DataFrame()

        wf_result = run_walk_forward_validation(
            base_dataframes=wf_base_dataframes,
            full_sample_results=results,
            scenarios=WF_SCENARIOS,
            backtest_fn=backtest_from_dataframe,
            initial_capital=config.initial_wallet,
            sizing_mode=sizing_mode,
        )

        if wf_result.get('any_passed'):
            console.print(Panel(
                f"[bold green]Walk-Forward Validation PASSED[/bold green]\n"
                f"Meilleure config WF: {wf_result.get('best_wf_config', {}).get('scenario', 'N/A')} "
                f"({wf_result.get('best_wf_config', {}).get('timeframe', 'N/A')})\n"
                f"OOS Sharpe moyen: {wf_result.get('best_wf_config', {}).get('avg_oos_sharpe', 0):.2f}",
                title="[bold cyan]Walk-Forward Validation[/bold cyan]",
                border_style="green", width=PANEL_WIDTH
            ))
        else:
            console.print(Panel(
                "[bold yellow]Walk-Forward Validation: aucune config n'a passé les quality gates OOS[/bold yellow]\n"
                "[dim]Utilisation du meilleur résultat full-sample (mode dégradé)[/dim]",
                title="[bold cyan]Walk-Forward Validation[/bold cyan]",
                border_style="yellow", width=PANEL_WIDTH
            ))
    except Exception as wf_err:
        logger.warning(f"[WF] Walk-forward validation skipped: {wf_err}")

    # Identifier le meilleur résultat — C-13 + P2-05: OOS quality gate centralisée
    _pool_main, _ = apply_oos_quality_gate(
        results, backtest_pair,
        log_tag="MAIN C-13",
    )

    # P2-01: utiliser la config Walk-Forward (OOS) en priorité → élimine le biais look-ahead.
    # Si aucune config WF ne passe les quality gates, fallback sur IS-Calmar.
    _wf_best_cfg = None
    try:
        _wf_best_cfg = wf_result.get('best_wf_config') if wf_result.get('any_passed') else None
    except Exception as _e:
        logger.warning("[WF] Impossible de récupérer best_wf_config: %s", _e)
        _wf_best_cfg = None

    if _wf_best_cfg:
        logger.info(
            "[MAIN P2-01] Sélection Walk-Forward OOS: %s EMA(%s,%s) %s — "
            "OOS Sharpe=%.2f (look-ahead éliminé).",
            _wf_best_cfg['scenario'],
            _wf_best_cfg['ema_periods'][0],
            _wf_best_cfg['ema_periods'][1],
            _wf_best_cfg['timeframe'],
            _wf_best_cfg.get('avg_oos_sharpe', 0.0),
        )
        best_params = {
            'timeframe': _wf_best_cfg['timeframe'],
            'ema1_period': _wf_best_cfg['ema_periods'][0],
            'ema2_period': _wf_best_cfg['ema_periods'][1],
            'scenario': _wf_best_cfg['scenario'],
        }
        best_params.update(SCENARIO_DEFAULT_PARAMS.get(_wf_best_cfg['scenario'], {}))
    else:
        best_params = {
            'timeframe': '1d',  # conservative default
            'ema1_period': 26,
            'ema2_period': 50,
            'scenario': 'StochRSI',
        }
        best_params.update(SCENARIO_DEFAULT_PARAMS.get('StochRSI', {}))
        logger.warning(
            "[MAIN P1-WF] Aucun résultat WF valide — paramètres CONSERVATIFS par défaut "
            "(EMA 26/50, StochRSI, 1d). Les achats restent bloqués par P0-03/oos_blocked."
        )

    # Afficher les resultats
    display_backtest_table(backtest_pair, results, console)

    # Execution des ordres reels avec les meilleurs parametres

    # Mise a jour de l'etat du bot
    pair_state['last_best_params'] = best_params
    pair_state['execution_count'] = pair_state.get('execution_count', 0) + 1
    save_bot_state()

    console.print("\n")
    try:
        # POSITION SIZING: utiliser le mode passé en paramètre
        execute_real_trades(real_trading_pair, best_params['timeframe'], best_params, backtest_pair, sizing_mode=sizing_mode)
    except Exception as e:
        logger.error(f"Une erreur est survenue lors de l'execution des ordres en reel: {e}")
        subj, body = trading_execution_error_email(str(e), traceback.format_exc())
        send_email_alert(subject=subj, body=body)

    # Gestion de l'historique d'execution
    current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Panel pour l'historique et la planification
    pair_state['last_run_time'] = current_run_time

    # SOLUTION DEFINITIVE - Éviter les planifications multiples
    # Vérifier si une tâche existe déjà pour cette paire
    existing_job = None
    for job in schedule.jobs:
        try:
            job_func = job.job_func
            if job_func is not None and hasattr(job_func, 'args') and len(job_func.args) >= 2 and job_func.args[0] == backtest_pair:
                existing_job = job
                break
        except Exception:
            continue

    # Si une tâche existe déjà, la supprimer
    if existing_job:
        schedule.cancel_job(existing_job)
        logger.info(f"Ancienne planification supprimée pour {backtest_pair}")

    # Programmer une tâche UNIQUE et HOMOGENE toutes les 2 minutes (indépendant du timeframe)
    # IMPORTANT: recalculer start_date à CHAQUE exécution pour conserver une fenêtre glissante de 5 ans
    schedule.every(config.schedule_interval_minutes).minutes.do(  # P2-02: intervalle configurable
        lambda bp=backtest_pair, rp=real_trading_pair, tfs=timeframes, sm=sizing_mode: backtest_and_display_results(
            bp,
            rp,
            (datetime.today() - timedelta(days=config.backtest_days)).strftime("%d %B %Y"),
            tfs,
            sm
        )
    )

    console.print(build_tracking_panel(pair_state, current_run_time))
    console.print("\n")

# cleanup_expired_cache importé depuis cache_manager.py (Phase 4)

# check_admin_privileges moved to timestamp_utils.py (P3-SRP)

if __name__ == "__main__":

    full_timestamp_resync()
    logger.info("Synchronisation complète exécutée au démarrage.")

    # MODE ULTRA-ROBUSTE SANS POPUP - COMPENSATION BINANCE PURE
    logger.info("Bot crypto H24/7 - Mode ultra-robuste avec privileges admin")

    crypto_pairs = [
        {"backtest_pair": "SOLUSDT", "real_pair": "SOLUSDC"},
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

        # Récupération des frais réels depuis l'API Binance (override config defaults)
        real_taker, real_maker = get_binance_trading_fees(client)
        with _bot_state_lock:  # P1-04: protéger la mutation du singleton config
            config.taker_fee = real_taker
            config.maker_fee = real_maker

        # Chargement de l'etat du bot
        load_bot_state()

        # C-03: Réconciliation positions au démarrage — détecte les positions orphelines
        # (achat exécuté avant un crash, état non sauvegardé)
        try:
            reconcile_positions_with_exchange(crypto_pairs)
        except Exception as reconcile_err:
            logger.error(f"[RECONCILE] Erreur lors de la réconciliation: {reconcile_err}")

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
                    _live_best_params[backtest_pair] = dict(best_params)
                # Marquer le timestamp du backtest initial pour que le 1er horaire ne
                # relance pas immédiatement un backtest complet.
                with _bot_state_lock:
                    _last_backtest_time[backtest_pair] = time.time()
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

        def _graceful_shutdown(signum: int, frame: Any) -> None:
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
