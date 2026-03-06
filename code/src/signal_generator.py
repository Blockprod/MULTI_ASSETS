"""
signal_generator.py — Buy/Sell signal condition checkers.

Extracted from MULTI_SYMBOLS.py (P3-SRP) to isolate signal generation logic
from the trading execution engine.

Functions are pure closures that return checker functions.  No mutable global
state — all behaviour is locked-in at closure creation time.
"""

from __future__ import annotations

import logging
import pandas as pd
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def generate_buy_condition_checker(
    best_params: Dict[str, Any],
) -> Callable[[pd.Series, float], Tuple[bool, str]]:
    """
    Génère une fonction de vérification des conditions d'achat
    reflétant EXACTEMENT le backtest gagnant.

    La closure capture *best_params* et renvoie un checker pur (pas d'effet
    de bord).  Inclut : filtre volatilité, timing optimisé, RSI momentum.

    Parameters
    ----------
    best_params : dict
        Meilleurs paramètres de backtest ; clé ``'scenario'`` requise.

    Returns
    -------
    callable
        ``check_buy_signal(row, usdc_balance) -> (bool, str)``
    """
    def check_buy_signal(row: pd.Series, usdc_balance: float) -> Tuple[bool, str]:
        """
        Vérifie si les conditions d'achat sont remplies.
        Retourne (is_buy_signal, detailed_reason)
        """
        if usdc_balance <= 0:
            return False, "Solde USDC insuffisant"

        # Condition de base : EMA1 > EMA2 + StochRSI dans la fenêtre [buy_min, buy_max]
        # P2-08: seuils depuis config pour cohérence exacte avec le backtest
        from bot_config import config as _cfg_buy
        _buy_min = getattr(_cfg_buy, 'stoch_rsi_buy_min', 0.05)
        _buy_max = getattr(_cfg_buy, 'stoch_rsi_buy_max', 0.8)
        ema_condition = row['ema1'] > row['ema2']
        stoch_condition = row['stoch_rsi'] < _buy_max

        if not ema_condition:
            return False, f"EMA1 ({row['ema1']:.2f}) <= EMA2 ({row['ema2']:.2f})"
        if not stoch_condition:
            return False, f"StochRSI ({row['stoch_rsi']:.4f}) >= {_buy_max}"
        if row['stoch_rsi'] <= _buy_min:
            return False, f"StochRSI ({row['stoch_rsi']:.4f}) <= {_buy_min} (trop bas)"

        # Conditions additionnelles selon le scénario
        scenario = best_params.get('scenario', 'StochRSI')

        if scenario == 'StochRSI_SMA' and 'sma_long' in row:
            sma_long = best_params.get('sma_long', 200)
            if row['close'] <= row['sma_long']:
                return False, f"Prix ({row['close']:.4f}) <= SMA{sma_long} ({row['sma_long']:.4f})"

        if scenario == 'StochRSI_ADX' and 'adx' in row:
            from bot_config import config as _cfg_adx
            adx_threshold = getattr(_cfg_adx, 'adx_threshold', 25.0)
            if row['adx'] <= adx_threshold:
                return False, f"ADX ({row['adx']:.2f}) <= {adx_threshold}"

        if scenario == 'StochRSI_TRIX' and 'TRIX_HISTO' in row:
            if row['TRIX_HISTO'] <= 0:
                return False, f"TRIX_HISTO ({row['TRIX_HISTO']:.6f}) <= 0"

        # A-1: Volume filter — volume > SMA(volume)
        from bot_config import config as _cfg
        if getattr(_cfg, 'volume_filter_enabled', False):
            if 'volume' in row and 'vol_sma' in row:
                try:
                    _vol = float(row['volume'])
                    _vsma = float(row['vol_sma'])
                    if _vsma > 0 and _vol <= _vsma:
                        return False, f"Volume ({_vol:,.0f}) <= SMA_vol ({_vsma:,.0f})"
                except (ValueError, TypeError):
                    pass  # skip filter if data unavailable

        # A-2: Multi-timeframe filter — 4h trend must be bullish
        if getattr(_cfg, 'mtf_filter_enabled', False):
            _mtf = row.get('mtf_bullish') if hasattr(row, 'get') else row.get('mtf_bullish', None)
            if _mtf is not None and float(_mtf) < 0.5:
                return False, "MTF 4h trend baissier (EMA_fast_4h <= EMA_slow_4h)"

        # Toutes les conditions sont remplies
        return True, "[OK] Signal d'achat valide"

    return check_buy_signal


def generate_sell_condition_checker(
    best_params: Dict[str, Any],
    config: Any = None,
) -> Callable[..., Tuple[bool, Optional[str]]]:
    """
    Génère une fonction de vérification des conditions de vente
    reflétant EXACTEMENT le backtest gagnant.

    Inclut : trailing stop profit-based, partial profit taking,
    dynamic ATR stop-loss.

    Parameters
    ----------
    best_params : dict
        Meilleurs paramètres de backtest ; clé ``'scenario'`` requise.
    config : object, optional
        Config bot avec ``atr_stop_multiplier``.  Par défaut 5.5.

    Returns
    -------
    callable
        ``check_sell_signal(row, coin_balance, entry_price, current_price,
        atr_value) -> (bool, Optional[str])``
    """
    # Resolve atr_stop_multiplier from config or default
    atr_stop_multiplier = getattr(config, 'atr_stop_multiplier', 5.5) if config else 5.5

    def check_sell_signal(row: pd.Series, coin_balance: float,
                          entry_price: Optional[float], current_price: float,
                          atr_value: Optional[float]) -> Tuple[bool, Optional[str]]:
        """
        Vérifie si les conditions de vente sont remplies.
        Retourne (is_sell_signal, sell_reason)
        - sell_reason peut être : 'SIGNAL', 'STOP-LOSS', 'TRAILING-STOP', 'PARTIAL-1', 'PARTIAL-2', None
        """
        if coin_balance <= 0 or entry_price is None:
            return False, None

        # Sécurisation des entrées
        if atr_value is None:
            return False, None

        # STOP-LOSS FIXE À config.atr_stop_multiplier × ATR
        stop_loss = entry_price - (atr_stop_multiplier * atr_value)

        # Sécurisation des entrées
        if entry_price is None or current_price is None or atr_value is None:
            return False, None  # Impossible de calculer les stops

        # Note: partial_taken flags will be accessed from pair_state in the calling function
        # They are not passed here to avoid modifying them from this pure checker.

        # Vérification des stops
        if current_price < stop_loss:
            return True, "STOP-LOSS"

        # Trailing stop logic will be handled in the main function with access to max_price.
        # We return a signal for the main function to check.
        # The main function will have its own trailing stop logic.

        # Condition de signal de vente : EMA2 > EMA1 + StochRSI > stoch_rsi_sell_exit
        # C-1: seuil optimisé 0.2 → 0.4 (bench +2% PnL, -1pp DD) — doit rester identique au backtest
        from bot_config import config as _cfg_sell
        _sell_exit = getattr(_cfg_sell, 'stoch_rsi_sell_exit', 0.4)
        ema_condition = row['ema2'] > row['ema1']
        stoch_condition = row['stoch_rsi'] > _sell_exit

        if not (ema_condition and stoch_condition):
            return False, None

        # Conditions additionnelles selon le scénario
        scenario = best_params.get('scenario', 'StochRSI')

        if scenario == 'StochRSI_TRIX' and 'TRIX_HISTO' in row:
            if row['TRIX_HISTO'] <= 0:
                return True, "SIGNAL"  # Signal amélioré avec TRIX

        # Signal de vente confirmé
        return True, "SIGNAL"

    return check_sell_signal
