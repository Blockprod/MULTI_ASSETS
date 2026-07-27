"""P1-03: Named constants to replace magic numbers across the codebase.

Centralise toutes les valeurs numériques significatives pour faciliter
la modification, l'audit, et la documentation des seuils métier.
"""
from decimal import Decimal

# ── Order Manager ──────────────────────────────────────────────────────────────
QTY_OVERSHOOT_TOLERANCE = Decimal('1.02')   # 2% de tolérance sur min_qty (MI-03)
DUST_FINAL_FRACTION = Decimal('0.20')       # 20% du solde restant — seuil filtre dust

SL_MAX_RETRIES: int = 3                     # Nombre max de tentatives de pose du SL
SL_BACKOFF_BASE: float = 1.5               # Coefficient de backoff exponentiel (secondes)

TIMEFRAME_SECONDS: dict = {
    '1m': 60,
    '5m': 300,
    '15m': 900,
    '30m': 1800,
    '1h': 3600,
    '4h': 14400,
    '1d': 86400,
}

# ── Trade Helpers: Partial Sells ────────────────────────────────────────────────
PARTIAL_1_PROFIT_PCT: float = 1.02    # Seuil de profit +2% pour partial-1
PARTIAL_2_PROFIT_PCT: float = 1.04    # Seuil de profit +4% pour partial-2
PARTIAL_1_QTY_MIN: float = 0.45      # Ratio quantité min pour détection partial-1
PARTIAL_1_QTY_MAX: float = 0.55      # Ratio quantité max pour détection partial-1
PARTIAL_2_QTY_MIN: float = 0.25      # Ratio quantité min pour détection partial-2
PARTIAL_2_QTY_MAX: float = 0.35      # Ratio quantité max pour détection partial-2
SNIPER_BAND_PCT: float = 2.0         # Écart max % pour entrée sniper

# ── MULTI_SYMBOLS Runtime ──────────────────────────────────────────────────────
SAVE_THROTTLE_SECONDS: float = 5.0   # Intervalle min entre sauvegardes d'état
MAX_SAVE_FAILURES: int = 3           # Max d'échecs consécutifs avant emergency halt
