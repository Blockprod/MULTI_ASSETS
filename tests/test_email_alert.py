import pytest
import email_utils
from email_utils import send_email_alert
from bot_config import Config


def _make_test_config(**overrides):
    """Config minimale non-gelée pour les tests (via __new__)."""
    cfg = Config.__new__(Config)
    defaults = dict(
        sender_email="test@test.com",
        smtp_password="testpass",
        receiver_email="recv@test.com",
        smtp_server="smtp.gmail.com",
        smtp_port=587,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


def test_send_email_success(monkeypatch):
    class DummySMTP:
        def __init__(self, *args, **kwargs): pass
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, user, pwd): pass
        def sendmail(self, from_addr, to_addr, msg): self.sent = True
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr("smtplib.SMTP", lambda *a, **kw: DummySMTP())
    # P0-01: remplacer le singleton gelé par une config de test non-gelée
    monkeypatch.setattr(email_utils, 'config', _make_test_config())
    assert send_email_alert("Test", "Body") is True

def test_send_email_missing_credentials(monkeypatch):
    """Test que l'envoi échoue proprement quand les credentials sont vides."""
    class FailingSMTP:
        def __init__(self, *args, **kwargs): pass
        def ehlo(self): pass
        def starttls(self): pass
        def login(self, user, pwd): raise Exception("Auth failed")
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr("smtplib.SMTP", lambda *a, **kw: FailingSMTP())
    # P0-01: remplacer le singleton gelé par une config de test non-gelée
    monkeypatch.setattr(email_utils, 'config', _make_test_config(
        sender_email="", smtp_password="", receiver_email=""
    ))
    # L'envoi doit lever une exception ou retourner False
    with pytest.raises(Exception):
        send_email_alert("Test", "Body")


# ─── P1-05: Fallback alerts_unsent.jsonl ─────────────────────────────────────

class TestEmailFallback:
    """Tests pour send_email_alert_with_fallback et _write_alert_unsent_fallback (P1-05)."""

    def test_fallback_writes_jsonl_on_smtp_failure(self, tmp_path, monkeypatch):
        """Si SMTP échoue, l'alerte est écrite dans alerts_unsent.jsonl."""
        import json
        import email_utils

        fallback_file = tmp_path / "alerts_unsent.jsonl"
        monkeypatch.setattr(email_utils, '_ALERTS_UNSENT_FILE', str(fallback_file))
        monkeypatch.setattr(email_utils, 'send_email_alert', lambda s, b: (_ for _ in ()).throw(OSError("SMTP down")))

        result = email_utils.send_email_alert_with_fallback("Test subject", "Test body")

        assert result is False
        assert fallback_file.exists()
        record = json.loads(fallback_file.read_text(encoding='utf-8').strip())
        assert record["subject"] == "Test subject"
        assert record["body"] == "Test body"
        assert "timestamp" in record

    def test_fallback_returns_true_on_success(self, monkeypatch):
        """Si SMTP réussit, send_email_alert_with_fallback retourne True, pas de fichier."""
        import email_utils
        monkeypatch.setattr(email_utils, 'send_email_alert', lambda s, b: True)
        result = email_utils.send_email_alert_with_fallback("Subject", "Body")
        assert result is True

    def test_fallback_does_not_crash_if_dir_unwritable(self, monkeypatch):
        """Si le répertoire logs/ est inaccessible, pas de crash (log seul)."""
        import email_utils
        monkeypatch.setattr(email_utils, 'send_email_alert', lambda s, b: (_ for _ in ()).throw(OSError("SMTP down")))
        monkeypatch.setattr(email_utils, '_ALERTS_UNSENT_FILE', '/nonexistent_dir/alerts.jsonl')
        # Ne doit pas lever
        result = email_utils.send_email_alert_with_fallback("Subject", "Body")
        assert result is False
