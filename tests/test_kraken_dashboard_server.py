from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[1] / "code" / "scripts" / "kraken_dashboard_server.py"
_SPEC = importlib.util.spec_from_file_location("kraken_dashboard_server_test_module", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
kds = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("kraken_dashboard_server_test_module", kds)
_SPEC.loader.exec_module(kds)


def test_kraken_dashboard_uses_dedicated_runtime_paths() -> None:
    assert kds.PORT == 8084
    assert kds.BOT_STATE.endswith("bot_state_kraken.json")
    assert kds.HEARTBEAT.endswith("heartbeat_kraken.json")
    assert kds.METRICS_FILE.endswith("code\\metrics\\metrics_kraken.json") or kds.METRICS_FILE.endswith("code/metrics/metrics_kraken.json")
    assert "kraken_bot" in kds.BOT_STATE
    assert "kraken_bot" in kds.HEARTBEAT
    assert kds.EQUITY_HISTORY_FILE.endswith("kraken_dashboard_equity_history.json")
    assert kds.BOT_LOG.endswith("kraken_trading_bot.log")


def test_kraken_dashboard_html_rebrands_binance_dashboard() -> None:
    html = kds._get_kraken_dashboard_html().decode("utf-8")

    assert "MULTI ASSETS KRAKEN PRO Dashboard" in html
    assert "KRAKEN PRO" in html
    assert "free USD/USDC cash on Kraken" in html
    assert "MULTI ASSETS BINANCE Dashboard" not in html
    assert "BINANCE" not in html


def test_kraken_dashboard_reads_sealed_json_v1_state(tmp_path: Path) -> None:
    payload = {"XRPUSDC": {"last_order_side": "SELL"}}
    path = tmp_path / "state.json"
    path.write_bytes(b"JSON_V1:" + (b"x" * 32) + json.dumps(payload).encode("utf-8"))

    assert kds._read_json(str(path)) == payload


def test_kraken_dashboard_trade_stats_ignore_non_kraken_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    journal = logs_dir / "trade_journal.jsonl"
    journal.write_text(
        "\n".join(
            [
                json.dumps({"broker": "kraken", "pair": "XRPUSDC", "side": "sell", "pnl": 4.2, "ts": "2026-06-27T10:00:00Z"}),
                json.dumps({"broker": "binance", "pair": "XRPUSDC", "side": "sell", "pnl": 99.0, "ts": "2026-06-27T10:01:00Z"}),
                json.dumps({"pair": "XRPUSDC", "side": "sell", "pnl": 88.0, "ts": "2026-06-27T10:02:00Z"}),
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kds, "LOGS_DIR", str(logs_dir))

    assert kds._kraken_cumulative_pnl({"XRPUSDC"}) == (4.2, 1)
    assert len(kds._kraken_recent_trades({"XRPUSDC"})) == 1
    assert kds._kraken_win_stats({"XRPUSDC"}) == (100.0, 1, 1)

    curve = kds._kraken_build_equity_curve(100.0, {"XRPUSDC"})
    assert curve[-1]["equity"] == pytest.approx(104.2)


def test_kraken_dashboard_fetches_balances_through_kraken_client(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeKrakenClient:
        def get_account(self):
            return {
                "balances": [
                    {"asset": "USDC", "free": "123.45", "locked": "1.55"},
                    {"asset": "BTC", "free": "0.01", "locked": "0"},
                ]
            }

    monkeypatch.setattr(kds, "_get_kraken_client", lambda: FakeKrakenClient())
    monkeypatch.setattr(kds, "_account_balances_cache", (0.0, None))

    balances = kds._fetch_kraken_account_balances()

    assert balances == {"USDC": 125.0, "BTC": 0.01}
    assert kds._fetch_kraken_usdc_balance() == pytest.approx(125.0)


def test_kraken_dashboard_marks_private_api_ko(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        kds,
        "_read_json",
        lambda _path: {
            "kraken_private_api_ok": False,
            "kraken_preflight": {"permission_error": "EGeneral:Permission denied"},
        },
    )
    monkeypatch.setattr(kds, "_fetch_kraken_usdc_balance", lambda: None)

    data = kds._kraken_collect_data()

    assert data["system_status"] == "KRAKEN PRIVATE API KO"
    assert "Permission denied" in data["system_status_detail"]


def test_kraken_dashboard_data_matches_binance_dashboard_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read_json(path: str):
        if path == kds.BOT_STATE:
            return {
                "_daily_pnl_tracker": {"starting_equity": 100.0},
                "kraken_private_api_ok": True,
                "XRPUSDC": {
                    "last_order_side": "SELL",
                    "entries_ready": False,
                    "wf_status": "oos_failed",
                    "execution_count": 2,
                },
            }
        if path == kds.HEARTBEAT:
            return {"timestamp": "2026-07-15T20:31:41Z", "pid": 123, "circuit_mode": "RUNNING", "usdc_balance": 125.0}
        if path == kds.METRICS_FILE:
            return {"pairs": {"XRPUSDC": {"oos_blocked": True}}, "taker_fee": 0.008, "maker_fee": 0.004}
        return {}

    monkeypatch.setattr(kds, "_read_json", fake_read_json)
    monkeypatch.setattr(kds, "_fetch_kraken_usdc_balance", lambda: 125.0)
    monkeypatch.setattr(kds, "_kraken_cumulative_pnl", lambda _pairs: (0.0, 0))
    monkeypatch.setattr(kds, "_kraken_win_stats", lambda _pairs: (None, 0, 0))
    monkeypatch.setattr(kds, "_kraken_recent_trades", lambda _pairs, limit=50: [])
    monkeypatch.setattr(kds, "_kraken_build_equity_curve", lambda *_args: [])

    data = kds._kraken_collect_data()

    assert data["broker"] == "KRAKEN"
    assert data["total_pairs"] == 1
    assert data["open_count"] == 0
    assert data["total_equity"] == pytest.approx(125.0)
    assert data["cumul_pnl"] == 0.0
    assert data["trade_count"] == 0
    assert "XRPUSDC" in data["pairs"]
    assert data["pairs"]["XRPUSDC"]["oos_blocked"] is True
