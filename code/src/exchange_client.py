"""
exchange_client.py — Client Binance robuste et helpers d'ordres.
Phase 4 refactoring: extrait de MULTI_SYMBOLS.py
"""
import hashlib
import hmac
import logging
import random
import requests  # type: ignore[import-untyped]
import threading
import time
import uuid
from typing import Any, Dict, Optional, Union

from binance.client import Client
from binance.exceptions import BinanceAPIException

from bot_config import log_exceptions, retry_with_backoff, config as _config
from exceptions import BalanceUnavailableError, OrderError

logger = logging.getLogger(__name__)


# ─── Token-bucket rate limiter (C-05) ─────────────────────────────────────
# Limite l'API Binance à 18 req/s (marge sur la limite Binance de 20 req/s / 1200 req/min).
# Chaque appel à _request() consomme un token. Si le bucket est vide, on attend.
class _TokenBucket:
    """Token bucket thread-safe pour le rate limiting."""
    def __init__(self, rate: float, capacity: float):
        self._rate = rate           # Tokens ajoutés par seconde
        self._capacity = capacity   # Capacité maximale du bucket
        self._tokens: float = capacity
        self._last: float = time.time()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        """Attend qu'un token soit disponible. Retourne False si timeout dépassé."""
        deadline = time.time() + timeout
        while True:
            with self._lock:
                now = time.time()
                elapsed = now - self._last
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
            if time.time() > deadline:
                logger.warning("[RATE LIMITER] Timeout en attendant un token API")
                return False
            time.sleep(0.05)

# 18 req/s = 1080 req/min — marge de sécurité par rapport à la limite Binance (1200 req/min)
_api_rate_limiter = _TokenBucket(rate=18.0, capacity=18.0)


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

    def ping(self):
        """Override du ping() Binance : absorbe les erreurs de géo-restriction
        (runners CI, serveurs hors zone Binance) sans bloquer l'initialisation.
        Le bot dispose de sa propre validation de connectivité.
        """
        try:
            return super().ping()
        except Exception as exc:
            logger.warning("Binance ping() ignoré (%s) — connectivité validée séparément.", exc)
            return {}

    def close_connection(self):
        """Fermeture sécurisée — protège contre AttributeError si __init__ a
        échoué ou n'a pas été appelé (ex. instanciation via __new__).

        Le ``Client.__del__`` de python-binance accède à ``self.session`` sans
        vérifier son existence, ce qui lève ``AttributeError`` lorsque
        ``__init__`` n'a pas terminé.  Ce guard corrige ce défaut.
        """
        session = getattr(self, 'session', None)
        if session:
            session.close()

    def _perform_ultra_robust_sync(self):
        """Synchronisation avec compensation du décalage horloge locale/Binance.

        L'offset est calculé précisément :
          offset = serverTime - localTime - latence/2
        On ajoute ensuite une marge de sécurité de -500 ms pour absorber les
        petites dérives sans jamais envoyer un timestamp trop ancien.
        recvWindow=60000 dans les ordres couvre le reste.
        """
        try:
            local_before = int(time.time() * 1000)
            server_time = self.get_server_time()['serverTime']
            local_after = int(time.time() * 1000)
            latency = (local_after - local_before) // 2
            real_offset = server_time - (local_before + latency)
            # Marge de sécurité conservatrice : -500 ms (ni trop négatif, ni positif)
            # Évite l'ancien forcing à -5000 ms qui envoyait des timestamps trop anciens.
            SAFETY_MARGIN_MS = -500
            adjusted_offset = real_offset + SAFETY_MARGIN_MS
            # Clamp : jamais plus de -10 s ni plus de +1 s
            self._server_time_offset = max(-10000, min(1000, adjusted_offset))
            self._last_sync = time.time()
            self._error_count = 0
            logger.info(f"SYNCHRO OK: offset={self._server_time_offset}ms (real={real_offset}ms, latency={latency}ms)")
        except Exception as e:
            logger.error(f"Echec synchronisation: {e}")
            self._server_time_offset = -2000  # fallback conservateur

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
                # C-05: Rate limiting — acquérir un token avant tout appel réseau
                _api_rate_limiter.acquire(timeout=30.0)
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
    # recvWindow : centralisé dans config (P1-11)
    params_order = [
        ('symbol', symbol),
        ('side', side),
        ('type', 'MARKET'),
        ('quantity', f"{quantity}" if quantity is not None else None),
        ('quoteOrderQty', f"{float(quoteOrderQty):.2f}" if quoteOrderQty is not None else None),
        ('newClientOrderId', client_id),  # P0-IDEM: idempotence — même ID sur chaque retry
        ('recvWindow', _config.recv_window),
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
            # Utiliser executedQty (quantité base asset réelle) plutôt que quoteOrderQty (USDC dépensés)
            _display_qty = result.get('executedQty') or quantity or quoteOrderQty
            order_summary = f"Symbol: {symbol}, Side: {side}, Qty: {_display_qty}"
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
        # P2-A: avant chaque retry, vérifier si l'ordre précédent a déjà été exécuté
        if attempt > 0:
            try:
                existing = client.get_order(symbol=symbol, origClientOrderId=client_id)
                if existing.get('status') in ('FILLED', 'PARTIALLY_FILLED'):
                    logger.info(
                        "[IDEM P2-A] BUY %s déjà %s (clientId=%s) — retry annulé",
                        symbol, existing['status'], client_id,
                    )
                    return existing
            except Exception as _lookup_err:
                logger.debug("[IDEM P2-A] Ordre BUY introuvable, retry normal: %s", _lookup_err)
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
        # P2-A: avant chaque retry, vérifier si l'ordre précédent a déjà été exécuté
        if attempt > 0:
            try:
                existing = client.get_order(symbol=symbol, origClientOrderId=client_id)
                if existing.get('status') in ('FILLED', 'PARTIALLY_FILLED'):
                    logger.info(
                        "[IDEM P2-A] SELL %s déjà %s (clientId=%s) — retry annulé",
                        symbol, existing['status'], client_id,
                    )
                    return existing
            except Exception as _lookup_err:
                logger.debug("[IDEM P2-A] Ordre SELL introuvable, retry normal: %s", _lookup_err)
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


def place_exchange_stop_loss(
    client,
    symbol: str,
    quantity: str,
    stop_price: float,
    limit_slippage: float = 0.005,
    send_alert=None,
) -> Dict[str, Any]:
    """Place un ordre STOP_LOSS (market trigger) sur Binance Spot après un achat.

    C-02: Utilise STOP_LOSS au lieu de STOP_LOSS_LIMIT pour garantir le fill
    même en cas de flash crash ou de gap de prix. Un ordre STOP_LOSS déclenche
    un market sell dès que stopPrice est atteint — pas de risque de non-fill.

    Args:
        client: Instance BinanceFinalClient
        symbol: Paire Binance (ex. 'BTCUSDC')
        quantity: Quantité à vendre (chaîne déjà arrondie selon lot-size)
        stop_price: Prix de déclenchement du stop
        limit_slippage: Ignoré (conservé pour backward compat)
        send_alert: Callable(subject, body_main, client) pour alertes email

    Returns:
        Dict de la réponse Binance incluant 'orderId' et 'status'

    Raises:
        OrderError: Si le placement échoue après retry
    """
    client._sync_server_time()
    timestamp = int(time.time() * 1000) + client._server_time_offset
    client_id = _generate_client_order_id('sl')

    # Snapper stop_price au tickSize Binance (fix PRICE_FILTER -1013)
    from decimal import Decimal as _PriceD
    try:
        _sym_info = client.get_symbol_info(symbol)
        _price_filter = next(
            (f for f in _sym_info['filters'] if f['filterType'] == 'PRICE_FILTER'), None
        ) if _sym_info else None
        _tick = _PriceD(str(_price_filter['tickSize'])) if _price_filter else _PriceD('0.01')
        _tick_decs = max(0, -int(_tick.as_tuple().exponent))
    except Exception:
        _tick = _PriceD('0.01')
        _tick_decs = 2

    stop_price_snapped = float((_PriceD(str(stop_price)) // _tick) * _tick)
    stop_price_str = f"{stop_price_snapped:.{_tick_decs}f}"
    logger.debug(
        "[SL-ORDER] C-02 STOP_LOSS tick=%s stop_raw=%.8f→%s",
        _tick, stop_price, stop_price_str,
    )

    params = [
        ('symbol', symbol),
        ('side', 'SELL'),
        ('type', 'STOP_LOSS'),
        ('quantity', quantity),
        ('stopPrice', stop_price_str),
        ('newClientOrderId', client_id),
        ('recvWindow', _config.recv_window),  # P1-11
        ('timestamp', int(timestamp)),
    ]
    query_string = '&'.join([f"{k}={v}" for k, v in params])
    signature = hmac.new(
        client.api_secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    query_string_with_sig = query_string + f"&signature={signature}"
    headers = {
        'X-MBX-APIKEY': client.api_key,
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    url = 'https://api.binance.com/api/v3/order'

    # Jusqu'à 3 tentatives avec backoff
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            _api_rate_limiter.acquire(timeout=30.0)
            response = requests.post(
                url, data=query_string_with_sig, headers=headers, timeout=10
            )
            if response.status_code != 200:
                error_data = response.json()
                error_code = error_data.get('code', 'UNKNOWN')
                error_msg = error_data.get('msg', 'Unknown error')
                logger.error(
                    "[SL-ORDER] Erreur Binance: code=%s, msg=%s", error_code, error_msg
                )
                if send_alert:
                    send_alert(
                        subject=f"[BOT] ERREUR STOP-LOSS {symbol}",
                        body_main=(
                            f"Placement stop-loss échoué: {error_code} - {error_msg}\n"
                            f"Symbol: {symbol}, Qty: {quantity}, Stop: {stop_price}"
                        ),
                        client=client,
                    )
                raise OrderError(
                    f"STOP_LOSS échoué: {error_code} - {error_msg}",
                    symbol=symbol,
                )
            result = response.json()
            logger.info(
                "[SL-ORDER] Stop-loss exchange placé: %s qty=%s stop=%s orderId=%s",
                symbol, quantity, stop_price, result.get('orderId'),
            )
            if send_alert:
                send_alert(
                    subject=f"[BOT] Stop-loss placé {symbol}",
                    body_main=(
                        f"STOP_LOSS (market trigger) placé avec succès.\n"
                        f"Symbol: {symbol}  Qty: {quantity}\n"
                        f"Stop: {stop_price}\n"
                        f"OrderId: {result.get('orderId')}"
                    ),
                    client=client,
                )
            return result
        except OrderError:
            raise  # ne pas retry sur erreur métier
        except Exception as exc:
            last_exc = exc
            wait = 1.5 * (2 ** attempt) + random.uniform(0.0, 1.0)
            logger.warning(
                "[SL-ORDER] Tentative %d/%d échouée pour %s: %s. Retry dans %.1fs",
                attempt + 1, 3, symbol, exc, wait,
            )
            time.sleep(wait)

    raise OrderError(
        f"place_exchange_stop_loss: {3} tentatives échouées pour {symbol}: {last_exc}",
        symbol=symbol,
    )


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


def get_spot_balance_usdc(client) -> float:
    """Retourne le solde SPOT global converti en USDC (y compris coins).

    P0-02: lève BalanceUnavailableError si l'API échoue au lieu de retourner 0.0
    silencieusement, ce qui bloquerait tous les achats sans avertissement.
    Ne décore PAS avec @log_exceptions pour laisser l'exception se propager.
    """
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
    except Exception as exc:
        logger.error("[P0-02] get_spot_balance_usdc: %s — cycle skippé.", exc)
        raise BalanceUnavailableError(
            f"Impossible de récupérer le solde USDC depuis l'exchange: {exc}"
        ) from exc
