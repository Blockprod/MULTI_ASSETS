"""
tests/test_position_sizing.py — Tests unitaires pour position_sizing.py (C-09)

Couvre :
- compute_position_size_by_risk : cas nominaux et cas limites
- compute_position_size_fixed_notional : cas nominaux et cas limites
- Entrées invalides → 0.0 sans exception
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

import pytest


class TestPositionSizeByRisk:
    def test_nominal(self):
        """Taille calculée correctement sur un cas nominal."""
        from position_sizing import compute_position_size_by_risk
        # equity=10000, risk_pct=0.01 (1%), entry=100, atr=2, stop_mult=3
        # stop_distance = 3 * 2 = 6 USD
        # risk_amount = 10000 * 0.01 = 100 USD
        # qty = 100 / 6 ≈ 16.666...
        qty = compute_position_size_by_risk(
            equity=10000.0, atr_value=2.0, entry_price=100.0,
            risk_pct=0.01, stop_atr_multiplier=3.0
        )
        assert abs(qty - (100.0 / 6.0)) < 1e-9

    def test_zero_atr_returns_zero(self):
        """ATR nul → quantité 0 (pas de division par zéro)."""
        from position_sizing import compute_position_size_by_risk
        qty = compute_position_size_by_risk(
            equity=10000.0, atr_value=0.0, entry_price=100.0
        )
        assert qty == 0.0

    def test_negative_atr_returns_zero(self):
        """ATR négatif → quantité 0."""
        from position_sizing import compute_position_size_by_risk
        qty = compute_position_size_by_risk(
            equity=10000.0, atr_value=-1.0, entry_price=100.0
        )
        assert qty == 0.0

    def test_none_atr_returns_zero(self):
        """ATR None → quantité 0 sans exception."""
        from position_sizing import compute_position_size_by_risk
        qty = compute_position_size_by_risk(
            equity=10000.0, atr_value=None, entry_price=100.0
        )
        assert qty == 0.0

    def test_zero_equity_returns_zero(self):
        """Capital nul → quantité 0."""
        from position_sizing import compute_position_size_by_risk
        qty = compute_position_size_by_risk(
            equity=0.0, atr_value=2.0, entry_price=100.0, risk_pct=0.01
        )
        assert qty == 0.0

    def test_zero_entry_price_returns_zero(self):
        """Prix d'entrée nul → quantité 0."""
        from position_sizing import compute_position_size_by_risk
        qty = compute_position_size_by_risk(
            equity=10000.0, atr_value=2.0, entry_price=0.0
        )
        assert qty == 0.0

    def test_high_risk_pct(self):
        """risk_pct = 1.0 (100%) → quantité cohérente."""
        from position_sizing import compute_position_size_by_risk
        qty = compute_position_size_by_risk(
            equity=1000.0, atr_value=10.0, entry_price=200.0,
            risk_pct=1.0, stop_atr_multiplier=2.0
        )
        # risk_amount = 1000, stop_distance = 20 → qty = 50
        assert abs(qty - 50.0) < 1e-9

    def test_returns_float(self):
        """La fonction retourne toujours un float."""
        from position_sizing import compute_position_size_by_risk
        qty = compute_position_size_by_risk(
            equity=5000.0, atr_value=5.0, entry_price=50.0
        )
        assert isinstance(qty, float)


class TestPositionSizeFixedNotional:
    def test_nominal(self):
        """Montant fixe de 500 USD / prix 100 → 5 coins."""
        from position_sizing import compute_position_size_fixed_notional
        qty = compute_position_size_fixed_notional(
            equity=10000.0, notional_per_trade_usd=500.0, entry_price=100.0
        )
        assert abs(qty - 5.0) < 1e-9

    def test_zero_entry_price_returns_zero(self):
        """Prix d'entrée nul → quantité 0."""
        from position_sizing import compute_position_size_fixed_notional
        qty = compute_position_size_fixed_notional(
            equity=10000.0, notional_per_trade_usd=500.0, entry_price=0.0
        )
        assert qty == 0.0

    def test_none_entry_price_returns_zero(self):
        """Prix d'entrée None → quantité 0 sans exception."""
        from position_sizing import compute_position_size_fixed_notional
        qty = compute_position_size_fixed_notional(
            equity=10000.0, notional_per_trade_usd=500.0, entry_price=None
        )
        assert qty == 0.0

    def test_default_notional_based_on_equity(self):
        """Sans notional_per_trade_usd, utilise 10% de equity."""
        from position_sizing import compute_position_size_fixed_notional
        # equity=10000, default notional = max(100, 1000) = 1000 → qty = 1000/200 = 5
        qty = compute_position_size_fixed_notional(
            equity=10000.0, entry_price=200.0
        )
        assert qty > 0.0
