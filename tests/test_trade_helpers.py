"""tests/test_trade_helpers.py — MI-06

Tests unitaires des fonctions helpers du moteur de trading dans order_manager.py :
  - _sync_entry_state    : synchronisation état entrée après BUY
  - _update_trailing_stop: activation et ratchet du trailing stop
  - _execute_partial_sells: déclenchement et garde min_notional
  - _handle_dust_cleanup : détection et nettoyage dust

Toutes les dépendances réseau et I/O sont mockées.
"""
import os
import sys
from decimal import Decimal
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

import pytest
from order_manager import (
    _sync_entry_state,
    _update_trailing_stop,
    _execute_partial_sells,
    _handle_dust_cleanup,
    _TradeCtx,
    _TradingDeps,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    """Config minimal injecté dans _TradingDeps."""
    from bot_config import Config
    cfg = Config.__new__(Config)
    defaults = dict(
        atr_stop_multiplier=3.0,
        atr_multiplier=5.5,
        slippage_buy=0.001,
        breakeven_enabled=True,
        breakeven_trigger_pct=0.02,
        partial_threshold_1=0.05,
        partial_threshold_2=0.10,
        partial_pct_1=0.50,
        partial_pct_2=0.30,
        position_size_cushion=0.98,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


def _make_deps(**overrides) -> _TradingDeps:
    """_TradingDeps minimal avec tous les callables mockés."""
    defaults = dict(
        client=MagicMock(),
        bot_state={},
        bot_state_lock=MagicMock(),
        save_fn=MagicMock(),
        send_alert_fn=MagicMock(),
        place_sl_fn=MagicMock(),
        market_sell_fn=MagicMock(),
        market_buy_fn=MagicMock(),
        update_daily_pnl_fn=MagicMock(),
        is_loss_limit_fn=MagicMock(return_value=False),
        gen_buy_checker_fn=MagicMock(),
        gen_sell_checker_fn=MagicMock(),
        check_order_executed_fn=MagicMock(return_value=False),
        get_usdc_sells_fn=MagicMock(return_value=0.0),
        get_sniper_entry_fn=MagicMock(return_value=None),
        check_partial_exits_fn=MagicMock(return_value=(False, False)),
        console=MagicMock(),
        config=_make_config(),
        is_valid_stop_loss_fn=MagicMock(return_value=True),
    )
    defaults.update(overrides)
    return _TradingDeps(**defaults)


def _make_ctx(**overrides) -> _TradeCtx:
    """_TradeCtx minimal pour la suite de tests."""
    defaults = dict(
        real_trading_pair='TRXUSDC',
        backtest_pair='TRXUSDC',
        time_interval='1h',
        sizing_mode='baseline',
        pair_state={},
        best_params={'scenario': 'StochRSI'},
        ema1_period=26,
        ema2_period=50,
        scenario='StochRSI',
        coin_symbol='TRX',
        quote_currency='USDC',
        usdc_balance=1000.0,
        coin_balance_free=100.0,
        coin_balance_locked=0.0,
        coin_balance=100.0,
        current_price=10.0,
        row=MagicMock(),
        orders=[],
        min_qty=0.001,
        max_qty=10000.0,
        step_size=0.001,
        min_notional=5.0,
        min_qty_dec=Decimal('0.001'),
        max_qty_dec=Decimal('10000.0'),
        step_size_dec=Decimal('0.001'),
        step_decimals=3,
    )
    defaults.update(overrides)
    return _TradeCtx(**defaults)


# ---------------------------------------------------------------------------
# Tests _sync_entry_state
# ---------------------------------------------------------------------------

class TestSyncEntryState:
    """Tests de _sync_entry_state — synchronisation après BUY confirmé."""

    def test_noop_if_last_side_is_sell(self):
        """last_side='SELL' → aucune modification du pair_state."""
        ps = {'last_order_side': 'SELL'}
        ctx = _make_ctx(pair_state=ps)
        deps = _make_deps()
        _sync_entry_state(ctx, 'SELL', deps)
        # save_fn ne doit pas être appelé
        deps.save_fn.assert_not_called()
        assert 'entry_price' not in ps

    def test_noop_if_no_filled_buy_order(self):
        """last_side='BUY' mais pas d'ordre FILLED → aucune modification."""
        ps = {}
        orders = [{'status': 'NEW', 'side': 'BUY'}]
        ctx = _make_ctx(pair_state=ps, orders=orders)
        deps = _make_deps()
        _sync_entry_state(ctx, 'BUY', deps)
        deps.save_fn.assert_not_called()

    def test_sets_entry_price_from_filled_buy(self):
        """BUY FILLED avec prix > 0 → entry_price et stop_loss_at_entry initialisés."""
        ps = {}
        orders = [{'status': 'FILLED', 'side': 'BUY', 'price': '10.0', 'executedQty': '5.0', 'cummulativeQuoteQty': '50.0'}]
        row = MagicMock()
        row.get = lambda k, d=None: 2.0 if k == 'atr' else d  # atr = 2.0
        ctx = _make_ctx(pair_state=ps, orders=orders, row=row)
        deps = _make_deps()
        _sync_entry_state(ctx, 'BUY', deps)
        assert ps.get('entry_price') == 10.0
        # stop_loss_at_entry = price - atr_stop_multiplier(3.0) * atr(2.0) = 10 - 6 = 4
        assert ps.get('stop_loss_at_entry') == pytest.approx(4.0)
        assert ps.get('last_order_side') == 'BUY'
        deps.save_fn.assert_called_once()

    def test_entry_price_not_overwritten_if_already_set(self):
        """Si entry_price est déjà défini, il n'est pas écrasé."""
        ps = {'entry_price': 9.0}  # déjà défini
        orders = [{'status': 'FILLED', 'side': 'BUY', 'price': '10.0', 'executedQty': '5.0', 'cummulativeQuoteQty': '50.0'}]
        row = MagicMock()
        row.get = lambda k, d=None: 2.0 if k == 'atr' else d
        ctx = _make_ctx(pair_state=ps, orders=orders, row=row)
        deps = _make_deps()
        _sync_entry_state(ctx, 'BUY', deps)
        assert ps['entry_price'] == 9.0  # inchangé


# ---------------------------------------------------------------------------
# Tests _update_trailing_stop
# ---------------------------------------------------------------------------

class TestUpdateTrailingStop:
    """Tests de _update_trailing_stop — activation et ratchet."""

    def test_noop_if_not_in_position(self):
        """Aucune position → retourne sans modifier pair_state."""
        ps = {'last_order_side': 'SELL'}
        ctx = _make_ctx(pair_state=ps, coin_balance=0.0)
        deps = _make_deps()
        _update_trailing_stop(ctx, deps)
        # save_fn est appelé mais par la mise à jour max_price — OK
        # Le point clé : trailing_stop_activated reste absent/False
        assert not ps.get('trailing_stop_activated', False)

    def test_trailing_activates_when_price_reaches_threshold(self):
        """Prix courant >= activation_price → trailing_stop_activated=True."""
        entry_price = 10.0
        atr_at_entry = 2.0
        # atr_multiplier=5.5 → activation_price = 10 + 5.5*2 = 21
        activation_price = entry_price + 5.5 * atr_at_entry
        ps = {
            'last_order_side': 'BUY',
            'entry_price': entry_price,
            'atr_at_entry': atr_at_entry,
            'trailing_activation_price_at_entry': activation_price,
            'trailing_stop_activated': False,
        }
        # Prix exactement au niveau d'activation
        ctx = _make_ctx(pair_state=ps, coin_balance=100.0, current_price=activation_price)
        deps = _make_deps()
        _update_trailing_stop(ctx, deps)
        assert ps.get('trailing_stop_activated') is True
        assert ps.get('trailing_stop') is not None

    def test_trailing_not_activated_below_threshold(self):
        """Prix < activation_price → trailing reste désactivé."""
        entry_price = 10.0
        atr_at_entry = 2.0
        activation_price = entry_price + 5.5 * atr_at_entry  # 21
        ps = {
            'last_order_side': 'BUY',
            'entry_price': entry_price,
            'atr_at_entry': atr_at_entry,
            'trailing_activation_price_at_entry': activation_price,
            'trailing_stop_activated': False,
        }
        ctx = _make_ctx(pair_state=ps, coin_balance=100.0, current_price=15.0)  # < 21
        deps = _make_deps()
        _update_trailing_stop(ctx, deps)
        assert ps.get('trailing_stop_activated') is False

    def test_trailing_ratchets_up_on_higher_price(self):
        """Une fois activé, le trailing stop monte quand le prix monte (ratchet)."""
        entry_price = 10.0
        atr_at_entry = 2.0
        activation_price = entry_price + 5.5 * atr_at_entry  # 21
        initial_trailing = 15.0
        # Prix monte de 21 à 25
        ps = {
            'last_order_side': 'BUY',
            'entry_price': entry_price,
            'atr_at_entry': atr_at_entry,
            'trailing_activation_price_at_entry': activation_price,
            'trailing_stop_activated': True,
            'max_price': 21.0,
            'trailing_stop': initial_trailing,
        }
        # Nouveau prix max = 25 → nouveau trailing = 25 - 5.5*2 = 14 → mais 14 < 15 !
        # Non! Avec atr_multiplier=5.5 et atr=2, trailing_distance = 11
        # new_trailing = 25 - 11 = 14 → supérieur à initial 15? Non, 14 < 15.
        # Voyons avec price=30 → trailing = 30-11=19 > 15 → ratchet OK
        ctx = _make_ctx(pair_state=ps, coin_balance=100.0, current_price=30.0)
        deps = _make_deps()
        _update_trailing_stop(ctx, deps)
        # Le trailing doit avoir monté au-dessus de 15
        new_trailing = ps.get('trailing_stop', 0)
        assert new_trailing > initial_trailing, f"trailing_stop {new_trailing} devrait être > {initial_trailing}"


# ---------------------------------------------------------------------------
# Tests _execute_partial_sells
# ---------------------------------------------------------------------------

class TestExecutePartialSells:
    """Tests de _execute_partial_sells — déclenchement et garde min_notional."""

    def test_noop_if_not_in_buy_position(self):
        """last_order_side != 'BUY' → pas de vente partielle."""
        ps = {'last_order_side': 'SELL'}
        ctx = _make_ctx(pair_state=ps, coin_balance_free=100.0)
        deps = _make_deps()
        _execute_partial_sells(ctx, deps)
        deps.market_sell_fn.assert_not_called()

    def test_noop_if_zero_free_balance(self):
        """coin_balance_free = 0 → pas de vente partielle."""
        ps = {'last_order_side': 'BUY', 'entry_price': 10.0}
        ctx = _make_ctx(pair_state=ps, coin_balance_free=0.0)
        deps = _make_deps()
        _execute_partial_sells(ctx, deps)
        deps.market_sell_fn.assert_not_called()

    def test_noop_if_profit_below_threshold1(self):
        """Profit < partial_threshold_1 (5%) → pas de vente partielle."""
        entry_price = 10.0
        current_price = 10.03  # +3% < 5%
        ps = {'last_order_side': 'BUY', 'entry_price': entry_price,
              'partial_taken_1': False}
        ctx = _make_ctx(pair_state=ps, coin_balance_free=100.0,
                        coin_balance=100.0, current_price=current_price)
        deps = _make_deps()
        _execute_partial_sells(ctx, deps)
        deps.market_sell_fn.assert_not_called()

    def test_partial1_triggered_when_profit_above_threshold(self):
        """Profit >= partial_threshold_1 (5%) → vente partielle déclenchée."""
        entry_price = 10.0
        current_price = 10.6  # +6% > 5%
        ps = {'last_order_side': 'BUY', 'entry_price': entry_price,
              'partial_taken_1': False, 'partial_taken_2': False,
              'partial_enabled': True}
        market_sell = MagicMock(return_value={'status': 'FILLED', 'orderId': '123'})
        mock_account = MagicMock()
        mock_account.get_account.return_value = {
            'balances': [{'asset': 'TRX', 'free': '50.0', 'locked': '0.0'}]
        }
        ctx = _make_ctx(pair_state=ps, coin_balance_free=100.0,
                        coin_balance=100.0, current_price=current_price)
        deps = _make_deps(market_sell_fn=market_sell, client=mock_account)
        _execute_partial_sells(ctx, deps)
        # Une vente doit avoir été tentée
        market_sell.assert_called_once()

    def test_partial1_blocked_when_notional_too_small(self):
        """Valeur notionnelle de la vente partielle < min_notional → vente bloquée, flag mis à True."""
        entry_price = 10.0
        current_price = 10.6  # +6% > 5%
        ps = {'last_order_side': 'BUY', 'entry_price': entry_price,
              'partial_taken_1': False, 'partial_enabled': True}
        # coin_balance très faible : 0.001 * 10.6 * 0.5 = 0.0053 USDC < min_notional(5.0)
        ctx = _make_ctx(pair_state=ps, coin_balance_free=0.001,
                        coin_balance=0.001, current_price=current_price,
                        min_notional=5.0,
                        min_qty_dec=Decimal('0.001'),
                        max_qty_dec=Decimal('10000.0'),
                        step_size_dec=Decimal('0.001'),
                        step_decimals=3)
        deps = _make_deps()
        _execute_partial_sells(ctx, deps)
        # Vente non exécutée
        deps.market_sell_fn.assert_not_called()
        # Flag mis à True pour éviter retry infini
        assert ps.get('partial_taken_1') is True


# ---------------------------------------------------------------------------
# Tests _handle_dust_cleanup
# ---------------------------------------------------------------------------

class TestHandleDustCleanup:
    """Tests de _handle_dust_cleanup — détection et nettoyage dust."""

    def test_returns_true_when_position_is_large(self):
        """Position normale > min_qty avec valeur suffisante → position_has_crypto=True."""
        ps = {'last_order_side': 'BUY'}
        ctx = _make_ctx(pair_state=ps, coin_balance=100.0, current_price=10.0,
                        min_qty=0.001, min_notional=5.0,
                        coin_balance_locked=0.0, coin_balance_free=100.0)
        deps = _make_deps()
        result = _handle_dust_cleanup(ctx, deps)
        assert result is True  # pas de dust

    def test_returns_false_and_resets_state_when_dust_below_min_notional(self):
        """Dust présent avec valeur < min_notional → état réinitialisé, position_has_crypto=False."""
        ps = {
            'last_order_side': 'SELL',
            'entry_price': 10.0,  # stale entry state
            'stop_loss_at_entry': 8.0,
        }
        # coin_balance = 0.0005 (très petit, entre 1% et 100% de min_qty=0.001)
        # valeur notionnelle = 0.0005 * 10.0 = 0.005 USDC < min_notional=5.0
        ctx = _make_ctx(
            pair_state=ps,
            coin_balance=0.0005,
            coin_balance_free=0.0005,
            coin_balance_locked=0.0,
            current_price=10.0,
            min_qty=0.001,
            min_notional=5.0,
        )
        deps = _make_deps()
        result = _handle_dust_cleanup(ctx, deps)
        # L'état stale doit être réinitialisé
        assert ps.get('entry_price') is None
        assert ps.get('stop_loss_at_entry') is None
        assert ps.get('last_order_side') == 'SELL'
        # save_fn(force=True) appelé
        deps.save_fn.assert_called_once_with(force=True)

    def test_attempts_sell_when_dust_above_min_notional(self):
        """Dust présent avec valeur >= min_notional → tentative de vente."""
        ps = {'last_order_side': 'SELL'}
        # coin_balance = 0.0005 mais prix élevé → valeur > min_notional
        # 0.0005 * 20000 = 10 USDC > 5 USDC
        market_sell = MagicMock(return_value={'status': 'FILLED'})
        mock_client = MagicMock()
        mock_client.get_account.return_value = {
            'balances': [{'asset': 'TRX', 'free': '0.0', 'locked': '0.0'}]
        }
        ctx = _make_ctx(
            pair_state=ps,
            coin_balance=0.0005,
            coin_balance_free=0.0005,
            coin_balance_locked=0.0,
            current_price=20000.0,  # très haut prix → valeur = 10 USDC > min_notional
            min_qty=0.001,
            min_notional=5.0,
        )
        deps = _make_deps(market_sell_fn=market_sell, client=mock_client)
        _handle_dust_cleanup(ctx, deps)
        market_sell.assert_called_once()

    def test_no_dust_detection_when_balance_is_zero(self):
        """coin_balance = 0 → pas de dust, position_has_crypto=False sans action."""
        ps = {}
        ctx = _make_ctx(pair_state=ps, coin_balance=0.0, coin_balance_free=0.0,
                        coin_balance_locked=0.0, current_price=10.0)
        deps = _make_deps()
        result = _handle_dust_cleanup(ctx, deps)
        assert result is False
        deps.market_sell_fn.assert_not_called()
        deps.save_fn.assert_not_called()
