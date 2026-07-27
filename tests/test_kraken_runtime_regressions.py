from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import importlib
import json
import logging
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
KRAKEN_DIR = ROOT / "code" / "src" / "kraken_bot"

KRAKEN_MODULES = {
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
    "email_templates",
    "email_utils",
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
    "runner",
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


@contextmanager
def kraken_import_context(monkeypatch, *, bot_mode: str = "DEMO", sizing_mode: str = "risk"):
    old_modules: dict[str, ModuleType | None] = {
        name: sys.modules.get(name) for name in KRAKEN_MODULES
    }
    for name in KRAKEN_MODULES:
        sys.modules.pop(name, None)
    monkeypatch.syspath_prepend(str(KRAKEN_DIR))
    monkeypatch.setenv("BROKER", "KRAKEN")
    monkeypatch.setenv("BOT_MODE", bot_mode)
    monkeypatch.setenv("SIZING_MODE", sizing_mode)
    monkeypatch.setenv("KRAKEN_API_KEY", "test-key")
    monkeypatch.setenv("KRAKEN_SECRET_KEY", "dGVzdC1zZWNyZXQ=")
    monkeypatch.setenv("SENDER_EMAIL", "sender@example.test")
    monkeypatch.setenv("RECEIVER_EMAIL", "receiver@example.test")
    monkeypatch.setenv("GOOGLE_MAIL_PASSWORD", "password")
    try:
        yield
    finally:
        for name in KRAKEN_MODULES:
            old_module = old_modules[name]
            if old_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old_module


def _import_kraken_module(name: str) -> Any:
    return cast(Any, importlib.import_module(name))


def _row() -> dict:
    return {
        "atr": 5.0,
        "close": 100.0,
        "open": 99.0,
        "high": 102.0,
        "low": 98.0,
        "ema1": 101.0,
        "ema2": 99.0,
    }


def _config(tmp_path, **overrides):
    defaults = dict(
        atr_stop_multiplier=3.0,
        atr_multiplier=3.0,
        bot_mode="DEMO",
        allow_baseline_live=False,
        max_concurrent_long=6,
        partial_pct_1=0.50,
        partial_pct_2=0.30,
        partial_threshold_1=0.02,
        partial_threshold_2=0.04,
        position_size_cushion=0.98,
        risk_per_trade=0.01,
        states_dir=str(tmp_path),
        oos_strict_mode=True,
        breakeven_enabled=True,
        breakeven_trigger_pct=0.02,
        trailing_activation_pct=0.03,
        email_cooldown_seconds=300,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _filled_buy(qty: str = "9.80", *, order_id: str = "ORDER-1", client_id: str = "") -> dict:
    return {
        "status": "FILLED",
        "side": "BUY",
        "orderId": order_id,
        "clientOrderId": client_id,
        "executedQty": qty,
        "cummulativeQuoteQty": str(float(qty) * 100.0),
        "fills": [],
    }


def _ctx(om, *, sizing_mode: str = "risk", pair_state: dict | None = None, **overrides):
    values = dict(
        real_trading_pair="XRPUSDC",
        backtest_pair="XRPUSDC",
        time_interval="1h",
        sizing_mode=sizing_mode,
        pair_state=pair_state if pair_state is not None else {},
        best_params={"_entries_ready": True},
        ema1_period=18,
        ema2_period=58,
        scenario="StochRSI",
        coin_symbol="XRP",
        quote_currency="USDC",
        usdc_balance=1000.0,
        coin_balance_free=0.0,
        coin_balance_locked=0.0,
        coin_balance=0.0,
        current_price=100.0,
        row=_row(),
        orders=[],
        min_qty=0.01,
        max_qty=0.0,
        step_size=0.01,
        min_notional=5.0,
        min_qty_dec=Decimal("0.01"),
        max_qty_dec=Decimal("0"),
        step_size_dec=Decimal("0.01"),
        step_decimals=2,
    )
    values.update(overrides)
    return om._TradeCtx(**values)


def _deps(om, tmp_path, *, config=None, bot_state: dict | None = None, **overrides):
    if bot_state is None:
        bot_state = {}
    defaults = dict(
        client=MagicMock(),
        bot_state=bot_state,
        bot_state_lock=threading.RLock(),
        save_fn=MagicMock(),
        send_alert_fn=MagicMock(),
        place_sl_fn=MagicMock(return_value={"orderId": "SL-1"}),
        market_sell_fn=MagicMock(return_value={"status": "FILLED"}),
        market_buy_fn=MagicMock(return_value=_filled_buy()),
        update_daily_pnl_fn=MagicMock(),
        is_loss_limit_fn=MagicMock(return_value=False),
        gen_buy_checker_fn=MagicMock(return_value=MagicMock(return_value=(True, "signal"))),
        gen_sell_checker_fn=MagicMock(),
        check_order_executed_fn=MagicMock(return_value=False),
        get_usdc_sells_fn=MagicMock(return_value=1000.0),
        get_sniper_entry_fn=MagicMock(return_value=100.0),
        check_partial_exits_fn=MagicMock(return_value=(False, False)),
        console=MagicMock(),
        config=config or _config(tmp_path),
        is_valid_stop_loss_fn=MagicMock(return_value=True),
        buy_allocation_lock=None,
    )
    defaults.update(overrides)
    return om._TradingDeps(**defaults)


def test_kraken_buy_filled_persists_state_places_sl_and_wal(monkeypatch, tmp_path) -> None:
    with kraken_import_context(monkeypatch):
        import order_manager as om

        ctx = _ctx(om)
        deps = _deps(om, tmp_path)
        with (
            patch.object(om, "display_buy_signal_panel"),
            patch.object(om, "log_trade"),
            patch.object(om, "wal_write") as wal_write,
        ):
            om._execute_buy(ctx, deps)

        assert ctx.pair_state["last_order_side"] == "BUY"
        assert ctx.pair_state["entry_price"] == 100.0
        assert ctx.pair_state["stop_loss_at_entry"] == 85.0
        assert ctx.pair_state["stop_loss"] == 85.0
        assert ctx.pair_state["initial_position_size"] == 9.8
        assert ctx.pair_state["sl_exchange_placed"] is True
        assert isinstance(ctx.pair_state["partial_enabled"], bool)
        deps.place_sl_fn.assert_called_once()
        buy_kwargs = deps.market_buy_fn.call_args.kwargs
        assert buy_kwargs["newClientOrderId"].startswith("kb")
        assert len(buy_kwargs["newClientOrderId"]) <= 18
        assert [call.args[0] for call in wal_write.call_args_list] == [
            om.OP_BUY_INTENT,
            om.OP_BUY_CONFIRMED,
            om.OP_SL_PLACED,
        ]


def test_kraken_valid_buy_signal_without_reference_sell_alerts_without_error(
    monkeypatch,
    tmp_path,
    caplog,
) -> None:
    with kraken_import_context(monkeypatch):
        import order_manager as om

        cast(
            dict[str, float],
            getattr(om, "_no_reference_sell_alert_last_sent"),
        ).clear()
        ctx = _ctx(
            om,
            real_trading_pair="CROUSDC",
            backtest_pair="CROUSDC",
            coin_symbol="CRO",
            min_qty=90.0,
            min_qty_dec=Decimal("90"),
        )
        deps = _deps(om, tmp_path, get_usdc_sells_fn=MagicMock(return_value=0.0))

        with (
            caplog.at_level(logging.WARNING, logger="order_manager"),
            patch.object(om, "display_buy_signal_panel"),
        ):
            om._execute_buy(ctx, deps)

        deps.market_buy_fn.assert_not_called()
        deps.send_alert_fn.assert_called_once()
        alert_kwargs = deps.send_alert_fn.call_args.kwargs
        assert "Achat bloque CROUSDC" in alert_kwargs["subject"]
        assert "aucune transaction SELL historique" in alert_kwargs["body_main"]
        assert not [
            record for record in caplog.records
            if record.name == "order_manager" and record.levelno >= logging.ERROR
        ]


def test_kraken_max_qty_zero_does_not_zero_buy_sizing(monkeypatch) -> None:
    with kraken_import_context(monkeypatch):
        import order_manager as om

        result = om._compute_buy_quantity(
            sizing_mode="baseline",
            usdc_for_buy=1000.0,
            usdc_balance=1000.0,
            entry_price=100.0,
            atr_value=5.0,
            min_qty_dec=Decimal("0.01"),
            max_qty_dec=Decimal("0"),
            step_size_dec=Decimal("0.01"),
            step_decimals=2,
        )

        assert result is not None
        assert result[0] == Decimal("9.80")


def test_kraken_buy_exception_recovers_filled_order_by_client_id(monkeypatch, tmp_path) -> None:
    with kraken_import_context(monkeypatch):
        import order_manager as om

        ctx = _ctx(om)
        client = MagicMock()
        client.get_order.return_value = _filled_buy(qty="9.75", order_id="ORDER-RECOVERED")
        deps = _deps(
            om,
            tmp_path,
            client=client,
            market_buy_fn=MagicMock(side_effect=RuntimeError("transport lost after AddOrder")),
        )

        with (
            patch.object(om, "display_buy_signal_panel"),
            patch.object(om, "log_trade"),
            patch.object(om, "wal_write") as wal_write,
        ):
            om._execute_buy(ctx, deps)

        deps.market_buy_fn.assert_called_once()
        client.get_order.assert_called_once()
        assert ctx.pair_state["last_order_side"] == "BUY"
        assert ctx.pair_state["initial_position_size"] == 9.75
        assert ctx.pair_state["sl_exchange_placed"] is True
        assert om.OP_BUY_CONFIRMED in [call.args[0] for call in wal_write.call_args_list]


def test_kraken_buy_unknown_after_exception_halts_and_marks_reconcile(monkeypatch, tmp_path) -> None:
    with kraken_import_context(monkeypatch):
        import order_manager as om

        ctx = _ctx(om)
        client = MagicMock()
        client.get_order.side_effect = RuntimeError("not found")
        deps = _deps(
            om,
            tmp_path,
            client=client,
            bot_state={},
            market_buy_fn=MagicMock(side_effect=RuntimeError("timeout")),
        )

        with (
            patch.object(om, "display_buy_signal_panel"),
            patch.object(om, "log_trade"),
            patch.object(om, "wal_write"),
        ):
            om._execute_buy(ctx, deps)

        assert deps.bot_state["emergency_halt"] is True
        assert (tmp_path / "reconcile_required.json").exists()
        deps.place_sl_fn.assert_not_called()


def test_kraken_buy_blocks_without_pair_sell_history_even_with_free_quote(monkeypatch, tmp_path) -> None:
    with kraken_import_context(monkeypatch):
        import order_manager as om

        ctx = _ctx(om, sizing_mode="baseline")
        client = MagicMock()
        client.broker = "KRAKEN"
        client.get_account.return_value = {
            "balances": [{"asset": "USDC", "free": "1000.0", "locked": "0"}]
        }
        deps = _deps(
            om,
            tmp_path,
            client=client,
            get_usdc_sells_fn=MagicMock(return_value=0.0),
            market_buy_fn=MagicMock(return_value=_filled_buy(qty="0.66")),
        )

        with (
            patch.object(om, "display_buy_signal_panel"),
            patch.object(om, "log_trade"),
            patch.object(om, "wal_write"),
        ):
            om._execute_buy(ctx, deps)

        deps.market_buy_fn.assert_not_called()
        assert ctx.pair_state.get("last_order_side") != "BUY"


def test_kraken_latest_pair_sell_amount_ignores_stale_buy_timestamp(monkeypatch, tmp_path) -> None:
    with kraken_import_context(monkeypatch):
        import order_manager as om

        ctx = _ctx(
            om,
            sizing_mode="baseline",
            pair_state={"last_order_side": "SELL", "buy_timestamp": 1710000000.0},
        )
        client = MagicMock()
        client.broker = "KRAKEN"
        client.get_account.return_value = {
            "balances": [{"asset": "USDC", "free": "1000.0", "locked": "0"}]
        }
        deps = _deps(
            om,
            tmp_path,
            client=client,
            get_usdc_sells_fn=MagicMock(return_value=123.0),
            market_buy_fn=MagicMock(return_value=_filled_buy(qty="0.66")),
        )

        with (
            patch.object(om, "display_buy_signal_panel"),
            patch.object(om, "log_trade"),
            patch.object(om, "wal_write"),
        ):
            om._execute_buy(ctx, deps)

        deps.get_usdc_sells_fn.assert_called()
        deps.market_buy_fn.assert_called_once()
        assert deps.market_buy_fn.call_args.kwargs["quoteOrderQty"] > 0


def test_kraken_ondousd_buy_uses_usd_quote_balance_not_usdc(monkeypatch, tmp_path) -> None:
    with kraken_import_context(monkeypatch):
        import order_manager as om

        ctx = _ctx(
            om,
            sizing_mode="baseline",
            real_trading_pair="ONDOUSD",
            backtest_pair="ONDOUSD",
            coin_symbol="ONDO",
            quote_currency="USD",
            usdc_balance=10.0,
        )
        client = MagicMock()
        client.broker = "KRAKEN"
        client.get_account.return_value = {
            "balances": [
                {"asset": "USD", "free": "42.0", "locked": "0"},
                {"asset": "USDC", "free": "999.0", "locked": "0"},
            ]
        }
        deps = _deps(
            om,
            tmp_path,
            client=client,
            get_usdc_sells_fn=MagicMock(return_value=20.0),
            market_buy_fn=MagicMock(return_value=_filled_buy(qty="0.41")),
        )

        with (
            patch.object(om, "display_buy_signal_panel"),
            patch.object(om, "log_trade"),
            patch.object(om, "wal_write"),
        ):
            om._execute_buy(ctx, deps)

        deps.market_buy_fn.assert_called_once()
        assert deps.market_buy_fn.call_args.kwargs["quoteOrderQty"] == 19.0
        assert ctx.usdc_balance == 42.0


def test_kraken_live_baseline_and_max_concurrent_block_buy(monkeypatch, tmp_path) -> None:
    with kraken_import_context(monkeypatch):
        import order_manager as om

        live_cfg = _config(tmp_path, bot_mode="LIVE", allow_baseline_live=False)
        baseline_deps = _deps(om, tmp_path, config=live_cfg)
        with patch.object(om, "display_buy_signal_panel"), patch.object(om, "wal_write"):
            om._execute_buy(_ctx(om, sizing_mode="baseline"), baseline_deps)
        baseline_deps.market_buy_fn.assert_not_called()

        capped_cfg = _config(tmp_path, max_concurrent_long=1)
        capped_deps = _deps(
            om,
            tmp_path,
            config=capped_cfg,
            bot_state={"ONDOUSD": {"last_order_side": "BUY"}},
        )
        with patch.object(om, "display_buy_signal_panel"), patch.object(om, "wal_write"):
            om._execute_buy(_ctx(om, sizing_mode="risk"), capped_deps)
        capped_deps.market_buy_fn.assert_not_called()


def test_kraken_fetch_balances_absent_base_is_zero(monkeypatch) -> None:
    with kraken_import_context(monkeypatch):
        runner = _import_kraken_module("runner")

        runner.client = MagicMock()
        runner.client.get_account.return_value = {
            "balances": [{"asset": "USDC", "free": "250.0", "locked": "0"}]
        }

        balances = runner._fetch_balances("XRPUSDC")

        assert balances is not None
        assert balances[1] == "XRP"
        assert balances[3] == 250.0
        assert balances[4] == 0.0
        assert balances[5] == 0.0
        assert balances[6] == 0.0


def test_kraken_fetch_balances_ondousd_uses_usd_not_usdc(monkeypatch) -> None:
    with kraken_import_context(monkeypatch):
        runner = _import_kraken_module("runner")

        runner.client = MagicMock()
        runner.client.get_account.return_value = {
            "balances": [
                {"asset": "USD", "free": "17.5", "locked": "0"},
                {"asset": "USDC", "free": "250.0", "locked": "0"},
            ]
        }

        balances = runner._fetch_balances("ONDOUSD")

        assert balances is not None
        assert balances[1] == "ONDO"
        assert balances[2] == "USD"
        assert balances[3] == 17.5


def test_kraken_extract_coin_from_usd_and_usdc_pairs(monkeypatch) -> None:
    with kraken_import_context(monkeypatch):
        bot_config = _import_kraken_module("bot_config")

        assert bot_config.extract_coin_from_pair("ONDOUSD") == ("ONDO", "USD")
        assert bot_config.extract_coin_from_pair("XRPUSDC") == ("XRP", "USDC")
        assert bot_config.extract_coin_from_pair("CROUSDC") == ("CRO", "USDC")


def test_kraken_last_sell_helper_returns_latest_pair_sell_amount(monkeypatch) -> None:
    with kraken_import_context(monkeypatch):
        trade_helpers = _import_kraken_module("trade_helpers")
        client = MagicMock()
        client.get_my_trades.return_value = [
            {
                "isBuyer": False,
                "orderId": "OLD-SELL",
                "quoteQty": "100.0",
                "commission": "0",
                "commissionAsset": "USD",
                "time": 1_700_000_000_000,
            },
            {
                "isBuyer": True,
                "orderId": "BUY",
                "quoteQty": "12.0",
                "commission": "0",
                "commissionAsset": "USD",
                "time": 1_700_000_500_000,
            },
            {
                "isBuyer": False,
                "orderId": "LATEST-SELL",
                "quoteQty": "40.0",
                "commission": "0.1",
                "commissionAsset": "USD",
                "time": 1_700_001_000_000,
            },
            {
                "isBuyer": False,
                "orderId": "LATEST-SELL",
                "quoteQty": "2.0",
                "commission": "0.01",
                "commissionAsset": "USD",
                "time": 1_700_001_000_500,
            },
        ]

        amount, fee, fee_asset = trade_helpers.get_last_sell_trade_usdc("ONDOUSD", client)

        assert amount == 42.0
        assert fee == 0.11
        assert fee_asset == "USD"


def test_kraken_runtime_status_prioritizes_emergency_halt(monkeypatch) -> None:
    with kraken_import_context(monkeypatch):
        runner = _import_kraken_module("runner")
        runner.bot_state.clear()
        runner.bot_state.update(
            {
                "emergency_halt": True,
                "emergency_halt_reason": "BUY sans SL reparable impossible",
                "XRPUSDC": {"entries_ready": True, "broker_pair_status": "online"},
            }
        )

        status, detail = runner._runtime_system_status()

        assert status == "EMERGENCY_HALT / NO-BUY"
        assert detail == "BUY sans SL reparable impossible"


def test_kraken_reconcile_clears_stale_position_halt_when_no_buy_left(monkeypatch) -> None:
    with kraken_import_context(monkeypatch):
        runner = _import_kraken_module("runner")
        runner.bot_state.clear()
        runner.bot_state.update(
            {
                "emergency_halt": True,
                "emergency_halt_reason": "BUY sans SL reparable impossible pour ONDOUSD: stop=None",
                "ONDOUSD": {"last_order_side": "SELL", "sl_exchange_placed": False},
            }
        )

        cleared = runner._clear_resolved_emergency_halt_after_reconcile()

        assert cleared is True
        assert "emergency_halt" not in runner.bot_state
        assert "emergency_halt_reason" not in runner.bot_state


def test_kraken_reconcile_keeps_halt_when_unprotected_buy_remains(monkeypatch) -> None:
    with kraken_import_context(monkeypatch):
        runner = _import_kraken_module("runner")
        runner.bot_state.clear()
        runner.bot_state.update(
            {
                "emergency_halt": True,
                "emergency_halt_reason": "BUY sans SL reparable impossible pour ONDOUSD: stop=None",
                "ONDOUSD": {"last_order_side": "BUY", "sl_exchange_placed": False},
            }
        )

        cleared = runner._clear_resolved_emergency_halt_after_reconcile()

        assert cleared is False
        assert runner.bot_state["emergency_halt"] is True


def test_kraken_sl_repair_unrepairable_marks_reconcile(monkeypatch, tmp_path) -> None:
    with kraken_import_context(monkeypatch):
        runner = _import_kraken_module("runner")
        runner.bot_state.clear()
        runner.client = MagicMock()
        runner.client.api_key = "test-key"
        runner.client.api_secret = "test-secret"
        runner.client.get_open_orders.return_value = []
        marker_path = tmp_path / "reconcile_required.json"
        pair_state = {
            "last_order_side": "BUY",
            "sl_exchange_placed": False,
            "stop_loss": None,
            "stop_loss_at_entry": None,
        }

        monkeypatch.setattr(runner, "RECONCILE_REQUIRED_MARKER", str(marker_path))
        monkeypatch.setattr(runner, "save_bot_state", MagicMock())

        repaired = runner._repair_missing_exchange_sl_for_open_position(
            "XRP/USDC",
            "XRPUSDC",
            pair_state,
            "XRP",
            1.0,
            Decimal("0.01"),
            Decimal("0.000001"),
            6,
        )

        assert repaired is False
        assert runner.bot_state["emergency_halt"] is True
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        assert marker["source"] == "sl_repair"
        assert "XRPUSDC" in marker["reason"]


def test_kraken_load_bot_state_accepts_empty_state(monkeypatch) -> None:
    with kraken_import_context(monkeypatch, bot_mode="DEMO"):
        runner = _import_kraken_module("runner")
        runner.bot_state["XRPUSDC"] = {"last_order_side": "BUY"}
        notify = MagicMock()

        monkeypatch.setattr(runner, "load_state", MagicMock(return_value={}))
        monkeypatch.setattr(runner, "_error_notification_handler", notify)

        runner.load_bot_state()

        assert runner.bot_state == {}
        notify.assert_not_called()


def test_kraken_live_refuses_corrupt_state(monkeypatch) -> None:
    with kraken_import_context(monkeypatch, bot_mode="LIVE"):
        runner = _import_kraken_module("runner")
        notify = MagicMock()

        monkeypatch.setattr(runner, "load_state", MagicMock(side_effect=RuntimeError("bad hmac")))
        monkeypatch.setattr(runner, "_error_notification_handler", notify)

        with pytest.raises(SystemExit, match="Etat Kraken invalide en LIVE"):
            runner.load_bot_state()

        notify.assert_called_once()


def test_kraken_demo_corrupt_state_recovers_empty_once(monkeypatch) -> None:
    with kraken_import_context(monkeypatch, bot_mode="DEMO"):
        runner = _import_kraken_module("runner")
        runner.bot_state["XRPUSDC"] = {"last_order_side": "BUY"}
        notify = MagicMock()

        monkeypatch.setattr(runner, "load_state", MagicMock(side_effect=RuntimeError("bad hmac")))
        monkeypatch.setattr(runner, "_error_notification_handler", notify)

        runner.load_bot_state()

        assert runner.bot_state == {}
        notify.assert_called_once()


def test_kraken_order_history_does_not_resurrect_buy_without_balance(monkeypatch) -> None:
    with kraken_import_context(monkeypatch):
        runner = _import_kraken_module("runner")

        runner.client = MagicMock()
        runner.client.get_all_orders.return_value = [
            {"status": "FILLED", "side": "BUY", "orderId": "OLD-BUY"},
        ]
        save_mock = MagicMock()
        monkeypatch.setattr(runner, "save_bot_state", save_mock)
        pair_state = {"last_order_side": "SELL", "entry_price": None}

        orders, last_side = runner._sync_order_history(
            "XRPUSDC",
            pair_state,
            coin_balance=0.0,
            min_qty=1.65,
        )

        assert orders == [{"status": "FILLED", "side": "BUY", "orderId": "OLD-BUY"}]
        assert last_side is None
        assert pair_state["last_order_side"] == "SELL"
        save_mock.assert_not_called()


def test_kraken_default_pairs_are_current_active_kraken_pairs() -> None:
    source = (KRAKEN_DIR / "runner.py").read_text(encoding="utf-8")

    assert '"HBARUSDC"' not in source
    assert '"XRPUSDC"' in source
    assert '"ONDOUSD"' in source
    assert '"CROUSDC"' in source


def test_kraken_drop_unclosed_ohlc_candle(monkeypatch) -> None:
    with kraken_import_context(monkeypatch):
        data_fetcher = _import_kraken_module("data_fetcher")

        df = pd.DataFrame(
            {"open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0], "close": [1.0, 2.0], "volume": [1.0, 2.0]},
            index=pd.to_datetime(["2026-07-15T09:00:00Z", "2026-07-15T10:00:00Z"]),
        )

        clipped = data_fetcher.drop_unclosed_kraken_candle(
            df,
            "1h",
            now=pd.Timestamp("2026-07-15T10:30:00Z"),
        )
        unchanged = data_fetcher.drop_unclosed_kraken_candle(
            df.iloc[:1],
            "1h",
            now=pd.Timestamp("2026-07-15T10:05:00Z"),
        )

        assert list(clipped.index) == [pd.Timestamp("2026-07-15T09:00:00Z")]
        assert len(unchanged) == 1


def test_kraken_history_rebuilds_when_cache_is_shallow_and_csv_exists(monkeypatch, tmp_path) -> None:
    with kraken_import_context(monkeypatch):
        data_fetcher = _import_kraken_module("data_fetcher")

        cached_df = pd.DataFrame(
            {
                "open": [1.0],
                "high": [1.0],
                "low": [1.0],
                "close": [1.0],
                "volume": [1.0],
            },
            index=pd.to_datetime(["2024-01-01T00:00:00Z"]),
        )
        raw_rows: list[list[Any]] = [
            [1_704_067_200_000, "1", "2", "1", "2", "10", 0, "0", 0, "0", "0", "0"],
            [1_704_070_800_000, "2", "3", "2", "3", "11", 0, "0", 0, "0", "0", "0"],
            [1_704_074_400_000, "3", "4", "3", "4", "12", 0, "0", 0, "0", "0", "0"],
        ]

        class Client:
            def __init__(self) -> None:
                self.fetch_calls = 0

            def get_history_required_bars(self, interval: str) -> int:
                assert interval == "1h"
                return 3

            def has_deep_historical_source(self, pair: str, interval: str) -> bool:
                assert pair == "ONDOUSD"
                assert interval == "1h"
                return True

            def get_historical_klines(self, pair: str, interval: str, start_date: str) -> list[list[Any]]:
                assert pair == "ONDOUSD"
                assert interval == "1h"
                assert start_date == "2024-01-01"
                self.fetch_calls += 1
                return raw_rows

        client = Client()
        writes: list[tuple[Any, ...]] = []
        monkeypatch.setattr(data_fetcher, "ensure_cache_dir", lambda: None)
        monkeypatch.setattr(data_fetcher, "get_cache_path", lambda *_args: (tmp_path / "cache.pkl", tmp_path / "cache.lock"))
        monkeypatch.setattr(data_fetcher, "safe_cache_read", lambda _path: cached_df)
        monkeypatch.setattr(data_fetcher, "safe_cache_write", lambda *_args: writes.append(_args))
        monkeypatch.setattr(data_fetcher, "update_cache_with_recent_data", lambda cached, *_args: cached)

        result = data_fetcher.fetch_historical_data("ONDOUSD", "1h", "2024-01-01", client)

        assert client.fetch_calls == 1
        assert len(result) == 3
        assert len(writes) == 1
