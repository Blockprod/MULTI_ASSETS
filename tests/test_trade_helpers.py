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
from typing import Any, cast
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
from trade_helpers import check_partial_exits_from_history


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
    defaults: dict[str, Any] = dict(
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
    defaults: dict[str, Any] = dict(
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

    def test_adaptive_atr_multiplier_applied(self):
        """ML-03: atr_median_30d présent dans row → multiplicateur ATR adaptatif appliqué."""
        ps = {}
        orders = [{'status': 'FILLED', 'side': 'BUY', 'price': '100.0',
                   'executedQty': '10.0', 'cummulativeQuoteQty': '1000.0'}]
        row = MagicMock()
        # atr=2.0, atr_median_30d=1.0 → vol_ratio=2, scale=sqrt(2)≈1.414
        # atr_stop_multiplier = 3.0 * 1.414 = 4.24, clampé dans [1.5, 5.0]
        row.get = lambda k, d=None: {'atr': 2.0, 'atr_median_30d': 1.0}.get(k, d)
        ctx = _make_ctx(pair_state=ps, orders=orders, row=row, current_price=100.0)
        deps = _make_deps()
        _sync_entry_state(ctx, 'BUY', deps)
        # Avec adaptive: stop = 100 - 4.24*2 ≈ 91.5  (< stop classique 94.0)
        std_stop = 100.0 - 3.0 * 2.0  # 94.0 sans adaptatif
        sl_val = ps.get('stop_loss_at_entry')
        assert sl_val is not None
        assert sl_val < std_stop  # adaptatif donne un stop plus large


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

    def test_recalcs_missing_trailing_activation_price(self):
        """trailing_activation_price_at_entry=None + entry+atr définis → recalculé."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'atr_at_entry': 2.0,
            'trailing_activation_price_at_entry': None,  # manquant
            'trailing_stop_activated': False,
            'max_price': 100.0,
        }
        ctx = _make_ctx(pair_state=ps, coin_balance=10.0, current_price=99.0)
        deps = _make_deps()
        _update_trailing_stop(ctx, deps)
        # Doit être recalculé : 100 + 5.5*2 = 111
        assert ps['trailing_activation_price_at_entry'] == pytest.approx(111.0)
        deps.save_fn.assert_called()

    def test_trailing_activation_email_failure_silent(self):
        """Email d'activation trailing échoue silencieusement (chemin except)."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'atr_at_entry': 2.0,
            'trailing_activation_price_at_entry': 108.0,
            'trailing_stop_activated': False,
            'max_price': 112.0,
        }
        ctx = _make_ctx(pair_state=ps, coin_balance=10.0, current_price=112.0)
        deps = _make_deps()
        deps.send_alert_fn.side_effect = Exception("SMTP fail")
        _update_trailing_stop(ctx, deps)  # ne doit pas crasher
        assert ps.get('trailing_stop_activated') is True

    def test_breakeven_stop_triggered(self):
        """Profit >= breakeven_trigger_pct → stop_loss remonté, breakeven_triggered=True, stop_loss_at_entry inchangé."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'atr_at_entry': 2.0,
            'trailing_activation_price_at_entry': 111.0,
            'trailing_stop_activated': False,
            'max_price': 103.0,
            'stop_loss_at_entry': 94.0,
            'stop_loss': 94.0,
            'breakeven_triggered': False,
        }
        # current_price=103 : profit 3% >= trigger 2%
        ctx = _make_ctx(pair_state=ps, coin_balance=10.0, current_price=103.0)
        deps = _make_deps()
        _update_trailing_stop(ctx, deps)
        assert ps.get('breakeven_triggered') is True
        # stop_loss_at_entry remains immutable (original ATR SL)
        assert ps.get('stop_loss_at_entry') == 94.0
        # stop_loss (active SL) is raised to entry + slippage
        sl_val = ps.get('stop_loss')
        assert sl_val is not None
        assert sl_val > 94.0  # remonté au prix d'entrée

    def test_breakeven_email_failure_silent(self):
        """Email d'activation breakeven échoue silencieusement."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'atr_at_entry': 2.0,
            'trailing_activation_price_at_entry': 111.0,
            'trailing_stop_activated': False,
            'max_price': 103.0,
            'stop_loss_at_entry': 94.0,
            'breakeven_triggered': False,
        }
        ctx = _make_ctx(pair_state=ps, coin_balance=10.0, current_price=103.0)
        deps = _make_deps()
        deps.send_alert_fn.side_effect = Exception("SMTP fail")
        _update_trailing_stop(ctx, deps)  # ne doit pas crasher
        assert ps.get('breakeven_triggered') is True


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

    def test_cancel_exchange_sl_called_on_partial(self):
        """sl_order_id présent → _cancel_exchange_sl appelé (succès), sl_order_id remis à None."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'partial_taken_1': False,
            'partial_taken_2': False,
            'partial_enabled': True,
            'sl_order_id': 'SL_ABC',
            'sl_exchange_placed': True,
            'stop_loss_at_entry': 94.0,
        }
        ctx = _make_ctx(pair_state=ps, coin_balance_free=10.0, coin_balance=10.0,
                        current_price=106.0)  # +6% >= partial_threshold_1(5%)
        deps = _make_deps()
        mock_client = cast(MagicMock, deps.client)
        mock_client.cancel_order.return_value = {'orderId': 'SL_ABC', 'status': 'CANCELED'}
        mock_client.get_account.return_value = {
            'balances': [{'asset': 'TRX', 'free': '10.0', 'locked': '0'}]
        }
        deps.market_sell_fn.return_value = {'status': 'FILLED', 'executedQty': '5.0'}
        deps.place_sl_fn.return_value = {'orderId': 'SL_NEW'}
        _execute_partial_sells(ctx, deps)
        mock_client.cancel_order.assert_called_once_with(symbol='TRXUSDC', orderId='SL_ABC')
        # Après la vente partielle, un nouveau SL est replacé → sl_order_id = 'SL_NEW'
        assert ps.get('sl_order_id') == 'SL_NEW'
        assert ps.get('sl_exchange_placed') is True

    def test_cancel_exchange_sl_exception_sends_alert(self):
        """cancel_order lève une exception → email d'alerte envoyé, sl_order_id conservé."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'partial_taken_1': False,
            'partial_enabled': True,
            'sl_order_id': 'SL_FAIL',
            'sl_exchange_placed': True,
        }
        ctx = _make_ctx(pair_state=ps, coin_balance_free=10.0, coin_balance=10.0,
                        current_price=106.0)
        deps = _make_deps()
        cast(MagicMock, deps.client).cancel_order.side_effect = Exception("API timeout")
        deps.market_sell_fn.return_value = {'status': 'FILLED', 'executedQty': '5.0'}
        deps.place_sl_fn.return_value = {'orderId': 'SL_NEW2'}
        _execute_partial_sells(ctx, deps)
        deps.send_alert_fn.assert_called()  # email d'alerte sur l'échec d'annulation
        assert ps.get('sl_order_id') == 'SL_FAIL'  # conservé (cancel a échoué)

    def test_api_desync_corrects_partial_flags(self):
        """check_partial_exits_fn diffère de l'état local → flags resynchronisés."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'partial_taken_1': False,  # local : non pris
            'partial_taken_2': False,
            'partial_enabled': True,
        }
        # profit < threshold_1 pour ne pas trigger de vente
        ctx = _make_ctx(pair_state=ps, coin_balance_free=10.0, coin_balance=10.0,
                        current_price=99.0)
        deps = _make_deps()
        # API dit que partial_1 a déjà été pris (désynchronisation)
        deps.check_partial_exits_fn.return_value = (True, False)
        _execute_partial_sells(ctx, deps)
        assert ps.get('partial_taken_1') is True  # corrigé vers source de vérité API
        deps.save_fn.assert_called()

    def test_partial_2_triggered_with_sl_cancel(self):
        """profit >= partial_threshold_2 (10%) + partial_taken_1=True → PARTIAL-2 déclenché."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'partial_taken_1': True,  # déjà pris
            'partial_taken_2': False,
            'partial_enabled': True,
            'sl_order_id': 'SL_P2',
            'sl_exchange_placed': True,
            'stop_loss_at_entry': 94.0,
        }
        ctx = _make_ctx(pair_state=ps, coin_balance_free=5.0, coin_balance=5.0,
                        current_price=111.0)  # +11% >= partial_threshold_2(10%)
        deps = _make_deps()
        # API confirme que partial_1 est bien pris (pas de désync)
        deps.check_partial_exits_fn.return_value = (True, False)
        mock_client2 = cast(MagicMock, deps.client)
        mock_client2.cancel_order.return_value = {'orderId': 'SL_P2', 'status': 'CANCELED'}
        mock_client2.get_account.return_value = {
            'balances': [{'asset': 'TRX', 'free': '3.5', 'locked': '0'}]
        }
        deps.market_sell_fn.return_value = {'status': 'FILLED', 'executedQty': '1.5'}
        deps.place_sl_fn.return_value = {'orderId': 'SL_P2_NEW'}
        _execute_partial_sells(ctx, deps)
        deps.market_sell_fn.assert_called_once()
        assert ps.get('partial_taken_2') is True

    def test_blocked_sell_email_exception(self):
        """Email alerte vente bloquée (notional trop faible) lève → except catchée (L402-403)."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'partial_taken_1': False,
            'partial_enabled': True,
        }
        ctx = _make_ctx(pair_state=ps, coin_balance_free=0.001, coin_balance=0.001,
                        current_price=106.0, min_notional=5.0)
        deps = _make_deps()
        deps.check_partial_exits_fn.return_value = (False, False)
        deps.send_alert_fn.side_effect = Exception("SMTP blocked")
        _execute_partial_sells(ctx, deps)  # ne doit pas crasher
        assert ps.get('partial_taken_1') is True  # flag mis à True malgré l'échec email

    def test_partial_sell_email_throws(self):
        """Email post-vente-partielle lève → except catchée silencieusement (L444-445)."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'partial_taken_1': False,
            'partial_taken_2': False,
            'partial_enabled': True,
            'stop_loss_at_entry': 94.0,
        }
        ctx = _make_ctx(pair_state=ps, coin_balance_free=10.0, coin_balance=10.0,
                        current_price=106.0)
        deps = _make_deps()
        deps.check_partial_exits_fn.return_value = (False, False)
        cast(MagicMock, deps.client).get_account.return_value = {
            'balances': [{'asset': 'TRX', 'free': '5.0', 'locked': '0'}]
        }
        deps.market_sell_fn.return_value = {'status': 'FILLED', 'executedQty': '5.0'}
        deps.place_sl_fn.return_value = {'orderId': 'SL_EMAIL_THROW'}
        deps.send_alert_fn.side_effect = Exception("SMTP fail")
        _execute_partial_sells(ctx, deps)  # ne doit pas crasher
        assert ps.get('partial_taken_1') is True

    def test_partial_sell_invalid_stop_loss_skips_email(self):
        """is_valid_stop_loss_fn=False → email non envoyé, warning loggé (L446-447)."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'partial_taken_1': False,
            'partial_taken_2': False,
            'partial_enabled': True,
            'stop_loss_at_entry': 94.0,
        }
        ctx = _make_ctx(pair_state=ps, coin_balance_free=10.0, coin_balance=10.0,
                        current_price=106.0)
        deps = _make_deps(is_valid_stop_loss_fn=MagicMock(return_value=False))
        deps.check_partial_exits_fn.return_value = (False, False)
        cast(MagicMock, deps.client).get_account.return_value = {
            'balances': [{'asset': 'TRX', 'free': '5.0', 'locked': '0'}]
        }
        deps.market_sell_fn.return_value = {'status': 'FILLED', 'executedQty': '5.0'}
        deps.place_sl_fn.return_value = {'orderId': 'SL_VALID_SKIP'}
        _execute_partial_sells(ctx, deps)
        assert ps.get('partial_taken_1') is True
        deps.send_alert_fn.assert_not_called()

    def test_partial_blocked_min_notional_restores_sl(self):
        """P0: SL cancelled before partial → min_notional blocks sell → SL MUST be re-placed."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'partial_taken_1': True,
            'partial_taken_2': False,
            'partial_enabled': True,
            'sl_order_id': 'SL_ORIG',
            'sl_exchange_placed': True,
            'stop_loss_at_entry': 94.0,
        }
        # coin_balance=2.0, price=111 → qty_to_sell=2.0*0.3=0.6 → rounds to 0 with step=1.0
        # 0 < min_qty(1.0) → blocked. But coin_balance=2.0 > min_qty → SL can be restored
        mock_client = MagicMock()
        mock_client.cancel_order.return_value = {'orderId': 'SL_ORIG', 'status': 'CANCELED'}
        mock_client.get_account.return_value = {
            'balances': [{'asset': 'TRX', 'free': '2.0', 'locked': '0'}]
        }
        ctx = _make_ctx(
            pair_state=ps,
            coin_balance_free=2.0, coin_balance=2.0,
            current_price=111.0,  # +11% >= partial_threshold_2(10%)
            min_notional=10.0,
            min_qty_dec=Decimal('1.0'),  # step rounds 0.6 to 0 < min_qty
            max_qty_dec=Decimal('100000.0'),
            step_size_dec=Decimal('1.0'),
            step_decimals=0,
        )
        deps = _make_deps(client=mock_client)
        deps.check_partial_exits_fn.return_value = (True, False)
        deps.place_sl_fn.return_value = {'orderId': 'SL_RESTORED'}
        _execute_partial_sells(ctx, deps)
        # Partial-2 blocked → flag set
        assert ps.get('partial_taken_2') is True
        # SL MUST have been restored
        deps.place_sl_fn.assert_called_once()
        assert ps.get('sl_order_id') == 'SL_RESTORED'
        assert ps.get('sl_exchange_placed') is True

    def test_partial_sell_not_filled_restores_sl(self):
        """SL cancelled → sell order not FILLED → SL MUST be re-placed."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'partial_taken_1': False,
            'partial_taken_2': False,
            'partial_enabled': True,
            'sl_order_id': 'SL_B4',
            'sl_exchange_placed': True,
            'stop_loss_at_entry': 94.0,
        }
        mock_client = MagicMock()
        mock_client.cancel_order.return_value = {'orderId': 'SL_B4', 'status': 'CANCELED'}
        mock_client.get_account.return_value = {
            'balances': [{'asset': 'TRX', 'free': '10.0', 'locked': '0'}]
        }
        ctx = _make_ctx(pair_state=ps, coin_balance_free=10.0, coin_balance=10.0,
                        current_price=106.0)
        deps = _make_deps(client=mock_client)
        deps.check_partial_exits_fn.return_value = (False, False)
        deps.market_sell_fn.return_value = {'status': 'EXPIRED'}  # NOT FILLED
        deps.place_sl_fn.return_value = {'orderId': 'SL_AFTER_FAIL'}
        _execute_partial_sells(ctx, deps)
        # SL must be restored after failed sell
        assert ps.get('sl_order_id') == 'SL_AFTER_FAIL'
        assert ps.get('sl_exchange_placed') is True

    def test_partial_sell_exception_restores_sl(self):
        """SL cancelled → sell raises exception → SL MUST be re-placed."""
        ps = {
            'last_order_side': 'BUY',
            'entry_price': 100.0,
            'partial_taken_1': False,
            'partial_taken_2': False,
            'partial_enabled': True,
            'sl_order_id': 'SL_EXC',
            'sl_exchange_placed': True,
            'stop_loss_at_entry': 94.0,
        }
        mock_client = MagicMock()
        mock_client.cancel_order.return_value = {'orderId': 'SL_EXC', 'status': 'CANCELED'}
        mock_client.get_account.return_value = {
            'balances': [{'asset': 'TRX', 'free': '10.0', 'locked': '0'}]
        }
        ctx = _make_ctx(pair_state=ps, coin_balance_free=10.0, coin_balance=10.0,
                        current_price=106.0)
        deps = _make_deps(client=mock_client)
        deps.check_partial_exits_fn.return_value = (False, False)
        deps.market_sell_fn.side_effect = Exception("API timeout")
        deps.place_sl_fn.return_value = {'orderId': 'SL_RECOVERED'}
        _execute_partial_sells(ctx, deps)
        # SL must be restored despite sell exception
        assert ps.get('sl_order_id') == 'SL_RECOVERED'
        assert ps.get('sl_exchange_placed') is True


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


# ---------------------------------------------------------------------------
# Tests check_partial_exits_from_history (trade_helpers.py)
# ---------------------------------------------------------------------------

def _make_trade(*, is_buyer: bool, qty: float, price: float, time: int,
                order_id: int = 1) -> dict:
    """Helper to build a fake Binance trade dict."""
    return {
        'isBuyer': is_buyer,
        'qty': str(qty),
        'price': str(price),
        'time': time,
        'orderId': order_id,
    }


class TestCheckPartialExitsFromHistory:
    """Tests for check_partial_exits_from_history — fills grouping & detection."""

    def test_no_trades_returns_false_false(self):
        client = MagicMock()
        client.get_my_trades.return_value = []
        p1, p2 = check_partial_exits_from_history('ONDOUSDC', 1.00, client)
        assert (p1, p2) == (False, False)

    def test_no_sells_after_buy(self):
        client = MagicMock()
        client.get_my_trades.return_value = [
            _make_trade(is_buyer=True, qty=100.0, price=1.00, time=1000, order_id=1),
        ]
        p1, p2 = check_partial_exits_from_history('ONDOUSDC', 1.00, client)
        assert (p1, p2) == (False, False)

    def test_single_fill_partial_1_detected(self):
        """One sell fill = 50% of buy qty at +3% → PARTIAL-1 detected."""
        client = MagicMock()
        client.get_my_trades.return_value = [
            _make_trade(is_buyer=True, qty=100.0, price=1.00, time=1000, order_id=1),
            _make_trade(is_buyer=False, qty=50.0, price=1.03, time=2000, order_id=2),
        ]
        p1, p2 = check_partial_exits_from_history('ONDOUSDC', 1.00, client)
        assert p1 is True
        assert p2 is False

    def test_multi_fill_grouped_by_order_id(self):
        """A single market sell split into 3 fills → grouped into one order,
        total qty = 50% → PARTIAL-1 detected (P5-DASH-4 regression test)."""
        client = MagicMock()
        client.get_my_trades.return_value = [
            _make_trade(is_buyer=True, qty=800.0, price=0.265, time=1000, order_id=10),
            # 3 fills of the same sell order (orderId=20)
            _make_trade(is_buyer=False, qty=150.0, price=0.275, time=2000, order_id=20),
            _make_trade(is_buyer=False, qty=130.0, price=0.274, time=2001, order_id=20),
            _make_trade(is_buyer=False, qty=124.6, price=0.276, time=2002, order_id=20),
        ]
        p1, p2 = check_partial_exits_from_history('ONDOUSDC', 0.265, client)
        # total sell = 404.6 / 800 = 0.50575 → in [0.45, 0.55]
        # avg price ~0.275 >= 0.265 * 1.02 * 0.99 (tolerant threshold)
        assert p1 is True
        assert p2 is False

    def test_both_partials_detected(self):
        """Two separate sell orders: P1 (~50%) then P2 (~30%)."""
        client = MagicMock()
        client.get_my_trades.return_value = [
            _make_trade(is_buyer=True, qty=1000.0, price=1.00, time=1000, order_id=1),
            _make_trade(is_buyer=False, qty=500.0, price=1.03, time=2000, order_id=2),
            _make_trade(is_buyer=False, qty=300.0, price=1.05, time=3000, order_id=3),
        ]
        p1, p2 = check_partial_exits_from_history('ONDOUSDC', 1.00, client)
        assert p1 is True
        assert p2 is True

    def test_invalid_entry_price_returns_false(self):
        client = MagicMock()
        client.get_my_trades.return_value = [
            _make_trade(is_buyer=True, qty=100.0, price=1.00, time=1000),
        ]
        p1, p2 = check_partial_exits_from_history('ONDOUSDC', 0.0, client)
        assert (p1, p2) == (False, False)

    def test_exception_returns_false_false(self):
        client = MagicMock()
        client.get_my_trades.side_effect = Exception("API error")
        p1, p2 = check_partial_exits_from_history('ONDOUSDC', 1.00, client)
        assert (p1, p2) == (False, False)

    def test_multi_fill_buy_accumulated(self):
        """Buy order split into 2 fills → qty accumulated correctly."""
        client = MagicMock()
        client.get_my_trades.return_value = [
            _make_trade(is_buyer=True, qty=400.0, price=1.00, time=1000, order_id=1),
            _make_trade(is_buyer=True, qty=400.0, price=1.00, time=1001, order_id=1),
            _make_trade(is_buyer=False, qty=400.0, price=1.03, time=2000, order_id=2),
        ]
        p1, p2 = check_partial_exits_from_history('ONDOUSDC', 1.00, client)
        # sell 400 / buy 800 = 0.50 → P1 detected
        assert p1 is True
        assert p2 is False
