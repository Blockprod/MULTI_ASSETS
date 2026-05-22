"""ibkr_forex_dashboard_server.py — Dashboard IBKR Forex (port 8083).

Wrapper léger sur dashboard_server.py : surcharge uniquement les chemins
et le port. Aucune logique dupliquée.

Paires : EUR/USD, EUR/GBP — Paper Trading
Endpoint : http://127.0.0.1:8083/dashboard
"""
from __future__ import annotations

import os
import sys
import threading

# ─── Chemin scripts → import dashboard_server ─────────────────────────────
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# ─── Placeholder Binance (dashboard_server.py ne se lance pas sans ces vars) ─
os.environ.setdefault("BINANCE_API_KEY", "IBKR_NOT_USED")
os.environ.setdefault("BINANCE_SECRET_KEY", "IBKR_NOT_USED")

import dashboard_server as _ds  # noqa: E402

# ─── Surcharge des constantes IBKR ────────────────────────────────────────
_BASE_DIR = os.path.abspath(os.path.join(_SCRIPTS_DIR, "..", ".."))
_SRC_DIR  = os.path.join(_BASE_DIR, "code", "src")

_ds.HEARTBEAT           = os.path.join(_SRC_DIR,  "ibkr", "states", "heartbeat.json")
_ds.BOT_STATE           = os.path.join(_SRC_DIR,  "ibkr", "states", "ibkr_forex_state.json")
_ds.LOGS_DIR            = os.path.join(_SRC_DIR,  "ibkr", "logs")
_ds.METRICS_FILE        = os.path.join(_BASE_DIR, "metrics", "ibkr_metrics.json")
_ds.EQUITY_HISTORY_FILE = os.path.join(_BASE_DIR, "states", "ibkr_equity_history.json")
_ds.BOT_LOG             = os.path.join(_BASE_DIR, "code",   "logs", "ibkr_forex.log")
_ds.PORT                = 8083
_ds._HTML_FILE          = os.path.join(_SCRIPTS_DIR, "ibkr_forex_dashboard.html")
_ds._BINANCE_REST       = ""   # désactivé : pas d'API Binance pour IBKR


# ─── Désactiver la récupération du solde Binance ──────────────────────────
def _noop_fetch_balances() -> None:
    return None


_ds._fetch_account_balances = _noop_fetch_balances  # type: ignore[assignment]
_ds._fetch_usdc_balance     = _noop_fetch_balances  # type: ignore[assignment]


# ─── Remplacer le parser crypto_pairs par les paires Forex ───────────────
def _parse_forex_pairs() -> list[dict[str, str]]:
    return [
        {"backtest_pair": "EURUSD", "real_pair": "EURUSD"},
        {"backtest_pair": "EURGBP", "real_pair": "EURGBP"},
    ]


_ds._parse_crypto_pairs = _parse_forex_pairs  # type: ignore[assignment]


# ─── Point d'entrée ───────────────────────────────────────────────────────
if __name__ == "__main__":
    from http.server import ThreadingHTTPServer  # noqa: E402

    # (pas de prewarm Binance balance pour IBKR)
    server = ThreadingHTTPServer(("127.0.0.1", _ds.PORT), _ds.DashboardHandler)
    server.daemon_threads = True
    print(f"[IBKR-DASHBOARD] Démarré → http://127.0.0.1:{_ds.PORT}/dashboard")
    print("[IBKR-DASHBOARD] Ctrl+C pour arrêter.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[IBKR-DASHBOARD] Arrêté.")
        sys.exit(0)
