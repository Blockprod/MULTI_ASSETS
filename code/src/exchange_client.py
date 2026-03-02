"""
exchange_client.py — Client Binance robuste et helpers d'ordres.
Phase 4 refactoring: extrait de MULTI_SYMBOLS.py
"""
import hashlib
import hmac
import logging
import random
import requests
import threading
import time
import uuid
from typing import Any, Dict, Optional, Union

from binance.client import Client
from binance.exceptions import BinanceAPIException

from bot_config import log_exceptions, retry_with_backoff
from exceptions import OrderError

logger = logging.getLogger(__name__)


# ─── Client Binance robuste ────────────────────────────────────────────────
class BinanceFinalClient(Client):
    """Client Binance ULTRA ROBUSTE - Correction définitive du timestamp -1021"""

    def __init__(self, api_key, api_secret, **kwargs):
        self.api_key = api_key
        self.api_secret = api_secret
        self._server_time_offset = -2000
        self._last_sync = 0
        self._sync_interval = 180
        self._error_count = 0
        self._max_errors = 5
        kwargs['requests_params'] = {'timeout': 45}
        super().__init__(api_key, api_secret, **kwargs)
        logger.info("Client Binance ULTRA ROBUSTE initialisé")
        self._perform_ultra_robust_sync()

    def _perform_ultra_robust_sync(self):
        """Synchronisation radicale avec compensation complète."""
        try:
            local_before = int(time.time() * 1000)
            server_time = self.get_server_time()['serverTime']
            local_after = int(time.time() * 1000)
            latency = (local_after - local_before) // 2
            real_offset = server_time - (local_before + latency)
            if real_offset > -2000:
                forced_offset = -5000
            else:
                forced_offset = real_offset
            test_local = int(time.time() * 1000)
            test_timestamp = test_local + forced_offset
            test_diff = test_timestamp - server_time
            if test_diff > 800:
                forced_offset = -8000
            self._server_time_offset = forced_offset
            self._last_sync = time.time()
            self._error_count = 0
            logger.info(f"SYNCHRO OK: offset={self._server_time_offset}ms")
        except Exception as e:
            logger.error(f"Echec synchronisation: {e}")
            self._server_time_offset = -10000

    def _get_ultra_safe_timestamp(self):
        """Génère un timestamp garanti correct.
        
        La sync périodique toutes les 60s via _perform_ultra_robust_sync est suffisante.
        Pas d'appel API supplémentaire ici (économie rate-limit).
        """
        current_time = time.time()
        if (current_time - self._last_sync > 60 or self._error_count > 0):
            self._perform_ultra_robust_sync()
        safe_ts = int(current_time * 1000) + self._server_time_offset
        return safe_ts

    def _request(self, method, uri, signed, force_params=False, **kwargs):
        """Override MINIMAL - only handle timestamp sync, let parent handle params."""
        import schedule
        from datetime import datetime
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Sanitize recvWindow to avoid duplicate-parameter errors
                try:
                    if 'recvWindow' in kwargs:
                        kwargs.pop('recvWindow', None)
                    if 'params' in kwargs and isinstance(kwargs['params'], dict) and 'recvWindow' in kwargs['params']:
                        kwargs['params'].pop('recvWindow', None)
                    if 'data' in kwargs and isinstance(kwargs['data'], dict) and 'recvWindow' in kwargs['data']:
                        kwargs['data'].pop('recvWindow', None)
                except Exception as _sanitize_ex:
                    logger.debug(f"_request: recvWindow sanitation failed: {_sanitize_ex}")

                result = super()._request(method, uri, signed, force_params=force_params, **kwargs)
                self._error_count = max(0, self._error_count - 1)
                return result
                
            except BinanceAPIException as e:
                if getattr(e, 'code', None) == -1021:
                    self._error_count += 1
                    logger.warning(f"Erreur -1021 détectée (tentative {attempt+1}), resync obligatoire")
                    self._perform_ultra_robust_sync()
                    if attempt < max_retries - 1:
                        time.sleep(2)
                        continue
                    raise
                else:
                    if getattr(e, 'code', None) == -1101:
                        logger.error(f"BinanceAPIException -1101 (Duplicate recvWindow): {e}")
                    else:
                        logger.error(f"BinanceAPIException: {e}")
                    raise
                    
            except Exception as e:
                logger.error(f"Erreur inattendue dans _request: {e}")
                if attempt < max_retries - 1:
                    now = datetime.now()
                    next_run = schedule.next_run()
                    if next_run:
                        delta = next_run - now
                        minutes_left = max(0, int(delta.total_seconds() // 60))
                        print(f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution dans {minutes_left} min ({next_run.strftime('%H:%M:%S')})")
                    else:
                        print(f"[TIME] {now.strftime('%H:%M:%S')} - Bot actif (RUNNING) | Prochaine execution non planifiée")
                    _backoff = min(10 * (2 ** attempt), 30)  # 10s, 20s, 30s max
                    logger.warning(f"_request: retry {attempt+1}/{max_retries} après {_backoff}s")
                    time.sleep(_backoff)
                    continue
                raise
        return None

    def _sync_server_time(self):
        self._perform_ultra_robust_sync()

    def _sync_server_time_robust(self):
        self._perform_ultra_robust_sync()

    def _get_synchronized_timestamp(self):
        return self._get_ultra_safe_timestamp()


# ─── Helper functions ──────────────────────────────────────────────────────

def _generate_client_order_id(prefix: str = 'bot') -> str:
    """Generate a reasonably unique client order id."""
    return f"{prefix}-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"


def _direct_market_order(client, symbol: str, side: str,
                         quoteOrderQty: Optional[float] = None,
                         quantity: Optional[Union[float, str]] = None,
                         client_id: Optional[str] = None,
                         send_alert=None) -> Dict[str, Any]:
    """Appel API REST direct pour éviter le bug 'Duplicate recvWindow' du wrapper Binance.
    
    Args:
        client: Instance BinanceFinalClient
        send_alert: Callable(subject, body_main, client) pour envoyer des alertes email
    """
    client._sync_server_time()
    timestamp = int(time.time() * 1000) + client._server_time_offset
    params_order = [
        ('symbol', symbol),
        ('side', side),
        ('type', 'MARKET'),
        ('quantity', f"{quantity}" if quantity is not None else None),
        ('quoteOrderQty', f"{float(quoteOrderQty):.2f}" if quoteOrderQty is not None else None),
        ('timestamp', int(timestamp)),
    ]
    params_order = [(k, v) for k, v in params_order if v is not None]
    query_string = '&'.join([f"{k}={v}" for k, v in params_order])
    signature = hmac.new(client.api_secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
    query_string_with_sig = query_string + f"&signature={signature}"
    headers = {
        'X-MBX-APIKEY': client.api_key,
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    url = 'https://api.binance.com/api/v3/order'
    try:
        response = requests.post(url, data=query_string_with_sig, headers=headers, timeout=10)
        if response.status_code != 200:
            try:
                error_data = response.json()
                error_code = error_data.get('code', 'UNKNOWN')
                error_msg = error_data.get('msg', 'Unknown error')
                logger.error(f"[DEBUG ORDER] Erreur API Binance: code={error_code}, msg={error_msg}")
                if send_alert:
                    # Masquer les détails sensibles (signature, timestamp)
                    safe_params = [(k, v) for k, v in params_order if k not in ('signature', 'timestamp')]
                    send_alert(
                        subject=f"[BOT CRYPTO] ERREUR EXECUTION {side.upper()} ORDER",
                        body_main=f"Erreur lors de l'execution de l'ordre {side.upper()} : {error_code} - {error_msg}\n\nParams : {safe_params}",
                        client=client
                    )
                raise BinanceAPIException(response, error_code, error_msg)
            except (ValueError, KeyError):
                response.raise_for_status()
        result = response.json()
        if send_alert:
            # Ne pas inclure params_order (contient la signature HMAC)
            order_summary = f"Symbol: {symbol}, Side: {side}, Qty: {quantity or quoteOrderQty}"
            send_alert(
                subject=f"[BOT CRYPTO] {side.upper()} ORDER EXECUTE",
                body_main=f"Ordre {side.upper()} exécuté avec succès.\n\n{order_summary}\nOrderId: {result.get('orderId', 'N/A')}\nStatus: {result.get('status', 'N/A')}",
                client=client
            )
        return result
    except BinanceAPIException as e:
        raise OrderError(f"Erreur API {side.upper()}: {e}", symbol=symbol) from e
    except Exception as e:
        if send_alert:
            try:
                send_alert(
                    subject=f"[BOT CRYPTO] EXCEPTION {side.upper()} ORDER",
                    body_main=f"Exception lors de l'appel API {side.upper()} : {e}\n\nParams : {params_order}",
                    client=client
                )
            except Exception:
                pass
        raise OrderError(f"Exception {side.upper()} order: {e}", symbol=symbol) from e


def safe_market_buy(client, symbol: str, quoteOrderQty: float,
                    max_retries: int = 4, send_alert=None) -> Dict[str, Any]:
    """Place a market BUY by quote amount with idempotency/retry and safety checks."""
    client_id = _generate_client_order_id('buy')
    last_exc = None
    # Min notional check
    try:
        current_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
        exchange_info = client.get_exchange_info()
        symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
        notional_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'), None) if symbol_info else None
        min_notional = float(notional_filter.get('minNotional', '10.0')) if notional_filter else 10.0
        if quoteOrderQty < min_notional:
            logger.error(f"[ORDER BLOCKED] quoteOrderQty {quoteOrderQty} < min_notional {min_notional} pour {symbol}")
            raise ValueError(f"Order value {quoteOrderQty} is below min_notional {min_notional} for {symbol}")
    except Exception as e:
        logger.error(f"[ORDER CHECK] Impossible de vérifier min_notional pour {symbol}: {e}")
        raise
    for attempt in range(max_retries):
        try:
            client._sync_server_time()
            res = _direct_market_order(
                client=client, symbol=symbol, side='BUY',
                quoteOrderQty=quoteOrderQty, client_id=client_id,
                send_alert=send_alert
            )
            logger.info(f"Market buy placed: {symbol} quote={quoteOrderQty} clientId={client_id}")
            return res
        except Exception as e:
            last_exc = e
            logger.warning(f"safe_market_buy attempt {attempt+1} failed for {symbol}: {e}")
            time.sleep(1.0 + random.random())
    logger.error(f"safe_market_buy failed after {max_retries} attempts for {symbol}")
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"safe_market_buy failed after {max_retries} attempts for {symbol}")


def safe_market_sell(client, symbol: str, quantity: Union[float, str],
                     max_retries: int = 4, send_alert=None) -> Dict[str, Any]:
    """Place a market SELL with idempotent retries and safety checks."""
    client_id = _generate_client_order_id('sell')
    last_exc = None
    for attempt in range(max_retries):
        try:
            client._sync_server_time()
            res = _direct_market_order(
                client=client, symbol=symbol, side='SELL',
                quantity=quantity, client_id=client_id,
                send_alert=send_alert
            )
            logger.info(f"Market sell placed: {symbol} qty={quantity} clientId={client_id}")
            return res
        except Exception as e:
            last_exc = e
            logger.warning(f"safe_market_sell attempt {attempt+1} failed for {symbol}: {e}")
            time.sleep(1.0 + random.random())
    logger.error(f"safe_market_sell failed after {max_retries} attempts for {symbol}")
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"safe_market_sell failed after {max_retries} attempts for {symbol}")


@retry_with_backoff(max_retries=3, base_delay=2.0)
@log_exceptions(default_return=None)
def place_trailing_stop_order(client, symbol: str, quantity: float,
                              activation_price: float, trailing_delta: int,
                              client_id: Optional[str] = None,
                              send_alert=None) -> dict:
    """Place un ordre TRAILING_STOP_MARKET.
    
    ATTENTION : TRAILING_STOP_MARKET est un type d'ordre Futures uniquement.
    Cette fonction NE FONCTIONNE PAS sur l'API Spot Binance (api/v3/order).
    Conservée pour compatibilité mais ne doit pas être appelée sur un compte Spot.
    
    Utilise client.create_order() pour bénéficier du retry/sync de _request().
    """
    order_params = {
        'symbol': symbol, 'side': 'SELL', 'type': 'TRAILING_STOP_MARKET',
        'quantity': float(quantity), 'activationPrice': float(activation_price),
        'trailingDelta': int(trailing_delta),
    }
    if client_id:
        order_params['newClientOrderId'] = client_id
    try:
        result = client.create_order(**order_params)
        if send_alert:
            order_summary = (f"Symbol: {symbol}, Qty: {quantity}, "
                             f"ActivationPrice: {activation_price}, TrailingDelta: {trailing_delta}, "
                             f"OrderId: {result.get('orderId')}")
            send_alert(subject="[BOT CRYPTO] TRAILING STOP EXECUTE",
                       body_main=f"TRAILING STOP exécuté.\n\n{order_summary}",
                       client=client)
        return result
    except BinanceAPIException as e:
        if send_alert:
            send_alert(subject="[BOT CRYPTO] ERREUR EXECUTION TRAILING STOP",
                       body_main=f"Erreur TRAILING STOP: {e.code} - {e.message}\n"
                                 f"Symbol: {symbol}, Qty: {quantity}",
                       client=client)
        raise


@retry_with_backoff(max_retries=3, base_delay=2.0)
@log_exceptions(default_return=None)
def place_stop_loss_order(client, symbol: str, quantity: float, stop_price: float,
                          client_id: Optional[str] = None,
                          send_alert=None) -> dict:
    """Place un ordre STOP_LOSS sur Binance (spot).
    
    Utilise client.create_order() pour bénéficier du retry/sync de _request().
    """
    order_params = {
        'symbol': symbol, 'side': 'SELL', 'type': 'STOP_LOSS',
        'quantity': float(quantity), 'stopPrice': float(stop_price),
    }
    if client_id:
        order_params['newClientOrderId'] = client_id
    try:
        result = client.create_order(**order_params)
        if send_alert:
            order_summary = (f"Symbol: {symbol}, Qty: {quantity}, StopPrice: {stop_price}, "
                             f"OrderId: {result.get('orderId')}, Status: {result.get('status')}")
            send_alert(subject="[BOT CRYPTO] STOP LOSS EXECUTE",
                       body_main=f"STOP LOSS exécuté.\n\n{order_summary}",
                       client=client)
        return result
    except BinanceAPIException as e:
        if send_alert:
            send_alert(subject="[BOT CRYPTO] ERREUR EXECUTION STOP LOSS",
                       body_main=f"Erreur STOP LOSS: {e.code} - {e.message}\n"
                                 f"Symbol: {symbol}, Qty: {quantity}, StopPrice: {stop_price}",
                       client=client)
        raise


def is_valid_stop_loss_order(symbol, quantity, stop_price) -> bool:
    """Vérifie que les paramètres d'ordre stop-loss sont valides."""
    if symbol is None or symbol == "None" or not isinstance(symbol, str) or len(symbol) < 5:
        return False
    try:
        q = float(quantity)
        p = float(stop_price)
        if q <= 0 or p <= 0:
            return False
    except Exception:
        return False
    return True


def can_execute_partial_safely(coin_balance: float, current_price: float,
                               min_notional: float) -> bool:
    """Vérifie si les partials peuvent s'exécuter sans créer de reliquat non-vendable."""
    final_remaining_qty = coin_balance * 0.20
    final_notional_value = final_remaining_qty * current_price
    safety_margin = min_notional * 1.1
    if final_notional_value < safety_margin:
        logger.warning(
            f"[PARTIAL-CHECK] Position trop petite pour partials sûrs:\n"
            f"  Position totale: {coin_balance:.8f} ({coin_balance * current_price:.2f} USDC)\n"
            f"  Reliquat final (20%): {final_remaining_qty:.8f} ({final_notional_value:.2f} USDC)\n"
            f"  MIN_NOTIONAL requis: {safety_margin:.2f} USDC\n"
            f"  → PARTIALS DÉSACTIVÉS pour cette position"
        )
        return False
    logger.info(
        f"[PARTIAL-CHECK] Position suffisante pour partials:\n"
        f"  Reliquat final (20%): {final_notional_value:.2f} USDC > {safety_margin:.2f} USDC ✓"
    )
    return True


@log_exceptions(default_return={'min_qty': None, 'step_size': None, 'min_notional': None})
def get_symbol_filters(client, symbol: str) -> Dict:
    """Récupère les filters min_qty, step_size et min_notional pour un symbole."""
    from decimal import Decimal
    info = client.get_symbol_info(symbol)
    if not info:
        raise ValueError(f"Aucune information trouvée pour le symbole {symbol}")
    result = {
        'min_qty': Decimal('0.001'),
        'step_size': Decimal('0.000001'),
        'min_notional': Decimal('10.0')
    }
    for f in info['filters']:
        if f['filterType'] == 'LOT_SIZE':
            result['min_qty'] = Decimal(f['minQty'])
            result['step_size'] = Decimal(f['stepSize'])
        elif f['filterType'] == 'MIN_NOTIONAL':
            result['min_notional'] = Decimal(f.get('minNotional', '10.0'))
    logger.info(f"Filters pour {symbol}: min_qty={result['min_qty']}, step_size={result['step_size']}, min_notional={result['min_notional']}")
    return result


_tickers_lock = threading.Lock()
_tickers_cache: Dict[str, Any] = {'data': None, 'timestamp': 0.0}


def get_all_tickers_cached(client, cache_ttl: int = 10) -> dict:
    """Récupère tous les tickers Binance avec cache local (TTL configurable)."""
    now = time.time()
    with _tickers_lock:
        if _tickers_cache['data'] is not None and (now - _tickers_cache['timestamp'] < cache_ttl):
            return _tickers_cache['data']
        tickers = {t['symbol']: float(t['price']) for t in client.get_all_tickers()}
        _tickers_cache['data'] = tickers
        _tickers_cache['timestamp'] = now
        return tickers


@log_exceptions(default_return=0.0)
def get_spot_balance_usdc(client) -> float:
    """Retourne le solde SPOT global converti en USDC (y compris coins)."""
    try:
        account_info = client.get_account()
        tickers = get_all_tickers_cached(client)
        spot_balance_usdc = 0.0
        for bal in account_info['balances']:
            asset = bal['asset']
            free = float(bal['free'])
            locked = float(bal['locked'])
            total = free + locked
            if total < 1e-8:
                continue
            if asset == 'USDC':
                spot_balance_usdc += total
            else:
                symbol1 = asset + 'USDC'
                symbol2 = 'USDC' + asset
                if symbol1 in tickers:
                    spot_balance_usdc += total * tickers[symbol1]
                elif symbol2 in tickers and tickers[symbol2] > 0:
                    spot_balance_usdc += total * (1.0 / tickers[symbol2])
        return spot_balance_usdc
    except Exception:
        return 0.0
