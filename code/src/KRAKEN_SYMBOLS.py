"""Kraken Pro Spot entrypoint for the isolated Kraken runtime."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


os.environ["BROKER"] = "KRAKEN"
_SRC_DIR = Path(__file__).resolve().parent
_KRAKEN_BOT_DIR = Path(__file__).resolve().parent / "kraken_bot"
_KRAKEN_BOT_PATH = str(_KRAKEN_BOT_DIR)
_DEFAULT_DATA_DIR = _SRC_DIR.parent.parent / "data"
if _DEFAULT_DATA_DIR.exists():
    os.environ.setdefault("KRAKEN_HISTORICAL_DATA_DIR", str(_DEFAULT_DATA_DIR))
sys.path[:] = [
    p for p in sys.path
    if Path(p or os.curdir).resolve() not in {_SRC_DIR, _KRAKEN_BOT_DIR}
]
sys.path.insert(0, _KRAKEN_BOT_PATH)

_KRAKEN_LOCAL_MODULES = {
    "backtest_orchestrator",
    "backtest_runner",
    "broker_models",
    "bot_config",
    "cache_manager",
    "constants",
    "correlation_guard",
    "cython_integrity",
    "data_fetcher",
    "display_ui",
    "email_utils",
    "email_templates",
    "error_handler",
    "exceptions",
    "exchange_client",
    "indicators_engine",
    "kraken_client",
    "market_analysis",
    "metrics",
    "order_manager",
    "position_reconciler",
    "position_sizing",
    "signal_generator",
    "state_manager",
    "strategy_policy",
    "timestamp_utils",
    "trade_helpers",
    "trade_journal",
    "wal_logger",
    "walk_forward",
    "watchdog",
}
for _module_name in _KRAKEN_LOCAL_MODULES:
    sys.modules.pop(_module_name, None)

if __name__ == "__main__":
    runpy.run_path(str(_KRAKEN_BOT_DIR / "runner.py"), run_name="__main__")
