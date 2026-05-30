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
        {"backtest_pair": "GBPUSD", "real_pair": "GBPUSD"},
    ]


_ds._parse_crypto_pairs = _parse_forex_pairs  # type: ignore[assignment]


# ─── Corriger le calcul d'equity pour IBKR Forex ─────────────────────────
# Binance : equity = cash + market_value  (l'actif est détenu)
# IBKR FX : equity = initial_capital + unrealized_pnl  (SHORT = pas d'actif détenu, qty = notionnel)
_IBKR_INITIAL_CAPITAL: float = 10_000.0

# ─── Patch PnL réalisé : inclure side="cover" (fermeture SHORT Forex) ────────
# dashboard_server.py compte uniquement side="sell" (trades Binance spot).
# Pour IBKR Forex, le close d'un SHORT est enregistré avec side="cover".

_orig_cumulative_pnl = _ds._cumulative_pnl
_orig_build_equity_curve = _ds._build_equity_curve
_orig_win_stats = _ds._win_stats


def _ibkr_cumulative_pnl(real_pairs: set) -> tuple:
    total = 0.0
    count = 0
    import os
    import json as _json
    try:
        files = []
        if os.path.isdir(_ds.LOGS_DIR):
            for _f in os.listdir(_ds.LOGS_DIR):
                if _f == "trade_journal.jsonl" or (_f.startswith("journal_") and _f.endswith(".jsonl")):
                    files.append(os.path.join(_ds.LOGS_DIR, _f))
        for _path in files:
            try:
                with open(_path, encoding="utf-8") as _fh:
                    for _line in _fh:
                        _line = _line.strip()
                        if not _line:
                            continue
                        _rec = _json.loads(_line)
                        if real_pairs and _rec.get("pair") not in real_pairs:
                            continue
                        _pnl = _rec.get("pnl")
                        if _pnl is not None and _rec.get("side", "").lower() in ("sell", "cover"):
                            total += float(_pnl)
                            count += 1
            except Exception:
                pass
    except Exception:
        pass
    return total, count


def _ibkr_build_equity_curve(starting_equity: float, real_pairs: set) -> list:
    import os
    import json as _json
    closes: list = []
    try:
        if os.path.isdir(_ds.LOGS_DIR):
            for _fname in sorted(os.listdir(_ds.LOGS_DIR)):
                if _fname == "trade_journal.jsonl" or (_fname.startswith("journal_") and _fname.endswith(".jsonl")):
                    _path = os.path.join(_ds.LOGS_DIR, _fname)
                    try:
                        with open(_path, encoding="utf-8") as _fh:
                            for _line in _fh:
                                _line = _line.strip()
                                if not _line:
                                    continue
                                _rec = _json.loads(_line)
                                if real_pairs and _rec.get("pair") not in real_pairs:
                                    continue
                                if _rec.get("side", "").lower() in ("sell", "cover") and _rec.get("pnl") is not None:
                                    closes.append({"ts": _rec["ts"], "pnl": float(_rec["pnl"])})
                    except Exception:
                        pass
    except Exception:
        pass
    closes.sort(key=lambda r: r.get("ts", ""))
    if not closes:
        return []
    equity = starting_equity
    points: list = [{"ts": closes[0]["ts"], "equity": round(equity, 2)}]
    for s in closes:
        equity += s["pnl"]
        points.append({"ts": s["ts"], "equity": round(equity, 2)})
    if len(points) > 250:
        step = max(1, len(points) // 250)
        last = points[-1]
        points = points[::step]
        if points[-1]["ts"] != last["ts"]:
            points.append(last)
    return points


_ds._cumulative_pnl = _ibkr_cumulative_pnl          # type: ignore[assignment]
_ds._build_equity_curve = _ibkr_build_equity_curve  # type: ignore[assignment]


def _ibkr_win_stats(real_pairs: set) -> tuple:
    """Remplace _win_stats : compte side in ('sell', 'cover') pour IBKR Forex."""
    import os
    import json as _json
    win_count = 0
    total_count = 0
    try:
        if os.path.isdir(_ds.LOGS_DIR):
            for _f in os.listdir(_ds.LOGS_DIR):
                if _f == "trade_journal.jsonl" or (_f.startswith("journal_") and _f.endswith(".jsonl")):
                    _path = os.path.join(_ds.LOGS_DIR, _f)
                    try:
                        with open(_path, encoding="utf-8") as _fh:
                            for _line in _fh:
                                _line = _line.strip()
                                if not _line:
                                    continue
                                _rec = _json.loads(_line)
                                if real_pairs and _rec.get("pair") not in real_pairs:
                                    continue
                                if _rec.get("side", "").lower() not in ("sell", "cover"):
                                    continue
                                total_count += 1
                                _pnl = _rec.get("pnl")
                                if _pnl is not None and float(_pnl) > 0:
                                    win_count += 1
                    except Exception:
                        pass
    except Exception:
        pass
    win_rate = round(win_count / total_count * 100.0, 1) if total_count > 0 else None
    return win_rate, win_count, total_count


_ds._win_stats = _ibkr_win_stats  # type: ignore[assignment]

_orig_update_equity_history = _ds._update_equity_history
_orig_collect_data = _ds.collect_data

# Neutraliser l'écriture d'equity incorrecte pendant l'appel original à collect_data
_ds._update_equity_history = lambda eq, now_ts=None: _ds._read_equity_history()  # type: ignore[assignment]


def _ibkr_collect_data() -> dict:
    data = _orig_collect_data()
    unrealized: float = data.get("total_unrealized_pnl") or 0.0

    # PnL réalisé (all-time) = somme des closes side=sell|cover dans le journal IBKR
    _real_pairs = {p["real_pair"] for p in _parse_forex_pairs()}
    realized_pnl, _ = _ibkr_cumulative_pnl(_real_pairs)

    # equity = capital initial + PnL réalisé + PnL latent (positions ouvertes)
    equity: float = round(_IBKR_INITIAL_CAPITAL + realized_pnl + unrealized, 2)
    # cash = capital initial + PnL réalisé (pas de market value en Forex marge)
    cash: float = round(_IBKR_INITIAL_CAPITAL + realized_pnl, 2)

    # Écrire la bonne valeur d'equity dans l'historique
    now_utc: str | None = data.get("now")
    history = _orig_update_equity_history(equity, now_ts=now_utc)

    # Reconstruire la courbe equity avec les valeurs corrigées
    latest_buy_ts = None
    for pd in data.get("pairs", {}).values():
        if pd.get("in_position") and pd.get("buy_timestamp") is not None:
            ts = pd["buy_timestamp"]
            if latest_buy_ts is None or ts > latest_buy_ts:
                latest_buy_ts = ts

    equity_curve = _ds._build_mark_to_market_curve(
        _IBKR_INITIAL_CAPITAL, equity, latest_buy_ts, history
    )

    data["total_equity"]     = equity
    data["usdc_balance"]     = cash
    data["starting_equity"]  = _IBKR_INITIAL_CAPITAL
    data["equity_delta"]     = round(unrealized, 2)
    data["equity_curve"]     = equity_curve
    data["max_drawdown_pct"] = _ds._max_drawdown_pct(history)
    return data


_ds.collect_data = _ibkr_collect_data  # type: ignore[assignment]


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
