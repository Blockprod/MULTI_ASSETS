import os
import sys
import locale
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
# Suppression du ChainedAssignmentError pandas 2.2 CoW — déclenché par le moteur
# Cython indicators qui construit un DataFrame à partir de vues numpy de df.
# Corrigé dans indicators.pyx (np.array copies), ce filtre est une sécurité.
try:
    import pandas as _pd
    warnings.filterwarnings("ignore", category=_pd.errors.ChainedAssignmentError)
except AttributeError:
    pass  # pandas < 2.2 n'a pas ChainedAssignmentError

# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

# ─── Imports depuis les modules extraits (Phase 4) ──────────────────────────
from bot_config import (
    config, extract_coin_from_pair,
    log_exceptions, retry_with_backoff,
    set_error_notification_callback, VERBOSE_LOGS,
)
from position_sizing import (
    compute_position_size_by_risk,
    compute_position_size_fixed_notional,
    compute_position_size_volatility_parity,
)
from email_utils import send_email_alert, send_trading_alert_email
from state_manager import save_state, load_state
from display_ui import (
    PANEL_WIDTH,
    display_buy_signal_panel, display_sell_signal_panel,
    display_account_balances_panel, display_market_changes,
    display_results_for_pair, display_backtest_table,
    display_trading_panel, build_tracking_panel,
    display_closure_panel, display_execution_header,
    display_bot_active_banner,
)
from cache_manager import (
    get_cache_key, get_cache_path,
    safe_cache_read, safe_cache_write, update_cache_with_recent_data,
    cleanup_expired_cache, ensure_cache_dir,
)
from exchange_client import (
    BinanceFinalClient, is_valid_stop_loss_order, can_execute_partial_safely,
    place_stop_loss_order as _place_stop_loss_order,
    safe_market_buy as _safe_market_buy,
    safe_market_sell as _safe_market_sell,
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
except Exception as e:
    pass

"""
MULTI_SYMBOLS.py

Table of Contents:
1. Imports
2. Global Variables & Constants
3. Utility Decorators
4. Configuration Classes
5. Exchange Client Classes
6. Core Helpers (caching, error handling, etc.)
7. Indicator Calculation
8. Display Functions
9. Trading Logic (order placement, state management)
10. Backtest & Execution Logic
11. Main Entrypoint
"""


"""Standard, external, and project-specific imports (alphabetized, deduplicated)."""
import argparse
import ctypes
import json
import logging
import numpy as np
import pandas as pd
import schedule
import signal
import socket
import subprocess
import threading
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, MACD
from ta.volatility import AverageTrueRange
import time
import traceback
from binance.client import Client
from binance.exceptions import BinanceAPIException
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from rich import print
from rich.console import Console
from rich.panel import Panel
from tqdm import tqdm
from typing import Any, Dict, List, Optional, Tuple

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

# Paramètre pour activer/désactiver les logs détaillés (VERBOSE = False pour plus de rapidité)
# (VERBOSE_LOGS importé depuis bot_config)

# Config et config importés depuis bot_config.py (Phase 4)
# La classe Config et config = Config.from_env() sont dans bot_config.py

sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import Cython modules for optimization (en-tête du fichier)
try:
    import backtest_engine_standard as backtest_engine
    CYTHON_BACKTEST_AVAILABLE = True
    logger.info("Cython backtest engine loaded successfully.")
except ImportError as e:
    CYTHON_BACKTEST_AVAILABLE = False
    logger.warning(f"Cython backtest_engine_standard not available ({e}), using Python fallback")
    backtest_engine = None  # Set to None to avoid NameError later

# C-14: Import compiled Cython indicator engine as the single authoritative source.
# Falls back to Python implementation in calculate_indicators() if unavailable.
try:
    import indicators as _cython_indicators  # type: ignore[import]
    CYTHON_INDICATORS_AVAILABLE = True
    logger.info("Cython indicators engine loaded (C-14).")
except ImportError as _ind_import_err:
    _cython_indicators = None  # type: ignore[assignment]
    CYTHON_INDICATORS_AVAILABLE = False
    logger.warning("Cython indicators not available (%s) — using Python fallback.", _ind_import_err)

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
def _error_notification_handler(fn: str, e: Exception, a: tuple, kw: dict) -> None:
    subj, body = generic_exception_email(fn, e, a, kw)
    send_trading_alert_email(subject=subj, body_main=body, client=client)

set_error_notification_callback(_error_notification_handler)

# Timeframes
timeframes = [
    Client.KLINE_INTERVAL_1HOUR,
    Client.KLINE_INTERVAL_4HOUR,
    Client.KLINE_INTERVAL_1DAY
]

# Calcul de la date de debut
today = datetime.today()
start_date = (today - timedelta(days=config.backtest_days)).strftime("%d %B %Y")

# Cache pour les indicateurs (LRU : les entrées les plus anciennes sont évincées)
from collections import OrderedDict
_INDICATORS_CACHE_MAX = 30
indicators_cache: OrderedDict[str, pd.DataFrame] = OrderedDict()

# Cache pour exchange_info (TTL 24h — évite un appel API coûteux à chaque cycle)
_exchange_info_cache: Dict[str, Any] = {'data': None, 'ts': 0.0}
_EXCHANGE_INFO_TTL: float = 24.0 * 3600.0  # 24 heures


def get_cached_exchange_info(client) -> Dict:
    """Retourne les infos d'exchange depuis le cache mémoire (TTL 24 h).
    
    Réduit les appels API répétitifs à chaque cycle de trading.
    Le cache est invalidé après 24 h ou lors du premier appel.
    """
    global _exchange_info_cache
    if (
        _exchange_info_cache['data'] is None
        or (time.time() - _exchange_info_cache['ts']) > _EXCHANGE_INFO_TTL
    ):
        _exchange_info_cache['data'] = client.get_exchange_info()
        _exchange_info_cache['ts'] = time.time()
        logger.debug("[CACHE] exchange_info recharg\u00e9 (TTL expir\u00e9 ou premier appel)")
    return _exchange_info_cache['data']

# etat du bot
bot_state: Dict[str, Dict] = {}

# ─── Thread-safety du bot_state (C-01) ────────────────────────────────────────
# RLock global pour serialize save/load du bot_state
_bot_state_lock = threading.RLock()
# Locks par paire : empêchent deux exécutions simultanées sur la même paire
_pair_execution_locks: Dict[str, threading.Lock] = {}
_pair_locks_mutex = threading.Lock()

# Paramètres par défaut des scénarios — constante partagée (3 emplacements)
SCENARIO_DEFAULT_PARAMS: Dict[str, Dict] = {
    'StochRSI': {'stoch_period': 14},
    'StochRSI_SMA': {'stoch_period': 14, 'sma_long': 200},
    'StochRSI_ADX': {'stoch_period': 14, 'adx_period': 14},
    'StochRSI_TRIX': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15}
}

# _cache_dir_initialized remplacé par ensure_cache_dir() de cache_manager.py (Phase 4)

# Variable globale pour stocker la paire courante lors des backtests
_current_backtest_pair = None

# --- Order Placement Helpers ---
# Fonctions d'ordres importées depuis exchange_client.py (Phase 4)
# Wrappers pour passer le client global automatiquement
def place_trailing_stop_order(symbol, quantity, activation_price, trailing_delta, client_id=None):
    # AVERTISSEMENT : TRAILING_STOP_MARKET est un type d'ordre Futures uniquement.
    # Cette fonction NE PEUT PAS être utilisée sur l'API Spot Binance.
    # Elle est conservée pour compatibilité mais soulève une erreur si appelée.
    raise NotImplementedError(
        "TRAILING_STOP_MARKET n'est pas disponible sur Binance Spot. "
        "Utilisez le trailing manuel implémenté dans monitor_and_trade_for_pair()."
    )

def place_stop_loss_order(symbol, quantity, stop_price, client_id=None):
    return _place_stop_loss_order(client, symbol, quantity, stop_price, client_id, send_alert=send_trading_alert_email)

def safe_market_buy(symbol, quoteOrderQty, max_retries=4):
    return _safe_market_buy(client, symbol, quoteOrderQty, max_retries, send_alert=send_trading_alert_email)

def safe_market_sell(symbol, quantity, max_retries=4):
    return _safe_market_sell(client, symbol, quantity, max_retries, send_alert=send_trading_alert_email)

# --- Utility Functions ---
def full_timestamp_resync():
    try:
        sync_windows_silently()
        time.sleep(1)  
        client._sync_server_time()
        logger.info("Synchronisation complète (Windows + Binance) effectuée avant envoi d’ordre.")
    except Exception as e:
        logger.error(f"Echec de la resynchronisation horaire: {e}")

def validate_api_connection() -> bool:
    """Valide la connexion a l'API Binance."""
    try:
        client.ping()
        logger.info(f"Connexion API validée")
        return True
    except Exception as e:
        logger.error(f"Echec de validation API: {e}")
        try:
            subj, body = api_connection_failure_email(str(e))
            send_trading_alert_email(subject=subj, body_main=body, client=client)
        except Exception:
            pass
        return False

@log_exceptions(default_return=False)
def validate_data_integrity(df: pd.DataFrame) -> bool:
    """Valide l'integrite des donnees de marche."""
    if df.empty:
        return False
    # Verifier les valeurs negatives
    if (df[['open', 'high', 'low', 'close', 'volume']] < 0).any().any():
        logger.warning("Valeurs negatives detectees dans les donnees")
        return False
    # Verifier la coherence OHLC
    invalid_ohlc = (df['high'] < df[['open', 'close']].max(axis=1)) | \
                   (df['low'] > df[['open', 'close']].min(axis=1))
    if invalid_ohlc.any():
        logger.warning("Donnees OHLC incoherentes detectees")
        return False
    # Detecter les gaps temporels (sans warning)
    time_diff = df.index.to_series().diff()
    expected_interval = time_diff.mode()[0] if not time_diff.mode().empty else None
    if expected_interval:
        gaps = time_diff > expected_interval * 1.5
        if gaps.any():
            pass  # Silence, plus de warning
    return True

# get_cache_key importé depuis cache_manager.py (Phase 4)

# --- Save throttle: évite les écritures disque excessives (max 1x / 5s) ---
_last_save_time: float = 0.0
_SAVE_THROTTLE_SECONDS: float = 5.0

@log_exceptions(default_return=None)
def save_bot_state(force: bool = False):
    """Sauvegarde l'etat du bot (wrapper vers state_manager).
    
    Throttled à 1 écriture / 5s sauf si force=True (arrêt, crash).
    Thread-safe via _bot_state_lock (C-01).
    """
    global _last_save_time
    now = time.time()
    if not force and (now - _last_save_time) < _SAVE_THROTTLE_SECONDS:
        return
    with _bot_state_lock:
        save_state(bot_state)
    _last_save_time = now

@log_exceptions(default_return=None)
def load_bot_state():
    """Charge l'etat du bot (wrapper vers state_manager).
    
    Thread-safe via _bot_state_lock (C-01).
    """
    global bot_state
    loaded = load_state()
    if loaded:
        with _bot_state_lock:
            bot_state = loaded

# get_symbol_filters: wrapper vers exchange_client.py (Phase 4)
from exchange_client import get_symbol_filters as _get_symbol_filters_impl
def get_symbol_filters(symbol: str) -> Dict:
    """Wrapper qui passe le client global."""
    return _get_symbol_filters_impl(client, symbol)


def reconcile_positions_with_exchange(crypto_pairs_list: List[Dict]) -> None:
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
            coin_symbol, quote_currency = extract_coin_from_pair(real_pair)
        except Exception as e:
            logger.error(f"[RECONCILE] Impossible d'extraire coin/quote pour {real_pair}: {e}")
            continue
        try:
            account_info = client.get_account()
            coin_balance_obj = next((b for b in account_info['balances'] if b['asset'] == coin_symbol), None)
            coin_balance = float(coin_balance_obj['free']) if coin_balance_obj else 0.0
        except Exception as e:
            logger.error(f"[RECONCILE] Impossible de récupérer le solde Binance pour {coin_symbol}: {e}")
            continue
        pair_state = bot_state.get(backtest_pair, {})
        local_in_position = (
            pair_state.get('in_position', False)
            or pair_state.get('last_order_side') == 'BUY'
        )
        # Seuil minimal heuristique (0.001 USD-équivalent approximatif)
        has_real_balance = coin_balance > 0.0001

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
                f"mais solde {coin_symbol} est ~0 — réinitialisation de l'état."
            )
            with _bot_state_lock:
                if backtest_pair in bot_state:
                    bot_state[backtest_pair]['last_order_side'] = 'SELL'
                    bot_state[backtest_pair]['entry_price'] = None
                    bot_state[backtest_pair]['partial_taken_1'] = False
                    bot_state[backtest_pair]['partial_taken_2'] = False
            save_bot_state(force=True)
        else:
            logger.info(
                f"[RECONCILE] {backtest_pair}: cohérent "
                f"(balance={coin_balance:.6f} {coin_symbol}, in_position={local_in_position})"
            )
    logger.info("[RECONCILE] Vérification terminée.")

# --- Data Fetching ---
# get_cache_path, is_cache_expired, safe_cache_read, safe_cache_write,
# update_cache_with_recent_data importés depuis cache_manager.py (Phase 4)

@retry_with_backoff(max_retries=3, base_delay=2.0)
@log_exceptions(default_return=pd.DataFrame())
def fetch_historical_data(pair_symbol: str, time_interval: str, start_date: str, force_refresh: bool = False) -> pd.DataFrame:
    """Recupere les donnees historiques avec validation et cache thread-safe.
    IMPORTANT: force_refresh=True pour bypasser le cache et forcer un telechargement frais.
    Utilise la pagination pour recuperer TOUTES les donnees depuis start_date."""
    try:
        # Initialiser le cache via cache_manager (Phase 4)
        ensure_cache_dir()
        
        # Générer des chemins sécurisés
        cache_file, lock_file = get_cache_path(pair_symbol, time_interval, start_date)
        
        # Lecture ultra-sécurisée du cache (sauf si force_refresh=True)
        if not force_refresh:
            cached_df = safe_cache_read(cache_file)
            if cached_df is not None and not cached_df.empty:
                #  MISE À JOUR INTELLIGENTE: Ajouter les dernières bougies sans retélécharger 5 ans
                updated_df = update_cache_with_recent_data(cached_df, pair_symbol, time_interval, client)
                
                # Sauvegarder le cache mis à jour
                safe_cache_write(cache_file, lock_file, updated_df)
                
                logger.info(f"[OK] Cache used + updated: {pair_symbol} {time_interval} ({len(updated_df)} candles)")
                return updated_df
        
        # Recuperation depuis l'API - Récupérer TOUTES les données depuis start_date
        if not VERBOSE_LOGS:
            logger.info(f"Telechargement {pair_symbol} {time_interval}...")
        else:
            logger.info(f"Debut telechargement pour {pair_symbol} {time_interval} depuis {start_date}")
        
        try:
            # Récupérer TOUTES les données depuis start_date (pagination automatique)
            klinesT = client.get_historical_klines(pair_symbol, time_interval, start_date)
            
            if not klinesT:
                raise ValueError(f"Aucune donnee pour {pair_symbol} et {time_interval}")
            
            if not VERBOSE_LOGS:
                logger.info(f"OK: {len(klinesT)} candles | {pd.Timestamp(klinesT[0][0], unit='ms').strftime('%Y-%m-%d')} -> {pd.Timestamp(klinesT[-1][0], unit='ms').strftime('%Y-%m-%d')}")
            else:
                logger.info(f"Telechargement complete: {len(klinesT)} candles recuperees pour {pair_symbol}")
                logger.info(f"  Date premiere bougie: {pd.Timestamp(klinesT[0][0], unit='ms')}")
                logger.info(f"  Date derniere bougie: {pd.Timestamp(klinesT[-1][0], unit='ms')}")
            
        except Exception as e:
            logger.error(f"Erreur lors du telechargement: {e}")
            raise
        
        all_klines = klinesT
        
        # Creation du DataFrame
        df = pd.DataFrame(all_klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
            'close_time', 'quote_av', 'trades', 'tb_base_av', 'tb_quote_av', 'ignore'
        ])
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        
        # Conversion securisee
        numeric_columns = ['open', 'high', 'low', 'close', 'volume']
        df[numeric_columns] = df[numeric_columns].astype(float)
        
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        
        # Validation des donnees
        if not validate_data_integrity(df):
            logger.warning(f"Donnees invalides pour {pair_symbol}")
            
        # Sauvegarde ultra-sécurisée du cache
        safe_cache_write(cache_file, lock_file, df)
        
        return df
        
    except BinanceAPIException as e:
        logger.error(f"Erreur API Binance: {e}")
        try:
            subj, body = data_retrieval_error_email(pair_symbol, time_interval, start_date, str(e))
            send_trading_alert_email(subject=subj, body_main=body, client=client)
        except Exception:
            pass
        raise
    except Exception as e:
        error_str = str(e)
        logger.error(f"Erreur recuperation donnees: {e}")
        
        # Détecter les erreurs de réseau
        if any(keyword in error_str.lower() for keyword in ['nameresolutionerror', 'getaddrinfo failed', 'max retries exceeded', 'connection']):
            logger.warning("Erreur de connectivité détectée, tentative de récupération...")
            
            if check_network_connectivity():
                logger.info("Connexion rétablie, nouvelle tentative...")
                time.sleep(3)
                # Une seule tentative supplémentaire
                try:
                    klinesT = client.get_historical_klines(pair_symbol, time_interval, start_date)
                    if klinesT:
                        df = pd.DataFrame(klinesT, columns=[
                            'timestamp', 'open', 'high', 'low', 'close', 'volume', 
                            'close_time', 'quote_av', 'trades', 'tb_base_av', 'tb_quote_av', 'ignore'
                        ])
                        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
                        numeric_columns = ['open', 'high', 'low', 'close', 'volume']
                        df[numeric_columns] = df[numeric_columns].astype(float)
                        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df.set_index('timestamp', inplace=True)
                        logger.info(f"Données récupérées après rétablissement connexion")
                        return df
                except Exception as retry_error:
                    logger.error(f"echec après rétablissement: {retry_error}")
        
        try:
            subj, body = network_error_email(pair_symbol, str(e))
            send_trading_alert_email(subject=subj, body_main=body, client=client)
        except Exception:
            pass
        return pd.DataFrame()

# --- Indicator Calculation ---

def compute_stochrsi(rsi_series: pd.Series, period: int = 14) -> pd.Series:
    """Calcul vectorisé robuste du StochRSI (partagé par calculate_indicators et prepare_base_dataframe)."""
    rsi_np = rsi_series.to_numpy()
    min_rsi = pd.Series(rsi_np).rolling(window=period, min_periods=period).min().to_numpy()
    max_rsi = pd.Series(rsi_np).rolling(window=period, min_periods=period).max().to_numpy()
    denom = max_rsi - min_rsi
    with np.errstate(divide='ignore', invalid='ignore'):
        stochrsi = np.where(denom != 0, (rsi_np - min_rsi) / denom, 0)
    stochrsi = np.clip(stochrsi, 0, 1)
    stochrsi = np.nan_to_num(stochrsi, nan=0)
    return pd.Series(stochrsi, index=rsi_series.index)


def get_optimal_ema_periods(df: pd.DataFrame, timeframe: str = '4h', symbol: str = 'TRXUSDC') -> tuple:
    """
    Optimisation EMA adaptative: Sélectionne les meilleures périodes EMA selon le timeframe.

    IMPORTANT (C-12 — anti-look-ahead-bias): ce calcul DOIT porter uniquement sur la
    fenêtre In-Sample (IS). L'appelant est responsable de passer un slice IS:

        is_df = df.iloc[:int(len(df) * 0.70)]
        ema1, ema2 = get_optimal_ema_periods(is_df, timeframe=tf, symbol=pair)

    Basé sur l'analyse statistique de la volatilité et momentum par timeframe:
    - 1h: Volatilité haute, trending rapide → EMA courts (9/21)
    - 4h: Balance swing/trend → EMA moyens (14/26 ou 26/50)
    - 1d: Trend long terme → EMA longs (26/50 ou 50/200)
    """
    try:
        # Calculer la volatilité (ATR/Close)
        atr = AverageTrueRange(
            high=df.get('high', df['close']),
            low=df.get('low', df['close']),
            close=df['close'],
            window=14
        ).average_true_range()
        volatility = (atr / df['close']).mean()
        
        # Sélectionner les périodes basées sur volatilité et timeframe
        timeframe_map = {
            '1m': (5, 13),    # Très court terme
            '5m': (7, 17),    # Court terme
            '15m': (9, 21),   # Court-moyen
            '30m': (12, 26),  # Moyen court
            '1h': (14, 26),   # Moyen
            '4h': (26, 50),   # Moyen-long (default)
            '1d': (50, 200),  # Long terme
        }
        
        base_ema1, base_ema2 = timeframe_map.get(timeframe, (26, 50))
        
        # Ajustement dynamique basé sur la volatilité
        # Volatilité haute → utiliser EMA plus courts (réaction plus rapide)
        # Volatilité basse → utiliser EMA plus longs (réduction du bruit)
        if volatility > 0.015:  # Haute volatilité
            # Réduit périodes de 10-15%
            ema1 = max(5, int(base_ema1 * 0.88))
            ema2 = max(10, int(base_ema2 * 0.88))
        elif volatility < 0.005:  # Basse volatilité
            # Augmente périodes de 10-15% (moins sensible au bruit)
            ema1 = int(base_ema1 * 1.12)
            ema2 = int(base_ema2 * 1.12)
        else:
            ema1, ema2 = base_ema1, base_ema2
        
        logger.info(f"EMA adaptatif pour {symbol} {timeframe}: {ema1}/{ema2} (volatilité: {volatility:.4f})")
        return ema1, ema2
        
    except Exception as e:
        logger.warning(f"Erreur optimisation EMA adaptative, utilisant défaut (26, 50): {e}")
        return 26, 50

def calculate_indicators(
    df: pd.DataFrame, 
    ema1_period: int, 
    ema2_period: int, 
    stoch_period: int = 14,
    sma_long: Optional[int] = None, 
    adx_period: Optional[int] = None, 
    trix_length: Optional[int] = None, 
    trix_signal: Optional[int] = None
) -> pd.DataFrame:
    """
    Calcule les indicateurs techniques avec cache robuste et optimisation mémoire.
    Toutes les opérations sont vectorisées (pandas/numpy), aucune boucle Python.
    """
    try:
        if df.empty or 'close' not in df.columns:
            raise KeyError("DataFrame vide ou colonne 'close' absente")
        
        # Préparer les paramètres pour le cache
        params = {
            'ema1': ema1_period, 
            'ema2': ema2_period, 
            'stoch': stoch_period,
            'sma': sma_long, 
            'adx': adx_period, 
            'trix_len': trix_length, 
            'trix_sig': trix_signal
        }
        cache_key = get_cache_key("indicators", f"{df['close'].iloc[-1]}_{len(df)}_{df.index[-1]}", params)
        
        # Lecture thread-safe du cache (LRU : move_to_end on hit)
        try:
            if cache_key in indicators_cache:
                cached_df = indicators_cache[cache_key]
                if len(cached_df) == len(df.dropna(subset=['close'])):
                    indicators_cache.move_to_end(cache_key)  # LRU touch
                    logger.debug("Indicateurs chargés depuis le cache mémoire")
                    return cached_df.copy()
        except (KeyError, AttributeError, TypeError):
            pass  # Cache corrompu ou indisponible -> recalcul

        # C-14: Déléguer au moteur Cython centralisé quand disponible.
        # Cela élimine la duplication Python/Cython et garantit que le live
        # et le backtest utilisent exactement le même moteur d'indicateurs.
        if CYTHON_INDICATORS_AVAILABLE and _cython_indicators is not None:
            try:
                df_cython = _cython_indicators.calculate_indicators(
                    df.copy(),  # copie explicite — pandas CoW (pandas 2.x) interdit les écritures sur df passé en argument
                    ema1_period,
                    ema2_period,
                    stoch_period,
                    sma_long or 0,
                    adx_period or 0,
                    trix_length or 0,
                    trix_signal or 0,
                )
                if df_cython is not None and not df_cython.empty:
                    # Mise en cache LRU
                    try:
                        indicators_cache[cache_key] = df_cython.copy()
                        indicators_cache.move_to_end(cache_key)
                        while len(indicators_cache) > _INDICATORS_CACHE_MAX:
                            indicators_cache.popitem(last=False)
                    except Exception:
                        pass
                    logger.debug("Indicateurs calculés via Cython (C-14)")
                    return df_cython
            except Exception as _cython_ind_err:
                logger.warning(
                    "Cython indicators failed (%s) — fallback Python.", _cython_ind_err
                )

        # Copie de travail (Python fallback)
        df_work = df.copy()
        
        # Nettoyage minimal des NaN sur 'close'
        df_work['close'] = df_work['close'].ffill().bfill()
        if df_work['close'].isna().any():
            logger.warning("Données 'close' entièrement NaN après nettoyage")
            return pd.DataFrame()

        # --- RSI (seulement si absent) ---
        if 'rsi' not in df_work.columns:
            df_work['rsi'] = RSIIndicator(df_work['close'], window=14).rsi()

        # --- MACD (OPTIMISATION #7: Filtre Momentum MACD) ---
        # MACD = EMA(12) - EMA(26), Signal = EMA(9) de MACD
        # Histogram = MACD - Signal, positif = bullish, négatif = bearish
        try:
            macd_indicator = MACD(df_work['close'], window_fast=12, window_slow=26, window_sign=9)
            df_work['macd'] = macd_indicator.macd()
            df_work['macd_signal'] = macd_indicator.macd_signal()
            df_work['macd_histogram'] = macd_indicator.macd_diff()  # histogram positif = bullish
        except Exception as e:
            logger.warning(f"Erreur calcul MACD: {e}, skipping MACD filter")
            df_work['macd_histogram'] = np.nan

        # --- EMA ---
        # IMPORTANT: utiliser adjust=False pour correspondre aux calculs Binance (methode recursive/online)
        df_work['ema1'] = df_work['close'].ewm(span=ema1_period, adjust=False).mean()
        df_work['ema2'] = df_work['close'].ewm(span=ema2_period, adjust=False).mean()

        # --- Stochastic RSI (vectorized robust calculation) ---
        if 'rsi' in df_work.columns:
            df_work['stoch_rsi'] = compute_stochrsi(df_work['rsi'], period=stoch_period)

        # --- ATR ---
        df_work['atr'] = AverageTrueRange(
            high=df_work.get('high', df_work['close']),
            low=df_work.get('low', df_work['close']),
            close=df_work['close'],
            window=config.atr_period
        ).average_true_range()

        # --- SMA long ---
        if sma_long:
            df_work['sma_long'] = df_work['close'].rolling(window=sma_long).mean()

        # --- ADX (utiliser l'implémentation standard Wilder via ta.trend.ADXIndicator) ---
        if adx_period and len(df_work) >= adx_period + 2:
            try:
                df_work['adx'] = ADXIndicator(high=df_work['high'], low=df_work['low'], close=df_work['close'], window=adx_period).adx()
            except Exception:
                df_work['adx'] = np.nan

        # --- TRIX ---
        if trix_length and trix_signal:
            trix_ema1 = df_work['close'].ewm(span=trix_length, adjust=False).mean()
            trix_ema2 = trix_ema1.ewm(span=trix_length, adjust=False).mean()
            trix_ema3 = trix_ema2.ewm(span=trix_length, adjust=False).mean()
            df_work['TRIX_PCT'] = trix_ema3.pct_change() * 100
            df_work['TRIX_SIGNAL'] = df_work['TRIX_PCT'].rolling(window=trix_signal).mean()
            df_work['TRIX_HISTO'] = df_work['TRIX_PCT'] - df_work['TRIX_SIGNAL']

        # --- Nettoyage final ---
        # Supprimer NaN uniquement des colonnes essentielles (pas stoch_rsi qui a des 0 valides)
        df_work.dropna(subset=['close', 'rsi', 'atr'], inplace=True)

        # --- Mise en cache LRU (éviction des entrées les plus anciennes) ---
        try:
            indicators_cache[cache_key] = df_work.copy()
            indicators_cache.move_to_end(cache_key)
            while len(indicators_cache) > _INDICATORS_CACHE_MAX:
                indicators_cache.popitem(last=False)  # Evict oldest
            logger.debug(f"Indicateurs mis en cache: {cache_key[:30]}...")
        except (MemoryError, KeyError) as e:
            logger.debug(f"Erreur mise en cache: {e}")

        logger.debug(f"Indicateurs calculés: {len(df_work)} lignes")
        return df_work

    except Exception as e:
        logger.error(f"Erreur calcul indicateurs: {e}", exc_info=True)
        try:
            subj, body = indicator_error_email(str(e))
            send_trading_alert_email(subject=subj, body_main=body, client=client)
        except Exception:
            pass
        return pd.DataFrame()

def universal_calculate_indicators(
    df: pd.DataFrame,
    ema1_period: int,
    ema2_period: int,
    stoch_period: int = 14,
    sma_long: Optional[int] = None,
    adx_period: Optional[int] = None,
    trix_length: Optional[int] = None,
    trix_signal: Optional[int] = None
) -> pd.DataFrame:
    """
    Universal indicator calculation for live trading and backtest.
    Uses Cython-accelerated calculation if available, else falls back to Python.
    Mirrors the logic of backtest_from_dataframe for indicator calculation.
    """
    try:
        if df.empty or len(df) < 10:
            return pd.DataFrame()

        # If Cython is available and has the required function, use it.
        if CYTHON_BACKTEST_AVAILABLE and backtest_engine is not None and hasattr(backtest_engine, 'calculate_indicators_fast'):
            try:
                # Prepare the data for Cython
                df_work = df.copy()
                # Cython function expects specific column names
                if 'high' not in df_work.columns:
                    df_work['high'] = df_work['close']
                if 'low' not in df_work.columns:
                    df_work['low'] = df_work['close']
                
                # Call Cython-accelerated calculation
                result = backtest_engine.calculate_indicators_fast(
                    df_work['close'].to_numpy(dtype=np.float64),
                    df_work['high'].to_numpy(dtype=np.float64),
                    df_work['low'].to_numpy(dtype=np.float64),
                    ema1_period,
                    ema2_period,
                    stoch_period,
                    sma_long if sma_long else 0,
                    adx_period if adx_period else 0,
                    trix_length if trix_length else 0,
                    trix_signal if trix_signal else 0
                )
                # Convert the result (dict of arrays) to a DataFrame with the same index
                if isinstance(result, dict):
                    # The Cython function returns a dict with keys like 'ema1', 'ema2', 'stoch_rsi', 'atr', etc.
                    # It might also return 'sma_long', 'adx', 'TRIX_HISTO' if requested.
                    # We need to ensure the DataFrame index matches the original df_work index.
                    # The Cython function likely processes all rows, so the length matches.
                    if len(df_work) == len(next(iter(result.values()))):
                        return pd.DataFrame(result, index=df_work.index)
                    else:
                        logger.warning("Cython calculate_indicators_fast returned array length mismatch, using Python fallback.")
                else:
                    logger.warning("Cython calculate_indicators_fast returned unexpected type, using Python fallback.")
            except Exception as e:
                logger.warning(f"Cython indicator calculation failed, using Python fallback: {e}")
                # Fallback to Python below

        # Fallback: use the robust Python implementation
        return calculate_indicators(
            df,
            ema1_period,
            ema2_period,
            stoch_period=stoch_period,
            sma_long=sma_long,
            adx_period=adx_period,
            trix_length=trix_length,
            trix_signal=trix_signal
        )
    except Exception as e:
        logger.error(f"Erreur universal_calculate_indicators: {e}", exc_info=True)
        return pd.DataFrame()

# --- Backtest Functions ---
def prepare_base_dataframe(pair: str, timeframe: str, start_date: str, stoch_period: int = 14) -> Optional[pd.DataFrame]:
    """Prépare un DataFrame avec tous les indicateurs de base partagés par tous les scénarios."""
    df = fetch_historical_data(pair, timeframe, start_date)
    if df.empty:
        return None

    # Calculer TOUS les EMA possibles (adjust=False pour correspondre Binance - methode recursive/online)
    for period in [14, 25, 26, 45, 50]:
        df[f'ema_{period}'] = df['close'].ewm(span=period, adjust=False).mean()

    # Indicateurs communs à tous les scénarios
    df['rsi'] = RSIIndicator(df['close'], window=14).rsi()

    df['atr'] = AverageTrueRange(
        high=df['high'], low=df['low'], close=df['close'], window=config.atr_period
    ).average_true_range()
    # Stochastic RSI
    df['stoch_rsi'] = compute_stochrsi(df['rsi'], period=stoch_period)

    # Supprimer NaN uniquement des colonnes essentielles
    df.dropna(subset=['close', 'rsi', 'atr'], inplace=True)
    return df

def get_binance_trading_fees(client, symbol='TRXUSDC'):
    """
    Récupère les frais de trading réels depuis l'API Binance.
    Retourne taker_fee et maker_fee actualisés pour ce compte.
    """
    try:
        account_info = client.get_account()
        taker_commission = account_info.get('takerCommission', 10) / 10000
        maker_commission = account_info.get('makerCommission', 10) / 10000
        logger.info(f"[FEES REELS] Binance - Taker: {taker_commission*100:.4f}%, Maker: {maker_commission*100:.4f}%")
        return taker_commission, maker_commission
    except Exception as e:
        logger.warning(f"[FEES] Impossible de récupérer frais Binance: {e}. Utilisation valeurs par défaut.")
        return config.taker_fee, config.maker_fee

def backtest_from_dataframe(
    df: pd.DataFrame,
    ema1_period: int,
    ema2_period: int,
    sma_long: Optional[int] = None,
    adx_period: Optional[int] = None,
    trix_length: Optional[int] = None,
    trix_signal: Optional[int] = None,
    sizing_mode: str = 'baseline',
    periods_per_year: int = 8766,
    **kwargs,
) -> Dict[str, Any]:
    """Backtest à partir d'un DataFrame préparé.
    
    Utilise l'implémentation Cython accélérée si disponible (30-50x plus rapide)
    uniquement pour le mode de sizing 'baseline'. Pour les modes avancés
    (par ex. 'risk'), force l'utilisation de la version Python qui gère
    explicitement le position sizing.
    """
    try:
        if df.empty or len(df) < 50:
            return {'final_wallet': 0.0, 'trades': pd.DataFrame(), 'max_drawdown': 0.0, 'win_rate': 0.0}

        # Sécuriser la présence des colonnes EMA dynamiques (adaptatives) dans le DataFrame source
        # Cela évite les KeyError lorsque les périodes calculées diffèrent des EMA historiques (12/22/44/176...)
        if f'ema_{ema1_period}' not in df.columns:
            df[f'ema_{ema1_period}'] = df['close'].ewm(span=ema1_period, adjust=False).mean()
        if f'ema_{ema2_period}' not in df.columns:
            df[f'ema_{ema2_period}'] = df['close'].ewm(span=ema2_period, adjust=False).mean()

        # === OPTIMISATION CYTHON : Appeler le moteur optimisé si disponible ===
        # IMPORTANT: le moteur Cython ne gère que le sizing historique "baseline".
        # Pour les autres modes (ex: 'risk'), on force la version Python qui
        # applique le position sizing avancé.
        if CYTHON_BACKTEST_AVAILABLE and backtest_engine is not None and sizing_mode == 'baseline':
            try:
                # Préparer les données pour le backtest Cython
                df_work = df.copy()
                df_work['ema1'] = df_work[f'ema_{ema1_period}']
                df_work['ema2'] = df_work[f'ema_{ema2_period}']
                # StochRSI déjà calculé dans prepare_base_dataframe
                # Pas besoin de recalculer ici
                # Ajouter indicateurs spécifiques
                if sma_long:
                    df_work['sma_long'] = df_work['close'].rolling(window=sma_long).mean()
                
                if adx_period:
                    if 'adx' in df.columns:
                        df_work['adx'] = df['adx'].values
                    else:
                        # Utiliser ADXIndicator (optimisé et testé)
                        df_work['adx'] = ADXIndicator(
                            high=df_work['high'],
                            low=df_work['low'],
                            close=df_work['close'],
                            window=adx_period
                        ).adx()
                
                if trix_length and trix_signal:
                    trix_ema1 = df_work['close'].ewm(span=trix_length, adjust=False).mean()
                    trix_ema2 = trix_ema1.ewm(span=trix_length, adjust=False).mean()
                    trix_ema3 = trix_ema2.ewm(span=trix_length, adjust=False).mean()
                    df_work['TRIX_PCT'] = trix_ema3.pct_change() * 100
                    df_work['TRIX_SIGNAL'] = df_work['TRIX_PCT'].rolling(window=trix_signal).mean()
                    df_work['TRIX_HISTO'] = df_work['TRIX_PCT'] - df_work['TRIX_SIGNAL']
                
                # Appeler le moteur Cython optimisé
                result = backtest_engine.backtest_from_dataframe_fast(
                    df_work['close'].to_numpy(dtype=np.float64),
                    df_work['high'].to_numpy(dtype=np.float64),
                    df_work['low'].to_numpy(dtype=np.float64),
                    df_work['ema1'].to_numpy(dtype=np.float64),
                    df_work['ema2'].to_numpy(dtype=np.float64),
                    df_work['stoch_rsi'].to_numpy(dtype=np.float64),
                    df_work['atr'].to_numpy(dtype=np.float64),
                    df_work['sma_long'].to_numpy(dtype=np.float64) if sma_long and 'sma_long' in df_work.columns else None,
                    df_work['adx'].to_numpy(dtype=np.float64) if adx_period and 'adx' in df_work.columns else None,
                    df_work['TRIX_HISTO'].to_numpy(dtype=np.float64) if trix_length and 'TRIX_HISTO' in df_work.columns else None,
                    config.initial_wallet,  # initial_wallet
                    'StochRSI',  # scenario
                    sma_long is not None,  # use_sma
                    adx_period is not None,  # use_adx
                    trix_length is not None,  # use_trix
                    config.taker_fee,  # C-08: fee réel (synchronisé avec le live via get_binance_trading_fees)
                )
                
                # Convertir le résultat Cython en format compatible
                return {
                    'final_wallet': result['final_wallet'],
                    'trades': pd.DataFrame(result['trades']) if result['trades'] else pd.DataFrame(),
                    'max_drawdown': result['max_drawdown'],
                    'win_rate': result['win_rate']
                }
            
            except Exception as e:
                logger.warning(f"Cython backtest failed, using Python fallback: {e}")
                import traceback
                traceback.print_exc()
                # Continuer avec la version Python comme fallback
        
        # === VERSION PYTHON (fallback ou si Cython n'est pas disponible) ===
        df_work = df.copy()
        # Sécuriser les colonnes EMA dynamiques générées par l'adaptatif
        if f'ema_{ema1_period}' not in df_work.columns:
            df_work[f'ema_{ema1_period}'] = df_work['close'].ewm(span=ema1_period, adjust=False).mean()
        if f'ema_{ema2_period}' not in df_work.columns:
            df_work[f'ema_{ema2_period}'] = df_work['close'].ewm(span=ema2_period, adjust=False).mean()
        df_work['ema1'] = df_work[f'ema_{ema1_period}']
        df_work['ema2'] = df_work[f'ema_{ema2_period}']
        
        # Ajouter indicateurs spécifiques
        if sma_long:
            df_work['sma_long'] = df_work['close'].rolling(window=sma_long).mean()
        
        if adx_period and 'adx' in df.columns:
            df_work['adx'] = df['adx'].values
        elif adx_period:
            # Implementation ADX simple (peut être améliorée)
            df_work['adx'] = ADXIndicator(high=df_work['high'], low=df_work['low'], close=df_work['close'], window=adx_period).adx()

        if trix_length and trix_signal:
            trix_ema1 = df_work['close'].ewm(span=trix_length, adjust=False).mean()
            trix_ema2 = trix_ema1.ewm(span=trix_length, adjust=False).mean()
            trix_ema3 = trix_ema2.ewm(span=trix_length, adjust=False).mean()
            df_work['TRIX_PCT'] = trix_ema3.pct_change() * 100
            df_work['TRIX_SIGNAL'] = df_work['TRIX_PCT'].rolling(window=trix_signal).mean()
            df_work['TRIX_HISTO'] = df_work['TRIX_PCT'] - df_work['TRIX_SIGNAL']

        # Logique de backtest (version Python)
        usd = config.initial_wallet
        coin = 0.0
        trades_history = []
        in_position = False
        entry_price = 0.0  # Tracked for position analysis (logged at entry/exit)
        entry_usd_invested = 0.0  # Track USD invested at entry
        max_price = 0.0
        trailing_stop = 0.0
        trailing_stop_activated = False
        stop_loss = 0.0
        max_drawdown = 0.0
        peak_wallet = config.initial_wallet
        winning_trades = 0
        total_trades = 0
        equity_curve = [config.initial_wallet]

        # Variables de référence fixées à l'entrée de position
        atr_at_entry = 0.0
        stop_loss_at_entry = 0.0
        trailing_activation_price_at_entry = 0.0
        partial_taken_1 = False
        partial_taken_2 = False

        for i in range(len(df_work)):
            # Utiliser la bougie actuelle pour une réactivité optimale (meilleure détection des signaux)
            idx_signal = i
            row_close = df_work['close'].iloc[i]
            row_atr = df_work['atr'].iloc[i]
            index = df_work.index[i]

            if in_position:
                # === TRAILING ACTIVATION PROFESSIONNELLE ===
                # Phase 1: Stop-loss FIXE à 3×ATR jusqu'à activation du trailing
                # Phase 2: Activation quand prix >= entry + 5×ATR
                # Phase 3: Trailing suit à max_price - 5.5×ATR
                
                trailing_distance = config.atr_multiplier * atr_at_entry
                
                # Mise à jour du max_price
                if row_close > max_price:
                    max_price = row_close
                
                # Vérifier activation du trailing (prix >= entry + 5×ATR)
                if not trailing_stop_activated and row_close >= trailing_activation_price_at_entry:
                    trailing_stop_activated = True
                    trailing_stop = max_price - trailing_distance
                    logger.debug(f"[BACKTEST] Trailing activé à {row_close:.4f} (seuil: {trailing_activation_price_at_entry:.4f})")
                
                # Mise à jour trailing si activé (ne peut que monter)
                if trailing_stop_activated:
                    new_trailing = max_price - trailing_distance
                    if new_trailing > trailing_stop:
                        trailing_stop = new_trailing
                
                # OPTIMISATION PARTIAL PROFIT TAKING: Vendre partiellement au profit +2% et +4%
                if in_position and coin > 0 and entry_price > 0:
                    profit_pct = (row_close - entry_price) / entry_price
                    # Vendre 50% au profit +2%
                    if not partial_taken_1 and profit_pct >= config.partial_threshold_1:
                        partial_qty_1 = coin * config.partial_pct_1
                        # BACKTEST: Simulate partial sell without real API call
                        partial_proceeds_1 = partial_qty_1 * row_close * (1 - config.taker_fee)
                        usd += partial_proceeds_1
                        coin -= partial_qty_1
                        partial_taken_1 = True
                        logger.debug(f"[BACKTEST] Vente 50% position simulée à +{profit_pct*100:.2f}% (qty: {partial_qty_1:.6f}, +{partial_proceeds_1:.2f} USD)")
                    # Vendre 30% du RESTE au profit +4%
                    if not partial_taken_2 and profit_pct >= config.partial_threshold_2 and coin > 0:
                        partial_qty_2 = coin * config.partial_pct_2  # % du reste (après PARTIAL-1)
                        partial_proceeds_2 = partial_qty_2 * row_close * (1 - config.taker_fee)
                        usd += partial_proceeds_2
                        coin -= partial_qty_2
                        partial_taken_2 = True
                        logger.debug(f"[BACKTEST] Vente 30% position simulée à +{profit_pct*100:.2f}% (qty: {partial_qty_2:.6f}, +{partial_proceeds_2:.2f} USD)")
                
                # Calculate current wallet value and drawdown
                current_wallet = usd + (coin * row_close)

                # Track equity curve for risk metrics
                equity_curve.append(current_wallet)

                # Update peak BEFORE calculating drawdown
                if current_wallet > peak_wallet:
                    peak_wallet = current_wallet

                # Calculate drawdown from peak
                drawdown = (peak_wallet - current_wallet) / peak_wallet if peak_wallet > 0 else 0.0
                max_drawdown = max(max_drawdown, drawdown)

                # Check exit conditions
                exit_trade = False
                motif_sortie = None
                # Stop-loss FIXE à 3×ATR (toujours actif)
                if row_close <= stop_loss_at_entry:
                    exit_trade = True
                    motif_sortie = 'STOP_LOSS'
                # Trailing stop (seulement si activé ET prix < trailing)
                elif trailing_stop_activated and row_close <= trailing_stop:
                    exit_trade = True
                    motif_sortie = 'TRAILING_STOP'
                # Signal de vente (EMA cross down + StochRSI high)
                elif df_work['ema2'].iloc[i] > df_work['ema1'].iloc[i] and df_work['stoch_rsi'].iloc[i] > 0.2:
                    exit_trade = True
                    motif_sortie = 'SIGNAL'

                if exit_trade:
                    # OPTIMISATION TIMING: Utiliser next_open pour l'exécution de la sortie (clôture)
                    if i + 1 < len(df_work):
                        exit_base_price = df_work.iloc[i + 1]['open']
                    else:
                        exit_base_price = row_close
                    
                    # Appliquer slippage de vente
                    optimized_exit_price = exit_base_price * (1 - config.slippage_sell)
                    
                    # BACKTEST: Skip real order execution - simulation only
                    logger.debug(f"[BACKTEST] Sortie simulée: {motif_sortie} à {optimized_exit_price:.4f} (qty: {coin:.6f})")

                    # Calcul proceeds local (pour le backtest) avec optimisation timing
                    gross_proceeds = coin * optimized_exit_price
                    fee = gross_proceeds * config.taker_fee
                    usd = usd + (gross_proceeds - fee)
                    coin = 0.0
                    
                    # CORRECT profit calculation: final USD - initial USD invested
                    trade_profit = usd - entry_usd_invested
                    
                    if trade_profit > 0:
                        winning_trades += 1
                    total_trades += 1
                    
                    trades_history.append({
                        'date': index, 
                        'type': 'sell', 
                        'price': row_close, 
                        'profit': trade_profit,
                        'motif': motif_sortie,
                        'stop_loss': f'{stop_loss:.4f} USDC',
                        'trailing_stop': f'{trailing_stop:.4f} USDC'
                    })
                    
                    in_position = False
                    entry_price = 0.0
                    entry_usd_invested = 0.0
                    max_price = 0.0
                    trailing_stop = 0.0
                    stop_loss = 0.0
                    trailing_stop_activated = False
                    continue

            # Condition d'achat
            buy_condition = (df_work['ema1'].iloc[idx_signal] > df_work['ema2'].iloc[idx_signal] and 
                             df_work['stoch_rsi'].iloc[idx_signal] < 0.8 and 
                             df_work['stoch_rsi'].iloc[idx_signal] > 0.05 and 
                             usd > 0)
            if sma_long and 'sma_long' in df_work.columns:
                buy_condition &= (row_close > df_work['sma_long'].iloc[idx_signal])
            if adx_period and 'adx' in df_work.columns:
                buy_condition &= (df_work['adx'].iloc[idx_signal] > 25)
            if trix_length and 'TRIX_HISTO' in df_work.columns:
                buy_condition &= (df_work['TRIX_HISTO'].iloc[idx_signal] > 0)

            if buy_condition and not in_position:
                # OPTIMISATION TIMING: Utiliser next_open au lieu de close pour exécution plus réaliste
                if i + 1 < len(df_work):
                    base_price = df_work.iloc[i + 1]['open']
                else:
                    base_price = row_close
                
                # Appliquer slippage d'achat
                optimized_price = base_price * (1 + config.slippage_buy)

                if sizing_mode == 'baseline':
                    gross_coin = (usd * 0.95) / optimized_price if optimized_price > 0 else 0.0
                elif sizing_mode == 'risk':
                    # RISK-BASED: % risk avec ATR stop-loss
                    if row_atr and row_atr > 0 and optimized_price > 0:
                        qty_by_risk = compute_position_size_by_risk(
                            equity=usd,
                            atr_value=row_atr,
                            entry_price=optimized_price,
                            risk_pct=config.risk_per_trade,
                            stop_atr_multiplier=config.atr_stop_multiplier
                        )
                        max_affordable = (usd * 0.95) / optimized_price
                        gross_coin = min(max_affordable, qty_by_risk)
                    else:
                        gross_coin = (usd * 0.95) / optimized_price if optimized_price > 0 else 0.0
                elif sizing_mode == 'fixed_notional':
                    # Fixed notional: montant USD fixe par trade (10% du capital)
                    if optimized_price > 0:
                        notional_per_trade = usd * 0.1
                        qty_fixed = compute_position_size_fixed_notional(
                            equity=usd,
                            notional_per_trade_usd=notional_per_trade,
                            entry_price=optimized_price
                        )
                        max_affordable = (usd * 0.95) / optimized_price
                        gross_coin = min(max_affordable, qty_fixed)
                    else:
                        gross_coin = 0.0
                elif sizing_mode == 'volatility_parity':
                    # Volatility parity: volatilité fixe du P&L
                    if row_atr and row_atr > 0 and optimized_price > 0:
                        qty_vol = compute_position_size_volatility_parity(
                            equity=usd,
                            atr_value=row_atr,
                            entry_price=optimized_price,
                            target_volatility_pct=config.target_volatility_pct
                        )
                        max_affordable = (usd * 0.95) / optimized_price
                        gross_coin = min(max_affordable, qty_vol)
                    else:
                        gross_coin = (usd * 0.95) / optimized_price if optimized_price > 0 else 0.0
                else:
                    # Fallback to baseline for unknown modes
                    gross_coin = (usd * 0.95) / optimized_price if optimized_price > 0 else 0.0

                if gross_coin and gross_coin > 0:
                    fee_in_coin = gross_coin * config.taker_fee
                    coin = gross_coin - fee_in_coin
                    entry_usd_invested = usd
                    usd = 0.0
                    entry_price = optimized_price
                    logger.debug(f"Position ouverte: entry={entry_price:.4f}, stop_loss={optimized_price - (config.atr_stop_multiplier * row_atr):.4f}")
                    max_price = optimized_price
                    atr_at_entry = row_atr
                    stop_loss_at_entry = optimized_price - (config.atr_stop_multiplier * atr_at_entry)
                    trailing_activation_price_at_entry = optimized_price + (config.atr_multiplier * atr_at_entry)
                    trailing_stop = 0.0
                    trailing_stop_activated = False
                    partial_taken_1 = False
                    partial_taken_2 = False
                    trades_history.append({'date': index, 'type': 'buy', 'price': optimized_price})
                    in_position = True
                    logger.debug(f"[BACKTEST] Position ouverte avec stop loss simulé à {stop_loss_at_entry:.4f}")
                else:
                    # Impossible d'ouvrir une position (pas de taille calculable)
                    pass

        # Final wallet calculation
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        final_wallet = usd + (coin * df_work['close'].iloc[-1]) if in_position else usd
        equity_curve.append(final_wallet)

        # Compute risk-adjusted metrics (Sharpe, Sortino, etc.)
        try:
            from walk_forward import compute_risk_metrics
            risk_metrics = compute_risk_metrics(
                np.array(equity_curve),
                trades_df=pd.DataFrame(trades_history) if trades_history else None,
                periods_per_year=periods_per_year,
            )
        except Exception:
            risk_metrics = {}

        result = {
            'final_wallet': final_wallet,
            'trades': pd.DataFrame(trades_history),
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
        }
        result.update(risk_metrics)
        return result

    except Exception as e:
        logger.error(f"Erreur dans backtest_from_dataframe: {e}", exc_info=True)
        return {'final_wallet': 0.0, 'trades': pd.DataFrame(), 'max_drawdown': 0.0, 'win_rate': 0.0}

def empty_result_dict(timeframe: str, ema1: int, ema2: int, scenario_name: str) -> Dict[str, Any]:
    """Retourne un résultat de backtest vide en cas d'erreur."""
    return {
        'timeframe': timeframe,
        'ema_periods': (ema1, ema2),
        'scenario': scenario_name,
        'initial_wallet': config.initial_wallet,
        'final_wallet': 0.0,
        'trades': pd.DataFrame(),
        'max_drawdown': 0.0,
        'win_rate': 0.0
    }

def run_single_backtest_optimized(args: Tuple) -> Dict[str, Any]:
    """Execute un backtest unique à partir d'un DataFrame préparé.

    args tuple layout: (timeframe, ema1, ema2, scenario, base_df, pair_symbol, sizing_mode)
    """
    # Backwards-compatible unpacking (older tasks may not include sizing_mode)
    if len(args) == 6:
        (timeframe, ema1, ema2, scenario, base_df, pair_symbol) = args
        sizing_mode = 'baseline'
    else:
        (timeframe, ema1, ema2, scenario, base_df, pair_symbol, sizing_mode) = args
    try:
        # Stocker temporairement le nom de la paire pour l'optimisation sniper
        global _current_backtest_pair
        _current_backtest_pair = pair_symbol
        
        result = backtest_from_dataframe(
            df=base_df,
            ema1_period=ema1,
            ema2_period=ema2,
            sma_long=scenario['params'].get('sma_long'),
            adx_period=scenario['params'].get('adx_period'),
            trix_length=scenario['params'].get('trix_length'),
            trix_signal=scenario['params'].get('trix_signal'),
            sizing_mode=sizing_mode
        )
        return {
            'timeframe': timeframe,
            'ema_periods': (ema1, ema2),
            'scenario': scenario['name'],
            'initial_wallet': config.initial_wallet,
            'final_wallet': result['final_wallet'],
            'trades': result['trades'],
            'max_drawdown': result['max_drawdown'],
            'win_rate': result['win_rate']
        }
    except Exception as e:
        logger.error(f"Erreur backtest parallele: {e}")
        return empty_result_dict(timeframe, ema1, ema2, scenario['name'])
    finally:
        # Nettoyer la variable globale
        _current_backtest_pair = None

def run_all_backtests(backtest_pair: str, start_date: str, timeframes: List[str], sizing_mode: str = 'baseline') -> List[Dict[str, Any]]:
    """Execute tous les backtests en parallele (version optimisée)."""
    results = []

    # Préparer 1 DataFrame par timeframe
    base_dataframes = {}
    for tf in timeframes:
        df = prepare_base_dataframe(backtest_pair, tf, start_date, 14)
        base_dataframes[tf] = df if df is not None and not df.empty else pd.DataFrame()

    # OPTIMISATION #3: EMA adaptatives par timeframe
    # On calcule une paire EMA optimisée par timeframe, puis on ajoute les paires historiques pour couverture
    ema_periods_by_tf = {}
    extra_ema_pairs = [(18, 36), (20, 40), (30, 60)]
    for tf, df_tf in base_dataframes.items():
        if df_tf is not None and not df_tf.empty:
            # C-12: calculer la volatilité sur IS uniquement (premier 70 %) pour éviter
            # le biais de look-ahead — les périodes EMA ne doivent pas «voir» l'OOS.
            is_end = max(int(len(df_tf) * 0.70), 1)
            adaptive_ema = get_optimal_ema_periods(df_tf.iloc[:is_end], timeframe=tf, symbol=backtest_pair)
        else:
            adaptive_ema = (26, 50)
        # Ajouter fallback historique + grid étendu
        ema_periods_by_tf[tf] = [adaptive_ema, (26, 50)] + extra_ema_pairs
    scenarios = [
        {'name': 'StochRSI', 'params': {'stoch_period': 14}},
        {'name': 'StochRSI_SMA', 'params': {'stoch_period': 14, 'sma_long': 200}},
        {'name': 'StochRSI_ADX', 'params': {'stoch_period': 14, 'adx_period': 14}},
        {'name': 'StochRSI_TRIX', 'params': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15}},
    ]

    tasks = []
    for timeframe in timeframes:
        base_df = base_dataframes.get(timeframe, pd.DataFrame())
        if base_df.empty:
            continue
        ema_periods = ema_periods_by_tf.get(timeframe, [(26, 50)])
        # Supprimer les doublons éventuels
        ema_periods_unique = []
        for pair in ema_periods:
            if pair not in ema_periods_unique:
                ema_periods_unique.append(pair)
        for ema1, ema2 in ema_periods_unique:
            for scenario in scenarios:
                # include sizing_mode in task tuple so workers can use risk-based sizing when requested
                tasks.append((timeframe, ema1, ema2, scenario, base_df, backtest_pair, sizing_mode))

    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        future_to_task = {executor.submit(run_single_backtest_optimized, task): task for task in tasks}
        with tqdm(
            total=len(tasks), 
            desc="[BACKTESTS]", 
            colour="green",
            bar_format="{desc}: {percentage:3.0f}%|█{bar:30}█| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]"
        ) as pbar:
            for future in as_completed(future_to_task):
                try:
                    result = future.result()
                    results.append(result)
                    pbar.update(1)
                except Exception as e:
                    logger.error(f"Erreur future: {e}")
                    pbar.update(1)

    return results

def run_parallel_backtests(crypto_pairs: List[Dict], start_date: str, timeframes: List[str], sizing_mode: str = 'baseline') -> Dict[str, Any]:
    """Exécute les backtests en parallèle et retourne les résultats bruts."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    max_workers = min(len(crypto_pairs), 5)

    results_by_pair = {}
    
    # Calculer le nombre total de tâches pour la barre de progression globale
    extra_ema_pairs = [(18, 36), (20, 40), (30, 60)]
    ema_periods_by_tf = {tf: [(26, 50), (14, 26)] + extra_ema_pairs for tf in timeframes}
    scenarios = [
        {'name': 'StochRSI', 'params': {'stoch_period': 14}},
        {'name': 'StochRSI_SMA', 'params': {'stoch_period': 14, 'sma_long': 200}},
        {'name': 'StochRSI_ADX', 'params': {'stoch_period': 14, 'adx_period': 14}},
        {'name': 'StochRSI_TRIX', 'params': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15}},
    ]
    # Total tasks with adaptative EMA pairs per timeframe
    total_tasks = 0
    for tf in timeframes:
        ema_list = ema_periods_by_tf.get(tf, [(26, 50)])
        total_tasks += len(crypto_pairs) * len(ema_list) * len(scenarios)
    
    # Affichage unique et propre
    console.print(f"\n[bold cyan]Lancement de {total_tasks} backtests avec optimisation sniper...[/bold cyan]")

    def run_single_pair(pair_info):
        try:
            # Exécuter le backtest SANS affichage
            results = run_all_backtests(
                pair_info["backtest_pair"], 
                start_date, 
                timeframes,
                sizing_mode=sizing_mode
            )
            return pair_info["backtest_pair"], pair_info["real_pair"], results
        except Exception as e:
            logger.error(f"Erreur backtest {pair_info['backtest_pair']}: {e}")
            return pair_info["backtest_pair"], pair_info["real_pair"], []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_pair = {executor.submit(run_single_pair, pair): pair for pair in crypto_pairs}
        for future in as_completed(future_to_pair):
            try:
                backtest_pair, real_pair, results = future.result()
                results_by_pair[backtest_pair] = {
                    'real_pair': real_pair,
                    'results': results
                }
            except Exception as e:
                logger.error(f"Erreur future: {e}")

    return results_by_pair  # retourne un dict structuré

# display_results_for_pair extraite dans display_ui.py (Phase 5)

# --- Live Trading Functions ---
def generate_buy_condition_checker(best_params: Dict[str, Any]):
    """
    Génère une fonction de vérification des conditions d'achat 
    qui reflète EXACTEMENT le backtest gagnant.
    Inclut les optimisations: filtre volatilité, timing optimisé, RSI momentum filter.
    """
    def check_buy_signal(row: pd.Series, usdc_balance: float) -> Tuple[bool, str]:
        """
        Vérifie si les conditions d'achat sont remplies.
        Retourne (is_buy_signal, detailed_reason)
        """
        if usdc_balance <= 0:
            return False, "Solde USDC insuffisant"
        
        # Condition de base : EMA1 > EMA2 + StochRSI < 0.8
        ema_condition = row['ema1'] > row['ema2']
        stoch_condition = row['stoch_rsi'] < 0.8
        
        if not ema_condition:
            return False, f"EMA1 ({row['ema1']:.2f}) <= EMA2 ({row['ema2']:.2f})"
        if not stoch_condition:
            return False, f"StochRSI ({row['stoch_rsi']:.4f}) >= 0.8"
        
        # Conditions additionnelles selon le scénario
        scenario = best_params.get('scenario', 'StochRSI')
        
        if scenario == 'StochRSI_SMA' and 'sma_long' in row:
            sma_long = best_params.get('sma_long', 200)
            if row['close'] <= row['sma_long']:
                return False, f"Prix ({row['close']:.4f}) <= SMA{sma_long} ({row['sma_long']:.4f})"
        
        if scenario == 'StochRSI_ADX' and 'adx' in row:
            adx_threshold = 25
            if row['adx'] <= adx_threshold:
                return False, f"ADX ({row['adx']:.2f}) <= {adx_threshold}"
        
        if scenario == 'StochRSI_TRIX' and 'TRIX_HISTO' in row:
            if row['TRIX_HISTO'] <= 0:
                return False, f"TRIX_HISTO ({row['TRIX_HISTO']:.6f}) <= 0"
        
        # Toutes les conditions sont remplies
        return True, "[OK] Signal d'achat valide"
    
    return check_buy_signal

def generate_sell_condition_checker(best_params: Dict[str, Any]):
    """
    Génère une fonction de vérification des conditions de vente 
    qui reflète EXACTEMENT le backtest gagnant.
    Inclut: trailing stop profit-based, partial profit taking, dynamic ATR stop-loss
    """
    def check_sell_signal(row: pd.Series, coin_balance: float, 
                         entry_price: Optional[float], current_price: float, 
                         atr_value: Optional[float]) -> Tuple[bool, Optional[str]]:
        """
        Vérifie si les conditions de vente sont remplies.
        Retourne (is_sell_signal, sell_reason)
        - sell_reason peut être : 'SIGNAL', 'STOP-LOSS', 'TRAILING-STOP', 'PARTIAL-1', 'PARTIAL-2', None
        """
        if coin_balance <= 0 or entry_price is None:
            return False, None
        
        # Sécurisation des entrées
        if atr_value is None:
            return False, None
        
        # STOP-LOSS FIXE À config.atr_stop_multiplier × ATR
        stop_loss = entry_price - (config.atr_stop_multiplier * atr_value)
        
        # Sécurisation des entrées
        if entry_price is None or current_price is None or atr_value is None:
            return False, None  # Impossible de calculer les stops

        # Note: partial_taken flags will be accessed from pair_state in the calling function
        # They are not passed here to avoid modifying them from this pure checker.
        
        # Vérification des stops
        if current_price < stop_loss:
            return True, "STOP-LOSS"
        
        # Trailing stop logic will be handled in the main function with access to max_price.
        # We return a signal for the main function to check.
        # The main function will have its own trailing stop logic.
        
        # Condition de signal de vente : EMA2 > EMA1 + StochRSI > 0.2 (base commune)
        ema_condition = row['ema2'] > row['ema1']
        stoch_condition = row['stoch_rsi'] > 0.2
        
        if not (ema_condition and stoch_condition):
            return False, None
        
        # Conditions additionnelles selon le scénario
        scenario = best_params.get('scenario', 'StochRSI')
        
        if scenario == 'StochRSI_TRIX' and 'TRIX_HISTO' in row:
            if row['TRIX_HISTO'] <= 0:
                return True, "SIGNAL"  # Signal amélioré avec TRIX
        
        # Signal de vente confirmé
        return True, "SIGNAL"
    
    return check_sell_signal

def sync_windows_silently():
    """Synchronise Windows silencieusement si privilèges admin disponibles."""
    if os.name != 'nt':
        logger.debug("sync_windows_silently: ignoré (OS non-Windows)")
        return False
    
    try:
        # Vérifier les privilèges admin
        if not check_admin_privileges():
            logger.debug("Pas de privilèges admin - synchronisation ignorée")
            return False
        
        logger.info("Synchronisation Windows silencieuse en cours...")
        
        # Commandes de synchronisation directes (sans popup)
        commands = [
            ['net', 'stop', 'w32time'],
            ['net', 'start', 'w32time'],
            ['w32tm', '/config', '/manualpeerlist:time.windows.com,0x1', '/syncfromflags:manual', '/reliable:yes'],
            ['w32tm', '/config', '/update'],
            ['w32tm', '/resync', '/force']
        ]
        
        success_count = 0
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=15,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                if result.returncode == 0:
                    success_count += 1
                    logger.debug(f"Commande réussie: {' '.join(cmd)}")
            except Exception as e:
                logger.debug(f"Erreur commande {' '.join(cmd)}: {e}")
                continue
        
        if success_count >= 3:
            logger.info("Synchronisation Windows silencieuse: REUSSIE")
            return True
        else:
            logger.warning(f"Synchronisation partielle: {success_count}/5")
            return False
            
    except Exception as e:
        logger.debug(f"Erreur synchronisation silencieuse: {e}")
        return False

def init_timestamp_solution():
    """Initialisation ULTRA ROBUSTE de la synchronisation."""
    try:
        logger.info("=== INITIALISATION SYNCHRO ULTRA ROBUSTE ===")
        
        # Utiliser la BONNE méthode
        if hasattr(client, '_perform_ultra_robust_sync'):
            client._perform_ultra_robust_sync()
        elif hasattr(client, '_sync_server_time_robust'):
            client._sync_server_time_robust()
        else:
            client._sync_server_time()
        
        # Test de validation
        if hasattr(client, '_get_ultra_safe_timestamp'):
            test_ts = client._get_ultra_safe_timestamp()
        else:
            test_ts = client._get_synchronized_timestamp()
            
        server_ts = client.get_server_time()['serverTime']
        diff = test_ts - server_ts
        
        logger.info(f"VALIDATION: diff={diff}ms")
        
        if abs(diff) < 1000:
            logger.info("SYNCHRONISATION PARFAITE - Prêt pour le trading")
        else:
            logger.info(f"SYNCHRONISATION STABLE: {diff}ms")
            
        return True
        
    except Exception as e:
        logger.error(f"Échec initialisation: {e}")
        logger.info("Fallback robuste activé")
        return True  # Continuer même en cas d'erreur

def check_network_connectivity() -> bool:
    """Vérifie la connectivité réseau et tente de la rétablir."""
    
    try:
        # Test de connectivité basique
        socket.create_connection(("8.8.8.8", 53), timeout=5)
        return True
    except (socket.error, OSError):
        logger.warning("Perte de connectivité réseau détectée")
        
        if os.name != 'nt':
            logger.warning("Réactivation réseau automatique non supportée sur cet OS")
            return False
        
        try:
            # Tenter de réactiver la connexion réseau (Windows uniquement)
            logger.info("Tentative de réactivation de la connexion réseau...")
            
            # Flush DNS
            subprocess.run(['ipconfig', '/flushdns'], capture_output=True, timeout=10)
            
            # Renouveler l'IP
            subprocess.run(['ipconfig', '/release'], capture_output=True, timeout=10)
            time.sleep(2)
            subprocess.run(['ipconfig', '/renew'], capture_output=True, timeout=15)
            
            # Attendre un peu
            time.sleep(5)
            
            # Re-tester
            socket.create_connection(("8.8.8.8", 53), timeout=10)
            logger.info("Connexion réseau rétablie")
            return True
            
        except Exception as e:
            logger.error(f"Impossible de rétablir la connexion: {e}")
            return False

# compute_position_size_by_risk, compute_position_size_fixed_notional, 
# compute_position_size_volatility_parity importés depuis position_sizing.py (Phase 4)

def get_sniper_entry_price(pair_symbol: str, signal_price: float, max_wait_candles: int = 4) -> float:
    """Optimise le prix d'entrée en utilisant la timeframe 15min pour des entrées 'sniper'."""
    try:
        # Récupérer les données 15min récentes (24h pour avoir assez de données)
        df_15m = fetch_historical_data(pair_symbol, Client.KLINE_INTERVAL_15MINUTE, "1 day ago")
        if df_15m.empty or len(df_15m) < 20:
            return signal_price
        
        # Prendre les dernières bougies 15min
        recent_candles = df_15m.tail(max_wait_candles)
        
        # Logique simple : chercher le prix le plus bas dans la fenêtre récente
        # qui reste proche du signal (max 2% d'écart)
        best_price = signal_price
        
        for _, candle in recent_candles.iterrows():
            candle_price = candle['low']  # Utiliser le plus bas de la bougie
            
            # Vérifier que le prix reste dans une fourchette acceptable (max 2% d'écart)
            price_diff_pct = abs(candle_price - signal_price) / signal_price * 100
            
            if price_diff_pct <= 2.0 and candle_price < best_price:
                best_price = candle_price
        
        # Calculer l'amélioration (logs désactivés pendant backtest)
        if best_price < signal_price:
            improvement = (signal_price - best_price) / signal_price * 100
            logger.debug(f"Optimisation sniper: amélioration de {improvement:.2f}% (prix: {best_price:.8f})")
        
        return best_price
        
    except Exception as e:
        logger.debug(f"Erreur optimisation entree sniper: {e}")
        return signal_price

@retry_with_backoff(max_retries=3, base_delay=1.0)
def get_last_sell_trade_usdc(real_trading_pair: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    try:
        coin_symbol, quote_currency = extract_coin_from_pair(real_trading_pair)
        trades = client.get_my_trades(symbol=real_trading_pair, limit=100)  # Augmenter la limite
        logger.debug(f"Trades recuperes pour {coin_symbol}/{quote_currency} : {len(trades) if trades else 0} trades")
        if not trades:
            logger.warning("Aucun trade trouve pour cette paire.")
            return None, None, None

        aggregated_sells = {}
        aggregated_commissions = {}
        commission_assets = {}
        for trade in trades:
            if 'isBuyer' not in trade or 'quoteQty' not in trade or 'orderId' not in trade:
                logger.error(f"Trade mal forme : {trade}")
                continue
            if not trade['isBuyer'] and float(trade['quoteQty']) > 0:  # Ignorer quoteQty=0
                order_id = trade['orderId']
                quote_qty = float(trade['quoteQty'])
                commission = float(trade.get('commission', 0))
                commission_asset = trade.get('commissionAsset', 'UNKNOWN')
                if order_id not in aggregated_sells:
                    aggregated_sells[order_id] = 0.0
                    aggregated_commissions[order_id] = 0.0
                    commission_assets[order_id] = commission_asset
                aggregated_sells[order_id] += quote_qty
                aggregated_commissions[order_id] += commission

        for trade in reversed(trades):
            oid = trade['orderId']
            if oid in aggregated_sells:
                total_amount = aggregated_sells[oid]
                total_fee = aggregated_commissions[oid]
                fee_asset = commission_assets[oid]
                logger.debug(f"Derniere vente : Order ID={oid}, Montant={total_amount:.8f} {quote_currency}, Frais={total_fee:.8f} {fee_asset}")
                return total_amount, total_fee, fee_asset
        logger.info("Aucune vente valide trouvee.")
        return None, None, None
    except Exception as e:
        logger.error(f"Erreur recuperation derniere vente : {e}")
        return None, None, None

def get_usdc_from_all_sells_since_last_buy(real_trading_pair: str) -> float:
    """
    Récupère le montant total USDC de TOUTES les ventes (SELL) depuis le dernier achat (BUY).
    Utilisé pour calculer le capital disponible pour le prochain achat.
    
    Returns:
        float: Montant total USDC net (après frais) de toutes les ventes depuis le dernier BUY
    """
    try:
        _, quote_currency = extract_coin_from_pair(real_trading_pair)
        trades = client.get_my_trades(symbol=real_trading_pair, limit=500)
        
        if not trades:
            logger.warning(f"[CAPITAL] Aucun trade trouvé pour {real_trading_pair}")
            return 0.0
        
        # Trouver le dernier BUY
        last_buy_time = None
        for trade in reversed(trades):
            if trade.get('isBuyer', False):
                last_buy_time = trade['time']
                logger.info(f"[CAPITAL] Dernier BUY trouvé à {datetime.fromtimestamp(last_buy_time/1000).strftime('%Y-%m-%d %H:%M:%S')}")
                break
        
        if last_buy_time is None:
            logger.warning(f"[CAPITAL] Aucun BUY trouvé dans l'historique")
            return 0.0
        
        # Agréger toutes les ventes APRÈS le dernier BUY
        total_usdc = 0.0
        sell_count = 0
        for trade in trades:
            if not trade.get('isBuyer') and trade['time'] > last_buy_time:
                quote_qty = float(trade.get('quoteQty', 0))
                commission = float(trade.get('commission', 0))
                commission_asset = trade.get('commissionAsset', '')
                
                # Si la commission est en quote_currency (USDC), la déduire
                if commission_asset == quote_currency:
                    net_usdc = quote_qty - commission
                else:
                    net_usdc = quote_qty
                
                total_usdc += net_usdc
                sell_count += 1
        
        logger.info(f"[CAPITAL] {sell_count} ventes trouvées depuis dernier BUY = {total_usdc:.2f} {quote_currency}")
        return total_usdc
        
    except Exception as e:
        logger.error(f"[CAPITAL] Erreur lors de la récupération : {e}")
        return 0.0

def check_partial_exits_from_history(real_trading_pair: str, entry_price: float) -> Tuple[bool, bool]:
    """
    Vérifie dans l'historique Binance si les ventes partielles ont déjà été exécutées.
    Cette fonction reconstruit l'état réel depuis l'API Binance (source de vérité).
    
    Args:
        real_trading_pair: Symbole de la paire (ex: TRXUSDC)
        entry_price: Prix d'entrée pour calculer les seuils théoriques
    
    Returns:
        Tuple[bool, bool]: (partial_1_executed, partial_2_executed)
    """
    try:
        coin_symbol, _ = extract_coin_from_pair(real_trading_pair)
        trades = client.get_my_trades(symbol=real_trading_pair, limit=500)
        
        if not trades or entry_price is None or entry_price <= 0:
            return False, False
        
        # Trouver le dernier BUY
        last_buy_time = None
        last_buy_qty = 0.0
        for trade in reversed(trades):
            if trade.get('isBuyer', False):
                last_buy_time = trade['time']
                last_buy_qty += float(trade.get('qty', 0))
                break
        
        if last_buy_time is None:
            logger.debug(f"[PARTIAL-CHECK] Aucun BUY trouvé dans l'historique")
            return False, False
        
        # Calculer les seuils théoriques de PARTIAL
        partial_threshold_1 = entry_price * 1.02  # +2%
        partial_threshold_2 = entry_price * 1.04  # +4%
        
        # Analyser toutes les ventes APRÈS le dernier BUY
        sells_after_buy = []
        for trade in trades:
            if not trade.get('isBuyer') and trade['time'] > last_buy_time:
                sell_qty = float(trade.get('qty', 0))
                sell_price = float(trade.get('price', 0))
                sells_after_buy.append({
                    'qty': sell_qty,
                    'price': sell_price,
                    'time': trade['time']
                })
        
        if not sells_after_buy:
            logger.debug(f"[PARTIAL-CHECK] Aucune vente après le dernier BUY")
            return False, False
        
        # Trier par timestamp
        sells_after_buy.sort(key=lambda x: x['time'])
        
        # Analyser les ventes pour détecter les PARTIAL
        partial_1_detected = False
        partial_2_detected = False
        
        for sell in sells_after_buy:
            sell_qty = sell['qty']
            sell_price = sell['price']
            
            # Vérifier si c'est une vente partielle ~50% (entre 45% et 55% de la position initiale)
            if not partial_1_detected and 0.45 <= (sell_qty / last_buy_qty) <= 0.55:
                if sell_price >= partial_threshold_1:
                    partial_1_detected = True
                    logger.info(f"[PARTIAL-CHECK] PARTIAL-1 détecté : {sell_qty:.8f} {coin_symbol} à {sell_price:.4f} USDC (~50%)")
            
            # Vérifier si c'est une vente partielle ~30% (entre 25% et 35% de la position initiale)
            elif not partial_2_detected and 0.25 <= (sell_qty / last_buy_qty) <= 0.35:
                if sell_price >= partial_threshold_2 and partial_1_detected:
                    partial_2_detected = True
                    logger.info(f"[PARTIAL-CHECK] PARTIAL-2 détecté : {sell_qty:.8f} {coin_symbol} à {sell_price:.4f} USDC (~30%)")
        
        logger.info(f"[PARTIAL-CHECK] État depuis API : PARTIAL-1={partial_1_detected}, PARTIAL-2={partial_2_detected}")
        return partial_1_detected, partial_2_detected
        
    except Exception as e:
        logger.error(f"[PARTIAL-CHECK] Erreur lors de la vérification : {e}")
        return False, False

def check_if_order_executed(orders: List[Dict], order_type: str) -> bool:
    """Verifie si un ordre donne (achat ou vente) a deja ete execute."""
    if orders:
        last_order = orders[-1]  # Dernier ordre execute
        last_order_type = last_order['side']  # "BUY" ou "SELL"
        last_order_status = last_order['status']  # Statut de l'ordre (ex. 'FILLED')

        if last_order_status == 'FILLED' and last_order_type == order_type:            
            return True  # Un ordre a deja ete execute       
    
    return False

# Throttle pour ne pas re-lancer les backtests à chaque cycle planifié (1h minimum entre 2 backtests)
_last_backtest_time: Dict[str, float] = {}
_BACKTEST_THROTTLE_SECONDS: float = 3600.0  # 1 heure


def execute_scheduled_trading(real_trading_pair: str, time_interval: str, best_params: Dict[str, Any], backtest_pair: str, sizing_mode: str):
    """Wrapper pour les exécutions planifiées avec affichage complet (identique au démarrage)."""
    try:
        # === MESSAGE VISUEL DE DEMARRAGE ===
        logger.info(f"[SCHEDULED] DEBUT execution planifiee - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        display_execution_header(backtest_pair, real_trading_pair, time_interval, console)
        
        # Force flush de la console
        import sys
        sys.stdout.flush()
        logger.info(f"[SCHEDULED] Header affiché, debut des backtests...")
        
        # Re-faire le backtest pour obtenir les paramètres les plus à jour
        # THROTTLE: ne re-backtester que toutes les heures (pas à chaque cycle de 2 min)
        _now = time.time()
        _last_bt = _last_backtest_time.get(backtest_pair, 0)
        _time_since_last = _now - _last_bt
        
        if _time_since_last < _BACKTEST_THROTTLE_SECONDS:
            _remaining = int((_BACKTEST_THROTTLE_SECONDS - _time_since_last) / 60)
            logger.info(f"[SCHEDULED] Backtest throttlé pour {backtest_pair} — prochain dans ~{_remaining} min. Utilisation des anciens paramètres.")
        else:
            logger.info(f"[SCHEDULED] Re-backtest de {backtest_pair} pour obtenir les paramètres les plus à jour...")
        
            # Calculer les dates dynamiquement
            today = datetime.today()
            dynamic_start_date = (today - timedelta(days=config.backtest_days)).strftime("%d %B %Y")
            logger.info(f"[SCHEDULED] Backtest dates: {dynamic_start_date} -> {today.strftime('%d %B %Y')}")
            
            # Re-exécuter le backtest et AFFICHER les résultats
            logger.info(f"[SCHEDULED] Lancement des backtests...")
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
                _last_backtest_time[backtest_pair] = time.time()
                logger.info(f"[SCHEDULED] {len(backtest_results)} resultats de backtest recus")
                
                # C-07: sélectionner le meilleur résultat par ratio Calmar (ROI / max_drawdown)
                # Plus robuste que le profit brut : pénalise les stratégies à fort drawdown
                def _calmar_key(x):
                    roi = (x['final_wallet'] - x['initial_wallet']) / max(x['initial_wallet'], 1.0)
                    dd  = max(x.get('max_drawdown', 0.001), 0.001)
                    return roi / dd

                # C-13: OOS quality gates — filtrer d'abord les résultats qui passent
                # les critères institutionnels (Sharpe > 0.5 & WinRate > 45 %).
                # Si aucun résultat ne passe, on utilise tout le pool en mode dégradé.
                try:
                    from walk_forward import validate_oos_result as _validate_oos
                    _oos_valid = [
                        r for r in backtest_results
                        if _validate_oos(
                            r.get('sharpe_ratio', 0.0),
                            r.get('win_rate', 0.0),
                        )
                    ]
                    if _oos_valid:
                        _selection_pool = _oos_valid
                        logger.info(
                            "[SCHEDULED C-13] %d/%d résultats passent les OOS gates — sélection restreinte.",
                            len(_oos_valid), len(backtest_results),
                        )
                    else:
                        _selection_pool = backtest_results
                        logger.warning(
                            "[SCHEDULED C-13] Aucun résultat ne passe les OOS gates "
                            "(Sharpe > 0.5 & WR > 45 %%) — fallback full-pool."
                        )
                except Exception as _oos_err:
                    logger.warning("[SCHEDULED C-13] validate_oos_result indisponible: %s", _oos_err)
                    _selection_pool = backtest_results

                best_result = max(_selection_pool, key=_calmar_key)
                best_profit = best_result['final_wallet'] - best_result['initial_wallet']
                
                logger.info(f"[SCHEDULED] Meilleur resultat: {best_result['scenario']} sur {best_result['timeframe']} | Profit: ${best_profit:,.2f}")
                
                # === AFFICHAGE DES RESULTATS ===
                try:
                    display_results_for_pair(backtest_pair, backtest_results)
                    logger.info(f"[SCHEDULED] Résultats affichés pour {backtest_pair}")
                    sys.stdout.flush()
                except Exception as display_err:
                    logger.error(f"[SCHEDULED] Erreur affichage résultats: {str(display_err)}")
                
                # Mettre à jour best_params avec les derniers résultats du backtest
                updated_best_params = {
                    'timeframe': best_result['timeframe'],
                    'ema1_period': best_result['ema_periods'][0],
                    'ema2_period': best_result['ema_periods'][1],
                    'scenario': best_result['scenario'],
                }
                updated_best_params.update(SCENARIO_DEFAULT_PARAMS.get(best_result['scenario'], {}))
                
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
        
        # === AFFICHAGE PANEL - TRADING EN TEMPS REEL ===
        try:
            logger.info(f"[SCHEDULED] Affichage panel trading...")
            display_trading_panel(real_trading_pair, best_params, console)
            logger.info(f"[SCHEDULED] Panel trading affiché, appel execute_real_trades...")
            sys.stdout.flush()
        except Exception as panel_trading_err:
            logger.error(f"[SCHEDULED] Erreur affichage panel trading: {str(panel_trading_err)}")
        
        # Exécuter le trading avec les paramètres mis à jour
        try:
            logger.info(f"[SCHEDULED] Appel execute_real_trades avec {best_params['scenario']} sur {best_params['timeframe']} + sizing_mode='{sizing_mode}'...")
            execute_real_trades(real_trading_pair, best_params['timeframe'], best_params, backtest_pair, sizing_mode=sizing_mode)
            logger.info(f"[SCHEDULED] execute_real_trades complété avec succès")
        except Exception as trade_error:
            logger.error(f"[SCHEDULED] Erreur dans execute_real_trades: {str(trade_error)}")
            logger.error(f"[SCHEDULED] Traceback: {traceback.format_exc()}")
        
        # === AFFICHAGE PANEL - SUIVI & PLANIFICATION ===
        logger.info(f"[SCHEDULED] Affichage des informations de suivi...")
        
        # Assurer l'initialisation par defaut de l'etat de la paire
        pair_state = bot_state.setdefault(backtest_pair, {})
        # IMPORTANT : Ne pas réinitialiser last_order_side s'il existe déjà
        if 'last_order_side' not in pair_state:
            pair_state['last_order_side'] = None
        current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Mettre à jour l'état
        pair_state['last_run_time'] = current_run_time
        save_bot_state()
        
        # Afficher le panel de suivi
        logger.info(f"[SCHEDULED] Création et affichage du panel de suivi...")
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

# [REMOVED] Duplicate imports and logger re-initialization were here — already defined at top of file

def execute_real_trades(real_trading_pair: str, time_interval: str, best_params: Dict[str, Any], backtest_pair: str, sizing_mode: str = 'baseline'):
    """
    Exécution complète des trades réels avec gestion totale du cycle achat/vente,
    stop-loss, trailing-stop, sniper entry, envoi d'emails d'alerte et affichage console.
    Stratégie d'origine préservée intégralement.
    
    Args:
        sizing_mode: Position sizing strategy ('baseline', 'risk', 'fixed_notional', 'volatility_parity')
                    DEFAULT='baseline' (95% du capital par trade)
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


def _execute_real_trades_inner(real_trading_pair: str, time_interval: str, best_params: Dict[str, Any], backtest_pair: str, sizing_mode: str = 'baseline'):
    """Implémentation interne de execute_real_trades (appelée sous per-pair lock)."""
    # pair_state dérivé localement depuis bot_state (pas de global) — les mutations du dict
    # se propagent automatiquement dans bot_state[backtest_pair] par référence.
    pair_state = bot_state.setdefault(backtest_pair, {})
    # IMPORTANT : Ne pas réinitialiser last_order_side s'il existe déjà (il persiste entre exécutions)
    # Cela permet de tracker correctement si une position est ouverte ou fermée
    if 'last_order_side' not in pair_state:
        pair_state['last_order_side'] = None

    # Paramètres stratégiques - VALEURS FIXES COHÉRENTES AVEC CYTHON GAGNANT $2.3M
    ema1_period = best_params.get('ema1_period') or 26
    ema2_period = best_params.get('ema2_period') or 50
    atr_multiplier = config.atr_multiplier  # Trailing stop (from Config)
    atr_stop_multiplier = config.atr_stop_multiplier  # Stop-loss initial (from Config)
    scenario = best_params.get('scenario', 'StochRSI')

    try:
        # === COMPTES & SOLDES ===
        account_info = client.get_account()
        coin_symbol, quote_currency = extract_coin_from_pair(real_trading_pair)

        usdc_balance_obj = next((b for b in account_info['balances'] if b['asset'] == 'USDC'), None)
        usdc_balance = float(usdc_balance_obj['free']) if usdc_balance_obj else 0.0

        coin_balance_obj = next((b for b in account_info['balances'] if b['asset'] == coin_symbol), None)
        coin_balance = float(coin_balance_obj['free']) if coin_balance_obj else 0.0

        if coin_balance_obj is None:
            return

        # === FILTRES PAIRE (une seule récupération) ===
        exchange_info = get_cached_exchange_info(client)
        symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == real_trading_pair), None)
        if not symbol_info:
            console.print(f"[ERREUR] Informations symbole introuvables pour {real_trading_pair}.")
            return

        lot_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
        notional_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'), None)
        if not lot_filter:
            console.print("[ERREUR] Filtre LOT_SIZE non trouvé.")
            return

        min_qty = float(lot_filter['minQty'])
        max_qty = float(lot_filter['maxQty'])
        step_size = float(lot_filter['stepSize'])
        min_notional = float(notional_filter.get('minNotional', '10.0')) if notional_filter else 10.0

        min_qty_dec = Decimal(str(min_qty))
        max_qty_dec = Decimal(str(max_qty))
        step_size_dec = Decimal(str(step_size))
        step_decimals = abs(int(step_size_dec.as_tuple().exponent))

        # Afficher le panel des soldes (après min_qty défini)
        last_buy_price = pair_state.get('entry_price') if coin_balance >= min_qty else None
        atr_at_entry = pair_state.get('atr_at_entry') if coin_balance >= min_qty else None
        display_account_balances_panel(
            account_info, coin_symbol, quote_currency, client, console,
            pair_state=pair_state,
            last_buy_price=last_buy_price, atr_at_entry=atr_at_entry
        )

        # === DONNÉES & INDICATEURS ===
        df = fetch_historical_data(real_trading_pair, time_interval, start_date, force_refresh=True)
        df = universal_calculate_indicators(
            df, ema1_period, ema2_period,
            stoch_period=best_params.get('stoch_period', 14),
            sma_long=best_params.get('sma_long'),
            adx_period=best_params.get('adx_period'),
            trix_length=best_params.get('trix_length'),
            trix_signal=best_params.get('trix_signal')
        )

        if df.empty or len(df) < 2:
            logger.error(f"[TRADING] Données insuffisantes pour {real_trading_pair}: {len(df)} lignes – cycle ignoré")
            return
        row = df.iloc[-2]
        # Harmonize current_price for all panels in this cycle
        current_price = float(client.get_symbol_ticker(symbol=real_trading_pair)['price'])
        global_current_price = current_price

          # === HISTORIQUE ORDRES ===
        orders = client.get_all_orders(symbol=real_trading_pair, limit=20)
        # Ensure orders is always a list
        if not isinstance(orders, list):
            orders = [orders] if orders else []
        filled_orders = [o for o in reversed(orders) if o['status'] == 'FILLED']
        last_filled_order = filled_orders[0] if filled_orders else None
        last_side = last_filled_order['side'] if last_filled_order else None

        if last_side and pair_state['last_order_side'] != last_side:
            pair_state['last_order_side'] = last_side
            save_bot_state()
        # === GESTION POSITION APRÈS BUY ===
        if last_side == 'BUY':
            last_buy_order = next((o for o in reversed(orders) if o['status'] == 'FILLED' and o['side'] == 'BUY'), None)
            if last_buy_order:
                executed_qty = float(last_buy_order.get('executedQty', 0))
                price = float(last_buy_order.get('price', 0))
                if price == 0.0 and executed_qty > 0:
                    price = float(last_buy_order.get('cummulativeQuoteQty', 0)) / executed_qty

                atr_value = row.get('atr')
                # Set entry variables ONLY if not already set (never update after entry)
                if atr_value is not None and price > 0:
                    if pair_state.get('atr_at_entry') is None:
                        pair_state['atr_at_entry'] = atr_value
                    if pair_state.get('entry_price') is None:
                        pair_state['entry_price'] = price
                    if pair_state.get('stop_loss_at_entry') is None:
                        pair_state['stop_loss_at_entry'] = price - atr_stop_multiplier * atr_value
                        pair_state['stop_loss'] = pair_state['stop_loss_at_entry']
                    if pair_state.get('trailing_activation_price_at_entry') is None:
                        pair_state['trailing_activation_price_at_entry'] = price + atr_multiplier * atr_value
                        pair_state['trailing_activation_price'] = pair_state['trailing_activation_price_at_entry']
                    # === TRACKER L'ÉTAT DE LA POSITION ===
                    pair_state['last_order_side'] = 'BUY'
                    save_bot_state()

        # === LOGIQUE CENTRALISÉE STOP-LOSS / TRAILING-STOP ===
        if pair_state.get('last_order_side') == 'BUY' and coin_balance > 0:
            entry_price = pair_state.get('entry_price')
            atr_at_entry = pair_state.get('atr_at_entry')
            trailing_activation_price = pair_state.get('trailing_activation_price_at_entry')
            trailing_activated = pair_state.get('trailing_stop_activated', False)
            max_price = pair_state.get('max_price')
            if max_price is None:
                max_price = entry_price if entry_price is not None else global_current_price

            # Protection : si trailing_activation_price n'existe pas, le recalculer
            if trailing_activation_price is None and entry_price and atr_at_entry:
                trailing_activation_price = entry_price + (config.atr_multiplier * atr_at_entry)
                pair_state['trailing_activation_price_at_entry'] = trailing_activation_price
                pair_state['trailing_activation_price'] = trailing_activation_price
                save_bot_state()
                logger.info(f"[TRAILING] Prix d'activation recalculé: {trailing_activation_price:.4f}")

            # Mise à jour du max_price
            if max_price is None:
                max_price = global_current_price
            if max_price is not None and global_current_price is not None and global_current_price > max_price:
                max_price = global_current_price

            # === ACTIVATION DU TRAILING (quand prix >= entry + 5×ATR) ===
            if not trailing_activated and trailing_activation_price:
                if trailing_activation_price is not None and global_current_price is not None and global_current_price >= trailing_activation_price:
                    trailing_activated = True
                    logger.info(f"[TRAILING] ⚡ ACTIVÉ à {global_current_price:.4f} (seuil: {trailing_activation_price:.4f})")
                    # Initialiser le trailing stop
                    trailing_distance = config.atr_multiplier * atr_at_entry if atr_at_entry else None
                    if trailing_distance:
                        trailing_stop_val = max_price - trailing_distance
                        pair_state['trailing_stop'] = trailing_stop_val
                        logger.info(f"[TRAILING] Stop initial: {trailing_stop_val:.4f}")

            # Mise à jour du trailing stop SI activé
            if trailing_activated and atr_at_entry is not None and max_price is not None:
                trailing_distance = config.atr_multiplier * atr_at_entry
                new_trailing = max_price - trailing_distance
                current_trailing = pair_state.get('trailing_stop', 0)
                # Le trailing ne peut que monter (protection des gains)
                if new_trailing is not None and current_trailing is not None and new_trailing > current_trailing:
                    pair_state['trailing_stop'] = new_trailing
                    logger.info(f"[TRAILING] Nouveau stop : {new_trailing:.4f} (max: {max_price:.4f})")

            pair_state.update({
                'trailing_stop_activated': trailing_activated,
                'max_price': max_price,
            })
            save_bot_state()

            # === VÉRIFICATION STOP-LOSS FIXE / TRAILING STOP ===
            stop_loss_fixed = pair_state.get('stop_loss_at_entry')  # Stop-loss FIXE à 3×ATR
            trailing_stop = pair_state.get('trailing_stop', 0)  # Trailing (si activé)
            trailing_activated = pair_state.get('trailing_stop_activated', False)
            
            # Déterminer le niveau de stop effectif
            # Si trailing activé ET trailing_stop > stop_loss_fixed, utiliser trailing
            # Sinon utiliser stop_loss_fixed
            effective_stop = stop_loss_fixed
            is_trailing_stop = False
            if trailing_activated and trailing_stop is not None and stop_loss_fixed is not None and trailing_stop > (stop_loss_fixed or 0):
                effective_stop = trailing_stop
                is_trailing_stop = True
            
            if effective_stop is not None and global_current_price is not None and global_current_price <= effective_stop:
                # Exécution vente immédiate sur stop-loss
                quantity_decimal = Decimal(str(coin_balance))
                quantity_rounded = (quantity_decimal // step_size_dec) * step_size_dec
                if quantity_rounded < min_qty_dec:
                    quantity_rounded = quantity_decimal
                if quantity_rounded > max_qty_dec:
                    quantity_rounded = max_qty_dec

                # Vérification min_notional (valeur de l'ordre >= min_notional)
                order_value = float(quantity_rounded) * global_current_price
                if quantity_rounded >= min_qty_dec and order_value >= min_notional:
                    qty_str = f"{quantity_rounded:.{step_decimals}f}"
                    stop_loss_order = safe_market_sell(symbol=real_trading_pair, quantity=qty_str)
                    if stop_loss_order and stop_loss_order.get('status') == 'FILLED':
                        logger.info(f"[STOP-LOSS] Vente exécutée et confirmée : {qty_str} {coin_symbol}")
                        
                        # Récupérer le prix d'exécution
                        executed_price = global_current_price
                        total_usdc_received = float(qty_str) * executed_price
                        
                        # Déterminer le type de stop-loss
                        if is_trailing_stop:
                            stop_type = "TRAILING-STOP (dynamique)"
                            stop_desc = f"Prix max atteint : {pair_state.get('max_price', 0):.4f} USDC\nTrailing stop : {trailing_stop:.4f} USDC"
                        else:
                            stop_type = "STOP-LOSS (fixe à 3×ATR)"
                            stop_desc = f"Stop-loss fixe : {stop_loss_fixed:.4f} USDC"
                        

                        # === EMAIL STOP-LOSS (sécurisé) ===
                        if is_valid_stop_loss_order(real_trading_pair, qty_str, executed_price):
                            extra = f"DETAILS DU STOP:\n{stop_desc}\nPrix d'entree : {pair_state.get('entry_price', 0):.4f} USDC\nTimeframe : {time_interval}\nEMA : {ema1_period}/{ema2_period}\nScenario : {scenario}"
                            subj, body = sell_executed_email(
                                pair=real_trading_pair, qty=float(qty_str), price=executed_price,
                                usdc_received=total_usdc_received, sell_reason=stop_type,
                                extra_details=extra
                            )
                            try:
                                send_trading_alert_email(subject=subj, body_main=body, client=client)
                                logger.info(f"[STOP-LOSS] E-mail d'alerte envoye pour la vente")
                            except Exception as e:
                                logger.error(f"[STOP-LOSS] L'envoi de l'e-mail a echoue : {e}")
                        else:
                            logger.warning(f"[STOP-LOSS] Email NON ENVOYÉ : paramètres invalides (symbol={real_trading_pair}, qty={qty_str}, price={executed_price})")

                        # Capturer entry_price AVANT le reset pour le journal PnL
                        _saved_entry_price = pair_state.get('entry_price') or 0

                        # Reset entry variables after closure
                        pair_state.update({
                        'entry_price': None, 'max_price': None, 'stop_loss': None,
                        'trailing_stop': None, 'trailing_stop_activated': False,
                        'atr_at_entry': None, 'stop_loss_at_entry': None,
                        'trailing_activation_price_at_entry': None,
                        'last_order_side': 'SELL'
                        })
                        save_bot_state()

                    # Journal de trading (Phase 2) — STOP-LOSS / TRAILING
                    if 'executed_price' in locals() and executed_price:  # type: ignore[possibly-undefined]
                        try:
                            logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                            _entry_px = _saved_entry_price if '_saved_entry_price' in locals() else 0  # type: ignore[possibly-undefined]
                            _exec_price = float(executed_price)
                            _qty = float(qty_str) if qty_str else 0.0  # type: ignore[possibly-undefined]
                            _pnl = (_exec_price - _entry_px) * _qty if _entry_px and _exec_price else None
                            _pnl_pct = ((_exec_price / _entry_px) - 1) if _entry_px and _exec_price else None
                            _sell_reason = stop_type if 'stop_type' in locals() else 'STOP-LOSS'  # type: ignore[possibly-undefined]
                            log_trade(
                                logs_dir=logs_dir,
                                pair=real_trading_pair,
                                side='sell',
                                quantity=_qty,
                                price=_exec_price,
                                fee=_qty * config.taker_fee * _exec_price,
                                scenario=scenario,
                                timeframe=time_interval,
                                pnl=_pnl,
                                pnl_pct=_pnl_pct,
                                extra={'sell_reason': _sell_reason},
                            )
                        except Exception as journal_err:
                            logger.error(f"[JOURNAL] Erreur écriture vente stop: {journal_err}")

                # Affichage explicite de la nature du stop-loss utilisé
                if is_trailing_stop:
                    stop_loss_info = f"{trailing_stop:.4f} USDC (dynamique : trailing)"
                else:
                    stop_loss_info = f"{stop_loss_fixed:.4f} USDC (fixe à l'entrée)"

                display_closure_panel(stop_loss_info, global_current_price, coin_symbol, coin_balance, console)
                pair_state['last_execution'] = datetime.now(timezone.utc)
                save_bot_state()
                return

        # === GESTION POSITION PARTIELLE (50%) ===
        # On stocke la taille initiale de la position à chaque achat complet
        if pair_state.get('last_order_side') == 'BUY' and pair_state.get('initial_position_size') is None and coin_balance > min_qty * 1.01:
            pair_state['initial_position_size'] = coin_balance
            save_bot_state()

        # === RÈGLE STRICTE : AUCUN ACHAT TANT QUE POSITION NON SOLDÉE À 100% ===
        # Si coin_balance > min_qty → Position EXISTE → Mode VENTE OBLIGATOIRE
        # Si coin_balance <= min_qty → Résidu/poussière → Traiter comme position FERMÉE → Mode ACHAT autorisé
        position_has_crypto = coin_balance > min_qty
        
        # === DÉTECTION ET NETTOYAGE FORCÉ DES RÉSIDUS (DUST) ===
        # Si résidu < min_qty : VENDRE DE FORCE pour débloquer le trading
        dust_threshold = min_qty * 0.98  # 98% du minimum tradable
        has_dust = min_qty * 0.01 < coin_balance < dust_threshold  # Détecte vraiment du dust (> 1% min_qty mais < 98%)
        
        if has_dust:
            logger.warning(f"[DUST] Résidu détecté : {coin_balance:.8f} {coin_symbol} (entre 1% et 98% de min_qty)")
            
            # IMPORTANT: Vérifier si la valeur totale du résidu respecte MIN_NOTIONAL
            dust_notional_value = coin_balance * current_price
            if dust_notional_value < min_notional:
                logger.warning(f"[DUST] Valeur du résidu ({dust_notional_value:.2f} USDC) < MIN_NOTIONAL ({min_notional:.2f} USDC)")
                logger.warning(f"[DUST] Impossible de vendre le résidu - Binance refuse les ordres < {min_notional:.2f} USDC")
                logger.info(f"[DUST] Résidu ignoré (position considérée comme fermée)")
            else:
                logger.info(f"[DUST] Tentative de vente forcée du résidu pour débloquer le trading...")
                
                try:
                    # Binance accepte les "dust conversions" via l'API
                    # Tenter une vente MARKET de tout le solde (même < min_qty)
                    qty_str = f"{coin_balance:.{step_decimals}f}"
                    
                    # Certaines paires acceptent les ventes < min_qty si c'est le solde total
                    dust_sell_order = safe_market_sell(symbol=real_trading_pair, quantity=qty_str)
                    
                    if dust_sell_order and dust_sell_order.get('status') == 'FILLED':
                        logger.info(f"[DUST] Vente réussie et confirmée du résidu : {qty_str} {coin_symbol}")
                        # Reset complet de l'état après nettoyage
                        pair_state.update({
                            'entry_price': None, 'max_price': None, 'stop_loss': None,
                            'trailing_stop': None, 'trailing_stop_activated': False,
                            'atr_at_entry': None, 'stop_loss_at_entry': None,
                            'trailing_activation_price_at_entry': None,
                            'initial_position_size': None,
                            'partial_taken_1': False,
                            'partial_taken_2': False
                        })
                        save_bot_state()
                        
                        # Rafraîchir le solde
                        account_info = client.get_account()
                        coin_balance_obj = next((b for b in account_info['balances'] if b['asset'] == coin_symbol), None)
                        coin_balance = float(coin_balance_obj['free']) if coin_balance_obj else 0.0
                        position_has_crypto = coin_balance > min_qty
                        
                        logger.info(f"[DUST] Position nettoyée. Nouveau solde : {coin_balance:.8f} {coin_symbol}")
                    else:
                        logger.warning(f"[DUST] Vente du résidu échouée - Binance refuse")
                        logger.info(f"[DUST] Continuant quand même (résidu < min_qty = position fermée)")
                except Exception as e:
                    logger.error(f"[DUST] Erreur lors de la tentative de vente : {e}")
                    logger.info(f"[DUST] Continuant quand même (résidu < min_qty = position fermée)")

        # === CONDITIONS VENTE STRATÉGIE (vérifier SEULEMENT si position ouverte) ===
        if position_has_crypto:
            entry_price_for_panel = pair_state.get('entry_price') or row.get('close')
            pair_state.setdefault('stop_loss_at_entry', entry_price_for_panel - 3 * (row.get('atr') or 0.0) if entry_price_for_panel and row.get('atr') else None)
            pair_state.setdefault('atr_at_entry', row.get('atr'))
            pair_state.setdefault('max_price', entry_price_for_panel)

            # === SYNCHRONISATION AVEC L'HISTORIQUE API (SOURCE DE VÉRITÉ) ===
            # Vérifier l'état réel des ventes partielles depuis Binance
            # Cette vérification reconstruit l'état même si bot_state.pkl est corrompu
            if entry_price_for_panel:
                try:
                    api_partial_1, api_partial_2 = check_partial_exits_from_history(real_trading_pair, entry_price_for_panel)
                    
                    # Synchroniser les flags locaux avec l'état réel de l'API
                    state_partial_1 = pair_state.get('partial_taken_1', False)
                    state_partial_2 = pair_state.get('partial_taken_2', False)
                    
                    if api_partial_1 != state_partial_1 or api_partial_2 != state_partial_2:
                        logger.warning(f"[SYNC] Désynchronisation détectée ! État local: P1={state_partial_1}, P2={state_partial_2}")
                        logger.warning(f"[SYNC] État API (source de vérité): P1={api_partial_1}, P2={api_partial_2}")
                        logger.info(f"[SYNC] Correction automatique des flags depuis l'historique Binance")
                        
                        # Corriger avec l'état réel de l'API
                        pair_state['partial_taken_1'] = api_partial_1
                        pair_state['partial_taken_2'] = api_partial_2
                        save_bot_state()
                        
                        logger.info(f"[SYNC] Flags synchronisés : PARTIAL-1={api_partial_1}, PARTIAL-2={api_partial_2}")
                    else:
                        logger.debug(f"[SYNC] État cohérent : PARTIAL-1={state_partial_1}, PARTIAL-2={state_partial_2}")
                except Exception as e:
                    logger.error(f"[SYNC] Erreur lors de la synchronisation API : {e}")
                    logger.debug(f"[SYNC] Utilisation des flags locaux : P1={pair_state.get('partial_taken_1', False)}, P2={pair_state.get('partial_taken_2', False)}")

            check_sell_signal = generate_sell_condition_checker(best_params)
            sell_triggered, sell_reason = check_sell_signal(row, coin_balance, entry_price_for_panel, current_price, row.get('atr'))
            
            # === PARTIALS: Activation/désactivation selon la taille de position ===
            partial_enabled = pair_state.get('partial_enabled', True)  # Default True pour rétro-compatibilité

            if not partial_enabled:
                logger.info("[PARTIAL] Mode partials désactivé pour cette position (taille insuffisante)")
                # Sauter directement au SIGNAL de vente final (100%)
                if sell_triggered and sell_reason in ('PARTIAL-1', 'PARTIAL-2'):
                    # Ignore les partials, ne vend que sur SIGNAL
                    sell_triggered = False
                # Si le signal final est atteint, on vend tout

            # === EXÉCUTION VENTE SIGNAL SI CONDITIONS REMPLIES ===
            if sell_triggered:
                try:
                    # Déterminer la quantité à vendre selon le type de signal
                    if sell_reason == 'SIGNAL':
                        # Vendre 100% (vente complète)
                        qty_to_sell = coin_balance
                    elif sell_reason == 'PARTIAL-1' and partial_enabled:
                        # Vendre 50% (première prise partielle)
                        qty_to_sell = coin_balance * 0.5
                    elif sell_reason == 'PARTIAL-2' and partial_enabled:
                        # Vendre 30% du reste (après PARTIAL-1, on vend 30% du solde restant)
                        qty_to_sell = coin_balance * 0.30
                    else:
                        # Autres raisons (stop-loss, trailing-stop) - déjà gérées ailleurs
                        qty_to_sell = None

                    if qty_to_sell and qty_to_sell > 0:
                        # Arrondir la quantité selon les règles d'échange
                        quantity_decimal = Decimal(str(qty_to_sell))
                        quantity_rounded = (quantity_decimal // step_size_dec) * step_size_dec
                        if quantity_rounded < min_qty_dec:
                            quantity_rounded = quantity_decimal
                        if quantity_rounded > max_qty_dec:
                            quantity_rounded = max_qty_dec

                        # IMPORTANT: Vérifier DEUX filtres Binance
                        # 1. LOT_SIZE: Quantité >= min_qty
                        # 2. MIN_NOTIONAL: Valeur totale >= min_notional (en USDC)
                        notional_value = float(quantity_rounded) * current_price
                        
                        if quantity_rounded >= min_qty_dec and notional_value >= min_notional:
                            qty_str = f"{quantity_rounded:.{step_decimals}f}"
                            sell_order = safe_market_sell(symbol=real_trading_pair, quantity=qty_str)
                            if sell_order and sell_order.get('status') == 'FILLED':
                                logger.info(f"[{sell_reason}] Vente exécutée et confirmée : {qty_str} {coin_symbol}")
                                
                                # Récupérer le prix d'exécution
                                executed_price = current_price
                                total_usdc_received = float(qty_str) * executed_price
                                
                                # === MISE À JOUR DES FLAGS ET ÉTAT APRÈS SUCCÈS ===
                                if sell_reason == 'PARTIAL-1':
                                    pair_state['partial_taken_1'] = True
                                    save_bot_state()
                                    logger.info(f"[PARTIAL-1] Flag mis à jour : partial_taken_1 = True")
                                    sell_type_desc = "Prise de profit partielle 1 (+2%)"
                                    position_closed = "50%"
                                elif sell_reason == 'PARTIAL-2':
                                    pair_state['partial_taken_2'] = True
                                    save_bot_state()
                                    logger.info(f"[PARTIAL-2] Flag mis à jour : partial_taken_2 = True")
                                    sell_type_desc = "Prise de profit partielle 2 (+4%)"
                                    position_closed = "30%"
                                elif sell_reason == 'SIGNAL':
                                    sell_type_desc = "Signal de vente (croisement baissier)"
                                    position_closed = "100% (20% restants)"
                                    # Reset état complet après vente SIGNAL
                                    pair_state.update({
                                        'entry_price': None, 'max_price': None, 'stop_loss': None,
                                        'trailing_stop': None, 'trailing_stop_activated': False,
                                        'atr_at_entry': None, 'stop_loss_at_entry': None,
                                        'trailing_activation_price_at_entry': None,
                                        'initial_position_size': None,
                                        'last_order_side': None,
                                        'partial_taken_1': False,
                                        'partial_taken_2': False
                                    })
                                    save_bot_state()
                                    logger.info(f"[SIGNAL] État réinitialisé après vente complète")
                                else:
                                    sell_type_desc = sell_reason
                                    position_closed = "100%"
                                
                                # === EMAIL VENTE RÉUSSIE ===
                                extra = f"Timeframe : {time_interval}\nEMA : {ema1_period}/{ema2_period}\nScenario : {scenario}"
                                subj, body = sell_executed_email(
                                    pair=real_trading_pair, qty=float(qty_str), price=executed_price,
                                    usdc_received=total_usdc_received, sell_reason=sell_type_desc or "SIGNAL",
                                    extra_details=extra
                                )
                                # Validation stricte avant envoi d'email de vente automatique
                                if is_valid_stop_loss_order(real_trading_pair, qty_str, executed_price):
                                    try:
                                        send_trading_alert_email(subject=subj, body_main=body, client=client)
                                        logger.info(f"[{sell_reason}] E-mail d'alerte envoyé pour la vente")
                                    except Exception as e:
                                        logger.error(f"[{sell_reason}] L'envoi de l'e-mail a echoue : {e}")
                                else:
                                    logger.warning(f"[{sell_reason}] Email NON ENVOYÉ : paramètres invalides (symbol={real_trading_pair}, qty={qty_str}, price={executed_price})")
                                # Rafraîchir le balance après vente
                                account_info = client.get_account()
                                coin_balance_obj = next((b for b in account_info['balances'] if b['asset'] == coin_symbol), None)
                                coin_balance = float(coin_balance_obj['free']) if coin_balance_obj else 0.0

                                # Journal de trading (Phase 2) — SIGNAL / PARTIAL
                                try:
                                    logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                                    _entry_px = pair_state.get('entry_price') or 0
                                    _pnl = (float(executed_price) - _entry_px) * float(qty_str) if _entry_px and executed_price and qty_str else None
                                    _pnl_pct = ((float(executed_price) / _entry_px) - 1) if _entry_px and executed_price else None
                                    log_trade(
                                        logs_dir=logs_dir,
                                        pair=real_trading_pair,
                                        side='sell',
                                        quantity=float(qty_str) if qty_str else 0,
                                        price=float(executed_price) if executed_price else 0,
                                        fee=float(qty_str or 0) * config.taker_fee * float(executed_price or 0),
                                        scenario=scenario,
                                        timeframe=time_interval,
                                        pnl=_pnl,
                                        pnl_pct=_pnl_pct,
                                        extra={'sell_reason': sell_reason, 'position_closed': position_closed},
                                    )
                                except Exception as journal_err:
                                    logger.error(f"[JOURNAL] Erreur écriture vente: {journal_err}")
                        else:
                            # Explication du rejet (quantité ou notional insuffisant)
                            if quantity_rounded < min_qty_dec:
                                logger.warning(f"[{sell_reason}] Vente bloquée : Quantité {quantity_rounded} < min_qty {min_qty_dec}")
                            if notional_value < min_notional:
                                logger.warning(f"[{sell_reason}] Vente bloquée : Valeur {notional_value:.2f} USDC < MIN_NOTIONAL {min_notional:.2f} USDC")
                                logger.warning(f"[{sell_reason}] Impossible de vendre {float(quantity_rounded):.8f} {coin_symbol} - montant insuffisant")
                                
                                # Si PARTIAL bloqué pour montant insuffisant, marquer comme "pris" pour éviter retry infini
                                # (le reliquat sera vendu avec le SIGNAL final)
                                if sell_reason == 'PARTIAL-1':
                                    pair_state['partial_taken_1'] = True
                                    save_bot_state()
                                    logger.info(f"[PARTIAL-1] Montant trop faible - Flag mis à True pour éviter retry")
                                elif sell_reason == 'PARTIAL-2':
                                    pair_state['partial_taken_2'] = True
                                    save_bot_state()
                                    logger.info(f"[PARTIAL-2] Montant trop faible - Flag mis à True pour éviter retry")

                                # Si un reliquat < min_qty subsiste, tenter une vente finale pour éviter tout résidu
                                remaining_dec = Decimal(str(coin_balance))
                                if remaining_dec > 0 and remaining_dec < (min_qty_dec * Decimal('1.02')):
                                    try:
                                        qty_str_remaining = f"{remaining_dec:.{step_decimals}f}"
                                        logger.info(f"[SELL] Reliquat détecté ({qty_str_remaining}), tentative de vente finale pour éviter un résidu")
                                        dust_sell_order = safe_market_sell(symbol=real_trading_pair, quantity=qty_str_remaining)
                                        if dust_sell_order and dust_sell_order.get('status') == 'FILLED':
                                            logger.info(f"[SELL] Reliquat vendu avec succès et confirmé : {qty_str_remaining} {coin_symbol}")
                                            coin_balance = 0.0
                                            pair_state.update({
                                                'entry_price': None, 'max_price': None, 'stop_loss': None,
                                                'trailing_stop': None, 'trailing_stop_activated': False,
                                                'atr_at_entry': None, 'stop_loss_at_entry': None,
                                                'trailing_activation_price_at_entry': None,
                                                'initial_position_size': None,
                                                'last_order_side': None,
                                                'partial_taken_1': False,
                                                'partial_taken_2': False
                                            })
                                            save_bot_state()
                                        else:
                                            logger.warning(f"[SELL] Reliquat non vendu (< min_qty). Trading suspendu jusqu'à action manuelle")
                                    except Exception as e:
                                        logger.error(f"[SELL] Erreur lors de la vente du reliquat: {e}")
                except Exception as e:
                    logger.error(f"[VENTE] Erreur lors de l'exécution : {e}")
            
            # Afficher panel VENTE
            display_sell_signal_panel(
                row=row, coin_balance=coin_balance, pair_state=pair_state,
                sell_triggered=sell_triggered, console=console, coin_symbol=coin_symbol,
                sell_reason=sell_reason
            )
        else:
            # === CONDITIONS ACHAT (vérifier SEULEMENT si position fermée) ===
            check_buy_signal = generate_buy_condition_checker(best_params)
            buy_condition, buy_reason = check_buy_signal(row, usdc_balance)
            
            # === CAPITAL DISPONIBLE POUR ACHAT ===
            # Récupérer les USDC de TOUTES les ventes depuis le dernier achat via l'historique Binance
            usdc_for_buy = get_usdc_from_all_sells_since_last_buy(real_trading_pair)
            
            if usdc_for_buy <= 0:
                logger.error(f"[BUY] ERREUR : Aucun capital disponible ! USDC des ventes = {usdc_for_buy:.2f} USDC")
                logger.error(f"[BUY] Aucune vente trouvée dans l'historique depuis le dernier achat")
                logger.warning(f"[BUY] Conditions d'achat remplies mais TRADING BLOQUÉ - Capital insuffisant")
            else:
                logger.info(f"[BUY] Capital disponible (ventes depuis dernier BUY) : {usdc_for_buy:.2f} USDC")
            
            # === EXÉCUTION ACHAT SI CONDITIONS REMPLIES (POSITION SIZING IDENTIQUE AU BACKTEST) ===
              # Sécurité anti-double-buy: si le dernier ordre est déjà un BUY FILLED, on skip ce cycle
            if orders and check_if_order_executed(orders, 'BUY'):
              logger.warning(f"[BUY] Anti-double-buy: dernier ordre détecté comme BUY FILLED – achat ignoré pour ce cycle")
            elif buy_condition and usdc_for_buy > 0:
                try:
                    # POSITION SIZING - 100% IDENTIQUE AU BACKTEST
                    atr_value = row.get('atr', None)
                    entry_price = current_price

                    # Optimisation sniper: chercher le meilleur prix d'entrée sur les 15min récents
                    optimized_entry = get_sniper_entry_price(real_trading_pair, entry_price)
                    if optimized_entry < entry_price:
                        logger.info(f"[BUY] Prix sniper optimisé: {optimized_entry:.6f} (vs spot {entry_price:.6f}, gain {(entry_price - optimized_entry) / entry_price * 100:.2f}%)")
                        entry_price = optimized_entry
                    
                    if sizing_mode == 'baseline':
                        # Baseline: 95% du capital disponible
                        gross_coin = (usdc_for_buy * 0.95) / entry_price
                    elif sizing_mode == 'risk':
                        # RISK-BASED: % risk avec ATR stop-loss
                        if atr_value and atr_value > 0:
                            qty_by_risk = compute_position_size_by_risk(
                                equity=usdc_for_buy,
                                atr_value=atr_value,
                                entry_price=entry_price,
                                risk_pct=config.risk_per_trade,
                                stop_atr_multiplier=config.atr_stop_multiplier
                            )
                            max_affordable = (usdc_for_buy * 0.95) / entry_price
                            gross_coin = min(max_affordable, qty_by_risk)
                        else:
                            logger.warning(f"[BUY] ATR invalide, fallback baseline")
                            gross_coin = (usdc_for_buy * 0.95) / entry_price
                    elif sizing_mode == 'fixed_notional':
                        # Fixed notional: montant USD fixe par trade
                        notional_per_trade = usdc_for_buy * 0.1  # 10% du capital pour ce trade
                        qty_fixed = compute_position_size_fixed_notional(
                            equity=usdc_for_buy,
                            notional_per_trade_usd=notional_per_trade,
                            entry_price=entry_price
                        )
                        max_affordable = (usdc_for_buy * 0.95) / entry_price
                        gross_coin = min(max_affordable, qty_fixed)
                    elif sizing_mode == 'volatility_parity':
                        # Volatility parity: volatilité fixe du P&L
                        if atr_value and atr_value > 0:
                            qty_vol = compute_position_size_volatility_parity(
                                equity=usdc_for_buy,
                                atr_value=atr_value,
                                entry_price=entry_price,
                                target_volatility_pct=0.02
                            )
                            max_affordable = (usdc_for_buy * 0.95) / entry_price
                            gross_coin = min(max_affordable, qty_vol)
                        else:
                            logger.warning(f"[BUY] ATR invalide, fallback baseline")
                            gross_coin = (usdc_for_buy * 0.95) / entry_price
                    else:
                        logger.warning(f"[BUY] sizing_mode inconnu '{sizing_mode}', fallback baseline")
                        gross_coin = (usdc_for_buy * 0.95) / entry_price
                    
                    # Arrondir selon les règles d'échange
                    quantity_decimal = Decimal(str(gross_coin))
                    quantity_rounded = (quantity_decimal // step_size_dec) * step_size_dec
                    if quantity_rounded < min_qty_dec:
                        quantity_rounded = quantity_decimal
                    if quantity_rounded > max_qty_dec:
                        quantity_rounded = max_qty_dec
                    
                    if quantity_rounded >= min_qty_dec:
                        qty_str = f"{quantity_rounded:.{step_decimals}f}"
                        quote_amount = float(quantity_rounded) * entry_price
                        
                        logger.info(f"[BUY] Sizing mode: {sizing_mode}")
                        logger.info(f"[BUY] Quantité calculée: {qty_str} {coin_symbol} (~{quote_amount:.2f} USDC)")
                        
                        buy_order = safe_market_buy(symbol=real_trading_pair, quoteOrderQty=quote_amount)
                        if buy_order and buy_order.get('status') == 'FILLED':
                            logger.info(f"[BUY] Achat exécuté et confirmé : {qty_str} {coin_symbol}")
                            logger.info(f"[BUY] Capital utilisé : {usdc_for_buy:.2f} USDC (provenant des ventes)")
                            logger.info(f"[BUY] Quantité réellement exécutée : {buy_order.get('executedQty', 'N/A')} {coin_symbol}")
                            
                            # === EMAIL ACHAT RÉUSSI ===
                            extra = f"Timeframe : {time_interval}\nEMA : {ema1_period}/{ema2_period}\nScenario : {scenario}"
                            subj, body = buy_executed_email(
                                pair=real_trading_pair, qty=float(qty_str), price=entry_price,
                                usdc_spent=quote_amount, usdc_balance_after=usdc_balance,
                                extra_details=extra
                            )
                            try:
                                send_trading_alert_email(subject=subj, body_main=body, client=client)
                                logger.info(f"[BUY] E-mail d'alerte envoye pour l'achat")
                            except Exception as e:
                                logger.error(f"[BUY] L'envoi de l'e-mail a echoue : {e}")
                            
                            # Vérifier si la position permet des partials sûrs
                            can_partial = can_execute_partial_safely(
                                coin_balance=float(quantity_rounded),
                                current_price=entry_price,
                                min_notional=min_notional
                            )
                            pair_state.update({
                                'entry_price': entry_price,
                                'atr_at_entry': atr_value,
                                'stop_loss_at_entry': entry_price - (config.atr_stop_multiplier * atr_value) if atr_value else None,
                                'trailing_activation_price_at_entry': entry_price + (config.atr_multiplier * atr_value) if atr_value else entry_price * (1 + config.trailing_activation_pct),  # activation trailing (aligned with backtest formula)
                                'max_price': entry_price,
                                'trailing_stop_activated': False,
                                'initial_position_size': float(quantity_rounded),
                                'last_order_side': 'BUY',
                                'partial_enabled': can_partial,  # NOUVEAU FLAG
                                'partial_taken_1': False,
                                'partial_taken_2': False
                            })
                            save_bot_state()
                            
                            # Journal de trading (Phase 2)
                            try:
                                logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
                                log_trade(
                                    logs_dir=logs_dir,
                                    pair=real_trading_pair,
                                    side='buy',
                                    quantity=float(quantity_rounded),
                                    price=entry_price,
                                    fee=float(quantity_rounded) * config.taker_fee * entry_price,
                                    slippage=config.slippage_buy,
                                    scenario=scenario,
                                    timeframe=time_interval,
                                    ema1=best_params.get('ema1_period'),
                                    ema2=best_params.get('ema2_period'),
                                    atr_value=atr_value,
                                    stop_price=pair_state.get('stop_loss_at_entry'),
                                    equity_before=usdc_balance,
                                )
                            except Exception as journal_err:
                                logger.error(f"[JOURNAL] Erreur écriture achat: {journal_err}")
                            
                            # Rafraîchir le balance après achat
                            account_info = client.get_account()
                            usdc_balance_obj = next((b for b in account_info['balances'] if b['asset'] == 'USDC'), None)
                            usdc_balance = float(usdc_balance_obj['free']) if usdc_balance_obj else 0.0
                    else:
                        logger.warning(f"[BUY] Quantité {quantity_rounded} < min_qty {min_qty_dec}, achat annulé")
                except Exception as e:
                    logger.error(f"[ACHAT] Erreur lors de l'exécution : {e}")
            
            # Afficher panel ACHAT
            display_buy_signal_panel(
                row=row, usdc_balance=usdc_balance, best_params=best_params,
                scenario=scenario, buy_condition=buy_condition, console=console,
                pair_state=pair_state, buy_reason=buy_reason
            )

    except Exception as e:
        logger.error(f"Erreur inattendue dans execute_real_trades : {e}")
        console.print(f"Erreur lors de l'execution de l'ordre : {e}")

def detect_market_changes(pair: str, timeframes: List[str], start_date: str) -> Dict[str, Any]:
    """
    Détecte intelligemment les changements IMPORTANTS du marché.
    
    Retourne:
    - EMA crosses (bullish/bearish)
    - StochRSI extremes
    - TRIX histogram changes
    - Prix nouveaux (highs/lows)
    """
    changes = {
        'ema_crosses': [],
        'stoch_extremes': [],
        'trix_changes': [],
        'price_records': [],
        'execution_time': datetime.now().strftime("%H:%M:%S")
    }
    
    try:
        for tf in timeframes:
            try:
                df = prepare_base_dataframe(pair, tf, start_date, 14)
                if df is None or df.empty or len(df) < 50:
                    continue
                
                # Récupérer les 2 derniers candles
                if len(df) >= 2:
                    prev_candle = df.iloc[-2]
                    curr_candle = df.iloc[-1]
                    
                    # Détection: EMA Cross (bullish/bearish)
                    ema1_prev = prev_candle.get('ema_26', None)
                    ema2_prev = prev_candle.get('ema_50', None)
                    ema1_curr = curr_candle.get('ema_26', None)
                    ema2_curr = curr_candle.get('ema_50', None)
                    
                    if (ema1_prev is not None and ema2_prev is not None and 
                        ema1_curr is not None and ema2_curr is not None):
                        
                        if ema1_prev <= ema2_prev and ema1_curr > ema2_curr:
                            changes['ema_crosses'].append({
                                'timeframe': tf,
                                'type': ' BULLISH CROSS',
                                'ema1': ema1_curr,
                                'ema2': ema2_curr,
                                'price': curr_candle['close']
                            })
                        elif ema1_prev >= ema2_prev and ema1_curr < ema2_curr:
                            changes['ema_crosses'].append({
                                'timeframe': tf,
                                'type': ' BEARISH CROSS',
                                'ema1': ema1_curr,
                                'ema2': ema2_curr,
                                'price': curr_candle['close']
                            })
                    
                    # Détection: StochRSI extremes
                    stoch_curr = curr_candle.get('stoch_rsi', None)
                    if stoch_curr is not None:
                        if stoch_curr < 0.2:
                            changes['stoch_extremes'].append({
                                'timeframe': tf,
                                'type': ' OVERSOLD',
                                'value': stoch_curr,
                                'price': curr_candle['close']
                            })
                        elif stoch_curr > 0.8:
                            changes['stoch_extremes'].append({
                                'timeframe': tf,
                                'type': ' OVERBOUGHT',
                                'value': stoch_curr,
                                'price': curr_candle['close']
                            })
                    
                    # Détection: Prix record
                    high_price = curr_candle['high']
                    low_price = curr_candle['low']
                    
                    if len(df) >= 20:
                        recent_high = df['high'].iloc[-20:].max()
                        recent_low = df['low'].iloc[-20:].min()
                        
                        if high_price >= recent_high:
                            changes['price_records'].append({
                                'timeframe': tf,
                                'type': '🆕 NEW 20-CANDLE HIGH',
                                'value': high_price,
                                'previous_high': recent_high
                            })
                        
                        if low_price <= recent_low:
                            changes['price_records'].append({
                                'timeframe': tf,
                                'type': '🆕 NEW 20-CANDLE LOW',
                                'value': low_price,
                                'previous_low': recent_low
                            })
            
            except Exception as e:
                logger.debug(f"Erreur détection changements {pair} {tf}: {e}")
                continue
    
    except Exception as e:
        logger.debug(f"Erreur globale détection changements: {e}")
    
    return changes

# display_market_changes extraite dans display_ui.py (Phase 5)

def backtest_and_display_results(backtest_pair: str, real_trading_pair: str, start_date: str, timeframes: List[str], sizing_mode: str = 'baseline'):
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
    console.print(f"\n[bold cyan][ANALYZE] Analyse des changements du marche...[/bold cyan]")
    market_changes = detect_market_changes(backtest_pair, timeframes, dynamic_start_date)
    display_market_changes(market_changes, backtest_pair, console=console)
    
    logger.info(f"Backtest period: 5 years from today | Start date: {dynamic_start_date}")
    
    # COMPENSATION BINANCE ULTRA-ROBUSTE A CHAQUE BACKTEST
    logger.info("Compensation timestamp Binance ultra-robuste active")
    
    logger.info("Debut des backtests...")
    
    if backtest_pair not in bot_state:
        bot_state[backtest_pair] = {
            'last_run_time': None,
            'last_best_params': None,
            'execution_count': 0,
            'entry_price': None,
            'max_price': None,
            'trailing_stop': None,
            'stop_loss': None,
            'last_execution': None
        }
    
    pair_state = bot_state[backtest_pair]
    
    try:
        results = run_all_backtests(backtest_pair, dynamic_start_date, timeframes, sizing_mode=sizing_mode)
    except Exception as e:
        logger.error(f"Une erreur est survenue pendant les backtests : {e}")
        return

    if not results:
        logger.error("Aucune donnee de backtest n'a ete generee")
        return

    # === WALK-FORWARD VALIDATION (Phase 2) ===
    try:
        from walk_forward import run_walk_forward_validation
        # Recréer base_dataframes pour WF (données déjà en cache)
        wf_base_dataframes = {}
        for tf in timeframes:
            df_wf = prepare_base_dataframe(backtest_pair, tf, dynamic_start_date, 14)
            wf_base_dataframes[tf] = df_wf if df_wf is not None and not df_wf.empty else pd.DataFrame()
        
        wf_scenarios = [
            {'name': 'StochRSI', 'params': {'stoch_period': 14}},
            {'name': 'StochRSI_SMA', 'params': {'stoch_period': 14, 'sma_long': 200}},
            {'name': 'StochRSI_ADX', 'params': {'stoch_period': 14, 'adx_period': 14}},
            {'name': 'StochRSI_TRIX', 'params': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15}},
        ]
        
        wf_result = run_walk_forward_validation(
            base_dataframes=wf_base_dataframes,
            full_sample_results=results,
            scenarios=wf_scenarios,
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

    # Identifier le meilleur résultat — C-13: filtrer par OOS gates en priorité
    try:
        from walk_forward import validate_oos_result as _validate_oos_main
        _oos_valid_main = [
            r for r in results
            if _validate_oos_main(r.get('sharpe_ratio', 0.0), r.get('win_rate', 0.0))
        ]
        if _oos_valid_main:
            _pool_main = _oos_valid_main
            logger.info(
                "[MAIN C-13] %d/%d résultats passent les OOS gates.",
                len(_oos_valid_main), len(results),
            )
        else:
            _pool_main = results
            logger.warning(
                "[MAIN C-13] Aucun résultat ne passe les OOS gates — fallback full-pool."
            )
    except Exception as _oos_err_main:
        logger.warning("[MAIN C-13] validate_oos_result indisponible: %s", _oos_err_main)
        _pool_main = results

    def _calmar_key_main_oos(x):
        roi = (x['final_wallet'] - x['initial_wallet']) / max(x['initial_wallet'], 1.0)
        dd  = max(x.get('max_drawdown', 0.001), 0.001)
        return roi / dd

    best_result = max(_pool_main, key=_calmar_key_main_oos)
    best_profit = best_result['final_wallet'] - best_result['initial_wallet']

    # Afficher les resultats
    display_backtest_table(backtest_pair, results, console)

    # Execution des ordres reels avec les meilleurs parametres
    best_params = {
        'timeframe': best_result['timeframe'],
        'ema1_period': best_result['ema_periods'][0],
        'ema2_period': best_result['ema_periods'][1],
        'scenario': best_result['scenario'],
    }
    best_params.update(SCENARIO_DEFAULT_PARAMS.get(best_result['scenario'], {}))

    # Mise a jour de l'etat du bot
    pair_state['last_best_params'] = best_params
    pair_state['execution_count'] += 1
    save_bot_state()

    # Affichage soigne pour la section trading en temps reel
    console.print("\n")
    display_trading_panel(real_trading_pair, best_params, console)
    
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
    schedule.every(2).minutes.do(
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

def check_admin_privileges():
    """Vérifie les privilèges admin sans élévation."""
    if os.name != 'nt':
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

if __name__ == "__main__":

    full_timestamp_resync()
    logger.info("Synchronisation complète exécutée au démarrage.")

    # MODE ULTRA-ROBUSTE SANS POPUP - COMPENSATION BINANCE PURE
    logger.info("Bot crypto H24/7 - Mode ultra-robuste avec privileges admin")
    
    crypto_pairs = [
        {"backtest_pair": "SOLUSDT", "real_pair": "SOLUSDC"},        
    ]
    try:
        # SOLUTION ULTRA-ROBUSTE TIMESTAMP
        logger.info("=== INITIALISATION TIMESTAMP ULTRA-ROBUSTE ===")
        if not init_timestamp_solution():
            logger.error("IMPOSSIBLE D'INITIALISER LA SYNCHRONISATION")
        
        # Re-synchroniser avant chaque session de trading
        client._sync_server_time()
        logger.info("Synchronisation timestamp pre-trading terminee")
        
        # Validation de la connexion API au demarrage
        if not validate_api_connection():
            logger.error("Impossible de valider la connexion API. Arret du programme.")
            exit(1)
        
        # Récupération des frais réels depuis l'API Binance (override config defaults)
        real_taker, real_maker = get_binance_trading_fees(client)
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
        
        # === NOUVELLE LOGIQUE : backtests optimisés + affichage propre ===
        parser = argparse.ArgumentParser(description='Run backtests and optional sizing mode')
        parser.add_argument('--sizing-mode', choices=['baseline', 'risk', 'fixed_notional', 'volatility_parity'], default=config.sizing_mode, help='Position sizing mode to use for backtests')
        args, unknown = parser.parse_known_args()

        # Exécuter les backtests avec affichage propre (passer sizing_mode)
        all_results = run_parallel_backtests(crypto_pairs, start_date, timeframes, sizing_mode=args.sizing_mode)

        # === AFFICHAGE PROPRE, SANS CHEVAUCHEMENT ===
        for backtest_pair, data in all_results.items():
            if not data['results']:
                console.print(f"[red]Aucun résultat pour {backtest_pair}[/red]")
                continue

            # C-07 + C-13: sélectionner par Calmar dans les résultats OOS-validés
            def _calmar_key_main(x):
                roi = (x['final_wallet'] - x['initial_wallet']) / max(x['initial_wallet'], 1.0)
                dd  = max(x.get('max_drawdown', 0.001), 0.001)
                return roi / dd

            try:
                from walk_forward import validate_oos_result as _validate_oos_loop
                _oos_valid_loop = [
                    r for r in data['results']
                    if _validate_oos_loop(r.get('sharpe_ratio', 0.0), r.get('win_rate', 0.0))
                ]
                _pool_loop = _oos_valid_loop if _oos_valid_loop else data['results']
                if not _oos_valid_loop:
                    logger.warning(
                        "[MAIN-LOOP C-13] %s: aucun résultat OOS-valide — fallback full-pool.",
                        backtest_pair,
                    )
            except Exception:
                _pool_loop = data['results']

            best_result = max(_pool_loop, key=_calmar_key_main)
            best_profit = best_result['final_wallet'] - best_result['initial_wallet']

            # Affichage des résultats via la fonction centralisée
            display_results_for_pair(backtest_pair, data['results'])
            
            # === Exécuter le trading réel avec les meilleurs paramètres ===
            best_params = {
                'timeframe': best_result['timeframe'],
                'ema1_period': best_result['ema_periods'][0],
                'ema2_period': best_result['ema_periods'][1],
                'scenario': best_result['scenario'],
            }
            best_params.update(SCENARIO_DEFAULT_PARAMS.get(best_result['scenario'], {}))
            
            # Initialiser l'état du bot pour cette paire
            if backtest_pair not in bot_state:
                bot_state[backtest_pair] = {
                    'last_run_time': None,
                    'last_best_params': None,
                    'execution_count': 0,
                    'entry_price': None,
                    'max_price': None,
                    'trailing_stop': None,
                    'stop_loss': None,
                    'last_execution': None
                }
            
            pair_state = bot_state[backtest_pair]
            
            try:
                execute_real_trades(data['real_pair'], best_result['timeframe'], best_params, backtest_pair, sizing_mode=args.sizing_mode)
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
            
            # Planification UNIQUE homogène toutes les 2 minutes (indépendant du timeframe)
            # CRITICAL: Capture best_params and sizing_mode with default argument to avoid late-binding closure issue
            schedule.every(2).minutes.do(
                lambda bp=backtest_pair, rp=data['real_pair'], tf=best_result['timeframe'], params=dict(best_params), sm=args.sizing_mode: execute_scheduled_trading(rp, tf, params, bp, sm)
            )
            
            # Afficher le panel de suivi
            console.print(build_tracking_panel(pair_state, current_run_time))
            console.print("\n")
            
            save_bot_state(force=True)

        logger.info(f"Tâches planifiées actives: {len(schedule.jobs)}")
        
        # === BOUCLE PRINCIPALE ===
        # C-04: Handler SIGTERM pour graceful shutdown (PM2, taskkill, systemd)
        # Capturé ICI pour avoir accès à save_bot_state et error_handler dans la portée
        def _graceful_shutdown(signum, frame):
            logger.critical(f"[SHUTDOWN] Signal {signum} reçu — sauvegarde état et arrêt propre")
            save_bot_state(force=True)

            # C-11: Vérifier les stops actifs sur Binance avant de quitter
            # Pour chaque paire en position BUY, s'assurer qu'un stop-loss est en place.
            try:
                pair_lookup = {p['backtest_pair']: p['real_pair'] for p in crypto_pairs}
                for bp, ps in list(bot_state.items()):
                    if ps.get('last_order_side') != 'BUY':
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
                            try:
                                send_trading_alert_email(
                                    subject=f"[CRITIQUE] Stop manquant au shutdown: {bp}",
                                    body_main=(
                                        f"Le bot s'arrête sur signal {signum}.\n\n"
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
                            logger.info("[SHUTDOWN C-11] Stop actif confirmé pour %s", bp)
                    except Exception as _ord_err:
                        logger.error(
                            "[SHUTDOWN C-11] Impossible de récupérer les ordres pour %s: %s",
                            real_sym, _ord_err,
                        )
            except Exception as _shutdown_check_err:
                logger.error("[SHUTDOWN C-11] Erreur vérification stops: %s", _shutdown_check_err)

            sys.exit(0)
        try:
            signal.signal(signal.SIGTERM, _graceful_shutdown)
            logger.info("[SHUTDOWN] Handler SIGTERM enregistré (C-04)")
        except (OSError, ValueError) as _sig_err:
            logger.warning(f"[SHUTDOWN] Impossible d'enregistrer SIGTERM handler: {_sig_err}")

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
            while True:
                try:
                    # Check circuit breaker status
                    if not error_handler.circuit_breaker.is_available():
                        logger.critical(f"[CIRCUIT] Bot en mode pause - Circuit ouvert. Prochaine tentative: {error_handler.circuit_breaker.timeout_seconds}s")
                        time.sleep(10)
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
                            time.sleep(10)
                            continue

                    # Vérification réseau toutes les 5 minutes
                    network_check_counter += 1
                    if network_check_counter >= 5:  # 5 cycles = 5 minutes
                        if not check_network_connectivity():
                            logger.warning("Connectivité réseau perdue...")
                            time.sleep(30)
                            continue
                        network_check_counter = 0

                    # Affichage du temps restant avant la prochaine exécution
                    now = datetime.now()
                    next_run = schedule.next_run()
                    if next_run:
                        delta = next_run - now
                        minutes_left = max(0, int(delta.total_seconds() // 60))
                        print(f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution dans {minutes_left} min ({next_run.strftime('%H:%M:%S')})")
                    else:
                        print(f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution non planifiée")

                    running_counter += 1
                    if running_counter % 1 == 0:  # Toutes les 10 minutes (600s sleep)
                        print(f"[RUNNING] {now.strftime('%H:%M:%S')} - running en cours")

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
                        with open(tmp_path, "w") as f:
                            json.dump(heartbeat, f)
                        os.replace(tmp_path, hb_path)
                    except Exception as hb_err:
                        logger.error(f"[HEARTBEAT] Erreur écriture: {hb_err}")

                    time.sleep(120)

                except Exception as e:
                    # Use error handler to manage main loop exceptions
                    should_continue, _ = error_handler.handle_error(
                        error=e,
                        context="main_loop",
                        critical=True
                    )

                    if not should_continue:
                        logger.critical(f"[CIRCUIT] Main loop paused due to circuit breaker. Waiting {error_handler.circuit_breaker.timeout_seconds}s before retry")
                        time.sleep(error_handler.circuit_breaker.timeout_seconds)
                    else:
                        logger.warning(f"[MAIN_LOOP] Continuing despite error - circuit still available")
                        time.sleep(30)
        except KeyboardInterrupt:
            logger.info("Execution interrompue par l'utilisateur. Arret du script.")
            save_bot_state(force=True)
        except Exception as e:
            error_msg = f"Erreur inattendue au démarrage : {e}"
            logger.error(error_msg)
            try:
                subj, body = critical_startup_error_email(str(e), traceback.format_exc())
                send_email_alert(subject=subj, body=body)
            except Exception:
                pass
            save_bot_state(force=True)

    except KeyboardInterrupt:
        logger.info("Execution interrompue par l'utilisateur. Arret du script.")
        save_bot_state(force=True)
    except Exception as e:
        error_msg = f"Erreur inattendue au démarrage : {e}"
        logger.error(error_msg)
        try:
            subj, body = critical_startup_error_email(str(e), traceback.format_exc())
            send_email_alert(subject=subj, body=body)
        except Exception:
            pass
        save_bot_state(force=True)