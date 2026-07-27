from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KRAKEN_BOT = ROOT / "code" / "src" / "kraken_bot"
TEMPLATES_PATH = KRAKEN_BOT / "email_templates.py"


def _load_templates():
    spec = importlib.util.spec_from_file_location("kraken_email_templates_test_module", TEMPLATES_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("kraken_email_templates_test_module", module)
    spec.loader.exec_module(module)
    return module


def test_kraken_api_connection_email_is_broker_branded(monkeypatch) -> None:
    monkeypatch.setenv("BROKER", "KRAKEN")
    templates = _load_templates()

    subject, body = templates.api_connection_failure_email("boom")

    assert "Kraken" in subject
    assert "API KRAKEN" in body
    assert "cles API Kraken" in body
    assert "BINANCE" not in body
    assert "Binance" not in body


def test_kraken_trade_emails_keep_binance_shape_with_kraken_broker(monkeypatch) -> None:
    monkeypatch.setenv("BROKER", "KRAKEN")
    templates = _load_templates()

    buy_subject, buy_body = templates.buy_executed_email("XRPUSDC", 10, 1.5, 15, 100)
    sell_subject, sell_body = templates.sell_executed_email("XRPUSDC", 10, 1.6, 16, "TRAILING-STOP")

    assert buy_subject.startswith("[INFO] Achat execute")
    assert sell_subject.startswith("[INFO] Vente executee")
    assert "Broker              : Kraken" in buy_body
    assert "Broker              : Kraken" in sell_body
    assert "BINANCE" not in buy_body + sell_body
    assert "Binance" not in buy_body + sell_body


def test_kraken_buy_email_uses_pair_quote_currency(monkeypatch) -> None:
    monkeypatch.setenv("BROKER", "KRAKEN")
    templates = _load_templates()

    _subject, body = templates.buy_executed_email(
        "ONDOUSD",
        10,
        1.5,
        15,
        27,
    )

    assert "Prix d'entree       : 1.5000 USD" in body
    assert "Montant investi     : 15.00 USD" in body
    assert "Solde USD apres" in body
    assert "USDC apres" not in body


def test_kraken_email_utils_adds_kraken_spot_balance_label() -> None:
    source = (KRAKEN_BOT / "email_utils.py").read_text(encoding="utf-8")

    assert "Solde SPOT {_exchange_label(client)} global" in source
    assert "Solde SPOT global" not in source
