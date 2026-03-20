"""
compute_kelly.py — Half-Kelly criterion calculator from trade_journal.jsonl
===========================================================================

Reads the live trade journal and computes the half-Kelly fraction f*:
    f* = WinRate × (avg_win / avg_loss) × 0.5

Compares the result with the current risk_per_trade in bot_config.py
and prints a calibration report.

Usage:
    .venv\\Scripts\\python.exe code/scripts/compute_kelly.py

Requirements:
    - trade_journal.jsonl must exist in code/src/logs/
    - Minimum 50 closed trades (side='sell' with pnl != None)
    - No modification to any production file is performed

Output:
    Calibration report printed to stdout. Human decision required to
    update risk_per_trade in bot_config.py.
"""

import os
import sys

# Resolve paths relative to this script's location
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_SCRIPT_DIR, "..", "src")
_LOGS_DIR = os.path.join(_SRC_DIR, "logs")

sys.path.insert(0, _SRC_DIR)

from trade_journal import journal_summary  # noqa: E402

# Current value from bot_config.py:73 — update here if it changes
RISK_PER_TRADE_CONFIG = 0.055
MIN_TRADES_WARNING = 50
MIN_TRADES_RELIABLE = 100


def compute_half_kelly(logs_dir: str) -> None:
    """Read journal, compute half-Kelly, and print calibration report."""
    summary = journal_summary(logs_dir)

    total_sells = summary.get("total_sells", 0)
    win_count = summary.get("win_count", 0)
    loss_count = summary.get("loss_count", 0)
    win_rate = summary.get("win_rate", 0.0)  # percentage 0-100
    avg_win = summary.get("avg_win", 0.0)    # absolute USDC
    avg_loss = summary.get("avg_loss", 0.0)  # absolute USDC (negative)
    total_pnl = summary.get("total_pnl", 0.0)
    best_trade = summary.get("best_trade", 0.0)
    worst_trade = summary.get("worst_trade", 0.0)

    print("=" * 60)
    print("  HALF-KELLY CALIBRATION REPORT")
    print("  Journal:", os.path.join(logs_dir, "trade_journal.jsonl"))
    print("=" * 60)
    print()

    # --- Data quality checks ---
    if total_sells == 0:
        print("❌ No closed trades found in journal.")
        print("   Run the bot in DEMO or LIVE mode to accumulate trades.")
        return

    if total_sells < MIN_TRADES_WARNING:
        print(f"⚠️  Only {total_sells} closed trades found.")
        print(f"   Minimum recommended: {MIN_TRADES_WARNING} (reliable: {MIN_TRADES_RELIABLE}).")
        print("   Results below are indicative only — do NOT update config yet.\n")
    elif total_sells < MIN_TRADES_RELIABLE:
        print(f"⚠️  {total_sells} closed trades found (< {MIN_TRADES_RELIABLE} for high reliability).")
        print("   Treat the calibration as informative, not definitive.\n")
    else:
        print(f"✅  {total_sells} closed trades — sufficient for reliable calibration.\n")

    # --- Raw statistics ---
    wr_decimal = win_rate / 100.0
    avg_loss_abs = abs(avg_loss) if avg_loss != 0.0 else None

    print("── Journal statistics ──────────────────────────────")
    print(f"  Total closed trades : {total_sells}")
    print(f"  Wins                : {win_count}")
    print(f"  Losses              : {loss_count}")
    print(f"  Win rate            : {win_rate:.1f}%")
    print(f"  Avg win  (USDC)     : {avg_win:+.4f}")
    print(f"  Avg loss (USDC)     : {avg_loss:+.4f}")
    print(f"  Total PnL (USDC)    : {total_pnl:+.4f}")
    print(f"  Best trade  (USDC)  : {best_trade:+.4f}")
    print(f"  Worst trade (USDC)  : {worst_trade:+.4f}")
    print()

    # --- Kelly computation ---
    print("── Half-Kelly computation ──────────────────────────")

    if avg_loss_abs is None or avg_loss_abs == 0.0:
        print("❌ Cannot compute Kelly: avg_loss is zero (no losing trades or all pnl=0).")
        print("   This is statistically unusual — check the journal data quality.")
        return

    if avg_win <= 0.0:
        print("❌ Cannot compute Kelly: avg_win is zero or negative.")
        print("   No profitable trades recorded yet.")
        return

    win_loss_ratio = avg_win / avg_loss_abs
    full_kelly = wr_decimal * win_loss_ratio - (1.0 - wr_decimal)
    half_kelly = wr_decimal * win_loss_ratio * 0.5

    print(f"  Win/loss ratio      : {win_loss_ratio:.4f}")
    print(f"  Full Kelly (f)      : {full_kelly:.4f}  ({full_kelly * 100:.2f}% of capital)")
    print(f"  Half-Kelly (f*)     : {half_kelly:.4f}  ({half_kelly * 100:.2f}% of capital)")
    print()

    # --- Comparison vs current config ---
    print("── Comparison vs current config ────────────────────")
    print(f"  risk_per_trade (config) : {RISK_PER_TRADE_CONFIG:.4f}  ({RISK_PER_TRADE_CONFIG * 100:.2f}%)")
    print(f"  Half-Kelly (f*)         : {half_kelly:.4f}  ({half_kelly * 100:.2f}%)")

    delta = half_kelly - RISK_PER_TRADE_CONFIG
    delta_pct = (delta / RISK_PER_TRADE_CONFIG) * 100.0

    if abs(delta) < 0.005:
        print(f"  → Config is well-calibrated (Δ = {delta:+.4f}, {delta_pct:+.1f}%).")
        print()
    elif half_kelly < RISK_PER_TRADE_CONFIG:
        print(f"  → ⚠️  Config is ABOVE half-Kelly by {abs(delta):.4f} ({abs(delta_pct):.1f}%).")
        print(f"       Consider reducing risk_per_trade from {RISK_PER_TRADE_CONFIG:.3f} to ~{half_kelly:.4f}")
        print("       in code/src/bot_config.py:73 after validating on more trades.")
        print()
    else:
        print(f"  → ℹ️  Config is BELOW half-Kelly by {abs(delta):.4f} ({abs(delta_pct):.1f}%).")
        print(f"       Half-Kelly suggests up to {half_kelly:.4f} — cautious increase possible.")
        print("       Do NOT increase beyond half-Kelly without walk-forward OOS validation.")
        print()

    # --- Safety checks ---
    print("── Safety gates ────────────────────────────────────")
    if full_kelly <= 0:
        print("  🔴 Full Kelly is negative — strategy has negative expected value.")
        print("     Do NOT trade. Review signal logic before any sizing change.")
    elif full_kelly < 0.02:
        print("  🟠 Full Kelly is very low (< 2%). Edge is thin.")
        print("     Keep risk_per_trade conservative until more data available.")
    elif half_kelly > 0.15:
        print("  🟠 Half-Kelly > 15% — unusually high. Possible look-ahead in journal data?")
        print("     Verify journal integrity before updating config.")
    else:
        print("  ✅ Kelly values within reasonable bounds.")

    print()
    print("=" * 60)
    print("  ⚠️  DECISION REQUIRED: update bot_config.py manually.")
    print("      File: code/src/bot_config.py:73")
    print("      Key:  risk_per_trade")
    print("      This script does NOT modify any production file.")
    print("=" * 60)


if __name__ == "__main__":
    # Allow overriding the logs directory via CLI argument
    logs_dir = sys.argv[1] if len(sys.argv) > 1 else _LOGS_DIR

    if not os.path.isdir(logs_dir):
        print(f"❌ Logs directory not found: {logs_dir}")
        print("   Usage: .venv\\Scripts\\python.exe code/scripts/compute_kelly.py [logs_dir]")
        sys.exit(1)

    compute_half_kelly(logs_dir)
