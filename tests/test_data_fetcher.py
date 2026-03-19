"""tests/test_data_fetcher.py — MA-03

Tests unitaires pour data_fetcher.py.
Couvre validate_data_integrity, get_cached_exchange_info
et get_binance_trading_fees sans connexion réseau.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

import pandas as pd
import pytest
from unittest.mock import MagicMock

import data_fetcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 5) -> pd.DataFrame:
    """DataFrame OHLCV valide avec DatetimeIndex horaire."""
    timestamps = pd.date_range('2024-01-01', periods=n, freq='1h')
    return pd.DataFrame(
        {
            'open':   [100.0] * n,
            'high':   [105.0] * n,  # high >= max(open, close)
            'low':    [95.0] * n,   # low  <= min(open, close)
            'close':  [102.0] * n,
            'volume': [1000.0] * n,
        },
        index=timestamps,
    )


@pytest.fixture(autouse=True)
def reset_exchange_info_cache():
    """Remet le cache exchange_info à zéro avant chaque test."""
    data_fetcher._exchange_info_cache['data'] = None
    data_fetcher._exchange_info_cache['ts'] = 0.0
    yield
    data_fetcher._exchange_info_cache['data'] = None
    data_fetcher._exchange_info_cache['ts'] = 0.0


# ---------------------------------------------------------------------------
# Tests validate_data_integrity
# ---------------------------------------------------------------------------

class TestValidateDataIntegrity:
    """Tests de validate_data_integrity (fonction pure sur DataFrame)."""

    def test_empty_dataframe_returns_false(self):
        """Un DataFrame vide doit être rejeté."""
        assert data_fetcher.validate_data_integrity(pd.DataFrame()) is False

    def test_valid_ohlcv_returns_true(self):
        """Données OHLCV cohérentes et positives → True."""
        assert data_fetcher.validate_data_integrity(_make_ohlcv()) is True

    def test_negative_close_returns_false(self):
        """Un close négatif invalide les données."""
        df = _make_ohlcv()
        df.loc[df.index[0], 'close'] = -1.0
        assert data_fetcher.validate_data_integrity(df) is False

    def test_negative_volume_returns_false(self):
        """Un volume négatif invalide les données."""
        df = _make_ohlcv()
        df.loc[df.index[0], 'volume'] = -0.5
        assert data_fetcher.validate_data_integrity(df) is False

    def test_high_below_open_returns_false(self):
        """high < open → incohérence OHLC → False."""
        df = _make_ohlcv()
        # high=80 < open=100
        df.loc[df.index[0], 'high'] = 80.0
        assert data_fetcher.validate_data_integrity(df) is False

    def test_high_below_close_returns_false(self):
        """high < close → incohérence OHLC → False."""
        df = _make_ohlcv()
        # close=102 ; forcer high=101 < 102
        df.loc[df.index[0], 'high'] = 101.0
        df.loc[df.index[0], 'close'] = 103.0
        assert data_fetcher.validate_data_integrity(df) is False

    def test_low_above_close_returns_false(self):
        """low > close → incohérence OHLC → False."""
        df = _make_ohlcv()
        # close=102 ; forcer low=110 > 102
        df.loc[df.index[0], 'low'] = 110.0
        assert data_fetcher.validate_data_integrity(df) is False

    def test_single_row_valid_returns_true(self):
        """Un seul enregistrement valide passe la validation."""
        assert data_fetcher.validate_data_integrity(_make_ohlcv(n=1)) is True


# ---------------------------------------------------------------------------
# Tests get_cached_exchange_info
# ---------------------------------------------------------------------------

class TestGetCachedExchangeInfo:
    """Tests du cache exchange_info (TTL 24 h)."""

    def test_first_call_invokes_client(self):
        """Premier appel → client.get_exchange_info() appelé."""
        mock_client = MagicMock()
        mock_client.get_exchange_info.return_value = {'symbols': []}
        result = data_fetcher.get_cached_exchange_info(mock_client)
        mock_client.get_exchange_info.assert_called_once()
        assert result == {'symbols': []}

    def test_second_call_within_ttl_uses_cache(self):
        """Second appel dans le TTL → pas d'appel API supplémentaire."""
        mock_client = MagicMock()
        mock_client.get_exchange_info.return_value = {'symbols': ['BTCUSDC']}
        # Premier appel
        data_fetcher.get_cached_exchange_info(mock_client)
        # Deuxième appel immédiat (bien dans le TTL)
        result = data_fetcher.get_cached_exchange_info(mock_client)
        assert mock_client.get_exchange_info.call_count == 1
        assert result == {'symbols': ['BTCUSDC']}

    def test_expired_cache_re_fetches(self):
        """Cache avec ts expiré (> 24 h) → nouvel appel API."""
        mock_client = MagicMock()
        mock_client.get_exchange_info.return_value = {'symbols': []}
        # Pré-remplir le cache avec un ts expiré
        data_fetcher._exchange_info_cache['data'] = {'symbols': ['old']}
        data_fetcher._exchange_info_cache['ts'] = time.time() - (25 * 3600)  # 25 h ago
        result = data_fetcher.get_cached_exchange_info(mock_client)
        mock_client.get_exchange_info.assert_called_once()
        assert result == {'symbols': []}


# ---------------------------------------------------------------------------
# Tests get_binance_trading_fees
# ---------------------------------------------------------------------------

class TestGetBinanceTradingFees:
    """Tests de get_binance_trading_fees."""

    def test_returns_correct_fees_from_account_info(self):
        """Frais calculés correctement depuis takerCommission / makerCommission."""
        mock_client = MagicMock()
        # Binance retourne des frais en points de base * 100 : ex. 10 = 0.001 = 0.1 %
        mock_client.get_account.return_value = {
            'takerCommission': 7,    # 7 / 10000 = 0.0007
            'makerCommission': 2,    # 2 / 10000 = 0.0002
        }
        taker, maker = data_fetcher.get_binance_trading_fees(mock_client)
        assert abs(taker - 0.0007) < 1e-9
        assert abs(maker - 0.0002) < 1e-9

    def test_returns_defaults_on_api_exception(self):
        """En cas d'échec API → valeurs par défaut retournées, sans exception."""
        mock_client = MagicMock()
        mock_client.get_account.side_effect = RuntimeError("API error")
        taker, maker = data_fetcher.get_binance_trading_fees(
            mock_client,
            default_taker=0.001,
            default_maker=0.001,
        )
        assert taker == 0.001
        assert maker == 0.001
