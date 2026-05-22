"""tests/test_ibkr_data_fetcher.py — Tests unitaires pour ibkr_data_fetcher.

Teste la conversion bars → DataFrame et la logique de cache
sans connexion IB Gateway.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

# ─── Path setup ───────────────────────────────────────────────────────────────
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR  = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_SRC_DIR   = os.path.join(_ROOT_DIR, "code", "src")
_IBKR_DIR  = os.path.join(_SRC_DIR, "ibkr")

for _p in (_SRC_DIR, _IBKR_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("BINANCE_API_KEY",    "TEST_PLACEHOLDER")
os.environ.setdefault("BINANCE_SECRET_KEY", "TEST_PLACEHOLDER")
os.environ.setdefault("IBKR_ACCOUNT", "DU_TEST")
os.environ.setdefault("IBKR_SECRET",  "TEST_SECRET_KEY_NOT_REAL_32BYTES!")

from ibkr_data_fetcher import (  # noqa: E402
    _bars_to_dataframe, fetch_forex_data, invalidate_cache,
)


# ─── Données de test ──────────────────────────────────────────────────────────

def _make_raw_bars(n: int = 10) -> list:
    """Génère n barres au format [ts_ms, open, high, low, close, volume]."""
    import time as _time
    base_ts = int(_time.time() * 1000) - n * 3_600_000
    bars = []
    for i in range(n):
        ts = base_ts + i * 3_600_000
        bars.append([ts, 1.08 + i * 0.001, 1.085 + i * 0.001,
                     1.075 + i * 0.001, 1.082 + i * 0.001, 100_000 + i * 1000])
    return bars


# ─── Tests _bars_to_dataframe ────────────────────────────────────────────────

class TestBarsToDataframe:
    def test_empty_input_returns_empty_df(self):
        df = _bars_to_dataframe([])
        assert df.empty

    def test_columns_present(self):
        bars = _make_raw_bars(5)
        df = _bars_to_dataframe(bars)
        for col in ("open", "high", "low", "close", "volume"):
            assert col in df.columns

    def test_index_is_datetimeindex(self):
        bars = _make_raw_bars(5)
        df = _bars_to_dataframe(bars)
        assert isinstance(df.index, pd.DatetimeIndex)

    def test_index_is_utc(self):
        bars = _make_raw_bars(5)
        df = _bars_to_dataframe(bars)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert str(df.index.tz) == "UTC"

    def test_row_count_matches(self):
        bars = _make_raw_bars(8)
        df = _bars_to_dataframe(bars)
        assert len(df) == 8

    def test_sorted_by_timestamp(self):
        bars = _make_raw_bars(5)
        # Mélanger
        import random
        random.shuffle(bars)
        df = _bars_to_dataframe(bars)
        assert df.index.is_monotonic_increasing

    def test_dtypes_are_float64(self):
        bars = _make_raw_bars(3)
        df = _bars_to_dataframe(bars)
        for col in ("open", "high", "low", "close", "volume"):
            assert df[col].dtype == "float64", f"{col} n'est pas float64"

    def test_invalid_bar_skipped(self):
        bars = _make_raw_bars(3)
        bars.append(["not_a_ts", "x", "y", "z", "w", "0"])
        df = _bars_to_dataframe(bars)
        # Au moins les 3 valides doivent passer
        assert len(df) == 3

    def test_no_duplicate_index(self):
        bars = _make_raw_bars(5)
        # Dupliquer le dernier
        bars.append(bars[-1])
        df = _bars_to_dataframe(bars)
        assert not df.index.duplicated().any()


# ─── Tests fetch_forex_data avec client mocké ────────────────────────────────

class TestFetchForexData:
    def _mock_client(self, bars):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.get_historical_klines.return_value = bars
        return client

    def test_returns_dataframe(self, tmp_path):
        bars = _make_raw_bars(10)
        client = self._mock_client(bars)
        df = fetch_forex_data(
            "EURUSD", "1h", "1 Jan 2024",
            client, cache_dir=str(tmp_path),
        )
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 10

    def test_cache_hit_skips_client(self, tmp_path):
        bars = _make_raw_bars(5)
        client = self._mock_client(bars)

        # Premier appel → écrit cache
        fetch_forex_data("EURUSD", "1h", "1 Jan 2024", client,
                         cache_dir=str(tmp_path))
        assert client.get_historical_klines.call_count == 1

        # Deuxième appel → doit lire depuis le cache
        fetch_forex_data("EURUSD", "1h", "1 Jan 2024", client,
                         cache_dir=str(tmp_path))
        assert client.get_historical_klines.call_count == 1  # pas d'appel supplémentaire

    def test_force_refresh_bypasses_cache(self, tmp_path):
        bars = _make_raw_bars(5)
        client = self._mock_client(bars)

        fetch_forex_data("EURUSD", "1h", "1 Jan 2024", client,
                         cache_dir=str(tmp_path))
        fetch_forex_data("EURUSD", "1h", "1 Jan 2024", client,
                         cache_dir=str(tmp_path), force_refresh=True)
        assert client.get_historical_klines.call_count == 2

    def test_empty_response_returns_empty_df(self, tmp_path):
        client = self._mock_client([])
        df = fetch_forex_data("EURUSD", "1h", "1 Jan 2024", client,
                               cache_dir=str(tmp_path))
        assert df.empty

    def test_invalidate_cache(self, tmp_path):
        bars = _make_raw_bars(5)
        client = self._mock_client(bars)

        # Remplir le cache
        fetch_forex_data("EURUSD", "1h", "1 Jan 2024", client,
                         cache_dir=str(tmp_path))
        assert client.get_historical_klines.call_count == 1

        # Invalider
        invalidate_cache("EURUSD", "1h", "1 Jan 2024", str(tmp_path))

        # Prochain appel recharge depuis client
        fetch_forex_data("EURUSD", "1h", "1 Jan 2024", client,
                         cache_dir=str(tmp_path))
        assert client.get_historical_klines.call_count == 2
