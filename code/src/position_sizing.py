"""
position_sizing.py — Fonctions de dimensionnement de position.

Contient les 3 modes de sizing:
- risk-based (ATR stop-loss)
- fixed notional (montant USD fixe)
- volatility parity (volatilité cible du P&L)
"""
import logging
from typing import Optional

from bot_config import config

logger = logging.getLogger('trading_bot')


def compute_position_size_by_risk(
    equity: float,
    atr_value: Optional[float],
    entry_price: float,
    risk_pct: Optional[float] = None,
    stop_atr_multiplier: Optional[float] = None,
) -> float:
    """Calcule la taille de position basée sur un risque fixe en $.

    Args:
        equity: capital disponible en quote currency
        atr_value: valeur ATR
        entry_price: prix d'entrée prévu
        risk_pct: fraction du capital à risquer. Si None → config.risk_per_trade
        stop_atr_multiplier: multiple ATR pour le stop. Si None → config.atr_stop_multiplier

    Returns:
        Quantité de coin à acheter (float).
    """
    try:
        if risk_pct is None:
            risk_pct = config.risk_per_trade
        if stop_atr_multiplier is None:
            stop_atr_multiplier = config.atr_stop_multiplier

        if atr_value is None or atr_value <= 0 or entry_price is None or entry_price <= 0:
            return 0.0

        stop_distance = stop_atr_multiplier * float(atr_value)
        if stop_distance <= 0:
            return 0.0

        risk_amount = float(equity) * float(risk_pct)
        if risk_amount <= 0:
            return 0.0

        qty_coin = risk_amount / stop_distance
        return float(qty_coin) if qty_coin > 0 else 0.0
    except Exception:
        return 0.0


def compute_position_size_fixed_notional(
    equity: float,
    notional_per_trade_usd: Optional[float] = None,
    entry_price: Optional[float] = None,
) -> float:
    """Calcule la taille de position avec allocation fixe en USD.

    Args:
        equity: capital total disponible
        notional_per_trade_usd: montant USD fixe. Si None → 10 % de equity.
        entry_price: prix d'entrée prévu

    Returns:
        Quantité de coin à acheter (float).
    """
    try:
        if entry_price is None or entry_price <= 0:
            return 0.0
        if notional_per_trade_usd is None:
            notional_per_trade_usd = max(100.0, equity * 0.1)
        if notional_per_trade_usd <= 0:
            return 0.0
        qty_coin = float(notional_per_trade_usd) / float(entry_price)
        return float(qty_coin) if qty_coin > 0 else 0.0
    except Exception:
        return 0.0


def compute_position_size_volatility_parity(
    equity: float,
    atr_value: float,
    entry_price: float,
    target_volatility_pct: float = 0.02,
) -> float:
    """Calcule la taille de position pour maintenir une volatilité fixe du P&L.

    Taille = (equity × target_volatility_pct) / (ATR × entry_price)

    Args:
        equity: capital disponible
        atr_value: valeur ATR
        entry_price: prix d'entrée prévu
        target_volatility_pct: volatilité cible (ex: 0.02 = 2 %)

    Returns:
        Quantité de coin à acheter (float).
    """
    try:
        if atr_value is None or atr_value <= 0 or entry_price is None or entry_price <= 0:
            return 0.0
        if target_volatility_pct is None or target_volatility_pct <= 0:
            target_volatility_pct = 0.02

        volatility_amount = float(equity) * float(target_volatility_pct)
        qty_coin = volatility_amount / (float(atr_value) * float(entry_price))
        return float(qty_coin) if qty_coin > 0 else 0.0
    except Exception:
        return 0.0
