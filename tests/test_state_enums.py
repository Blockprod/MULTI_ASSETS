"""Tests pour machine d'état explicite (state_enums.py)."""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

from state_enums import (
    PairLifecycle,
    BotRiskState,
    StateValidator,
    get_pair_lifecycle,
    get_bot_risk_state,
    validate_pair_state_invariants,
)


class TestPairLifecycleTransitions:
    """Transitions valides/invalides."""

    def test_entry_long_from_flat(self):
        """FLAT → LONG valide."""
        assert StateValidator.is_valid_transition(PairLifecycle.FLAT, PairLifecycle.LONG)

    def test_entry_short_from_flat(self):
        """FLAT → SHORT valide."""
        assert StateValidator.is_valid_transition(PairLifecycle.FLAT, PairLifecycle.SHORT)

    def test_invalid_long_to_short(self):
        """LONG → SHORT invalide."""
        assert not StateValidator.is_valid_transition(PairLifecycle.LONG, PairLifecycle.SHORT)

    def test_assert_transition_raises_on_invalid(self):
        """assert_transition() lève si invalide."""
        with pytest.raises(ValueError, match="transition invalide"):
            StateValidator.assert_transition("EURUSD", PairLifecycle.LONG, PairLifecycle.SHORT)


class TestFromLastOrderSide:
    """Reconstruction état depuis last_order_side."""

    def test_flat_from_none(self):
        """None → FLAT."""
        assert PairLifecycle.from_last_order_side(None) == PairLifecycle.FLAT

    def test_long_from_buy(self):
        """'BUY' → LONG."""
        assert PairLifecycle.from_last_order_side("BUY") == PairLifecycle.LONG

    def test_oos_blocked_overrides(self):
        """oos_blocked=True → OOS_BLOCKED."""
        assert PairLifecycle.from_last_order_side("BUY", oos_blocked=True) == PairLifecycle.OOS_BLOCKED


class TestInvariantPositionOpen:
    """Position ouverte → SL posé."""

    def test_long_without_sl_raises(self):
        """LONG sans SL → erreur."""
        pair_state = {"last_order_side": "BUY", "sl_order_id": None, "sl_exchange_placed": False}
        with pytest.raises(ValueError, match="INVARIANT-VIOLATION"):
            StateValidator.assert_invariant_position_open(pair_state, "SOLUSDT")

    def test_long_with_sl_passes(self):
        """LONG avec SL → OK."""
        pair_state = {"last_order_side": "BUY", "sl_order_id": "12345", "sl_exchange_placed": True}
        StateValidator.assert_invariant_position_open(pair_state, "SOLUSDT")


class TestValidateAllInvariants:
    """Lance tous invariants."""

    def test_valid_long_position(self):
        """LONG valide passe tous invariants."""
        bot_state = {
            "emergency_halt": False,
            "SOLUSDT": {
                "last_order_side": "BUY",
                "entry_price": 100.0,
                "sl_order_id": "12345",
                "sl_exchange_placed": True,
            },
        }
        validate_pair_state_invariants(bot_state, "SOLUSDT")

    def test_long_without_sl_fails(self):
        """LONG sans SL échoue invariant."""
        bot_state = {
            "emergency_halt": False,
            "SOLUSDT": {
                "last_order_side": "BUY",
                "entry_price": 100.0,
                "sl_order_id": None,
                "sl_exchange_placed": False,
            },
        }
        with pytest.raises(ValueError):
            validate_pair_state_invariants(bot_state, "SOLUSDT")


class TestGetPairLifecycle:
    """Helper get_pair_lifecycle()."""

    def test_get_long_lifecycle(self):
        """Récupère LONG."""
        bot_state = {"SOLUSDT": {"last_order_side": "BUY", "oos_blocked": False}}
        assert get_pair_lifecycle(bot_state, "SOLUSDT") == PairLifecycle.LONG

    def test_get_flat_lifecycle(self):
        """Récupère FLAT."""
        bot_state = {"SOLUSDT": {"last_order_side": None, "oos_blocked": False}}
        assert get_pair_lifecycle(bot_state, "SOLUSDT") == PairLifecycle.FLAT


class TestGetBotRiskState:
    """Helper get_bot_risk_state()."""

    def test_normal_state(self):
        """Perte < WARNING → NORMAL."""
        bot_state = {
            "emergency_halt": False,
            "daily_loss_pct": 0.01,
            "max_drawdown_pct": 0.05,
        }
        assert get_bot_risk_state(bot_state) == BotRiskState.NORMAL

    def test_critical_drawdown(self):
        """Perte >= max → CRITICAL."""
        bot_state = {
            "emergency_halt": False,
            "daily_loss_pct": 0.06,
            "max_drawdown_pct": 0.05,
        }
        assert get_bot_risk_state(bot_state) == BotRiskState.CRITICAL_DRAWDOWN
