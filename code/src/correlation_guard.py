"""
correlation_guard.py — Anti-corrélation: bloque entrées si paires trop corrélées (> 0.85 30j).

Protection contre risque systématique: deux paires hautement corrélées = risque de perte combinée.

Architecture:
- CorrelationData: cache rolling correlation sur 30j
- CorrelationGuard: vérifie si entrée candidate corrélée avec positions ouvertes
- check_correlation_guard(): fonction publique pour blocage entrée
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Paramètres du guard
# ──────────────────────────────────────────────────────────────────────────────
CORRELATION_WINDOW_DAYS = 30  # Fenêtre rolling 30 jours
CORRELATION_THRESHOLD = 0.85  # Seuil de blocage
MIN_CANDLES_FOR_CORRELATION = 100  # Min bougies pour calcul valide


class CorrelationData:
    """Cache rolling correlation entre paires."""

    def __init__(self):
        """Initialise cache."""
        # Structure: pair_name → pd.Series (close prices avec timestamp)
        self._price_history: Dict[str, pd.DataFrame] = {}
        self._last_correlation_check: Dict[Tuple[str, str], Tuple[float, float]] = {}

    def add_candle(self, pair: str, timestamp: float, close_price: float) -> None:
        """Ajoute une bougie au historique d'une paire (idempotent sur timestamp).

        Args:
            pair: Symbole paire (ex: 'SOLUSDT', 'EURUSD')
            timestamp: Timestamp Unix (secondes)
            close_price: Prix de clôture
        """
        if pair not in self._price_history:
            self._price_history[pair] = pd.DataFrame(
                columns=["timestamp", "close"]
            )

        df = self._price_history[pair]
        # Idempotence: ignorer si timestamp déjà présent
        if len(df) > 0 and timestamp in df["timestamp"].values:
            return

        new_row = pd.DataFrame(
            {"timestamp": [timestamp], "close": [close_price]}
        )
        self._price_history[pair] = pd.concat([df, new_row], ignore_index=True)

        # Nettoyer: garder seulement 30j + buffer
        cutoff_timestamp = datetime.now().timestamp() - (
            CORRELATION_WINDOW_DAYS * 86400 * 1.5
        )
        self._price_history[pair] = self._price_history[pair][
            self._price_history[pair]["timestamp"] >= cutoff_timestamp
        ].reset_index(drop=True)

    def get_correlation(self, pair1: str, pair2: str) -> Optional[float]:
        """Calcule corrélation rolling 30j entre deux paires.

        Returns:
            Corrélation coefficient (-1.0 à +1.0) ou None si données insuffisantes
        """
        if pair1 not in self._price_history or pair2 not in self._price_history:
            return None

        df1 = self._price_history[pair1]
        df2 = self._price_history[pair2]

        if len(df1) < MIN_CANDLES_FOR_CORRELATION or len(
            df2
        ) < MIN_CANDLES_FOR_CORRELATION:
            logger.debug(
                "[CORR] Historique insuffisant pour %s/%s (%d / %d candles)",
                pair1,
                pair2,
                len(df1),
                len(df2),
            )
            return None

        # Aligner les séries par timestamp
        df1_sorted = df1.sort_values("timestamp").reset_index(drop=True)
        df2_sorted = df2.sort_values("timestamp").reset_index(drop=True)

        # Merger sur timestamp (inner join)
        merged = pd.merge(
            df1_sorted,
            df2_sorted,
            on="timestamp",
            how="inner",
            suffixes=("_1", "_2"),
        )

        if len(merged) < MIN_CANDLES_FOR_CORRELATION:
            logger.debug(
                "[CORR] Pas assez de points communs %s/%s (%d)",
                pair1,
                pair2,
                len(merged),
            )
            return None

        # Garder fenêtre 30j
        cutoff = datetime.now().timestamp() - (CORRELATION_WINDOW_DAYS * 86400)
        merged = merged[merged["timestamp"] >= cutoff]

        if len(merged) < MIN_CANDLES_FOR_CORRELATION:
            return None

        # Calculer corrélation
        corr = merged["close_1"].corr(merged["close_2"])
        return float(corr) if not np.isnan(corr) else None


class CorrelationGuard:
    """Vérifie si une entrée est autorisée basé sur corrélations."""

    def __init__(self, correlation_data: Optional[CorrelationData] = None):
        """Initialise guard.

        Args:
            correlation_data: Instance CorrelationData ou None (mode test)
        """
        self.correlation_data = correlation_data or CorrelationData()

    def can_enter(
        self, candidate_pair: str, open_positions: Dict[str, str]
    ) -> Tuple[bool, Optional[str]]:
        """Vérifie si une entrée est autorisée.

        Args:
            candidate_pair: Paire candidate pour entrée (ex: 'SOLUSDT')
            open_positions: Dict paires ouvertes (pair → last_order_side)
                           Exemple: {'PEPEUSDT': 'BUY', 'EURUSD': 'SHORT'}

        Returns:
            (autorised: bool, raison_blocage: Optional[str])
            - (True, None) si entrée OK
            - (False, "SOLUSDT corrélé à PEPEUSDT (0.91)") si bloquée
        """
        blocked_pairs = []
        for open_pair, side in open_positions.items():
            # Ignorer positions en cours de fermeture (SELLING/COVERING)
            if side not in ("BUY", "SHORT"):
                continue

            corr = self.correlation_data.get_correlation(
                candidate_pair, open_pair
            )
            if corr is None:
                # Pas assez de données — autoriser (pas de donnée = risque modéré)
                continue

            # Vérifier seuil
            abs_corr = abs(corr)
            if abs_corr >= CORRELATION_THRESHOLD:
                blocked_pairs.append((open_pair, corr))

        if blocked_pairs:
            reasons = [
                f"{p} ({c:.2f})" for p, c in blocked_pairs
            ]
            reason = f"{candidate_pair} corrélé à {', '.join(reasons)}"
            return False, reason

        return True, None


# Singleton persistant — alimenté par feed_candle() à chaque cycle de trading
_GLOBAL_GUARD = CorrelationGuard()


def feed_candle(pair: str, timestamp: float, close_price: float) -> None:
    """Alimente le guard global avec une bougie fermée.

    Appelé depuis la boucle de trading après chaque fetch OHLCV.
    Idempotent : double appel avec même timestamp est ignoré.

    Args:
        pair: Symbole paire (ex: 'SOLUSDT')
        timestamp: Timestamp Unix de la bougie fermée (secondes)
        close_price: Prix de clôture de la bougie
    """
    _GLOBAL_GUARD.correlation_data.add_candle(pair, timestamp, close_price)


def check_correlation_guard(
    candidate_pair: str,
    bot_state: Dict,
    correlation_guard: Optional[CorrelationGuard] = None,
) -> Tuple[bool, Optional[str]]:
    """Vérifie garde corrélation avant entrée.

    Args:
        candidate_pair: Paire candidate pour BUY/SHORT
        bot_state: État du bot contenant positions ouvertes
        correlation_guard: Instance guard (sinon _GLOBAL_GUARD utilisé)

    Returns:
        (authorized: bool, reason: Optional[str])
    """
    if correlation_guard is None:
        correlation_guard = _GLOBAL_GUARD

    # Extraire positions ouvertes — seules les clés avec dict (pair_state) sont valides
    open_positions = {}
    for pair, pair_state in bot_state.items():
        if not isinstance(pair_state, dict):
            continue  # Ignorer clés non-paires (bool, float, str...)
        last_side = pair_state.get("last_order_side")
        if last_side in ("BUY", "SHORT"):
            open_positions[pair] = last_side

    return correlation_guard.can_enter(candidate_pair, open_positions)
