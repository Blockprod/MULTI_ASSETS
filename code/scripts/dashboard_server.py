"""dashboard_server.py — Dashboard web MULTI_ASSETS (stdlib only, port 8080).

Sources de données :
  - states/heartbeat.json     → liveness bot (mis à jour chaque cycle)
  - states/bot_state.json     → positions, PnL journalier, état complet
  - metrics/metrics.json      → snapshot métriques (mis à jour toutes les 5 min)

Endpoints :
  GET /           → dashboard HTML
  GET /dashboard  → idem
  GET /api/data   → données JSON brutes

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
# Le bot écrit ses fichiers relatifs à code/src/ (cf. bot_config.py states_dir)
SRC_DIR      = os.path.join(BASE_DIR, "code", "src")
HEARTBEAT    = os.path.join(SRC_DIR, "states", "heartbeat.json")
BOT_STATE    = os.path.join(SRC_DIR, "states", "bot_state.json")
LOGS_DIR     = os.path.join(SRC_DIR, "logs")
METRICS_FILE = os.path.join(BASE_DIR, "metrics", "metrics.json")
MULTI_SRC    = os.path.join(SRC_DIR, "MULTI_SYMBOLS.py")
ENV_FILE     = os.path.join(BASE_DIR, ".env")
PORT         = 8082
_BINANCE_REST = "https://api.binance.com"

# Cache solde USDC : (last_fetch_time, value)
_balance_cache: tuple[float, float | None] = (0.0, None)
_BALANCE_TTL  = 120  # secondes


# ─── solde USDC via Binance API (stdlib) ────────────────────────────────────────

def _load_env_key(name: str) -> str | None:
    """Lit une variable depuis .env ou os.environ, sans dépendance externe."""
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
    """Appel GET /api/v3/account pour récupérer le solde USDC libre.
    Résultat mis en cache 120s pour ne pas dépasser le rate limit.
    """
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
        # Utiliser l'heure serveur Binance pour éviter toute dérive d'horloge
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


# ─── parser crypto_pairs depuis MULTI_SYMBOLS.py ─────────────────────────────

def _parse_crypto_pairs() -> list[dict[str, str]]:
    """Extrait crypto_pairs depuis MULTI_SYMBOLS.py via regex.
    Retourne une liste de {backtest_pair, real_pair}.
    """
    try:
        with open(MULTI_SRC, encoding="utf-8") as fh:
            src = fh.read()
        # Cherche le bloc crypto_pairs = [ ... ]
        block_match = re.search(
            r"crypto_pairs\s*=\s*\[(.*?)\]",
            src,
            re.DOTALL,
        )
        if not block_match:
            return []
        block = block_match.group(1)
        # Extrait chaque {"backtest_pair": "X", "real_pair": "Y"}
        pairs = re.findall(
            r'"backtest_pair"\s*:\s*"([^"]+)".*?"real_pair"\s*:\s*"([^"]+)"',
            block,
            re.DOTALL,
        )
        return [{"backtest_pair": bp, "real_pair": rp} for bp, rp in pairs]
    except Exception:
        return []

_JSON_HEADER = b"JSON_V1:"
_HEADER_LEN  = len(_JSON_HEADER) + 32  # 8 bytes marker + 32 bytes HMAC-SHA256


# ─── helpers ──────────────────────────────────────────────────────────────────

def _read_json(path: str) -> dict:
    """Lit un fichier JSON (format plain ou JSON_V1 signé)."""
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


def _fmt_price(val: Any, decimals: int = 4) -> str:
    if val is None:
        return "—"
    try:
        return f"{float(val):,.{decimals}f}"
    except Exception:
        return str(val)


def _fmt_usdc(val: Any) -> str:
    if val is None:
        return "—"
    try:
        v = float(val)
        sign = "+" if v > 0 else ""
        return f"{sign}{v:,.2f} USDC"
    except Exception:
        return str(val)


def _pnl_color(val: Any) -> str:
    try:
        return "#10b981" if float(val) >= 0 else "#ef4444"
    except Exception:
        return "#94a3b8"


def _status_dot(alive: bool) -> str:
    cls = "dot-alive" if alive else "dot-dead"
    label = "ACTIF" if alive else "INACTIF"
    return f'<span class="dot {cls}"></span><span class="status-label">{label}</span>'


def _badge(text: str, kind: str = "neutral") -> str:
    """kind: ok | warn | danger | neutral"""
    return f'<span class="badge badge-{kind}">{text}</span>'


# ─── data aggregation ─────────────────────────────────────────────────────────

def _get_daily_pnl(tracker: Any) -> tuple[float, float]:
    """Retourne (daily_pnl_usdc, daily_pnl_pct) depuis _daily_pnl_tracker."""
    if not isinstance(tracker, dict):
        return 0.0, 0.0
    today = datetime.now().strftime("%Y-%m-%d")
    # Format actuel : {date: {total_pnl, trade_count}}
    if today in tracker:
        pnl = float(tracker[today].get("total_pnl", 0.0))
        return pnl, 0.0
    # Format legacy : {date, daily_pnl, daily_pnl_pct, starting_equity, ...}
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
    """Somme tous les PnL de vente dans les journaux, filtrés par real_pairs.
    Retourne (total_pnl_usdc, nombre_de_trades).
    """
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


def collect_data() -> dict:
    """Agrège toutes les sources en un seul dictionnaire."""
    hb = _read_json(HEARTBEAT)
    raw_state = _read_json(BOT_STATE)
    mt = _read_json(METRICS_FILE)

    # ── paires configurées dans MULTI_SYMBOLS.py ─────────────────────────────
    configured = _parse_crypto_pairs()
    backtest_pairs = {p["backtest_pair"] for p in configured}
    real_pairs     = {p["real_pair"]     for p in configured}

    bot_state: dict = raw_state.get("bot_state", raw_state)
    tracker = raw_state.get("_daily_pnl_tracker", {})
    emergency_halt: bool = bool(raw_state.get("emergency_halt", mt.get("emergency_halt", False)))
    halt_reason: str = raw_state.get("emergency_halt_reason", "") or ""

    # Bot liveness
    age = _age_seconds(hb.get("timestamp", "")) if hb else 999999
    alive = age < 300

    # Daily PnL
    daily_pnl, daily_pnl_pct = _get_daily_pnl(tracker)
    starting_equity = _get_starting_equity(tracker)
    usdc_balance: float | None = hb.get("usdc_balance") if hb else None
    if usdc_balance is None:
        usdc_balance = _fetch_usdc_balance()

    # Per-pair data — filtré sur les paires configurées uniquement
    pairs: dict[str, dict] = {}
    for symbol, ps in bot_state.items():
        if not isinstance(ps, dict):
            continue
        # Ignorer les paires non présentes dans crypto_pairs
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

        # Merge metrics.json per-pair if available
        mt_pair = (mt.get("pairs") or {}).get(symbol, {})
        # Trouver le real_pair correspondant pour affichage
        real_pair = next((p["real_pair"] for p in configured if p["backtest_pair"] == symbol), symbol)

        pairs[symbol] = {
            "real_pair":      real_pair,
            "in_position":    in_position,
            "entry_price":    entry,
            "spot_price":     spot,
            "qty":            qty,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pct": unrealized_pct,
            "stop_loss":      sl,
            "sl_dist_pct":    sl_dist_pct,
            "sl_placed":      bool(ps.get("sl_exchange_placed") or mt_pair.get("sl_placed")),
            "trailing_active": bool(ps.get("trailing_stop_activated", False)),
            "trailing_stop":  ps.get("trailing_stop"),
            "scenario":       ps.get("entry_scenario") or ps.get("last_best_params", {}).get("scenario", "—"),
            "timeframe":      ps.get("entry_timeframe") or ps.get("last_best_params", {}).get("timeframe", "—"),
            "last_execution": ps.get("last_execution"),
            "execution_count": int(ps.get("execution_count") or 0),
            "oos_blocked":    bool(ps.get("oos_blocked", mt_pair.get("oos_blocked", False))),
            "drawdown_halted": bool(ps.get("drawdown_halted", False)),
            "buy_timestamp":  ps.get("buy_timestamp"),
        }

    open_count = sum(1 for p in pairs.values() if p["in_position"])
    cumul_pnl, trade_count = _cumulative_pnl(real_pairs)

    return {
        "now":            datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "alive":          alive,
        "age_seconds":    age,
        "pid":            hb.get("pid", "—"),
        "circuit_mode":   hb.get("circuit_mode", "—"),
        "error_count":    hb.get("error_count", 0),
        "loop_counter":   hb.get("loop_counter", "—"),
        "emergency_halt": emergency_halt,
        "halt_reason":    halt_reason,
        "daily_pnl":      daily_pnl,
        "daily_pnl_pct":  daily_pnl_pct,
        "starting_equity": starting_equity,
        "usdc_balance":   usdc_balance,
        "open_count":     open_count,
        "cumul_pnl":      cumul_pnl,
        "trade_count":    trade_count,
        "pairs":          pairs,
        "api_latency_ms": mt.get("api_latency_ms"),
        "taker_fee":      mt.get("taker_fee"),
        "metrics_ts":     mt.get("timestamp_utc", "—"),
    }


# ─── HTML renderer ────────────────────────────────────────────────────────────

def _render_kpi(label: str, value: str, sub: str = "", accent: str = "") -> str:
    style = f'style="color:{accent}"' if accent else ""
    return f"""
    <div class="kpi-card">
      <div class="kpi-label">{label}</div>
      <div class="kpi-value" {style}>{value}</div>
      {"<div class='kpi-sub'>" + sub + "</div>" if sub else ""}
    </div>"""


def _render_position_card(symbol: str, p: dict) -> str:
    pnl = p["unrealized_pnl"]
    pct = p["unrealized_pct"]
    pnl_color = _pnl_color(pnl)
    pnl_str = _fmt_usdc(pnl) if pnl is not None else "—"
    pct_str = f"{pct:+.2f}%" if pct is not None else ""

    sl_dist = f"{p['sl_dist_pct']:.2f}%" if p["sl_dist_pct"] is not None else "—"
    trailing_str = _fmt_price(p["trailing_stop"]) if p["trailing_active"] else "Inactif"
    entry_ts = ""
    if p["buy_timestamp"]:
        try:
            dt = datetime.fromtimestamp(float(p["buy_timestamp"]))
            entry_ts = dt.strftime("%d/%m %H:%M")
        except Exception:
            pass

    sl_badge = _badge("✓ SL posé", "ok") if p["sl_placed"] else _badge("✗ SL absent", "danger")
    trail_badge = _badge("Trailing ON", "ok") if p["trailing_active"] else ""

    return f"""
    <div class="pos-card">
      <div class="pos-header">
        <span class="pos-symbol">{p.get('real_pair', symbol)}</span>
        <span style="font-size:.75rem;color:var(--text3);margin-left:4px">({symbol})</span>
        <span class="pos-badges">{sl_badge} {trail_badge}</span>
        {"<span class='pos-entry-ts'>" + entry_ts + "</span>" if entry_ts else ""}
      </div>
      <div class="pos-grid">
        <div class="pos-metric">
          <div class="pos-metric-label">Prix entrée</div>
          <div class="pos-metric-value">{_fmt_price(p['entry_price'])}</div>
        </div>
        <div class="pos-metric">
          <div class="pos-metric-label">Prix actuel</div>
          <div class="pos-metric-value accent">{_fmt_price(p['spot_price'])}</div>
        </div>
        <div class="pos-metric">
          <div class="pos-metric-label">PnL latent</div>
          <div class="pos-metric-value" style="color:{pnl_color}">{pnl_str}</div>
        </div>
        <div class="pos-metric">
          <div class="pos-metric-label">PnL %</div>
          <div class="pos-metric-value" style="color:{pnl_color}">{pct_str if pct_str else "—"}</div>
        </div>
        <div class="pos-metric">
          <div class="pos-metric-label">Stop-loss</div>
          <div class="pos-metric-value warn">{_fmt_price(p['stop_loss'])}</div>
        </div>
        <div class="pos-metric">
          <div class="pos-metric-label">Distance SL</div>
          <div class="pos-metric-value warn">{sl_dist}</div>
        </div>
        <div class="pos-metric">
          <div class="pos-metric-label">Trailing stop</div>
          <div class="pos-metric-value">{trailing_str}</div>
        </div>
        <div class="pos-metric">
          <div class="pos-metric-label">Scénario</div>
          <div class="pos-metric-value">{p['scenario']} / {p['timeframe']}</div>
        </div>
      </div>
    </div>"""


def _render_pairs_table(pairs: dict) -> str:
    rows = ""
    for symbol, p in pairs.items():
        if p["in_position"]:
            status_badge = _badge("● EN POSITION", "ok")
        elif p["oos_blocked"]:
            status_badge = _badge("OOS BLOQUÉ", "danger")
        elif p["drawdown_halted"]:
            status_badge = _badge("DD HALT", "danger")
        else:
            status_badge = _badge("En attente", "neutral")

        last_exec = p["last_execution"] or "—"
        if last_exec != "—":
            try:
                dt = datetime.fromisoformat(last_exec.replace("Z", "+00:00"))
                last_exec = dt.strftime("%d/%m %H:%M")
            except Exception:
                pass

        spot_str = _fmt_price(p["spot_price"]) if p["spot_price"] else "—"
        exec_cnt = p["execution_count"]

        rows += f"""
        <tr>
          <td class="td-symbol">{p['real_pair']}<br><span style="font-size:.72rem;color:var(--text3);font-weight:400">backtest: {symbol}</span></td>
          <td>{status_badge}</td>
          <td>{spot_str}</td>
          <td>{last_exec}</td>
          <td>{p['scenario']}</td>
          <td>{exec_cnt}</td>
        </tr>"""

    return f"""
    <table class="pairs-table">
      <thead>
        <tr>
          <th>Paire</th>
          <th>Statut</th>
          <th>Prix</th>
          <th>Dernier cycle</th>
          <th>Scénario</th>
          <th>Exécutions</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def render_html() -> str:
    d = collect_data()
    alive = d["alive"]

    # ── status bar ────────────────────────────────────────────────────────────
    bot_status_html = _status_dot(alive)
    age_str = f"{d['age_seconds']}s" if d["age_seconds"] < 999999 else "?"
    circuit = str(d["circuit_mode"]).upper()
    halt_banner = ""
    if d["emergency_halt"]:
        reason = f" — {d['halt_reason']}" if d["halt_reason"] else ""
        halt_banner = f'<div class="halt-banner">🛑 EMERGENCY HALT ACTIF{reason}</div>'

    # ── KPI row ───────────────────────────────────────────────────────────────
    daily_pnl_color = _pnl_color(d["daily_pnl"])
    daily_pnl_str   = _fmt_usdc(d["daily_pnl"])
    daily_pct_str   = f"{d['daily_pnl_pct']:+.2f}%" if d["daily_pnl_pct"] != 0 else ""

    usdc_val = f"{d['usdc_balance']:,.2f} USDC" if d["usdc_balance"] is not None else "—"

    kpi_row = (
        _render_kpi("Statut", bot_status_html, f"Cycle #{d['loop_counter']} · PID {d['pid']}")
        + _render_kpi("Solde USDC spot", usdc_val, "Solde libre Binance", accent="#60a5fa")
        + _render_kpi("PnL journalier", daily_pnl_str, daily_pct_str, accent=daily_pnl_color)
        + _render_kpi(
            "PnL cumulé",
            _fmt_usdc(d["cumul_pnl"]),
            f"{d['trade_count']} trade(s) fermé(s)",
            accent=_pnl_color(d["cumul_pnl"]),
        )
        + _render_kpi(
            "Positions ouvertes",
            str(d["open_count"]),
            f"sur {len(d['pairs'])} paire(s) suivie(s)",
            accent="#3b82f6" if d["open_count"] > 0 else "#94a3b8",
        )
    )

    # ── open positions ────────────────────────────────────────────────────────
    open_positions = [
        _render_position_card(sym, p)
        for sym, p in d["pairs"].items()
        if p["in_position"]
    ]
    if open_positions:
        positions_section = f"""
        <section>
          <h2 class="section-title">Positions actives</h2>
          <div class="positions-grid">{"".join(open_positions)}</div>
        </section>"""
    else:
        positions_section = """
        <section>
          <h2 class="section-title">Positions actives</h2>
          <div class="empty-state">Aucune position ouverte en ce moment.</div>
        </section>"""

    # ── pairs overview ────────────────────────────────────────────────────────
    pairs_overview = f"""
    <section>
      <h2 class="section-title">Suivi des paires</h2>
      {_render_pairs_table(d["pairs"])}
    </section>"""

    # ── footer ──────────────────────────────────────────────────────────────

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MULTI_ASSETS — Dashboard</title>
  <style>
    :root {{
      --bg:        #0a0e1a;
      --bg2:       #0f1629;
      --bg3:       #162040;
      --border:    #1e2d4f;
      --border2:   #253660;
      --text:      #e2e8f0;
      --text2:     #94a3b8;
      --text3:     #64748b;
      --accent:    #3b82f6;
      --accent2:   #60a5fa;
      --ok:        #10b981;
      --warn:      #f59e0b;
      --danger:    #ef4444;
      --neutral:   #475569;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }}

    /* ── Top header ── */
    .topbar {{
      display: grid;
      grid-template-columns: 1fr auto 1fr;
      align-items: center;
      padding: 14px 28px;
      background: var(--bg2);
      border-bottom: 1px solid var(--border);
      gap: 12px;
    }}
    .topbar-logo {{ display: flex; align-items: center; gap: 10px; }}
    .topbar-logo svg {{ flex-shrink: 0; }}
    .topbar-logo-text {{ font-size: .78rem; color: var(--text3); line-height: 1.4; }}
    .topbar-logo-text strong {{ color: var(--accent2); font-size: 1.05rem; }}
    .topbar-center {{ text-align: center; }}
    .topbar-title {{ font-size: 1.75rem; font-weight: 700; color: var(--text); letter-spacing: .04em; }}
    .topbar-right {{ font-size: .78rem; color: var(--text3); text-align: right; line-height: 1.7; }}

    /* ── Status dot ── */
    .dot {{
      display: inline-block; width: 10px; height: 10px;
      border-radius: 50%; margin-right: 6px; vertical-align: middle;
    }}
    .dot-alive {{ background: var(--ok); box-shadow: 0 0 6px var(--ok); animation: pulse 2s infinite; }}
    .dot-dead  {{ background: var(--danger); }}
    @keyframes pulse {{
      0%, 100% {{ box-shadow: 0 0 4px var(--ok); }}
      50%       {{ box-shadow: 0 0 12px var(--ok); }}
    }}
    .status-label {{ font-weight: 600; font-size: .85rem; vertical-align: middle; }}

    /* ── Halt banner ── */
    .halt-banner {{
      background: linear-gradient(90deg, #7f1d1d, #991b1b);
      color: #fca5a5; padding: 10px 28px; font-weight: 600; font-size: .9rem;
      border-bottom: 1px solid #ef4444;
    }}

    /* ── Main content ── */
    .main {{ padding: 24px 28px; max-width: 1400px; margin: 0 auto; }}

    /* ── KPI row ── */
    .kpi-row {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 14px;
      margin-bottom: 28px;
    }}
    .kpi-card {{
      background: var(--bg2);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px 20px;
      transition: border-color .2s;
    }}
    .kpi-card:hover {{ border-color: var(--border2); }}
    .kpi-label {{ font-size: .75rem; color: var(--text3); text-transform: uppercase; letter-spacing: .06em; margin-bottom: 8px; }}
    .kpi-value {{ font-size: 1.5rem; font-weight: 700; color: var(--text); line-height: 1; }}
    .kpi-sub   {{ font-size: .78rem; color: var(--text2); margin-top: 6px; }}

    /* ── Section titles ── */
    .section-title {{
      font-size: .9rem; font-weight: 600; color: var(--accent2);
      text-transform: uppercase; letter-spacing: .08em;
      margin-bottom: 14px; padding-bottom: 8px;
      border-bottom: 1px solid var(--border);
    }}
    section {{ margin-bottom: 32px; }}

    /* ── Open position cards ── */
    .positions-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(480px, 1fr));
      gap: 16px;
    }}
    .pos-card {{
      background: var(--bg2);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px 20px;
      border-left: 3px solid var(--accent);
    }}
    .pos-header {{
      display: flex; align-items: center; gap: 10px;
      margin-bottom: 14px; flex-wrap: wrap;
    }}
    .pos-symbol {{
      font-size: 1.1rem; font-weight: 700; color: var(--accent2);
    }}
    .pos-badges {{ display: flex; gap: 6px; flex-wrap: wrap; }}
    .pos-entry-ts {{ margin-left: auto; font-size: .75rem; color: var(--text3); }}
    .pos-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
    }}
    .pos-metric {{}}
    .pos-metric-label {{ font-size: .7rem; color: var(--text3); text-transform: uppercase; letter-spacing: .05em; margin-bottom: 3px; }}
    .pos-metric-value {{ font-size: .95rem; font-weight: 600; color: var(--text); }}
    .pos-metric-value.accent {{ color: var(--accent2); }}
    .pos-metric-value.warn   {{ color: var(--warn); }}

    /* ── Pairs table ── */
    .pairs-table {{
      width: 100%; border-collapse: collapse; font-size: .875rem;
    }}
    .pairs-table thead th {{
      padding: 10px 14px;
      text-align: left;
      background: var(--bg3);
      color: var(--text2);
      font-weight: 600;
      font-size: .75rem;
      text-transform: uppercase;
      letter-spacing: .06em;
      border-bottom: 1px solid var(--border);
    }}
    .pairs-table tbody tr {{
      border-bottom: 1px solid var(--border);
      transition: background .15s;
    }}
    .pairs-table tbody tr:hover {{ background: var(--bg3); }}
    .pairs-table td {{ padding: 11px 14px; color: var(--text); }}
    .td-symbol {{ font-weight: 700; color: var(--accent2); }}

    /* ── Badges ── */
    .badge {{
      display: inline-block; padding: 2px 8px; border-radius: 4px;
      font-size: .72rem; font-weight: 600; letter-spacing: .03em;
    }}
    .badge-ok      {{ background: rgba(16,185,129,.15); color: #34d399; border: 1px solid rgba(16,185,129,.3); }}
    .badge-warn    {{ background: rgba(245,158,11,.15);  color: #fbbf24; border: 1px solid rgba(245,158,11,.3); }}
    .badge-danger  {{ background: rgba(239,68,68,.15);   color: #f87171; border: 1px solid rgba(239,68,68,.3); }}
    .badge-neutral {{ background: rgba(71,85,105,.20);   color: #94a3b8; border: 1px solid rgba(71,85,105,.3); }}

    /* ── Empty state ── */
    .empty-state {{
      background: var(--bg2); border: 1px dashed var(--border);
      border-radius: 10px; padding: 32px; text-align: center;
      color: var(--text3); font-size: .9rem;
    }}

    /* ── Footer ── */
    .footer {{
      padding: 14px 28px;
      background: var(--bg2);
      border-top: 1px solid var(--border);
      display: flex; justify-content: space-between; align-items: center;
      font-size: .75rem; color: var(--text3);
    }}
    .footer-right {{ display: flex; gap: 20px; }}
    #countdown {{ color: var(--accent2); font-weight: 700; }}
  </style>
</head>
<body>

    <div class="topbar">
    <div class="topbar-logo">
      <svg width="38" height="38" viewBox="0 0 38 38" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect width="38" height="38" rx="8" fill="#1e2d4f"/>
        <polygon points="19,6 30,13 30,25 19,32 8,25 8,13" fill="none" stroke="#3b82f6" stroke-width="1.8"/>
        <polygon points="19,11 26,15 26,23 19,27 12,23 12,15" fill="#1e3a6e" stroke="#60a5fa" stroke-width="1"/>
        <line x1="19" y1="11" x2="19" y2="27" stroke="#60a5fa" stroke-width="1.2"/>
        <line x1="12" y1="15" x2="26" y2="23" stroke="#3b82f6" stroke-width="1"/>
        <line x1="26" y1="15" x2="12" y2="23" stroke="#3b82f6" stroke-width="1"/>
        <circle cx="19" cy="19" r="2.5" fill="#60a5fa"/>
      </svg>
      <div class="topbar-logo-text">
        <strong>MULTI_ASSETS</strong><br>
        Trading System
      </div>
    </div>

    <div class="topbar-center">
      <div class="topbar-title">Dashboard</div>
    </div>

    <div class="topbar-right">
      Circuit breaker : <strong style="color:{'var(--ok)' if circuit == 'RUNNING' else 'var(--warn)'}">{circuit}</strong> &nbsp;·&nbsp;
      Dernier cycle : <strong style="color:{'var(--ok)' if alive else 'var(--danger)'}">{age_str if d['age_seconds'] < 999999 else 'jamais'}</strong> &nbsp;·&nbsp;
      Erreurs : <strong style="color:{'var(--danger)' if d['error_count'] > 0 else 'var(--text)'}">{d["error_count"]}</strong><br>
      <span style="color:var(--text3)">{d["now"]}</span>
    </div>
  </div>

  {halt_banner}

  <div class="main">
    <div class="kpi-row">{kpi_row}</div>
    {positions_section}
    {pairs_overview}
  </div>

  <div class="footer">
    <div class="footer-right">
      <span>Rafraîchissement dans <span id="countdown">10</span>s</span>
    </div>
  </div>

  <script>
    let t = 10;
    const el = document.getElementById("countdown");
    setInterval(() => {{
      t--;
      if (t <= 0) {{ location.reload(); }}
      el.textContent = t;
    }}, 1000);
  </script>
</body>
</html>"""


# ─── HTTP handler ─────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/", "/dashboard"):
            body = render_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/data":
            data = collect_data()
            body = json.dumps(data, default=str, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # silencieux


# ─── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), DashboardHandler)
    print(f"[DASHBOARD] Serveur démarré → http://127.0.0.1:{PORT}/dashboard")
    print("[DASHBOARD] Ctrl+C pour arrêter.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[DASHBOARD] Arrêté.")
        sys.exit(0)

