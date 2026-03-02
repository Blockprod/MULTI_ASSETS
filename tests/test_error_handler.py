"""Tests for error_handler.py — CircuitBreaker + ErrorHandler."""
import sys
import os
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

from error_handler import (
    CircuitBreaker, ErrorHandler, SafeMode,
    initialize_error_handler, get_error_handler,
)


class TestCircuitBreaker:
    """Tests pour CircuitBreaker."""

    def test_initial_state(self):
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=60)
        assert cb.is_available()
        assert cb.failure_count == 0
        assert cb.mode == SafeMode.RUNNING

    def test_trip_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_available()  # 2 < 3, still available
        cb.record_failure()
        assert not cb.is_available()  # 3 >= 3, tripped
        assert cb.mode == SafeMode.PAUSED

    def test_reset_on_success(self):
        cb = CircuitBreaker(failure_threshold=2, timeout_seconds=60)
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_available()
        cb.record_success()
        assert cb.is_available()
        assert cb.mode == SafeMode.RUNNING
        assert cb.failure_count == 0

    def test_timeout_recovery(self):
        cb = CircuitBreaker(failure_threshold=1, timeout_seconds=1)
        cb.record_failure()
        assert not cb.is_available()
        # Simulate timeout expiry
        cb.last_failure_time = datetime.now() - timedelta(seconds=5)
        assert cb.is_available()
        assert cb.mode == SafeMode.RUNNING

    def test_get_status_json(self):
        import json
        cb = CircuitBreaker()
        status = json.loads(cb.get_status())
        assert "mode" in status
        assert status["mode"] == "RUNNING"
        assert "failure_count" in status


@patch('email_utils.send_email_alert', new_callable=MagicMock)
class TestErrorHandler:
    """Tests pour ErrorHandler (emails mockés pour éviter les envois réels)."""

    def test_handle_error_records_history(self, _mock_email):
        handler = ErrorHandler()
        err = ValueError("test error")
        handler.handle_error(err, context="test_context")
        assert len(handler.error_history) == 1
        assert handler.error_history[0]["context"] == "test_context"
        assert handler.error_history[0]["error_type"] == "ValueError"

    def test_handle_error_with_fallback(self, _mock_email):
        handler = ErrorHandler()
        fallback = MagicMock(return_value="recovered")
        should_continue, result = handler.handle_error(
            ValueError("test"),
            context="test",
            safe_fallback=fallback,
        )
        assert should_continue is True
        assert result == "recovered"
        fallback.assert_called_once()

    def test_handle_error_critical_sets_alert_mode(self, _mock_email):
        handler = ErrorHandler()
        handler.handle_error(RuntimeError("critical!"), context="test", critical=True)
        assert handler.circuit_breaker.mode == SafeMode.ALERT

    def test_safe_execute_success(self, _mock_email):
        handler = ErrorHandler()
        success, result = handler.safe_execute(
            func=lambda x: x * 2,
            func_args=(5,),
            context="multiply",
        )
        assert success is True
        assert result == 10

    def test_safe_execute_failure_with_fallback(self, _mock_email):
        handler = ErrorHandler()

        def failing_func():
            raise ValueError("boom")

        success, result = handler.safe_execute(
            func=failing_func,
            context="test_fail",
            safe_fallback=lambda: "fallback_value",
        )
        assert success is True
        assert result == "fallback_value"

    def test_safe_execute_skips_when_circuit_open(self, _mock_email):
        handler = ErrorHandler()
        # Trip the circuit
        for _ in range(3):
            handler.circuit_breaker.record_failure()
        success, result = handler.safe_execute(
            func=lambda: "should not run",
            context="test_skip",
        )
        assert success is False
        assert result is None

    def test_error_history_max_limit(self, _mock_email):
        handler = ErrorHandler()
        handler.max_history = 5
        for i in range(10):
            handler.handle_error(ValueError(f"err {i}"), context=f"ctx_{i}")
        assert len(handler.error_history) <= 5

    def test_clear_history(self, _mock_email):
        handler = ErrorHandler()
        handler.handle_error(ValueError("err"), context="ctx")
        assert len(handler.error_history) == 1
        handler.clear_history()
        assert len(handler.error_history) == 0


class TestGlobalErrorHandler:
    """Tests pour initialize/get_error_handler."""

    def test_initialize_and_get(self):
        handler = initialize_error_handler()
        retrieved = get_error_handler()
        assert handler is retrieved
        assert isinstance(handler, ErrorHandler)

    def test_get_before_init_raises(self):
        import error_handler as eh
        eh._error_handler = None
        with pytest.raises(RuntimeError, match="not initialized"):
            get_error_handler()

    @patch('email_utils.send_email_alert')
    def test_send_alert_email_delegates_to_email_utils(self, mock_send):
        """send_alert_email delegates to email_utils.send_email_alert."""
        import error_handler as eh
        eh._last_alert_email_time = 0.0  # Reset throttle for test
        handler = ErrorHandler(email_config={})
        handler.send_alert_email("test subject", "test body")
        mock_send.assert_called_once()
