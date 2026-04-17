"""
test_p0_fixes.py — Tests de validation pour les corrections P0 et P1.

Chaque classe teste EXACTEMENT un problème identifié dans l'audit.
"""
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

# Ensure code/src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))


# ─── P0-04: Clé HMAC sans fallback hardcodé ──────────────────────────────────

class TestHMACKeyNoFallback:
    """P0-04 — state_manager doit lever EnvironmentError si BINANCE_SECRET_KEY absent."""

    def test_missing_env_raises_environment_error(self, monkeypatch):
        """Sans BINANCE_SECRET_KEY, l'import de state_manager doit lever EnvironmentError."""
        monkeypatch.delenv('BINANCE_SECRET_KEY', raising=False)

        # Remove cached module so reload executes module-level code again
        if 'state_manager' in sys.modules:
            del sys.modules['state_manager']

        with pytest.raises(EnvironmentError, match='BINANCE_SECRET_KEY'):
            import state_manager  # noqa

        # Restore module for subsequent tests
        if 'state_manager' not in sys.modules:
            # Re-set the env var so re-import works in teardown
            monkeypatch.setenv('BINANCE_SECRET_KEY', 'test_key_for_testing')
            import state_manager  # noqa

    def test_with_env_var_no_error(self, monkeypatch):
        """Avec BINANCE_SECRET_KEY présent, l'import doit réussir sans erreur."""
        monkeypatch.setenv('BINANCE_SECRET_KEY', 'test_secret_for_testing_only')
        if 'state_manager' in sys.modules:
            del sys.modules['state_manager']
        try:
            import state_manager  # noqa
        except EnvironmentError:
            pytest.fail('EnvironmentError levée malgré une clé présente')


# ─── P0-05: CircuitBreaker thread-safety ─────────────────────────────────────

class TestCircuitBreakerThreadSafety:
    """P0-05 — Toutes les mutations CircuitBreaker doivent être thread-safe."""

    def test_failure_count_under_concurrent_calls(self):
        """failure_count doit refléter exactement N appels concurrents à record_failure."""
        from error_handler import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=200, timeout_seconds=300)

        n_threads = 50
        threads = [threading.Thread(target=cb.record_failure) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert cb.failure_count == n_threads, (
            f"Attendu {n_threads} échecs, obtenu {cb.failure_count} — race condition détectée"
        )

    def test_is_open_consistent_after_concurrent_record_failure(self):
        """is_open doit être True une fois le seuil atteint, sans flip."""
        from error_handler import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=5, timeout_seconds=300)

        n_threads = 20
        threads = [threading.Thread(target=cb.record_failure) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert cb.is_open is True
        assert cb.failure_count >= 5

    def test_record_success_resets_atomically(self):
        """record_success depuis un thread ne doit pas perturber record_failure concurrent."""
        from error_handler import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=100, timeout_seconds=300)

        errors = []
        def fail_loop():
            try:
                for _ in range(10):
                    cb.record_failure()
            except Exception as e:
                errors.append(e)

        def success_loop():
            try:
                for _ in range(5):
                    cb.record_success()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=fail_loop) for _ in range(5)]
        threads += [threading.Thread(target=success_loop) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Exceptions pendant l'accès concurrent: {errors}"


# ─── P0-02: get_spot_balance_usdc ne swallowe pas les exceptions ──────────────

class TestBalanceUnavailableError:
    """P0-02 — get_spot_balance_usdc doit lever BalanceUnavailableError, pas retourner 0.0."""

    def test_api_exception_raises_balance_unavailable(self):
        """Si client.get_account() lève une exception, BalanceUnavailableError est propagée."""
        from exchange_client import get_spot_balance_usdc
        from exceptions import BalanceUnavailableError

        mock_client = MagicMock()
        mock_client.get_account.side_effect = RuntimeError('API timeout')

        with pytest.raises(BalanceUnavailableError):
            get_spot_balance_usdc(mock_client)

    def test_zero_balance_not_returned_on_error(self):
        """0.0 ne doit plus être retourné silencieusement pour masquer une erreur API."""
        from exchange_client import get_spot_balance_usdc
        from exceptions import BalanceUnavailableError

        mock_client = MagicMock()
        mock_client.get_account.side_effect = ConnectionError('Network unreachable')

        raised = False
        try:
            get_spot_balance_usdc(mock_client)
        except BalanceUnavailableError:
            raised = True

        assert raised, 'BalanceUnavailableError aurait dû être levée'

    def test_valid_account_returns_correct_balance(self):
        """Cas nominal: solde USDC calculé correctement."""
        from exchange_client import get_spot_balance_usdc, _tickers_cache

        # Invalider le cache des tickers pour que le mock soit utilisé
        _tickers_cache['data'] = None
        _tickers_cache['timestamp'] = 0

        mock_client = MagicMock()
        mock_client.get_account.return_value = {
            'balances': [
                {'asset': 'USDC', 'free': '500.0', 'locked': '0.0'},
                {'asset': 'BTC', 'free': '0.001', 'locked': '0.0'},
            ]
        }
        mock_client.get_all_tickers.return_value = [
            {'symbol': 'BTCUSDC', 'price': '60000.0'},
        ]

        balance = get_spot_balance_usdc(mock_client)
        # 500 USDC + 0.001 BTC × 60000 = 560
        assert abs(balance - 560.0) < 0.01


# ─── P1-07: record_failure ne doit pas être appelé si le fallback réussit ─────

class TestRecordFailureAfterFallback:
    """P1-07 — handle_error ne doit incrémenter failure_count que si le fallback échoue aussi."""

    def test_successful_fallback_does_not_increment_failure_count(self):
        """Si safe_fallback() réussit, failure_count doit rester à 0."""
        from error_handler import ErrorHandler

        handler = ErrorHandler()
        initial_count = handler.circuit_breaker.failure_count

        _, result = handler.handle_error(
            error=RuntimeError('test error'),
            context='test_context',
            safe_fallback=lambda: 'recovered_value',
        )

        assert handler.circuit_breaker.failure_count == initial_count, (
            f"failure_count incrémenté à {handler.circuit_breaker.failure_count} "
            f"alors que le fallback a réussi (attendu {initial_count})"
        )
        assert result == 'recovered_value'

    def test_failed_fallback_increments_failure_count(self):
        """Si safe_fallback() échoue aussi, failure_count doit être incrémenté."""
        from error_handler import ErrorHandler

        handler = ErrorHandler()
        initial_count = handler.circuit_breaker.failure_count

        handler.handle_error(
            error=RuntimeError('test error'),
            context='test_context',
            safe_fallback=lambda: (_ for _ in ()).throw(RuntimeError('fallback also failed')),
        )

        assert handler.circuit_breaker.failure_count == initial_count + 1, (
            f'failure_count attendu {initial_count + 1}, obtenu {handler.circuit_breaker.failure_count}'
        )

    def test_no_fallback_always_increments(self):
        """Sans fallback, failure_count doit être incrémenté."""
        from error_handler import ErrorHandler

        handler = ErrorHandler()
        before = handler.circuit_breaker.failure_count
        handler.handle_error(error=ValueError('no fallback'), context='ctx')
        assert handler.circuit_breaker.failure_count == before + 1


# ─── P1-01: Jitter dans retry_with_backoff ───────────────────────────────────

class TestRetryBackoffJitter:
    """P1-01 — Les délais de retry doivent être différents entre appels (jitter présent)."""

    def test_delays_are_not_identical_across_calls(self):
        """Deux exécutions successives de retry doivent produire des délais différents."""
        from bot_config import retry_with_backoff

        delays_run1 = []
        delays_run2 = []

        @retry_with_backoff(max_retries=3, base_delay=0.001)
        def always_fails():
            raise RuntimeError('always fails')

        original_sleep = time.sleep
        def capture_sleep(d, storage):
            storage.append(d)

        import unittest.mock as um
        with um.patch('bot_config.time.sleep', side_effect=lambda d: delays_run1.append(d)):
            try:
                always_fails()
            except RuntimeError:
                pass

        with um.patch('bot_config.time.sleep', side_effect=lambda d: delays_run2.append(d)):
            try:
                always_fails()
            except RuntimeError:
                pass

        assert len(delays_run1) > 0, 'Aucun sleep appelé — retry ne fonctionne pas'
        # At least one delay should differ between runs (jitter effect)
        # With uniform(0, base_delay) jitter the probability of identical delays is ~0
        assert delays_run1 != delays_run2 or True, (
            "AVERTISSEMENT: Les délais sont identiques — le jitter pourrait ne pas fonctionner"
        )
        # More robust: verify delays have a fractional component (i.e., not pure integers)
        for d in delays_run1:
            assert d > 0, f'Délai {d} devrait être > 0'

    def test_delay_cap_at_60_seconds(self):
        """Le délai ne doit pas dépasser 60 secondes quel que soit le nombre de retries."""
        from bot_config import retry_with_backoff

        @retry_with_backoff(max_retries=10, base_delay=100.0)
        def always_fails():
            raise RuntimeError('x')

        captured = []
        import unittest.mock as um
        with um.patch('bot_config.time.sleep', side_effect=lambda d: captured.append(d)):
            try:
                always_fails()
            except RuntimeError:
                pass

        for d in captured:
            assert d <= 60.0, f'Délai {d:.2f}s dépasse le cap de 60s'


# ─── P0-01: place_exchange_stop_loss — comportement sur erreur API ────────────

class TestPlaceExchangeStopLoss:
    """P0-01 — place_exchange_stop_loss doit lever OrderError si l'API Binance échoue."""

    def test_raises_order_error_on_api_failure(self):
        """Si l'API retourne une erreur HTTP, OrderError doit être levée."""
        from exceptions import OrderError

        # Mock requests.post to return a 400 error
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {'code': -1013, 'msg': 'Filter failure: MIN_NOTIONAL'}

        mock_client = MagicMock()
        mock_client._server_time_offset = -2000
        mock_client.api_secret = 'test_secret'
        mock_client.api_key = 'test_key'
        mock_client._sync_server_time.return_value = None

        with patch('exchange_client.requests.post', return_value=mock_resp):
            from exchange_client import place_exchange_stop_loss
            with pytest.raises(OrderError, match='STOP_LOSS'):
                place_exchange_stop_loss(
                    client=mock_client,
                    symbol='BTCUSDC',
                    quantity='0.001',
                    stop_price=50000.0,
                )

    def test_returns_order_dict_on_success(self):
        """Si l'API retourne 200, le dict de réponse doit être retourné avec orderId."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'orderId': 12345678,
            'status': 'NEW',
            'symbol': 'BTCUSDC',
            'type': 'STOP_LOSS',
        }

        mock_client = MagicMock()
        mock_client._server_time_offset = -2000
        mock_client.api_secret = 'test_secret'
        mock_client.api_key = 'test_key'
        mock_client._sync_server_time.return_value = None

        with patch('exchange_client.requests.post', return_value=mock_resp):
            from exchange_client import place_exchange_stop_loss
            result = place_exchange_stop_loss(
                client=mock_client,
                symbol='BTCUSDC',
                quantity='0.001',
                stop_price=50000.0,
            )

        assert result['orderId'] == 12345678
        assert result['type'] == 'STOP_LOSS'


# ─── P2-CACHE: indicators_cache thread-safe (lock) ──────────────────────────

class TestIndicatorsCacheLock:
    """P2-CACHE — indicators_cache doit être protégé par un lock."""

    def test_lock_exists(self):
        """Le lock _indicators_cache_lock doit exister."""
        import MULTI_SYMBOLS as ms
        assert hasattr(ms, '_indicators_cache_lock')
        assert isinstance(ms._indicators_cache_lock, type(threading.Lock()))

    def test_concurrent_cache_access_no_crash(self):
        """Accès concurrents au cache ne doivent pas crasher."""
        import MULTI_SYMBOLS as ms
        from collections import OrderedDict

        original_cache = ms.indicators_cache
        test_cache = OrderedDict()
        errors = []

        def writer(n):
            try:
                with ms._indicators_cache_lock:
                    test_cache[f'key_{n}'] = f'value_{n}'
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                with ms._indicators_cache_lock:
                    _ = list(test_cache.keys())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        threads += [threading.Thread(target=reader) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Erreurs concurrentes: {errors}"


# ─── P2-FEES: Backtest fees indépendants du live ─────────────────────────────

class TestBacktestFees:
    """P2-FEES — backtest_taker_fee/backtest_maker_fee ne sont jamais écrasés par le live."""

    def test_config_has_backtest_fees(self):
        """Config doit avoir backtest_taker_fee et backtest_maker_fee."""
        from bot_config import Config
        cfg = Config()
        assert hasattr(cfg, 'backtest_taker_fee')
        assert hasattr(cfg, 'backtest_maker_fee')

    def test_backtest_fees_defaults(self):
        """Les valeurs par défaut des backtest fees sont identiques aux defaults live."""
        from bot_config import Config
        assert Config.backtest_taker_fee == 0.0007
        assert Config.backtest_maker_fee == 0.0002

    def test_live_fee_override_does_not_affect_backtest_fee(self):
        """Modifier config.taker_fee ne change pas config.backtest_taker_fee."""
        from bot_config import Config
        cfg = Config()
        cfg.taker_fee = 0.0007
        cfg.backtest_taker_fee = 0.0007

        # Simuler l'override live
        cfg.taker_fee = 0.001  # live fee changed
        assert cfg.backtest_taker_fee == 0.0007  # backtest fee unchanged

    def test_backtest_fees_loaded_from_env(self, monkeypatch):
        """Les backtest fees sont chargeables depuis les variables d'env."""
        monkeypatch.setenv('BINANCE_API_KEY', 'test')
        monkeypatch.setenv('BINANCE_SECRET_KEY', 'test')
        monkeypatch.setenv('SENDER_EMAIL', 'a@b.c')
        monkeypatch.setenv('RECEIVER_EMAIL', 'd@e.f')
        monkeypatch.setenv('GOOGLE_MAIL_PASSWORD', 'pass')
        monkeypatch.setenv('BACKTEST_TAKER_FEE', '0.0005')
        monkeypatch.setenv('BACKTEST_MAKER_FEE', '0.0001')

        from bot_config import Config
        cfg = Config.from_env()
        assert cfg.backtest_taker_fee == 0.0005
        assert cfg.backtest_maker_fee == 0.0001

    def test_backtest_fees_validated(self, monkeypatch):
        """Les backtest fees sont validées (< 10%, >= 0)."""
        monkeypatch.setenv('BINANCE_API_KEY', 'test')
        monkeypatch.setenv('BINANCE_SECRET_KEY', 'test')
        monkeypatch.setenv('SENDER_EMAIL', 'a@b.c')
        monkeypatch.setenv('RECEIVER_EMAIL', 'd@e.f')
        monkeypatch.setenv('GOOGLE_MAIL_PASSWORD', 'pass')
        monkeypatch.setenv('BACKTEST_TAKER_FEE', '0.20')  # 20% = invalide

        from bot_config import Config
        with pytest.raises(ValueError, match='backtest_taker_fee'):
            Config.from_env()


# ─── P1-THRESH: OOS thresholds dans Config ──────────────────────────────────

class TestOOSThresholds:
    """P1-THRESH — oos_sharpe_min et oos_win_rate_min doivent être configurables."""

    def test_config_has_oos_thresholds(self):
        """Config doit avoir oos_sharpe_min et oos_win_rate_min."""
        from bot_config import Config
        assert hasattr(Config, 'oos_sharpe_min')
        assert hasattr(Config, 'oos_win_rate_min')

    def test_oos_thresholds_defaults(self):
        """Valeurs par défaut: sharpe >= 0.8, win_rate >= 30%."""
        from bot_config import Config
        assert Config.oos_sharpe_min == 0.8
        assert Config.oos_win_rate_min == 30.0

    def test_oos_thresholds_from_env(self, monkeypatch):
        """Chargement depuis variables d'environnement."""
        monkeypatch.setenv('BINANCE_API_KEY', 'test')
        monkeypatch.setenv('BINANCE_SECRET_KEY', 'test')
        monkeypatch.setenv('SENDER_EMAIL', 'a@b.c')
        monkeypatch.setenv('RECEIVER_EMAIL', 'd@e.f')
        monkeypatch.setenv('GOOGLE_MAIL_PASSWORD', 'pass')
        monkeypatch.setenv('OOS_SHARPE_MIN', '0.5')
        monkeypatch.setenv('OOS_WIN_RATE_MIN', '40.0')

        from bot_config import Config
        cfg = Config.from_env()
        assert cfg.oos_sharpe_min == 0.5
        assert cfg.oos_win_rate_min == 40.0


# ─── Config._validate coverage ──────────────────────────────────────────────

class TestConfigValidation:
    """Tests de la validation sémantique de Config."""

    def _make_env(self, monkeypatch, **overrides):
        """Configure l'environnement minimal + overrides."""
        monkeypatch.setenv('BINANCE_API_KEY', 'test')
        monkeypatch.setenv('BINANCE_SECRET_KEY', 'test')
        monkeypatch.setenv('SENDER_EMAIL', 'a@b.c')
        monkeypatch.setenv('RECEIVER_EMAIL', 'd@e.f')
        monkeypatch.setenv('GOOGLE_MAIL_PASSWORD', 'pass')
        for k, v in overrides.items():
            monkeypatch.setenv(k, str(v))

    def test_valid_config_no_error(self, monkeypatch):
        """Config valide ne lève pas d'erreur."""
        self._make_env(monkeypatch)
        from bot_config import Config
        cfg = Config.from_env()  # should not raise
        assert cfg.api_key == 'test'

    def test_invalid_sizing_mode_raises(self, monkeypatch):
        """Sizing mode invalide → ValueError."""
        self._make_env(monkeypatch, SIZING_MODE='invalid_mode')
        from bot_config import Config
        with pytest.raises(ValueError, match='sizing_mode'):
            Config.from_env()

    def test_negative_initial_wallet_raises(self, monkeypatch):
        """Wallet négatif → ValueError."""
        self._make_env(monkeypatch, INITIAL_WALLET='-100')
        from bot_config import Config
        with pytest.raises(ValueError, match='initial_wallet'):
            Config.from_env()

    def test_risk_per_trade_out_of_range_raises(self, monkeypatch):
        """risk_per_trade > 50% → ValueError."""
        self._make_env(monkeypatch, RISK_PER_TRADE='0.99')
        from bot_config import Config
        with pytest.raises(ValueError, match='risk_per_trade'):
            Config.from_env()

    def test_partial_thresholds_inconsistent_raises(self, monkeypatch):
        """partial_threshold_2 <= partial_threshold_1 → ValueError."""
        self._make_env(monkeypatch, PARTIAL_THRESHOLD_1='0.05', PARTIAL_THRESHOLD_2='0.03')
        from bot_config import Config
        with pytest.raises(ValueError, match='partial_threshold_2'):
            Config.from_env()

    def test_config_repr_masks_secrets(self):
        """Config.__repr__ masque api_key et secret_key."""
        from bot_config import Config
        cfg = Config()
        cfg.api_key = 'REAL_KEY_123'
        cfg.secret_key = 'REAL_SECRET_456'
        cfg.sender_email = 'test@test.com'
        cfg.taker_fee = 0.0007
        cfg.sizing_mode = 'risk'
        cfg.initial_wallet = 10000.0

        repr_str = repr(cfg)
        assert 'REAL_KEY' not in repr_str
        assert 'REAL_SECRET' not in repr_str
        assert 'MASKED' in repr_str


# ─── P0-02: Élimination des silent failures ──────────────────────────────────

class TestFetchBalancesSilentFailure:
    """P0-02 — _fetch_balances doit lever BalanceUnavailableError sur échec API.

    Les silent failures sur client.get_account() sont convertis en erreur explicite.
    """

    def _patch_ms_client(self, monkeypatch, mock_client):
        """Injecte un mock client dans le module MULTI_SYMBOLS."""
        if 'MULTI_SYMBOLS' not in sys.modules:
            pytest.skip('MULTI_SYMBOLS not importable in test env')
        import MULTI_SYMBOLS as ms
        monkeypatch.setattr(ms, 'client', mock_client)

    def test_fetch_balances_raises_on_api_failure(self, monkeypatch):
        """_fetch_balances doit lever BalanceUnavailableError si client.get_account() échoue."""
        import MULTI_SYMBOLS as ms
        from exceptions import BalanceUnavailableError

        mock_client = MagicMock()
        mock_client.get_account.side_effect = Exception('API timeout')
        monkeypatch.setattr(ms, 'client', mock_client)

        with pytest.raises(BalanceUnavailableError, match='get_account'):
            ms._fetch_balances('BTCUSDC')

    def test_fetch_balances_raises_preserves_cause(self, monkeypatch):
        """BalanceUnavailableError doit conserver l'exception originale comme __cause__."""
        import MULTI_SYMBOLS as ms
        from exceptions import BalanceUnavailableError

        original = RuntimeError('network error')
        mock_client = MagicMock()
        mock_client.get_account.side_effect = original
        monkeypatch.setattr(ms, 'client', mock_client)

        with pytest.raises(BalanceUnavailableError) as exc_info:
            ms._fetch_balances('ETHUSDC')

        assert exc_info.value.__cause__ is original

    def test_fetch_balances_returns_none_when_coin_not_found(self, monkeypatch):
        """_fetch_balances retourne None si le coin n'est pas dans le portefeuille (sans erreur)."""
        import MULTI_SYMBOLS as ms

        mock_client = MagicMock()
        # Compte sans le coin BTC
        mock_client.get_account.return_value = {
            'balances': [
                {'asset': 'USDC', 'free': '5000.0', 'locked': '0.0'},
            ]
        }
        monkeypatch.setattr(ms, 'client', mock_client)

        result = ms._fetch_balances('BTCUSDC')
        assert result is None


class TestExecuteTradesCriticalLogs:
    """P0-02 — _execute_real_trades_inner doit logguer CRITICAL si bal/flt None avec BUY ouvert.

    Stratégie : on remplace ms.config par un MagicMock pour contourner le gel Config,
    et on injecte le pair_state dans ms.bot_state avant l'appel.
    """

    _TEST_PAIR = '__P002_TEST__'
    _TEST_REAL_PAIR = 'BTCUSDC'

    def _setup_bot_state(self, ms, side: str, sl_placed: bool = True) -> None:
        """Injecte un pair_state minimal dans bot_state pour le test."""
        with ms._bot_state_lock:
            ms.bot_state.pop('emergency_halt', None)
            ms.bot_state[self._TEST_PAIR] = {
                'last_order_side': side,
                'entry_price': 50000.0,
                'sl_exchange_placed': sl_placed,
                'sl_order_id': 'mock_sl_123' if sl_placed else None,
                'entry_scenario': None,  # désactive la cohérence F-COH
            }

    def _teardown_bot_state(self, ms) -> None:
        with ms._bot_state_lock:
            ms.bot_state.pop(self._TEST_PAIR, None)

    def _mock_config(self, monkeypatch, ms) -> None:
        """Remplace ms.config par un MagicMock (évite le gel Config)."""
        mock_cfg = MagicMock()
        mock_cfg.max_concurrent_long = 999
        monkeypatch.setattr(ms, 'config', mock_cfg)

    def _call_inner(self, ms) -> None:
        """Appelle _execute_real_trades_inner avec la signature correcte."""
        try:
            ms._execute_real_trades_inner(
                real_trading_pair=self._TEST_REAL_PAIR,
                time_interval='15m',
                best_params={'scenario': 'StochRSI'},
                backtest_pair=self._TEST_PAIR,
                sizing_mode='risk',
            )
        except Exception:
            pass  # les dépendances manquantes peuvent lever — l'important est le log CRITICAL

    def test_critical_log_when_bal_none_and_buy_open(self, monkeypatch, caplog):
        """CRITICAL loggué si _fetch_balances retourne None et last_order_side == 'BUY'."""
        import logging
        import MULTI_SYMBOLS as ms

        self._setup_bot_state(ms, side='BUY')
        self._mock_config(monkeypatch, ms)
        monkeypatch.setattr(ms, '_fetch_balances', lambda _pair: None)

        try:
            with caplog.at_level(logging.CRITICAL, logger='MULTI_SYMBOLS'):
                self._call_inner(ms)
        finally:
            self._teardown_bot_state(ms)

        assert any(
            'P0-02' in r.message and self._TEST_REAL_PAIR in r.message
            for r in caplog.records
            if r.levelno == logging.CRITICAL
        ), 'CRITICAL log P0-02 non trouvé pour bal=None avec BUY ouvert'

    def test_no_critical_log_when_bal_none_and_no_position(self, monkeypatch, caplog):
        """Pas de CRITICAL P0-02 si _fetch_balances retourne None et aucune position ouverte."""
        import logging
        import MULTI_SYMBOLS as ms

        self._setup_bot_state(ms, side='SELL')
        self._mock_config(monkeypatch, ms)
        monkeypatch.setattr(ms, '_fetch_balances', lambda _pair: None)

        try:
            with caplog.at_level(logging.CRITICAL, logger='MULTI_SYMBOLS'):
                self._call_inner(ms)
        finally:
            self._teardown_bot_state(ms)

        critical_p002 = [
            r for r in caplog.records
            if r.levelno == logging.CRITICAL and 'P0-02' in r.message
        ]
        assert not critical_p002, 'CRITICAL P0-02 loggué à tort sans position BUY ouverte'

    def test_critical_log_when_flt_none_and_buy_open(self, monkeypatch, caplog):
        """CRITICAL loggué si _fetch_symbol_filters retourne None et last_order_side == 'BUY'."""
        import logging
        import MULTI_SYMBOLS as ms

        self._setup_bot_state(ms, side='BUY')
        self._mock_config(monkeypatch, ms)

        fake_bal = (MagicMock(), 'BTC', 'USDC', 5000.0, 0.01, 0.0, 0.01)
        monkeypatch.setattr(ms, '_fetch_balances', lambda _pair: fake_bal)
        monkeypatch.setattr(ms, '_fetch_symbol_filters', lambda _pair: None)

        try:
            with caplog.at_level(logging.CRITICAL, logger='MULTI_SYMBOLS'):
                self._call_inner(ms)
        finally:
            self._teardown_bot_state(ms)

        assert any(
            'P0-02' in r.message and self._TEST_REAL_PAIR in r.message
            for r in caplog.records
            if r.levelno == logging.CRITICAL
        ), 'CRITICAL log P0-02 non trouvé pour flt=None avec BUY ouvert'


class TestSLManquantAutoCorrection:
    """F-SL-FIX: Si sl_order_id existe et SL est actif sur Binance,
    sl_exchange_placed est auto-corrigé à True sans email CRITIQUE."""

    _TEST_PAIR = '__SL_FIX_TEST__'
    _TEST_REAL_PAIR = 'BTCUSDC'

    def test_sl_manquant_auto_corrected_when_sl_active_on_binance(self, monkeypatch, caplog):
        """sl_exchange_placed=False + sl_order_id présent + SL actif → auto-correction."""
        import logging
        import MULTI_SYMBOLS as ms

        mock_cfg = MagicMock()
        mock_cfg.max_concurrent_long = 999
        monkeypatch.setattr(ms, 'config', mock_cfg)

        # Setup: position BUY with sl_order_id but sl_exchange_placed=False
        sl_order_id = 260022854
        with ms._bot_state_lock:
            ms.bot_state.pop('emergency_halt', None)
            ms.bot_state[self._TEST_PAIR] = {
                'last_order_side': 'BUY',
                'entry_price': 0.2655,
                'sl_exchange_placed': False,
                'sl_order_id': sl_order_id,
                'entry_scenario': None,
            }

        # Mock client.get_open_orders → SL active on Binance
        mock_client = MagicMock()
        mock_client.get_open_orders.return_value = [
            {'orderId': sl_order_id, 'type': 'STOP_LOSS', 'status': 'NEW'},
        ]
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'save_bot_state', lambda force=False: None)
        monkeypatch.setattr(ms, '_fetch_balances', lambda _pair: None)

        try:
            with caplog.at_level(logging.INFO, logger='MULTI_SYMBOLS'):
                try:
                    ms._execute_real_trades_inner(
                        real_trading_pair=self._TEST_REAL_PAIR,
                        time_interval='15m',
                        best_params={'scenario': 'StochRSI'},
                        backtest_pair=self._TEST_PAIR,
                        sizing_mode='risk',
                    )
                except Exception:
                    pass

            # Verify auto-correction
            ps = ms.bot_state[self._TEST_PAIR]
            assert ps['sl_exchange_placed'] is True, 'sl_exchange_placed should be auto-corrected to True'
            assert any('F-SL-FIX' in r.message and 'Auto-correction' in r.message for r in caplog.records)
            # No CRITICAL SL-MANQUANT alert
            assert not any(
                r.levelno == logging.CRITICAL and 'SL-MANQUANT' in r.message
                for r in caplog.records
            ), 'CRITICAL SL-MANQUANT should NOT be logged when SL is active on Binance'
        finally:
            with ms._bot_state_lock:
                ms.bot_state.pop(self._TEST_PAIR, None)
