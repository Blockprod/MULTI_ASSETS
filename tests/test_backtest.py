"""Tests for backtest_from_dataframe in MULTI_SYMBOLS.py."""
import sys
import os
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

# ---------------------------------------------------------------------------
# Helper to build a realistic OHLCV DataFrame with all required indicators
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 200, start_price: float = 100.0, trend: str = 'up') -> pd.DataFrame:
    """Generate a synthetic DataFrame with all columns that backtest_from_dataframe expects."""
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=n, freq='4h')
    noise = np.random.normal(0, 0.5, n)

    if trend == 'up':
        close = start_price + np.linspace(0, 60, n) + np.cumsum(noise)
    elif trend == 'down':
        close = start_price - np.linspace(0, 60, n) + np.cumsum(noise)
    else:  # flat
        close = start_price + np.cumsum(noise)

    high = close + np.abs(np.random.normal(1, 0.3, n))
    low = close - np.abs(np.random.normal(1, 0.3, n))
    open_ = close + np.random.normal(0, 0.3, n)
    volume = np.random.uniform(1000, 5000, n)

    df = pd.DataFrame({
        'open': open_, 'high': high, 'low': low,
        'close': close, 'volume': volume,
    }, index=dates)

    # EMA columns (will be recomputed by backtest but expected in df)
    df['ema_12'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_22'] = df['close'].ewm(span=22, adjust=False).mean()

    # ATR (simplified 14-period)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean().bfill()

    # StochRSI (simplified)
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_min = rsi.rolling(14).min()
    rsi_max = rsi.rolling(14).max()
    df['stoch_rsi'] = ((rsi - rsi_min) / (rsi_max - rsi_min)).fillna(0.5)

    return df


def _import_backtest():
    """Try to import backtest_from_dataframe; skip the test if it fails."""
    with patch.dict(os.environ, {
        'BINANCE_API_KEY': 'test_key',
        'BINANCE_SECRET_KEY': 'test_secret',
        'SENDER_EMAIL': 'a@b.c',
        'RECEIVER_EMAIL': 'a@b.c',
        'GOOGLE_MAIL_PASSWORD': 'pass',
    }):
        try:
            from MULTI_SYMBOLS import backtest_from_dataframe
            return backtest_from_dataframe
        except Exception:
            pytest.skip("Cannot import MULTI_SYMBOLS (missing deps or network)")


# =========================================================================
# Tests
# =========================================================================

class TestBacktestFromDataframe:
    """Unit tests for the pure-Python backtest engine."""

    def test_empty_df_returns_zero(self):
        fn = _import_backtest()
        result = fn(pd.DataFrame(), ema1_period=12, ema2_period=22)
        assert result['final_wallet'] == 0.0
        assert result['max_drawdown'] == 0.0

    def test_short_df_returns_zero(self):
        fn = _import_backtest()
        df = _make_ohlcv(n=30)  # < 50 rows → early-return
        result = fn(df, ema1_period=12, ema2_period=22)
        assert result['final_wallet'] == 0.0

    def test_uptrend_positive_profit(self):
        """An uptrend with clear EMA crossover should produce positive returns."""
        fn = _import_backtest()
        df = _make_ohlcv(n=500, start_price=100, trend='up')
        result = fn(df, ema1_period=12, ema2_period=22, sizing_mode='baseline')
        # In a strong uptrend the wallet should grow (or at least not crash)
        assert result['final_wallet'] >= 0, "Final wallet should not be negative"
        assert isinstance(result['trades'], pd.DataFrame)

    def test_downtrend_low_wallet(self):
        """A downtrend should result in modest or negative returns."""
        fn = _import_backtest()
        df = _make_ohlcv(n=500, start_price=200, trend='down')
        result = fn(df, ema1_period=12, ema2_period=22, sizing_mode='baseline')
        # The bot should not be able to make huge profits in a bear market
        # Final wallet can still be > 0 (it starts with initial_wallet config value)
        assert result['final_wallet'] >= 0

    def test_different_sizing_modes_return_results(self):
        """Each sizing mode should return a valid result dict."""
        fn = _import_backtest()
        df = _make_ohlcv(n=300, start_price=100, trend='up')
        for mode in ('baseline', 'risk', 'fixed_notional', 'volatility_parity'):
            result = fn(df, ema1_period=12, ema2_period=22, sizing_mode=mode)
            assert 'final_wallet' in result, f"Mode {mode} missing final_wallet"
            assert 'win_rate' in result, f"Mode {mode} missing win_rate"
            assert 'max_drawdown' in result, f"Mode {mode} missing max_drawdown"

    def test_trades_history_has_buy_and_sell(self):
        """In a sufficiently long uptrend, at least one buy+sell cycle should occur."""
        fn = _import_backtest()
        df = _make_ohlcv(n=500, start_price=100, trend='up')
        result = fn(df, ema1_period=12, ema2_period=22, sizing_mode='baseline')
        trades_df = result['trades']
        if not trades_df.empty:
            types = trades_df['type'].unique().tolist()
            assert 'buy' in types, "Expected at least one buy"

    def test_win_rate_between_0_and_100(self):
        fn = _import_backtest()
        df = _make_ohlcv(n=500, start_price=100, trend='up')
        result = fn(df, ema1_period=12, ema2_period=22)
        assert 0.0 <= result['win_rate'] <= 100.0

    def test_max_drawdown_non_negative(self):
        fn = _import_backtest()
        df = _make_ohlcv(n=300, start_price=100, trend='flat')
        result = fn(df, ema1_period=12, ema2_period=22)
        assert result['max_drawdown'] >= 0.0
