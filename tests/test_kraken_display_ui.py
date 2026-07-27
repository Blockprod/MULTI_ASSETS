from __future__ import annotations

import importlib.util
import os
import sys
from io import StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

from rich.console import Console


ROOT = Path(__file__).resolve().parents[1]
KRAKEN_SRC = ROOT / "code" / "src" / "kraken_bot"


def _load_kraken_display_ui() -> ModuleType:
    os.environ.setdefault("KRAKEN_API_KEY", "ci_dummy_key")
    os.environ.setdefault("KRAKEN_SECRET_KEY", "ci_dummy_secret")
    os.environ.setdefault("KRAKEN_API_URL", "https://api.kraken.test")
    os.environ.setdefault("KRAKEN_WS_URL", "wss://ws-auth.kraken.test/v2")

    saved_path = list(sys.path)
    saved_modules: dict[str, ModuleType] = {}
    for name in ("bot_config", "exchange_client"):
        module = sys.modules.get(name)
        if module is not None:
            saved_modules[name] = module
    try:
        for name in ("bot_config", "exchange_client"):
            sys.modules.pop(name, None)
        sys.path.insert(0, str(KRAKEN_SRC))
        spec = importlib.util.spec_from_file_location(
            "kraken_display_ui_test_module",
            KRAKEN_SRC / "display_ui.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path
        for name in ("bot_config", "exchange_client"):
            if name in saved_modules:
                sys.modules[name] = saved_modules[name]
            else:
                sys.modules.pop(name, None)


def _render(panel) -> str:
    console = Console(file=StringIO(), record=True, width=180)
    console.print(panel)
    return console.export_text()


def test_kraken_tracking_panel_identifies_pair_and_live_mode() -> None:
    display_ui = _load_kraken_display_ui()

    text = _render(display_ui.build_tracking_panel(
        {
            "pair_symbol": "XRPUSDC",
            "broker_symbol": "XRP/USDC",
            "backtest_display_status": "termine",
            "wf_session_status": "validated",
            "wf_status": "validated",
            "entries_ready": True,
            "live_execution_mode": "TRADABLE",
            "display_next_live": "2026-06-30 18:02:00",
            "display_next_wf": "2026-06-30 19:00:30",
            "history_status": {
                "1h": {
                    "oldest_available": "2026-05-30T14:00:00+00:00",
                    "newest_available": "2026-06-30T13:00:00+00:00",
                    "bars_available": 726,
                    "bars_required": 1500,
                    "source": "api",
                }
            },
            "effective_backtest_fee_taker": 0.004,
            "effective_backtest_fee_maker": 0.0025,
            "effective_live_fee_taker": 0.004,
            "effective_live_fee_maker": 0.0025,
            "kraken_fee_source": "api",
            "active_strategy": {
                "snapshot_id": "krk123",
                "timeframe": "1h",
                "stoch_buy_min": 0.08,
                "stoch_buy_max": 0.80,
                "stoch_sell_exit": 0.40,
                "wf_folds_completed": 4,
                "wf_folds_requested": 4,
            },
        },
        "2026-06-30 18:00:00",
    ))

    assert "SUIVI D'EXECUTION & PLANIFICATION AUTOMATIQUE" in text
    assert "XRPUSDC" in text
    assert "XRP/USDC" in text
    assert "Backtest IS" in text
    assert "WF session" in text
    assert "Trading Live" in text
    assert "TRADABLE" in text
    assert "Plage donnees" in text
    assert "Bougies WF" in text
    assert "Frais backtest/live" in text
    assert "Source frais" in text
    assert "API" in text
    assert "PAIRE NON IDENTIFIEE" not in text


def test_kraken_bot_active_banner_shows_partial_tradable() -> None:
    display_ui = _load_kraken_display_ui()
    console = Console(file=StringIO(), record=True, width=180)

    display_ui.display_bot_active_banner(
        3,
        None,
        console,
        schedule_status={
            "display_next_live": "2026-06-30 18:02:00",
            "display_next_wf": "2026-06-30 19:00:30",
            "system_status": "PARTIAL_TRADABLE 1/2",
            "system_status_detail": "tradable=XRPUSDC | protection_only=ONDOUSD",
        },
    )
    text = console.export_text()

    assert "PARTIAL_TRADABLE 1/2" in text
    assert "tradable=XRPUSDC | protection_only=ONDOUSD" in text


def test_kraken_bot_active_banner_keeps_no_buy_and_halt_statuses() -> None:
    display_ui = _load_kraken_display_ui()
    console = Console(file=StringIO(), record=True, width=180)

    display_ui.display_bot_active_banner(
        3,
        None,
        console,
        schedule_status={
            "display_next_live": "2026-06-30 18:02:00",
            "display_next_wf": "2026-06-30 19:00:30",
            "system_status": "PROTECTION_ONLY / NO-BUY",
            "system_status_detail": "aucune paire Kraken avec WF courant valide 4/4",
        },
    )
    display_ui.display_bot_active_banner(
        3,
        None,
        console,
        schedule_status={
            "display_next_live": "2026-06-30 18:02:00",
            "display_next_wf": "2026-06-30 19:00:30",
            "system_status": "EMERGENCY_HALT / NO-BUY",
            "system_status_detail": "reconciliation requise",
        },
    )
    text = console.export_text()

    assert "PROTECTION_ONLY / NO-BUY" in text
    assert "EMERGENCY_HALT / NO-BUY" in text
    assert "reconciliation requise" in text


def test_kraken_backtest_titles_match_binance_diagnostic_style() -> None:
    display_ui = _load_kraken_display_ui()
    console = Console(file=StringIO(), record=True, width=180)
    results = [
        {
            "scenario": "StochRSI",
            "timeframe": "1h",
            "ema_periods": (26, 50),
            "initial_wallet": 1000.0,
            "final_wallet": 1040.0,
            "max_drawdown": 0.02,
            "win_rate": 60.0,
            "trades": [1] * 12,
        }
    ]

    display_ui.display_results_for_pair("XRPUSDC", results, console=console)
    display_ui.display_backtest_table("XRPUSDC", results, console=console)
    text = console.export_text()

    assert "DIAGNOSTIC BACKTEST IS" in text
    assert "Resultats IS diagnostic" in text
    assert "Backtest IS diagnostic" in text
    assert "Capital depart" in text
    assert "Capital final" in text
    assert "$1,000.00" in text
    assert "$1,040.00" in text
    assert "MEILLEUR RESULTAT IS" not in text
    assert "Backtest IS Results" not in text


def test_kraken_balance_panel_uses_kraken_label_from_env(monkeypatch) -> None:
    monkeypatch.setenv("BROKER", "KRAKEN")
    display_ui = _load_kraken_display_ui()
    console = Console(file=StringIO(), record=True, width=180)
    account_info = {
        "balances": [
            {"asset": "USDC", "free": "254.39", "locked": "0"},
            {"asset": "XRP", "free": "0", "locked": "0"},
        ]
    }
    client = SimpleNamespace(
        get_account=lambda: account_info,
        get_symbol_ticker=lambda symbol: {"price": "77.60"},
    )

    display_ui.display_account_balances_panel(
        account_info,
        "XRP",
        "USDC",
        client,
        console,
        pair_state={},
    )
    text = console.export_text()

    assert "Solde global Kraken" in text
    assert "Solde global Binance" not in text
