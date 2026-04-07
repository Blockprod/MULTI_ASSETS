"""
Trade Journal — Structured trade logging for audit & analysis (P5).
=================================================================

Writes one JSON object per line to ``trade_journal.jsonl``.
Each record captures:
  - timestamp (ISO 8601 UTC)
  - pair, side, quantity, price, fee, slippage
  - signal source (scenario, timeframe, EMA params)
  - risk metrics at entry (ATR, stop distance, position %)
  - PnL at close (if sell)

The file is append-only and safe for concurrent reads.
"""

import json
import os
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any

_CURRENT_JOURNAL = "trade_journal.jsonl"  # active file — renamed at month boundary

logger = logging.getLogger("trade_journal")

_journal_lock = threading.Lock()


def _get_journal_path(logs_dir: str) -> str:
    """Return the path to the active trade journal file."""
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, _CURRENT_JOURNAL)


def _maybe_rotate_journal(logs_dir: str) -> None:
    """Rename trade_journal.jsonl to journal_YYYY-MM.jsonl if a new month has started.

    Detection: reads the first record's timestamp from the current file.
    If that month differs from today's month, the file is archived.
    Safe to call on every write — O(1) when no rotation needed.
    """
    current_file = os.path.join(logs_dir, _CURRENT_JOURNAL)
    if not os.path.exists(current_file):
        return
    try:
        with open(current_file, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
        if not first_line:
            return  # Empty file, nothing to rotate
        first_record = json.loads(first_line)
        ts_str = first_record.get("ts", "")
        if not ts_str:
            return
        ts = datetime.fromisoformat(ts_str)
        file_month = (ts.year, ts.month)
        now = datetime.now(timezone.utc)
        current_month = (now.year, now.month)
        if file_month != current_month:
            archive_name = f"journal_{ts.year:04d}-{ts.month:02d}.jsonl"
            archive_path = os.path.join(logs_dir, archive_name)
            os.rename(current_file, archive_path)
            logger.info(
                "[JOURNAL] Rotation mensuelle: trade_journal.jsonl -> %s", archive_name
            )
    except Exception as e:
        logger.warning("[JOURNAL] Rotation mensuelle échouée (non-bloquant): %s", e)


def log_trade(
    logs_dir: str,
    *,
    pair: str,
    side: str,
    quantity: float,
    price: float,
    fee: float = 0.0,
    slippage: float = 0.0,
    scenario: str = "",
    timeframe: str = "",
    ema1: Optional[int] = None,
    ema2: Optional[int] = None,
    atr_value: Optional[float] = None,
    stop_price: Optional[float] = None,
    pnl: Optional[float] = None,
    pnl_pct: Optional[float] = None,
    equity_before: Optional[float] = None,
    equity_after: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """Append a trade record to the journal.

    Returns True on success, False on write error.
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "pair": pair,
        "side": side,
        "qty": quantity,
        "price": price,
        "fee": fee,
        "slippage": slippage,
        "scenario": scenario,
        "timeframe": timeframe,
        "ema1": ema1,
        "ema2": ema2,
        "atr": atr_value,
        "stop": stop_price,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "equity_before": equity_before,
        "equity_after": equity_after,
    }
    if extra:
        record.update(extra)

    try:
        with _journal_lock:
            _maybe_rotate_journal(logs_dir)
            path = _get_journal_path(logs_dir)
            line = json.dumps(record, default=str) + "\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        return True
    except Exception as e:
        logger.error(f"[JOURNAL] Failed to write trade: {e}")
        return False


def read_journal(logs_dir: str, last_n: Optional[int] = None) -> list:
    """Read trade journal entries. Optionally return only last N records."""
    path = _get_journal_path(logs_dir)
    if not os.path.exists(path):
        return []
    records = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        if last_n is not None:
            records = records[-last_n:]
    except Exception as e:
        logger.error(f"[JOURNAL] Failed to read: {e}")
    return records


def journal_summary(logs_dir: str) -> Dict[str, Any]:
    """Compute summary statistics from the trade journal."""
    records = read_journal(logs_dir)
    if not records:
        return {"total_trades": 0}

    sells = [r for r in records if r.get("side") == "sell" and r.get("pnl") is not None]
    pnls = [r["pnl"] for r in sells]

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    return {
        "total_trades": len(records),
        "total_sells": len(sells),
        "total_pnl": sum(pnls) if pnls else 0.0,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": (len(wins) / len(sells) * 100) if sells else 0.0,
        "avg_win": (sum(wins) / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "best_trade": max(pnls) if pnls else 0.0,
        "worst_trade": min(pnls) if pnls else 0.0,
    }
