"""Tests for position sizing functions in MULTI_SYMBOLS.py."""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

# Mock config and other globals before importing the module functions
# We need to mock the Config and env vars to avoid loading real keys
_mock_config = MagicMock()
_mock_config.risk_per_trade = 0.05
_mock_config.atr_stop_multiplier = 3.0
_mock_config.atr_multiplier = 5.5
_mock_config.taker_fee = 0.0007
_mock_config.maker_fee = 0.0002
_mock_config.slippage_buy = 0.0001
_mock_config.slippage_sell = 0.0001
_mock_config.initial_wallet = 10000.0
_mock_config.target_volatility_pct = 0.02


class TestComputePositionSizeByRisk:
    """Tests for compute_position_size_by_risk."""

    def test_basic_risk_sizing(self):
        """Test risk-based sizing: risk 5% of 10000 with ATR stop of 3*ATR."""
        # Import with mock
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'test', 'BINANCE_SECRET_KEY': 'test',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'test'
        }):
            try:
                from MULTI_SYMBOLS import compute_position_size_by_risk
            except Exception:
                pytest.skip("Cannot import MULTI_SYMBOLS (missing dependencies)")
        
        # equity=10000, ATR=100, entry=50000, risk_pct=0.05, stop_mult=3.0
        # stop_distance = 3.0 * 100 = 300
        # max_risk_usd = 10000 * 0.05 = 500
        # qty = 500 / 300 = 1.6667
        qty = compute_position_size_by_risk(
            equity=10000.0, atr_value=100.0, entry_price=50000.0,
            risk_pct=0.05, stop_atr_multiplier=3.0
        )
        assert qty > 0
        assert abs(qty - 1.6667) < 0.01

    def test_zero_atr_returns_zero(self):
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'test', 'BINANCE_SECRET_KEY': 'test',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'test'
        }):
            try:
                from MULTI_SYMBOLS import compute_position_size_by_risk
            except Exception:
                pytest.skip("Cannot import")
        qty = compute_position_size_by_risk(equity=10000, atr_value=0, entry_price=100)
        assert qty == 0.0

    def test_zero_equity_returns_zero(self):
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'test', 'BINANCE_SECRET_KEY': 'test',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'test'
        }):
            try:
                from MULTI_SYMBOLS import compute_position_size_by_risk
            except Exception:
                pytest.skip("Cannot import")
        qty = compute_position_size_by_risk(equity=0, atr_value=100, entry_price=50000)
        assert qty == 0.0


class TestComputePositionSizeFixedNotional:
    """Tests for compute_position_size_fixed_notional."""

    def test_fixed_notional(self):
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'test', 'BINANCE_SECRET_KEY': 'test',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'test'
        }):
            try:
                from MULTI_SYMBOLS import compute_position_size_fixed_notional
            except Exception:
                pytest.skip("Cannot import")
        # notional=1000, entry_price=200 → qty = 5.0
        qty = compute_position_size_fixed_notional(
            equity=10000, notional_per_trade_usd=1000, entry_price=200
        )
        assert abs(qty - 5.0) < 0.001

    def test_default_notional_10pct(self):
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'test', 'BINANCE_SECRET_KEY': 'test',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'test'
        }):
            try:
                from MULTI_SYMBOLS import compute_position_size_fixed_notional
            except Exception:
                pytest.skip("Cannot import")
        # No notional specified → default = max(100, 10000*0.1) = 1000
        # entry_price = 500 → qty = 1000/500 = 2.0
        qty = compute_position_size_fixed_notional(equity=10000, entry_price=500)
        assert abs(qty - 2.0) < 0.001

    def test_zero_price(self):
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'test', 'BINANCE_SECRET_KEY': 'test',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'test'
        }):
            try:
                from MULTI_SYMBOLS import compute_position_size_fixed_notional
            except Exception:
                pytest.skip("Cannot import")
        qty = compute_position_size_fixed_notional(equity=10000, entry_price=0)
        assert qty == 0.0


class TestComputePositionSizeVolatilityParity:
    """Tests for compute_position_size_volatility_parity."""

    def test_volatility_parity(self):
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'test', 'BINANCE_SECRET_KEY': 'test',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'test'
        }):
            try:
                from MULTI_SYMBOLS import compute_position_size_volatility_parity
            except Exception:
                pytest.skip("Cannot import")
        # equity=10000, ATR=50, entry=1000, target=0.02
        # qty = (10000 * 0.02) / (50 * 1000) = 200 / 50000 = 0.004
        qty = compute_position_size_volatility_parity(
            equity=10000, atr_value=50, entry_price=1000, target_volatility_pct=0.02
        )
        assert abs(qty - 0.004) < 0.0001

    def test_high_atr_reduces_size(self):
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'test', 'BINANCE_SECRET_KEY': 'test',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'test'
        }):
            try:
                from MULTI_SYMBOLS import compute_position_size_volatility_parity
            except Exception:
                pytest.skip("Cannot import")
        qty_low = compute_position_size_volatility_parity(
            equity=10000, atr_value=50, entry_price=1000, target_volatility_pct=0.02
        )
        qty_high = compute_position_size_volatility_parity(
            equity=10000, atr_value=200, entry_price=1000, target_volatility_pct=0.02
        )
        assert qty_high < qty_low  # Higher volatility → smaller position

    def test_zero_atr_returns_zero(self):
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'test', 'BINANCE_SECRET_KEY': 'test',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'test'
        }):
            try:
                from MULTI_SYMBOLS import compute_position_size_volatility_parity
            except Exception:
                pytest.skip("Cannot import")
        qty = compute_position_size_volatility_parity(
            equity=10000, atr_value=0, entry_price=1000
        )
        assert qty == 0.0
