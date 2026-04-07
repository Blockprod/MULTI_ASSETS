"""
email_utils.py — Utilitaires d'envoi d'emails pour le bot de trading.

Contient:
- send_email_alert: envoi SMTP avec retry
- send_trading_alert_email: wrapper avec ajout du solde SPOT
- send_email_alert_with_fallback: wrapper avec fallback alerts_unsent.jsonl (P1-05)
"""
import json
import logging
import os
import smtplib
import time as _time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from bot_config import config, log_exceptions, retry_with_backoff

logger = logging.getLogger('trading_bot')


@retry_with_backoff(max_retries=3, base_delay=2.0)
def send_email_alert(subject: str, body: str) -> bool:
    """Envoie une alerte par email avec retry automatique.

    Le sujet est automatiquement préfixé par ``[<project_name>]`` issu de
    ``config.project_name`` (env var ``BOT_PROJECT_NAME``, défaut
    ``MULTI_ASSETS``) pour distinguer les alertes entre projets.
    """
    try:
        project = getattr(config, 'project_name', 'MULTI_ASSETS')
        prefixed_subject = f"[{project}] {subject}"
        msg = MIMEMultipart()
        msg['From'] = config.sender_email
        msg['To'] = config.receiver_email
        msg['Subject'] = prefixed_subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP(config.smtp_server, config.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.sender_email, config.smtp_password)
            server.sendmail(config.sender_email, config.receiver_email, msg.as_string())
        logger.info("Email d'alerte envoyé avec succès")
        return True
    except Exception as e:
        logger.error(f"Erreur envoi email: {e}")
        raise


@log_exceptions(default_return=False)
def send_trading_alert_email(
    subject: str, body_main: str, client, add_spot_balance: bool = True
) -> bool:
    """Envoie un email d'alerte de trading avec possibilité d'injecter le solde SPOT."""
    body = body_main
    if add_spot_balance:
        try:
            from exchange_client import get_spot_balance_usdc
            spot_balance_usdc = get_spot_balance_usdc(client)
            body += f"\n\nSolde SPOT global : {spot_balance_usdc:.2f} USDC"
        except Exception as _e:
            logger.warning("[EMAIL] Solde SPOT USDC indisponible: %s", _e)
    return send_email_alert(subject, body)


# ── P1-05: Fallback alerts_unsent.jsonl ───────────────────────────────────────

_ALERTS_UNSENT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'logs', 'alerts_unsent.jsonl'
)


def _write_alert_unsent_fallback(subject: str, body: str) -> None:
    """Écrit une alerte non envoyée dans alerts_unsent.jsonl si SMTP est indisponible."""
    try:
        os.makedirs(os.path.dirname(_ALERTS_UNSENT_FILE), exist_ok=True)
        record = {
            "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            "subject": subject,
            "body": body,
        }
        with open(_ALERTS_UNSENT_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        logger.warning(
            "[EMAIL-FALLBACK] Alerte non envoyée écrite dans alerts_unsent.jsonl : %s",
            subject,
        )
    except Exception as e:
        logger.error("[EMAIL-FALLBACK] Impossible d'écrire dans alerts_unsent.jsonl: %s", e)


def send_email_alert_with_fallback(subject: str, body: str) -> bool:
    """Envoie un email d'alerte. Si SMTP échoue, écrit dans alerts_unsent.jsonl.

    Utiliser quand l'alerte ne doit pas être silencieusement perdue.
    """
    try:
        return send_email_alert(subject, body)
    except Exception:
        _write_alert_unsent_fallback(subject, body)
        return False
