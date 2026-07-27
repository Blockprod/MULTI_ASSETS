from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
KRAKEN_SRC = ROOT / "code" / "src" / "kraken_bot"


def _load_kraken_backtest_orchestrator() -> ModuleType:
    saved_path = list(sys.path)
    saved_modules: dict[str, ModuleType] = {}
    for name in ("exchange_client", "backtest_orchestrator"):
        module = sys.modules.get(name)
        if module is not None:
            saved_modules[name] = module
            sys.modules.pop(name, None)
    try:
        sys.path.insert(0, str(KRAKEN_SRC))
        spec = importlib.util.spec_from_file_location(
            "kraken_backtest_orchestrator_test_module",
            KRAKEN_SRC / "backtest_orchestrator.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path
        for name in ("exchange_client", "backtest_orchestrator"):
            if name in saved_modules:
                sys.modules[name] = saved_modules[name]
            else:
                sys.modules.pop(name, None)


def test_kraken_protection_log_symbol_fallback() -> None:
    orchestrator = _load_kraken_backtest_orchestrator()
    deps = SimpleNamespace(client=SimpleNamespace(broker="KRAKEN"))

    assert orchestrator._broker_display_symbol(deps, "XRPUSDC", "XRPUSDC", {}) == "XRP/USDC"
    assert orchestrator._broker_display_symbol(deps, "ONDOUSD", "ONDOUSD", {}) == "ONDO/USD"


def test_kraken_oos_gate_blocks_without_email_even_if_alert_requested(monkeypatch) -> None:
    orchestrator = _load_kraken_backtest_orchestrator()
    monkeypatch.syspath_prepend(str(KRAKEN_SRC))

    send_alert = MagicMock()
    save_state = MagicMock()
    deps = SimpleNamespace(
        bot_state={},
        bot_state_lock=threading.RLock(),
        config=SimpleNamespace(
            oos_strict_mode=True,
            oos_sharpe_min=0.3,
            oos_win_rate_min=30.0,
            backtest_throttle_seconds=3600.0,
        ),
        client=SimpleNamespace(broker="KRAKEN"),
        save_fn=save_state,
        send_alert_fn=send_alert,
        oos_alert_last_sent={},
        oos_alert_lock=threading.Lock(),
    )
    results = [{"sharpe_ratio": 0.0, "win_rate": 0.0, "max_drawdown": 10.0}]

    pool, blocked = orchestrator._apply_oos_quality_gate(
        results,
        "ONDOUSD",
        deps,
        log_tag="TEST",
        send_alert=True,
        save_force=True,
    )

    assert pool == results
    assert blocked is True
    assert deps.bot_state["ONDOUSD"]["oos_blocked"] is True
    assert "oos_alert_sent_ts" not in deps.bot_state["ONDOUSD"]
    assert deps.oos_alert_last_sent == {}
    save_state.assert_called_once_with(force=True)
    send_alert.assert_not_called()
