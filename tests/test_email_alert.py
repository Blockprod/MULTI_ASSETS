import os
import pytest
from email_utils import send_email_alert

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
    # Mock config credentials
    monkeypatch.setattr("email_utils.config.sender_email", "test@test.com")
    monkeypatch.setattr("email_utils.config.smtp_password", "testpass")
    monkeypatch.setattr("email_utils.config.receiver_email", "recv@test.com")
    monkeypatch.setattr("email_utils.config.smtp_server", "smtp.gmail.com")
    monkeypatch.setattr("email_utils.config.smtp_port", 587)
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
    monkeypatch.setattr("email_utils.config.sender_email", "")
    monkeypatch.setattr("email_utils.config.smtp_password", "")
    monkeypatch.setattr("email_utils.config.receiver_email", "")
    monkeypatch.setattr("email_utils.config.smtp_server", "smtp.gmail.com")
    monkeypatch.setattr("email_utils.config.smtp_port", 587)
    # L'envoi doit lever une exception ou retourner False
    with pytest.raises(Exception):
        send_email_alert("Test", "Body")
