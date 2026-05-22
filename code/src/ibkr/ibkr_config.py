"""
ibkr_config.py — Configuration singleton pour le runtime IBKR Forex.

Isolation totale de bot_config.py (Binance). Chargement exclusif
depuis les variables d'environnement. Aucune modification des fichiers
Binance existants.
"""
from __future__ import annotations

import os
import logging

logger = logging.getLogger("ibkr_forex")


class IBKRConfig:
    """Singleton de configuration IBKR Forex.

    Chargé via IBKRConfig.from_env().
    __repr__ masque IBKR_ACCOUNT et IBKR_SECRET (données sensibles).
    """

    _instance: "IBKRConfig | None" = None

    def __init__(self) -> None:
        # ─── Connexion IB Gateway ─────────────────────────────────────────
        self.host: str = "127.0.0.1"
        self.port: int = 4002              # 4002=paper, 4001=live
        self.client_id: int = 4            # Distinct: EDGECORE=1, AlphaEdge=2 ou 3, IBKR_FOREX=4
        self.account: str = ""             # DU1234567 (paper) / U1234567 (live)
        self.paper_mode: bool = True

        # ─── Clé HMAC state file ─────────────────────────────────────────
        self.ibkr_secret: str = ""         # Clé dédiée — JAMAIS BINANCE_SECRET_KEY

        # ─── Fees Forex ──────────────────────────────────────────────────
        self.taker_fee: float = 0.00003    # ~$2 par 100k notionnel
        self.maker_fee: float = 0.00001
        # Figés pour le backtest — ne jamais écraser au runtime
        self.backtest_taker_fee: float = 0.00003
        self.backtest_maker_fee: float = 0.00001

        # ─── Capital et risque ───────────────────────────────────────────
        self.initial_capital: float = 10_000.0   # 10 000€ paper
        self.risk_per_trade: float = 0.055        # 5.5% = ~550€/trade
        self.max_leverage: float = 20.0           # IBKR Forex retail (20:1 EUR/USD)
        self.daily_loss_limit_pct: float = 0.05   # 5% = 500€/j

        # ─── Cycle ───────────────────────────────────────────────────────
        self.schedule_interval_minutes: int = 60  # Cycle Forex 1h

        # ─── Email (réutilisé depuis l'env existant) ─────────────────────
        self.sender_email: str = ""
        self.receiver_email: str = ""
        self.smtp_password: str = ""

        # ─── Chemins (isolés de Binance) ─────────────────────────────────
        _base = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        self.states_dir: str = os.path.join(
            os.path.dirname(__file__), "states"
        )
        self.state_file: str = "ibkr_forex_state.json"
        self.cache_dir: str = os.path.join(_base, "cache", "ibkr")

        # ─── Dashboard ───────────────────────────────────────────────────
        self.dashboard_port: int = 8083

    @classmethod
    def from_env(cls) -> "IBKRConfig":
        """Charge la config depuis les variables d'environnement.

        Lève EnvironmentError si IBKR_SECRET est absent.
        Les scripts d'entrée (IBKR_FOREX.py, ibkr_backtest.py) appellent
        load_dotenv() avant tout import — ce singleton ne charge pas .env.
        """
        if cls._instance is not None:
            return cls._instance

        cfg = cls()

        # Connexion
        cfg.host = os.environ.get("IBKR_HOST", "127.0.0.1")
        cfg.port = int(os.environ.get("IBKR_PORT", "4002"))
        cfg.client_id = int(os.environ.get("IBKR_CLIENT_ID", "4"))
        cfg.paper_mode = os.environ.get("IBKR_PAPER_MODE", "true").lower() == "true"

        account = os.environ.get("IBKR_ACCOUNT", "")
        # Optionnel — si vide, IBKRForexClient.connect() le détecte via managedAccounts()
        cfg.account = account

        ibkr_secret = os.environ.get("IBKR_SECRET", "")
        if not ibkr_secret:
            raise EnvironmentError(
                "[IBKR-P0] Variable d'environnement IBKR_SECRET manquante. "
                "Clé HMAC requise pour la signature du fichier d'état."
            )
        cfg.ibkr_secret = ibkr_secret

        # Fees
        cfg.taker_fee = float(os.environ.get("IBKR_TAKER_FEE", "0.00003"))
        cfg.maker_fee = float(os.environ.get("IBKR_MAKER_FEE", "0.00001"))
        cfg.backtest_taker_fee = float(os.environ.get("IBKR_TAKER_FEE", "0.00003"))
        cfg.backtest_maker_fee = float(os.environ.get("IBKR_MAKER_FEE", "0.00001"))

        # Capital
        cfg.initial_capital = float(os.environ.get("IBKR_INITIAL_CAPITAL", "10000.0"))
        cfg.risk_per_trade = float(os.environ.get("IBKR_RISK_PER_TRADE", "0.02"))
        cfg.max_leverage = float(os.environ.get("IBKR_MAX_LEVERAGE", "5.0"))
        cfg.daily_loss_limit_pct = float(
            os.environ.get("IBKR_DAILY_LOSS_LIMIT_PCT", "0.05")
        )

        # Cycle
        cfg.schedule_interval_minutes = int(
            os.environ.get("IBKR_SCHEDULE_INTERVAL_MIN", "60")
        )

        # Email (variables partagées avec le bot Binance)
        cfg.sender_email = os.environ.get("SENDER_EMAIL", "")
        cfg.receiver_email = os.environ.get("RECEIVER_EMAIL", "")
        cfg.smtp_password = os.environ.get("GOOGLE_MAIL_PASSWORD", "")

        # Chemins optionnellement surchargés
        states_dir_env = os.environ.get("IBKR_STATES_DIR", "")
        if states_dir_env:
            cfg.states_dir = states_dir_env
        state_file_env = os.environ.get("IBKR_STATE_FILE", "")
        if state_file_env:
            cfg.state_file = state_file_env
        cache_dir_env = os.environ.get("IBKR_CACHE_DIR", "")
        if cache_dir_env:
            cfg.cache_dir = cache_dir_env

        # Dashboard
        cfg.dashboard_port = int(os.environ.get("IBKR_DASHBOARD_PORT", "8083"))

        os.makedirs(cfg.states_dir, exist_ok=True)
        os.makedirs(cfg.cache_dir, exist_ok=True)

        cls._instance = cfg
        logger.info("[IBKR-CONFIG] Configuration chargée (paper=%s, port=%d, clientId=%d)",
                    cfg.paper_mode, cfg.port, cfg.client_id)
        return cfg

    def __repr__(self) -> str:
        account_masked = f"{self.account[:2]}***{self.account[-2:]}" if len(self.account) > 4 else "***"
        return (
            f"IBKRConfig(host={self.host!r}, port={self.port}, "
            f"client_id={self.client_id}, account={account_masked!r}, "
            f"paper_mode={self.paper_mode}, "
            f"risk_per_trade={self.risk_per_trade}, "
            f"initial_capital={self.initial_capital})"
        )
