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
EQUITY_HISTORY_FILE = os.path.join(BASE_DIR, "states", "dashboard_equity_history.json")
BOT_LOG      = os.path.join(BASE_DIR, "code", "logs", "trading_bot.log")
MULTI_SRC    = os.path.join(SRC_DIR, "MULTI_SYMBOLS.py")
ENV_FILE     = os.path.join(BASE_DIR, ".env")
PORT         = 8082
_BINANCE_REST = "https://api.binance.com"

_account_balances_cache: tuple[float, dict[str, float] | None] = (0.0, None)
_BALANCE_TTL  = 120
_QUOTE_CURRENCIES = ("USDC", "USDT", "BUSD", "EUR")
_EQUITY_HISTORY_MAX_POINTS = 1440
_EQUITY_HISTORY_SAMPLE_SECONDS = 60


def _normalize_dashboard_log_line(line: str) -> str:
  """Rewrite legacy voluntary shutdown lines so the dashboard does not flag them as critical."""
  legacy_shutdown = " - CRITICAL - [SHUTDOWN] Signal 2 reçu — arrêt demandé"
  if legacy_shutdown in line:
    return line.replace(
      legacy_shutdown,
      " - INFO - [SHUTDOWN] Signal 2 (SIGINT) reçu — arrêt volontaire demandé [legacy]",
    )
  return line


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


def _fetch_account_balances() -> dict[str, float] | None:
    global _account_balances_cache
    now = time.time()
    if now - _account_balances_cache[0] < _BALANCE_TTL:
        return _account_balances_cache[1]

    api_key    = _load_env_key("BINANCE_API_KEY")
    api_secret = _load_env_key("BINANCE_SECRET_KEY")
    if not api_key or not api_secret:
        _account_balances_cache = (now, None)
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
        balances = {
            str(b.get("asset") or "").upper(): float(b.get("free") or 0.0) + float(b.get("locked") or 0.0)
            for b in data.get("balances", [])
            if b.get("asset")
        }
        _account_balances_cache = (now, balances)
        return balances
    except Exception:
        _account_balances_cache = (now, None)
        return None

def _fetch_usdc_balance() -> float | None:
    balances = _fetch_account_balances()
    if balances is None:
        return None
    return balances.get("USDC")


def _extract_base_asset(symbol: str) -> str | None:
    upper_symbol = symbol.upper()
    for quote_currency in _QUOTE_CURRENCIES:
        if upper_symbol.endswith(quote_currency) and len(upper_symbol) > len(quote_currency):
            return upper_symbol[:-len(quote_currency)]
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

def _coerce_timestamp(ts: str | float | int | None) -> float | None:
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str) and ts.strip():
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    return None


def _read_equity_history() -> list[dict[str, float | str]]:
    try:
        with open(EQUITY_HISTORY_FILE, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return []

    points = payload.get("points", []) if isinstance(payload, dict) else payload
    if not isinstance(points, list):
        return []

    cleaned: list[dict[str, float | str]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        point_ts = _coerce_timestamp(point.get("ts"))
        equity = point.get("equity")
        if point_ts is None or equity is None:
            continue
        try:
            cleaned.append({
                "ts": datetime.fromtimestamp(point_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "equity": round(float(equity), 2),
            })
        except Exception:
            continue

    cleaned.sort(key=lambda point: _coerce_timestamp(point["ts"]) or 0.0)
    return cleaned[-_EQUITY_HISTORY_MAX_POINTS:]


def _write_equity_history(points: list[dict[str, float | str]]) -> None:
    try:
        os.makedirs(os.path.dirname(EQUITY_HISTORY_FILE), exist_ok=True)
        temp_path = f"{EQUITY_HISTORY_FILE}.tmp"
        with open(temp_path, "w", encoding="utf-8") as fh:
            json.dump({"points": points}, fh, ensure_ascii=True)
        os.replace(temp_path, EQUITY_HISTORY_FILE)
    except Exception:
        pass


def _update_equity_history(current_equity: float, now_ts: str | None = None) -> list[dict[str, float | str]]:
    snapshot_ts = now_ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot_epoch = _coerce_timestamp(snapshot_ts)
    snapshot = {"ts": snapshot_ts, "equity": round(float(current_equity), 2)}
    points = _read_equity_history()

    if points and snapshot_epoch is not None:
        last_epoch = _coerce_timestamp(points[-1]["ts"])
        if last_epoch is not None and (snapshot_epoch - last_epoch) < _EQUITY_HISTORY_SAMPLE_SECONDS:
            points[-1] = snapshot
        else:
            points.append(snapshot)
    else:
        points.append(snapshot)

    points = points[-_EQUITY_HISTORY_MAX_POINTS:]
    _write_equity_history(points)
    return points


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


# --- equity curve + log helpers -----------------------------------------

def _build_equity_curve(starting_equity: float, real_pairs: set[str]) -> list[dict]:
    """Build equity timeseries from trade journal for chart rendering."""
    sells: list[dict] = []
    try:
        if os.path.isdir(LOGS_DIR):
            for fname in sorted(os.listdir(LOGS_DIR)):
                if fname == "trade_journal.jsonl" or (fname.startswith("journal_") and fname.endswith(".jsonl")):
                    path = os.path.join(LOGS_DIR, fname)
                    try:
                        with open(path, encoding="utf-8") as fh:
                            for line in fh:
                                line = line.strip()
                                if not line:
                                    continue
                                rec = json.loads(line)
                                if real_pairs and rec.get("pair") not in real_pairs:
                                    continue
                                if rec.get("side", "").lower() == "sell" and rec.get("pnl") is not None:
                                    sells.append({"ts": rec["ts"], "pnl": float(rec["pnl"])})
                    except Exception:
                        pass
    except Exception:
        pass
    sells.sort(key=lambda r: r.get("ts", ""))
    if not sells:
        return []
    equity = starting_equity
    points: list[dict] = [{"ts": sells[0]["ts"], "equity": round(equity, 2)}]
    for s in sells:
        equity += s["pnl"]
        points.append({"ts": s["ts"], "equity": round(equity, 2)})
    if len(points) > 250:
        step = max(1, len(points) // 250)
        last = points[-1]
        points = points[::step]
        if points[-1]["ts"] != last["ts"]:
            points.append(last)
    return points


def _build_mark_to_market_curve(
    starting_equity: float | None,
    current_equity: float,
    buy_timestamp: str | float | int | None,
  history_points: list[dict[str, float | str]] | None = None,
) -> list[dict]:
    """Build a minimal mark-to-market curve anchored on the current session start equity."""
    if starting_equity is None or starting_equity <= 0:
        return []

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    start_ts = now_ts
    if isinstance(buy_timestamp, (int, float)):
        start_ts = datetime.fromtimestamp(float(buy_timestamp), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif isinstance(buy_timestamp, str) and buy_timestamp.strip():
        start_ts = buy_timestamp

    curve: list[dict[str, float | str]] = [
        {"ts": start_ts, "equity": round(float(starting_equity), 2)},
    ]

    start_epoch = _coerce_timestamp(start_ts)
    for point in history_points or []:
        if not isinstance(point, dict):
            continue
        point_ts = point.get("ts")
        point_equity = point.get("equity")
        point_epoch = _coerce_timestamp(point_ts)
        if point_epoch is None or point_equity is None:
            continue
        if start_epoch is not None and point_epoch < start_epoch:
            continue
        curve.append({
            "ts": datetime.fromtimestamp(point_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "equity": round(float(point_equity), 2),
        })

    final_point = {"ts": now_ts, "equity": round(float(current_equity), 2)}
    if curve[-1]["ts"] != final_point["ts"]:
        curve.append(final_point)
    else:
        curve[-1] = final_point

    deduped: list[dict[str, float | str]] = []
    for point in curve:
        if deduped and deduped[-1]["ts"] == point["ts"]:
            deduped[-1] = point
        else:
            deduped.append(point)
    return deduped


def _read_log_lines(n: int = 120) -> list[str]:
  """Return last N lines from the main bot log file."""
  for path in (BOT_LOG, os.path.join(LOGS_DIR, "trading_bot.log")):
    try:
      with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
      return [_normalize_dashboard_log_line(ln.rstrip("\n\r")) for ln in lines[-n:]]
    except Exception:
      continue
  return []


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
    needs_live_balances = usdc_balance is None or any(
      isinstance(ps, dict)
      and (not backtest_pairs or symbol in backtest_pairs)
      and ps.get("last_order_side") == "BUY"
      for symbol, ps in bot_state.items()
    )
    account_balances = _fetch_account_balances() if needs_live_balances else None
    if usdc_balance is None and account_balances is not None:
      usdc_balance = account_balances.get("USDC")
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

        mt_pair = (mt.get("pairs") or {}).get(symbol, {})
        real_pair = next((p["real_pair"] for p in configured if p["backtest_pair"] == symbol), symbol)
        base_asset = _extract_base_asset(real_pair)
        live_qty = None
        if in_position and account_balances is not None and base_asset is not None:
          live_qty = account_balances.get(base_asset, 0.0)
        display_qty = live_qty if live_qty is not None else qty

        unrealized_pnl = None
        unrealized_pct = None
        if in_position and entry and spot and display_qty is not None:
          try:
            unrealized_pnl = (float(spot) - float(entry)) * float(display_qty)
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

        is_oos_blocked = bool(ps.get("oos_blocked", mt_pair.get("oos_blocked", False)))
        if is_oos_blocked:
            oos_blocked_count += 1

        pairs[symbol] = {
            "real_pair":           real_pair,
            "in_position":         in_position,
            "entry_price":         entry,
            "spot_price":          spot,
            "qty":                 display_qty,
            "unrealized_pnl":      unrealized_pnl,
            "unrealized_pct":      unrealized_pct,
            "stop_loss":           sl,
            "sl_dist_pct":         sl_dist_pct,
            "sl_placed":           bool(ps.get("sl_exchange_placed") or mt_pair.get("sl_placed")),
            "trailing_active":     bool(ps.get("trailing_stop_activated", False)),
            "trailing_stop":       ps.get("trailing_stop"),
            "scenario":            ps.get("entry_scenario") or (ps.get("last_best_params") or {}).get("scenario", ""),
            "timeframe":           ps.get("entry_timeframe") or (ps.get("last_best_params") or {}).get("timeframe", ""),
          "entry_ema1":          ps.get("entry_ema1"),
          "entry_ema2":          ps.get("entry_ema2"),
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
    recent = _recent_trades(real_pairs, limit=50)

    total_unrealized_pnl = sum(
        p["unrealized_pnl"] for p in pairs.values()
        if p["in_position"] and p["unrealized_pnl"] is not None
    )
    total_market_value = sum(
        float(p["spot_price"] or 0) * float(p["qty"] or 0)
        for p in pairs.values()
        if p["in_position"] and p["spot_price"] and p["qty"]
    )
    total_equity = (usdc_balance or 0) + total_market_value
    equity_delta = None
    if starting_equity and starting_equity > 0:
      equity_delta = total_equity - float(starting_equity)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_local = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    equity_history = _update_equity_history(total_equity, now_ts=now_utc)

    latest_buy_ts = None
    for pair_data in pairs.values():
      if pair_data["in_position"] and pair_data.get("buy_timestamp") is not None:
        _ts = pair_data.get("buy_timestamp")
        if latest_buy_ts is None or _ts > latest_buy_ts:
          latest_buy_ts = _ts

    equity_curve = _build_mark_to_market_curve(starting_equity, total_equity, latest_buy_ts, equity_history)

    return {
        "now":             now_utc,
        "now_local":       now_local,
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
        "equity_delta":    equity_delta,
        "trade_count":     trade_count,
        "pairs":           pairs,
        "recent_trades":        recent,
        "total_unrealized_pnl": total_unrealized_pnl,
        "total_equity":         total_equity,
        "equity_curve":         equity_curve,
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
  <meta name="darkreader-lock">
  <title>TRADING/BOT DASHBOARD</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:ital,wght@0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">
  <script>
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.getRegistrations().then(function(r) { r.forEach(function(s) { s.unregister(); }); });
    }
  </script>
  <style>
    :root {
      --bg:      #0a0c10;
      --bg1:     #111418;
      --bg2:     #181d24;
      --bg3:     #1e242d;
      --border:  #1e2530;
      --border2: #2a3547;
      --brand:   #f0c040;
      --green:   #22c55e;
      --red:     #ef4444;
      --yellow:  #f59e0b;
      --cyan:    #06b6d4;
      --text:    #c8d3e0;
      --text2:   #7a8899;
      --text3:   #4a5568;
      --font:    'JetBrains Mono', 'Courier New', monospace;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    html, body { height: 100%; overflow: hidden; }
    body {
      font-family: var(--font);
      background: var(--bg);
      color: var(--text);
      font-size: 12px;
      display: flex;
      flex-direction: column;
      -webkit-font-smoothing: antialiased;
    }
    ::-webkit-scrollbar { width: 5px; height: 5px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 3px; }

    /* ── HEADER ── */
    header {
      background: var(--bg1);
      border-bottom: 1px solid var(--border);
      padding: 0 16px;
      height: 44px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
      gap: 12px;
    }
    .h-left { display: flex; align-items: center; gap: 10px; }
    .brand {
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 1.5px;
      color: #fff;
      white-space: nowrap;
    }
    .brand .slash { color: var(--brand); }
    .brand-sub {
      font-size: 10px;
      font-weight: 500;
      color: var(--text2);
      letter-spacing: 3px;
      text-transform: uppercase;
      border-left: 1px solid var(--border2);
      padding-left: 10px;
    }
    .h-right { display: flex; align-items: center; gap: 10px; }
    #clock {
      font-size: 13px;
      font-weight: 500;
      color: var(--text);
      letter-spacing: 0.5px;
      white-space: nowrap;
    }
    .refresh-btn {
      background: var(--bg3);
      border: 1px solid var(--border2);
      color: var(--text2);
      font-family: var(--font);
      font-size: 10px;
      font-weight: 600;
      padding: 4px 10px;
      cursor: pointer;
      letter-spacing: 1.5px;
      text-transform: uppercase;
      transition: background 0.12s, color 0.12s;
    }
    .refresh-btn:hover { background: var(--border2); color: var(--text); }
    .status-dot {
      width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
      background: var(--green);
      box-shadow: 0 0 8px var(--green);
      transition: background 0.3s, box-shadow 0.3s;
    }
    .status-dot.offline { background: var(--red); box-shadow: 0 0 8px var(--red); animation: blink 1.5s ease infinite; }
    .status-dot.warn    { background: var(--yellow); box-shadow: 0 0 8px var(--yellow); }
    @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.2} }

    /* ── ALERT BARS ── */
    .alert-bar {
      display: none;
      background: #1a0808;
      border-bottom: 1px solid var(--red);
      padding: 5px 16px;
      font-size: 11px;
      color: var(--red);
      letter-spacing: 0.5px;
      flex-shrink: 0;
    }
    .alert-bar.visible { display: block; }

    /* ── MAIN LAYOUT ── */
    .content {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 1px;
      background: var(--border);
      min-height: 0;
    }

    /* ── KPI STRIP ── */
    .kpi-strip {
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 6px;
      flex-shrink: 0;
      padding: 6px 6px 0;
      background: var(--bg);
    }
    .kpi-card {
      background: var(--bg1);
      padding: 16px 20px 14px;
      border: 1px solid var(--border2);
      border-top: 2px solid var(--border2);
      transition: border-top-color 0.15s, background 0.15s;
    }
    .kpi-card:hover { border-top-color: var(--brand); background: var(--bg2); }
    .kpi-label {
      font-size: 9px;
      font-weight: 600;
      letter-spacing: 2.5px;
      text-transform: uppercase;
      color: var(--text3);
      margin-bottom: 8px;
    }
    .kpi-value {
      font-size: 44px;
      font-weight: 700;
      color: var(--brand);
      line-height: 1.05;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .kpi-value.neutral { color: var(--text); }
    .kpi-value.pos { color: var(--green); }
    .kpi-value.neg { color: var(--red); }
    .kpi-sub { font-size: 11px; color: var(--text2); margin-top: 5px; }
    .kpi-sub.pos { color: var(--green); }
    .kpi-sub.neg { color: var(--red); }

    /* ── MAIN GRID ── */
    .main-grid {
      flex: 1;
      display: grid;
      grid-template-rows: 1fr 1fr;
      gap: 1px;
      min-height: 0;
    }
    .row {
      display: grid;
      gap: 1px;
      min-height: 0;
    }
    .row-mid { grid-template-columns: 3fr 2fr; }
    .row-bot { grid-template-columns: 1fr 1fr; }

    /* ── PANEL ── */
    .panel {
      background: var(--bg1);
      display: flex;
      flex-direction: column;
      min-height: 0;
      overflow: hidden;
    }
    .panel-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 7px 12px;
      border-bottom: 1px solid var(--border);
      flex-shrink: 0;
    }
    .panel-title {
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 2.5px;
      text-transform: uppercase;
      color: var(--text2);
    }
    .panel-badge {
      font-size: 10px;
      color: var(--text3);
      letter-spacing: 0.5px;
    }
    .panel-body {
      flex: 1;
      overflow: auto;
      min-height: 0;
    }

    /* ── SVG CHART ── */
    .chart-wrap {
      flex: 1;
      min-height: 0;
      padding: 8px 10px 4px;
      overflow: hidden;
    }
    #equity-chart { width: 100%; height: 100%; display: block; }

    /* ── TABLES ── */
    table { width: 100%; border-collapse: collapse; }
    thead th {
      background: var(--bg2);
      padding: 6px 10px;
      text-align: left;
      font-size: 9px;
      font-weight: 600;
      letter-spacing: 2px;
      text-transform: uppercase;
      color: var(--text3);
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
      position: sticky;
      top: 0;
      z-index: 1;
    }
    thead th.r { text-align: right; }
    tbody td {
      padding: 5px 10px;
      border-bottom: 1px solid var(--border);
      color: var(--text);
      white-space: nowrap;
      font-size: 12px;
    }
    tbody td.r { text-align: right; }
    tbody tr:hover td { background: var(--bg2); }
    .c-brand  { color: var(--brand) !important; font-weight: 600; }
    .c-green  { color: var(--green) !important; }
    .c-red    { color: var(--red)   !important; }
    .c-dim    { color: var(--text2) !important; }
    .empty-row td { text-align: center; color: var(--text3); padding: 18px; font-style: italic; }

    /* ── ACTION BADGES ── */
    .badge {
      display: inline-block;
      padding: 2px 7px;
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 1.5px;
      text-transform: uppercase;
    }
    .badge-buy   { background: rgba(34,197,94,0.12);  color: var(--green); border: 1px solid rgba(34,197,94,0.35); }
    .badge-close { background: rgba(239,68,68,0.12);  color: var(--red);   border: 1px solid rgba(239,68,68,0.35); }

    /* ── LIVE LOG ── */
    #live-log {
      font-size: 11px;
      line-height: 1.55;
      padding: 2px 0;
    }
    .log-line {
      padding: 1px 10px;
      white-space: pre-wrap;
      word-break: break-all;
      color: var(--text2);
      border-bottom: 1px solid rgba(30,37,48,0.5);
    }
    .log-line:hover { background: var(--bg2); }
    .log-err  { color: var(--red); }
    .log-warn { color: var(--yellow); }
    .log-dbg  { color: var(--text3); }

    /* ── FOOTER ── */
    footer {
      background: var(--bg1);
      border-top: 1px solid var(--border);
      padding: 3px 16px;
      font-size: 9px;
      letter-spacing: 0.5px;
      color: var(--text3);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-shrink: 0;
    }
  </style>
</head>
<body>

  <!-- HEADER -->
  <header>
    <div class="h-left">
      <span class="brand">TRADING<span class="slash">/</span>BOT</span>
      <span class="brand-sub">Dashboard</span>
    </div>
    <div class="h-right">
      <span id="clock">--:--:-- LOCAL</span>
      <button class="refresh-btn" id="refresh-btn" onclick="manualRefresh()">&#8635; REFRESH</button>
      <span class="status-dot" id="status-dot"></span>
    </div>
  </header>

  <!-- ALERT BARS -->
  <div class="alert-bar" id="disconnect-bar">&#9888; Bot disconnected &mdash; no heartbeat detected. Check PM2 process.</div>
  <div class="alert-bar" id="halt-bar">&#9888; EMERGENCY HALT ACTIVE</div>

  <!-- CONTENT -->
  <div class="content">

    <!-- KPI STRIP -->
    <div class="kpi-strip">
      <div class="kpi-card">
        <div class="kpi-label">Equity</div>
        <div class="kpi-value" id="kpi-equity">--</div>
        <div class="kpi-sub" id="kpi-equity-sub">&nbsp;</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Cash (USDC)</div>
        <div class="kpi-value neutral" id="kpi-cash">--</div>
        <div class="kpi-sub" id="kpi-cash-sub">&nbsp;</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Day P&amp;L</div>
        <div class="kpi-value" id="kpi-daypnl">--</div>
        <div class="kpi-sub" id="kpi-daypnl-sub">&nbsp;</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Vs. Start</div>
        <div class="kpi-value" id="kpi-vsstart">--</div>
        <div class="kpi-sub" id="kpi-vsstart-sub">&nbsp;</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">Positions</div>
        <div class="kpi-value neutral" id="kpi-positions">--</div>
        <div class="kpi-sub" id="kpi-positions-sub">&nbsp;</div>
      </div>
    </div>

    <!-- MAIN GRID -->
    <div class="main-grid">

      <!-- ROW 1: Equity Curve + Open Positions -->
      <div class="row row-mid">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title">Equity Curve</span>
            <span class="panel-badge" id="curve-badge">no data</span>
          </div>
          <div class="chart-wrap">
            <svg id="equity-chart" xmlns="http://www.w3.org/2000/svg"></svg>
          </div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title">Open Positions</span>
            <span class="panel-badge" id="pos-badge">0 / 0</span>
          </div>
          <div class="panel-body">
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th class="r">Qty</th>
                  <th class="r">Entry</th>
                  <th class="r">Current</th>
                  <th class="r">P&amp;L</th>
                </tr>
              </thead>
              <tbody id="pos-tbody">
                <tr class="empty-row"><td colspan="5">No open positions</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- ROW 2: Trade History + Live Log -->
      <div class="row row-bot">
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title">Trade History</span>
            <span class="panel-badge">last 50</span>
          </div>
          <div class="panel-body">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Action</th>
                  <th>Symbol</th>
                  <th class="r">Qty</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody id="trades-tbody">
                <tr class="empty-row"><td colspan="5">No trades yet</td></tr>
              </tbody>
            </table>
          </div>
        </div>
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title">Live Log</span>
            <span class="panel-badge">last 120 lines &bull; auto-scroll</span>
          </div>
          <div class="panel-body" id="log-container">
            <div id="live-log">
              <div class="log-line log-dbg">Waiting for log data...</div>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>

  <!-- FOOTER -->
  <footer>
    <span>PAPER TRADING // FOR TESTING ONLY // NOT FINANCIAL ADVICE</span>
    <span>Last refresh: <span id="last-refresh">--</span></span>
  </footer>

  <script>
    // ================================================================
    // TRADING/BOT Dashboard — Terminal Edition
    // /api/data  polled every 5s
    // /api/logs  polled every 5s
    // ================================================================
    var POLL_MS = 5000;
    var _equityCurveData = [];
    var _lastAlive = null;
    var _lastHalt  = null;
    var _logAutoScroll = true;
    var _chartResizeTimer = null;

    function $(id) { return document.getElementById(id); }

    function escHtml(s) {
      if (!s) return '';
      var d = document.createElement('div');
      d.textContent = String(s);
      return d.innerHTML;
    }

    function fmtMoney(v, dec) {
      if (v === null || v === undefined) return '--';
      var d = (dec !== undefined) ? dec : 2;
      return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
    }

    function fmtPnl(v) {
      if (v === null || v === undefined) return '--';
      var n = Number(v);
      return (n >= 0 ? '+' : '') + fmtMoney(n) + ' $';
    }

    function fmtPct(v) {
      if (v === null || v === undefined) return '';
      var n = Number(v);
      return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
    }

    function pnlCls(v) {
      var n = Number(v);
      return n > 0 ? 'pos' : n < 0 ? 'neg' : '';
    }

    function fmtPrice(v) {
      if (v === null || v === undefined) return '--';
      var n = Number(v);
      if (n >= 1000) return fmtMoney(n, 2);
      if (n >= 1)    return fmtMoney(n, 4);
      return fmtMoney(n, 6);
    }

    function fmtQty(v) {
      if (v === null || v === undefined) return '--';
      var n = Number(v);
      if (n >= 1000) return fmtMoney(n, 0);
      if (n >= 1)    return fmtMoney(n, 2);
      return fmtMoney(n, 4);
    }

    function stripQuote(pair) {
      if (!pair) return pair;
      return pair.replace(/USDC$/i, '').replace(/USDT$/i, '');
    }

    function fmtTradeTime(ts) {
      if (!ts) return '--';
      try {
        var d = new Date(ts);
        return d.toLocaleString('en-US', {
          month: '2-digit', day: '2-digit',
          hour: '2-digit', minute: '2-digit', hour12: false
        }).replace(',', '');
      } catch(e) { return String(ts).substring(0, 16); }
    }

    // ── CLOCK ──
    function updateClock() {
      var now = new Date();
      $('clock').textContent = now.toLocaleTimeString('en-US', { hour12: false }) + ' LOCAL';
    }

    // ── EQUITY CURVE SVG ──
    function drawChart(points) {
      var svg = $('equity-chart');
      if (!svg) return;
      var W = svg.clientWidth  || svg.parentElement.clientWidth  || 600;
      var H = svg.clientHeight || svg.parentElement.clientHeight || 180;
      svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
      svg.innerHTML = '';

      if (!points || points.length < 2) {
        var t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        t.setAttribute('x', W / 2); t.setAttribute('y', H / 2);
        t.setAttribute('text-anchor', 'middle');
        t.setAttribute('fill', '#4a5568');
        t.setAttribute('font-family', 'JetBrains Mono, monospace');
        t.setAttribute('font-size', '11');
        t.textContent = 'No historical data yet';
        svg.appendChild(t);
        return;
      }

      var PAD = { top: 12, right: 10, bottom: 20, left: 54 };
      var cW = W - PAD.left - PAD.right;
      var cH = H - PAD.top  - PAD.bottom;

      var vals = points.map(function(p) { return p.equity; });
      var ts   = points.map(function(p) { return new Date(p.ts).getTime(); });
      var minV = Math.min.apply(null, vals);
      var maxV = Math.max.apply(null, vals);
      var vRng = maxV - minV;
      minV -= vRng * 0.06;
      maxV += vRng * 0.06;
      vRng = maxV - minV || 1;

      var minT = ts[0];
      var maxT = ts[ts.length - 1];
      var tRng = maxT - minT || 1;

      function xC(t) { return PAD.left + ((t - minT) / tRng) * cW; }
      function yC(v) { return PAD.top  + cH - ((v - minV) / vRng) * cH; }

      // defs: gradient
      var defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
      var grad = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
      grad.setAttribute('id', 'cg'); grad.setAttribute('x1','0'); grad.setAttribute('y1','0');
      grad.setAttribute('x2','0'); grad.setAttribute('y2','1');
      var s1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
      s1.setAttribute('offset','0%'); s1.setAttribute('stop-color','#f0c040'); s1.setAttribute('stop-opacity','0.28');
      var s2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
      s2.setAttribute('offset','100%'); s2.setAttribute('stop-color','#f0c040'); s2.setAttribute('stop-opacity','0.02');
      grad.appendChild(s1); grad.appendChild(s2); defs.appendChild(grad); svg.appendChild(defs);

      // horizontal grid lines
      for (var g = 0; g <= 4; g++) {
        var gv = minV + (vRng * g / 4);
        var gy = yC(gv);
        var gl = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        gl.setAttribute('x1', PAD.left); gl.setAttribute('x2', PAD.left + cW);
        gl.setAttribute('y1', gy); gl.setAttribute('y2', gy);
        gl.setAttribute('stroke', '#1e2530'); gl.setAttribute('stroke-width', '1');
        svg.appendChild(gl);
        var lbl = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        lbl.setAttribute('x', PAD.left - 4); lbl.setAttribute('y', gy + 3);
        lbl.setAttribute('text-anchor', 'end'); lbl.setAttribute('fill', '#4a5568');
        lbl.setAttribute('font-family', 'JetBrains Mono, monospace'); lbl.setAttribute('font-size', '8');
        lbl.textContent = '$' + (gv >= 1000 ? (gv / 1000).toFixed(0) + 'k' : gv.toFixed(0));
        svg.appendChild(lbl);
      }

      // x-axis labels
      var xDates = [{ t: minT, lbl: 'Start' }, { t: maxT, lbl: 'Now' }];
      if (tRng > 7 * 86400 * 1000) {
        var mid = Math.round((minT + maxT) / 2);
        var md = new Date(mid);
        xDates.push({ t: mid, lbl: md.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) });
      }
      xDates.forEach(function(xl) {
        var xt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        xt.setAttribute('x', xC(xl.t)); xt.setAttribute('y', PAD.top + cH + 14);
        xt.setAttribute('text-anchor', 'middle'); xt.setAttribute('fill', '#4a5568');
        xt.setAttribute('font-family', 'JetBrains Mono, monospace'); xt.setAttribute('font-size', '8');
        xt.textContent = xl.lbl;
        svg.appendChild(xt);
      });

      // area fill
      var aD = 'M ' + xC(ts[0]) + ' ' + (PAD.top + cH);
      aD += ' L ' + xC(ts[0]) + ' ' + yC(vals[0]);
      for (var i = 1; i < points.length; i++) { aD += ' L ' + xC(ts[i]) + ' ' + yC(vals[i]); }
      aD += ' L ' + xC(ts[ts.length-1]) + ' ' + (PAD.top + cH) + ' Z';
      var area = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      area.setAttribute('d', aD); area.setAttribute('fill', 'url(#cg)');
      svg.appendChild(area);

      // line
      var lD = 'M ' + xC(ts[0]) + ' ' + yC(vals[0]);
      for (var j = 1; j < points.length; j++) { lD += ' L ' + xC(ts[j]) + ' ' + yC(vals[j]); }
      var line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      line.setAttribute('d', lD); line.setAttribute('stroke', '#f0c040');
      line.setAttribute('stroke-width', '1.5'); line.setAttribute('fill', 'none');
      svg.appendChild(line);

      // end dot
      var lastX = xC(ts[ts.length-1]);
      var lastY = yC(vals[vals.length-1]);
      var dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      dot.setAttribute('cx', lastX); dot.setAttribute('cy', lastY);
      dot.setAttribute('r', '3'); dot.setAttribute('fill', '#f0c040');
      svg.appendChild(dot);
    }

    // ── POSITIONS ──
    function renderPositions(pairs) {
      var tbody = $('pos-tbody');
      var open = Object.values(pairs).filter(function(p) { return p.in_position; });
      $('pos-badge').textContent = open.length + ' / ' + Object.keys(pairs).length;
      if (open.length === 0) {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="5">No open positions</td></tr>';
        return;
      }
      tbody.innerHTML = open.map(function(p) {
        var sym = stripQuote(p.real_pair || '');
        var pnl = p.unrealized_pnl;
        var pct = p.unrealized_pct;
        var cls = (pnl !== null) ? 'c-' + (pnl >= 0 ? 'green' : 'red') : '';
        return '<tr>'
          + '<td class="c-brand">' + escHtml(sym) + '</td>'
          + '<td class="r">' + fmtQty(p.qty) + '</td>'
          + '<td class="r">' + fmtPrice(p.entry_price) + ' $</td>'
          + '<td class="r">' + fmtPrice(p.spot_price) + ' $</td>'
          + '<td class="r ' + cls + '">'
          + (pnl !== null ? fmtPnl(pnl) : '--')
          + (pct !== null ? '<br><span style="font-size:10px">' + fmtPct(pct) + '</span>' : '')
          + '</td></tr>';
      }).join('');
    }

    // ── TRADE HISTORY ──
    function renderTrades(trades) {
      var tbody = $('trades-tbody');
      if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr class="empty-row"><td colspan="5">No trades yet</td></tr>';
        return;
      }
      tbody.innerHTML = trades.slice(0, 50).map(function(t) {
        var side   = (t.side || '').toLowerCase();
        var isBuy  = side === 'buy';
        var sym    = escHtml(stripQuote(t.pair || ''));
        var badge  = isBuy
          ? '<span class="badge badge-buy">BUY</span>'
          : '<span class="badge badge-close">CLOSE</span>';
        var detail = '';
        if (isBuy) {
          detail = t.stop ? 'SL $' + fmtPrice(t.stop) : '';
        } else {
          var sr  = escHtml(t.sell_reason || '');
          var pnlv = (t.pnl !== null && t.pnl !== undefined)
            ? ' &mdash; P&amp;L ' + (t.pnl >= 0 ? '+' : '') + fmtMoney(t.pnl) + '$'
            : '';
          detail = sr + pnlv;
        }
        return '<tr>'
          + '<td class="c-dim">' + escHtml(fmtTradeTime(t.ts)) + '</td>'
          + '<td>' + badge + '</td>'
          + '<td class="c-brand">' + sym + '</td>'
          + '<td class="r">' + fmtQty(t.qty) + '</td>'
          + '<td>' + detail + '</td>'
          + '</tr>';
      }).join('');
    }

    // ── LIVE LOG ──
    async function refreshLogs() {
      try {
        var r = await fetch('/api/logs');
        if (!r.ok) return;
        var lines = await r.json();
        var container = $('log-container');
        var logDiv    = $('live-log');
        var atBottom  = container.scrollTop + container.clientHeight >= container.scrollHeight - 30;
        logDiv.innerHTML = lines.map(function(line) {
          var cls = 'log-line';
          if (line.indexOf(' - ERROR - ') >= 0 || line.indexOf('ERROR') >= 0) cls += ' log-err';
          else if (line.indexOf(' - WARNING - ') >= 0 || line.indexOf('WARN') >= 0) cls += ' log-warn';
          else if (line.indexOf(' - DEBUG - ') >= 0) cls += ' log-dbg';
          return '<div class="' + cls + '">' + escHtml(line) + '</div>';
        }).join('');
        if (atBottom || _logAutoScroll) {
          container.scrollTop = container.scrollHeight;
        }
      } catch(e) { /* ignore */ }
    }

    // ── MAIN REFRESH ──
    async function doRefresh() {
      try {
        var r = await fetch('/api/data');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        var d = await r.json();

        var alive = d.alive;
        var dot   = $('status-dot');

        // status
        if (!alive) {
          dot.className = 'status-dot offline';
          $('disconnect-bar').classList.add('visible');
        } else {
          $('disconnect-bar').classList.remove('visible');
          if (d.emergency_halt) {
            dot.className = 'status-dot warn';
            var hb = $('halt-bar');
            hb.classList.add('visible');
            hb.textContent = '\u26A0 EMERGENCY HALT ACTIVE' + (d.halt_reason ? ' \u2014 ' + d.halt_reason : '');
          } else {
            dot.className = 'status-dot';
            $('halt-bar').classList.remove('visible');
          }
        }

        // KPIs
        var equity      = d.total_equity !== undefined ? d.total_equity : d.usdc_balance;
        var cash        = d.usdc_balance;
        var dayPnl      = d.daily_pnl  || 0;
        var realizedPnl = d.cumul_pnl  || 0;
        var equityDelta = d.equity_delta;
        var startEq     = d.starting_equity;

        $('kpi-equity').textContent = equity !== null && equity !== undefined
          ? fmtMoney(equity) + ' $' : '--';
        $('kpi-equity-sub').textContent = startEq
          ? 'start: ' + fmtMoney(startEq) + ' $' : 'total portfolio';

        $('kpi-cash').textContent = cash !== null && cash !== undefined
          ? fmtMoney(cash) + ' $' : '--';
        $('kpi-cash-sub').textContent = 'free USDC balance';

        $('kpi-daypnl').textContent = fmtPnl(dayPnl);
        $('kpi-daypnl').className   = 'kpi-value ' + pnlCls(dayPnl);
        $('kpi-daypnl-sub').textContent = 'realized today ' + fmtPct(d.daily_pnl_pct);
        $('kpi-daypnl-sub').className   = 'kpi-sub ' + pnlCls(dayPnl);

        var vsValue = (equityDelta !== null && equityDelta !== undefined) ? equityDelta : realizedPnl;
        $('kpi-vsstart').textContent = fmtPnl(vsValue);
        $('kpi-vsstart').className   = 'kpi-value ' + pnlCls(vsValue);
        var vsPct = (startEq && startEq > 0) ? (vsValue / startEq * 100) : 0;
        $('kpi-vsstart-sub').textContent = fmtPct(vsPct) + ' total · realized ' + fmtPnl(realizedPnl);
        $('kpi-vsstart-sub').className   = 'kpi-sub ' + pnlCls(vsValue);

        $('kpi-positions').textContent = d.open_count;
        $('kpi-positions-sub').textContent = 'max ' + d.total_pairs;

        // equity curve
        if (d.equity_curve && d.equity_curve.length > 1) {
          _equityCurveData = d.equity_curve;
          drawChart(_equityCurveData);
          var first  = d.equity_curve[0].equity;
          var last   = d.equity_curve[d.equity_curve.length - 1].equity;
          var chgPct = first > 0 ? ((last - first) / first * 100) : 0;
          var cbadge = $('curve-badge');
          cbadge.textContent = (chgPct >= 0 ? '+' : '') + chgPct.toFixed(2) + '% total';
          cbadge.style.color = chgPct >= 0 ? 'var(--green)' : 'var(--red)';
        }

        // positions + trades
        renderPositions(d.pairs || {});
        renderTrades(d.recent_trades || []);

        $('last-refresh').textContent = new Date().toLocaleTimeString('en-US', { hour12: false });

      } catch(e) {
        console.error('[dashboard] refresh error:', e);
      }
    }

    function manualRefresh() {
      var btn = $('refresh-btn');
      if (btn) btn.textContent = '\u21BB ...';
      doRefresh().then(function() {
        if (btn) btn.textContent = '\u21BB REFRESH';
      });
      refreshLogs();
    }

    // resize handler
    window.addEventListener('resize', function() {
      clearTimeout(_chartResizeTimer);
      _chartResizeTimer = setTimeout(function() {
        if (_equityCurveData.length > 1) drawChart(_equityCurveData);
      }, 150);
    });

    // start
    updateClock();
    setInterval(updateClock, 1000);
    doRefresh();
    setInterval(doRefresh, POLL_MS);
    refreshLogs();
    setInterval(refreshLogs, POLL_MS);
  </script>

</body>
</html>"""


# --- HTML loader — reads dashboard.html from disk on every request -------
# This means CSS/layout changes take effect immediately with no server restart.
_HTML_FILE = os.path.join(os.path.dirname(__file__), "dashboard.html")


def _get_dashboard_html() -> bytes:
    """Return dashboard HTML — prefers dashboard.html on disk over embedded string."""
    try:
        with open(_HTML_FILE, encoding="utf-8") as fh:
            return fh.read().encode("utf-8")
    except Exception:
        return DASHBOARD_HTML.encode("utf-8")


# --- HTTP handler --------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/", "/dashboard"):
            body = _get_dashboard_html()
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
        elif self.path == "/api/logs":
            lines = _read_log_lines(120)
            body = json.dumps(lines, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
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
