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
from datetime import datetime
from typing import Dict, Callable, Any, Optional, Tuple
from enum import Enum
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


class SafeMode(Enum):
    """Bot operating modes"""
    RUNNING = "RUNNING"  # Normal operation
    PAUSED = "PAUSED"    # Error detected - no new orders
    ALERT = "ALERT"      # Critical error - human intervention needed


class CircuitBreaker:
    """Simple circuit breaker to prevent cascading failures"""
    
    def __init__(self, failure_threshold: int = 3, timeout_seconds: int = 300):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.last_failure_time = None
        self.is_open = False
        self.mode = SafeMode.RUNNING
    
    def record_success(self):
        """Reset on successful execution"""
        self.failure_count = 0
        self.is_open = False
        self.mode = SafeMode.RUNNING
        logger.info("[CIRCUIT] Reset - Normal operation resumed")
    
    def record_failure(self):
        """Record failure and possibly trip circuit"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        if self.failure_count >= self.failure_threshold:
            self.is_open = True
            self.mode = SafeMode.PAUSED
            logger.warning(f"[CIRCUIT] TRIPPED - {self.failure_count} failures detected. Bot paused.")
    
    def is_available(self) -> bool:
        """Check if circuit allows execution"""
        if not self.is_open:
            return True
        
        # Check if timeout expired
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
    """Central error handling with auto-pause and alerting"""
    
    def __init__(self, email_config: Optional[Dict[str, str]] = None):
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, timeout_seconds=300)
        self.error_history = []
        self.email_config = email_config or {}
        self.max_history = 50
    
    def send_alert_email(self, subject: str, body: str, error_details: Dict = None):
        """Send immediate alert email"""
        if not self.email_config.get('smtp_server'):
            logger.warning("[ALERT] Email not configured - skipping notification")
            return
        
        try:
            # Build email
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"[BOT ALERT] {subject}"
            msg['From'] = self.email_config.get('sender_email')
            msg['To'] = self.email_config.get('recipient_email')
            
            # Plain text version
            text_content = f"""
ALERTE ERREUR DU BOT DE TRADING

Heure: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Mode: {self.circuit_breaker.mode.value}

DETAILS:
{body}

ACTION RECOMMANDÉE:
1. Consulter les logs en temps réel
2. Vérifier l'état du bot
3. Corriger manuellement si nécessaire
4. Le bot reprendra après {self.circuit_breaker.timeout_seconds}s si pas d'intervention

---
Message automatique du Bot de Trading Crypto
            """
            
            if error_details:
                text_content += f"\n\nDETAILS TECHNIQUES:\n{json.dumps(error_details, indent=2)}"
            
            part = MIMEText(text_content, 'plain')
            msg.attach(part)
            
            # Send
            with smtplib.SMTP(self.email_config.get('smtp_server'), int(self.email_config.get('smtp_port', 587))) as server:
                server.starttls()
                server.login(self.email_config.get('sender_email'), self.email_config.get('sender_password'))
                server.send_message(msg)
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
        self.error_history.append(error_record)
        if len(self.error_history) > self.max_history:
            self.error_history.pop(0)
        
        # Log
        logger.error(f"[ERROR] {context}: {type(error).__name__}: {str(error)}")
        logger.debug(f"[TRACEBACK]\n{traceback.format_exc()}")
        
        # Update circuit breaker
        self.circuit_breaker.record_failure()
        
        # Try fallback if provided
        fallback_result = None
        if safe_fallback:
            try:
                logger.info(f"[RECOVERY] Attempting safe fallback for {context}")
                fallback_result = safe_fallback()
                logger.info(f"[RECOVERY] Fallback successful")
                self.circuit_breaker.record_success()
                return True, fallback_result
            except Exception as fallback_error:
                logger.error(f"[RECOVERY] Fallback failed: {fallback_error}")
        
        # Send alert
        alert_body = f"""
Contexte: {context}
Erreur: {type(error).__name__}
Message: {str(error)[:200]}

Mode Circuit: {self.circuit_breaker.mode.value}
Nombre d'erreurs: {self.circuit_breaker.failure_count}
        """
        
        if critical:
            self.circuit_breaker.mode = SafeMode.ALERT
            self.send_alert_email(
                f"ERREUR CRITIQUE - {context}",
                alert_body,
                error_details=error_record
            )
        else:
            self.send_alert_email(
                f"Erreur détectée - {context}",
                alert_body,
                error_details=error_record
            )
        
        # Return based on circuit state
        if self.circuit_breaker.is_available():
            logger.warning(f"[CONTINUE] Continuing despite error (circuit still available)")
            return True, None
        else:
            logger.critical(f"[PAUSE] Bot paused - Circuit breaker tripped. Recovery in {self.circuit_breaker.timeout_seconds}s")
            return False, None
    
    def safe_execute(
        self,
        func: Callable,
        func_args: tuple = (),
        func_kwargs: dict = None,
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
        """Clear error history"""
        self.error_history = []
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
