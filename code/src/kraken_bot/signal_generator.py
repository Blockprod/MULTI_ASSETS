"""
signal_generator.py — Buy/Sell signal condition checkers.

Extracted from MULTI_SYMBOLS.py (P3-SRP) to isolate signal generation logic
from the trading execution engine.

Functions are pure closures that return checker functions.  No mutable global
state — all behaviour is locked-in at closure creation time.
"""

from __future__ import annotations

import logging
import os
import pickle
import pandas as pd
from typing import Any, Callable, Dict, Optional, Tuple

from strategy_policy import entry_levels, evaluate_buy_signal, evaluate_signal_exit

logger = logging.getLogger(__name__)

# ── ML-08: Lazy model cache (shadow mode — never blocks signals) ─
_ML08_CACHE: Dict[str, Any] = {}

def _get_ml08_model() -> Optional[Dict[str, Any]]:
    """Load ML confidence model from cache/ if present; return None if missing."""
    if "loaded" not in _ML08_CACHE:
        _ML08_CACHE["loaded"] = True  # mark as attempted (even on failure)
        _model_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "cache", "ml_confidence_model.pkl"
        )
        try:
            with open(_model_path, "rb") as _fh:
                _ML08_CACHE["payload"] = pickle.load(_fh)
            logger.info("[ML-08] Confidence model loaded from %s", _model_path)
        except FileNotFoundError:
            pass  # model not yet trained — expected
        except Exception as _e:
            logger.warning("[ML-08] Model load failed: %s", _e)
    return _ML08_CACHE.get("payload")

def generate_buy_condition_checker(
    best_params: Dict[str, Any],
    stoch_buy_min: Optional[float] = None,
    stoch_buy_max: Optional[float] = None,
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
        from bot_config import config as _cfg_shared
        signal_ok, signal_reason = evaluate_buy_signal(
            row,
            usdc_balance,
            best_params,
            _cfg_shared,
            stoch_buy_min=stoch_buy_min,
            stoch_buy_max=stoch_buy_max,
        )
        if not signal_ok:
            return signal_ok, signal_reason

        # ML-08 shadow: log ML confidence probability — NEVER blocks signal
        try:
            _m08 = _get_ml08_model()
            if _m08 is not None:
                _close = float(row.get('close', 0.0) or 0.0)
                _atr = float(row.get('atr', 0.0) or 0.0)
                _ema1_v = float(row.get('ema1', 1.0) or 1.0)
                _ema2_v = float(row.get('ema2', 1.0) or 1.0)
                _atr_med = float(row.get('atr_median_30d', 0.0) or 0.0)
                _f = {
                    'atr_pct': _atr / _close if _close > 0 else 0.0,
                    'stop_dist_pct': _atr * float(best_params.get('atr_stop_multiplier', 2.0)) / _close if _close > 0 else 0.0,
                    'ema_ratio': _ema1_v / max(_ema2_v, 1e-9),
                    'equity_before': 0.0,
                    'scenario': str(best_params.get('scenario', 'StochRSI')),
                    'timeframe': str(best_params.get('timeframe', '1h')),
                }
                _fcols = _m08.get('feature_cols', [])
                _X = pd.DataFrame([{c: _f.get(c, 0.0) for c in _fcols}])
                _prob = float(_m08['model'].predict_proba(_X)[0][1])
                _threshold = float(_m08.get('threshold', 0.55))
                logger.debug(
                    "[ML-08 SHADOW] BUY confidence P(profitable)=%.3f (thr=%.2f) %s",
                    _prob, _threshold, "✓" if _prob >= _threshold else "↓",
                )
        except Exception as _ml08_err:
            logger.debug("[ML-08 shadow] prediction skipped: %s", _ml08_err)

        # Toutes les conditions sont remplies
        return signal_ok, signal_reason

    return check_buy_signal


def generate_sell_condition_checker(
    best_params: Dict[str, Any],
    config: Any = None,
    stoch_sell_exit: Optional[float] = None,
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
    # Resolve strategy config once for the closure.
    from bot_config import config as _global_config
    _strategy_config = config or _global_config

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

        levels = entry_levels(
            float(entry_price),
            float(atr_value),
            row.get('atr_median_30d'),
            atr_stop_multiplier=getattr(_strategy_config, 'atr_stop_multiplier', 5.5),
            atr_multiplier=getattr(_strategy_config, 'atr_multiplier', 3.0),
        )
        stop_loss = levels.stop_loss

        # Sécurisation des entrées
        if entry_price is None or current_price is None or atr_value is None:
            return False, None  # Impossible de calculer les stops

        # Note: partial_taken flags will be accessed from pair_state in the calling function
        # They are not passed here to avoid modifying them from this pure checker.

        # Vérification des stops
        if current_price <= stop_loss:
            return True, "STOP-LOSS"

        # Trailing stop logic will be handled in the main function with access to max_price.
        # We return a signal for the main function to check.
        # The main function will have its own trailing stop logic.

        return evaluate_signal_exit(
            row,
            best_params,
            _strategy_config,
            stoch_sell_exit=stoch_sell_exit,
        )

    return check_sell_signal
