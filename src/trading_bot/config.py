"""
Module de configuration centralisée pour le bot de trading.
"""

import os
from typing import Optional


class Config:
    """Configuration centralisée du bot de trading."""

    # Champs obligatoires
    api_key: str
    secret_key: str
    sender_email: str
    receiver_email: str
    smtp_password: str

    # Champs optionnels (avec valeur par défaut)
    api_timeout: int = 30
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    maker_fee: float = 0.0004
    taker_fee: float = 0.0007
    use_limit_orders: bool = False
    limit_order_timeout: int = 60
    capital_usage_ratio: float = 0.995
    initial_wallet: float = 10000.0
    backtest_days: int = 1825
    max_workers: int = 4
    cache_dir: str = "cache"
    states_dir: str = "states"
    state_file: str = "bot_state.pkl"
    atr_period: int = 14
    atr_multiplier: float = 5.0
    atr_stop_multiplier: float = 3.0
    risk_per_trade: float = 0.01

    @classmethod
    def from_env(cls) -> "Config":
        """Charge la configuration depuis les variables d'environnement."""
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        required_vars = {
            "api_key": "BINANCE_API_KEY",
            "secret_key": "BINANCE_SECRET_KEY",
            "sender_email": "SENDER_EMAIL",
            "receiver_email": "RECEIVER_EMAIL",
            "smtp_password": "GOOGLE_MAIL_PASSWORD",
        }
        config_data = {}
        for key, env_var in required_vars.items():
            value = os.getenv(env_var)
            if not value:
                raise ValueError(f"Variable d'environnement manquante: {env_var}")
            config_data[key] = value
        # Optionnels
        config_data["maker_fee"] = float(os.getenv("MAKER_FEE", "0.0004"))
        config_data["taker_fee"] = float(os.getenv("TAKER_FEE", "0.0007"))
        config_data["use_limit_orders"] = os.getenv(
            "USE_LIMIT_ORDERS", "true"
        ).lower() in ["true", "1", "yes"]
        config_data["limit_order_timeout"] = int(os.getenv("LIMIT_ORDER_TIMEOUT", "60"))
        config_data["capital_usage_ratio"] = float(
            os.getenv("CAPITAL_USAGE_RATIO", "0.999")
        )
        config_data["api_timeout"] = int(os.getenv("API_TIMEOUT", "30"))
        config_data["max_workers"] = int(os.getenv("MAX_WORKERS", "4"))
        config_data["initial_wallet"] = float(os.getenv("INITIAL_WALLET", "10000.0"))
        config_data["backtest_days"] = int(os.getenv("BACKTEST_DAYS", "1825"))
        config_data["cache_dir"] = os.getenv("CACHE_DIR", "cache")
        config_data["states_dir"] = os.getenv("STATES_DIR", "states")
        config_data["state_file"] = os.getenv("STATE_FILE", "bot_state.pkl")
        config_data["atr_period"] = int(os.getenv("ATR_PERIOD", "14"))
        config_data["atr_multiplier"] = float(os.getenv("ATR_MULTIPLIER", "5.0"))
        config_data["atr_stop_multiplier"] = float(
            os.getenv("ATR_STOP_MULTIPLIER", "3.0")
        )
        config_data["risk_per_trade"] = float(os.getenv("RISK_PER_TRADE", "0.01"))
        config_data["smtp_server"] = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        config_data["smtp_port"] = int(os.getenv("SMTP_PORT", "587"))
        # Création de l'instance et assignation des attributs
        self = cls()
        for k, v in config_data.items():
            setattr(self, k, v)
        return self

    def __init__(self):
        pass
