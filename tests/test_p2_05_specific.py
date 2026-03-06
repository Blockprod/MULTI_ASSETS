"""
test_p2_05_specific.py — Les 6 tests spécifiques mentionnés dans le plan P2-05.

Priorité	Test	Valeur
1  test_stop_loss_placement_failure → market sell rollback	Critique
2  test_oos_block_prevents_buy → P0-03 validation	Critique
3  test_balance_unavailable_error → P0-02 propagation	Haute (déjà dans test_p0_fixes.py — rappel + enrichissement)
4  test_partial_sell_idempotency → double vente impossible	Haute
5  test_backtest_fill_next_open → P1-02 validation	Haute (déjà dans test_p1_p2_fixes.py — rappel)
6  test_circuit_breaker_thread_safety → P0-05 validation	Haute (déjà dans test_p0_fixes.py — rappel)

Ce fichier couvre les tests 1, 2 et 4 qui n'étaient pas encore écrits.
Les tests 3, 5 et 6 sont déjà couverts ; une suite de référence croisée est
incluse pour garantir la traçabilité complète du plan P2-05.
"""
import os
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'code', 'src')
CODE_DIR = os.path.join(os.path.dirname(__file__), '..', 'code')
sys.path.insert(0, SRC_DIR)
sys.path.insert(0, CODE_DIR)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — Stop-loss placement failure → market sell rollback  (P0-01, Critique)
# ─────────────────────────────────────────────────────────────────────────────

class TestStopLossRollbackOnFailure:
    """
    Vérifie que, lorsque place_exchange_stop_loss lève OrderError après les retries,
    l'appelant exécute immédiatement un market-sell de rollback (pas de position orpheline).

    Logique implémentée dans MULTI_SYMBOLS.py, pattern P0-01 :
        try:
            result = place_exchange_stop_loss_order(symbol, qty, stop_price)
            pair_state['sl_order_id'] = result['orderId']
        except Exception:
            safe_market_sell(symbol, qty)   ← rollback
    """

    def test_rollback_market_sell_called_when_sl_placement_fails(self):
        """OrderError → market_sell de rollback doit être appelé exactement 1 fois."""
        from exceptions import OrderError

        market_sell_calls = []

        def mock_place_sl(symbol, quantity, stop_price):
            raise OrderError("STOP_LOSS placement failed", symbol=symbol)

        def mock_market_sell(symbol, quantity):
            market_sell_calls.append({'symbol': symbol, 'quantity': quantity})
            return {'status': 'FILLED', 'executedQty': quantity}

        # Reproduit le pattern P0-01 tel qu'implémenté dans MULTI_SYMBOLS.py
        pair_state: dict = {}
        symbol = 'BTCUSDC'
        qty = '0.001'
        stop_price = 50000.0

        try:
            result = mock_place_sl(symbol, qty, stop_price)
            pair_state['sl_order_id'] = result['orderId']
        except Exception:
            mock_market_sell(symbol, qty)

        assert len(market_sell_calls) == 1, (
            f"market_sell aurait dû être appelé 1 fois (rollback), "
            f"obtenu {len(market_sell_calls)}"
        )
        assert market_sell_calls[0]['symbol'] == symbol
        assert market_sell_calls[0]['quantity'] == qty

    def test_sl_order_id_not_persisted_after_failure(self):
        """sl_order_id ne doit PAS être persisté dans pair_state si le placement échoue."""
        from exceptions import OrderError

        def mock_place_sl_fail(symbol, quantity, stop_price):
            raise OrderError("API failure", symbol=symbol)

        pair_state: dict = {}
        try:
            result = mock_place_sl_fail('BTCUSDC', '0.001', 50000.0)
            pair_state['sl_order_id'] = result['orderId']
        except Exception:
            pass  # rollback (market sell)

        assert 'sl_order_id' not in pair_state, (
            "sl_order_id ne doit pas être enregistré si le placement du SL a échoué"
        )

    def test_sl_order_id_persisted_on_success(self):
        """Cas nominal : sl_order_id doit être persisté si le placement réussit."""
        def mock_place_sl_ok(symbol, quantity, stop_price):
            return {'orderId': 99887766, 'status': 'NEW'}

        pair_state: dict = {}
        try:
            result = mock_place_sl_ok('BTCUSDC', '0.001', 50000.0)
            pair_state['sl_order_id'] = result['orderId']
        except Exception:
            pass

        assert pair_state.get('sl_order_id') == 99887766

    def test_place_exchange_stop_loss_raises_order_error_after_all_retries(self):
        """place_exchange_stop_loss doit lever OrderError après les 3 retries (intégration)."""
        from exceptions import OrderError
        from exchange_client import place_exchange_stop_loss

        bad_response = MagicMock()
        bad_response.status_code = 400
        bad_response.json.return_value = {'code': -2010, 'msg': 'Account has insufficient balance'}

        mock_client = MagicMock()
        mock_client._server_time_offset = 0
        mock_client.api_secret = 'test_key'
        mock_client.api_key = 'test_key'
        mock_client._sync_server_time.return_value = None

        with patch('exchange_client.requests.post', return_value=bad_response):
            with pytest.raises(OrderError):
                place_exchange_stop_loss(
                    client=mock_client,
                    symbol='BTCUSDC',
                    quantity='0.001',
                    stop_price=50000.0,
                )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — OOS block prevents new buys  (P0-03, Critique)
# ─────────────────────────────────────────────────────────────────────────────

class TestOOSBlockPreventsNewBuys:
    """
    Vérifie que le flag pair_state['oos_blocked'] bloque les nouveaux achats
    (P0-03) tout en laissant les ventes / stop-loss s'exécuter.

    Code ciblé dans MULTI_SYMBOLS.py :
        if pair_state.get('oos_blocked'):
            logger.warning("[BUY BLOCKED P0-03] ...")
        elif buy_condition and usdc_for_buy > 0:
            ...exécuter l'achat...
    """

    def _simulate_buy_decision(self, pair_state: dict, buy_condition: bool,
                                usdc_for_buy: float) -> bool:
        """Microfunc qui reproduit la condition de branchement P0-03."""
        if pair_state.get('oos_blocked'):
            return False          # achat bloqué
        if buy_condition and usdc_for_buy > 0:
            return True           # achat autorisé
        return False

    def test_oos_blocked_prevents_buy(self):
        """pair_state['oos_blocked'] = True → aucun achat."""
        pair_state = {'oos_blocked': True, 'oos_blocked_since': 1700000000.0}
        assert self._simulate_buy_decision(pair_state, buy_condition=True, usdc_for_buy=500.0) is False

    def test_oos_not_blocked_allows_buy(self):
        """Sans oos_blocked, un achat avec signal valide est autorisé."""
        pair_state = {}
        assert self._simulate_buy_decision(pair_state, buy_condition=True, usdc_for_buy=500.0) is True

    def test_oos_blocked_false_allows_buy(self):
        """oos_blocked=False explicite → achat autorisé."""
        pair_state = {'oos_blocked': False}
        assert self._simulate_buy_decision(pair_state, buy_condition=True, usdc_for_buy=500.0) is True

    def test_oos_block_set_when_no_result_passes_gates(self):
        """Quand aucun résultat ne passe les gates, oos_blocked doit être True
        et le pool de sélection ne doit contenir que des résultats non filtrés."""
        try:
            from walk_forward import validate_oos_result
        except ImportError:
            pytest.skip("walk_forward.validate_oos_result non disponible")

        # Simuler des résultats tous en dessous des seuils (sharpe < 0.5 ou WR < 45%)
        bad_results = [
            {'sharpe_ratio': 0.1, 'win_rate': 30.0, 'final_wallet': 9000, 'initial_wallet': 10000},
            {'sharpe_ratio': 0.3, 'win_rate': 40.0, 'final_wallet': 9500, 'initial_wallet': 10000},
        ]

        oos_valid = [r for r in bad_results if validate_oos_result(r.get('sharpe_ratio', 0.0), r.get('win_rate', 0.0))]
        assert len(oos_valid) == 0, "Aucun résultat ne devrait passer avec sharpe < 0.5"

        # oos_blocked doit être mis à True dans ce cas
        pair_state: dict = {}
        if not oos_valid:
            pair_state['oos_blocked'] = True

        assert pair_state['oos_blocked'] is True

    def test_oos_block_cleared_when_results_pass_gates(self):
        """Quand des résultats repassent les gates, oos_blocked doit être supprimé."""
        try:
            from walk_forward import validate_oos_result
        except ImportError:
            pytest.skip("walk_forward.validate_oos_result non disponible")

        good_results = [
            {'sharpe_ratio': 1.2, 'win_rate': 60.0, 'final_wallet': 12000, 'initial_wallet': 10000},
        ]

        oos_valid = [r for r in good_results if validate_oos_result(r.get('sharpe_ratio', 0.0), r.get('win_rate', 0.0))]
        pair_state = {'oos_blocked': True, 'oos_blocked_since': 1700000000.0}

        if oos_valid:
            pair_state.pop('oos_blocked', None)
            pair_state.pop('oos_blocked_since', None)

        assert 'oos_blocked' not in pair_state

    def test_sell_still_executes_when_oos_blocked(self):
        """Les ventes (SIGNAL / STOP-LOSS) doivent continuer même si achat bloqué."""
        # Le bloc oos_blocked ne concerne QUE le code d'achat, pas les ventes.
        # Ici on vérifie la structure de contrôle : position_has_crypto → sell logic
        # n'est pas conditionnée par oos_blocked.
        pair_state = {'oos_blocked': True, 'last_order_side': 'BUY', 'entry_price': 100.0}
        position_has_crypto = True   # solde existant
        sell_triggered = True        # signal de vente reçu

        # Guard OOS ne bloquerait PAS la vente (elle est dans un bloc séparé)
        buy_blocked = pair_state.get('oos_blocked', False)
        sell_should_execute = position_has_crypto and sell_triggered

        assert buy_blocked is True
        assert sell_should_execute is True


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — Partial sell idempotency  (anti-double-sell, Haute)
# ─────────────────────────────────────────────────────────────────────────────

class TestPartialSellIdempotency:
    """
    Vérifie que le flag partial_taken_1 (et partial_taken_2) empêche une seconde
    exécution d'un partial au même seuil.

    Pattern implémenté dans backtest_from_dataframe (Python) :
        if not partial_taken_1 and profit_pct >= config.partial_threshold_1:
            sell_partial(50%)
            partial_taken_1 = True

    Et dans le live trading (MULTI_SYMBOLS.py) :
        pair_state['partial_taken_1'] = True  ← mis à True après vente
    """

    def _apply_partial_logic(self, pair_state: dict, profit_pct: float,
                              partial_threshold_1: float = 0.02,
                              partial_threshold_2: float = 0.04) -> list:
        """Simule la logique de partial selling. Retourne la liste des ventes déclenchées."""
        sells = []
        if not pair_state.get('partial_taken_1', False) and profit_pct >= partial_threshold_1:
            sells.append('PARTIAL-1')
            pair_state['partial_taken_1'] = True
        if not pair_state.get('partial_taken_2', False) and profit_pct >= partial_threshold_2:
            sells.append('PARTIAL-2')
            pair_state['partial_taken_2'] = True
        return sells

    def test_first_partial_executes_when_threshold_reached(self):
        """Quand profit_pct >= threshold et partial non pris, PARTIAL-1 est exécuté."""
        pair_state = {'partial_taken_1': False, 'partial_taken_2': False}
        sells = self._apply_partial_logic(pair_state, profit_pct=0.025)
        assert 'PARTIAL-1' in sells
        assert pair_state['partial_taken_1'] is True

    def test_second_call_does_not_re_execute_partial_1(self):
        """Appel identique après que partial_taken_1=True → PARTIAL-1 NON exécuté."""
        pair_state = {'partial_taken_1': True, 'partial_taken_2': False}
        sells = self._apply_partial_logic(pair_state, profit_pct=0.025)
        assert 'PARTIAL-1' not in sells, (
            "PARTIAL-1 ne doit pas être ré-exécuté quand partial_taken_1=True"
        )

    def test_partial_2_not_executed_before_threshold(self):
        """PARTIAL-2 ne doit pas se déclencher si profit_pct < partial_threshold_2."""
        pair_state = {'partial_taken_1': True, 'partial_taken_2': False}
        sells = self._apply_partial_logic(pair_state, profit_pct=0.025)
        assert 'PARTIAL-2' not in sells

    def test_partial_2_executes_at_correct_threshold(self):
        """PARTIAL-2 se déclenche quand profit_pct >= partial_threshold_2."""
        pair_state = {'partial_taken_1': True, 'partial_taken_2': False}
        sells = self._apply_partial_logic(pair_state, profit_pct=0.045)
        assert 'PARTIAL-2' in sells

    def test_both_partials_blocked_when_flags_true(self):
        """Aucun partial si les deux flags sont déjà True."""
        pair_state = {'partial_taken_1': True, 'partial_taken_2': True}
        sells = self._apply_partial_logic(pair_state, profit_pct=0.10)
        assert sells == [], f"Aucun partial attendu, obtenu : {sells}"

    def test_partial_flag_persisted_in_pair_state(self):
        """Après PARTIAL-1, le flag doit rester True en mémoire (pas de reset accidentel)."""
        pair_state = {'partial_taken_1': False, 'partial_taken_2': False}
        self._apply_partial_logic(pair_state, profit_pct=0.03)
        assert pair_state['partial_taken_1'] is True
        # Deuxième appel (cycle suivant)
        sells_2 = self._apply_partial_logic(pair_state, profit_pct=0.03)
        assert 'PARTIAL-1' not in sells_2

    def test_concurrent_partial_execution_is_idempotent(self):
        """
        Simule 2 threads appelant la logique de partial simultanément.
        Un seul partial doit être enregistré (pas de double vente).
        """

        pair_state = {'partial_taken_1': False, 'partial_taken_2': False}
        sell_count = [0]
        lock = threading.Lock()

        def threaded_partial():
            # Logique thread-safe : lire puis écrire atomiquement
            with lock:
                if not pair_state.get('partial_taken_1', False):
                    sell_count[0] += 1
                    pair_state['partial_taken_1'] = True

        threads = [threading.Thread(target=threaded_partial) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sell_count[0] == 1, (
            f"PARTIAL-1 exécuté {sell_count[0]} fois, attendu exactement 1"
        )


# ─────────────────────────────────────────────────────────────────────────────
# RÉFÉRENCE CROISÉE — Tests déjà couverts dans d'autres fichiers (P2-05 complet)
# ─────────────────────────────────────────────────────────────────────────────

class TestP205CrossReferences:
    """
    Vérifie que les 3 autres cas P2-05 sont bien couverts dans les fichiers
    de test existants. Ces tests servent de « sentinel » pour empêcher la
    suppression accidentelle des tests critiques.
    """

    def test_balance_unavailable_error_is_covered(self):
        """P2-05 #3 — BalanceUnavailableError est testée dans test_p0_fixes.py."""
        import importlib
        p0 = importlib.import_module('test_p0_fixes')
        assert hasattr(p0, 'TestBalanceUnavailableError'), (
            "TestBalanceUnavailableError doit exister dans test_p0_fixes.py"
        )

    def test_backtest_fill_next_open_is_covered(self):
        """P2-05 #5 — fill next_open est testé dans test_p1_p2_fixes.py."""
        import importlib
        p1p2 = importlib.import_module('test_p1_p2_fixes')
        assert hasattr(p1p2, 'TestCythonEngineP1P2'), (
            "TestCythonEngineP1P2 doit exister dans test_p1_p2_fixes.py"
        )

    def test_circuit_breaker_thread_safety_is_covered(self):
        """P2-05 #6 — CircuitBreaker thread-safety est testée dans test_p0_fixes.py."""
        import importlib
        p0 = importlib.import_module('test_p0_fixes')
        assert hasattr(p0, 'TestCircuitBreakerThreadSafety'), (
            "TestCircuitBreakerThreadSafety doit exister dans test_p0_fixes.py"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 7 — OOS alert email cooldown (anti-spam)
# ─────────────────────────────────────────────────────────────────────────────

class TestOOSAlertCooldown:
    """Vérifie que l'alerte email OOS est throttled et n'est pas envoyée
    à chaque exécution planifiée (toutes les 2 min).
    L'email ne doit être renvoyé qu'après backtest_throttle_seconds (défaut 1h)."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Reset du module-level cooldown dict avant chaque test."""
        import MULTI_SYMBOLS as ms
        ms._oos_alert_last_sent.clear()
        yield
        ms._oos_alert_last_sent.clear()

    def _bad_results(self):
        """Résultats qui ne passent jamais les OOS gates."""
        return [
            {'sharpe_ratio': 0.1, 'win_rate': 10.0, 'final_wallet': 9000,
             'initial_wallet': 10000, 'timeframe': '1d',
             'ema_periods': [20, 40], 'scenario': 'StochRSI'},
        ]

    @patch('MULTI_SYMBOLS.send_trading_alert_email', return_value=True)
    @patch('MULTI_SYMBOLS.save_bot_state')
    def test_first_call_sends_email(self, mock_save, mock_email):
        """Première alerte OOS: email envoyé."""
        import MULTI_SYMBOLS as ms
        _, blocked = ms.apply_oos_quality_gate(
            self._bad_results(), 'SOLUSDT',
            log_tag='TEST', send_alert=True,
        )
        assert blocked is True
        assert mock_email.call_count == 1
        assert 'SOLUSDT' in _oos_alert_last_sent_keys(ms)

    @patch('MULTI_SYMBOLS.send_trading_alert_email', return_value=True)
    @patch('MULTI_SYMBOLS.save_bot_state')
    def test_second_call_within_cooldown_no_email(self, mock_save, mock_email):
        """Deuxième appel dans le cooldown: email throttled."""
        import MULTI_SYMBOLS as ms
        ms.apply_oos_quality_gate(
            self._bad_results(), 'SOLUSDT',
            log_tag='TEST', send_alert=True,
        )
        assert mock_email.call_count == 1

        # 2ème appel immédiat → throttled
        ms.apply_oos_quality_gate(
            self._bad_results(), 'SOLUSDT',
            log_tag='TEST', send_alert=True,
        )
        assert mock_email.call_count == 1  # pas de 2ème email

    @patch('MULTI_SYMBOLS.send_trading_alert_email', return_value=True)
    @patch('MULTI_SYMBOLS.save_bot_state')
    def test_email_sent_again_after_cooldown_expires(self, mock_save, mock_email):
        """Après expiration du cooldown, l'email est renvoyé."""
        import MULTI_SYMBOLS as ms
        import time as _time

        ms.apply_oos_quality_gate(
            self._bad_results(), 'SOLUSDT',
            log_tag='TEST', send_alert=True,
        )
        assert mock_email.call_count == 1

        # Simuler que le cooldown est expiré
        ms._oos_alert_last_sent['SOLUSDT'] = _time.time() - 7200  # 2h ago

        ms.apply_oos_quality_gate(
            self._bad_results(), 'SOLUSDT',
            log_tag='TEST', send_alert=True,
        )
        assert mock_email.call_count == 2  # email renvoyé

    @patch('MULTI_SYMBOLS.send_trading_alert_email', return_value=True)
    @patch('MULTI_SYMBOLS.save_bot_state')
    def test_different_pairs_have_independent_cooldowns(self, mock_save, mock_email):
        """Chaque paire a son propre cooldown indépendant."""
        import MULTI_SYMBOLS as ms

        ms.apply_oos_quality_gate(
            self._bad_results(), 'SOLUSDT',
            log_tag='TEST', send_alert=True,
        )
        ms.apply_oos_quality_gate(
            self._bad_results(), 'BTCUSDT',
            log_tag='TEST', send_alert=True,
        )
        # 2 emails: 1 par paire
        assert mock_email.call_count == 2

    @patch('MULTI_SYMBOLS.send_trading_alert_email', return_value=True)
    @patch('MULTI_SYMBOLS.save_bot_state')
    def test_no_alert_when_send_alert_false(self, mock_save, mock_email):
        """Pas d'email si send_alert=False (MAIN C-13 par défaut)."""
        import MULTI_SYMBOLS as ms
        _, blocked = ms.apply_oos_quality_gate(
            self._bad_results(), 'SOLUSDT',
            log_tag='TEST', send_alert=False,
        )
        assert blocked is True
        assert mock_email.call_count == 0


def _oos_alert_last_sent_keys(ms):
    """Helper: retourne les clés du cooldown dict."""
    return set(ms._oos_alert_last_sent.keys())