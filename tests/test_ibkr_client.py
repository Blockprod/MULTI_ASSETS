"""tests/test_ibkr_client.py — Tests unitaires pour IBKRForexClient.

Ces tests fonctionnent sans IB Gateway : IBKRForexClient._ib est mocké.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── Path setup (indépendant du cwd) ──────────────────────────────────────────
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR  = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_SRC_DIR   = os.path.join(_ROOT_DIR, "code", "src")
_IBKR_DIR  = os.path.join(_SRC_DIR, "ibkr")

for _p in (_SRC_DIR, _IBKR_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Placeholders Binance AVANT tout import (ibkr_config n'en dépend pas, mais
# ibkr_client importe schedule qui pourrait transitoirement passer par bot_config)
os.environ.setdefault("BINANCE_API_KEY",    "TEST_PLACEHOLDER")
os.environ.setdefault("BINANCE_SECRET_KEY", "TEST_PLACEHOLDER")

# Env IBKR minimaux pour IBKRConfig
os.environ.setdefault("IBKR_ACCOUNT", "DU_TEST")
os.environ.setdefault("IBKR_SECRET",  "TEST_SECRET_KEY_NOT_REAL_32BYTES!")


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def ibkr_cfg():
    from ibkr_config import IBKRConfig
    return IBKRConfig.from_env()


@pytest.fixture()
def mock_ib():
    """Retourne un MagicMock représentant ib_insync.IB."""
    ib = MagicMock()
    ib.isConnected.return_value = True
    ib.reqAccountSummary.return_value = [
        MagicMock(tag="NetLiquidation", value="10000.00")
    ]
    return ib


@pytest.fixture()
def client(ibkr_cfg, mock_ib):
    """IBKRForexClient avec ib_insync.IB mocké — pas de connexion réelle."""
    import ibkr_client as _ibkr_module
    # Si IB n'est pas encore disponible (ib_insync non installé), on l'injecte
    if getattr(_ibkr_module, "IB", None) is None:
        _ibkr_module.IB = MagicMock(return_value=mock_ib)
        _ibkr_module._IB_AVAILABLE = True
    with patch("ibkr_client.IB", return_value=mock_ib):
        from ibkr_client import IBKRForexClient
        c = IBKRForexClient(
            ibkr_cfg.host, ibkr_cfg.port, ibkr_cfg.client_id, ibkr_cfg.account
        )
        c._ib = mock_ib
        c._connected = True
        return c


# ─── Tests ibkr_config ────────────────────────────────────────────────────────

class TestIBKRConfig:
    def test_from_env_defaults(self, ibkr_cfg):
        assert ibkr_cfg.host == "127.0.0.1"
        assert ibkr_cfg.port == 4002
        assert ibkr_cfg.client_id == 4
        assert ibkr_cfg.paper_mode is True
        assert ibkr_cfg.initial_capital == 10_000.0

    def test_repr_masks_secrets(self, ibkr_cfg):
        r = repr(ibkr_cfg)
        # Le secret ne doit pas apparaître en clair
        assert ibkr_cfg.ibkr_secret not in r
        # Le compte doit être partiellement masqué (DU***ST au lieu de DU_TEST)
        assert ibkr_cfg.account not in r

    def test_missing_secret_raises(self):
        """Vérification que IBKR_SECRET manquant → EnvironmentError."""
        # IBKRConfig.from_env() doit lever EnvironmentError si IBKR_SECRET absent
        old_secret  = os.environ.pop("IBKR_SECRET", None)
        old_account = os.environ.pop("IBKR_ACCOUNT", None)
        try:
            from ibkr_config import IBKRConfig
            import importlib, ibkr_config
            # Force reload pour recharger avec les nouvelles env vars
            mod = importlib.reload(ibkr_config)
            with pytest.raises(EnvironmentError):
                mod.IBKRConfig.from_env()
        finally:
            if old_secret:
                os.environ["IBKR_SECRET"] = old_secret
            if old_account:
                os.environ["IBKR_ACCOUNT"] = old_account
            # Recharger le module avec les vars remises
            import importlib, ibkr_config
            importlib.reload(ibkr_config)


# ─── Tests IBKRForexClient ────────────────────────────────────────────────────

class TestIBKRForexClient:
    def test_is_connected(self, client):
        assert client._connected is True

    def test_get_account_balance(self, client, mock_ib):
        # accountSummary retourne une liste de MagicMock items avec tag/value
        item = MagicMock()
        item.tag = "NetLiquidation"
        item.value = "10000.0"
        mock_ib.accountSummary.return_value = [item]
        balance = client.get_account_balance()
        assert balance == pytest.approx(10_000.0)

    def test_get_symbol_ticker_mock(self, client, mock_ib):
        # get_symbol_ticker utilise reqMktData avec bid/ask, pas reqTickers
        ticker = MagicMock()
        ticker.bid = 1.0848
        ticker.ask = 1.0852
        mock_ib.reqMktData.return_value = ticker
        result = client.get_symbol_ticker(symbol="EURUSD")
        assert "price" in result
        assert float(result["price"]) == pytest.approx(1.0850, abs=1e-4)

    def test_parse_start_date_1095days(self, client):
        """_parse_start_date est une fonction module-level dans ibkr_client."""
        import ibkr_client as _ibkr_module
        import datetime
        start = (datetime.datetime.now() - datetime.timedelta(days=1095)).strftime("%d %b %Y")
        dt = _ibkr_module._parse_start_date(start)
        # Doit retourner un datetime proche de 1095 jours en arrière
        assert isinstance(dt, datetime.datetime)
        delta = abs((datetime.datetime.now() - dt).days - 1095)
        assert delta <= 2  # tolérance ±2 jours

    def test_build_forex_contract(self, client):
        # _build_forex_contract est une fonction module-level
        import ibkr_client as _ibkr_module
        contract = _ibkr_module._build_forex_contract("EURUSD")
        assert contract.secType == "CASH"
        assert contract.symbol == "EUR"
        assert contract.currency == "USD"
        assert contract.exchange == "IDEALPRO"

    def test_build_forex_contract_eurgbp(self, client):
        import ibkr_client as _ibkr_module
        contract = _ibkr_module._build_forex_contract("EURGBP")
        assert contract.symbol == "EUR"
        assert contract.currency == "GBP"

    def test_ensure_connected_noop_when_connected(self, client):
        """ensure_connected ne lève pas d'exception si déjà connecté."""
        client.ensure_connected()  # Ne doit pas lever

    def test_get_historical_klines_returns_list(self, client, mock_ib):
        """get_historical_klines retourne une liste (peut être vide si mock incomplet)."""
        mock_bar = MagicMock()
        mock_bar.date = 1_700_000_000
        mock_bar.open = 1.08
        mock_bar.high = 1.085
        mock_bar.low  = 1.075
        mock_bar.close = 1.082
        mock_bar.volume = 100_000
        mock_ib.reqHistoricalData.return_value = [mock_bar]

        result = client.get_historical_klines("EURUSD", "1h", "1 Jan 2024")
        assert isinstance(result, list)
