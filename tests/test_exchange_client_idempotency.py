"""
tests/test_exchange_client_idempotency.py — Idempotency, retry & robustness tests

Covers:
- P0-IDEM: safe_market_buy / safe_market_sell reuse the same clientOrderId across retries
- _request() retry logic for -1021, generic exceptions, and backoff timing
- _TokenBucket rate-limit edge cases
- get_symbol_filters caching / error fallback
- get_all_tickers_cached TTL and thread safety
- place_exchange_stop_loss retry vs business-error behavior
- get_spot_balance_usdc error propagation (P0-02)
"""
import os
import sys
import time
import threading

import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_mock_client(**overrides):
    """Create a lightweight mock matching BinanceFinalClient surface."""
    m = MagicMock()
    m.api_key = "test_api_key"
    m.api_secret = "test_secret_key_for_hmac_compat"
    m._server_time_offset = -2000
    m._sync_server_time = MagicMock()
    m.get_symbol_ticker.return_value = {'price': '100.00'}
    m.get_exchange_info.return_value = {
        'symbols': [{
            'symbol': 'SOLUSDC',
            'filters': [
                {'filterType': 'LOT_SIZE', 'minQty': '0.01', 'stepSize': '0.01'},
                {'filterType': 'MIN_NOTIONAL', 'minNotional': '10.0'},
            ]
        }]
    }
    m.get_symbol_info.return_value = {
        'symbol': 'SOLUSDC',
        'filters': [
            {'filterType': 'LOT_SIZE', 'minQty': '0.01', 'stepSize': '0.01'},
            {'filterType': 'MIN_NOTIONAL', 'minNotional': '10.0'},
            {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
        ]
    }
    for k, v in overrides.items():
        setattr(m, k, v)
    return m


FILLED_ORDER = {"orderId": 12345, "status": "FILLED", "clientOrderId": "bot-xxx"}


# ══════════════════════════════════════════════════════════════════════════════
# 1.  IDEMPOTENCY — same clientOrderId across retries  (P0-IDEM)
# ══════════════════════════════════════════════════════════════════════════════

class TestIdempotencyClientOrderId:
    """The *same* newClientOrderId must be sent on every retry of a single
    safe_market_buy / safe_market_sell call.  Binance uses this to de-dup."""

    @patch('exchange_client.random.random', return_value=0.0)
    @patch('exchange_client.time.sleep', return_value=None)
    def test_safe_market_buy_same_id_on_retries(self, mock_sleep, mock_rand):
        """All retry attempts within one safe_market_buy call share the same clientOrderId."""
        from exchange_client import safe_market_buy

        client = _make_mock_client()
        captured_ids = []
        call_count = [0]

        def capture_id(*args, **kwargs):
            call_count[0] += 1
            cid = kwargs.get('client_id')
            captured_ids.append(cid)
            if call_count[0] < 3:
                raise Exception("transient network error")
            return FILLED_ORDER

        with patch('exchange_client._direct_market_order', side_effect=capture_id):
            result = safe_market_buy(client, "SOLUSDC", quoteOrderQty=100.0, max_retries=4)

        assert result == FILLED_ORDER
        assert len(captured_ids) == 3
        # All IDs must be identical (idempotent)
        assert len(set(captured_ids)) == 1, (
            f"clientOrderId changed across retries: {captured_ids}"
        )

    @patch('exchange_client.random.random', return_value=0.0)
    @patch('exchange_client.time.sleep', return_value=None)
    def test_safe_market_sell_same_id_on_retries(self, mock_sleep, mock_rand):
        """All retry attempts within one safe_market_sell call share the same clientOrderId."""
        from exchange_client import safe_market_sell

        client = _make_mock_client()
        captured_ids = []
        call_count = [0]

        def capture_id(*args, **kwargs):
            call_count[0] += 1
            cid = kwargs.get('client_id')
            captured_ids.append(cid)
            if call_count[0] < 2:
                raise Exception("transient error")
            return FILLED_ORDER

        with patch('exchange_client._direct_market_order', side_effect=capture_id):
            result = safe_market_sell(client, "SOLUSDC", quantity=5.0, max_retries=4)

        assert result == FILLED_ORDER
        assert len(captured_ids) == 2
        assert len(set(captured_ids)) == 1

    @patch('exchange_client.random.random', return_value=0.0)
    @patch('exchange_client.time.sleep', return_value=None)
    def test_different_calls_get_different_ids(self, mock_sleep, mock_rand):
        """Two separate safe_market_buy calls must have *different* clientOrderIds."""
        from exchange_client import safe_market_buy

        client = _make_mock_client()
        captured_ids = []

        def capture_id(*args, **kwargs):
            captured_ids.append(kwargs.get('client_id'))
            return FILLED_ORDER

        with patch('exchange_client._direct_market_order', side_effect=capture_id):
            safe_market_buy(client, "SOLUSDC", quoteOrderQty=50.0, max_retries=1)
            safe_market_buy(client, "SOLUSDC", quoteOrderQty=50.0, max_retries=1)

        assert len(captured_ids) == 2
        assert captured_ids[0] != captured_ids[1], (
            "Distinct buy calls must produce distinct clientOrderIds"
        )


class TestGenerateClientOrderId:
    """_generate_client_order_id must produce unique, prefixed IDs."""

    def test_unique_ids(self):
        from exchange_client import _generate_client_order_id
        ids = {_generate_client_order_id('buy') for _ in range(100)}
        assert len(ids) == 100

    def test_prefix_present(self):
        from exchange_client import _generate_client_order_id
        cid = _generate_client_order_id('sell')
        assert cid.startswith('sell-')

    def test_default_prefix(self):
        from exchange_client import _generate_client_order_id
        cid = _generate_client_order_id()
        assert cid.startswith('bot-')


# ══════════════════════════════════════════════════════════════════════════════
# 2.  _request() — retry logic and error handling
# ══════════════════════════════════════════════════════════════════════════════

class TestRequestRetryLogic:
    """Tests on BinanceFinalClient._request() retry / error paths."""

    def _make_real_client(self):
        """Instantiate BinanceFinalClient with sync mocked out."""
        with patch('exchange_client.BinanceFinalClient._perform_ultra_robust_sync'):
            from exchange_client import BinanceFinalClient
            c = BinanceFinalClient.__new__(BinanceFinalClient)
            c.api_key = "k"
            c.api_secret = "s"
            c.session = None  # contrat __del__: close_connection attend cet attribut
            c._server_time_offset = -2000
            c._last_sync = time.time()
            c._error_count = 0
            c._max_errors = 5
            c._sync_interval = 180
        return c

    @patch('exchange_client._api_rate_limiter')
    @patch('exchange_client.time.sleep', return_value=None)
    def test_retry_on_1021_resyncs_and_retries(self, mock_sleep, mock_limiter):
        """A -1021 error triggers resync and retry up to 3 times."""
        from binance.exceptions import BinanceAPIException
        from exchange_client import BinanceFinalClient

        client = self._make_real_client()
        mock_limiter.acquire.return_value = True

        exc_1021 = BinanceAPIException.__new__(BinanceAPIException)
        exc_1021.code = -1021
        exc_1021.message = "Timestamp outside recvWindow"

        with patch.object(BinanceFinalClient, '_perform_ultra_robust_sync') as mock_sync, \
             patch('binance.client.Client._request', side_effect=exc_1021):
            with pytest.raises(BinanceAPIException):
                client._request('GET', '/api/v3/order', True)
            # Must have resynced on every -1021 attempt
            assert mock_sync.call_count == 3  # one per retry

    @patch('exchange_client._api_rate_limiter')
    @patch('exchange_client.time.sleep', return_value=None)
    def test_non_1021_api_error_not_retried(self, mock_sleep, mock_limiter):
        """-1101 or other API errors are raised immediately, not retried."""
        from binance.exceptions import BinanceAPIException

        client = self._make_real_client()
        mock_limiter.acquire.return_value = True

        exc_1101 = BinanceAPIException.__new__(BinanceAPIException)
        exc_1101.code = -1101
        exc_1101.message = "Duplicate recvWindow"

        with patch('binance.client.Client._request', side_effect=exc_1101) as mock_parent:
            with pytest.raises(BinanceAPIException):
                client._request('GET', '/api/v3/order', True)
            # Should NOT retry — only 1 call to parent
            assert mock_parent.call_count == 1

    @patch('exchange_client._api_rate_limiter')
    @patch('schedule.next_run', return_value=None)
    @patch('exchange_client.time.sleep', return_value=None)
    def test_generic_exception_retried_with_backoff(self, mock_sleep, mock_next, mock_limiter):
        """Generic exceptions are retried with exponential backoff."""
        client = self._make_real_client()
        mock_limiter.acquire.return_value = True

        call_count = [0]

        def parent_side_effect(*a, **kw):
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError("network blip")
            return {"result": "ok"}

        with patch('binance.client.Client._request', side_effect=parent_side_effect):
            result = client._request('GET', '/api/v3/time', False)

        assert result == {"result": "ok"}
        assert call_count[0] == 3
        # Backoff sleeps called (2 retries = 2 sleeps)
        assert mock_sleep.call_count >= 2

    @patch('exchange_client._api_rate_limiter')
    @patch('exchange_client.time.sleep', return_value=None)
    def test_request_success_decrements_error_count(self, mock_sleep, mock_limiter):
        """A successful _request() decrements _error_count."""
        client = self._make_real_client()
        client._error_count = 3
        mock_limiter.acquire.return_value = True

        with patch('binance.client.Client._request', return_value={"ok": True}):
            client._request('GET', '/api/v3/time', False)

        assert client._error_count == 2  # decremented by 1

    @patch('exchange_client._api_rate_limiter')
    def test_request_strips_recv_window_params(self, mock_limiter):
        """_request() sanitizes recvWindow from kwargs/params/data to avoid duplication."""
        client = self._make_real_client()
        mock_limiter.acquire.return_value = True

        with patch('binance.client.Client._request', return_value={"ok": True}) as mock_parent:
            client._request(
                'POST', '/api/v3/order', True,
                recvWindow=5000,
                params={'recvWindow': 5000, 'symbol': 'BTCUSDC'},
                data={'recvWindow': 5000},
            )
            # Parent was called — check that recvWindow was stripped
            _, call_kwargs = mock_parent.call_args
            assert 'recvWindow' not in call_kwargs
            if 'params' in call_kwargs:
                assert 'recvWindow' not in call_kwargs['params']
            if 'data' in call_kwargs:
                assert 'recvWindow' not in call_kwargs['data']


# ══════════════════════════════════════════════════════════════════════════════
# 3.  _TokenBucket — rate limiting edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestTokenBucketEdgeCases:

    def test_timeout_returns_false(self):
        """acquire() returns False when bucket is empty and timeout expires."""
        from exchange_client import _TokenBucket
        bucket = _TokenBucket(rate=0.0, capacity=1.0)
        bucket.acquire(timeout=0.01)  # drain the only token
        assert bucket.acquire(timeout=0.05) is False

    def test_refill_over_time(self):
        """Tokens refill proportionally to elapsed time."""
        from exchange_client import _TokenBucket
        bucket = _TokenBucket(rate=100.0, capacity=5.0)
        # Drain all
        for _ in range(5):
            bucket.acquire(timeout=0.01)
        # After draining, wait a bit for refill
        time.sleep(0.06)  # ~6 tokens at 100/s
        assert bucket.acquire(timeout=0.01) is True

    def test_capacity_ceiling(self):
        """Tokens never exceed capacity even after long idle."""
        from exchange_client import _TokenBucket
        # Use rate=0 so no tokens refill during the test
        bucket = _TokenBucket(rate=0.0, capacity=3.0)
        # Manually set _tokens to capacity (simulating long idle with refill capped)
        with bucket._lock:
            bucket._tokens = 3.0
        count = 0
        while bucket.acquire(timeout=0.001):
            count += 1
            if count > 10:
                break
        # Should not exceed capacity (3)
        assert count == 3, f"Capacity ceiling violated: got {count} tokens, expected 3"

    def test_concurrent_drain_no_overdraft(self):
        """Under heavy contention, total successful acquires <= capacity + refill."""
        from exchange_client import _TokenBucket
        bucket = _TokenBucket(rate=0.0, capacity=5.0)  # no refill
        successes = []
        lock = threading.Lock()

        def worker():
            ok = bucket.acquire(timeout=0.05)
            with lock:
                successes.append(ok)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        assert sum(successes) == 5  # exactly capacity, no overdraft


# ══════════════════════════════════════════════════════════════════════════════
# 4.  get_symbol_filters — caching, success, and error fallback
# ══════════════════════════════════════════════════════════════════════════════

class TestGetSymbolFilters:

    def test_returns_correct_filters(self):
        from exchange_client import get_symbol_filters
        client = _make_mock_client()
        client.get_symbol_info.return_value = {
            'symbol': 'SOLUSDC',
            'filters': [
                {'filterType': 'LOT_SIZE', 'minQty': '0.01', 'stepSize': '0.001'},
                {'filterType': 'MIN_NOTIONAL', 'minNotional': '5.0'},
            ]
        }
        result = get_symbol_filters(client, 'SOLUSDC')
        assert result['min_qty'] == Decimal('0.01')
        assert result['step_size'] == Decimal('0.001')
        assert result['min_notional'] == Decimal('5.0')

    def test_returns_defaults_on_missing_filters(self):
        """When filters are present but LOT_SIZE/MIN_NOTIONAL missing, defaults are used."""
        from exchange_client import get_symbol_filters
        client = _make_mock_client()
        client.get_symbol_info.return_value = {
            'symbol': 'SOLUSDC',
            'filters': [{'filterType': 'PRICE_FILTER', 'tickSize': '0.01'}]
        }
        result = get_symbol_filters(client, 'SOLUSDC')
        # Defaults
        assert result['min_qty'] == Decimal('0.001')
        assert result['step_size'] == Decimal('0.000001')
        assert result['min_notional'] == Decimal('10.0')

    def test_returns_fallback_dict_on_exception(self):
        """@log_exceptions returns the default_return dict when get_symbol_info raises."""
        from exchange_client import get_symbol_filters
        client = _make_mock_client()
        client.get_symbol_info.return_value = None  # triggers ValueError

        result = get_symbol_filters(client, 'UNKNOWNSYMBOL')
        # @log_exceptions(default_return=...) kicks in
        assert result == {'min_qty': None, 'step_size': None, 'min_notional': None}

    def test_symbol_info_api_exception(self):
        """Network error during get_symbol_info returns fallback dict."""
        from exchange_client import get_symbol_filters
        client = _make_mock_client()
        client.get_symbol_info.side_effect = Exception("API timeout")
        result = get_symbol_filters(client, 'SOLUSDC')
        assert result == {'min_qty': None, 'step_size': None, 'min_notional': None}


# ══════════════════════════════════════════════════════════════════════════════
# 5.  get_all_tickers_cached — TTL and thread safety
# ══════════════════════════════════════════════════════════════════════════════

class TestGetAllTickersCached:

    def setup_method(self):
        """Reset the module-level cache before each test."""
        import exchange_client as ec
        with ec._tickers_lock:
            ec._tickers_cache['data'] = None
            ec._tickers_cache['timestamp'] = 0.0

    def test_cache_hit_within_ttl(self):
        from exchange_client import get_all_tickers_cached
        client = _make_mock_client()
        client.get_all_tickers.return_value = [
            {'symbol': 'BTCUSDC', 'price': '50000.0'},
        ]
        # First call populates cache
        r1 = get_all_tickers_cached(client, cache_ttl=60)
        assert r1 == {'BTCUSDC': 50000.0}

        # Second call should NOT hit the API again
        client.get_all_tickers.return_value = [
            {'symbol': 'BTCUSDC', 'price': '99999.0'},
        ]
        r2 = get_all_tickers_cached(client, cache_ttl=60)
        assert r2 == {'BTCUSDC': 50000.0}  # stale value from cache
        assert client.get_all_tickers.call_count == 1

    def test_cache_miss_after_ttl(self):
        from exchange_client import get_all_tickers_cached

        client = _make_mock_client()
        client.get_all_tickers.return_value = [
            {'symbol': 'ETHUSDC', 'price': '3000.0'},
        ]
        get_all_tickers_cached(client, cache_ttl=0)  # TTL=0 → always expired

        client.get_all_tickers.return_value = [
            {'symbol': 'ETHUSDC', 'price': '3500.0'},
        ]
        r2 = get_all_tickers_cached(client, cache_ttl=0)
        assert r2 == {'ETHUSDC': 3500.0}
        assert client.get_all_tickers.call_count == 2

    def test_thread_safe_concurrent_access(self):
        """Multiple threads hitting the cache concurrently must not crash."""
        from exchange_client import get_all_tickers_cached

        client = _make_mock_client()
        client.get_all_tickers.return_value = [
            {'symbol': 'SOLUSDC', 'price': '150.0'},
        ]
        results = []
        lock = threading.Lock()

        def worker():
            r = get_all_tickers_cached(client, cache_ttl=60)
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(results) == 10
        for r in results:
            assert r == {'SOLUSDC': 150.0}


# ══════════════════════════════════════════════════════════════════════════════
# 6.  place_exchange_stop_loss — retry vs business error
# ══════════════════════════════════════════════════════════════════════════════

class TestPlaceExchangeStopLossRetry:

    @patch('exchange_client._api_rate_limiter')
    @patch('exchange_client.time.sleep', return_value=None)
    @patch('exchange_client.random.uniform', return_value=0.0)
    def test_retries_on_network_error(self, mock_rand, mock_sleep, mock_limiter):
        from exchange_client import place_exchange_stop_loss
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()

        call_count = [0]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'orderId': 999, 'status': 'NEW'}

        def side_effect(*a, **kw):
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError("timeout")
            return mock_resp

        with patch('exchange_client.requests.post', side_effect=side_effect):
            result = place_exchange_stop_loss(client, 'SOLUSDC', '5.0', 95.0)

        assert result['orderId'] == 999
        assert call_count[0] == 3

    @patch('exchange_client._api_rate_limiter')
    def test_does_not_retry_on_order_error(self, mock_limiter):
        """OrderError (business error) must propagate immediately, no retry."""
        from exchange_client import place_exchange_stop_loss
        from exceptions import OrderError
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {'code': -2010, 'msg': 'Insufficient balance'}

        with patch('exchange_client.requests.post', return_value=mock_resp):
            with pytest.raises(OrderError):
                place_exchange_stop_loss(client, 'SOLUSDC', '5.0', 95.0)

    @patch('exchange_client._api_rate_limiter')
    def test_success_first_attempt(self, mock_limiter):
        from exchange_client import place_exchange_stop_loss
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'orderId': 42, 'status': 'NEW'}

        with patch('exchange_client.requests.post', return_value=mock_resp):
            result = place_exchange_stop_loss(client, 'SOLUSDC', '5.0', 95.0)
        assert result['orderId'] == 42

    @patch('exchange_client._api_rate_limiter')
    @patch('exchange_client.time.sleep', return_value=None)
    @patch('exchange_client.random.uniform', return_value=0.0)
    def test_raises_after_all_retries_exhausted(self, mock_rand, mock_sleep, mock_limiter):
        from exchange_client import place_exchange_stop_loss
        from exceptions import OrderError
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()

        with patch('exchange_client.requests.post', side_effect=ConnectionError("down")):
            with pytest.raises(OrderError, match="tentatives échouées"):
                place_exchange_stop_loss(client, 'SOLUSDC', '5.0', 95.0)


# ══════════════════════════════════════════════════════════════════════════════
# 7.  get_spot_balance_usdc — P0-02 error propagation
# ══════════════════════════════════════════════════════════════════════════════

class TestGetSpotBalanceUsdc:

    def setup_method(self):
        """Reset ticker cache so each test is independent."""
        import exchange_client as ec
        with ec._tickers_lock:
            ec._tickers_cache['data'] = None
            ec._tickers_cache['timestamp'] = 0.0

    def test_raises_balance_unavailable_on_api_failure(self):
        """P0-02: must raise BalanceUnavailableError, never return 0.0."""
        from exchange_client import get_spot_balance_usdc
        from exceptions import BalanceUnavailableError

        client = _make_mock_client()
        client.get_account.side_effect = Exception("API connection failed")

        with pytest.raises(BalanceUnavailableError):
            get_spot_balance_usdc(client)

    def test_calculates_usdc_balance(self):
        from exchange_client import get_spot_balance_usdc

        client = _make_mock_client()
        client.get_account.return_value = {
            'balances': [
                {'asset': 'USDC', 'free': '5000.0', 'locked': '1000.0'},
                {'asset': 'BTC', 'free': '0.1', 'locked': '0.0'},
            ]
        }
        client.get_all_tickers.return_value = [
            {'symbol': 'BTCUSDC', 'price': '50000.0'},
        ]
        result = get_spot_balance_usdc(client)
        # 6000 USDC + 0.1*50000 = 11000
        assert abs(result - 11000.0) < 1.0

    def test_skips_zero_balance_assets(self):
        from exchange_client import get_spot_balance_usdc

        client = _make_mock_client()
        client.get_account.return_value = {
            'balances': [
                {'asset': 'USDC', 'free': '100.0', 'locked': '0.0'},
                {'asset': 'DOGE', 'free': '0.0', 'locked': '0.0'},
            ]
        }
        client.get_all_tickers.return_value = []
        result = get_spot_balance_usdc(client)
        assert result == 100.0


# ══════════════════════════════════════════════════════════════════════════════
# 8.  safe_market_buy / safe_market_sell — exhaustion & min notional
# ══════════════════════════════════════════════════════════════════════════════

class TestSafeMarketBuyExhaustion:

    @patch('exchange_client.random.random', return_value=0.0)
    @patch('exchange_client.time.sleep', return_value=None)
    def test_raises_after_max_retries(self, mock_sleep, mock_rand):
        from exchange_client import safe_market_buy
        client = _make_mock_client()

        with patch('exchange_client._direct_market_order', side_effect=Exception("fail")):
            with pytest.raises(Exception, match="fail"):
                safe_market_buy(client, "SOLUSDC", quoteOrderQty=100.0, max_retries=2)

    def test_below_min_notional_blocked(self):
        """Order below MIN_NOTIONAL must be rejected before any API call."""
        from exchange_client import safe_market_buy
        client = _make_mock_client()
        # min_notional is 10.0 from mock, order of 5.0 should be blocked
        with pytest.raises(ValueError, match="min_notional"):
            safe_market_buy(client, "SOLUSDC", quoteOrderQty=5.0)


class TestSafeMarketSellExhaustion:

    @patch('exchange_client.random.random', return_value=0.0)
    @patch('exchange_client.time.sleep', return_value=None)
    def test_raises_after_max_retries(self, mock_sleep, mock_rand):
        from exchange_client import safe_market_sell
        client = _make_mock_client()

        with patch('exchange_client._direct_market_order', side_effect=Exception("fail")):
            with pytest.raises(Exception, match="fail"):
                safe_market_sell(client, "SOLUSDC", quantity=5.0, max_retries=2)

    def test_sell_alert_called_on_success(self):
        """send_alert callback is forwarded to _direct_market_order."""
        from exchange_client import safe_market_sell
        client = _make_mock_client()
        alert_fn = MagicMock()

        with patch('exchange_client._direct_market_order', return_value=FILLED_ORDER) as mock_dmo:
            safe_market_sell(client, "SOLUSDC", quantity=5.0, send_alert=alert_fn)
            _, kwargs = mock_dmo.call_args
            assert kwargs.get('send_alert') is alert_fn


# ══════════════════════════════════════════════════════════════════════════════
# 9.  BinanceFinalClient — timestamp synchronisation
# ══════════════════════════════════════════════════════════════════════════════

class TestTimestampSync:

    def test_ultra_safe_timestamp_triggers_resync_on_error(self):
        """If _error_count > 0, _get_ultra_safe_timestamp must resync."""
        with patch('exchange_client.BinanceFinalClient._perform_ultra_robust_sync'):
            from exchange_client import BinanceFinalClient
            c = BinanceFinalClient.__new__(BinanceFinalClient)
            c.session = None  # contrat __del__
            c._server_time_offset = -2000
            c._last_sync = time.time()
            c._error_count = 1
            c._sync_interval = 180

        with patch.object(c, '_perform_ultra_robust_sync') as mock_sync:
            ts = c._get_ultra_safe_timestamp()
            mock_sync.assert_called_once()
            assert isinstance(ts, int)

    def test_ultra_safe_timestamp_resyncs_after_60s(self):
        """Periodic resync triggers when last sync > 60s ago."""
        with patch('exchange_client.BinanceFinalClient._perform_ultra_robust_sync'):
            from exchange_client import BinanceFinalClient
            c = BinanceFinalClient.__new__(BinanceFinalClient)
            c.session = None  # contrat __del__
            c._server_time_offset = -2000
            c._last_sync = time.time() - 120  # 2 minutes ago
            c._error_count = 0
            c._sync_interval = 180

        with patch.object(c, '_perform_ultra_robust_sync') as mock_sync:
            c._get_ultra_safe_timestamp()
            mock_sync.assert_called_once()

    def test_sync_clamps_offset(self):
        """_perform_ultra_robust_sync clamps offset within [-10000, 1000]."""
        with patch('exchange_client.BinanceFinalClient._perform_ultra_robust_sync'):
            from exchange_client import BinanceFinalClient
            c = BinanceFinalClient.__new__(BinanceFinalClient)
            c.session = None  # contrat __del__
            c._server_time_offset = 0
            c._last_sync = 0
            c._error_count = 0
            c._max_errors = 5

        now_ms = int(time.time() * 1000)
        # Simulate server very far ahead → offset would be huge positive
        c.get_server_time = MagicMock(return_value={'serverTime': now_ms + 50000})
        c._perform_ultra_robust_sync()
        assert c._server_time_offset <= 1000  # clamped

    def test_sync_fallback_on_failure(self):
        """If get_server_time fails, offset falls back to -2000."""
        with patch('exchange_client.BinanceFinalClient._perform_ultra_robust_sync'):
            from exchange_client import BinanceFinalClient
            c = BinanceFinalClient.__new__(BinanceFinalClient)
            c.session = None  # contrat __del__
            c._server_time_offset = 0
            c._last_sync = 0
            c._error_count = 0
            c._max_errors = 5

        c.get_server_time = MagicMock(side_effect=Exception("network error"))
        c._perform_ultra_robust_sync()
        assert c._server_time_offset == -2000


# ══════════════════════════════════════════════════════════════════════════════
# 10.  can_execute_partial_safely — notional validation
# ══════════════════════════════════════════════════════════════════════════════

class TestCanExecutePartialSafely:

    def test_large_position_allows_partials(self):
        from exchange_client import can_execute_partial_safely
        # 100 coins * $100 = $10000 total, 20% remaining = $2000 >> $11 min
        assert can_execute_partial_safely(100.0, 100.0, 10.0) is True

    def test_small_position_blocks_partials(self):
        from exchange_client import can_execute_partial_safely
        # 1 coin * $10 = $10 total, 20% remaining = $2 < $11 min
        assert can_execute_partial_safely(1.0, 10.0, 10.0) is False

    def test_edge_case_exactly_at_threshold(self):
        from exchange_client import can_execute_partial_safely
        # Need: 20% * balance * price >= min_notional * 1.1
        # 0.2 * balance * price = min_notional * 1.1
        # balance * price = 5.5 * min_notional
        # With min_notional=10: balance*price = 55
        # balance = 55/100 = 0.55  → remaining = 0.11 * 100 = 11.0 ≥ 11.0
        assert can_execute_partial_safely(0.55, 100.0, 10.0) is True
        # Just below
        assert can_execute_partial_safely(0.54, 100.0, 10.0) is False
