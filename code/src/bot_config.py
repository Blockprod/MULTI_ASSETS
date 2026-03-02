"""
bot_config.py — Configuration centralisée du bot de trading.

Contient:
- Classe Config (dataclass-like) avec chargement depuis .env
- Utilitaires de configuration (extract_coin_from_pair)
- Décorateurs (log_exceptions, retry_with_backoff)
- Constantes et flags
"""
import os
import sys
import logging
import time
import threading
from typing import Tuple

# Setup du logger commun
logger = logging.getLogger('trading_bot')

# Flag verbose
VERBOSE_LOGS = False

# Thread-local flag pour éviter les boucles récursives d'envoi d'email dans log_exceptions
_alert_sending = threading.local()

# Callback pour notification d'erreur (configuré par le module principal)
_error_notification_callback = None


def set_error_notification_callback(callback):
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
    slippage_buy: float = 0.0001
    slippage_sell: float = 0.0001
    initial_wallet: float = 10000.0
    backtest_days: int = 1095  # 3 ans glissants (1 cycle bear+bull complet)
    max_workers: int = 4
    cache_dir: str = "cache"
    states_dir: str = "states"
    state_file: str = "bot_state.pkl"
    atr_period: int = 14
    atr_multiplier: float = 5.5
    atr_stop_multiplier: float = 3.0
    risk_per_trade: float = 0.05
    sizing_mode: str = 'baseline'
    partial_threshold_1: float = 0.02
    partial_threshold_2: float = 0.04
    partial_pct_1: float = 0.50
    partial_pct_2: float = 0.30
    trailing_activation_pct: float = 0.03
    target_volatility_pct: float = 0.02

    def __init__(self):
        pass

    @classmethod
    def from_env(cls) -> 'Config':
        """Charge la configuration depuis les variables d'environnement."""
        try:
            from dotenv import load_dotenv
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
        config_data = {}
        for key, env_var in required_vars.items():
            value = os.getenv(env_var)
            if not value:
                raise ValueError(f"Variable d'environnement manquante: {env_var}")
            config_data[key] = value

        # Optionnels
        config_data['taker_fee'] = float(os.getenv('TAKER_FEE', '0.0007'))
        config_data['maker_fee'] = float(os.getenv('MAKER_FEE', '0.0002'))
        config_data['slippage_buy'] = float(os.getenv('SLIPPAGE_BUY', '0.0001'))
        config_data['slippage_sell'] = float(os.getenv('SLIPPAGE_SELL', '0.0001'))
        config_data['api_timeout'] = int(os.getenv('API_TIMEOUT', '30'))
        config_data['max_workers'] = int(os.getenv('MAX_WORKERS', '4'))
        config_data['initial_wallet'] = float(os.getenv('INITIAL_WALLET', '10000.0'))
        config_data['backtest_days'] = int(os.getenv('BACKTEST_DAYS', '1095'))  # 3 ans glissants (1 cycle bear+bull complet)
        # Chemins ancrés au répertoire du script (indépendant du cwd)
        # → résout le bug de cache différent selon le lanceur (bat vs terminal)
        _src_dir = os.path.dirname(os.path.abspath(__file__))
        _cache_env = os.getenv('CACHE_DIR', 'cache')
        config_data['cache_dir'] = _cache_env if os.path.isabs(_cache_env) else os.path.join(_src_dir, _cache_env)
        _states_env = os.getenv('STATES_DIR', 'states')
        config_data['states_dir'] = _states_env if os.path.isabs(_states_env) else os.path.join(_src_dir, _states_env)
        config_data['state_file'] = os.getenv('STATE_FILE', 'bot_state.pkl')
        config_data['atr_period'] = int(os.getenv('ATR_PERIOD', '14'))
        config_data['atr_multiplier'] = float(os.getenv('ATR_MULTIPLIER', '5.5'))
        config_data['atr_stop_multiplier'] = float(os.getenv('ATR_STOP_MULTIPLIER', '3.0'))
        config_data['risk_per_trade'] = float(os.getenv('RISK_PER_TRADE', '0.05'))
        config_data['smtp_server'] = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        config_data['smtp_port'] = int(os.getenv('SMTP_PORT', '587'))
        config_data['sizing_mode'] = os.getenv('SIZING_MODE', 'baseline')
        config_data['partial_threshold_1'] = float(os.getenv('PARTIAL_THRESHOLD_1', '0.02'))
        config_data['partial_threshold_2'] = float(os.getenv('PARTIAL_THRESHOLD_2', '0.04'))
        config_data['partial_pct_1'] = float(os.getenv('PARTIAL_PCT_1', '0.50'))
        config_data['partial_pct_2'] = float(os.getenv('PARTIAL_PCT_2', '0.30'))
        config_data['trailing_activation_pct'] = float(os.getenv('TRAILING_ACTIVATION_PCT', '0.03'))
        config_data['target_volatility_pct'] = float(os.getenv('TARGET_VOLATILITY_PCT', '0.02'))

        self = cls()
        for k, v in config_data.items():
            setattr(self, k, v)

        # Validation sémantique
        self._validate()
        return self

    def _validate(self):
        """Valide la cohérence des valeurs de configuration."""
        errors = []
        # Frais et slippage doivent être positifs et raisonnables (< 10%)
        for name in ('taker_fee', 'maker_fee', 'slippage_buy', 'slippage_sell'):
            val = getattr(self, name)
            if not (0 <= val < 0.10):
                errors.append(f"{name}={val} hors limites [0, 0.10)")
        # Risk per trade entre 0.1% et 50%
        if not (0.001 <= self.risk_per_trade <= 0.50):
            errors.append(f"risk_per_trade={self.risk_per_trade} hors limites [0.001, 0.50]")
        # Sizing mode valide
        valid_modes = {'baseline', 'risk', 'fixed_notional', 'volatility_parity'}
        if self.sizing_mode not in valid_modes:
            errors.append(f"sizing_mode='{self.sizing_mode}' invalide. Valides: {valid_modes}")
        # Partial thresholds cohérents
        if self.partial_threshold_2 <= self.partial_threshold_1:
            errors.append(
                f"partial_threshold_2 ({self.partial_threshold_2}) doit être > "
                f"partial_threshold_1 ({self.partial_threshold_1})"
            )
        # Partial percentages entre 0 et 1
        for name in ('partial_pct_1', 'partial_pct_2'):
            val = getattr(self, name)
            if not (0 < val <= 1.0):
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

        if errors:
            msg = "Erreur(s) de configuration:\n  - " + "\n  - ".join(errors)
            raise ValueError(msg)


# Singleton de configuration — créé à l'import
config = Config.from_env()


# ─── Décorateurs ─────────────────────────────────────────────────────────────

def log_exceptions(default_return=None):
    """Décorateur qui log les exceptions et retourne une valeur par défaut.

    Si un callback d'erreur est configuré (via set_error_notification_callback),
    il sera appelé pour envoyer une alerte. Protégé contre les boucles récursives.
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"[EXCEPTION] {func.__name__}: {e}", exc_info=True)
                if _error_notification_callback and not getattr(_alert_sending, 'active', False):
                    _alert_sending.active = True
                    try:
                        _error_notification_callback(func.__name__, e, args, kwargs)
                    except Exception as cb_exc:
                        logger.error(f"[EXCEPTION] Echec callback d'alerte: {cb_exc}")
                    finally:
                        _alert_sending.active = False
                return default_return if default_return is not None else None
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


def retry_with_backoff(max_retries: int = 3, base_delay: float = 1.0):
    """Décorateur pour retry avec backoff exponentiel."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise e
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Tentative {attempt + 1} echouee pour {func.__name__}: {e}. "
                        f"Retry dans {delay}s"
                    )
                    time.sleep(delay)
            return None
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
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
