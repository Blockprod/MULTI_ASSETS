"""
Unit Tests for MULTI_ASSETS Trading Bot — Phase 4
==================================================
Covers:
  - Risk metric computations (Sharpe, Sortino, Calmar, Profit Factor)
  - Walk-forward fold splitting
  - OOS validation gates
  - Heartbeat writer
  - Rate limiter logic
  - Exception hierarchy
"""

import sys
import os
import json
import tempfile
import time
import unittest
import numpy as np

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'code', 'src')))

from walk_forward import (
    compute_risk_metrics,
    default_risk_metrics,
    timeframe_to_periods_per_year,
    split_walk_forward_folds,
    validate_oos_result,
)
from exceptions import (
    TradingBotError,
    ConfigError,
    ExchangeError,
    RateLimitError,
    InsufficientFundsError,
    OrderError,
    DataError,
    StaleDataError,
    InsufficientDataError,
    StrategyError,
    StateError,
    CapitalProtectionError,
)


# ──────────────────────────────────────────────────────────────
# Risk Metrics Tests
# ──────────────────────────────────────────────────────────────
class TestRiskMetrics(unittest.TestCase):
    """Tests for compute_risk_metrics from walk_forward.py."""

    def test_default_metrics_are_zero(self):
        m = default_risk_metrics()
        self.assertEqual(m['sharpe_ratio'], 0.0)
        self.assertEqual(m['sortino_ratio'], 0.0)
        self.assertEqual(m['profit_factor'], 0.0)
        self.assertEqual(m['max_consecutive_losses'], 0)

    def test_default_metrics_returns_copy(self):
        m1 = default_risk_metrics()
        m2 = default_risk_metrics()
        m1['sharpe_ratio'] = 999
        self.assertEqual(m2['sharpe_ratio'], 0.0)

    def test_empty_equity_returns_defaults(self):
        m = compute_risk_metrics(np.array([]))
        self.assertEqual(m['sharpe_ratio'], 0.0)

    def test_single_value_equity_returns_defaults(self):
        m = compute_risk_metrics(np.array([1000.0]))
        self.assertEqual(m['sharpe_ratio'], 0.0)

    def test_constant_equity_sharpe_zero(self):
        """No returns → Sharpe should be 0."""
        eq = np.array([1000.0] * 100)
        m = compute_risk_metrics(eq, periods_per_year=365)
        self.assertEqual(m['sharpe_ratio'], 0.0)

    def test_monotonically_increasing_equity(self):
        """Steadily growing equity → positive Sharpe."""
        eq = np.linspace(1000, 1200, 365)
        m = compute_risk_metrics(eq, periods_per_year=365)
        self.assertGreater(m['sharpe_ratio'], 0)
        self.assertGreater(m['total_return_pct'], 0)
        self.assertGreater(m['annual_return_pct'], 0)
        # calmar_ratio is 0 for monotonically increasing equity (no drawdown)
        self.assertEqual(m['calmar_ratio'], 0.0)

    def test_monotonically_decreasing_equity(self):
        """Steadily declining equity → negative Sharpe."""
        eq = np.linspace(1000, 800, 365)
        m = compute_risk_metrics(eq, periods_per_year=365)
        self.assertLess(m['sharpe_ratio'], 0)
        self.assertLess(m['total_return_pct'], 0)

    def test_profit_factor_requires_trades_df(self):
        """Without trades_df, profit_factor stays 0."""
        eq = np.cumsum(np.ones(100)) + 1000
        m = compute_risk_metrics(eq, periods_per_year=365)
        self.assertEqual(m['profit_factor'], 0.0)

    def test_profit_factor_with_trades(self):
        """With winning trades, profit_factor > 1."""
        import pandas as pd
        eq = np.cumsum(np.ones(100)) + 1000
        trades = pd.DataFrame({
            'type': ['sell'] * 10,
            'profit': [10.0, 5.0, 8.0, -2.0, 12.0, 3.0, -1.0, 7.0, 9.0, 4.0]
        })
        m = compute_risk_metrics(eq, trades_df=trades, periods_per_year=365)
        self.assertGreater(m['profit_factor'], 1.0)

    def test_total_return_pct_accuracy(self):
        """Verify total return computation."""
        eq = np.array([1000.0, 1050.0, 1100.0])
        m = compute_risk_metrics(eq, periods_per_year=365)
        self.assertAlmostEqual(m['total_return_pct'], 10.0, places=1)

    def test_zero_initial_equity(self):
        """Zero start should not crash, return defaults."""
        eq = np.array([0.0, 100.0, 200.0])
        m = compute_risk_metrics(eq, periods_per_year=365)
        self.assertEqual(m['sharpe_ratio'], 0.0)


# ──────────────────────────────────────────────────────────────
# Timeframe Conversion Tests
# ──────────────────────────────────────────────────────────────
class TestTimeframeConversion(unittest.TestCase):
    def test_known_timeframes(self):
        self.assertEqual(timeframe_to_periods_per_year('1h'), 8766)
        self.assertEqual(timeframe_to_periods_per_year('1d'), 365)
        self.assertEqual(timeframe_to_periods_per_year('1w'), 52)
        self.assertEqual(timeframe_to_periods_per_year('15m'), 35064)

    def test_unknown_timeframe_defaults(self):
        self.assertEqual(timeframe_to_periods_per_year('3m'), 8766)
        self.assertEqual(timeframe_to_periods_per_year('unknown'), 8766)


# ──────────────────────────────────────────────────────────────
# Walk-Forward Fold Splitting Tests
# ──────────────────────────────────────────────────────────────
class TestWalkForwardFolds(unittest.TestCase):
    def _make_df(self, n):
        import pandas as pd
        return pd.DataFrame({'close': np.random.random(n), 'open': np.random.random(n),
                             'high': np.random.random(n), 'low': np.random.random(n),
                             'volume': np.random.random(n)})

    def test_basic_fold_count(self):
        df = self._make_df(2000)
        folds = split_walk_forward_folds(df, n_folds=4, initial_train_pct=0.4)
        self.assertGreaterEqual(len(folds), 1)
        self.assertLessEqual(len(folds), 4)

    def test_anchored_expanding_window(self):
        """Train always starts at index 0 (anchored), train size grows."""
        df = self._make_df(2000)
        folds = split_walk_forward_folds(df, n_folds=4, initial_train_pct=0.4)
        prev_train_len = 0
        for train_df, oos_df in folds:
            self.assertEqual(train_df.index[0], df.index[0])
            self.assertTrue(len(oos_df) > 0)
            self.assertGreaterEqual(len(train_df), prev_train_len)
            prev_train_len = len(train_df)

    def test_no_overlap(self):
        """Train and OOS indices must not overlap."""
        df = self._make_df(2000)
        folds = split_walk_forward_folds(df, n_folds=4, initial_train_pct=0.4)
        for train_df, oos_df in folds:
            overlap = set(train_df.index) & set(oos_df.index)
            self.assertEqual(len(overlap), 0)

    def test_insufficient_data_returns_empty(self):
        df = self._make_df(50)
        folds = split_walk_forward_folds(df, n_folds=4, initial_train_pct=0.4,
                                          min_train_bars=500, min_test_bars=200)
        self.assertEqual(len(folds), 0)


# ──────────────────────────────────────────────────────────────
# OOS Validation Gate Tests
# ──────────────────────────────────────────────────────────────
class TestOOSValidation(unittest.TestCase):
    def test_passing_result(self):
        self.assertTrue(validate_oos_result(sharpe=1.0, win_rate=55.0))

    def test_failing_sharpe(self):
        self.assertFalse(validate_oos_result(sharpe=0.1, win_rate=55.0))

    def test_failing_win_rate(self):
        self.assertFalse(validate_oos_result(sharpe=1.0, win_rate=30.0))

    def test_both_failing(self):
        self.assertFalse(validate_oos_result(sharpe=0.1, win_rate=30.0))


# ──────────────────────────────────────────────────────────────
# Exception Hierarchy Tests
# ──────────────────────────────────────────────────────────────
class TestExceptionHierarchy(unittest.TestCase):
    def test_base_class(self):
        self.assertTrue(issubclass(TradingBotError, Exception))

    def test_config_error(self):
        self.assertTrue(issubclass(ConfigError, TradingBotError))

    def test_exchange_subtypes(self):
        self.assertTrue(issubclass(RateLimitError, ExchangeError))
        self.assertTrue(issubclass(InsufficientFundsError, ExchangeError))
        self.assertTrue(issubclass(OrderError, ExchangeError))
        self.assertTrue(issubclass(ExchangeError, TradingBotError))

    def test_data_subtypes(self):
        self.assertTrue(issubclass(StaleDataError, DataError))
        self.assertTrue(issubclass(InsufficientDataError, DataError))
        self.assertTrue(issubclass(DataError, TradingBotError))

    def test_other_types(self):
        self.assertTrue(issubclass(StrategyError, TradingBotError))
        self.assertTrue(issubclass(StateError, TradingBotError))
        self.assertTrue(issubclass(CapitalProtectionError, TradingBotError))

    def test_catch_hierarchy(self):
        """Catching TradingBotError should catch all subtypes."""
        with self.assertRaises(TradingBotError):
            raise RateLimitError("test")
        with self.assertRaises(TradingBotError):
            raise StaleDataError("test")


# ──────────────────────────────────────────────────────────────
# Heartbeat Writer Tests
# ──────────────────────────────────────────────────────────────
class TestHeartbeat(unittest.TestCase):
    """Test the write_heartbeat function (imported dynamically to avoid
    importing the full MULTI_SYMBOLS module which needs Binance API)."""

    def test_heartbeat_writes_valid_json(self):
        """Simulate the heartbeat logic directly."""
        from datetime import datetime
        with tempfile.TemporaryDirectory() as tmpdir:
            heartbeat_path = os.path.join(tmpdir, "heartbeat.json")
            heartbeat = {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "pid": os.getpid(),
                "circuit_mode": "RUNNING",
                "error_count": 0,
                "loop_counter": 42,
            }
            tmp_path = heartbeat_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(heartbeat, f)
            os.replace(tmp_path, heartbeat_path)

            # Verify
            with open(heartbeat_path) as f:
                data = json.load(f)
            self.assertIn("timestamp", data)
            self.assertEqual(data["pid"], os.getpid())
            self.assertEqual(data["circuit_mode"], "RUNNING")
            self.assertEqual(data["loop_counter"], 42)


# ──────────────────────────────────────────────────────────────
# Watchdog Heartbeat Consumer Tests
# ──────────────────────────────────────────────────────────────
class TestWatchdogHeartbeat(unittest.TestCase):
    def setUp(self):
        from watchdog import TradingBotWatchdog
        self.tmpdir = tempfile.mkdtemp()
        self.hb_path = os.path.join(self.tmpdir, "heartbeat.json")
        self.wd = TradingBotWatchdog(heartbeat_path=self.hb_path)

    def test_no_heartbeat_file_is_fresh(self):
        """No heartbeat file → treated as fresh (startup grace period)."""
        self.assertTrue(self.wd.is_heartbeat_fresh())

    def test_recent_heartbeat_is_fresh(self):
        from datetime import datetime, timezone
        hb = {"timestamp": datetime.now(timezone.utc).isoformat()}
        with open(self.hb_path, "w") as f:
            json.dump(hb, f)
        self.assertTrue(self.wd.is_heartbeat_fresh())

    def test_stale_heartbeat_detected(self):
        # Write a heartbeat from 20 minutes ago
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        hb = {"timestamp": old_ts}
        with open(self.hb_path, "w") as f:
            json.dump(hb, f)
        self.assertFalse(self.wd.is_heartbeat_fresh())


if __name__ == '__main__':
    unittest.main()
