"""
bot_config.py — Configuration centralisée du bot de trading.

Contient:
- Classe Config (dataclass-like) avec chargement depuis .env
- Utilitaires de configuration (extract_coin_from_pair)
- Décorateurs (log_exceptions, retry_with_backoff)
- Constantes et flags
"""
import os
import logging
import random
import time
import threading
from functools import wraps
from typing import Any, Callable, ParamSpec, Tuple, TypeVar, cast

P = ParamSpec('P')
T = TypeVar('T')

# Setup du logger commun
logger = logging.getLogger('trading_bot')

# Flag verbose
VERBOSE_LOGS = False

# Thread-local flag pour éviter les boucles récursives d'envoi d'email dans log_exceptions
_alert_sending = threading.local()

# Callback pour notification d'erreur (configuré par le module principal)
_error_notification_callback = None  # pylint: disable=invalid-name


def set_error_notification_callback(callback: Any) -> None:
    """Configure le callback appelé par log_exceptions en cas d'erreur."""
    global _error_notification_callback
    _error_notification_callback = callback


# ─── Classe Config ───────────────────────────────────────────────────────────

class Config:
    """Configuration centralisée du bot de trading."""
    # Champs obligatoires
    api_key: str
    secret_key: str
    sender_email: str
    receiver_email: str
    smtp_password: str

    # Champs optionnels avec valeurs par défaut
    api_timeout: int = 30
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    taker_fee: float = 0.0007
    maker_fee: float = 0.0002
    backtest_taker_fee: float = 0.0007  # P2-FEES: frais figés pour le backtest
                                         # (jamais écrasés par le live)
    backtest_maker_fee: float = 0.0002  # P2-FEES: frais figés pour le backtest
    slippage_buy: float = 0.0001
    slippage_sell: float = 0.0001
    initial_wallet: float = 10000.0
    backtest_days: int = 1095  # 3 ans glissants (1 cycle bear+bull complet)
    max_workers: int = 4
    cache_dir: str = "cache"
    states_dir: str = "states"
    state_file: str = "bot_state.json"  # C-17: JSON (was .pkl)
    atr_period: int = 14
    atr_multiplier: float = 8.0  # E-1: optimisé 5.5→8.0 (PnL +22.5%, DD -0.9pp)
    atr_stop_multiplier: float = 3.0
    recv_window: int = 60000  # P1-11: centralisé, utilisé par exchange_client
    risk_per_trade: float = 0.055  # B-2: optimisé 5%→5.5% (Calmar max 2.004)
    sizing_mode: str = 'risk'  # B-2: risk-based sizing (5.5% risk per trade)
    partial_threshold_1: float = 0.02
    partial_threshold_2: float = 0.04
    partial_pct_1: float = 0.50
    partial_pct_2: float = 0.30
    trailing_activation_pct: float = 0.03
    target_volatility_pct: float = 0.02
    backtest_min_notional: float = 5.0  # Filtre Binance simulé en backtest (USDC)
    oos_sharpe_min: float = 0.8      # P1-THRESH: seuil OOS Sharpe minimum
    oos_win_rate_min: float = 30.0   # P1-THRESH: seuil OOS Win Rate minimum (%)
    oos_decay_min: float = 0.15      # seuil ratio OOS/FS Sharpe (anti-overfit gate)
    schedule_interval_minutes: int = 2  # P2-02: intervalle schedule (avant: hardcodé)
    risk_free_rate: float = 0.04     # P2-03: taux sans risque annuel (US T-bills)
    email_cooldown_seconds: int = 300  # P2-07: cooldown entre emails d'alerte
    stoch_rsi_buy_max: float = 0.8    # P2-08: seuil haut StochRSI pour achat
    stoch_rsi_buy_min: float = 0.05   # P2-08: seuil bas StochRSI pour achat
    stoch_rsi_sell_exit: float = 0.4  # C-1: optimisé 0.2→0.4 (bench +2% PnL, -1pp DD)
    adx_threshold: float = 25.0      # P2-08: seuil ADX minimum
    volume_filter_enabled: bool = False  # A-1: filtre volume (désactivé: bench négatif)
    volume_sma_period: int = 20        # A-1: période SMA pour filtre volume
    breakeven_enabled: bool = True     # B-3: break-even stop
    breakeven_trigger_pct: float = 0.02   # B-3: seuil d'activation (2%, bench optimal)
    stop_loss_cooldown_candles: int = 12   # A-3: cooldown post-stop/breakeven (12h, bench optimal)
    mtf_filter_enabled: bool = True    # A-2: filtre multi-timeframe 4h (EMA fast > EMA slow sur 4h)
    mtf_ema_fast: int = 18             # A-2: période EMA rapide sur 4h
    mtf_ema_slow: int = 58             # A-2: période EMA lente sur 4h
    max_parallel_pairs: int = 5      # P2-09: cap parallélisation run_parallel_backtests
    backtest_throttle_seconds: float = 3600.0  # P3-02: intervalle minimum entre deux backtests (s)
    project_name: str = "MULTI_ASSETS"  # Préfixe de tous les sujets d'alertes mail
    # P5-A: bloquer les achats si perte journalière > 5 % du capital initial
    daily_loss_limit_pct: float = 0.05
    # EM-P2-05: seuil de drawdown non réalisé par position (alerte CRITIQUE, pas de vente auto)
    max_drawdown_pct: float = 0.15
    fee_reference_symbol: str = 'TRXUSDC'  # MI-02: paire de référence pour les frais
    position_size_cushion: float = 0.98     # MI-04: coussin capital position (98% du solde)
    reconcile_min_qty: float = 0.01         # MI-05: quantité minimale pour réconciliation
    reconcile_min_notional: float = 5.0     # MI-05: valeur notionnelle minimale pour réconciliation
    # ST-P1-01: nombre max de positions longues simultanées (guard anti-corrélation)
    max_concurrent_long: int = 4
    # TS-P2-01: circuit breaker — quarantaine API Binance après N échecs réseau consécutifs
    circuit_breaker_threshold: int = 10
    circuit_breaker_reset_seconds: int = 60
    # C-01: mode d'exécution — 'DEMO' (dry-run, aucun ordre réel) ou 'LIVE'
    bot_mode: str = 'DEMO'

    def __init__(self) -> None:
        pass

    def __repr__(self) -> str:
        """Représentation sûre — masque les credentials sensibles (C-10)."""
        return (
            f"Config(api_key=***MASKED***, secret_key=***MASKED***, "
            f"sender_email={getattr(self, 'sender_email', '?')!r}, "
            f"taker_fee={getattr(self, 'taker_fee', '?')}, "
            f"sizing_mode={getattr(self, 'sizing_mode', '?')!r}, "
            f"initial_wallet={getattr(self, 'initial_wallet', '?')})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    def __setattr__(self, name: str, value: object) -> None:
        """Empêche toute mutation de Config après initialisation (P0-01)."""
        if name == '_frozen':
            object.__setattr__(self, name, value)
            return
        if getattr(self, '_frozen', False):
            raise AttributeError(
                f"Config is frozen — cannot set '{name}' after initialization. "
                "Store runtime state in module-level variables instead."
            )
        object.__setattr__(self, name, value)

    @classmethod
    def from_env(cls) -> 'Config':
        """Charge la configuration depuis les variables d'environnement."""
        try:
            from dotenv import load_dotenv  # pylint: disable=import-outside-toplevel
            load_dotenv()
        except ImportError:
            pass

        required_vars = {
            'api_key': 'BINANCE_API_KEY',
            'secret_key': 'BINANCE_SECRET_KEY',
            'sender_email': 'SENDER_EMAIL',
            'receiver_email': 'RECEIVER_EMAIL',
            'smtp_password': 'GOOGLE_MAIL_PASSWORD',
        }
        config_data: dict[str, Any] = {}
        for key, env_var in required_vars.items():
            value = os.getenv(env_var)
            if not value:
                raise ValueError(f"Variable d'environnement manquante: {env_var}")
            config_data[key] = value

        # Optionnels
        config_data['taker_fee'] = float(os.getenv('TAKER_FEE', '0.0007'))
        config_data['maker_fee'] = float(os.getenv('MAKER_FEE', '0.0002'))
        config_data['backtest_taker_fee'] = float(
            os.getenv('BACKTEST_TAKER_FEE', '0.0007'))  # P2-FEES
        config_data['backtest_maker_fee'] = float(
            os.getenv('BACKTEST_MAKER_FEE', '0.0002'))  # P2-FEES
        config_data['slippage_buy'] = float(os.getenv('SLIPPAGE_BUY', '0.0001'))
        config_data['slippage_sell'] = float(os.getenv('SLIPPAGE_SELL', '0.0001'))
        config_data['api_timeout'] = int(os.getenv('API_TIMEOUT', '30'))
        config_data['max_workers'] = int(os.getenv('MAX_WORKERS', '4'))
        config_data['initial_wallet'] = float(os.getenv('INITIAL_WALLET', '10000.0'))
        config_data['backtest_days'] = int(
            os.getenv('BACKTEST_DAYS', '1095'))  # 3 ans glissants (1 cycle bear+bull)
        # Chemins ancrés au répertoire du script (indépendant du cwd)
        # → résout le bug de cache différent selon le lanceur (bat vs terminal)
        _src_dir = os.path.dirname(os.path.abspath(__file__))
        _cache_env = os.getenv('CACHE_DIR', 'cache')
        config_data['cache_dir'] = (
            _cache_env if os.path.isabs(_cache_env)
            else os.path.join(_src_dir, _cache_env))
        _states_env = os.getenv('STATES_DIR', 'states')
        config_data['states_dir'] = (
            _states_env if os.path.isabs(_states_env)
            else os.path.join(_src_dir, _states_env))
        config_data['state_file'] = os.getenv('STATE_FILE', 'bot_state.json')  # C-17
        config_data['atr_period'] = int(os.getenv('ATR_PERIOD', '14'))
        config_data['atr_multiplier'] = float(os.getenv('ATR_MULTIPLIER', '8.0'))
        config_data['atr_stop_multiplier'] = float(os.getenv('ATR_STOP_MULTIPLIER', '3.0'))
        config_data['risk_per_trade'] = float(os.getenv('RISK_PER_TRADE', '0.055'))  # B-2
        config_data['smtp_server'] = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        config_data['smtp_port'] = int(os.getenv('SMTP_PORT', '587'))
        config_data['sizing_mode'] = os.getenv('SIZING_MODE', 'risk')  # B-2
        config_data['recv_window'] = int(os.getenv('RECV_WINDOW', '60000'))  # P1-11
        config_data['partial_threshold_1'] = float(os.getenv('PARTIAL_THRESHOLD_1', '0.02'))
        config_data['partial_threshold_2'] = float(os.getenv('PARTIAL_THRESHOLD_2', '0.04'))
        config_data['partial_pct_1'] = float(os.getenv('PARTIAL_PCT_1', '0.50'))
        config_data['partial_pct_2'] = float(os.getenv('PARTIAL_PCT_2', '0.30'))
        config_data['trailing_activation_pct'] = float(os.getenv('TRAILING_ACTIVATION_PCT', '0.03'))
        config_data['target_volatility_pct'] = float(os.getenv('TARGET_VOLATILITY_PCT', '0.02'))
        config_data['backtest_min_notional'] = float(os.getenv('BACKTEST_MIN_NOTIONAL', '5.0'))
        config_data['oos_sharpe_min'] = float(
            os.getenv('OOS_SHARPE_MIN', '0.8'))       # P1-THRESH
        config_data['oos_win_rate_min'] = float(
            os.getenv('OOS_WIN_RATE_MIN', '30.0'))  # P1-THRESH
        config_data['oos_decay_min'] = float(
            os.getenv('OOS_DECAY_MIN', '0.15'))        # anti-overfit
        config_data['schedule_interval_minutes'] = int(
            os.getenv('SCHEDULE_INTERVAL_MINUTES', '2'))  # P2-02
        config_data['risk_free_rate'] = float(
            os.getenv('RISK_FREE_RATE', '0.04'))  # P2-03
        config_data['email_cooldown_seconds'] = int(
            os.getenv('EMAIL_COOLDOWN_SECONDS', '300'))  # P2-07
        config_data['stoch_rsi_buy_max'] = float(
            os.getenv('STOCH_RSI_BUY_MAX', '0.8'))  # P2-08
        config_data['stoch_rsi_buy_min'] = float(
            os.getenv('STOCH_RSI_BUY_MIN', '0.05'))  # P2-08
        config_data['stoch_rsi_sell_exit'] = float(
            os.getenv('STOCH_RSI_SELL_EXIT', '0.4'))  # C-1
        config_data['adx_threshold'] = float(
            os.getenv('ADX_THRESHOLD', '25.0'))  # P2-08
        config_data['volume_filter_enabled'] = (
            os.getenv('VOLUME_FILTER_ENABLED', 'false').lower()
            in ('true', '1', 'yes'))  # A-1
        config_data['volume_sma_period'] = int(
            os.getenv('VOLUME_SMA_PERIOD', '20'))  # A-1
        config_data['breakeven_enabled'] = (
            os.getenv('BREAKEVEN_ENABLED', 'true').lower()
            in ('true', '1', 'yes'))  # B-3
        config_data['breakeven_trigger_pct'] = float(
            os.getenv('BREAKEVEN_TRIGGER_PCT', '0.02'))  # B-3
        config_data['stop_loss_cooldown_candles'] = int(
            os.getenv('STOP_LOSS_COOLDOWN_CANDLES', '12'))  # A-3
        config_data['mtf_filter_enabled'] = (
            os.getenv('MTF_FILTER_ENABLED', 'true').lower()
            in ('true', '1', 'yes'))  # A-2
        config_data['mtf_ema_fast'] = int(os.getenv('MTF_EMA_FAST', '18'))  # A-2
        config_data['mtf_ema_slow'] = int(os.getenv('MTF_EMA_SLOW', '58'))  # A-2
        config_data['max_parallel_pairs'] = int(
            os.getenv('MAX_PARALLEL_PAIRS', '5'))  # P2-09
        config_data['backtest_throttle_seconds'] = float(
            os.getenv('BACKTEST_THROTTLE_SECONDS', '3600.0'))  # P3-02
        config_data['project_name'] = os.getenv('BOT_PROJECT_NAME', 'MULTI_ASSETS')
        config_data['daily_loss_limit_pct'] = float(
            os.getenv('DAILY_LOSS_LIMIT_PCT', '0.05'))  # P5-A
        config_data['max_drawdown_pct'] = float(
            os.getenv('MAX_DRAWDOWN_PCT', '0.15'))  # EM-P2-05
        config_data['max_concurrent_long'] = int(
            os.getenv('MAX_CONCURRENT_LONG', '4'))  # ST-P1-01
        config_data['circuit_breaker_threshold'] = int(
            os.getenv('CIRCUIT_BREAKER_THRESHOLD', '10'))  # TS-P2-01
        config_data['circuit_breaker_reset_seconds'] = int(
            os.getenv('CIRCUIT_BREAKER_RESET_SECONDS', '60'))  # TS-P2-01
        config_data['fee_reference_symbol'] = os.getenv('FEE_REFERENCE_SYMBOL', 'TRXUSDC')  # MI-02
        config_data['position_size_cushion'] = float(
            os.getenv('POSITION_SIZE_CUSHION', '0.98'))  # MI-04
        config_data['reconcile_min_qty'] = float(
            os.getenv('RECONCILE_MIN_QTY', '0.01'))  # MI-05
        config_data['reconcile_min_notional'] = float(
            os.getenv('RECONCILE_MIN_NOTIONAL', '5.0'))  # MI-05
        config_data['bot_mode'] = os.getenv('BOT_MODE', 'DEMO')  # C-01

        self = cls()
        for k, v in config_data.items():
            setattr(self, k, v)

        # Validation sémantique
        self._validate()
        return self

    def _validate(self) -> None:
        """Valide la cohérence des valeurs de configuration."""
        errors = []
        # Frais et slippage doivent être positifs et raisonnables (< 10%)
        for name in ('taker_fee', 'maker_fee', 'backtest_taker_fee',
                     'backtest_maker_fee', 'slippage_buy', 'slippage_sell'):
            val = getattr(self, name)
            if not 0 <= val < 0.10:
                errors.append(f"{name}={val} hors limites [0, 0.10)")
        # Risk per trade entre 0.1% et 50%
        if not 0.001 <= self.risk_per_trade <= 0.50:
            errors.append(f"risk_per_trade={self.risk_per_trade} hors limites [0.001, 0.50]")
        # Sizing mode valide
        valid_modes = {'baseline', 'risk'}
        if self.sizing_mode not in valid_modes:
            errors.append(
                f"sizing_mode='{self.sizing_mode}' invalide. Valides: {valid_modes}")
        # Partial thresholds cohérents
        if self.partial_threshold_2 <= self.partial_threshold_1:
            errors.append(
                f"partial_threshold_2 ({self.partial_threshold_2}) doit être > "
                f"partial_threshold_1 ({self.partial_threshold_1})"
            )
        # Partial percentages entre 0 et 1
        for name in ('partial_pct_1', 'partial_pct_2'):
            val = getattr(self, name)
            if not 0 < val <= 1.0:
                errors.append(f"{name}={val} hors limites (0, 1.0]")
        # Wallet et backtest_days positifs
        if self.initial_wallet <= 0:
            errors.append(f"initial_wallet={self.initial_wallet} doit être > 0")
        if self.backtest_days <= 0:
            errors.append(f"backtest_days={self.backtest_days} doit être > 0")
        # ATR periods and multipliers
        if self.atr_period < 1:
            errors.append(f"atr_period={self.atr_period} doit être >= 1")
        if self.atr_multiplier <= 0:
            errors.append(f"atr_multiplier={self.atr_multiplier} doit être > 0")
        if self.atr_stop_multiplier <= 0:
            errors.append(f"atr_stop_multiplier={self.atr_stop_multiplier} doit être > 0")
        # API timeout
        if self.api_timeout < 1:
            errors.append(f"api_timeout={self.api_timeout} doit être >= 1")

        # C-15: Warn when config values diverge from Cython compile-time constants.
        # backtest_engine_standard.pyx uses:
        #   DEF ATR_MULTIPLIER      = 8.0   (trailing activation) — E-1
        #   DEF ATR_STOP_MULTIPLIER = 3.0   (initial stop)
        # If config differs, live trading behaviour won't match backtest outcomes.
        cython_atr_multiplier = 8.0
        cython_atr_stop_multiplier = 3.0
        if abs(self.atr_multiplier - cython_atr_multiplier) > 1e-9:
            logger.warning(
                "[CONFIG C-15] atr_multiplier=%.4f diffère de la constante Cython "
                "ATR_MULTIPLIER=%.4f — live et backtest ne seront pas alignés.",
                self.atr_multiplier, cython_atr_multiplier,
            )
        if abs(self.atr_stop_multiplier - cython_atr_stop_multiplier) > 1e-9:
            logger.warning(
                "[CONFIG C-15] atr_stop_multiplier=%.4f diffère de la constante Cython "
                "ATR_STOP_MULTIPLIER=%.4f — live et backtest ne seront pas alignés.",
                self.atr_stop_multiplier, cython_atr_stop_multiplier,
            )

        if errors:
            msg = "Erreur(s) de configuration:\n  - " + "\n  - ".join(errors)
            raise ValueError(msg)

        # P0-01: gel de la config après validation — toute mutation ultérieure lève AttributeError
        object.__setattr__(self, '_frozen', True)


# Singleton de configuration — créé à l'import
config = Config.from_env()


# ─── Décorateurs ─────────────────────────────────────────────────────────────

def log_exceptions(default_return: object = None) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Décorateur qui log les exceptions et retourne une valeur par défaut.

    Si un callback d'erreur est configuré (via set_error_notification_callback),
    il sera appelé pour envoyer une alerte. Protégé contre les boucles récursives.
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"[EXCEPTION] {func.__name__}: {e}", exc_info=True)
                if _error_notification_callback and not getattr(
                        _alert_sending, 'active', False):
                    _alert_sending.active = True
                    try:
                        _error_notification_callback(func.__name__, e, args, kwargs)
                    except Exception as cb_exc:
                        logger.error(f"[EXCEPTION] Echec callback d'alerte: {cb_exc}")
                    finally:
                        _alert_sending.active = False
                return cast(T, default_return)  # caller guarantees default_return ∈ T
        return wrapper
    return decorator


def retry_with_backoff(
        max_retries: int = 3,
        base_delay: float = 1.0,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Décorateur pour retry avec backoff exponentiel + jitter (P1-01).

    Le jitter évite le thundering-herd quand plusieurs threads retrytent simultanément
    après une erreur API commune.
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise e
                    # Backoff exponentiel avec jitter uniforme [0, base_delay]
                    delay = base_delay * (2 ** attempt) + random.uniform(0.0, base_delay)
                    delay = min(delay, 60.0)  # Cap à 60s
                    logger.warning(
                        f"Tentative {attempt + 1} echouee pour {func.__name__}: {e}. "
                        f"Retry dans {delay:.2f}s"
                    )
                    time.sleep(delay)
            # unreachable: the last iteration (attempt == max_retries-1) always re-raises
            raise AssertionError(
                "retry_with_backoff: all retries exhausted — "
                "last iteration always re-raises the caught exception"
            )
        return wrapper
    return decorator


# ─── Utilitaires de configuration ────────────────────────────────────────────

def extract_coin_from_pair(real_trading_pair: str) -> Tuple[str, str]:
    """Extrait le symbole du coin et la devise de cotation."""
    quote_currencies = ["USDC", "USDT", "BUSD", "EUR"]
    for quote in quote_currencies:
        if real_trading_pair.endswith(quote):
            coin_symbol = real_trading_pair[: -len(quote)]
            return coin_symbol, quote
    raise ValueError(
        f"Impossible de determiner le coin ou la monnaie de cotation pour {real_trading_pair}."
    )
