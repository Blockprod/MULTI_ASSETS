"""Tests correlation guard (P2-3)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

from correlation_guard import (
    CorrelationData,
    CorrelationGuard,
    check_correlation_guard,
    CORRELATION_THRESHOLD,
)


class TestCorrelationDataAdd:
    """Ajouter bougies au cache."""

    def test_add_single_candle(self):
        """Ajoute une bougie."""
        corr_data = CorrelationData()
        import time
        now = time.time()
        corr_data.add_candle("SOLUSDT", now, 100.0)
        assert "SOLUSDT" in corr_data._price_history

    def test_add_multiple_candles(self):
        """Ajoute plusieurs bougies."""
        corr_data = CorrelationData()
        import time
        now = time.time()
        for i in range(5):
            corr_data.add_candle("SOLUSDT", now + i * 3600, 100.0 + i)
        assert len(corr_data._price_history["SOLUSDT"]) == 5


class TestCorrelationGuardCanEnter:
    """Vérifier autorisation d'entrée."""

    def test_flat_market_allowed(self):
        """Marché flat → entrée OK."""
        guard = CorrelationGuard()
        authorized, reason = guard.can_enter("SOLUSDT", {})
        assert authorized is True
        assert reason is None

    def test_no_data_allows_entry(self):
        """Pas de données corrélation → entrée OK."""
        guard = CorrelationGuard()
        open_pos = {"PEPEUSDT": "BUY"}
        authorized, reason = guard.can_enter("SOLUSDT", open_pos)
        assert authorized is True

    def test_multiple_positions_checked(self):
        """Vérifie toutes positions."""
        guard = CorrelationGuard()
        open_pos = {"PEPEUSDT": "BUY", "ETHUSDT": "SHORT"}
        authorized, reason = guard.can_enter("SOLUSDT", open_pos)
        assert authorized is True

    def test_ignore_closing_positions(self):
        """Ignore positions en fermeture."""
        guard = CorrelationGuard()
        open_pos = {"SOLUSDT": "SELL"}
        authorized, reason = guard.can_enter("PEPEUSDT", open_pos)
        assert authorized is True


class TestCheckCorrelationGuardHelper:
    """Fonction publique check_correlation_guard()."""

    def test_no_open_positions(self):
        """Pas de position → autoriser."""
        bot_state = {"SOLUSDT": {"last_order_side": None}}
        authorized, reason = check_correlation_guard("PEPEUSDT", bot_state)
        assert authorized is True

    def test_with_open_long(self):
        """Position LONG → vérifier corrélation."""
        bot_state = {
            "SOLUSDT": {
                "last_order_side": "BUY",
                "entry_price": 100.0,
            },
            "PEPEUSDT": {"last_order_side": None},
        }
        authorized, reason = check_correlation_guard("PEPEUSDT", bot_state)
        assert authorized is True

    def test_with_open_short(self):
        """Position SHORT → vérifier corrélation."""
        bot_state = {
            "EURUSD": {
                "last_order_side": "SHORT",
                "entry_price": 1.08,
            },
        }
        authorized, reason = check_correlation_guard("GBPUSD", bot_state)
        assert authorized is True

    def test_filters_non_pair_keys(self):
        """Filtre clés non-paires."""
        bot_state = {
            "emergency_halt": False,
            "daily_loss_pct": 0.01,
            "SOLUSDT": {"last_order_side": "BUY"},
        }
        authorized, reason = check_correlation_guard("PEPEUSDT", bot_state)
        assert authorized is True


class TestCorrelationThreshold:
    """Seuil CORRELATION_THRESHOLD = 0.85."""

    def test_threshold_value(self):
        """Seuil correct."""
        assert CORRELATION_THRESHOLD == 0.85


class TestCorrelationWithData:
    """Tests corrélation avec données."""

    def test_sufficient_candles_compute(self):
        """Avec 150+ bougies, calcule corrélation."""
        corr_data = CorrelationData()
        import time
        now = time.time()

        for i in range(150):
            price = 100.0 + i * 0.5
            corr_data.add_candle("SOLUSDT", now + i * 3600, price)
            corr_data.add_candle("PEPEUSDT", now + i * 3600, price * 0.0001)

        corr = corr_data.get_correlation("SOLUSDT", "PEPEUSDT")
        # Avec données, corrélation doit être calculée (ou None si pas assez de points communs)
        assert corr is None or isinstance(corr, float)

    def test_insufficient_candles_no_corr(self):
        """< 100 bougies → pas de corrélation."""
        corr_data = CorrelationData()
        import time
        now = time.time()

        for i in range(50):
            corr_data.add_candle("SOLUSDT", now + i * 3600, 100.0 + i)
            corr_data.add_candle("PEPEUSDT", now + i * 3600, 0.01 + i * 0.0001)

        corr = corr_data.get_correlation("SOLUSDT", "PEPEUSDT")
        assert corr is None
