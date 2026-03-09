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


# ══════════════════════════════════════════════════════════════════════════════
# C-11 — Tests complémentaires pour couverture complète
# ══════════════════════════════════════════════════════════════════════════════

class TestC11Complements:
    """Tests ciblant les branches manquantes."""

    @patch('email_utils.send_email_alert', side_effect=Exception("SMTP down"))
    def test_send_alert_email_exception_logged_not_raised(self, _mock):
        """Si l'envoi email échoue, l'exception est loggée mais pas propagée."""
        import error_handler as eh
        eh._last_alert_email_time = 0.0
        handler = ErrorHandler()
        # Ne doit pas lever
        handler.send_alert_email("subject", "body")

    @patch('email_utils.send_email_alert', new_callable=MagicMock)
    def test_handle_error_fallback_failure_records_failure(self, _mock_email):
        """Si le fallback échoue aussi, record_failure est appelé."""
        handler = ErrorHandler()

        def bad_fallback():
            raise RuntimeError("fallback also crashed")

        should_continue, result = handler.handle_error(
            ValueError("original"),
            context="test_ctx",
            safe_fallback=bad_fallback,
        )
        # Fallback a échoué → failure enregistrée
        assert handler.circuit_breaker.failure_count >= 1

    @patch('email_utils.send_email_alert', new_callable=MagicMock)
    def test_get_status_returns_valid_json(self, _mock_email):
        """get_status retourne un JSON valide avec circuit_breaker et historique."""
        import json
        handler = ErrorHandler()
        handler.handle_error(ValueError("err"), context="ctx")
        status = json.loads(handler.get_status())
        assert "circuit_breaker" in status
        assert "recent_errors" in status
        assert status["recent_errors"] >= 1
        assert "last_error" in status
        assert status["last_error"]["context"] == "ctx"


# ══════════════════════════════════════════════════════════════════════════════
# P2-10 — Tests supplémentaires pour couverture ≥ 60%
# ══════════════════════════════════════════════════════════════════════════════

class TestP210Coverage:
    """P2-10: branches manquantes dans error_handler.py."""

    @patch('email_utils.send_email_alert', new_callable=MagicMock)
    def test_send_alert_email_throttled_skip(self, mock_send):
        """Deuxième appel rapproché est throttled → email non envoyé."""
        import error_handler as eh
        eh._last_alert_email_time = 0.0  # Reset
        handler = ErrorHandler()
        handler.send_alert_email("first", "body")
        assert mock_send.call_count == 1
        # Deuxième appel immédiat → throttled
        handler.send_alert_email("second", "body")
        assert mock_send.call_count == 1  # pas d'appel supplémentaire

    def test_safe_execute_failure_no_fallback(self):
        """safe_execute sans fallback: erreur enregistrée, CB still available → continue."""
        handler = ErrorHandler()

        def failing_func():
            raise RuntimeError("boom")

        success, result = handler.safe_execute(failing_func, context="no_fallback")
        # CB still available → should_continue = True
        assert success is True
        assert result is None
        assert len(handler.error_history) >= 1

    def test_safe_execute_with_kwargs(self):
        """safe_execute avec func_kwargs transmet les arguments."""
        handler = ErrorHandler()

        def adder(a, b=0):
            return a + b

        success, result = handler.safe_execute(
            adder, context="kwargs_test", func_args=(10,), func_kwargs={"b": 5}
        )
        assert success is True
        assert result == 15

    def test_clear_history_resets_circuit_breaker_count(self):
        """clear_history remet failure_count du circuit breaker à 0."""
        handler = ErrorHandler()
        handler.circuit_breaker.record_failure()
        handler.circuit_breaker.record_failure()
        assert handler.circuit_breaker.failure_count == 2
        handler.error_history.append({"error": "test"})  # Simulate
        handler.clear_history()
        assert handler.circuit_breaker.failure_count == 0
        assert len(handler.error_history) == 0

    def test_handle_error_non_critical_returns_true(self):
        """Erreur non-critique (failure_count < threshold) → should_continue=True."""
        handler = ErrorHandler()
        handler.circuit_breaker = CircuitBreaker(failure_threshold=5, timeout_seconds=60)
        success, result = handler.handle_error(
            ValueError("minor"), context="test"
        )
        assert success is True
        assert handler.circuit_breaker.failure_count == 1
