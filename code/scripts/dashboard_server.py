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
import threading
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
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

        sl = ps.get("stop_loss") or ps.get("stop_loss_at_entry")
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
  <meta name="color-scheme" content="dark">
  <meta name="darkreader-lock">
  <title>MULTI_ASSETS | Spot Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500;600;700&display=optional" rel="stylesheet">
  <script>
    // Purge any residual service workers
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.getRegistrations().then(function(r) {
        r.forEach(function(sw) { sw.unregister(); });
      });
    }
  </script>
  <style>
    /* ==========================================================
       CARBON g100 Dark Theme — AlphaEdge Navy Palette
       IBM Carbon Design System patterns + AlphaEdge color base
       ========================================================== */
    :root {
      /* ── Background layers (AlphaEdge navy mapped to Carbon g100) ── */
      --cds-background:       #06080d;
      --cds-layer-01:         #0c1017;
      --cds-layer-02:         #111820;
      --cds-layer-03:         #19212d;
      --cds-layer-hover-01:   #101824;
      --cds-layer-hover-02:   #162030;
      --cds-layer-active:     #1e2a3a;
      --cds-layer-selected:   #152030;
      /* ── Borders ── */
      --cds-border-subtle:    #1e2a3a;
      --cds-border-strong:    #2a3a52;
      --cds-border-interactive:#3b82f6;
      --cds-border-disabled:  rgba(110,130,160,0.2);
      /* ── Text (Carbon g100 luminance steps) ── */
      --cds-text-primary:     #e8edf5;
      --cds-text-secondary:   #a4b1c7;
      --cds-text-helper:      #6b7a94;
      --cds-text-placeholder: #3f4f66;
      --cds-text-on-color:    #ffffff;
      --cds-text-inverse:     #06080d;
      /* ── Interactive / Accent ── */
      --cds-interactive:      #3b82f6;
      --cds-link-primary:     #78a9ff;
      --cds-focus:            #ffffff;
      /* ── Support / Semantic ── */
      --cds-support-success:  #10b981;
      --cds-support-error:    #ef4444;
      --cds-support-warning:  #f59e0b;
      --cds-support-info:     #06b6d4;
      /* ── Extended semantic ── */
      --green:     #10b981;  --green-dim:  #064e3b;
      --red:       #ef4444;  --red-dim:    #4c1414;
      --yellow:    #f59e0b;  --yellow-dim: #4a3508;
      --cyan:      #06b6d4;  --purple:     #8b5cf6;
      /* ── Typography (IBM Plex) ── */
      --cds-body-01:    14px/1.43 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      --cds-label-01:   12px/1.34 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      --cds-helper-01:  12px/1.34 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      --cds-code-01:    12px/1.34 'IBM Plex Mono', 'SF Mono', 'Cascadia Code', Consolas, monospace;
      --cds-code-02:    14px/1.43 'IBM Plex Mono', 'SF Mono', 'Cascadia Code', Consolas, monospace;
      --font-sans: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      --font-mono: 'IBM Plex Mono', 'SF Mono', 'Cascadia Code', Consolas, monospace;
      /* ── Spacing (Carbon 2x grid) ── */
      --cds-spacing-03: 8px;   --cds-spacing-04: 12px;
      --cds-spacing-05: 16px;  --cds-spacing-06: 24px;
      --cds-spacing-07: 32px;  --cds-spacing-08: 40px;
      --cds-spacing-09: 48px;
      /* ── Motion ── */
      --cds-ease: cubic-bezier(0.25, 0.1, 0.25, 1);
      --cds-duration-fast-01: 70ms;
      --cds-duration-fast-02: 110ms;
      --cds-duration-moderate-01: 150ms;
      --cds-duration-moderate-02: 240ms;
    }

    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html { height: 100%; }
    body {
      font: var(--cds-body-01);
      background: var(--cds-background);
      color: var(--cds-text-primary);
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      height: 100%;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
    }
    ::selection { background: var(--cds-interactive); color: var(--cds-text-on-color); }

    /* ── SCROLLBAR (Carbon-style thin) ── */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--cds-border-strong); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--cds-text-helper); }

    /* ═══════════════════════════════════════════
       CARBON UI SHELL — Header (48px)
       ═══════════════════════════════════════════ */
    .cds--header {
      height: 48px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 var(--cds-spacing-05);
      background: var(--cds-layer-01);
      border-bottom: 1px solid var(--cds-border-subtle);
      flex-shrink: 0;
      z-index: 8000;
    }
    .cds--header__left {
      display: flex;
      align-items: center;
      gap: var(--cds-spacing-05);
      height: 100%;
    }
    .cds--header__prefix {
      display: flex;
      align-items: center;
      height: 100%;
      padding-right: var(--cds-spacing-05);
      border-right: 1px solid var(--cds-border-subtle);
      font-family: var(--font-mono);
      font-size: 14px;
      font-weight: 600;
      letter-spacing: 1.5px;
      text-transform: uppercase;
      color: var(--cds-interactive);
    }
    .cds--header__name {
      font-size: 14px;
      font-weight: 400;
      color: var(--cds-text-secondary);
      letter-spacing: 0.16px;
    }
    .cds--header__right {
      display: flex;
      align-items: center;
      gap: var(--cds-spacing-05);
      font: var(--cds-code-01);
      color: var(--cds-text-helper);
    }
    .cds--header__right .sep { color: var(--cds-border-subtle); }
    .cds--header__right .val { color: var(--cds-text-secondary); }

    /* ═══════════════════════════════════════════
       CARBON TAGS — Status badge in header
       ═══════════════════════════════════════════ */
    .cds--tag {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      height: 24px;
      padding: 0 8px;
      font: var(--cds-label-01);
      font-weight: 600;
      font-family: var(--font-mono);
      text-transform: uppercase;
      letter-spacing: 0.32px;
      white-space: nowrap;
      border-radius: 100px;
    }
    .cds--tag--green  { background: var(--green-dim);  color: var(--green);  border: 1px solid rgba(16,185,129,0.3); }
    .cds--tag--red    { background: var(--red-dim);    color: var(--red);    border: 1px solid rgba(239,68,68,0.3); }
    .cds--tag--yellow { background: var(--yellow-dim); color: var(--yellow); border: 1px solid rgba(245,158,11,0.3); }

    .status-dot {
      width: 8px; height: 8px; border-radius: 50%;
      display: inline-block; flex-shrink: 0;
    }
    .status-dot.ok   { background: var(--green);  box-shadow: 0 0 8px var(--green); }
    .status-dot.warn { background: var(--yellow); box-shadow: 0 0 8px var(--yellow); }
    .status-dot.err  { background: var(--red);    box-shadow: 0 0 8px var(--red); animation: pulse-dot 1.5s ease infinite; }
    @keyframes pulse-dot { 0%,100%{opacity:1} 50%{opacity:0.3} }

    /* ═══════════════════════════════════════════
       CARBON INLINE NOTIFICATIONS — Banners
       ═══════════════════════════════════════════ */
    .cds--inline-notification {
      display: none;
      align-items: center;
      gap: var(--cds-spacing-04);
      padding: var(--cds-spacing-04) var(--cds-spacing-05);
      font: var(--cds-body-01);
      font-weight: 500;
      letter-spacing: 0.16px;
      border-left: 3px solid;
      flex-shrink: 0;
    }
    .cds--inline-notification.visible { display: flex; }
    .cds--inline-notification--error {
      background: var(--red-dim);
      border-color: var(--red);
      color: var(--red);
    }
    .cds--inline-notification__icon { font-size: 20px; flex-shrink: 0; line-height: 1; }

    /* ═══════════════════════════════════════════
       MAIN CONTENT — Carbon flex column
       ═══════════════════════════════════════════ */
    .cds--content {
      flex: 1;
      overflow: auto;
      padding: var(--cds-spacing-05);
      display: flex;
      flex-direction: column;
      gap: var(--cds-spacing-05);
    }

    /* ═══════════════════════════════════════════
       CARBON KPI STRIP — Metric tiles row
       ═══════════════════════════════════════════ */
    .cds--kpi-strip {
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
      gap: 1px;
      background: var(--cds-border-subtle);
      border-radius: 0;
      flex-shrink: 0;
      min-height: 70px;
    }
    .cds--kpi {
      background: var(--cds-layer-01);
      padding: var(--cds-spacing-05);
      display: flex;
      flex-direction: column;
      gap: 4px;
      min-width: 0;
      border-top: 3px solid transparent;
      transition: border-color var(--cds-duration-fast-02) var(--cds-ease),
                  background var(--cds-duration-fast-02) var(--cds-ease);
    }
    .cds--kpi:hover { border-top-color: var(--cds-interactive); background: var(--cds-layer-hover-01); }
    .cds--kpi__label {
      font: var(--cds-label-01);
      font-weight: 500;
      letter-spacing: 0.32px;
      text-transform: uppercase;
      color: var(--cds-text-helper);
    }
    .kpi-value {
      font-family: var(--font-mono);
      font-size: 20px;
      font-weight: 600;
      color: var(--cds-text-primary);
      line-height: 1.3;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .kpi-sub {
      font: var(--cds-helper-01);
      color: var(--cds-text-placeholder);
      letter-spacing: 0.32px;
    }
    .kpi-value.positive { color: var(--green); }
    .kpi-value.negative { color: var(--red); }
    .kpi-value.warn     { color: var(--yellow); }
    .kpi-value.accent   { color: var(--cds-interactive); }

    /* Progress bar (Carbon-style) */
    .cds--progress-bar__track {
      height: 4px;
      background: var(--cds-layer-03);
      overflow: hidden;
      margin-top: 6px;
    }
    .cds--progress-bar__fill {
      height: 100%;
      transition: width 0.4s var(--cds-ease);
    }

    /* ═══════════════════════════════════════════
       GRID ROWS — 2-column layout system
       ═══════════════════════════════════════════ */
    .cds--grid-row { display: grid; gap: var(--cds-spacing-05); }
    .cds--grid-row--top    { grid-template-columns: 3fr 1fr; }
    .cds--grid-row--bottom { grid-template-columns: 1fr 1fr; }

    /* ═══════════════════════════════════════════
       CARBON TILE — Container component
       ═══════════════════════════════════════════ */
    .cds--tile {
      background: var(--cds-layer-01);
      border: 1px solid var(--cds-border-subtle);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      min-height: 0;
    }
    .cds--tile__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--cds-spacing-04) var(--cds-spacing-05);
      border-bottom: 1px solid var(--cds-border-subtle);
      flex-shrink: 0;
    }
    .cds--tile__title {
      font-size: 14px;
      font-weight: 600;
      letter-spacing: 0.16px;
      text-transform: uppercase;
      color: var(--cds-text-secondary);
    }
    .cds--tile__badge {
      font: var(--cds-code-01);
      font-weight: 600;
      padding: 2px 10px;
      background: var(--cds-layer-03);
      color: var(--cds-link-primary);
      border-radius: 100px;
      letter-spacing: 0.32px;
    }
    .cds--tile__body { padding: var(--cds-spacing-05); flex: 1; overflow: auto; }
    .cds--tile__body--flush { padding: 0; }

    /* ═══════════════════════════════════════════
       CARBON DATA TABLE
       ═══════════════════════════════════════════ */
    .cds--data-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }
    .cds--data-table th {
      text-align: left;
      padding: 10px var(--cds-spacing-05);
      font: var(--cds-label-01);
      font-weight: 600;
      letter-spacing: 0.32px;
      color: var(--cds-text-helper);
      background: var(--cds-layer-02);
      border-bottom: 1px solid var(--cds-border-subtle);
      text-transform: uppercase;
      white-space: nowrap;
    }
    .cds--data-table th.right, .cds--data-table td.right { text-align: right; }
    .cds--data-table td {
      padding: 8px var(--cds-spacing-05);
      border-bottom: 1px solid var(--cds-border-subtle);
      color: var(--cds-text-secondary);
      font-family: var(--font-mono);
      font-size: 13px;
      white-space: nowrap;
      transition: background var(--cds-duration-fast-01) var(--cds-ease);
    }
    .cds--data-table tbody tr:hover td { background: var(--cds-layer-hover-01); }
    .pnl-pos { color: var(--green) !important; }
    .pnl-neg { color: var(--red)   !important; }
    .sl-ok      { color: var(--green); }
    .sl-missing { color: var(--red); font-weight: 600; }
    .trailing-on { color: var(--cyan); }

    .empty-state {
      display: flex;
      align-items: center;
      justify-content: center;
      padding: var(--cds-spacing-09) var(--cds-spacing-05);
      color: var(--cds-text-placeholder);
      font-size: 14px;
      min-height: 80px;
    }

    /* ═══════════════════════════════════════════
       STATUS BADGES — Operational tags
       ═══════════════════════════════════════════ */
    .status-badge {
      display: inline-flex;
      align-items: center;
      height: 20px;
      padding: 0 8px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.32px;
      white-space: nowrap;
      border-radius: 100px;
    }
    .status-badge.long    { background: var(--green-dim);  color: var(--green); }
    .status-badge.waiting { background: var(--cds-layer-03); color: var(--cds-text-helper); }
    .status-badge.oos     { background: var(--red-dim);    color: var(--red); }
    .status-badge.halted  { background: var(--yellow-dim); color: var(--yellow); }

    /* ═══════════════════════════════════════════
       CARBON STRUCTURED LIST — System panel
       ═══════════════════════════════════════════ */
    .cds--structured-list { width: 100%; }
    .cds--structured-list__row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 9px 0;
      border-bottom: 1px solid var(--cds-border-subtle);
    }
    .cds--structured-list__row:last-child { border-bottom: none; }
    .cds--structured-list__label {
      font: var(--cds-body-01);
      color: var(--cds-text-helper);
    }
    .cds--structured-list__value {
      font-family: var(--font-mono);
      font-size: 14px;
      font-weight: 500;
      color: var(--cds-text-primary);
    }

    /* ═══════════════════════════════════════════
       RISK GRID — 2x4 metric tiles
       ═══════════════════════════════════════════ */
    .cds--risk-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1px;
      background: var(--cds-border-subtle);
    }
    .cds--risk-item {
      background: var(--cds-layer-01);
      padding: var(--cds-spacing-05);
    }
    .cds--risk-label {
      font: var(--cds-label-01);
      letter-spacing: 0.32px;
      color: var(--cds-text-placeholder);
      margin-bottom: 4px;
      text-transform: uppercase;
    }
    .cds--risk-value {
      font-family: var(--font-mono);
      font-size: 16px;
      font-weight: 600;
      color: var(--cds-text-primary);
    }

    /* ═══════════════════════════════════════════
       ACTIVITY LOG — Monospace event stream
       ═══════════════════════════════════════════ */
    .log-line {
      display: flex;
      gap: var(--cds-spacing-05);
      padding: 6px var(--cds-spacing-05);
      border-bottom: 1px solid var(--cds-border-subtle);
      font: var(--cds-code-01);
      transition: background var(--cds-duration-fast-01) var(--cds-ease);
    }
    .log-line:hover { background: var(--cds-layer-hover-01); }
    .log-time { color: var(--cds-text-placeholder); min-width: 75px; }
    .log-level {
      min-width: 48px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.32px;
    }
    .log-level.ok   { color: var(--green); }
    .log-level.warn { color: var(--yellow); }
    .log-level.err  { color: var(--red); }
    .log-level.info { color: var(--cyan); }
    .log-pair { color: var(--cds-link-primary); min-width: 85px; }
    .log-msg  { color: var(--cds-text-secondary); }

    /* ═══════════════════════════════════════════
       RESPONSIVE — Carbon breakpoints
       ═══════════════════════════════════════════ */
    @media (max-width: 1200px) {
      .cds--kpi-strip        { grid-template-columns: repeat(4, minmax(0, 1fr)); }
      .cds--grid-row--top    { grid-template-columns: 1fr; }
      .cds--grid-row--bottom { grid-template-columns: 1fr; }
    }
    @media (max-width: 768px) {
      .cds--kpi-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .cds--header { flex-direction: column; height: auto; padding: 8px var(--cds-spacing-05); gap: 8px; }
    }
  </style>
</head>
<body>

  <!-- ═══ CARBON UI SHELL HEADER ═══ -->
  <header class="cds--header">
    <div class="cds--header__left">
      <div class="cds--header__prefix">MULTI_ASSETS</div>
      <span class="cds--header__name">Spot Monitor</span>
      <div class="cds--tag cds--tag--green" id="mode-badge">
        <span class="status-dot ok" id="status-dot"></span>
        <span id="mode-text">LIVE</span>
      </div>
    </div>
    <div class="cds--header__right">
      <span>Cycle <span class="val" id="loop-counter">--</span></span>
      <span class="sep">|</span>
      <span class="val" id="bot-status">--</span>
      <span class="sep">|</span>
      <span class="val" id="clock">--:--:--</span>
    </div>
  </header>

  <!-- ═══ CARBON INLINE NOTIFICATIONS ═══ -->
  <div class="cds--inline-notification cds--inline-notification--error" id="disconnect-banner">
    <span class="cds--inline-notification__icon">&#9888;</span>
    Bot disconnected &mdash; no heartbeat detected. Check PM2 process.
  </div>
  <div class="cds--inline-notification cds--inline-notification--error" id="halt-banner">
    <span class="cds--inline-notification__icon">&#9888;</span>
    EMERGENCY HALT ACTIVE
  </div>

  <!-- ═══ MAIN CONTENT ═══ -->
  <main class="cds--content">

    <!-- KPI STRIP -->
    <div class="cds--kpi-strip">
      <div class="cds--kpi">
        <div class="cds--kpi__label">USDC Balance</div>
        <div class="kpi-value accent" id="kpi-balance">--</div>
        <div class="kpi-sub" id="kpi-balance-sub">&nbsp;</div>
      </div>
      <div class="cds--kpi">
        <div class="cds--kpi__label">Daily P&amp;L</div>
        <div class="kpi-value" id="kpi-daily-pnl">--</div>
        <div class="kpi-sub" id="kpi-daily-pct">&nbsp;</div>
      </div>
      <div class="cds--kpi">
        <div class="cds--kpi__label">Cumulative P&amp;L</div>
        <div class="kpi-value" id="kpi-cumul-pnl">--</div>
        <div class="kpi-sub" id="kpi-cumul-trades">&nbsp;</div>
      </div>
      <div class="cds--kpi">
        <div class="cds--kpi__label">Positions</div>
        <div class="kpi-value accent" id="kpi-positions">--</div>
        <div class="kpi-sub" id="kpi-pairs-count">&nbsp;</div>
      </div>
      <div class="cds--kpi">
        <div class="cds--kpi__label">Daily Loss Limit</div>
        <div class="kpi-value" id="kpi-daily-loss">--</div>
        <div class="kpi-sub" id="kpi-daily-loss-sub">&nbsp;</div>
        <div class="cds--progress-bar__track"><div class="cds--progress-bar__fill" id="loss-progress" style="width:0%;background:var(--green)"></div></div>
      </div>
      <div class="cds--kpi">
        <div class="cds--kpi__label">OOS Blocked</div>
        <div class="kpi-value" id="kpi-oos">--</div>
        <div class="kpi-sub" id="kpi-oos-sub">&nbsp;</div>
      </div>
      <div class="cds--kpi">
        <div class="cds--kpi__label">Circuit Breaker</div>
        <div class="kpi-value" id="kpi-circuit">--</div>
        <div class="kpi-sub" id="kpi-errors">&nbsp;</div>
      </div>
    </div>

    <!-- ROW 1: Open Positions + System -->
    <div class="cds--grid-row cds--grid-row--top">
      <div class="cds--tile">
        <div class="cds--tile__header">
          <div class="cds--tile__title">Open Positions</div>
          <div class="cds--tile__badge" id="pos-count">0</div>
        </div>
        <div class="cds--tile__body cds--tile__body--flush" id="positions-body">
          <div class="empty-state">No open positions</div>
        </div>
      </div>
      <div class="cds--tile">
        <div class="cds--tile__header">
          <div class="cds--tile__title">System</div>
          <div class="cds--tile__badge" id="sys-version">--</div>
        </div>
        <div class="cds--tile__body">
          <div class="cds--structured-list" id="system-rows">
            <div class="cds--structured-list__row"><span class="cds--structured-list__label">PID</span><span class="cds--structured-list__value" id="sys-pid">--</span></div>
            <div class="cds--structured-list__row"><span class="cds--structured-list__label">Heartbeat</span><span class="cds--structured-list__value" id="sys-heartbeat">--</span></div>
            <div class="cds--structured-list__row"><span class="cds--structured-list__label">Circuit Mode</span><span class="cds--structured-list__value" id="sys-circuit">--</span></div>
            <div class="cds--structured-list__row"><span class="cds--structured-list__label">Error Count</span><span class="cds--structured-list__value" id="sys-errors">--</span></div>
            <div class="cds--structured-list__row"><span class="cds--structured-list__label">Loop Counter</span><span class="cds--structured-list__value" id="sys-loop">--</span></div>
            <div class="cds--structured-list__row"><span class="cds--structured-list__label">Taker Fee</span><span class="cds--structured-list__value" id="sys-taker">--</span></div>
            <div class="cds--structured-list__row"><span class="cds--structured-list__label">Maker Fee</span><span class="cds--structured-list__value" id="sys-maker">--</span></div>
            <div class="cds--structured-list__row"><span class="cds--structured-list__label">API Latency</span><span class="cds--structured-list__value" id="sys-latency">--</span></div>
            <div class="cds--structured-list__row"><span class="cds--structured-list__label">Metrics Update</span><span class="cds--structured-list__value" id="sys-metrics-ts">--</span></div>
            <div class="cds--structured-list__row"><span class="cds--structured-list__label">Last Refresh</span><span class="cds--structured-list__value" id="sys-refresh">--</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- ROW 2: Pairs Overview + Risk & Performance -->
    <div class="cds--grid-row cds--grid-row--bottom">
      <div class="cds--tile">
        <div class="cds--tile__header">
          <div class="cds--tile__title">Pairs Overview</div>
          <div class="cds--tile__badge" id="pairs-total">0</div>
        </div>
        <div class="cds--tile__body cds--tile__body--flush" id="pairs-body">
          <div class="empty-state">No pairs configured</div>
        </div>
      </div>
      <div class="cds--tile">
        <div class="cds--tile__header">
          <div class="cds--tile__title">Risk &amp; Performance</div>
          <div class="cds--tile__badge">Live</div>
        </div>
        <div class="cds--tile__body cds--tile__body--flush">
          <div class="cds--risk-grid" id="risk-grid">
            <div class="cds--risk-item"><div class="cds--risk-label">Cumul P&amp;L</div><div class="cds--risk-value" id="r-cumul">--</div></div>
            <div class="cds--risk-item"><div class="cds--risk-label">Closed Trades</div><div class="cds--risk-value" id="r-trades">--</div></div>
            <div class="cds--risk-item"><div class="cds--risk-label">Daily P&amp;L %</div><div class="cds--risk-value" id="r-daily-pct">--</div></div>
            <div class="cds--risk-item"><div class="cds--risk-label">Starting Equity</div><div class="cds--risk-value" id="r-equity">--</div></div>
            <div class="cds--risk-item"><div class="cds--risk-label">Emergency Halt</div><div class="cds--risk-value" id="r-halt">--</div></div>
            <div class="cds--risk-item"><div class="cds--risk-label">OOS Blocked</div><div class="cds--risk-value" id="r-oos">--</div></div>
            <div class="cds--risk-item"><div class="cds--risk-label">Taker Fee</div><div class="cds--risk-value" id="r-taker">--</div></div>
            <div class="cds--risk-item"><div class="cds--risk-label">Unrealized P&amp;L</div><div class="cds--risk-value" id="r-unrealized">--</div></div>
          </div>
        </div>
      </div>
    </div>

    <!-- ACTIVITY LOG -->
    <div class="cds--tile" style="flex-shrink:0">
      <div class="cds--tile__header">
        <div class="cds--tile__title">Activity Log</div>
        <div class="cds--tile__badge" id="log-count">0</div>
      </div>
      <div class="cds--tile__body cds--tile__body--flush" id="log-body">
        <div class="empty-state">Waiting for data...</div>
      </div>
    </div>

  </main>

  <script>
    // ================================================================
    // MULTI_ASSETS Dashboard — Data Engine (Carbon Edition)
    // Polls /api/data every 5s via client-side fetch
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

      let html = '<table class="cds--data-table"><thead><tr>'
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
          + '<td style="color:var(--cds-link-primary);font-weight:600">' + escapeHtml(p.real_pair) + flags + '</td>'
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

      let html = '<table class="cds--data-table"><thead><tr>'
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
          + '<td style="color:var(--cds-link-primary)">' + escapeHtml(p.real_pair) + '</td>'
          + '<td>' + statusBadge + '</td>'
          + '<td>' + fmtNum(p.spot_price) + '</td>'
          + '<td>' + lastExec + '</td>'
          + '<td>' + escapeHtml(p.scenario || '--') + '</td>'
          + '<td>' + (p.execution_count || 0) + '</td>'
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
          badge.className = 'cds--tag cds--tag--red';
          dot.className = 'status-dot err';
          $('mode-text').textContent = 'DISCONNECTED';
          $('bot-status').textContent = 'NO HEARTBEAT';
          if (_lastAlive !== false) {
            addLog('ERR', 'Bot heartbeat lost -- ' + fmtAge(d.age_seconds));
            _lastAlive = false;
          }
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
          haltBanner.querySelector('.cds--inline-notification__icon').nextSibling.textContent =
            ' EMERGENCY HALT ACTIVE' + (d.halt_reason ? ' \u2014 ' + d.halt_reason : '');
          badge.className = 'cds--tag cds--tag--red';
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
            badge.className = 'cds--tag cds--tag--yellow';
            dot.className = 'status-dot warn';
            $('mode-text').textContent = circuitMode;
          } else {
            badge.className = 'cds--tag cds--tag--green';
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
        $('sys-errors').style.color = d.error_count > 0 ? 'var(--red)' : 'var(--cds-text-primary)';
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
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/data":
            data = collect_data()
            body = json.dumps(data, default=str, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
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

def _prewarm_cache() -> None:
    """Warm up Binance balance cache in background so first /api/data is instant."""
    try:
        _fetch_usdc_balance()
    except Exception:
        pass


if __name__ == "__main__":
    threading.Thread(target=_prewarm_cache, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), DashboardHandler)
    server.daemon_threads = True
    print(f"[DASHBOARD] Server started -> http://127.0.0.1:{PORT}/dashboard")
    print("[DASHBOARD] Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[DASHBOARD] Stopped.")
        sys.exit(0)
