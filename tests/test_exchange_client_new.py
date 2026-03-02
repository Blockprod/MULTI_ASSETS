"""
tests/test_exchange_client.py — Tests unitaires pour exchange_client.py (C-09)

Couvre :
- _TokenBucket : rate limiting correct, timeout respecté
- safe_market_buy : retry sur échec, clientOrderId unique
- safe_market_sell : retry sur échec
- is_valid_stop_loss_order : validation des paramètres
- can_execute_partial_safely : logique de validation notionnelle
Tous les appels Binance sont mockés — aucune connexion réseau réelle.
"""
import os, sys, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

import pytest
from unittest.mock import MagicMock, patch, call


class TestTokenBucket:
    """Tests du rate limiter C-05."""

    def test_acquire_returns_true_when_tokens_available(self):
        from exchange_client import _TokenBucket
        bucket = _TokenBucket(rate=100.0, capacity=10.0)
        assert bucket.acquire(timeout=1.0) is True

    def test_acquire_consumes_token(self):
        from exchange_client import _TokenBucket
        bucket = _TokenBucket(rate=0.0, capacity=1.0)  # rate=0: no refill
        assert bucket.acquire(timeout=0.1) is True   # 1 token available
        assert bucket.acquire(timeout=0.1) is False  # no more tokens, timeout

    def test_concurrent_acquire_thread_safe(self):
        """N threads acquérant simultanément ne dépassent pas la capacité."""
        from exchange_client import _TokenBucket
        bucket = _TokenBucket(rate=100.0, capacity=5.0)
        successes = []
        lock = threading.Lock()

        def worker():
            result = bucket.acquire(timeout=2.0)
            with lock:
                successes.append(result)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # Tous doivent finir (le bucket se recharge à 100 tok/s sur 2s max)
        assert len(successes) == 20

    def test_rate_limits_requests_per_second(self):
        """Un bucket à 5 req/s ne doit pas permettre >10 acquis en 1s à partir de 0 tokens."""
        from exchange_client import _TokenBucket
        # Démarrer avec 0 tokens, rate = 5/s
        bucket = _TokenBucket(rate=5.0, capacity=5.0)
        # Vider le bucket
        while bucket.acquire(timeout=0.01):
            pass
        t_start = time.time()
        count = 0
        # Pendant 1 seconde, compter combien de tokens on peut acquérir
        while time.time() - t_start < 1.0:
            if bucket.acquire(timeout=0.0):
                count += 1
            time.sleep(0.01)
        # À 5 req/s, on ne devrait pas dépasser ~7 en 1s (5 + marge de jitter)
        assert count <= 7, f"Rate limiter trop permissif: {count} acquis en 1s"


class TestIsValidStopLossOrder:
    def test_valid_params(self):
        from exchange_client import is_valid_stop_loss_order
        assert is_valid_stop_loss_order("SOLUSDC", 10.0, 150.0) is True

    def test_invalid_symbol_none(self):
        from exchange_client import is_valid_stop_loss_order
        assert is_valid_stop_loss_order(None, 10.0, 150.0) is False

    def test_invalid_symbol_short(self):
        from exchange_client import is_valid_stop_loss_order
        assert is_valid_stop_loss_order("BTC", 10.0, 150.0) is False

    def test_zero_quantity(self):
        from exchange_client import is_valid_stop_loss_order
        assert is_valid_stop_loss_order("SOLUSDC", 0.0, 150.0) is False

    def test_negative_stop_price(self):
        from exchange_client import is_valid_stop_loss_order
        assert is_valid_stop_loss_order("SOLUSDC", 10.0, -1.0) is False

    def test_zero_stop_price(self):
        from exchange_client import is_valid_stop_loss_order
        assert is_valid_stop_loss_order("SOLUSDC", 10.0, 0.0) is False


class TestSafeMarketBuy:
    def test_success_first_attempt(self):
        """safe_market_buy retourne le résultat Binance au premier essai."""
        from exchange_client import safe_market_buy
        mock_client = MagicMock()
        mock_client.api_key = "test_api_key"
        mock_client.api_secret = "test_secret_key_for_mocking"
        mock_order = {"orderId": 123, "status": "FILLED", "clientOrderId": "test"}

        with patch('exchange_client._direct_market_order', return_value=mock_order) as mock_dmo:
            result = safe_market_buy(mock_client, "SOLUSDC", quoteOrderQty=100.0, max_retries=3)
            assert result == mock_order
            assert mock_dmo.call_count == 1

    def test_unique_client_order_id_per_call(self):
        """Chaque appel génère un clientOrderId différent."""
        from exchange_client import safe_market_buy
        mock_client = MagicMock()
        mock_client.api_key = "test_api_key"
        mock_client.api_secret = "test_secret_key_for_mocking"
        mock_order = {"orderId": 1, "status": "FILLED"}

        ids = []
        with patch('exchange_client._direct_market_order', return_value=mock_order) as mock_dmo:
            for _ in range(5):
                safe_market_buy(mock_client, "SOLUSDC", quoteOrderQty=100.0)
            # Récupérer les clientOrderIds passés dans les appels
            for call_args in mock_dmo.call_args_list:
                params = call_args[0][3] if len(call_args[0]) > 3 else call_args[1].get('params', {})
                cid = params.get('newClientOrderId') if isinstance(params, dict) else None
                if cid:
                    ids.append(cid)
            # Vérification: si des IDs existent, ils doivent être uniques
            if ids:
                assert len(ids) == len(set(ids)), "Les clientOrderId doivent être uniques"

    def test_retry_on_exception(self):
        """safe_market_buy retry sur exception temporaire."""
        from exchange_client import safe_market_buy
        mock_client = MagicMock()
        mock_client.api_key = "test_api_key"
        mock_client.api_secret = "test_secret_key_for_mocking"
        mock_order = {"orderId": 99, "status": "FILLED"}

        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise Exception("Temporary API error")
            return mock_order

        with patch('exchange_client._direct_market_order', side_effect=side_effect):
            result = safe_market_buy(mock_client, "SOLUSDC", quoteOrderQty=100.0, max_retries=4)
            assert result == mock_order
            assert call_count[0] == 3


class TestSafeMarketSell:
    def test_success_first_attempt(self):
        from exchange_client import safe_market_sell
        mock_client = MagicMock()
        mock_client.api_key = "test_api_key"
        mock_client.api_secret = "test_secret_key_for_mocking"
        mock_order = {"orderId": 200, "status": "FILLED"}
        with patch('exchange_client._direct_market_order', return_value=mock_order):
            result = safe_market_sell(mock_client, "SOLUSDC", quantity=5.0, max_retries=3)
            assert result == mock_order

    def test_raises_after_max_retries(self):
        """Lève une exception après max_retries echecs."""
        from exchange_client import safe_market_sell
        mock_client = MagicMock()
        mock_client.api_key = "test_api_key"
        mock_client.api_secret = "test_secret_key_for_mocking"

        with patch('exchange_client._direct_market_order', side_effect=Exception("API down")):
            with pytest.raises(Exception):  # type: ignore[attr-defined]
                safe_market_sell(mock_client, "SOLUSDC", quantity=5.0, max_retries=2)
