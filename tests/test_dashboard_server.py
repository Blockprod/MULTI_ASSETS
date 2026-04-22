import os
import sys
import importlib.util
from pathlib import Path

import pytest


_MODULE_PATH = Path(__file__).resolve().parents[1] / 'code' / 'scripts' / 'dashboard_server.py'
_SPEC = importlib.util.spec_from_file_location('dashboard_server_test_module', _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
ds = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault('dashboard_server_test_module', ds)
_SPEC.loader.exec_module(ds)


def test_normalize_legacy_sigint_shutdown_line():
    line = "2026-04-22 17:58:26,348 - __main__ - CRITICAL - [SHUTDOWN] Signal 2 reçu — arrêt demandé"

    normalized = ds._normalize_dashboard_log_line(line)

    assert " - CRITICAL - " not in normalized
    assert " - INFO - " in normalized
    assert "arrêt volontaire demandé [legacy]" in normalized


def test_normalize_dashboard_log_line_keeps_other_lines_unchanged():
    line = "2026-04-22 18:10:00,849 - __main__ - INFO - Gestionnaire d'erreurs actif - Mode: RUNNING"

    assert ds._normalize_dashboard_log_line(line) == line


def test_build_mark_to_market_curve_uses_start_and_now_points():
    curve = ds._build_mark_to_market_curve(255.58, 254.44, 1776874197.0527651)

    assert len(curve) == 2
    assert curve[0]["equity"] == pytest.approx(255.58)
    assert curve[-1]["equity"] == pytest.approx(254.44)


def test_update_equity_history_refreshes_then_appends(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, 'EQUITY_HISTORY_FILE', str(tmp_path / 'dashboard_equity_history.json'))

    first = ds._update_equity_history(255.58, now_ts='2026-04-22T18:00:00Z')
    refreshed = ds._update_equity_history(255.40, now_ts='2026-04-22T18:00:30Z')
    appended = ds._update_equity_history(255.20, now_ts='2026-04-22T18:02:00Z')

    assert len(first) == 1
    assert len(refreshed) == 1
    assert refreshed[0]['equity'] == pytest.approx(255.40)
    assert len(appended) == 2
    assert appended[-1]['equity'] == pytest.approx(255.20)


def test_collect_data_exposes_equity_delta(monkeypatch):
    monkeypatch.setattr(ds, '_read_json', lambda path: {
        ds.HEARTBEAT: {
            'timestamp': '2026-04-22T18:24:20Z',
            'pid': 123,
            'circuit_mode': 'RUNNING',
            'error_count': 0,
            'loop_counter': 1,
            'usdc_balance': 81.76,
        },
        ds.BOT_STATE: {
            '_daily_pnl_tracker': {'starting_equity': 255.58, '2026-04-22': {'total_pnl': 0.0, 'trade_count': 0}},
            'ONDOUSDT': {
                'last_order_side': 'BUY',
                'entry_price': 0.2675,
                'entry_scenario': 'StochRSI',
                'entry_timeframe': '4h',
                'entry_ema1': 30,
                'entry_ema2': 60,
                'ticker_spot_price': 0.2664,
                'initial_position_size': 649.7,
                'buy_timestamp': 1776874197.0527651,
            },
        },
        ds.METRICS_FILE: {'pairs': {}},
    }.get(path, {}))
    monkeypatch.setattr(ds, '_parse_crypto_pairs', lambda: [{'backtest_pair': 'ONDOUSDT', 'real_pair': 'ONDOUSDC'}])
    monkeypatch.setattr(ds, '_age_seconds', lambda ts: 0)
    monkeypatch.setattr(ds, '_fetch_account_balances', lambda: {'USDC': 81.76, 'ONDO': 649.7})
    monkeypatch.setattr(ds, '_fetch_usdc_balance', lambda: 81.76)
    monkeypatch.setattr(ds, '_get_daily_pnl', lambda tracker: (0.0, 0.0))
    monkeypatch.setattr(ds, '_get_starting_equity', lambda tracker: 255.58)
    monkeypatch.setattr(ds, '_cumulative_pnl', lambda real_pairs: (7.13, 4))
    monkeypatch.setattr(ds, '_recent_trades', lambda real_pairs, limit=50: [])
    monkeypatch.setattr(ds, '_update_equity_history', lambda current_equity, now_ts=None: [
        {'ts': '2026-04-22T17:23:17Z', 'equity': 255.58},
        {'ts': now_ts or '2026-04-22T18:24:20Z', 'equity': round(current_equity, 2)},
    ])

    data = ds.collect_data()

    expected_equity = 81.76 + (649.7 * 0.2664)
    assert data['total_equity'] == pytest.approx(expected_equity)
    assert data['equity_delta'] == pytest.approx(expected_equity - 255.58)
    assert len(data['equity_curve']) == 3
    assert data['cumul_pnl'] == pytest.approx(7.13)
    assert data['pairs']['ONDOUSDT']['scenario'] == 'StochRSI'
    assert data['pairs']['ONDOUSDT']['timeframe'] == '4h'
    assert data['pairs']['ONDOUSDT']['entry_ema1'] == 30
    assert data['pairs']['ONDOUSDT']['entry_ema2'] == 60


def test_collect_data_prefers_live_coin_balance_for_open_position_qty(monkeypatch):
    monkeypatch.setattr(ds, '_read_json', lambda path: {
        ds.HEARTBEAT: {
            'timestamp': '2026-04-22T18:24:20Z',
            'pid': 123,
            'circuit_mode': 'RUNNING',
            'error_count': 0,
            'loop_counter': 1,
            'usdc_balance': 81.76,
        },
        ds.BOT_STATE: {
            '_daily_pnl_tracker': {'starting_equity': 255.58, '2026-04-22': {'total_pnl': 0.0, 'trade_count': 0}},
            'ONDOUSDT': {
                'last_order_side': 'BUY',
                'entry_price': 0.2675,
                'ticker_spot_price': 0.2664,
                'initial_position_size': 649.7,
                'buy_timestamp': 1776874197.0527651,
            },
        },
        ds.METRICS_FILE: {'pairs': {}},
    }.get(path, {}))
    monkeypatch.setattr(ds, '_parse_crypto_pairs', lambda: [{'backtest_pair': 'ONDOUSDT', 'real_pair': 'ONDOUSDC'}])
    monkeypatch.setattr(ds, '_age_seconds', lambda ts: 0)
    monkeypatch.setattr(ds, '_fetch_account_balances', lambda: {'USDC': 81.76, 'ONDO': 600.0})
    monkeypatch.setattr(ds, '_fetch_usdc_balance', lambda: 81.76)
    monkeypatch.setattr(ds, '_get_daily_pnl', lambda tracker: (0.0, 0.0))
    monkeypatch.setattr(ds, '_get_starting_equity', lambda tracker: 255.58)
    monkeypatch.setattr(ds, '_cumulative_pnl', lambda real_pairs: (0.0, 0))
    monkeypatch.setattr(ds, '_recent_trades', lambda real_pairs, limit=50: [])
    monkeypatch.setattr(ds, '_update_equity_history', lambda current_equity, now_ts=None: [
        {'ts': '2026-04-22T17:23:17Z', 'equity': 255.58},
        {'ts': now_ts or '2026-04-22T18:24:20Z', 'equity': round(current_equity, 2)},
    ])

    data = ds.collect_data()

    assert data['pairs']['ONDOUSDT']['qty'] == pytest.approx(600.0)
    assert data['pairs']['ONDOUSDT']['unrealized_pnl'] == pytest.approx((0.2664 - 0.2675) * 600.0)
    assert data['total_equity'] == pytest.approx(81.76 + (600.0 * 0.2664))


def test_dashboard_html_uses_equity_delta_and_dynamic_chart_scale():
    html = (Path(__file__).resolve().parents[1] / 'code' / 'scripts' / 'dashboard.html').read_text(encoding='utf-8')

    assert 'data.equity_delta' in html
    assert 'Vs. Ref' in html
    assert 'daily ref: ' in html
    assert 'realized today ' in html
    assert 'WF/OOS actif: ' in html
    assert 'AXIS_TARGET_MAX = 1000' not in html
    assert 'var d = n >= 100 ? 0 : n >= 1 ? 2 : 4;' not in html
    assert 'var minDomainDays = 30;' not in html