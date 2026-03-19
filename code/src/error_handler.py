"""
ERROR HANDLER MODULE - Circuit Breaker + Safe Mode for Trading Bot
Detects runtime errors and pauses trading automatically while alerting
"""

import sys
import os
# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

import traceback
import json
import logging
import threading
from datetime import datetime
from typing import Dict, Callable, Any, Optional, Tuple
from enum import Enum
from email_templates import error_handler_alert_body, handle_error_alert

import time as _time

logger = logging.getLogger(__name__)

# Throttle des emails d'alerte : max 1 email toutes les N secondes
# P2-07: valeur par défaut, surchargée par config.email_cooldown_seconds
_EMAIL_COOLDOWN_SECONDS = 300
_last_alert_email_time: float = 0.0
_email_throttle_lock = threading.Lock()  # P1-02: protège _last_alert_email_time


def _get_email_cooldown():
    """P2-07: Charge le cooldown depuis la config si disponible."""
    try:
        from bot_config import config
        return config.email_cooldown_seconds
    except Exception:
        return _EMAIL_COOLDOWN_SECONDS


class SafeMode(Enum):
    """Bot operating modes"""
    RUNNING = "RUNNING"  # Normal operation
    PAUSED = "PAUSED"    # Error detected - no new orders
    ALERT = "ALERT"      # Critical error - human intervention needed


class CircuitBreaker:
    """Simple circuit breaker to prevent cascading failures.

    Thread-safe (P0-05): all state mutations are protected by an RLock so that
    concurrent per-pair trading threads cannot corrupt shared counters.
    """

    def __init__(self, failure_threshold: int = 3, timeout_seconds: int = 300):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.last_failure_time = None
        self.is_open = False
        self.mode = SafeMode.RUNNING
        self._lock = threading.RLock()  # P0-05: protects all mutable attributes

    def record_success(self):
        """Reset on successful execution."""
        with self._lock:
            self.failure_count = 0
            self.is_open = False
            self.mode = SafeMode.RUNNING
        logger.info("[CIRCUIT] Reset - Normal operation resumed")

    def record_failure(self):
        """Record failure and possibly trip circuit."""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()
            if self.failure_count >= self.failure_threshold:
                self.is_open = True
                self.mode = SafeMode.PAUSED
                logger.warning(f"[CIRCUIT] TRIPPED - {self.failure_count} failures detected. Bot paused.")

    def is_available(self) -> bool:
        """Check if circuit allows execution."""
        with self._lock:
            if not self.is_open:
                return True

            # Check if timeout expired
            if self.last_failure_time is None:
                return True
            time_since_failure = (datetime.now() - self.last_failure_time).total_seconds()
            if time_since_failure > self.timeout_seconds:
                self.failure_count = 0
                self.is_open = False
                self.mode = SafeMode.RUNNING
                logger.info("[CIRCUIT] Timeout expired - Attempting recovery")
                return True

            return False

    def get_status(self) -> str:
        """Get circuit breaker status"""
        status = {
            "mode": self.mode.value,
            "is_open": self.is_open,
            "failure_count": self.failure_count,
            "last_failure": self.last_failure_time.isoformat() if self.last_failure_time else None
        }
        return json.dumps(status, indent=2)


class ErrorHandler:
    """Central error handling with auto-pause and alerting."""

    def __init__(self, email_config: Optional[Dict[str, str]] = None):
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, timeout_seconds=300)
        self.error_history: list[dict] = []
        self.email_config = email_config or {}
        self.max_history = 50
        self._history_lock = threading.Lock()  # P0-05: protects error_history list

    def send_alert_email(self, subject: str, body: str, error_details: Optional[Dict] = None, critical: bool = False):
        """Send alert email via email_utils, throttled to 1 every 5 minutes.

        Les alertes critiques (critical=True) bypasse le throttle pour garantir
        que les alertes P0/EMERGENCY ne soient jamais silencieusement supprimées.
        """
        global _last_alert_email_time
        now = _time.time()
        if not critical:
            with _email_throttle_lock:  # P1-02: atomic check-and-set
                _cooldown = _get_email_cooldown()  # P2-07
                if (now - _last_alert_email_time) < _cooldown:
                    logger.info(f"[ALERT] Email throttled (cooldown {_cooldown}s) — skipping: {subject}")
                    return
                _last_alert_email_time = now
        else:
            logger.info(f"[ALERT] Email critique — throttle bypassé : {subject}")
        try:
            from email_utils import send_email_alert

            text_content = error_handler_alert_body(
                inner_body=body,
                mode_value=self.circuit_breaker.mode.value,
                timeout_seconds=self.circuit_breaker.timeout_seconds,
                error_details=error_details,
            )

            send_email_alert(f"[BOT ALERT] {subject}", text_content)
            logger.info("[ALERT] Email sent successfully")

        except Exception as e:
            logger.error(f"[ALERT] Failed to send email: {e}")

    def handle_error(
        self,
        error: Exception,
        context: str,
        safe_fallback: Optional[Callable[[], Any]] = None,
        critical: bool = False
    ) -> Tuple[bool, Optional[Any]]:
        """
        Central error handler with automatic pause & recovery

        Args:
            error: The exception that occurred
            context: Where error occurred (e.g. "execute_real_trades")
            safe_fallback: Function to call for safe recovery
            critical: If True, trigger ALERT mode

        Returns:
            (should_continue, fallback_result)
        """
        # Record error
        error_record = {
            "timestamp": datetime.now().isoformat(),
            "context": context,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
            "circuit_mode": self.circuit_breaker.mode.value
        }
        with self._history_lock:  # P0-05: thread-safe list append
            self.error_history.append(error_record)
            if len(self.error_history) > self.max_history:
                self.error_history.pop(0)

        # Log
        logger.error(f"[ERROR] {context}: {type(error).__name__}: {str(error)}")
        logger.debug(f"[TRACEBACK]\n{traceback.format_exc()}")

        # P1-07: Try fallback FIRST — only record_failure if fallback also fails.
        # Previous order (record_failure then fallback) caused spurious PAUSED state.
        fallback_result = None
        if safe_fallback:
            try:
                logger.info(f"[RECOVERY] Attempting safe fallback for {context}")
                fallback_result = safe_fallback()
                logger.info("[RECOVERY] Fallback successful")
                self.circuit_breaker.record_success()
                return True, fallback_result
            except Exception as fallback_error:
                logger.error(f"[RECOVERY] Fallback failed: {fallback_error}")

        # Record failure only when no fallback succeeded
        self.circuit_breaker.record_failure()

        # Notify operator when circuit just opened (tripped to PAUSED)
        if (self.circuit_breaker.is_open
                and self.circuit_breaker.failure_count == self.circuit_breaker.failure_threshold):
            self.send_alert_email(
                "CIRCUIT BREAKER OUVERT — Bot en mode PAUSED",
                f"Le circuit breaker a atteint le seuil de "
                f"{self.circuit_breaker.failure_threshold} erreurs consecutives.\n\n"
                f"Le bot est en mode PAUSED. Aucun nouvel ordre ne sera passe.\n"
                f"Recuperation automatique dans {self.circuit_breaker.timeout_seconds}s.\n\n"
                f"Derniere erreur : {context} — {type(error).__name__}: {str(error)[:200]}",
                critical=True,
            )

        # Send alert
        _alert_subject, alert_body = handle_error_alert(
            context=context,
            error_type=type(error).__name__,
            error_msg=str(error),
            mode_value=self.circuit_breaker.mode.value,
            failure_count=self.circuit_breaker.failure_count,
            critical=critical,
        )

        if critical:
            self.circuit_breaker.mode = SafeMode.ALERT
            self.send_alert_email(
                _alert_subject,
                alert_body,
                error_details=error_record,
                critical=True,
            )
        else:
            self.send_alert_email(
                _alert_subject,
                alert_body,
                error_details=error_record,
            )

        # Return based on circuit state
        if self.circuit_breaker.is_available():
            logger.warning("[CONTINUE] Continuing despite error (circuit still available)")
            return True, None
        else:
            logger.critical(f"[PAUSE] Bot paused - Circuit breaker tripped. Recovery in {self.circuit_breaker.timeout_seconds}s")
            return False, None

    def safe_execute(
        self,
        func: Callable,
        func_args: tuple = (),
        func_kwargs: Optional[dict] = None,
        context: str = "unknown",
        safe_fallback: Optional[Callable] = None,
        critical: bool = False
    ) -> Tuple[bool, Optional[Any]]:
        """
        Safely execute a function with automatic error handling

        Returns:
            (success, result)
        """
        if func_kwargs is None:
            func_kwargs = {}

        # Check circuit before execution
        if not self.circuit_breaker.is_available():
            logger.warning(f"[SKIP] {context} skipped - circuit breaker open")
            return False, None

        try:
            result = func(*func_args, **func_kwargs)
            self.circuit_breaker.record_success()
            return True, result

        except Exception as e:
            should_continue, fallback_result = self.handle_error(
                error=e,
                context=context,
                safe_fallback=safe_fallback,
                critical=critical
            )
            return should_continue, fallback_result

    def get_status(self) -> str:
        """Get handler status"""
        status = {
            "circuit_breaker": json.loads(self.circuit_breaker.get_status()),
            "recent_errors": len(self.error_history),
            "last_error": self.error_history[-1] if self.error_history else None
        }
        return json.dumps(status, indent=2)

    def clear_history(self):
        """Clear error history (P1-03: thread-safe)."""
        with self._history_lock:
            self.error_history = []
        with self.circuit_breaker._lock:
            self.circuit_breaker.failure_count = 0
        logger.info("[HANDLER] Error history cleared")


# Global instance
_error_handler: Optional[ErrorHandler] = None


def initialize_error_handler(email_config: Optional[Dict[str, str]] = None) -> ErrorHandler:
    """Initialize global error handler"""
    global _error_handler
    _error_handler = ErrorHandler(email_config=email_config)
    logger.info("[HANDLER] Error handler initialized")
    return _error_handler


def get_error_handler() -> ErrorHandler:
    """Get global error handler (must be initialized first)"""
    if _error_handler is None:
        raise RuntimeError("Error handler not initialized. Call initialize_error_handler() first.")
    return _error_handler
