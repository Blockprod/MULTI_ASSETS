"""dashboard_server.py — Institutional-grade monitoring dashboard for MULTI_ASSETS.

Bloomberg Terminal x Datadog aesthetic — dark, dense, precise.
Client-side JS polling every 5s (no full page reload).

Sources de donnees :
  - states/heartbeat.json     -> liveness bot (mis a jour chaque cycle)
  - states/bot_state.json     -> positions, PnL journalier, etat complet
  - metrics/metrics.json      -> snapshot metriques (mis a jour toutes les 5 min)
  - code/src/logs/trade_journal.jsonl -> historique des trades

Endpoints :
  GET /           -> dashboard HTML
  GET /dashboard  -> idem
  GET /api/data   -> donnees JSON brutes
  GET /health     -> health check

Lancement : python dashboard_server.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

BASE_DIR     = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC_DIR      = os.path.join(BASE_DIR, "code", "src")
HEARTBEAT    = os.path.join(SRC_DIR, "states", "heartbeat.json")
BOT_STATE    = os.path.join(SRC_DIR, "states", "bot_state.json")
LOGS_DIR     = os.path.join(SRC_DIR, "logs")
METRICS_FILE = os.path.join(BASE_DIR, "metrics", "metrics.json")
MULTI_SRC    = os.path.join(SRC_DIR, "MULTI_SYMBOLS.py")
ENV_FILE     = os.path.join(BASE_DIR, ".env")
PORT         = 8082
_BINANCE_REST = "https://api.binance.com"

_balance_cache: tuple[float, float | None] = (0.0, None)
_BALANCE_TTL  = 120


# --- solde USDC via Binance API ------------------------------------------

def _load_env_key(name: str) -> str | None:
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return os.environ.get(name)


def _fetch_usdc_balance() -> float | None:
    global _balance_cache
    now = time.time()
    if now - _balance_cache[0] < _BALANCE_TTL:
        return _balance_cache[1]

    api_key    = _load_env_key("BINANCE_API_KEY")
    api_secret = _load_env_key("BINANCE_SECRET_KEY")
    if not api_key or not api_secret:
        _balance_cache = (now, None)
        return None

    try:
        with urllib.request.urlopen(f"{_BINANCE_REST}/api/v3/time", timeout=5) as r:
            server_ts = json.loads(r.read().decode())["serverTime"]
        qs     = f"timestamp={server_ts}&recvWindow=60000"
        sig    = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        url    = f"{_BINANCE_REST}/api/v3/account?{qs}&signature={sig}"
        req    = urllib.request.Request(url, headers={"X-MBX-APIKEY": api_key})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        balance = next(
            (float(b["free"]) for b in data.get("balances", []) if b["asset"] == "USDC"),
            None,
        )
        _balance_cache = (now, balance)
        return balance
    except Exception:
        _balance_cache = (now, None)
        return None


# --- parser crypto_pairs -------------------------------------------------

def _parse_crypto_pairs() -> list[dict[str, str]]:
    try:
        with open(MULTI_SRC, encoding="utf-8") as fh:
            src = fh.read()
        block_match = re.search(r"crypto_pairs\s*=\s*\[(.*?)\]", src, re.DOTALL)
        if not block_match:
            return []
        block = block_match.group(1)
        pairs = re.findall(
            r'"backtest_pair"\s*:\s*"([^"]+)".*?"real_pair"\s*:\s*"([^"]+)"',
            block, re.DOTALL,
        )
        return [{"backtest_pair": bp, "real_pair": rp} for bp, rp in pairs]
    except Exception:
        return []


_JSON_HEADER = b"JSON_V1:"
_HEADER_LEN  = len(_JSON_HEADER) + 32


# --- helpers -------------------------------------------------------------

def _read_json(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        if raw.startswith(_JSON_HEADER):
            raw = raw[_HEADER_LEN:]
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _age_seconds(ts_str: str) -> int:
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        return 999999


def _get_daily_pnl(tracker: Any) -> tuple[float, float]:
    if not isinstance(tracker, dict):
        return 0.0, 0.0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # P5-DASH: new format — {today: {total_pnl, trade_count}, starting_equity: ...}
    day_entry = tracker.get(today)
    if isinstance(day_entry, dict) and "total_pnl" in day_entry:
        total_pnl = float(day_entry.get("total_pnl", 0.0))
        equity = float(tracker.get("starting_equity", 0))
        pct = (total_pnl / equity * 100.0) if equity > 0 else 0.0
        return total_pnl, pct
    # Legacy format — {date: "YYYY-MM-DD", daily_pnl: ..., daily_pnl_pct: ...}
    tracker_date = tracker.get("date", "")
    if tracker_date and tracker_date != today:
        return 0.0, 0.0
    if "daily_pnl" in tracker:
        return float(tracker.get("daily_pnl", 0.0)), float(tracker.get("daily_pnl_pct", 0.0))
    return 0.0, 0.0


def _get_starting_equity(tracker: Any) -> float | None:
    if not isinstance(tracker, dict):
        return None
    if "starting_equity" in tracker:
        return float(tracker["starting_equity"])
    return None


def _cumulative_pnl(real_pairs: set[str]) -> tuple[float, int]:
    total = 0.0
    count = 0
    try:
        files = []
        if os.path.isdir(LOGS_DIR):
            for f in os.listdir(LOGS_DIR):
                if f == "trade_journal.jsonl" or (f.startswith("journal_") and f.endswith(".jsonl")):
                    files.append(os.path.join(LOGS_DIR, f))
        for path in files:
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        if real_pairs and rec.get("pair") not in real_pairs:
                            continue
                        pnl = rec.get("pnl")
                        if pnl is not None and rec.get("side", "").lower() == "sell":
                            total += float(pnl)
                            count += 1
            except Exception:
                pass
    except Exception:
        pass
    return total, count


def _recent_trades(real_pairs: set[str], limit: int = 20) -> list[dict]:
    """Last N closed trades for the activity log."""
    trades: list[dict] = []
    try:
        files = []
        if os.path.isdir(LOGS_DIR):
            for f in os.listdir(LOGS_DIR):
                if f == "trade_journal.jsonl" or (f.startswith("journal_") and f.endswith(".jsonl")):
                    files.append(os.path.join(LOGS_DIR, f))
        for path in sorted(files, reverse=True):
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        if real_pairs and rec.get("pair") not in real_pairs:
                            continue
                        trades.append(rec)
            except Exception:
                pass
    except Exception:
        pass
    trades.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return trades[:limit]


# --- data aggregation ----------------------------------------------------

def collect_data() -> dict:
    hb = _read_json(HEARTBEAT)
    raw_state = _read_json(BOT_STATE)
    mt = _read_json(METRICS_FILE)

    configured = _parse_crypto_pairs()
    backtest_pairs = {p["backtest_pair"] for p in configured}
    real_pairs     = {p["real_pair"]     for p in configured}

    bot_state: dict = raw_state.get("bot_state", raw_state)
    tracker = raw_state.get("_daily_pnl_tracker", {})
    emergency_halt: bool = bool(raw_state.get("emergency_halt", mt.get("emergency_halt", False)))
    halt_reason: str = raw_state.get("emergency_halt_reason", "") or ""

    age = _age_seconds(hb.get("timestamp", "")) if hb else 999999
    alive = age < 300

    daily_pnl, daily_pnl_pct = _get_daily_pnl(tracker)
    starting_equity = _get_starting_equity(tracker)
    usdc_balance: float | None = hb.get("usdc_balance") if hb else None
    if usdc_balance is None:
        usdc_balance = _fetch_usdc_balance()

    if daily_pnl_pct == 0.0 and starting_equity and starting_equity > 0:
        daily_pnl_pct = (daily_pnl / starting_equity) * 100.0

    pairs: dict[str, dict] = {}
    oos_blocked_count = 0
    for symbol, ps in bot_state.items():
        if not isinstance(ps, dict):
            continue
        if backtest_pairs and symbol not in backtest_pairs:
            continue
        in_position = ps.get("last_order_side") == "BUY"
        entry = ps.get("entry_price")
        spot = ps.get("ticker_spot_price")
        qty = ps.get("initial_position_size")

        unrealized_pnl = None
        unrealized_pct = None
        if in_position and entry and spot and qty:
            try:
                unrealized_pnl = (float(spot) - float(entry)) * float(qty)
                unrealized_pct = (float(spot) - float(entry)) / float(entry) * 100.0
            except Exception:
                pass

        sl = ps.get("stop_loss")
        sl_dist_pct = None
        if in_position and entry and sl:
            try:
                sl_dist_pct = abs((float(entry) - float(sl)) / float(entry) * 100.0)
            except Exception:
                pass

        mt_pair = (mt.get("pairs") or {}).get(symbol, {})
        real_pair = next((p["real_pair"] for p in configured if p["backtest_pair"] == symbol), symbol)

        is_oos_blocked = bool(ps.get("oos_blocked", mt_pair.get("oos_blocked", False)))
        if is_oos_blocked:
            oos_blocked_count += 1

        pairs[symbol] = {
            "real_pair":           real_pair,
            "in_position":         in_position,
            "entry_price":         entry,
            "spot_price":          spot,
            "qty":                 qty,
            "unrealized_pnl":      unrealized_pnl,
            "unrealized_pct":      unrealized_pct,
            "stop_loss":           sl,
            "sl_dist_pct":         sl_dist_pct,
            "sl_placed":           bool(ps.get("sl_exchange_placed") or mt_pair.get("sl_placed")),
            "trailing_active":     bool(ps.get("trailing_stop_activated", False)),
            "trailing_stop":       ps.get("trailing_stop"),
            "scenario":            ps.get("entry_scenario") or (ps.get("last_best_params") or {}).get("scenario", ""),
            "timeframe":           ps.get("entry_timeframe") or (ps.get("last_best_params") or {}).get("timeframe", ""),
            "last_execution":      ps.get("last_execution"),
            "execution_count":     int(ps.get("execution_count") or 0),
            "oos_blocked":         is_oos_blocked,
            "drawdown_halted":     bool(ps.get("drawdown_halted", False)),
            "buy_timestamp":       ps.get("buy_timestamp"),
            "breakeven_triggered": bool(ps.get("breakeven_triggered", False)),
            "partial_taken_1":     bool(ps.get("partial_taken_1", False)),
            "partial_taken_2":     bool(ps.get("partial_taken_2", False)),
        }

    open_count = sum(1 for p in pairs.values() if p["in_position"])
    cumul_pnl, trade_count = _cumulative_pnl(real_pairs)
    recent = _recent_trades(real_pairs)

    return {
        "now":             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "now_local":       datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "alive":           alive,
        "age_seconds":     age,
        "pid":             hb.get("pid"),
        "circuit_mode":    hb.get("circuit_mode", "unknown"),
        "error_count":     hb.get("error_count", 0),
        "loop_counter":    hb.get("loop_counter", 0),
        "emergency_halt":  emergency_halt,
        "halt_reason":     halt_reason,
        "daily_pnl":       daily_pnl,
        "daily_pnl_pct":   daily_pnl_pct,
        "starting_equity": starting_equity,
        "usdc_balance":    usdc_balance,
        "open_count":      open_count,
        "total_pairs":     len(pairs),
        "oos_blocked":     oos_blocked_count,
        "cumul_pnl":       cumul_pnl,
        "trade_count":     trade_count,
        "pairs":           pairs,
        "recent_trades":   recent,
        "api_latency_ms":  mt.get("api_latency_ms"),
        "taker_fee":       mt.get("taker_fee", 0.0007),
        "maker_fee":       mt.get("maker_fee", 0.0002),
        "metrics_ts":      mt.get("timestamp_utc"),
        "bot_version":     mt.get("bot_version", ""),
    }


# --- HTML template -------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MULTI_ASSETS | Spot Monitor</title>
  <style>
    :root {
      --bg-0: #06080d;
      --bg-1: #0c1017;
      --bg-2: #111820;
      --bg-3: #19212d;
      --border: #1e2a3a;
      --border-accent: #2a3a52;
      --text-0: #e8edf5;
      --text-1: #a4b1c7;
      --text-2: #6b7a94;
      --text-3: #3f4f66;
      --accent: #3b82f6;
      --accent-dim: #1e3a5f;
      --green: #10b981;
      --green-dim: #064e3b;
      --red: #ef4444;
      --red-dim: #4c1414;
      --yellow: #f59e0b;
      --yellow-dim: #4a3508;
      --cyan: #06b6d4;
      --purple: #8b5cf6;
      --font-mono: 'SF Mono', 'Cascadia Code', 'JetBrains Mono', 'Fira Code', Consolas, monospace;
      --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, sans-serif;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html { height: 100%; }
    body {
      font-family: var(--font-sans);
      background: var(--bg-0);
      color: var(--text-0);
      font-size: 13px;
      line-height: 1.4;
      overflow-x: hidden;
      height: 100%;
      display: flex;
      flex-direction: column;
    }

    /* --- TOP BAR --- */
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 20px;
      background: var(--bg-1);
      border-bottom: 1px solid var(--border);
      flex-shrink: 0;
      z-index: 100;
    }
    .topbar-left {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .logo {
      font-family: var(--font-mono);
      font-size: 15px;
      font-weight: 700;
      color: var(--accent);
      letter-spacing: 2px;
    }
    .logo span { color: var(--text-2); font-weight: 400; }
    .mode-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 3px 10px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      font-family: var(--font-mono);
      text-transform: uppercase;
      letter-spacing: 1px;
    }
    .mode-badge.live { background: var(--green-dim); color: var(--green); border: 1px solid var(--green); }
    .mode-badge.disconnected { background: var(--red-dim); color: var(--red); border: 1px solid var(--red); }
    .mode-badge.halted { background: var(--red-dim); color: var(--red); border: 1px solid var(--red); }
    .mode-badge.paused { background: var(--yellow-dim); color: var(--yellow); border: 1px solid var(--yellow); }
    .status-dot {
      width: 8px; height: 8px; border-radius: 50%;
      display: inline-block;
    }
    .status-dot.ok { background: var(--green); box-shadow: 0 0 6px var(--green); }
    .status-dot.warn { background: var(--yellow); box-shadow: 0 0 6px var(--yellow); }
    .status-dot.err { background: var(--red); box-shadow: 0 0 6px var(--red); animation: pulse 1.5s infinite; }
    @keyframes pulse {
      0%,100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    .topbar-right {
      display: flex;
      align-items: center;
      gap: 16px;
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--text-2);
    }
    .topbar-right .tick { color: var(--text-1); }

    /* --- MAIN GRID --- */
    .dashboard {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr 1fr;
      grid-template-rows: auto 1fr 1fr auto;
      gap: 1px;
      padding: 1px;
      background: var(--border);
      flex: 1;
      overflow: auto;
    }

    /* --- PANELS --- */
    .panel {
      background: var(--bg-1);
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
    }
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 10px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--border);
    }
    .panel-title {
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1.5px;
      color: var(--text-2);
    }
    .panel-badge {
      font-size: 10px;
      font-family: var(--font-mono);
      padding: 2px 6px;
      border-radius: 3px;
      background: var(--bg-3);
      color: var(--text-2);
    }

    /* --- KPI STRIP --- */
    .kpi-strip {
      grid-column: 1 / -1;
      display: grid;
      grid-template-columns: repeat(7, 1fr);
      gap: 1px;
      background: var(--border);
    }
    .kpi {
      background: var(--bg-1);
      padding: 12px 16px;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .kpi-label {
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--text-2);
    }
    .kpi-value {
      font-family: var(--font-mono);
      font-size: 22px;
      font-weight: 700;
      color: var(--text-0);
      line-height: 1.2;
    }
    .kpi-sub {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--text-2);
    }
    .kpi-value.positive { color: var(--green); }
    .kpi-value.negative { color: var(--red); }
    .kpi-value.warn { color: var(--yellow); }
    .kpi-value.accent { color: var(--accent); }

    /* --- BANNERS --- */
    .halt-banner {
      display: none;
      background: var(--red-dim);
      border-bottom: 1px solid var(--red);
      padding: 10px 16px;
      text-align: center;
      font-family: var(--font-mono);
      font-size: 12px;
      font-weight: 600;
      color: var(--red);
      letter-spacing: 1px;
      flex-shrink: 0;
    }
    .halt-banner.visible { display: block; }
    .disconnected-banner {
      display: none;
      background: var(--red-dim);
      border-bottom: 1px solid var(--red);
      padding: 8px 16px;
      text-align: center;
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--red);
      flex-shrink: 0;
    }
    .disconnected-banner.visible { display: block; }

    /* --- POSITIONS TABLE --- */
    .positions-panel { grid-column: 1 / 4; }
    .data-table {
      width: 100%;
      border-collapse: collapse;
      font-family: var(--font-mono);
      font-size: 11px;
    }
    .data-table th {
      text-align: left;
      padding: 6px 8px;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--text-3);
      border-bottom: 1px solid var(--border-accent);
      white-space: nowrap;
    }
    .data-table th.right, .data-table td.right { text-align: right; }
    .data-table td {
      padding: 5px 8px;
      border-bottom: 1px solid var(--bg-3);
      color: var(--text-1);
      white-space: nowrap;
    }
    .data-table tr:hover td { background: var(--bg-2); }
    .pnl-pos { color: var(--green); }
    .pnl-neg { color: var(--red); }
    .sl-ok { color: var(--green); }
    .sl-missing { color: var(--red); font-weight: 600; }
    .trailing-on { color: var(--cyan); }
    .empty-state {
      display: flex;
      align-items: center;
      justify-content: center;
      flex: 1;
      color: var(--text-3);
      font-size: 12px;
      font-style: italic;
      min-height: 40px;
    }
    .panel { overflow: auto; min-height: 0; }

    /* --- SYSTEM PANEL --- */
    .system-panel { grid-column: 4 / 5; }
    .sys-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 5px 0;
      border-bottom: 1px solid var(--bg-3);
    }
    .sys-row:last-child { border-bottom: none; }
    .sys-label { font-size: 11px; color: var(--text-2); }
    .sys-value {
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--text-1);
    }

    /* --- PAIRS OVERVIEW --- */
    .pairs-panel { grid-column: 1 / 3; }
    .pairs-table {
      width: 100%;
      border-collapse: collapse;
      font-family: var(--font-mono);
      font-size: 11px;
    }
    .pairs-table th {
      text-align: left;
      padding: 6px 8px;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--text-3);
      border-bottom: 1px solid var(--border-accent);
      white-space: nowrap;
    }
    .pairs-table td {
      padding: 5px 8px;
      border-bottom: 1px solid var(--bg-3);
      color: var(--text-1);
      white-space: nowrap;
    }
    .pairs-table tr:hover td { background: var(--bg-2); }
    .status-badge {
      display: inline-block;
      padding: 2px 6px;
      border-radius: 3px;
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.5px;
    }
    .status-badge.long { background: var(--green-dim); color: var(--green); }
    .status-badge.waiting { background: var(--bg-3); color: var(--text-2); }
    .status-badge.oos { background: var(--red-dim); color: var(--red); }
    .status-badge.halted { background: var(--yellow-dim); color: var(--yellow); }

    /* --- RISK PANEL --- */
    .risk-panel { grid-column: 3 / 5; }
    .risk-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1px;
      background: var(--border);
      border-radius: 4px;
      overflow: hidden;
    }
    .risk-item {
      background: var(--bg-1);
      padding: 10px 12px;
    }
    .risk-label {
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--text-3);
      margin-bottom: 2px;
    }
    .risk-value {
      font-family: var(--font-mono);
      font-size: 16px;
      font-weight: 600;
      color: var(--text-0);
    }

    /* --- PROGRESS BAR --- */
    .progress-track {
      height: 4px;
      background: var(--bg-3);
      border-radius: 2px;
      overflow: hidden;
      margin-top: 4px;
    }
    .progress-fill {
      height: 100%;
      border-radius: 2px;
      transition: width 0.5s ease;
    }

    /* --- ACTIVITY LOG --- */
    .alerts-panel { grid-column: 1 / -1; }
    .log-line {
      display: flex;
      gap: 12px;
      padding: 4px 0;
      border-bottom: 1px solid var(--bg-3);
      font-family: var(--font-mono);
      font-size: 11px;
    }
    .log-time { color: var(--text-3); min-width: 70px; }
    .log-level {
      min-width: 50px;
      font-weight: 600;
      text-transform: uppercase;
    }
    .log-level.ok { color: var(--green); }
    .log-level.warn { color: var(--yellow); }
    .log-level.err { color: var(--red); }
    .log-level.info { color: var(--cyan); }
    .log-msg { color: var(--text-1); }
    .log-pair { color: var(--accent); min-width: 80px; }

    /* --- RESPONSIVE --- */
    @media (max-width: 1200px) {
      .kpi-strip { grid-template-columns: repeat(4, 1fr); }
      .dashboard { grid-template-columns: 1fr 1fr; }
      .positions-panel { grid-column: 1 / -1; }
      .system-panel { grid-column: 1 / -1; }
      .pairs-panel { grid-column: 1 / -1; }
      .risk-panel { grid-column: 1 / -1; }
      .alerts-panel { grid-column: 1 / -1; }
    }
    @media (max-width: 768px) {
      .kpi-strip { grid-template-columns: repeat(2, 1fr); }
      .topbar { flex-direction: column; gap: 8px; }
    }
  </style>
</head>
<body>

  <!-- TOP BAR -->
  <div class="topbar">
    <div class="topbar-left">
      <div class="logo">MULTI_ASSETS <span>| SPOT MONITOR</span></div>
      <div class="mode-badge live" id="mode-badge">
        <span class="status-dot ok" id="status-dot"></span>
        <span id="mode-text">LIVE</span>
      </div>
    </div>
    <div class="topbar-right">
      <span>CYCLE <span class="tick" id="loop-counter">--</span></span>
      <span>|</span>
      <span id="bot-status">--</span>
      <span>|</span>
      <span id="clock">--:--:--</span>
    </div>
  </div>

  <!-- BANNERS (outside grid for clean layout) -->
  <div class="disconnected-banner" id="disconnect-banner">
    BOT DISCONNECTED -- No heartbeat detected. Check PM2 process or start the bot.
  </div>
  <div class="halt-banner" id="halt-banner">
    EMERGENCY HALT ACTIVE
  </div>

  <!-- DASHBOARD -->
  <div class="dashboard" id="dashboard">

    <!-- KPI STRIP -->
    <div class="kpi-strip">
      <div class="kpi">
        <div class="kpi-label">USDC Balance</div>
        <div class="kpi-value accent" id="kpi-balance">--</div>
        <div class="kpi-sub" id="kpi-balance-sub">&nbsp;</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Daily P&amp;L</div>
        <div class="kpi-value" id="kpi-daily-pnl">--</div>
        <div class="kpi-sub" id="kpi-daily-pct">&nbsp;</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Cumulative P&amp;L</div>
        <div class="kpi-value" id="kpi-cumul-pnl">--</div>
        <div class="kpi-sub" id="kpi-cumul-trades">&nbsp;</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Positions</div>
        <div class="kpi-value accent" id="kpi-positions">--</div>
        <div class="kpi-sub" id="kpi-pairs-count">&nbsp;</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Daily Loss Limit</div>
        <div class="kpi-value" id="kpi-daily-loss">--</div>
        <div class="kpi-sub" id="kpi-daily-loss-sub">&nbsp;</div>
        <div class="progress-track"><div class="progress-fill" id="loss-progress" style="width:0%;background:var(--green)"></div></div>
      </div>
      <div class="kpi">
        <div class="kpi-label">OOS Blocked</div>
        <div class="kpi-value" id="kpi-oos">--</div>
        <div class="kpi-sub" id="kpi-oos-sub">&nbsp;</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Circuit Breaker</div>
        <div class="kpi-value" id="kpi-circuit">--</div>
        <div class="kpi-sub" id="kpi-errors">&nbsp;</div>
      </div>
    </div>

    <!-- OPEN POSITIONS TABLE -->
    <div class="panel positions-panel">
      <div class="panel-header">
        <div class="panel-title">Open Positions</div>
        <div class="panel-badge" id="pos-count">0</div>
      </div>
      <div id="positions-body">
        <div class="empty-state">No open positions</div>
      </div>
    </div>

    <!-- SYSTEM STATUS -->
    <div class="panel system-panel">
      <div class="panel-header">
        <div class="panel-title">System</div>
        <div class="panel-badge" id="sys-version">--</div>
      </div>
      <div id="system-rows">
        <div class="sys-row"><span class="sys-label">PID</span><span class="sys-value" id="sys-pid">--</span></div>
        <div class="sys-row"><span class="sys-label">Heartbeat</span><span class="sys-value" id="sys-heartbeat">--</span></div>
        <div class="sys-row"><span class="sys-label">Circuit Mode</span><span class="sys-value" id="sys-circuit">--</span></div>
        <div class="sys-row"><span class="sys-label">Error Count</span><span class="sys-value" id="sys-errors">--</span></div>
        <div class="sys-row"><span class="sys-label">Loop Counter</span><span class="sys-value" id="sys-loop">--</span></div>
        <div class="sys-row"><span class="sys-label">Taker Fee</span><span class="sys-value" id="sys-taker">--</span></div>
        <div class="sys-row"><span class="sys-label">Maker Fee</span><span class="sys-value" id="sys-maker">--</span></div>
        <div class="sys-row"><span class="sys-label">API Latency</span><span class="sys-value" id="sys-latency">--</span></div>
        <div class="sys-row"><span class="sys-label">Metrics Update</span><span class="sys-value" id="sys-metrics-ts">--</span></div>
        <div class="sys-row"><span class="sys-label">Last Refresh</span><span class="sys-value" id="sys-refresh">--</span></div>
      </div>
    </div>

    <!-- PAIRS OVERVIEW -->
    <div class="panel pairs-panel">
      <div class="panel-header">
        <div class="panel-title">Pairs Overview</div>
        <div class="panel-badge" id="pairs-total">0</div>
      </div>
      <div id="pairs-body">
        <div class="empty-state">No pairs configured</div>
      </div>
    </div>

    <!-- RISK & PERFORMANCE -->
    <div class="panel risk-panel">
      <div class="panel-header">
        <div class="panel-title">Risk &amp; Performance</div>
        <div class="panel-badge">live</div>
      </div>
      <div class="risk-grid" id="risk-grid">
        <div class="risk-item">
          <div class="risk-label">Cumul P&amp;L</div>
          <div class="risk-value" id="r-cumul">--</div>
        </div>
        <div class="risk-item">
          <div class="risk-label">Closed Trades</div>
          <div class="risk-value" id="r-trades">--</div>
        </div>
        <div class="risk-item">
          <div class="risk-label">Daily P&amp;L %</div>
          <div class="risk-value" id="r-daily-pct">--</div>
        </div>
        <div class="risk-item">
          <div class="risk-label">Starting Equity</div>
          <div class="risk-value" id="r-equity">--</div>
        </div>
        <div class="risk-item">
          <div class="risk-label">Emergency Halt</div>
          <div class="risk-value" id="r-halt">--</div>
        </div>
        <div class="risk-item">
          <div class="risk-label">OOS Blocked</div>
          <div class="risk-value" id="r-oos">--</div>
        </div>
        <div class="risk-item">
          <div class="risk-label">Taker Fee</div>
          <div class="risk-value" id="r-taker">--</div>
        </div>
        <div class="risk-item">
          <div class="risk-label">Unrealized P&amp;L</div>
          <div class="risk-value" id="r-unrealized">--</div>
        </div>
      </div>
    </div>

    <!-- ACTIVITY LOG -->
    <div class="panel alerts-panel">
      <div class="panel-header">
        <div class="panel-title">Activity Log</div>
        <div class="panel-badge" id="log-count">0</div>
      </div>
      <div id="log-body">
        <div class="empty-state">Waiting for data...</div>
      </div>
    </div>

  </div>

  <script>
    // ================================================================
    // MULTI_ASSETS Dashboard -- Data Engine
    // Polls /api/data every 5s via client-side fetch (no page reload)
    // ================================================================
    const POLL_MS = 5000;
    const LOG_MAX = 40;
    const DAILY_LOSS_LIMIT_PCT = 5.0;
    const logEntries = [];
    let consecutiveErrors = 0;

    function $(id) { return document.getElementById(id); }

    function fmtUsdc(v) {
      if (v === null || v === undefined) return '--';
      const n = Number(v);
      const sign = n > 0 ? '+' : '';
      return sign + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' USDC';
    }
    function fmtUsdcShort(v) {
      if (v === null || v === undefined) return '--';
      return Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    function fmtPct(v) {
      if (v === null || v === undefined) return '--';
      const n = Number(v);
      const sign = n > 0 ? '+' : '';
      return sign + n.toFixed(2) + '%';
    }
    function fmtNum(v, d) {
      if (v === null || v === undefined) return '--';
      return Number(v).toFixed(d !== undefined ? d : 4);
    }
    function fmtAge(s) {
      if (!s && s !== 0) return '--';
      if (s >= 999999) return 'NEVER';
      if (s < 60) return s + 's ago';
      if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's ago';
      return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm ago';
    }
    function pnlClass(v) { return v >= 0 ? 'positive' : 'negative'; }
    function escapeHtml(s) {
      if (!s) return '';
      const d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML;
    }

    let _lastAlive = null;
    let _lastPosCount = null;
    let _lastBalance = null;
    let _lastHalt = null;

    function addLog(level, msg, pair) {
      const now = new Date();
      const ts = now.toLocaleTimeString('en-US', { hour12: false });
      logEntries.unshift({ ts, level, msg, pair: pair || '' });
      if (logEntries.length > LOG_MAX) logEntries.length = LOG_MAX;
    }

    function renderLog() {
      const el = $('log-body');
      if (logEntries.length === 0) {
        el.innerHTML = '<div class="empty-state">Waiting for data...</div>';
        return;
      }
      let html = '';
      for (const e of logEntries) {
        const cls = e.level === 'OK' ? 'ok' : e.level === 'WARN' ? 'warn' : e.level === 'ERR' ? 'err' : 'info';
        html += '<div class="log-line">'
          + '<span class="log-time">' + escapeHtml(e.ts) + '</span>'
          + '<span class="log-level ' + cls + '">' + escapeHtml(e.level) + '</span>'
          + (e.pair ? '<span class="log-pair">' + escapeHtml(e.pair) + '</span>' : '')
          + '<span class="log-msg">' + escapeHtml(e.msg) + '</span>'
          + '</div>';
      }
      el.innerHTML = html;
      $('log-count').textContent = logEntries.length;
    }

    function renderPositions(pairs) {
      const el = $('positions-body');
      const openPairs = Object.entries(pairs).filter(function(kv) { return kv[1].in_position; });
      $('pos-count').textContent = openPairs.length;

      if (openPairs.length === 0) {
        el.innerHTML = '<div class="empty-state">No open positions</div>';
        return;
      }

      let html = '<table class="data-table"><thead><tr>'
        + '<th>Pair</th>'
        + '<th class="right">Entry</th>'
        + '<th class="right">Current</th>'
        + '<th class="right">Qty</th>'
        + '<th class="right">P&amp;L</th>'
        + '<th class="right">P&amp;L %</th>'
        + '<th class="right">Stop-Loss</th>'
        + '<th class="right">SL Dist</th>'
        + '<th>SL</th>'
        + '<th>Trail</th>'
        + '<th>Scenario</th>'
        + '<th class="right">Age</th>'
        + '</tr></thead><tbody>';

      for (let i = 0; i < openPairs.length; i++) {
        const sym = openPairs[i][0];
        const p = openPairs[i][1];
        const pnl = p.unrealized_pnl || 0;
        const pnlPct = p.unrealized_pct || 0;
        const cls = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
        const slCls = p.sl_placed ? 'sl-ok' : 'sl-missing';
        const slText = p.sl_placed ? '&#10003;' : '&#10007; MISSING';
        const trailText = p.trailing_active ? '<span class="trailing-on">ACTIVE</span>' : '--';

        let ageStr = '--';
        if (p.buy_timestamp) {
          const secs = Math.floor(Date.now()/1000 - p.buy_timestamp);
          if (secs < 3600) ageStr = Math.floor(secs/60) + 'm';
          else if (secs < 86400) ageStr = Math.floor(secs/3600) + 'h ' + Math.floor((secs%3600)/60) + 'm';
          else ageStr = Math.floor(secs/86400) + 'd ' + Math.floor((secs%86400)/3600) + 'h';
        }

        let flags = '';
        if (p.breakeven_triggered) flags += '<span style="color:var(--cyan);margin-left:4px" title="Break-even triggered">BE</span>';
        if (p.partial_taken_1) flags += '<span style="color:var(--purple);margin-left:4px" title="Partial 1 taken">P1</span>';
        if (p.partial_taken_2) flags += '<span style="color:var(--purple);margin-left:4px" title="Partial 2 taken">P2</span>';

        html += '<tr>'
          + '<td style="color:var(--accent);font-weight:600">' + escapeHtml(p.real_pair) + flags + '</td>'
          + '<td class="right">' + fmtNum(p.entry_price) + '</td>'
          + '<td class="right">' + fmtNum(p.spot_price) + '</td>'
          + '<td class="right">' + fmtNum(p.qty, 6) + '</td>'
          + '<td class="right ' + cls + '">' + fmtUsdc(pnl) + '</td>'
          + '<td class="right ' + cls + '">' + fmtPct(pnlPct) + '</td>'
          + '<td class="right">' + fmtNum(p.stop_loss) + '</td>'
          + '<td class="right" style="color:var(--yellow)">' + (p.sl_dist_pct !== null && p.sl_dist_pct !== undefined ? fmtPct(-p.sl_dist_pct) : '--') + '</td>'
          + '<td class="' + slCls + '">' + slText + '</td>'
          + '<td>' + trailText + '</td>'
          + '<td>' + escapeHtml((p.scenario || '--') + ' / ' + (p.timeframe || '--')) + '</td>'
          + '<td class="right">' + ageStr + '</td>'
          + '</tr>';
      }
      html += '</tbody></table>';
      el.innerHTML = html;
    }

    function renderPairs(pairs) {
      const el = $('pairs-body');
      const entries = Object.entries(pairs);
      $('pairs-total').textContent = entries.length;

      if (entries.length === 0) {
        el.innerHTML = '<div class="empty-state">No pairs configured</div>';
        return;
      }

      let html = '<table class="pairs-table"><thead><tr>'
        + '<th>Pair</th>'
        + '<th>Status</th>'
        + '<th>Price</th>'
        + '<th>Last Cycle</th>'
        + '<th>Scenario</th>'
        + '<th>Executions</th>'
        + '</tr></thead><tbody>';

      for (let i = 0; i < entries.length; i++) {
        const sym = entries[i][0];
        const p = entries[i][1];
        let statusBadge;
        if (p.in_position) statusBadge = '<span class="status-badge long">&#9679; LONG</span>';
        else if (p.oos_blocked) statusBadge = '<span class="status-badge oos">OOS BLOCKED</span>';
        else if (p.drawdown_halted) statusBadge = '<span class="status-badge halted">DD HALT</span>';
        else statusBadge = '<span class="status-badge waiting">WAITING</span>';

        let lastExec = '--';
        if (p.last_execution) {
          try {
            const dt = new Date(p.last_execution);
            lastExec = dt.toLocaleString('en-GB', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit', hour12: false });
          } catch(e) { lastExec = p.last_execution; }
        }

        html += '<tr>'
          + '<td style="color:var(--accent)">' + escapeHtml(p.real_pair) + '</td>'
          + '<td>' + statusBadge + '</td>'
          + '<td style="font-family:var(--font-mono)">' + fmtNum(p.spot_price) + '</td>'
          + '<td>' + lastExec + '</td>'
          + '<td>' + escapeHtml(p.scenario || '--') + '</td>'
          + '<td style="font-family:var(--font-mono)">' + (p.execution_count || 0) + '</td>'
          + '</tr>';
      }
      html += '</tbody></table>';
      el.innerHTML = html;
    }

    function updateClock() {
      const now = new Date();
      $('clock').textContent = now.toLocaleTimeString('en-US', { hour12: false });
    }

    async function refresh() {
      try {
        const r = await fetch('/api/data');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        const d = await r.json();
        consecutiveErrors = 0;

        const alive = d.alive;
        const banner = $('disconnect-banner');
        const haltBanner = $('halt-banner');
        const badge = $('mode-badge');
        const dot = $('status-dot');

        // -- Connection state --
        if (!alive) {
          banner.classList.add('visible');
          badge.className = 'mode-badge disconnected';
          dot.className = 'status-dot err';
          $('mode-text').textContent = 'DISCONNECTED';
          $('bot-status').textContent = 'NO HEARTBEAT';
          if (_lastAlive !== false) {
            addLog('ERR', 'Bot heartbeat lost -- ' + fmtAge(d.age_seconds));
            _lastAlive = false;
          }
          renderLog();
          return;
        } else {
          banner.classList.remove('visible');
          if (_lastAlive === false) {
            addLog('OK', 'Bot reconnected -- heartbeat restored');
          }
          _lastAlive = true;
        }

        // -- Halt state --
        if (d.emergency_halt) {
          haltBanner.classList.add('visible');
          haltBanner.textContent = 'EMERGENCY HALT ACTIVE' + (d.halt_reason ? ' -- ' + d.halt_reason : '');
          badge.className = 'mode-badge halted';
          dot.className = 'status-dot err';
          $('mode-text').textContent = 'HALTED';
          $('bot-status').textContent = 'EMERGENCY HALT';
          if (_lastHalt !== true) {
            addLog('ERR', 'Emergency halt activated' + (d.halt_reason ? ': ' + d.halt_reason : ''));
            _lastHalt = true;
          }
        } else {
          haltBanner.classList.remove('visible');
          const circuitMode = (d.circuit_mode || 'default').toUpperCase();
          const isNominal = circuitMode === 'DEFAULT' || circuitMode === 'RUNNING';
          if (circuitMode === 'PAUSED' || circuitMode === 'ALERT' || circuitMode === 'UNKNOWN') {
            badge.className = 'mode-badge paused';
            dot.className = 'status-dot warn';
            $('mode-text').textContent = circuitMode;
          } else {
            badge.className = 'mode-badge live';
            dot.className = 'status-dot ok';
            $('mode-text').textContent = isNominal ? 'LIVE' : circuitMode;
          }
          $('bot-status').textContent = isNominal ? 'RUNNING' : circuitMode;
          if (_lastHalt === true) {
            addLog('OK', 'Emergency halt cleared');
            _lastHalt = false;
          }
        }

        // -- KPIs --
        const balance = d.usdc_balance;
        $('kpi-balance').textContent = balance !== null ? fmtUsdcShort(balance) : '--';
        $('kpi-balance-sub').textContent = d.starting_equity ? 'start: ' + fmtUsdcShort(d.starting_equity) : 'spot free balance';

        const dailyPnl = d.daily_pnl || 0;
        $('kpi-daily-pnl').textContent = fmtUsdc(dailyPnl);
        $('kpi-daily-pnl').className = 'kpi-value ' + pnlClass(dailyPnl);
        $('kpi-daily-pct').textContent = d.daily_pnl_pct ? fmtPct(d.daily_pnl_pct) : '';

        const cumulPnl = d.cumul_pnl || 0;
        $('kpi-cumul-pnl').textContent = fmtUsdc(cumulPnl);
        $('kpi-cumul-pnl').className = 'kpi-value ' + pnlClass(cumulPnl);
        $('kpi-cumul-trades').textContent = d.trade_count + ' closed trade' + (d.trade_count !== 1 ? 's' : '');

        $('kpi-positions').textContent = d.open_count + ' / ' + d.total_pairs;
        $('kpi-pairs-count').textContent = d.open_count + ' open, ' + d.total_pairs + ' monitored';

        // Daily loss limit
        const lossUsed = Math.abs(Math.min(dailyPnl, 0));
        const startEq = d.starting_equity || d.usdc_balance || 10000;
        const lossLimit = startEq * DAILY_LOSS_LIMIT_PCT / 100;
        const lossPct = lossLimit > 0 ? (lossUsed / lossLimit * 100) : 0;
        $('kpi-daily-loss').textContent = fmtUsdcShort(lossUsed) + ' / ' + fmtUsdcShort(lossLimit);
        $('kpi-daily-loss').className = 'kpi-value ' + (lossPct > 80 ? 'negative' : lossPct > 50 ? 'warn' : 'positive');
        $('kpi-daily-loss-sub').textContent = fmtPct(lossPct) + ' of 5% limit used';
        const bar = $('loss-progress');
        bar.style.width = Math.min(lossPct, 100) + '%';
        bar.style.background = lossPct > 80 ? 'var(--red)' : lossPct > 50 ? 'var(--yellow)' : 'var(--green)';

        $('kpi-oos').textContent = d.oos_blocked;
        $('kpi-oos').className = 'kpi-value ' + (d.oos_blocked > 0 ? 'warn' : 'positive');
        $('kpi-oos-sub').textContent = d.oos_blocked > 0 ? d.oos_blocked + ' pair(s) blocked' : 'all pairs clear';

        const circuitText = (d.circuit_mode || 'unknown').toUpperCase();
        $('kpi-circuit').textContent = circuitText;
        const isCircuitOk = circuitText === 'DEFAULT' || circuitText === 'RUNNING';
        $('kpi-circuit').className = 'kpi-value ' + (isCircuitOk ? 'positive' : circuitText === 'PAUSED' ? 'warn' : 'negative');
        $('kpi-errors').textContent = d.error_count + ' error' + (d.error_count !== 1 ? 's' : '');

        // -- Topbar --
        $('loop-counter').textContent = '#' + (d.loop_counter || '--');

        // -- Positions --
        renderPositions(d.pairs || {});

        // -- System --
        $('sys-version').textContent = d.bot_version || '--';
        $('sys-pid').textContent = d.pid || '--';
        $('sys-heartbeat').textContent = fmtAge(d.age_seconds);
        $('sys-heartbeat').style.color = d.age_seconds < 120 ? 'var(--green)' : d.age_seconds < 300 ? 'var(--yellow)' : 'var(--red)';
        $('sys-circuit').textContent = circuitText;
        $('sys-circuit').style.color = isCircuitOk ? 'var(--green)' : 'var(--yellow)';
        $('sys-errors').textContent = d.error_count;
        $('sys-errors').style.color = d.error_count > 0 ? 'var(--red)' : 'var(--text-1)';
        $('sys-loop').textContent = '#' + (d.loop_counter || 0);
        $('sys-taker').textContent = d.taker_fee ? (d.taker_fee * 100).toFixed(2) + '%' : '--';
        $('sys-maker').textContent = d.maker_fee ? (d.maker_fee * 100).toFixed(2) + '%' : '--';
        $('sys-latency').textContent = d.api_latency_ms ? d.api_latency_ms + 'ms' : '--';
        $('sys-metrics-ts').textContent = d.metrics_ts ? new Date(d.metrics_ts).toLocaleTimeString('en-US', {hour12:false}) : '--';
        $('sys-refresh').textContent = new Date().toLocaleTimeString('en-US', {hour12:false});

        // -- Pairs overview --
        renderPairs(d.pairs || {});

        // -- Risk panel --
        $('r-cumul').textContent = fmtUsdc(cumulPnl);
        $('r-cumul').style.color = cumulPnl >= 0 ? 'var(--green)' : 'var(--red)';
        $('r-trades').textContent = d.trade_count || 0;
        $('r-daily-pct').textContent = d.daily_pnl_pct ? fmtPct(d.daily_pnl_pct) : '--';
        $('r-daily-pct').style.color = (d.daily_pnl_pct || 0) >= 0 ? 'var(--green)' : 'var(--red)';
        $('r-equity').textContent = d.starting_equity ? fmtUsdcShort(d.starting_equity) : '--';
        $('r-halt').textContent = d.emergency_halt ? 'YES' : 'NO';
        $('r-halt').style.color = d.emergency_halt ? 'var(--red)' : 'var(--green)';
        $('r-oos').textContent = d.oos_blocked + ' / ' + d.total_pairs;
        $('r-oos').style.color = d.oos_blocked > 0 ? 'var(--yellow)' : 'var(--green)';
        $('r-taker').textContent = d.taker_fee ? (d.taker_fee * 10000).toFixed(1) + ' bps' : '--';

        // Unrealized P&L total
        let totalUnrealized = 0;
        const pairEntries = Object.entries(d.pairs || {});
        for (let i = 0; i < pairEntries.length; i++) {
          const p = pairEntries[i][1];
          if (p.in_position && p.unrealized_pnl !== null && p.unrealized_pnl !== undefined) {
            totalUnrealized += p.unrealized_pnl;
          }
        }
        $('r-unrealized').textContent = fmtUsdc(totalUnrealized);
        $('r-unrealized').style.color = totalUnrealized >= 0 ? 'var(--green)' : 'var(--red)';

        // -- Activity log from recent trades --
        if (d.recent_trades && d.recent_trades.length > 0 && logEntries.length === 0) {
          for (let i = Math.min(d.recent_trades.length, 15) - 1; i >= 0; i--) {
            const t = d.recent_trades[i];
            const side = (t.side || '').toUpperCase();
            const pnlStr = t.pnl !== undefined && t.pnl !== null ? ' | P&L: ' + fmtUsdc(t.pnl) : '';
            const msg = side + ' ' + fmtNum(t.qty, 6) + ' @ ' + fmtNum(t.price) + pnlStr;
            const level = side === 'SELL' ? (t.pnl >= 0 ? 'OK' : 'WARN') : 'INFO';
            const entry = {
              ts: t.ts ? new Date(t.ts).toLocaleTimeString('en-US', {hour12:false}) : '--',
              level: level,
              msg: msg,
              pair: t.pair || ''
            };
            logEntries.push(entry);
          }
        }

        // Log state changes
        const posCount = d.open_count || 0;
        if (_lastPosCount !== null && posCount !== _lastPosCount) {
          const verb = posCount > _lastPosCount ? 'opened' : 'closed';
          addLog('OK', 'Position ' + verb + ' | Active: ' + posCount);
        }
        if (_lastBalance !== null && balance !== null && Math.abs(balance - _lastBalance) > 0.01) {
          const delta = balance - _lastBalance;
          addLog(delta >= 0 ? 'OK' : 'WARN', 'Balance ' + (delta >= 0 ? '+' : '') + fmtUsdcShort(delta) + ' -> ' + fmtUsdcShort(balance));
        }
        _lastPosCount = posCount;
        _lastBalance = balance;

        renderLog();

      } catch (e) {
        consecutiveErrors++;
        addLog('ERR', 'Fetch error: ' + e.message);
        renderLog();
        if (consecutiveErrors >= 3) {
          $('disconnect-banner').classList.add('visible');
          $('status-dot').className = 'status-dot err';
        }
      }
    }

    // Boot
    updateClock();
    setInterval(updateClock, 1000);
    refresh();
    setInterval(refresh, POLL_MS);
  </script>
</body>
</html>"""


# --- HTTP handler --------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/", "/dashboard"):
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/data":
            data = collect_data()
            body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def log_message(self, fmt: str, *args: object) -> None:
        pass


# --- entry point ---------------------------------------------------------

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), DashboardHandler)
    print(f"[DASHBOARD] Server started -> http://127.0.0.1:{PORT}/dashboard")
    print("[DASHBOARD] Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[DASHBOARD] Stopped.")
        sys.exit(0)
