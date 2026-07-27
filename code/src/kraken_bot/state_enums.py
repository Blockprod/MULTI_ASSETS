"""
state_enums.py — Énums explicites pour machine d'état capital-critical.

Machine d'état pour paire (PairLifecycle) et bot (BotRiskState).
Réduit les bugs logiques en codifiant les transitions valides.

Architecture:
- PairLifecycle: états d'une paire (FLAT, LONG, SHORT, COVERING_LONG, COVERING_SHORT, OOS_BLOCKED)
- BotRiskState: état global du risque (NORMAL, WARNING, CRITICAL_DRAWDOWN, EMERGENCY_HALT)
- StateValidator: vérifie transitions valides + transitions de risque
"""

from __future__ import annotations
from enum import Enum
from typing import Set, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class PairLifecycle(Enum):
    """État d'une paire trading — déterminé par `pair_state['last_order_side']`."""

    FLAT = "FLAT"  # Pas de position — last_order_side ∈ (None, 'SELL', 'COVER')
    LONG = "LONG"  # Position long ouverte — last_order_side == 'BUY'
    SHORT = "SHORT"  # Position short ouverte — last_order_side == 'SHORT'
    COVERING_LONG = "COVERING_LONG"  # Fermeture d'un LONG en cours (SELL signal)
    COVERING_SHORT = "COVERING_SHORT"  # Fermeture d'un SHORT en cours (COVER signal)
    OOS_BLOCKED = "OOS_BLOCKED"  # Bloqué par OOS gates — entrée impossible jusqu'à unblock

    def is_position_open(self) -> bool:
        """True si une position réelle est ouverte."""
        return self in (PairLifecycle.LONG, PairLifecycle.SHORT)

    def is_closing(self) -> bool:
        """True si en phase de fermeture."""
        return self in (PairLifecycle.COVERING_LONG, PairLifecycle.COVERING_SHORT)

    @classmethod
    def from_last_order_side(cls, last_order_side: Optional[str], oos_blocked: bool = False) -> PairLifecycle:
        """Reconstruit l'état depuis last_order_side et oos_blocked.

        Args:
            last_order_side: 'BUY' | 'SHORT' | 'SELL' | 'COVER' | None
            oos_blocked: True si pair_state.get('oos_blocked') == True

        Returns:
            PairLifecycle correct
        """
        if oos_blocked:
            return cls.OOS_BLOCKED
        if last_order_side == "BUY":
            return cls.LONG
        if last_order_side == "SHORT":
            return cls.SHORT
        # SELL, COVER, None → FLAT
        return cls.FLAT


class BotRiskState(Enum):
    """État global du risque du bot."""

    NORMAL = "NORMAL"  # Opération normale — capital safe
    WARNING = "WARNING"  # Daily loss > seuil mais < emergency (ex: -3% du capital)
    CRITICAL_DRAWDOWN = "CRITICAL_DRAWDOWN"  # Drawdown > max_drawdown_pct
    EMERGENCY_HALT = "EMERGENCY_HALT"  # Arrêt d'urgence — aucun trading


class StateTransition(Enum):
    """Transitions valides dans la machine d'état."""

    # Entrées (FLAT → position)
    ENTRY_LONG = ("FLAT", "LONG")
    ENTRY_SHORT = ("FLAT", "SHORT")

    # Sorties (position → fermeture)
    BEGIN_CLOSE_LONG = ("LONG", "COVERING_LONG")
    BEGIN_CLOSE_SHORT = ("SHORT", "COVERING_SHORT")

    # Fin de fermeture
    CLOSE_COMPLETE = ("COVERING_LONG", "FLAT")
    CLOSE_COMPLETE_SHORT = ("COVERING_SHORT", "FLAT")

    # OOS gates
    ENTRY_OOS_BLOCKED = ("FLAT", "OOS_BLOCKED")
    EXIT_OOS_BLOCKED = ("OOS_BLOCKED", "FLAT")

    # SL ou ordres remplis (depuis n'importe quel état)
    SL_FILLED_FROM_LONG = ("LONG", "FLAT")
    SL_FILLED_FROM_SHORT = ("SHORT", "FLAT")
    SL_FILLED_FROM_COVERING_LONG = ("COVERING_LONG", "FLAT")
    SL_FILLED_FROM_COVERING_SHORT = ("COVERING_SHORT", "FLAT")

    @property
    def from_state(self) -> str:
        """État d'origine."""
        return self.value[0]

    @property
    def to_state(self) -> str:
        """État cible."""
        return self.value[1]


class StateValidator:
    """Validateur de transitions et invariants d'état machine."""

    # Tableau des transitions valides
    VALID_TRANSITIONS: Dict[str, Set[str]] = {
        PairLifecycle.FLAT.value: {
            PairLifecycle.LONG.value,
            PairLifecycle.SHORT.value,
            PairLifecycle.OOS_BLOCKED.value,
        },
        PairLifecycle.LONG.value: {
            PairLifecycle.FLAT.value,  # SL filled
            PairLifecycle.COVERING_LONG.value,  # Sell signal
        },
        PairLifecycle.SHORT.value: {
            PairLifecycle.FLAT.value,  # SL filled
            PairLifecycle.COVERING_SHORT.value,  # Cover signal
        },
        PairLifecycle.COVERING_LONG.value: {
            PairLifecycle.FLAT.value,  # Order filled
        },
        PairLifecycle.COVERING_SHORT.value: {
            PairLifecycle.FLAT.value,  # Order filled
        },
        PairLifecycle.OOS_BLOCKED.value: {
            PairLifecycle.FLAT.value,  # Exit OOS block
        },
    }

    @staticmethod
    def is_valid_transition(
        current_state: PairLifecycle, next_state: PairLifecycle
    ) -> bool:
        """Vérifie si la transition est valide."""
        valid_next = StateValidator.VALID_TRANSITIONS.get(
            current_state.value, set()
        )
        return next_state.value in valid_next

    @staticmethod
    def assert_transition(
        pair: str,
        current_state: PairLifecycle,
        next_state: PairLifecycle,
        context: str = "",
    ) -> None:
        """Lève une StateError si transition invalide."""
        if not StateValidator.is_valid_transition(current_state, next_state):
            msg = (
                f"[STATE-ERROR] {pair}: transition invalide "
                f"{current_state.value} → {next_state.value}"
            )
            if context:
                msg += f" ({context})"
            logger.critical(msg)
            raise ValueError(msg)

    @staticmethod
    def assert_invariant_position_open(
        pair_state: Dict[str, Any], pair: str
    ) -> None:
        """Vérifie invariant: si position ouverte → SL doit être posé."""
        lifecycle = PairLifecycle.from_last_order_side(
            pair_state.get("last_order_side"), pair_state.get("oos_blocked", False)
        )

        if lifecycle.is_position_open():
            sl_oid = pair_state.get("sl_order_id")
            sl_placed = pair_state.get("sl_exchange_placed", False)
            if not sl_placed or sl_oid is None:
                msg = (
                    f"[INVARIANT-VIOLATION] {pair}: position {lifecycle.value} ouverte "
                    f"sans SL exchange (sl_oid={sl_oid}, sl_placed={sl_placed})"
                )
                logger.critical(msg)
                raise ValueError(msg)

    @staticmethod
    def assert_invariant_no_double_exposure(
        pair_state: Dict[str, Any], pair: str
    ) -> None:
        """Vérifie invariant: jamais deux positions simultanées (LONG + SHORT)."""
        lifecycle = PairLifecycle.from_last_order_side(
            pair_state.get("last_order_side"), pair_state.get("oos_blocked", False)
        )

        # Un invariant implicite: last_order_side ne peut être que l'une de:
        # BUY (LONG), SHORT, SELL/COVER/None (FLAT)
        # Donc pas de double exposure par design
        if lifecycle == PairLifecycle.FLAT:
            if pair_state.get("entry_price") is not None:
                logger.warning(
                    "[INVARIANT] %s: état FLAT mais entry_price stale=%s — "
                    "nettoyage recommandé",
                    pair,
                    pair_state.get("entry_price"),
                )

    @staticmethod
    def assert_invariant_emergency_halt_blocks_entry(
        bot_state: Dict[str, Any], pair: str
    ) -> None:
        """Vérifie invariant: EMERGENCY_HALT → pas d'entrée nouvelle."""
        if bot_state.get("emergency_halt", False):
            lifecycle = PairLifecycle.from_last_order_side(
                bot_state.get(pair, {}).get("last_order_side")
            )
            if lifecycle == PairLifecycle.FLAT:
                # Flat est OK (aucune position nouvelle)
                pass
            elif lifecycle in (PairLifecycle.LONG, PairLifecycle.SHORT):
                # Position existante avant le halt — acceptable (doit être gérée par SL)
                logger.warning(
                    "[INVARIANT] %s: position %s ouverte avant EMERGENCY_HALT",
                    pair,
                    lifecycle.value,
                )


def validate_pair_state_invariants(
    bot_state: Dict[str, Any], pair: str
) -> None:
    """Lance tous les invariants pour une paire.

    Raises:
        ValueError si un invariant échoue
    """
    pair_state = bot_state.get(pair, {})
    StateValidator.assert_invariant_position_open(pair_state, pair)
    StateValidator.assert_invariant_no_double_exposure(pair_state, pair)
    StateValidator.assert_invariant_emergency_halt_blocks_entry(bot_state, pair)


def get_pair_lifecycle(bot_state: Dict[str, Any], pair: str) -> PairLifecycle:
    """Récupère l'état lifecycle d'une paire."""
    pair_state = bot_state.get(pair, {})
    return PairLifecycle.from_last_order_side(
        pair_state.get("last_order_side"),
        pair_state.get("oos_blocked", False),
    )


def get_bot_risk_state(bot_state: Dict[str, Any]) -> BotRiskState:
    """Détermine l'état global de risque du bot.

    Logic:
    - EMERGENCY_HALT → EMERGENCY_HALT
    - daily_loss > max_drawdown_pct → CRITICAL_DRAWDOWN
    - daily_loss > 0.5 * max_drawdown_pct → WARNING
    - Sinon → NORMAL
    """
    if bot_state.get("emergency_halt", False):
        return BotRiskState.EMERGENCY_HALT

    daily_loss_pct = bot_state.get("daily_loss_pct", 0.0)
    max_drawdown = bot_state.get("max_drawdown_pct", 0.05)

    if daily_loss_pct >= max_drawdown:
        return BotRiskState.CRITICAL_DRAWDOWN
    if daily_loss_pct >= 0.5 * max_drawdown:
        return BotRiskState.WARNING

    return BotRiskState.NORMAL
