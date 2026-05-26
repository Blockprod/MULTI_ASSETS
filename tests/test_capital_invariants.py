"""Tests des invariants critiques capital (P2-2)."""
import os
import sys
import pytest
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

from state_enums import (
    PairLifecycle,
    get_pair_lifecycle,
    validate_pair_state_invariants,
)


class TestNeverBuyWithoutSL:
    """P2-2.1: BUY doit avoir SL immédiatement."""

    def test_buy_without_sl_fails(self):
        """BUY + sl_order_id=None → erreur."""
        bot_state = {
            "SOLUSDT": {
                "last_order_side": "BUY",
                "entry_price": 100.0,
                "quantity": 1.0,
                "sl_order_id": None,
                "sl_exchange_placed": False,
            }
        }
        with pytest.raises(ValueError, match="INVARIANT-VIOLATION"):
            validate_pair_state_invariants(bot_state, "SOLUSDT")

    def test_buy_with_sl_valid(self):
        """BUY + SL valide → OK."""
        bot_state = {
            "SOLUSDT": {
                "last_order_side": "BUY",
                "entry_price": 100.0,
                "quantity": 1.0,
                "sl_order_id": "987654",
                "sl_exchange_placed": True,
                "stop_loss": 95.0,
            }
        }
        validate_pair_state_invariants(bot_state, "SOLUSDT")

    def test_short_without_sl_fails(self):
        """SHORT + sl_order_id=None → erreur."""
        bot_state = {
            "EURUSD": {
                "last_order_side": "SHORT",
                "entry_price": 1.08,
                "quantity": 100000.0,
                "sl_order_id": None,
                "sl_exchange_placed": False,
            }
        }
        with pytest.raises(ValueError, match="INVARIANT-VIOLATION"):
            validate_pair_state_invariants(bot_state, "EURUSD")


class TestNoDoubleExposure:
    """P2-2.2: Une paire ne peut pas être LONG + SHORT."""

    def test_single_long_valid(self):
        """LONG seul → OK."""
        bot_state = {
            "SOLUSDT": {
                "last_order_side": "BUY",
                "entry_price": 100.0,
                "quantity": 1.0,
                "sl_order_id": "123456",
                "sl_exchange_placed": True,
            }
        }
        lifecycle = get_pair_lifecycle(bot_state, "SOLUSDT")
        assert lifecycle == PairLifecycle.LONG
        validate_pair_state_invariants(bot_state, "SOLUSDT")

    def test_single_short_valid(self):
        """SHORT seul → OK."""
        bot_state = {
            "EURUSD": {
                "last_order_side": "SHORT",
                "entry_price": 1.08,
                "quantity": 100000.0,
                "sl_order_id": "555555",
                "sl_exchange_placed": True,
            }
        }
        lifecycle = get_pair_lifecycle(bot_state, "EURUSD")
        assert lifecycle == PairLifecycle.SHORT
        validate_pair_state_invariants(bot_state, "EURUSD")

    def test_flat_valid(self):
        """FLAT (no position) → OK."""
        bot_state = {
            "SOLUSDT": {
                "last_order_side": None,
                "entry_price": None,
                "quantity": None,
            }
        }
        lifecycle = get_pair_lifecycle(bot_state, "SOLUSDT")
        assert lifecycle == PairLifecycle.FLAT
        validate_pair_state_invariants(bot_state, "SOLUSDT")


class TestEmergencyHaltPropagation:
    """P2-2.3: emergency_halt=True → pas de trading neuf."""

    def test_emergency_halt_with_flat_ok(self):
        """emergency_halt + FLAT → OK (pas d'entrée)."""
        bot_state = {
            "emergency_halt": True,
            "SOLUSDT": {
                "last_order_side": None,
                "entry_price": None,
                "quantity": None,
            },
        }
        validate_pair_state_invariants(bot_state, "SOLUSDT")

    def test_emergency_halt_with_position_needs_sl(self):
        """emergency_halt + position ouverte → doit avoir SL."""
        bot_state = {
            "emergency_halt": True,
            "SOLUSDT": {
                "last_order_side": "BUY",
                "entry_price": 100.0,
                "quantity": 1.0,
                "sl_order_id": "123456",
                "sl_exchange_placed": True,
                "stop_loss": 95.0,
            },
        }
        validate_pair_state_invariants(bot_state, "SOLUSDT")

    def test_emergency_halt_position_without_sl_fails(self):
        """emergency_halt + position SANS SL → erreur."""
        bot_state = {
            "emergency_halt": True,
            "SOLUSDT": {
                "last_order_side": "BUY",
                "entry_price": 100.0,
                "quantity": 1.0,
                "sl_order_id": None,
                "sl_exchange_placed": False,
            },
        }
        with pytest.raises(ValueError, match="INVARIANT-VIOLATION"):
            validate_pair_state_invariants(bot_state, "SOLUSDT")


class TestMultiPairInvariants:
    """Invariants sur toutes les paires."""

    def test_all_pairs_with_positions_have_sl(self):
        """Toutes positions doivent avoir SL."""
        bot_state = {
            "SOLUSDT": {
                "last_order_side": "BUY",
                "entry_price": 100.0,
                "quantity": 1.0,
                "sl_order_id": "111111",
                "sl_exchange_placed": True,
            },
            "PEPEUSDT": {
                "last_order_side": "SHORT",
                "entry_price": 0.002,
                "quantity": 50000.0,
                "sl_order_id": "222222",
                "sl_exchange_placed": True,
            },
            "ETHUSDT": {
                "last_order_side": None,
                "entry_price": None,
                "quantity": None,
            },
        }
        for pair in ["SOLUSDT", "PEPEUSDT", "ETHUSDT"]:
            validate_pair_state_invariants(bot_state, pair)

    def test_one_pair_missing_sl_fails(self):
        """Si une paire oublie SL → fail."""
        bot_state = {
            "SOLUSDT": {
                "last_order_side": "BUY",
                "entry_price": 100.0,
                "quantity": 1.0,
                "sl_order_id": "111111",
                "sl_exchange_placed": True,
            },
            "PEPEUSDT": {
                "last_order_side": "SHORT",
                "entry_price": 0.002,
                "quantity": 50000.0,
                "sl_order_id": None,
                "sl_exchange_placed": False,
            },
        }
        validate_pair_state_invariants(bot_state, "SOLUSDT")
        with pytest.raises(ValueError, match="INVARIANT-VIOLATION"):
            validate_pair_state_invariants(bot_state, "PEPEUSDT")


class TestOOSBlockedState:
    """OOS_BLOCKED → pas d'entrée."""

    def test_oos_blocked_no_entry(self):
        """OOS_BLOCKED → pas d'entrée."""
        bot_state = {
            "SOLUSDT": {
                "last_order_side": None,
                "oos_blocked": True,
                "oos_blocked_since": time.time(),
            }
        }
        lifecycle = get_pair_lifecycle(bot_state, "SOLUSDT")
        assert lifecycle == PairLifecycle.OOS_BLOCKED

    def test_oos_blocked_with_existing_position(self):
        """OOS_BLOCKED + position existante → SL gère."""
        bot_state = {
            "SOLUSDT": {
                "last_order_side": "BUY",
                "oos_blocked": True,
                "oos_blocked_since": time.time(),
                "entry_price": 100.0,
                "quantity": 1.0,
                "sl_order_id": "123456",
                "sl_exchange_placed": True,
            }
        }
        lifecycle = get_pair_lifecycle(bot_state, "SOLUSDT")
        assert lifecycle == PairLifecycle.OOS_BLOCKED
        validate_pair_state_invariants(bot_state, "SOLUSDT")
