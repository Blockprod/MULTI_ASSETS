"""tests/test_position_reconciler.py — TS-P2-04

Tests de réconciliation positions : _check_pair_vs_exchange et _handle_pair_discrepancy
avec mock API Binance injecté via _ReconcileDeps (pas de patching global).

Scénarios couverts :
  1. Position orpheline détectée (coins Binance > 0, bot_state sans BUY)
  2. Position fantôme détectée (bot_state BUY, coins Binance = 0)
  3. Position cohérente (BUY + coins > 0) → aucune action corrective
  4. Aucune position (bot_state vide + coins = 0) → aucune action
  5. Échec API get_account → _check retourne None (pas d'exception)
  6. Position orpheline → email critique + restauration last_order_side='BUY'
  7. Position fantôme → reset état + last_order_side='SELL' + save(force=True)
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

from position_reconciler import (
    _ReconcileDeps,
    _PairStatus,
    _check_pair_vs_exchange,
    _handle_pair_discrepancy,
)

# ---------------------------------------------------------------------------
# Constantes de test
# ---------------------------------------------------------------------------
BACKTEST_PAIR = 'ONDOUSDT'
REAL_PAIR = 'ONDOUSDC'
COIN_SYMBOL = 'ONDO'
PRICE = 0.10        # prix unitaire ONDO en USDC
ONDO_QTY = 100.0   # solde suffisant pour dépasser MIN_NOTIONAL (100 × 0.10 = 10 USDC > 5 USDC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_account(ondo_total: float = ONDO_QTY) -> dict:
    """Mock d'account_info Binance avec le solde ONDO spécifié."""
    return {
        'balances': [
            {'asset': 'USDC', 'free': '1000.0', 'locked': '0.0'},
            {'asset': 'ONDO', 'free': str(ondo_total), 'locked': '0.0'},
        ]
    }


def _make_exchange_info(min_qty: str = '1.0') -> dict:
    """Exchange info avec LOT_SIZE pour ONDO (pas de filtre NOTIONAL → fallback 5.0)."""
    return {
        'symbols': [{
            'symbol': REAL_PAIR,
            'filters': [
                {
                    'filterType': 'LOT_SIZE',
                    'minQty': min_qty,
                    'stepSize': '1.0',
                    'maxQty': '9000000',
                },
            ],
        }]
    }


def _make_deps(
    bot_state: dict | None = None,
    ondo_total: float = ONDO_QTY,
    price: float = PRICE,
    alert_fn=None,
    save_fn=None,
) -> _ReconcileDeps:
    """Crée un _ReconcileDeps complet avec mocks injectés (aucun import circulaire)."""
    from unittest.mock import MagicMock
    mock_client = MagicMock()
    mock_client.get_account.return_value = _make_account(ondo_total=ondo_total)
    mock_client.get_symbol_ticker.return_value = {'price': str(price)}
    mock_client.get_all_orders.return_value = []    # pas d'historique par défaut
    mock_client.get_order.return_value = {'status': 'NEW'}  # SL non filled

    return _ReconcileDeps(
        client=mock_client,
        bot_state=bot_state if bot_state is not None else {},
        bot_state_lock=threading.RLock(),
        save_fn=save_fn or (lambda force=False: None),
        send_alert_fn=alert_fn or (lambda **kw: None),
        place_sl_fn=MagicMock(return_value={'orderId': 'sl_mock_001'}),
        get_exchange_info_fn=lambda c: _make_exchange_info(),
    )


def _make_pair_info() -> dict:
    return {'backtest_pair': BACKTEST_PAIR, 'real_pair': REAL_PAIR}


def _make_status(
    has_real_balance: bool,
    local_in_position: bool,
    pair_state: dict | None = None,
    coin_balance: float | None = None,
) -> _PairStatus:
    """Crée un _PairStatus directement (bypass _check_pair_vs_exchange)."""
    if coin_balance is None:
        coin_balance = ONDO_QTY if has_real_balance else 0.0
    return _PairStatus(
        backtest_pair=BACKTEST_PAIR,
        real_pair=REAL_PAIR,
        coin_symbol=COIN_SYMBOL,
        coin_balance=coin_balance,
        current_price=PRICE,
        pair_state=pair_state if pair_state is not None else {},
        has_real_balance=has_real_balance,
        local_in_position=local_in_position,
    )


# ---------------------------------------------------------------------------
# Tests : _check_pair_vs_exchange (lecture seule — construit _PairStatus)
# ---------------------------------------------------------------------------

class TestCheckPairVsExchange:

    def test_orphan_position_detected(self):
        """Coins sur Binance + bot_state sans BUY → has_real_balance=True, local_in_position=False."""
        bot_state = {BACKTEST_PAIR: {}}
        deps = _make_deps(bot_state=bot_state, ondo_total=ONDO_QTY)

        status = _check_pair_vs_exchange(_make_pair_info(), deps)

        assert status is not None
        assert status.has_real_balance is True
        assert status.local_in_position is False

    def test_ghost_position_detected(self):
        """Bot_state indique BUY, Binance solde=0 → has_real_balance=False, local_in_position=True."""
        bot_state = {BACKTEST_PAIR: {'last_order_side': 'BUY'}}
        deps = _make_deps(bot_state=bot_state, ondo_total=0.0)

        status = _check_pair_vs_exchange(_make_pair_info(), deps)

        assert status is not None
        assert status.has_real_balance is False
        assert status.local_in_position is True

    def test_coherent_position_with_coins(self):
        """Bot_state BUY + solde Binance > 0 → has_real_balance=True, local_in_position=True."""
        bot_state = {BACKTEST_PAIR: {'last_order_side': 'BUY'}}
        deps = _make_deps(bot_state=bot_state, ondo_total=ONDO_QTY)

        status = _check_pair_vs_exchange(_make_pair_info(), deps)

        assert status is not None
        assert status.has_real_balance is True
        assert status.local_in_position is True

    def test_no_position_no_coins(self):
        """Pas de BUY dans bot_state, solde=0 → has_real_balance=False, local_in_position=False."""
        bot_state = {BACKTEST_PAIR: {}}
        deps = _make_deps(bot_state=bot_state, ondo_total=0.0)

        status = _check_pair_vs_exchange(_make_pair_info(), deps)

        assert status is not None
        assert status.has_real_balance is False
        assert status.local_in_position is False

    def test_api_failure_returns_none(self):
        """Échec get_account → _check retourne None (pas d'exception propagée)."""
        deps = _make_deps()
        deps.client.get_account.side_effect = Exception("API timeout — expected in test")

        status = _check_pair_vs_exchange(_make_pair_info(), deps)

        assert status is None


# ---------------------------------------------------------------------------
# Tests : _handle_pair_discrepancy (actions correctives)
# ---------------------------------------------------------------------------

class TestHandlePairDiscrepancy:

    def test_orphan_triggers_email_and_restores_buy(self):
        """Position orpheline → email critique envoyé + last_order_side restauré à 'BUY'."""
        bot_state = {BACKTEST_PAIR: {}}
        alert_calls = []
        deps = _make_deps(
            bot_state=bot_state,
            alert_fn=lambda **kw: alert_calls.append(kw),
        )

        status = _make_status(has_real_balance=True, local_in_position=False,
                              pair_state=bot_state[BACKTEST_PAIR])
        _handle_pair_discrepancy(status, deps)

        # Au moins une alerte critique doit être envoyée
        assert len(alert_calls) >= 1, "Aucun email d'alerte envoyé pour position orpheline"
        subjects = [str(c.get('subject', '')) for c in alert_calls]
        assert any('orpheline' in s.lower() or 'critique' in s.lower() for s in subjects), (
            f"Email attendu avec 'orpheline' ou 'critique' dans le sujet, got: {subjects}"
        )
        # L'état doit être restauré avec last_order_side='BUY'
        assert bot_state[BACKTEST_PAIR].get('last_order_side') == 'BUY', (
            "Position orpheline : last_order_side doit être restauré à 'BUY'"
        )

    def test_ghost_resets_pair_state_to_sell(self):
        """Position fantôme (BUY dans bot_state, solde=0) → last_order_side='SELL' + save(force=True)."""
        bot_state = {
            BACKTEST_PAIR: {
                'last_order_side': 'BUY',
                'entry_price': 0.08,
                'sl_order_id': None,
            }
        }
        save_calls = []
        deps = _make_deps(
            bot_state=bot_state,
            ondo_total=0.0,
            save_fn=lambda force=False: save_calls.append(force),
        )

        status = _make_status(has_real_balance=False, local_in_position=True,
                              pair_state=bot_state[BACKTEST_PAIR], coin_balance=0.0)
        _handle_pair_discrepancy(status, deps)

        assert bot_state[BACKTEST_PAIR].get('last_order_side') == 'SELL', (
            "Position fantôme : last_order_side doit être réinitialisé à 'SELL'"
        )
        assert bot_state[BACKTEST_PAIR].get('entry_price') is None, (
            "entry_price doit être effacé après réconciliation fantôme"
        )
        assert any(f is True for f in save_calls), (
            "save_fn(force=True) doit être appelé après réconciliation fantôme"
        )

    def test_coherent_position_no_corrective_action(self):
        """Position cohérente (BUY + coins > 0) → aucune alerte email, état inchangé."""
        bot_state = {BACKTEST_PAIR: {'last_order_side': 'BUY', 'entry_price': 0.08}}
        alert_calls = []
        save_calls = []
        deps = _make_deps(
            bot_state=bot_state,
            ondo_total=ONDO_QTY,
            alert_fn=lambda **kw: alert_calls.append(kw),
            save_fn=lambda force=False: save_calls.append(force),
        )

        status = _make_status(has_real_balance=True, local_in_position=True,
                              pair_state=bot_state[BACKTEST_PAIR])
        _handle_pair_discrepancy(status, deps)

        assert len(alert_calls) == 0, (
            f"Aucune alerte critique attendue pour position cohérente, got: {alert_calls}"
        )
        assert bot_state[BACKTEST_PAIR].get('last_order_side') == 'BUY', (
            "last_order_side ne doit pas changer pour une position cohérente"
        )

    def test_no_position_no_coins_no_action(self):
        """Pas de position locale ni de solde → aucune alerte, save non appelé."""
        bot_state = {BACKTEST_PAIR: {}}
        alert_calls = []
        save_calls = []
        deps = _make_deps(
            bot_state=bot_state,
            ondo_total=0.0,
            alert_fn=lambda **kw: alert_calls.append(kw),
            save_fn=lambda force=False: save_calls.append(force),
        )

        status = _make_status(has_real_balance=False, local_in_position=False,
                              pair_state=bot_state[BACKTEST_PAIR], coin_balance=0.0)
        _handle_pair_discrepancy(status, deps)

        assert len(alert_calls) == 0, "Aucune alerte pour position absente + solde nul"
        assert len(save_calls) == 0, "save_fn ne doit pas être appelé en cas de cohérence totale"
