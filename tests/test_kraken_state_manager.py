from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
KRAKEN_SRC = ROOT / "code" / "src" / "kraken_bot"


def _load_kraken_state_manager() -> ModuleType:
    os.environ.setdefault("KRAKEN_SECRET_KEY", "test-secret")
    saved_path = list(sys.path)
    saved_modules: dict[str, ModuleType] = {}
    for name in ("bot_config", "exceptions", "state_manager"):
        module = sys.modules.get(name)
        if module is not None:
            saved_modules[name] = module
            sys.modules.pop(name, None)
    try:
        sys.path.insert(0, str(KRAKEN_SRC))
        spec = importlib.util.spec_from_file_location(
            "kraken_state_manager_test_module",
            KRAKEN_SRC / "state_manager.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path
        for name in ("bot_config", "exceptions", "state_manager"):
            if name in saved_modules:
                sys.modules[name] = saved_modules[name]
            else:
                sys.modules.pop(name, None)


def test_kraken_state_schema_accepts_runtime_keys(caplog) -> None:
    state_manager = _load_kraken_state_manager()
    state = {
        "XRPUSDC": {
            "broker": "kraken",
            "broker_symbol": "XRP/USDC",
            "broker_pair_status": "online",
            "broker_pair_candidates": [],
            "effective_backtest_fee_taker": 0.0026,
            "effective_backtest_fee_maker": 0.0016,
            "effective_live_fee_taker": 0.0026,
            "effective_live_fee_maker": 0.0016,
            "kraken_fee_source": "api",
            "kraken_fee_api_taker": 0.0026,
            "kraken_fee_api_maker": 0.0016,
            "kraken_fee_error": None,
            "history_status": {"1h": {"bars_available": 730}},
            "runtime_phase": "protection_only",
            "live_execution_mode": "PROTECTION_ONLY",
            "wf_session_status": "not_validated",
            "backtest_display_status": "terminated",
            "pair_symbol": "XRPUSDC",
            "backtest_pair": "XRPUSDC",
            "real_trading_pair": "XRPUSDC",
        },
        "kraken_preflight": {
            "public_ok": True,
            "balance_ok": True,
            "open_orders_ok": True,
            "closed_orders_ok": True,
            "private_api_ok": True,
            "permission_error": None,
            "nonce_error": None,
            "tradable": True,
        },
        "kraken_private_api_ok": True,
    }

    with caplog.at_level(logging.WARNING, logger="trading_bot"):
        state_manager.validate_bot_state(state)

    assert "STATE C-17" not in caplog.text
