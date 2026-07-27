"""Standalone Kraken Pro Spot dashboard (port 8084).

This server is intentionally independent from the Binance dashboard runtime.
It reads only Kraken state, heartbeat, metrics and journal files.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.abspath(os.path.join(_SCRIPTS_DIR, "..", ".."))
_SRC_DIR = os.path.join(_BASE_DIR, "code", "src")
_KRAKEN_BOT_DIR = os.path.join(_SRC_DIR, "kraken_bot")

_ORIGINAL_SYS_PATH = list(sys.path)
_KRAKEN_LOCAL_MODULES = {
    "broker_models",
    "exceptions",
    "kraken_client",
}
_ORIGINAL_MODULES = {
    _name: sys.modules[_name]
    for _name in _KRAKEN_LOCAL_MODULES
    if _name in sys.modules
}
try:
    for _path in (_SCRIPTS_DIR, _SRC_DIR, _KRAKEN_BOT_DIR):
        if _path not in sys.path:
            sys.path.insert(0, _path)
    for _name in _KRAKEN_LOCAL_MODULES:
        sys.modules.pop(_name, None)

    from broker_models import BrokerConfig  # noqa: E402
    from kraken_client import KrakenSpotClient  # noqa: E402
finally:
    sys.path[:] = _ORIGINAL_SYS_PATH
    for _name in _KRAKEN_LOCAL_MODULES:
        sys.modules.pop(_name, None)
    sys.modules.update(_ORIGINAL_MODULES)

PORT = int(os.environ.get("KRAKEN_DASHBOARD_PORT", "8084"))
LOGS_DIR = os.path.join(_SRC_DIR, "logs")
KRAKEN_MODULE_LOGS_DIR = os.path.join(_KRAKEN_BOT_DIR, "logs")
_KRAKEN_STATES_DIR = os.path.join(_KRAKEN_BOT_DIR, "states")
BOT_STATE = os.path.join(_KRAKEN_STATES_DIR, "bot_state_kraken.json")
HEARTBEAT = os.path.join(_KRAKEN_STATES_DIR, "heartbeat_kraken.json")
METRICS_FILE = os.path.join(_BASE_DIR, "code", "metrics", "metrics_kraken.json")
EQUITY_HISTORY_FILE = os.path.join(_BASE_DIR, "states", "kraken_dashboard_equity_history.json")
BOT_LOG = os.path.join(LOGS_DIR, "kraken_trading_bot.log")

_BALANCE_TTL = 15.0
_account_balances_cache: tuple[float, dict[str, float] | None] = (0.0, None)
_kraken_client: KrakenSpotClient | None = None
_HTML_FILE = os.path.join(_SCRIPTS_DIR, "dashboard.html")
_JSON_HEADER = b"JSON_V1:"
_HEADER_LEN = len(_JSON_HEADER) + 32


def _load_env_key(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value.strip().strip('"').strip("'")
    env_path = os.path.join(_BASE_DIR, ".env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw_value = line.split("=", 1)
                if key.strip() == name:
                    return raw_value.strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        if raw.startswith(_JSON_HEADER):
            raw = raw[_HEADER_LEN:]
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _file_age_seconds(path: str) -> float | None:
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


def _age_seconds(ts_str: str | None) -> int:
    if not ts_str:
        return 999999
    try:
        parsed = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - parsed).total_seconds())
    except Exception:
        return 999999


def _get_daily_pnl(tracker: Any) -> tuple[float, float]:
    if not isinstance(tracker, Mapping):
        return 0.0, 0.0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day_entry = tracker.get(today)
    if isinstance(day_entry, Mapping) and "total_pnl" in day_entry:
        total_pnl = float(day_entry.get("total_pnl") or 0.0)
        equity = float(tracker.get("starting_equity") or 0.0)
        pct = (total_pnl / equity * 100.0) if equity > 0 else 0.0
        return total_pnl, pct
    if "daily_pnl" in tracker:
        return float(tracker.get("daily_pnl") or 0.0), float(tracker.get("daily_pnl_pct") or 0.0)
    return 0.0, 0.0


def _max_drawdown_pct(equity_points: list[dict[str, Any]]) -> float:
    peak = 0.0
    max_dd = 0.0
    for point in equity_points:
        try:
            equity = float(point.get("equity") or 0.0)
        except (TypeError, ValueError):
            continue
        if equity > peak:
            peak = equity
        elif peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
    return round(max_dd, 2)


def _read_log_lines(n: int = 120) -> list[str]:
    for path in (
        BOT_LOG,
        os.path.join(LOGS_DIR, "trading_bot.log"),
        os.path.join(KRAKEN_MODULE_LOGS_DIR, "kraken_trading_bot.log"),
    ):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return [line.rstrip("\n\r") for line in fh.readlines()[-n:]]
        except Exception:
            continue
    return []


def _get_kraken_client() -> KrakenSpotClient | None:
    global _kraken_client
    if _kraken_client is not None:
        return _kraken_client

    api_key = _load_env_key("KRAKEN_API_KEY")
    api_secret = _load_env_key("KRAKEN_SECRET_KEY")
    if not api_key or not api_secret:
        return None

    _kraken_client = KrakenSpotClient(
        BrokerConfig(
            broker="KRAKEN",
            api_key=api_key,
            api_secret=api_secret,
            api_url=os.environ.get("KRAKEN_API_URL", "https://api.kraken.com"),
            ws_url=os.environ.get("KRAKEN_WS_URL", "wss://ws-auth.kraken.com/v2"),
            enable_native_trailing=False,
            strict_pair_validation=True,
        ),
        requests_params={"timeout": 8},
    )
    return _kraken_client


def _fetch_kraken_account_balances() -> dict[str, float] | None:
    global _account_balances_cache
    now = time.time()
    if now - _account_balances_cache[0] < _BALANCE_TTL:
        return _account_balances_cache[1]

    client = _get_kraken_client()
    if client is None:
        _account_balances_cache = (now, None)
        return None

    try:
        account = client.get_account()
        balances = {
            str(item.get("asset") or "").upper(): float(item.get("free") or 0.0)
            + float(item.get("locked") or 0.0)
            for item in account.get("balances", [])
            if isinstance(item, Mapping) and item.get("asset")
        }
        _account_balances_cache = (now, balances)
        return balances
    except Exception:
        _account_balances_cache = (now, None)
        return None


def _fetch_kraken_cash_balance() -> float | None:
    balances = _fetch_kraken_account_balances()
    if balances is None:
        return None
    return (balances.get("USDC") or 0.0) + (balances.get("USD") or 0.0)


def _fetch_kraken_usdc_balance() -> float | None:
    """Backward-compatible dashboard field: USD + USDC cash."""
    return _fetch_kraken_cash_balance()


def _record_is_kraken(record: Mapping[str, Any]) -> bool:
    broker = str(record.get("broker") or record.get("exchange") or "").strip().lower()
    return broker == "kraken"


def _iter_kraken_journal_records(real_pairs: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    files: list[str] = []
    for logs_dir in dict.fromkeys([LOGS_DIR, KRAKEN_MODULE_LOGS_DIR]):
        try:
            files.extend(
                os.path.join(logs_dir, name)
                for name in os.listdir(logs_dir)
                if name == "trade_journal.jsonl" or (name.startswith("journal_") and name.endswith(".jsonl"))
            )
        except OSError:
            continue

    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    if real_pairs and rec.get("pair") not in real_pairs:
                        continue
                    if not _record_is_kraken(rec):
                        continue
                    records.append(rec)
        except OSError:
            continue
    return records


def _kraken_cumulative_pnl(real_pairs: set[str]) -> tuple[float, int]:
    total = 0.0
    count = 0
    for rec in _iter_kraken_journal_records(real_pairs):
        if str(rec.get("side", "")).lower() != "sell":
            continue
        pnl = rec.get("pnl")
        if pnl is not None:
            total += float(pnl)
            count += 1
    return total, count


def _kraken_recent_trades(real_pairs: set[str], limit: int = 20) -> list[dict[str, Any]]:
    trades = _iter_kraken_journal_records(real_pairs)
    trades.sort(key=lambda rec: rec.get("ts", ""), reverse=True)
    return trades[:limit]


def _kraken_build_equity_curve(starting_equity: float, real_pairs: set[str]) -> list[dict[str, Any]]:
    sells = [
        {"ts": rec["ts"], "pnl": float(rec["pnl"])}
        for rec in _iter_kraken_journal_records(real_pairs)
        if str(rec.get("side", "")).lower() == "sell" and rec.get("pnl") is not None and rec.get("ts")
    ]
    sells.sort(key=lambda rec: rec.get("ts", ""))
    if not sells:
        return []

    equity = starting_equity
    points: list[dict[str, Any]] = [{"ts": sells[0]["ts"], "equity": round(equity, 2)}]
    for sell in sells:
        equity += sell["pnl"]
        points.append({"ts": sell["ts"], "equity": round(equity, 2)})
    if len(points) > 250:
        step = max(1, len(points) // 250)
        last = points[-1]
        points = points[::step]
        if points[-1]["ts"] != last["ts"]:
            points.append(last)
    return points


def _kraken_win_stats(real_pairs: set[str]) -> tuple[float | None, int, int]:
    win_count = 0
    total_count = 0
    for rec in _iter_kraken_journal_records(real_pairs):
        if str(rec.get("side", "")).lower() != "sell":
            continue
        pnl = rec.get("pnl")
        if pnl is None:
            continue
        total_count += 1
        if float(pnl) > 0:
            win_count += 1
    win_rate = round(win_count / total_count * 100.0, 1) if total_count else None
    return win_rate, win_count, total_count


def _extract_pair_states(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    ignored = {
        "emergency_halt",
        "emergency_halt_reason",
        "_daily_pnl_tracker",
        "_state_version",
        "reconcile_failed",
        "reconcile_failed_reason",
        "kraken_preflight",
        "kraken_private_api_ok",
    }
    return {
        key: dict(value)
        for key, value in state.items()
        if key not in ignored and isinstance(value, Mapping)
    }


def _kraken_collect_data() -> dict[str, Any]:
    raw_state = _read_json(BOT_STATE)
    heartbeat = _read_json(HEARTBEAT)
    metrics = _read_json(METRICS_FILE)
    raw_pair_states = _extract_pair_states(raw_state)
    real_pairs = set(raw_pair_states)

    usdc_balance = _fetch_kraken_usdc_balance()
    if usdc_balance is None:
        hb_balance = heartbeat.get("usdc_balance")
        try:
            usdc_balance = float(hb_balance) if hb_balance is not None else None
        except (TypeError, ValueError):
            usdc_balance = None

    status = "OK"
    detail = ""
    preflight = raw_state.get("kraken_preflight")
    if raw_state.get("kraken_private_api_ok") is False:
        status = "KRAKEN PRIVATE API KO"
        if isinstance(preflight, Mapping):
            detail = str(
                preflight.get("permission_error")
                or preflight.get("nonce_error")
                or "Balance/OpenOrders/ClosedOrders KO"
            )
    elif raw_state.get("reconcile_failed") or usdc_balance is None:
        status = "KRAKEN DEGRADE"
        detail = str(raw_state.get("reconcile_failed_reason") or "USD/USDC cash balance unavailable")

    starting_equity = 0.0
    tracker = raw_state.get("_daily_pnl_tracker")
    if isinstance(tracker, Mapping):
        try:
            starting_equity = float(tracker.get("starting_equity") or 0.0)
        except (TypeError, ValueError):
            starting_equity = 0.0
    if starting_equity <= 0.0 and usdc_balance is not None:
        starting_equity = usdc_balance

    cumulative_pnl, closed_trades = _kraken_cumulative_pnl(real_pairs)
    win_rate, win_count, win_total = _kraken_win_stats(real_pairs)
    daily_pnl, daily_pnl_pct = _get_daily_pnl(tracker)
    age = _age_seconds(str(heartbeat.get("timestamp") or "")) if heartbeat else 999999
    alive = age < 300

    metrics_pairs = metrics.get("pairs") if isinstance(metrics.get("pairs"), Mapping) else {}
    pairs: dict[str, dict[str, Any]] = {}
    total_unrealized_pnl = 0.0
    total_market_value = 0.0
    oos_blocked_count = 0
    for symbol, state in raw_pair_states.items():
        metric_state = metrics_pairs.get(symbol, {}) if isinstance(metrics_pairs, Mapping) else {}
        if not isinstance(metric_state, Mapping):
            metric_state = {}
        in_position = state.get("last_order_side") == "BUY"
        entry = state.get("entry_price")
        spot = state.get("ticker_spot_price") or state.get("spot_price")
        qty = state.get("initial_position_size") or state.get("quantity")

        unrealized_pnl = None
        unrealized_pct = None
        if in_position and entry and spot and qty is not None:
            try:
                unrealized_pnl = (float(spot) - float(entry)) * float(qty)
                unrealized_pct = (float(spot) - float(entry)) / float(entry) * 100.0
                total_unrealized_pnl += unrealized_pnl
                total_market_value += float(spot) * float(qty)
            except (TypeError, ValueError, ZeroDivisionError):
                unrealized_pnl = None
                unrealized_pct = None

        is_oos_blocked = bool(
            state.get("oos_blocked")
            or metric_state.get("oos_blocked")
            or state.get("wf_status") == "oos_failed"
            or state.get("entries_ready") is False
        )
        if is_oos_blocked:
            oos_blocked_count += 1

        raw_best_params = state.get("last_best_params")
        last_best_params: Mapping[str, Any] = raw_best_params if isinstance(raw_best_params, Mapping) else {}
        pairs[symbol] = {
            "real_pair": symbol,
            "in_position": in_position,
            "side": state.get("last_order_side") or "BUY",
            "entry_price": entry,
            "spot_price": spot,
            "qty": qty,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pct": unrealized_pct,
            "stop_loss": state.get("stop_loss") or state.get("stop_loss_at_entry"),
            "sl_dist_pct": None,
            "sl_placed": bool(state.get("sl_exchange_placed") or metric_state.get("sl_placed")),
            "trailing_active": bool(state.get("trailing_stop_activated", False)),
            "trailing_stop": state.get("trailing_stop"),
            "scenario": state.get("entry_scenario") or last_best_params.get("scenario", ""),
            "timeframe": state.get("entry_timeframe") or last_best_params.get("timeframe", ""),
            "entry_ema1": state.get("entry_ema1"),
            "entry_ema2": state.get("entry_ema2"),
            "last_execution": state.get("last_execution") or metric_state.get("last_execution"),
            "execution_count": int(state.get("execution_count") or metric_state.get("execution_count") or 0),
            "oos_blocked": is_oos_blocked,
            "drawdown_halted": bool(state.get("drawdown_halted", False) or metric_state.get("drawdown_halted", False)),
            "buy_timestamp": state.get("buy_timestamp"),
            "breakeven_triggered": bool(state.get("breakeven_triggered", False)),
            "partial_taken_1": bool(state.get("partial_taken_1", False)),
            "partial_taken_2": bool(state.get("partial_taken_2", False)),
            "wf_status": state.get("wf_status"),
            "entries_ready": bool(state.get("entries_ready", False)),
        }

    open_count = sum(1 for item in pairs.values() if item["in_position"])
    total_equity = (usdc_balance or 0.0) + total_market_value
    equity_delta = total_equity - starting_equity if starting_equity > 0 else None
    equity_curve = _kraken_build_equity_curve(starting_equity, real_pairs)
    max_drawdown_pct = _max_drawdown_pct(equity_curve)

    return {
        "broker": "KRAKEN",
        "now": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "now_local": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "alive": alive,
        "age_seconds": age,
        "pid": heartbeat.get("pid"),
        "circuit_mode": heartbeat.get("circuit_mode", "unknown"),
        "error_count": heartbeat.get("error_count", 0),
        "loop_counter": heartbeat.get("loop_counter", 0),
        "emergency_halt": bool(raw_state.get("emergency_halt", metrics.get("emergency_halt", False))),
        "halt_reason": raw_state.get("emergency_halt_reason") or metrics.get("emergency_halt_reason") or "",
        "system_status": status,
        "system_status_detail": detail,
        "state_path": BOT_STATE,
        "heartbeat_path": HEARTBEAT,
        "heartbeat": heartbeat,
        "heartbeat_age_seconds": _file_age_seconds(HEARTBEAT),
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": daily_pnl_pct,
        "starting_equity": starting_equity,
        "usdc_balance": usdc_balance,
        "open_count": open_count,
        "total_pairs": len(pairs),
        "oos_blocked": oos_blocked_count,
        "pairs": pairs,
        "metrics": metrics,
        "cumul_pnl": round(cumulative_pnl, 8),
        "cumulative_pnl": round(cumulative_pnl, 8),
        "equity_delta": equity_delta,
        "closed_trades": closed_trades,
        "trade_count": closed_trades,
        "recent_trades": _kraken_recent_trades(real_pairs, limit=50),
        "total_unrealized_pnl": total_unrealized_pnl,
        "total_equity": total_equity,
        "equity_curve": equity_curve,
        "api_latency_ms": metrics.get("api_latency_ms"),
        "taker_fee": metrics.get("taker_fee", 0.008),
        "maker_fee": metrics.get("maker_fee", 0.004),
        "metrics_ts": metrics.get("timestamp_utc"),
        "bot_version": metrics.get("bot_version", ""),
        "win_rate": win_rate,
        "win_count": win_count,
        "max_drawdown_pct": max_drawdown_pct,
        "win_total": win_total,
    }


def _get_kraken_dashboard_html() -> bytes:
    try:
        with open(_HTML_FILE, encoding="utf-8") as fh:
            html = fh.read()
        html = html.replace("MULTI ASSETS BINANCE Dashboard", "MULTI ASSETS KRAKEN PRO Dashboard")
        html = html.replace("BINANCE", "KRAKEN PRO")
        html = html.replace("Binance", "Kraken")
        html = html.replace("binance", "kraken")
        html = html.replace("free USDC balance", "free USD/USDC cash on Kraken")
        html = html.replace("LIVE TRADING // REAL CAPITAL AT RISK // RISK MANAGEMENT REQUIRED", "KRAKEN PRO SPOT // REAL CAPITAL AT RISK // RISK MANAGEMENT REQUIRED")
        return html.encode("utf-8")
    except Exception:
        pass

    html = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MULTI ASSETS KRAKEN PRO Dashboard</title>
  <style>
    :root { color-scheme: dark; font-family: Segoe UI, Arial, sans-serif; background: #10131a; color: #f3f6fb; }
    body { margin: 0; background: #10131a; }
    header { padding: 18px 24px; border-bottom: 1px solid #293140; display: flex; justify-content: space-between; gap: 16px; align-items: center; }
    .brand { font-weight: 700; letter-spacing: .04em; color: #7dd3fc; }
    main { padding: 22px 24px; display: grid; gap: 16px; max-width: 1320px; margin: 0 auto; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
    .card { border: 1px solid #293140; border-radius: 8px; padding: 14px; background: #151a23; }
    .label { color: #9ca8ba; font-size: 12px; text-transform: uppercase; }
    .value { font-size: 22px; font-weight: 650; margin-top: 6px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 9px 8px; border-bottom: 1px solid #293140; font-size: 13px; }
    th { color: #9ca8ba; font-weight: 600; }
    .ok { color: #86efac; }
    .warn { color: #fbbf24; }
    .bad { color: #fca5a5; }
  </style>
</head>
<body>
  <header>
    <div><span class="brand">KRAKEN PRO</span> <span>Spot Dashboard</span></div>
    <div>KRAKEN PRO SPOT // REAL CAPITAL AT RISK // RISK MANAGEMENT REQUIRED</div>
  </header>
  <main>
    <section class="grid">
      <div class="card"><div class="label">System status</div><div class="value" id="status">...</div></div>
      <div class="card"><div class="label">free USD/USDC cash on Kraken</div><div class="value" id="balance">...</div></div>
      <div class="card"><div class="label">Closed trades</div><div class="value" id="trades">...</div></div>
      <div class="card"><div class="label">Cumulative PnL</div><div class="value" id="pnl">...</div></div>
    </section>
    <section class="card">
      <h2>Paires</h2>
      <table><thead><tr><th>Paire</th><th>WF</th><th>Tradable</th><th>Position</th><th>Raison</th></tr></thead><tbody id="pairs"></tbody></table>
    </section>
    <section class="card">
      <h2>Trades récents</h2>
      <table><thead><tr><th>Heure</th><th>Paire</th><th>Side</th><th>PNL</th></tr></thead><tbody id="recent"></tbody></table>
    </section>
  </main>
  <script>
    function cls(status) {
      if (status === "OK") return "ok";
      if ((status || "").includes("KO")) return "bad";
      return "warn";
    }
    async function refresh() {
      const response = await fetch("/api/data", {cache: "no-store"});
      const data = await response.json();
      const status = document.getElementById("status");
      status.textContent = data.system_status || "...";
      status.className = "value " + cls(data.system_status);
      document.getElementById("balance").textContent = data.usdc_balance == null ? "N/A" : Number(data.usdc_balance).toFixed(2) + " USD-eq";
      document.getElementById("trades").textContent = data.closed_trades ?? 0;
      document.getElementById("pnl").textContent = Number(data.cumulative_pnl || 0).toFixed(4);
      const pairs = document.getElementById("pairs");
      pairs.innerHTML = "";
      Object.entries(data.pairs || {}).forEach(([pair, state]) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${pair}</td><td>${state.wf_status || ""}</td><td>${state.entries_ready ? "OUI" : "NON"}</td><td>${state.last_order_side || "-"}</td><td>${state.wf_block_reason || ""}</td>`;
        pairs.appendChild(tr);
      });
      const recent = document.getElementById("recent");
      recent.innerHTML = "";
      (data.recent_trades || []).forEach((trade) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${trade.ts || ""}</td><td>${trade.pair || ""}</td><td>${trade.side || ""}</td><td>${trade.pnl ?? ""}</td>`;
        recent.appendChild(tr);
      });
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>"""
    return html.encode("utf-8")


class DashboardHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("", "/"):
            self.send_response(302)
            self.send_header("Location", "/dashboard")
            self.end_headers()
            return
        if path == "/dashboard":
            self._send(200, _get_kraken_dashboard_html(), "text/html; charset=utf-8")
            return
        if path == "/api/data":
            body = json.dumps(_kraken_collect_data(), ensure_ascii=False, default=str).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/api/logs":
            body = json.dumps(_read_log_lines(120), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path == "/health":
            self._send(200, b'{"status":"ok"}', "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


def _prewarm_cache() -> None:
    try:
        _fetch_kraken_cash_balance()
    except Exception:
        pass


if __name__ == "__main__":
    threading.Thread(target=_prewarm_cache, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), DashboardHandler)
    server.daemon_threads = True
    print(f"[KRAKEN-DASHBOARD] Demarre -> http://127.0.0.1:{PORT}/dashboard")
    print("[KRAKEN-DASHBOARD] Ctrl+C pour arreter.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[KRAKEN-DASHBOARD] Arrete.")
        sys.exit(0)
