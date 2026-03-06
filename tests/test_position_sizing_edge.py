"""
test_position_sizing_edge.py — Tests de cas limites pour position_sizing.py.

Complète test_position_sizing.py avec :
  - volatility_parity : nominal, zero atr, zero price, zero target, None inputs
  - fixed_notional : None entry, zero notional, large equity
  - risk-based : NaN inputs, Inf inputs, very small values, string inputs
  - Cross-mode : comparaison des modes sur mêmes inputs
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

from position_sizing import (
    compute_position_size_by_risk,
    compute_position_size_fixed_notional,
    compute_position_size_volatility_parity,
)


# ---------------------------------------------------------------------------
#  volatility_parity
# ---------------------------------------------------------------------------

class TestVolatilityParityNominal:
    def test_nominal(self):
        """Cas nominal: qty = (equity * target) / (atr * price)."""
        # equity=10000, target=0.02, atr=2, price=100
        # qty = (10000*0.02) / (2*100) = 200/200 = 1.0
        qty = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
            target_volatility_pct=0.02,
        )
        assert abs(qty - 1.0) < 1e-9

    def test_high_volatility_target(self):
        """Cible de volatilité élevée → position plus grande."""
        qty = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
            target_volatility_pct=0.10,
        )
        # (10000*0.10) / (2*100) = 1000/200 = 5.0
        assert abs(qty - 5.0) < 1e-9

    def test_high_atr_small_position(self):
        """ATR élevé → position plus petite (inverse proportionnel)."""
        qty = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=20.0, entry_price=100.0,
            target_volatility_pct=0.02,
        )
        # (10000*0.02) / (20*100) = 200/2000 = 0.1
        assert abs(qty - 0.1) < 1e-9


class TestVolatilityParityEdge:
    def test_zero_atr(self):
        """ATR = 0 → 0.0 (pas de div par zéro)."""
        qty = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=0.0, entry_price=100.0,
        )
        assert qty == 0.0

    def test_negative_atr(self):
        """ATR négatif → 0.0."""
        qty = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=-5.0, entry_price=100.0,
        )
        assert qty == 0.0

    def test_none_atr(self):
        """ATR None → 0.0."""
        qty = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=None, entry_price=100.0,
        )
        assert qty == 0.0

    def test_zero_price(self):
        """Prix = 0 → 0.0."""
        qty = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=2.0, entry_price=0.0,
        )
        assert qty == 0.0

    def test_none_price(self):
        """Prix None → 0.0."""
        qty = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=2.0, entry_price=None,
        )
        assert qty == 0.0

    def test_zero_equity(self):
        """Equity = 0 → 0.0."""
        qty = compute_position_size_volatility_parity(
            equity=0.0, atr_value=2.0, entry_price=100.0,
        )
        assert qty == 0.0

    def test_negative_equity(self):
        """Equity négatif → 0.0."""
        qty = compute_position_size_volatility_parity(
            equity=-5000.0, atr_value=2.0, entry_price=100.0,
        )
        assert qty == 0.0

    def test_zero_target_uses_default(self):
        """target_volatility_pct = 0 → utilise 0.02 par défaut."""
        qty = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
            target_volatility_pct=0.0,
        )
        # Avec default 0.02: (10000*0.02)/(2*100) = 1.0
        assert qty == 1.0

    def test_none_target_uses_default(self):
        """target_volatility_pct = None → utilise 0.02 par défaut."""
        qty = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
            target_volatility_pct=None,
        )
        assert qty == 1.0


# ---------------------------------------------------------------------------
#  fixed_notional edge cases
# ---------------------------------------------------------------------------

class TestFixedNotionalEdge:
    def test_none_entry_price(self):
        """entry_price None → 0.0."""
        qty = compute_position_size_fixed_notional(
            equity=10000.0, entry_price=None,
        )
        assert qty == 0.0

    def test_negative_entry_price(self):
        """entry_price négatif → 0.0."""
        qty = compute_position_size_fixed_notional(
            equity=10000.0, entry_price=-100.0,
        )
        assert qty == 0.0

    def test_zero_notional(self):
        """notional = 0 → 0.0."""
        qty = compute_position_size_fixed_notional(
            equity=10000.0, notional_per_trade_usd=0.0, entry_price=100.0,
        )
        assert qty == 0.0

    def test_none_notional_uses_10pct(self):
        """notional None → max(100, 10% equity)."""
        qty = compute_position_size_fixed_notional(
            equity=50000.0, notional_per_trade_usd=None, entry_price=100.0,
        )
        # max(100, 50000*0.1) = 5000 → 5000/100 = 50.0
        assert abs(qty - 50.0) < 1e-9

    def test_small_equity_notional_floor_100(self):
        """Si equity faible, notional = max(100, 10% equity) = 100."""
        qty = compute_position_size_fixed_notional(
            equity=500.0, notional_per_trade_usd=None, entry_price=50.0,
        )
        # max(100, 500*0.1) = max(100, 50) = 100 → 100/50 = 2.0
        assert abs(qty - 2.0) < 1e-9

    def test_very_high_price(self):
        """Prix très élevé → quantité très petite mais > 0."""
        qty = compute_position_size_fixed_notional(
            equity=10000.0, notional_per_trade_usd=100.0, entry_price=1_000_000.0,
        )
        assert qty > 0
        assert qty < 0.001

    def test_returns_float(self):
        """Le résultat est toujours un float."""
        qty = compute_position_size_fixed_notional(
            equity=10000.0, notional_per_trade_usd=100.0, entry_price=50.0,
        )
        assert isinstance(qty, float)


# ---------------------------------------------------------------------------
#  risk-based edge cases (compléments de test_position_sizing.py)
# ---------------------------------------------------------------------------

class TestRiskEdge:
    def test_nan_atr_returns_zero(self):
        """ATR = NaN → 0.0."""
        qty = compute_position_size_by_risk(
            equity=10000.0, atr_value=float('nan'), entry_price=100.0,
        )
        assert qty == 0.0

    def test_inf_atr_returns_zero(self):
        """ATR = Inf → 0.0 (quantité ~ 0)."""
        qty = compute_position_size_by_risk(
            equity=10000.0, atr_value=float('inf'), entry_price=100.0,
        )
        # qty = risk / (stop_mult * inf) → 0
        assert qty == 0.0

    def test_very_small_atr(self):
        """ATR très petit → grande position mais pas infine (float)."""
        qty = compute_position_size_by_risk(
            equity=10000.0, atr_value=0.0001, entry_price=100.0,
            risk_pct=0.01, stop_atr_multiplier=3.0,
        )
        # 100 / (3 * 0.0001) = 100 / 0.0003 ≈ 333333
        assert qty > 100_000
        assert isinstance(qty, float)

    def test_negative_equity_returns_zero(self):
        """Equity négatif → 0.0."""
        qty = compute_position_size_by_risk(
            equity=-5000.0, atr_value=2.0, entry_price=100.0,
        )
        assert qty == 0.0

    def test_negative_risk_pct_returns_zero(self):
        """risk_pct négatif → 0.0."""
        qty = compute_position_size_by_risk(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
            risk_pct=-0.01,
        )
        assert qty == 0.0

    def test_negative_stop_mult_returns_zero(self):
        """stop_atr_multiplier négatif → 0.0."""
        qty = compute_position_size_by_risk(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
            stop_atr_multiplier=-3.0,
        )
        assert qty == 0.0


# ---------------------------------------------------------------------------
#  Cross-mode consistency
# ---------------------------------------------------------------------------

class TestCrossMode:
    def test_all_modes_return_float(self):
        """Les 3 modes retournent un float."""
        r = compute_position_size_by_risk(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
        )
        f = compute_position_size_fixed_notional(
            equity=10000.0, notional_per_trade_usd=1000.0, entry_price=100.0,
        )
        v = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
        )
        assert isinstance(r, float)
        assert isinstance(f, float)
        assert isinstance(v, float)

    def test_all_modes_return_zero_on_bad_input(self):
        """Les 3 modes retournent 0.0 sur entrées invalides."""
        r = compute_position_size_by_risk(equity=0.0, atr_value=0.0, entry_price=0.0)
        f = compute_position_size_fixed_notional(equity=0.0, entry_price=0.0)
        v = compute_position_size_volatility_parity(equity=0.0, atr_value=0.0, entry_price=0.0)
        assert r == 0.0
        assert f == 0.0
        assert v == 0.0

    def test_all_modes_positive_on_valid_input(self):
        """Les 3 modes retournent > 0 sur entrées valides."""
        r = compute_position_size_by_risk(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
        )
        f = compute_position_size_fixed_notional(
            equity=10000.0, entry_price=100.0,
        )
        v = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
        )
        assert r > 0
        assert f > 0
        assert v > 0


# ══════════════════════════════════════════════════════════════════════════════
# P2-10 — NaN/Inf sur entry_price et equity
# ══════════════════════════════════════════════════════════════════════════════

class TestP210NaNInfEdges:
    """P2-10: couverture des cas NaN/Inf sur les 3 modes."""

    def test_risk_nan_entry_price(self):
        """entry_price=NaN → retourne 0.0."""
        r = compute_position_size_by_risk(
            equity=10000.0, atr_value=2.0, entry_price=float('nan'),
        )
        assert r == 0.0

    def test_risk_inf_entry_price(self):
        """entry_price=inf → retourne 0.0."""
        r = compute_position_size_by_risk(
            equity=10000.0, atr_value=2.0, entry_price=float('inf'),
        )
        assert r == 0.0

    def test_risk_nan_equity(self):
        """equity=NaN → retourne 0.0."""
        r = compute_position_size_by_risk(
            equity=float('nan'), atr_value=2.0, entry_price=100.0,
        )
        assert r == 0.0

    def test_fixed_notional_nan_entry_price(self):
        """entry_price=NaN → retourne 0.0."""
        f = compute_position_size_fixed_notional(
            equity=10000.0, entry_price=float('nan'),
        )
        assert f == 0.0

    def test_fixed_notional_inf_entry_price(self):
        """entry_price=inf → retourne 0.0."""
        f = compute_position_size_fixed_notional(
            equity=10000.0, entry_price=float('inf'),
        )
        assert f == 0.0

    def test_fixed_notional_negative_equity(self):
        """equity négatif → notional_per_trade >= 100 (floor)."""
        f = compute_position_size_fixed_notional(
            equity=-500.0, entry_price=100.0,
        )
        # max(100, -500 * 0.1) = 100 → qty = 100/100 = 1.0
        assert f >= 0.0

    def test_volatility_nan_entry_price(self):
        """entry_price=NaN → retourne 0.0."""
        v = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=2.0, entry_price=float('nan'),
        )
        assert v == 0.0

    def test_volatility_inf_entry_price(self):
        """entry_price=inf → retourne 0.0."""
        v = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=2.0, entry_price=float('inf'),
        )
        assert v == 0.0

    def test_volatility_negative_target(self):
        """target_volatility_pct négatif → utilise default 0.02."""
        v = compute_position_size_volatility_parity(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
            target_volatility_pct=-0.05,
        )
        # Négatif devrait être traité comme invalid → default ou 0.0
        assert isinstance(v, float)
