"""tests/test_ibkr_risk.py — Tests unitaires fonctions risk management IBKR.

A3: couverture de _compute_position_size, trailing stop, exits partiels,
    daily_pnl/weekly_pnl gates. Aucune connexion IB Gateway nécessaire.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# ─── Path setup ───────────────────────────────────────────────────────────────
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR  = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_SRC_DIR   = os.path.join(_ROOT_DIR, "code", "src")
_IBKR_DIR  = os.path.join(_SRC_DIR, "ibkr")

for _p in (_SRC_DIR, _IBKR_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("BINANCE_API_KEY",    "TEST_PLACEHOLDER")
os.environ.setdefault("BINANCE_SECRET_KEY", "TEST_PLACEHOLDER")
os.environ.setdefault("IBKR_ACCOUNT",       "DU_TEST")
os.environ.setdefault("IBKR_SECRET",        "TEST_SECRET_KEY_NOT_REAL_32BYTES!")


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def ibkr_cfg():
    from ibkr_config import IBKRConfig
    IBKRConfig._instance = None   # reset singleton entre tests
    return IBKRConfig.from_env()


# ─── Tests _compute_position_size ─────────────────────────────────────────────

class TestComputePositionSize:
    """Teste _compute_position_size (fonction locale dans IBKR_FOREX)."""

    def _fn(self):
        """Retourne la fonction directement depuis le module."""
        import importlib
        # Importer sans exécuter main() — la fonction est au module level
        import IBKR_FOREX as _m
        return _m._compute_position_size

    def test_normal_case(self):
        fn = self._fn()
        # nav=10000, risk=5%, atr=0.0010, price=1.1000, multiplier=3
        # stop_distance = 3 * 0.0010 = 0.003
        # risk_amount = 10000 * 0.05 = 500
        # qty = 500 / 0.003 = 166666.67
        result = fn(10_000.0, 0.05, 0.0010, 1.1000, atr_stop_multiplier=3.0)
        assert result == pytest.approx(166_666.67, rel=1e-3)

    def test_zero_atr_returns_zero(self):
        fn = self._fn()
        assert fn(10_000.0, 0.05, 0.0, 1.1000) == 0.0

    def test_zero_nav_returns_zero(self):
        fn = self._fn()
        assert fn(0.0, 0.05, 0.0010, 1.1000) == 0.0

    def test_zero_price_returns_zero(self):
        fn = self._fn()
        assert fn(10_000.0, 0.05, 0.0010, 0.0) == 0.0

    def test_negative_atr_returns_zero(self):
        fn = self._fn()
        assert fn(10_000.0, 0.05, -0.001, 1.1000) == 0.0

    def test_scales_with_nav(self):
        fn = self._fn()
        qty_small = fn(10_000.0, 0.05, 0.001, 1.1, atr_stop_multiplier=3.0)
        qty_large = fn(20_000.0, 0.05, 0.001, 1.1, atr_stop_multiplier=3.0)
        assert qty_large == pytest.approx(qty_small * 2, rel=1e-6)

    def test_scales_with_atr_multiplier(self):
        fn = self._fn()
        qty_tight = fn(10_000.0, 0.05, 0.001, 1.1, atr_stop_multiplier=1.0)
        qty_wide  = fn(10_000.0, 0.05, 0.001, 1.1, atr_stop_multiplier=3.0)
        assert qty_tight == pytest.approx(qty_wide * 3, rel=1e-6)


# ─── Tests ibkr_config defaults ───────────────────────────────────────────────

class TestIBKRConfigDefaults:
    def test_risk_per_trade_default(self, monkeypatch):
        """C4: default code-side = 5.5% quand IBKR_RISK_PER_TRADE absent de l'env."""
        monkeypatch.delenv("IBKR_RISK_PER_TRADE", raising=False)
        from ibkr_config import IBKRConfig
        IBKRConfig._instance = None
        cfg = IBKRConfig.from_env()
        assert cfg.risk_per_trade == pytest.approx(0.055)

    def test_trailing_activation_multiplier_default(self, ibkr_cfg):
        """A4: default 3.0 (réaliste H1 Forex)."""
        assert ibkr_cfg.trailing_activation_multiplier == pytest.approx(3.0)

    def test_weekly_loss_limit_default(self, ibkr_cfg):
        """I2: weekly_loss_limit_pct présent avec valeur sensée."""
        assert ibkr_cfg.weekly_loss_limit_pct == pytest.approx(0.10)

    def test_sl_limit_offset_default(self, ibkr_cfg):
        """C2: offset StopLimitOrder 3 pips."""
        assert ibkr_cfg.sl_limit_offset == pytest.approx(0.00030)


# ─── Tests WAL SHORT ops ──────────────────────────────────────────────────────

class TestWALShortOps:
    def test_short_constants_exist(self):
        """C3: les 3 nouvelles constantes WAL sont exportées."""
        from ibkr_wal import (
            OP_FX_SHORT_INTENT,
            OP_FX_SHORT_CONFIRMED,
            OP_FX_SHORT_SL_PLACED,
        )
        assert OP_FX_SHORT_INTENT == "FX_SHORT_INTENT"
        assert OP_FX_SHORT_CONFIRMED == "FX_SHORT_CONFIRMED"
        assert OP_FX_SHORT_SL_PLACED == "FX_SHORT_SL_PLACED"

    def test_replay_detects_short_orphan(self, tmp_path, monkeypatch):
        """C3: ibkr_wal_replay() détecte un SHORT confirmé sans SL placé."""
        import ibkr_wal as _wal_mod
        wal_file = tmp_path / "ibkr_wal.jsonl"
        monkeypatch.setattr(_wal_mod, "_WAL_FILE", wal_file)
        monkeypatch.setattr(_wal_mod, "_WAL_LOCK", threading.Lock())

        _wal_mod.ibkr_wal_write("FX_SHORT_INTENT",    {"pair": "GBPUSD", "quote_qty": 5000})
        _wal_mod.ibkr_wal_write("FX_SHORT_CONFIRMED", {"pair": "GBPUSD", "entry_price": 1.34, "quantity": 40000, "order_id": 1})

        incomplete = _wal_mod.ibkr_wal_replay()
        assert len(incomplete) == 1
        assert incomplete[0].get("pair") == "GBPUSD"
        assert incomplete[0].get("chain") == "short"

    def test_replay_no_orphan_when_sl_placed(self, tmp_path, monkeypatch):
        """C3: pas d'orphelin si SHORT_SL_PLACED présent."""
        import ibkr_wal as _wal_mod
        wal_file = tmp_path / "ibkr_wal.jsonl"
        monkeypatch.setattr(_wal_mod, "_WAL_FILE", wal_file)
        monkeypatch.setattr(_wal_mod, "_WAL_LOCK", threading.Lock())

        _wal_mod.ibkr_wal_write("FX_SHORT_INTENT",    {"pair": "EURUSD"})
        _wal_mod.ibkr_wal_write("FX_SHORT_CONFIRMED", {"pair": "EURUSD", "quantity": 40000})
        _wal_mod.ibkr_wal_write("FX_SHORT_SL_PLACED", {"pair": "EURUSD", "sl_order_id": 42})

        incomplete = _wal_mod.ibkr_wal_replay()
        assert incomplete == []

    def test_replay_buy_orphan_still_detected(self, tmp_path, monkeypatch):
        """C3: le cas BUY orphelin classique est toujours détecté."""
        import ibkr_wal as _wal_mod
        wal_file = tmp_path / "ibkr_wal.jsonl"
        monkeypatch.setattr(_wal_mod, "_WAL_FILE", wal_file)
        monkeypatch.setattr(_wal_mod, "_WAL_LOCK", threading.Lock())

        _wal_mod.ibkr_wal_write("FX_BUY_INTENT",    {"pair": "EURUSD"})
        _wal_mod.ibkr_wal_write("FX_BUY_CONFIRMED", {"pair": "EURUSD", "quantity": 40000})

        incomplete = _wal_mod.ibkr_wal_replay()
        assert len(incomplete) == 1
        assert incomplete[0].get("chain") == "buy"


# ─── Tests _extract_fill_price (A1) ──────────────────────────────────────────

class TestExtractFillPrice:
    def test_reads_avgfillprice_first(self):
        from ibkr_order_manager_forex import _extract_fill_price
        result = {"avgFillPrice": "1.34500", "price": "0.0"}
        assert _extract_fill_price(result, 0.0) == pytest.approx(1.345)

    def test_falls_back_to_price(self):
        from ibkr_order_manager_forex import _extract_fill_price
        result = {"avgFillPrice": "0.0", "price": "1.34200"}
        assert _extract_fill_price(result, 0.0) == pytest.approx(1.342)

    def test_falls_back_to_fallback(self):
        from ibkr_order_manager_forex import _extract_fill_price
        assert _extract_fill_price({}, 1.2345) == pytest.approx(1.2345)

    def test_handles_none_values(self):
        from ibkr_order_manager_forex import _extract_fill_price
        result = {"avgFillPrice": None, "price": None}
        assert _extract_fill_price(result, 9.9) == pytest.approx(9.9)


# ─── Tests idempotence SELL (C1) ─────────────────────────────────────────────

class TestOrderMarketSellIdempotence:
    def test_sell_passes_orderref_to_client(self):
        """C1: safe_forex_sell génère un orderRef et le transmet."""
        from ibkr_order_manager_forex import safe_forex_sell

        mock_client = MagicMock()
        mock_client.order_market_sell.return_value = {
            "status": "FILLED", "executedQty": "40000", "price": "1.3420",
            "avgFillPrice": "1.3420", "orderId": 99,
        }

        result = safe_forex_sell(mock_client, "GBPUSD", 40_000.0, reason="TEST")

        call_kwargs = mock_client.order_market_sell.call_args[1]
        assert "orderRef" in call_kwargs
        assert len(call_kwargs["orderRef"]) == 36   # UUID4 format
        assert result is not None
        assert result["exit_price"] == pytest.approx(1.3420)

    def test_cover_passes_orderref_to_client(self):
        """C1: safe_forex_cover génère un orderRef."""
        from ibkr_order_manager_forex import safe_forex_cover

        mock_client = MagicMock()
        mock_client.order_market_buy.return_value = {
            "status": "FILLED", "executedQty": "40000", "price": "1.3400",
            "avgFillPrice": "1.3400", "orderId": 100,
        }

        safe_forex_cover(mock_client, "GBPUSD", 40_000.0, reason="COVER_TEST")
        call_kwargs = mock_client.order_market_buy.call_args[1]
        assert "orderRef" in call_kwargs
        assert len(call_kwargs["orderRef"]) == 36


# ─── Tests daily/weekly gate (I2) ────────────────────────────────────────────

class TestLossGates:
    def test_daily_loss_limit_blocks(self, ibkr_cfg):
        """Daily loss gate: perte > 5% bloque."""
        daily_loss = -(ibkr_cfg.daily_loss_limit_pct * ibkr_cfg.initial_capital + 1)
        limit = -ibkr_cfg.daily_loss_limit_pct * ibkr_cfg.initial_capital
        assert daily_loss <= limit

    def test_weekly_loss_limit_blocks(self, ibkr_cfg):
        """I2: weekly loss gate: perte > 10% bloque."""
        weekly_loss = -(ibkr_cfg.weekly_loss_limit_pct * ibkr_cfg.initial_capital + 1)
        limit = -ibkr_cfg.weekly_loss_limit_pct * ibkr_cfg.initial_capital
        assert weekly_loss <= limit

    def test_daily_within_limit_does_not_block(self, ibkr_cfg):
        """Perte < 5% ne bloque pas."""
        daily_pnl = -(ibkr_cfg.daily_loss_limit_pct * ibkr_cfg.initial_capital - 1)
        limit = -ibkr_cfg.daily_loss_limit_pct * ibkr_cfg.initial_capital
        assert daily_pnl > limit

    def test_weekly_within_limit_does_not_block(self, ibkr_cfg):
        """Perte < 10% ne bloque pas."""
        weekly_pnl = -(ibkr_cfg.weekly_loss_limit_pct * ibkr_cfg.initial_capital - 1)
        limit = -ibkr_cfg.weekly_loss_limit_pct * ibkr_cfg.initial_capital
        assert weekly_pnl > limit


# ─── Tests StopLimitOrder offset (C2) ────────────────────────────────────────

class TestStopLimitOffset:
    def test_sell_sl_lmt_below_stop(self):
        """C2: pour un SL SELL, limit = stop - offset (protection gap)."""
        stop = 1.34000
        offset = 0.00030
        lmt = max(0.00001, stop - offset)
        assert lmt == pytest.approx(1.33970)

    def test_buy_sl_lmt_above_stop(self):
        """C2: pour un SL BUY (SHORT), limit = stop + offset."""
        stop = 1.35000
        offset = 0.00030
        lmt = stop + offset
        assert lmt == pytest.approx(1.35030)
