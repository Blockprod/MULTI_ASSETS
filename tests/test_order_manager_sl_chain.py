"""tests/test_order_manager_sl_chain.py — TS-P2-03

Tests E2E de la chaîne P0-STOP dans _execute_buy() :
  1. SL échec × 3 → rollback safe_market_sell déclenché
  2. SL échec + rollback échec → set_emergency_halt + email CRITICAL
  3. SL succès → sl_exchange_placed=True, pas d'emergency_halt

Architecture : injecte les dépendances via _TradingDeps (pas de patching global).
"""
import os
import sys
import threading
from decimal import Decimal
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

from order_manager import _execute_buy, _TradeCtx, _TradingDeps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockRow(dict):
    """Ligne OHLCV minimale avec ATR valide pour débloquer l'achat."""
    pass


def _make_row() -> _MockRow:
    return _MockRow({
        'atr': 5.0, 'close': 100.0, 'open': 98.0, 'high': 102.0, 'low': 97.0,
        'ema1': 99.0, 'ema2': 95.0,  # requis par display_buy_signal_panel
    })


def _filled_buy_order(qty: str = '9.80') -> dict:
    """Mock d'ordre BUY FILLED renvoyé par market_buy_fn."""
    return {
        'status': 'FILLED',
        'executedQty': qty,
        'cummulativeQuoteQty': str(float(qty) * 100.0),
        'fills': [],
    }


def _make_config(**overrides):
    """Config injectable minimale pour _TradingDeps."""
    from bot_config import Config
    cfg = Config.__new__(Config)
    defaults = dict(
        atr_stop_multiplier=3.0,     # SL = entry - 3 * ATR = 100 - 15 = 85
        atr_multiplier=5.5,
        slippage_buy=0.001,
        breakeven_enabled=True,
        breakeven_trigger_pct=0.02,
        partial_threshold_1=0.05,
        partial_threshold_2=0.10,
        partial_pct_1=0.50,
        partial_pct_2=0.30,
        position_size_cushion=0.98,
        risk_per_trade=0.01,
        trailing_activation_pct=0.02,
        email_cooldown_seconds=300,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


def _make_ctx(pair_state: dict | None = None) -> _TradeCtx:
    """Contexte minimal pour atteindre la phase de placement SL dans _execute_buy().

    - current_price=100.0, ATR=5.0 → stop_loss_at_entry = 85.0 (> 0, SL tenté)
    - sizing_mode='baseline', usdc_balance=1000.0, step=0.01 → qty ≈ 9.80 (valide)
    - orders=[] → anti-double-buy non déclenché
    """
    return _TradeCtx(
        real_trading_pair='SOLUSDC',
        backtest_pair='SOLUSDC',
        time_interval='1h',
        sizing_mode='baseline',
        pair_state=pair_state if pair_state is not None else {},
        best_params={},
        ema1_period=18,
        ema2_period=58,
        scenario='StochRSI',
        coin_symbol='SOL',
        quote_currency='USDC',
        usdc_balance=1000.0,
        coin_balance_free=0.0,
        coin_balance_locked=0.0,
        coin_balance=0.0,
        current_price=100.0,
        row=_make_row(),
        orders=[],
        min_qty=0.01,
        max_qty=9000000.0,
        step_size=0.01,
        min_notional=5.0,
        min_qty_dec=Decimal('0.01'),
        max_qty_dec=Decimal('9000000'),
        step_size_dec=Decimal('0.01'),
        step_decimals=2,
    )


def _make_deps(bot_state: dict | None = None, **overrides) -> _TradingDeps:
    """Deps minimalistes : tous les guards passent, market_buy réussit, SL configurable."""
    if bot_state is None:
        bot_state = {}
    defaults = dict(
        client=MagicMock(),
        bot_state=bot_state,
        bot_state_lock=threading.RLock(),
        save_fn=MagicMock(),
        send_alert_fn=MagicMock(),
        place_sl_fn=MagicMock(return_value={'orderId': 'sl_ok_001'}),
        market_sell_fn=MagicMock(return_value={'status': 'FILLED'}),
        market_buy_fn=MagicMock(return_value=_filled_buy_order()),
        update_daily_pnl_fn=MagicMock(),
        is_loss_limit_fn=MagicMock(return_value=False),
        # gen_buy_checker_fn retourne une fonction qui retourne (True, 'signal')
        gen_buy_checker_fn=MagicMock(
            return_value=MagicMock(return_value=(True, 'test_signal'))
        ),
        gen_sell_checker_fn=MagicMock(),
        check_order_executed_fn=MagicMock(return_value=False),
        get_usdc_sells_fn=MagicMock(return_value=1000.0),
        get_sniper_entry_fn=MagicMock(return_value=100.0),   # pas d'optimisation prix
        check_partial_exits_fn=MagicMock(return_value=(False, False)),
        console=MagicMock(),
        config=_make_config(),
        is_valid_stop_loss_fn=MagicMock(return_value=True),
    )
    defaults.update(overrides)
    return _TradingDeps(**defaults)


# ---------------------------------------------------------------------------
# Tests TS-P2-03
# ---------------------------------------------------------------------------

class TestSLChain:
    """Chaîne P0-STOP : SL fail → rollback → emergency_halt."""

    def test_sl_fail_triggers_rollback(self):
        """3 échecs SL → rollback safe_market_sell déclenché (P0-STOP).

        Le rollback réussit → pas d'emergency_halt.
        """
        bot_state: dict = {}
        deps = _make_deps(
            bot_state=bot_state,
            place_sl_fn=MagicMock(side_effect=Exception('Binance: SL API unavailable')),
        )
        ctx = _make_ctx()

        with patch('order_manager.display_buy_signal_panel'):
            _execute_buy(ctx, deps)

        # Rollback market-sell déclenché
        deps.market_sell_fn.assert_called_once()
        call_kw = deps.market_sell_fn.call_args[1]
        assert call_kw.get('symbol') == 'SOLUSDC'

        # Rollback réussi → pas d'emergency_halt
        assert not bot_state.get('emergency_halt')

    def test_sl_fail_rollback_fail_triggers_emergency_halt(self):
        """SL échec + rollback échec → emergency_halt=True + email [EMERGENCY HALT] (P0-STOP double fail)."""
        bot_state: dict = {}
        deps = _make_deps(
            bot_state=bot_state,
            place_sl_fn=MagicMock(side_effect=Exception('SL API error')),
            market_sell_fn=MagicMock(side_effect=Exception('Rollback also failed')),
        )
        ctx = _make_ctx()

        with patch('order_manager.display_buy_signal_panel'):
            _execute_buy(ctx, deps)

        # emergency_halt posé dans bot_state par set_emergency_halt()
        assert bot_state.get('emergency_halt') is True

        # Email CRITICAL avec sujet EMERGENCY HALT envoyé
        all_calls = deps.send_alert_fn.call_args_list
        subjects = [c[1].get('subject', '') for c in all_calls]
        assert any('EMERGENCY' in s for s in subjects), (
            f"Aucun email EMERGENCY HALT parmi : {subjects}"
        )

    def test_sl_success_no_emergency_halt(self):
        """SL placé avec succès → sl_exchange_placed=True, pas de rollback ni emergency_halt (P0-01)."""
        bot_state: dict = {}
        deps = _make_deps(
            bot_state=bot_state,
            place_sl_fn=MagicMock(return_value={'orderId': 'sl_real_001'}),
        )
        ctx = _make_ctx()

        with patch('order_manager.display_buy_signal_panel'):
            _execute_buy(ctx, deps)

        # SL confirmé dans le pair_state
        assert ctx.pair_state.get('sl_exchange_placed') is True
        assert ctx.pair_state.get('sl_order_id') == 'sl_real_001'

        # Pas de rollback ni d'emergency_halt
        deps.market_sell_fn.assert_not_called()
        assert not bot_state.get('emergency_halt')
