"""tests/test_signal_generator.py — MA-02

Tests unitaires de signal_generator.py.
Couvre generate_buy_condition_checker et generate_sell_condition_checker
pour les 4 scénarios WF_SCENARIOS (StochRSI, StochRSI_SMA, StochRSI_ADX,
StochRSI_TRIX) ainsi que les filtres volume et MTF.

Aucun appel réseau — logique pure sur pandas.Series.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

import pandas as pd
from signal_generator import generate_buy_condition_checker, generate_sell_condition_checker


# ---------------------------------------------------------------------------
# Helpers : construction de rows de test
# ---------------------------------------------------------------------------

def _buy_row(
    ema1: float = 1.1,
    ema2: float = 1.0,
    stoch_rsi: float = 0.4,
    close: float = 100.0,
    sma_long: float = 90.0,
    adx: float = 30.0,
    trix_histo: float = 0.001,
    volume: float = 1000.0,
    vol_sma: float = 500.0,
    mtf_bullish: float = 1.0,
) -> pd.Series:
    """Crée un pd.Series représentant une bougie valide pour l'achat."""
    return pd.Series({
        'ema1': ema1,
        'ema2': ema2,
        'stoch_rsi': stoch_rsi,
        'close': close,
        'sma_long': sma_long,
        'adx': adx,
        'TRIX_HISTO': trix_histo,
        'volume': volume,
        'vol_sma': vol_sma,
        'mtf_bullish': mtf_bullish,
    })


def _sell_row(
    ema1: float = 1.0,
    ema2: float = 1.1,
    stoch_rsi: float = 0.6,
    trix_histo: float = -0.001,
) -> pd.Series:
    """Crée un pd.Series représentant une bougie valide pour la vente."""
    return pd.Series({
        'ema1': ema1,
        'ema2': ema2,
        'stoch_rsi': stoch_rsi,
        'TRIX_HISTO': trix_histo,
    })


# ---------------------------------------------------------------------------
# Tests generate_buy_condition_checker
# ---------------------------------------------------------------------------

class TestBuySignalBaseScenario:
    """Scénario de base : StochRSI seul."""

    def _checker(self):
        return generate_buy_condition_checker({'scenario': 'StochRSI'})

    def test_valid_conditions_return_true(self):
        """Toutes les conditions remplies → (True, message OK)."""
        checker = self._checker()
        ok, reason = checker(_buy_row(), 500.0)
        assert ok is True
        assert "OK" in reason

    def test_zero_usdc_balance_returns_false(self):
        """Solde USDC nul → refus immédiat."""
        checker = self._checker()
        ok, reason = checker(_buy_row(), 0.0)
        assert ok is False
        assert "Solde USDC" in reason

    def test_negative_balance_returns_false(self):
        """Solde USDC négatif → refus immédiat."""
        checker = self._checker()
        ok, reason = checker(_buy_row(), -100.0)
        assert ok is False

    def test_ema1_below_ema2_returns_false(self):
        """EMA1 <= EMA2 → signal invalide."""
        checker = self._checker()
        row = _buy_row(ema1=0.9, ema2=1.0)
        ok, reason = checker(row, 500.0)
        assert ok is False
        assert "EMA" in reason

    def test_stoch_rsi_at_max_returns_false(self):
        """StochRSI exactement au seuil haut (0.8) → refus (>= buy_max)."""
        checker = self._checker()
        row = _buy_row(stoch_rsi=0.8)
        ok, reason = checker(row, 500.0)
        assert ok is False
        assert "StochRSI" in reason

    def test_stoch_rsi_above_max_returns_false(self):
        """StochRSI > 0.8 → refus."""
        checker = self._checker()
        row = _buy_row(stoch_rsi=0.9)
        ok, reason = checker(row, 500.0)
        assert ok is False

    def test_stoch_rsi_at_min_returns_false(self):
        """StochRSI exactement au seuil bas (0.05) → refus (<= buy_min)."""
        checker = self._checker()
        row = _buy_row(stoch_rsi=0.05)
        ok, reason = checker(row, 500.0)
        assert ok is False
        assert "trop bas" in reason

    def test_stoch_rsi_below_min_returns_false(self):
        """StochRSI < 0.05 → refus."""
        checker = self._checker()
        row = _buy_row(stoch_rsi=0.01)
        ok, reason = checker(row, 500.0)
        assert ok is False

    def test_stoch_rsi_in_valid_range_returns_true(self):
        """StochRSI strictement compris entre 0.05 et 0.8 → OK."""
        checker = self._checker()
        for val in [0.1, 0.4, 0.5, 0.79]:
            ok, _ = checker(_buy_row(stoch_rsi=val), 500.0)
            assert ok is True, f"stoch_rsi={val} aurait dû être accepté"


class TestBuySignalSMAScenario:
    """Scénario StochRSI_SMA : filtre SMA200."""

    def _checker(self):
        return generate_buy_condition_checker({'scenario': 'StochRSI_SMA', 'sma_long': 200})

    def test_close_above_sma_returns_true(self):
        """Prix au-dessus de la SMA longue → signal valide."""
        checker = self._checker()
        row = _buy_row(close=110.0, sma_long=100.0)
        ok, reason = checker(row, 500.0)
        assert ok is True

    def test_close_below_sma_returns_false(self):
        """Prix en dessous de la SMA longue → refus."""
        checker = self._checker()
        row = _buy_row(close=90.0, sma_long=100.0)
        ok, reason = checker(row, 500.0)
        assert ok is False
        assert "SMA" in reason

    def test_close_equal_sma_returns_false(self):
        """Prix égal à la SMA longue → refus (<= condition)."""
        checker = self._checker()
        row = _buy_row(close=100.0, sma_long=100.0)
        ok, reason = checker(row, 500.0)
        assert ok is False


class TestBuySignalADXScenario:
    """Scénario StochRSI_ADX : filtre ADX."""

    def _checker(self):
        return generate_buy_condition_checker({'scenario': 'StochRSI_ADX'})

    def test_adx_above_threshold_returns_true(self):
        """ADX > 25.0 (défaut) → signal valide."""
        checker = self._checker()
        row = _buy_row(adx=30.0)
        ok, reason = checker(row, 500.0)
        assert ok is True

    def test_adx_at_threshold_returns_false(self):
        """ADX == 25.0 → refus (<= condition)."""
        checker = self._checker()
        row = _buy_row(adx=25.0)
        ok, reason = checker(row, 500.0)
        assert ok is False
        assert "ADX" in reason

    def test_adx_below_threshold_returns_false(self):
        """ADX < 25.0 → refus."""
        checker = self._checker()
        row = _buy_row(adx=20.0)
        ok, reason = checker(row, 500.0)
        assert ok is False


class TestBuySignalTRIXScenario:
    """Scénario StochRSI_TRIX : filtre TRIX_HISTO."""

    def _checker(self):
        return generate_buy_condition_checker({'scenario': 'StochRSI_TRIX'})

    def test_positive_trix_returns_true(self):
        """TRIX_HISTO > 0 → signal valide."""
        checker = self._checker()
        row = _buy_row(trix_histo=0.001)
        ok, reason = checker(row, 500.0)
        assert ok is True

    def test_zero_trix_returns_false(self):
        """TRIX_HISTO == 0 → refus (<= 0)."""
        checker = self._checker()
        row = _buy_row(trix_histo=0.0)
        ok, reason = checker(row, 500.0)
        assert ok is False
        assert "TRIX" in reason

    def test_negative_trix_returns_false(self):
        """TRIX_HISTO < 0 → refus."""
        checker = self._checker()
        row = _buy_row(trix_histo=-0.001)
        ok, reason = checker(row, 500.0)
        assert ok is False


class TestBuySignalMTFFilter:
    """Filtre multi-timeframe 4h (mtf_filter_enabled=True par défaut)."""

    def _checker(self):
        return generate_buy_condition_checker({'scenario': 'StochRSI'})

    def test_mtf_bullish_passes_filter(self):
        """mtf_bullish=1.0 (>= 0.5) → filtre MTF passé."""
        checker = self._checker()
        row = _buy_row(mtf_bullish=1.0)
        ok, _ = checker(row, 500.0)
        assert ok is True

    def test_mtf_bearish_blocks_signal(self):
        """mtf_bullish=0.0 (< 0.5) → filtre MTF bloque le signal."""
        checker = self._checker()
        row = _buy_row(mtf_bullish=0.0)
        ok, reason = checker(row, 500.0)
        assert ok is False
        assert "MTF" in reason

    def test_missing_mtf_column_skips_filter(self):
        """Absence de colonne mtf_bullish → filtre ignoré, signal valide."""
        checker = self._checker()
        # Construire une row sans mtf_bullish
        row = pd.Series({
            'ema1': 1.1, 'ema2': 1.0, 'stoch_rsi': 0.4,
            'close': 100.0, 'sma_long': 90.0, 'adx': 30.0,
            'TRIX_HISTO': 0.001, 'volume': 1000.0, 'vol_sma': 500.0,
        })
        ok, _ = checker(row, 500.0)
        assert ok is True


# ---------------------------------------------------------------------------
# Tests generate_sell_condition_checker
# ---------------------------------------------------------------------------

class TestSellSignalBase:
    """Tests de base de la logique de vente."""

    def _checker(self, scenario: str = 'StochRSI'):
        return generate_sell_condition_checker({'scenario': scenario})

    def test_no_coin_balance_returns_false(self):
        """Pas de coin en portefeuille → pas de vente."""
        checker = self._checker()
        ok, reason = checker(_sell_row(), 0.0, 100.0, 95.0, 2.0)
        assert ok is False
        assert reason is None

    def test_none_entry_price_returns_false(self):
        """entry_price=None → pas de signal."""
        checker = self._checker()
        ok, reason = checker(_sell_row(), 1.0, None, 95.0, 2.0)
        assert ok is False
        assert reason is None

    def test_none_atr_returns_false(self):
        """atr_value=None → pas de signal (calcul ATR impossible)."""
        checker = self._checker()
        ok, reason = checker(_sell_row(), 1.0, 100.0, 95.0, None)
        assert ok is False
        assert reason is None

    def test_stop_loss_triggered(self):
        """Prix < entry - (mult × ATR) → STOP-LOSS."""
        checker = self._checker()
        # entry=100, mult=5.5 (défaut), atr=2 → stop_loss=89
        ok, reason = checker(_sell_row(), 1.0, 100.0, 85.0, 2.0)
        assert ok is True
        assert reason == "STOP-LOSS"

    def test_price_above_stop_loss_no_auto_trigger(self):
        """Prix au-dessus du stop → pas de STOP-LOSS déclenché."""
        checker = self._checker()
        # entry=100, mult=5.5, atr=2 → stop_loss=89 ; prix=95 → pas de SL
        ok, reason = checker(_sell_row(ema1=1.1, ema2=0.9, stoch_rsi=0.2), 1.0, 100.0, 95.0, 2.0)
        # EMA condition non remplie (ema2 < ema1) → pas de SIGNAL non plus
        assert ok is False


class TestSellSignalScenarios:
    """Conditions de signal de vente selon EMA + StochRSI."""

    def _checker(self, scenario: str = 'StochRSI'):
        return generate_sell_condition_checker({'scenario': scenario})

    def test_ema_and_stoch_sell_condition_met(self):
        """EMA2 > EMA1 ET StochRSI > 0.4 → (True, 'SIGNAL')."""
        checker = self._checker()
        # entry=100, mult=5.5, atr=2 → stop_loss=89 ; prix=100 pas de SL
        row = _sell_row(ema2=1.1, ema1=1.0, stoch_rsi=0.6)
        ok, reason = checker(row, 1.0, 100.0, 100.0, 2.0)
        assert ok is True
        assert reason == "SIGNAL"

    def test_ema_condition_not_met_no_signal(self):
        """EMA2 <= EMA1 → pas de signal de vente."""
        checker = self._checker()
        row = _sell_row(ema2=0.9, ema1=1.0, stoch_rsi=0.6)
        ok, reason = checker(row, 1.0, 100.0, 100.0, 2.0)
        assert ok is False
        assert reason is None

    def test_stoch_sell_exit_not_met_no_signal(self):
        """StochRSI <= 0.4 (sell_exit) → pas de signal de vente."""
        checker = self._checker()
        row = _sell_row(ema2=1.1, ema1=1.0, stoch_rsi=0.3)
        ok, reason = checker(row, 1.0, 100.0, 100.0, 2.0)
        assert ok is False

    def test_trix_scenario_positive_histo_returns_signal(self):
        """Scénario TRIX : EMA2 > EMA1 + StochRSI > 0.4 → SIGNAL."""
        checker = self._checker('StochRSI_TRIX')
        row = _sell_row(ema2=1.1, ema1=1.0, stoch_rsi=0.6, trix_histo=0.001)
        ok, reason = checker(row, 1.0, 100.0, 100.0, 2.0)
        assert ok is True
        assert reason == "SIGNAL"

    def test_trix_scenario_negative_histo_also_returns_signal(self):
        """Scénario TRIX : TRIX_HISTO <= 0 → quand même SIGNAL (optimisation sell)."""
        checker = self._checker('StochRSI_TRIX')
        row = _sell_row(ema2=1.1, ema1=1.0, stoch_rsi=0.6, trix_histo=-0.001)
        ok, reason = checker(row, 1.0, 100.0, 100.0, 2.0)
        assert ok is True
        assert reason == "SIGNAL"

    def test_custom_atr_multiplier_via_config(self):
        """Le multiplicateur ATR est configurable via le paramètre config."""
        from bot_config import Config
        cfg = Config.__new__(Config)
        cfg.atr_stop_multiplier = 2.0  # Multiplicateur faible = stop proche

        checker = generate_sell_condition_checker({'scenario': 'StochRSI'}, config=cfg)
        # entry=100, mult=2.0, atr=2 → stop_loss=96 ; prix=95 < 96 → STOP-LOSS
        row = _sell_row(ema2=1.1, ema1=1.0, stoch_rsi=0.6)
        ok, reason = checker(row, 1.0, 100.0, 95.0, 2.0)
        assert ok is True
        assert reason == "STOP-LOSS"
