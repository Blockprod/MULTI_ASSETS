"""
ibkr_client.py — Client IBKR Forex implémentant le protocole ExchangePort.

Connexion via ib_insync.connectAsync() — méthode identique au projet AlphaEdge.
ClientId=4 : distinct de EDGECORE(1), AlphaEdge(2 ou 3) pour éviter tout conflit.

Protocole ExchangePort défini dans exchange_client.py (importé en lecture seule,
BinanceFinalClient n'est pas instancié).
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import random
import socket
import sys
import threading
from typing import Any, Dict, List

# ib_insync + Windows ProactorEventLoop = "Future attached to a different loop".
# WindowsSelectorEventLoopPolicy règle le problème (même fix qu'AlphaEdge).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = logging.getLogger("ibkr_forex")

try:
    from ib_insync import IB, Contract, MarketOrder, StopLimitOrder, Trade
    _IB_AVAILABLE = True
except ImportError:
    _IB_AVAILABLE = False
    logger.warning("[IBKR-CLIENT] ib_insync non installé — mode mock uniquement")


# ─── Constantes de reconnexion (identiques à AlphaEdge) ──────────────────────
_IB_TIMEOUT_SECONDS: float = 30.0
_RECONNECT_INITIAL_DELAY: float = 2.0
_RECONNECT_MAX_DELAY: float = 60.0      # IB Gateway libère clientId en ~30-60s
_RECONNECT_MAX_RETRIES: int = 6         # ~62s cumulés — suffit pour Error 326
_RECONNECT_JITTER: float = 0.10         # ±10%
_CLIENT_ID_IN_USE_CODE: int = 326       # IB Gateway error code : clientId déjà utilisé
# ─── Probe TCP (identique AlphaEdge gw_manager) ──────────────────────────────
_PORT_PROBE_TIMEOUT: float = 3.0          # timeout TCP (s)
_RECONNECT_PORT_POLL_DELAY: float = 30.0  # attente entre probes port fermé
_RECONNECT_PORT_POLL_MAX: int = 15        # 15 × 30s = 7.5 min (couvre restart 05:30)


def _is_api_port_open(host: str, port: int) -> bool:
    """Probe TCP — vérifie si IB Gateway écoute sur le port (pattern AlphaEdge)."""
    try:
        with socket.create_connection((host, port), timeout=_PORT_PROBE_TIMEOUT):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False

def _build_forex_contract(ibkr_pair: str) -> "Contract":
    """Construit le contrat IBKR CASH pour une paire Forex.

    ibkr_pair exemples : 'EURUSD', 'EURGBP'
    Retourne un Contract(secType='CASH', exchange='IDEALPRO')
    """
    if not _IB_AVAILABLE:
        raise RuntimeError("ib_insync non installé")
    # Conventions IBKR : les 3 premiers caractères = devise base, les 3 suivants = devise quote
    symbol = ibkr_pair[:3].upper()
    currency = ibkr_pair[3:].upper()
    contract = Contract()
    contract.secType = "CASH"
    contract.symbol = symbol
    contract.currency = currency
    contract.exchange = "IDEALPRO"
    return contract


class IBKRForexClient:
    """Client IBKR Forex thread-safe.

    Implémente structurellement le protocole ExchangePort défini dans
    exchange_client.py (même signatures de méthodes, types compatibles).

    Usage :
        client = IBKRForexClient(config)
        client.connect()          # appelé une fois au démarrage
        client.disconnect()       # appelé à l'arrêt propre
    """

    def __init__(self, host: str, port: int, client_id: int, account: str) -> None:
        if not _IB_AVAILABLE:
            raise RuntimeError(
                "ib_insync n'est pas installé. "
                "Exécuter : .venv\\Scripts\\pip install ib_insync"
            )
        self._host = host
        self._port = port
        self._client_id = client_id
        self._account = account
        self._lock = threading.Lock()
        self._connected = False
        self._disconnect_time: float = 0.0  # horodatage derniere deconnexion

        # Boucle asyncio dédiée — doit être active AVANT d'instancier IB()
        # pour que les futures ib_insync soient liées à cette boucle.
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ib = IB()
        # Callback proactif — même pattern qu'AlphaEdge session_lifecycle._on_ib_disconnect
        self._ib.disconnectedEvent += self._on_disconnect
        # B-03: callback de réconciliation post-reconnect (assigné par IBKR_FOREX)
        self._on_reconnect: "Any | None" = None  # type: ignore[assignment]

    # ─── Connexion ───────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Connexion synchrone à IB Gateway. Retry exponentiel identique à AlphaEdge."""
        self._loop.run_until_complete(self._connect_async())

    def is_connected(self) -> bool:
        """Retourne True si la connexion IB Gateway est active."""
        return self._ib.isConnected()

    def _on_disconnect(self) -> None:
        """Callback ib_insync disconnectedEvent — détection proactive (pattern AlphaEdge)."""
        import time as _t
        self._connected = False
        self._disconnect_time = _t.time()
        logger.warning(
            "[IBKR-CLIENT] IB Gateway déconnecté (disconnectedEvent) — "
            "reconnexion au prochain cycle"
        )

    async def _connect_async(self) -> None:
        # ─ 1. Probe TCP — attendre que le port soit ouvert (couvre restart 05:30) ─
        # Pattern identique à AlphaEdge gw_manager.ensure_gateway_ready().
        if not _is_api_port_open(self._host, self._port):
            logger.warning(
                "[IBKR-CLIENT] Port %d fermé — attente ouverture IB Gateway "
                "(restart 05:30 ou démarrage tardif ?)",
                self._port,
            )
            _port_open = False
            for _probe in range(1, _RECONNECT_PORT_POLL_MAX + 1):
                await asyncio.sleep(_RECONNECT_PORT_POLL_DELAY)
                if _is_api_port_open(self._host, self._port):
                    logger.info(
                        "[IBKR-CLIENT] Port %d ouvert après %.0fs",
                        self._port, _probe * _RECONNECT_PORT_POLL_DELAY,
                    )
                    _port_open = True
                    break
                logger.debug(
                    "[IBKR-CLIENT] Port %d toujours fermé (%d/%d)",
                    self._port, _probe, _RECONNECT_PORT_POLL_MAX,
                )
            if not _port_open:
                raise ConnectionError(
                    f"[IBKR-CLIENT] Port {self._port} toujours fermé après "
                    f"{_RECONNECT_PORT_POLL_MAX * _RECONNECT_PORT_POLL_DELAY:.0f}s "
                    f"— IB Gateway hors ligne ?"
                )

        # ─ 2. Connect ib_insync avec retry exponentiel ───────────────────────────
        delay = _RECONNECT_INITIAL_DELAY
        for attempt in range(1, _RECONNECT_MAX_RETRIES + 1):
            _err326 = False

            def _on_err(reqId: int, errorCode: int, errorString: str, contract: object) -> None:
                nonlocal _err326
                if errorCode == _CLIENT_ID_IN_USE_CODE:
                    _err326 = True

            try:
                logger.info(
                    "[IBKR-CLIENT] Connexion IB Gateway %s:%d clientId=%d (tentative %d/%d)",
                    self._host, self._port, self._client_id, attempt, _RECONNECT_MAX_RETRIES,
                )
                self._ib.errorEvent += _on_err
                await asyncio.wait_for(
                    self._ib.connectAsync(
                        host=self._host,
                        port=self._port,
                        clientId=self._client_id,
                        readonly=False,
                    ),
                    timeout=_IB_TIMEOUT_SECONDS,
                )
                self._ib.errorEvent -= _on_err
                self._connected = True
                # Auto-détection du compte (même pattern qu'AlphaEdge)
                if not self._account:
                    accounts = self._ib.managedAccounts()
                    if accounts:
                        self._account = accounts[0]
                        logger.info("[IBKR-CLIENT] Compte détecté automatiquement : %s",
                                    self._account)
                    else:
                        logger.warning("[IBKR-CLIENT] Aucun compte détecté via managedAccounts()")
                logger.info(
                    "[IBKR-CLIENT] Connecté à IB Gateway (paper=%s, clientId=%d) "
                    "— allocation: EDGECORE=1, AlphaEdge=3, IBKR_FOREX=4",
                    self._port == 4002, self._client_id,
                )
                return
            except Exception as exc:
                self._ib.errorEvent -= _on_err
                if _err326:
                    logger.warning(
                        "[IBKR-CLIENT] Tentative %d — clientId=%d déjà utilisé par IB Gateway "
                        "(Error 326). Session précédente en cours de libération...",
                        attempt, self._client_id,
                    )
                else:
                    logger.warning("[IBKR-CLIENT] Tentative %d échouée : %s", attempt, exc)
                if attempt < _RECONNECT_MAX_RETRIES:
                    jitter = delay * _RECONNECT_JITTER * random.uniform(-1, 1)
                    wait = min(delay + jitter, _RECONNECT_MAX_DELAY)
                    logger.info("[IBKR-CLIENT] Prochaine tentative dans %.1fs", wait)
                    await asyncio.sleep(wait)
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
        raise ConnectionError(
            f"[IBKR-CLIENT] IB Gateway inaccessible après {_RECONNECT_MAX_RETRIES} tentatives"
        )

    def ensure_connected(self) -> None:
        """Reconnecte si la connexion est perdue. Appelle _on_reconnect si assigné (B-03)."""
        if not self._ib.isConnected():
            self._connected = False
            logger.warning("[IBKR-CLIENT] Connexion perdue — reconnexion...")
            self.connect()
            if self._ib.isConnected() and callable(self._on_reconnect):
                try:
                    self._on_reconnect()
                except Exception as _rc_err:
                    logger.error("[IBKR-CLIENT] _on_reconnect ERREUR: %s", _rc_err)

    def disconnect(self) -> None:
        """Déconnexion propre."""
        if self._ib.isConnected():
            self._ib.disconnect()
        self._connected = False
        logger.info("[IBKR-CLIENT] Déconnecté de IB Gateway")

    # ─── Protocole ExchangePort ───────────────────────────────────────────────

    def get_account(self, **kwargs: Any) -> Dict[str, Any]:
        """Retourne un résumé du compte IBKR (soldes, NAV)."""
        self.ensure_connected()
        summary = self._ib.accountSummary(self._account)
        result: Dict[str, Any] = {}
        for item in summary:
            result[item.tag] = item.value
        return result

    def get_account_balance(self) -> float:
        """Retourne la valeur liquidative nette (NetLiquidation) en devise du compte."""
        self.ensure_connected()
        acct = self.get_account()
        try:
            return float(acct.get("NetLiquidation", 0.0))
        except (ValueError, TypeError):
            return 0.0

    def get_available_funds(self) -> float:
        """I4: Retourne les fonds disponibles (AvailableFunds) en devise du compte."""
        self.ensure_connected()
        acct = self.get_account()
        try:
            return float(acct.get("AvailableFunds", 0.0))
        except (ValueError, TypeError):
            return 0.0

    def get_forex_position(self, ibkr_pair: str) -> float:
        """C2: Retourne la position live nette sur IB Gateway pour une paire Forex.

        Interroge directement l'exchange — ne dépend pas du bot_state.
        Valeur positive = LONG, négative = SHORT, 0.0 = flat.

        Args:
            ibkr_pair : ex 'EURUSD'

        Returns:
            Quantité nette (devise base). 0.0 si non connecté ou aucune position.
        """
        if not self._ib.isConnected():
            logger.warning("[IBKR-CLIENT] get_forex_position: non connecté — retour 0.0")
            return 0.0
        try:
            symbol = ibkr_pair[:3].upper()
            currency = ibkr_pair[3:].upper()
            positions = self._ib.positions()
            for pos in positions:
                c = pos.contract
                if (
                    getattr(c, "secType", "") == "CASH"
                    and getattr(c, "symbol", "").upper() == symbol
                    and getattr(c, "currency", "").upper() == currency
                ):
                    return float(pos.position)
            return 0.0
        except Exception as exc:
            logger.error("[IBKR-CLIENT] get_forex_position %s ERREUR: %s", ibkr_pair, exc)
            return 0.0

    def get_all_tickers(self, **kwargs: Any) -> List[Dict[str, Any]]:
        """Non implémenté pour Forex (sans objet)."""
        return []

    def get_exchange_info(self, **kwargs: Any) -> Dict[str, Any]:
        """Non implémenté pour Forex (sans objet)."""
        return {}

    def get_symbol_ticker(self, **kwargs: Any) -> Dict[str, Any]:
        """Retourne le prix mid d'une paire Forex (I6: snapshot non-bloquant via reqTickers)."""
        self.ensure_connected()
        symbol: str = kwargs.get("symbol", "")
        contract = _build_forex_contract(symbol)
        tickers = self._ib.reqTickers(contract)
        if tickers:
            t = tickers[0]
            mid = (
                (t.bid + t.ask) / 2
                if t.bid and t.ask and t.bid > 0 and t.ask > 0
                else (t.last or t.close or 0.0)
            )
        else:
            mid = 0.0
        return {"symbol": symbol, "price": str(mid)}

    def get_symbol_info(self, symbol: str) -> Any:
        """Retourne les détails du contrat IBKR pour la paire."""
        self.ensure_connected()
        contract = _build_forex_contract(symbol)
        details = self._ib.reqContractDetails(contract)
        return details[0] if details else None

    def get_server_time(self, **kwargs: Any) -> Dict[str, Any]:
        """Retourne l'heure serveur IBKR."""
        self.ensure_connected()
        t = self._ib.reqCurrentTime()
        ts = t.timestamp() if isinstance(t, datetime.datetime) else float(t)
        return {"serverTime": int(ts)}

    def get_order(self, **kwargs: Any) -> Dict[str, Any]:
        """Retourne le statut d'un ordre par orderId."""
        self.ensure_connected()
        order_id: int = int(kwargs.get("orderId", 0))
        for trade in self._ib.trades():
            if trade.order.orderId == order_id:
                return self._trade_to_dict(trade)
        return {}

    def get_all_orders(self, **kwargs: Any) -> List[Dict[str, Any]]:
        self.ensure_connected()
        return [self._trade_to_dict(t) for t in self._ib.trades()]

    def get_open_orders(self, **kwargs: Any) -> List[Dict[str, Any]]:
        self.ensure_connected()
        return [self._trade_to_dict(t) for t in self._ib.openTrades()]

    def get_my_trades(self, **kwargs: Any) -> List[Dict[str, Any]]:
        self.ensure_connected()
        return [self._fill_to_dict(f) for t in self._ib.trades() for f in t.fills]

    def get_trade_fee(self, **kwargs: Any) -> List[Dict[str, Any]]:
        """Non applicable Forex IBKR — retourne structure vide."""
        return []

    def get_order_by_ref(self, order_ref: str) -> "Optional[Dict[str, Any]]":  # type: ignore[name-defined]  # noqa: F821
        """Recherche un ordre existant par orderRef (idempotence B-02).

        Retourne le dict trade si trouvé, None sinon.
        """
        if not order_ref:
            return None
        self.ensure_connected()
        for trade in self._ib.trades():
            if getattr(trade.order, "orderRef", "") == order_ref:
                return self._trade_to_dict(trade)
        return None

    def order_market_buy(self, **kwargs: Any) -> Dict[str, Any]:
        """Place un ordre d'achat au marché."""
        self.ensure_connected()
        symbol: str = kwargs["symbol"]
        quantity: float = float(kwargs.get("quantity", kwargs.get("quoteOrderQty", 0.0)))
        order_ref: str = kwargs.get("orderRef", "")
        # B-02: idempotence — ne pas doubler si l'ordre existe déjà
        if order_ref:
            existing = self.get_order_by_ref(order_ref)
            if existing:
                logger.info("[IBKR-CLIENT] order_market_buy: orderRef %s déjà soumis", order_ref)
                return existing
        contract = _build_forex_contract(symbol)
        order = MarketOrder("BUY", quantity)
        order.tif = "IOC"
        if order_ref:
            order.orderRef = order_ref
        trade = self._ib.placeOrder(contract, order)
        self._ib.sleep(1)
        return self._trade_to_dict(trade)

    def order_market_sell(self, **kwargs: Any) -> Dict[str, Any]:
        """Place un ordre de vente au marché."""
        self.ensure_connected()
        symbol: str = kwargs["symbol"]
        quantity: float = float(kwargs.get("quantity", 0.0))
        order_ref: str = kwargs.get("orderRef", "")
        # C1: idempotence — ne pas doubler si l'ordre existe déjà
        if order_ref:
            existing = self.get_order_by_ref(order_ref)
            if existing:
                logger.info("[IBKR-CLIENT] order_market_sell: orderRef %s déjà soumis", order_ref)
                return existing
        contract = _build_forex_contract(symbol)
        order = MarketOrder("SELL", quantity)
        order.tif = "IOC"
        if order_ref:
            order.orderRef = order_ref
        trade = self._ib.placeOrder(contract, order)
        self._ib.sleep(1)
        return self._trade_to_dict(trade)

    def create_order(self, **kwargs: Any) -> Dict[str, Any]:
        """Crée un ordre générique (stop, limit, etc.).

        kwargs attendus :
          symbol      : str  — ex 'EURUSD'
          side        : str  — 'BUY' | 'SELL'
          type        : str  — 'STOP_LOSS' | 'LIMIT' | 'MARKET'
          quantity    : float
          stopPrice   : float  (pour STOP_LOSS)
          price       : float  (pour LIMIT)
          orderRef    : str   (optionnel, pour idempotence B-02)
        """
        self.ensure_connected()
        symbol: str = kwargs["symbol"]
        side: str = kwargs.get("side", "SELL").upper()
        order_type: str = kwargs.get("type", "MARKET").upper()
        quantity: float = float(kwargs.get("quantity", 0.0))
        order_ref: str = kwargs.get("orderRef", "")
        # B-02: idempotence — ne pas doubler si l'ordre existe déjà
        if order_ref:
            existing = self.get_order_by_ref(order_ref)
            if existing:
                logger.info("[IBKR-CLIENT] create_order: orderRef %s déjà soumis", order_ref)
                return existing
        contract = _build_forex_contract(symbol)

        if order_type in ("STOP_LOSS", "STOP_LOSS_LIMIT", "STOP"):
            stop_price: float = float(kwargs.get("stopPrice", kwargs.get("stop_price", 0.0)))
            lmt_price_offset: float = float(kwargs.get("lmtPriceOffset", 0.00030))
            # C2: StopLimitOrder — évite le gap risk illimité du StopOrder market
            if side == "SELL":
                lmt_price = max(0.00001, stop_price - lmt_price_offset)
            else:
                lmt_price = stop_price + lmt_price_offset
            order = StopLimitOrder(side, quantity, stop_price, lmt_price)
        elif order_type == "LIMIT":
            from ib_insync import LimitOrder
            price: float = float(kwargs.get("price", 0.0))
            order = LimitOrder(side, quantity, price)
        else:
            order = MarketOrder(side, quantity)

        if order_ref:
            order.orderRef = order_ref
        trade = self._ib.placeOrder(contract, order)
        self._ib.sleep(1)
        return self._trade_to_dict(trade)

    def get_completed_orders(self) -> List[Dict[str, Any]]:
        """Retourne les ordres complétés (FILLED/CANCELED) via reqCompletedOrders.

        Utilisé comme fallback dans _check_and_handle_sl_hit après restart
        IB Gateway (ib.trades() est vide, mais completed orders persistent).
        """
        self.ensure_connected()
        try:
            completed = self._ib.reqCompletedOrders(apiOnly=False)
            return [self._trade_to_dict(t) for t in completed]
        except Exception as exc:
            logger.warning("[IBKR-CLIENT] get_completed_orders ERREUR: %s", exc)
            return []

    def cancel_order(self, **kwargs: Any) -> Dict[str, Any]:
        """Annule un ordre par orderId."""
        self.ensure_connected()
        order_id: int = int(kwargs.get("orderId", 0))
        for trade in self._ib.openTrades():
            if trade.order.orderId == order_id:
                self._ib.cancelOrder(trade.order)
                self._ib.sleep(1)
                return {"orderId": order_id, "status": "CANCELED"}
        logger.warning("[IBKR-CLIENT] cancel_order: orderId %d introuvable", order_id)
        return {"orderId": order_id, "status": "NOT_FOUND"}

    def get_historical_klines(
        self,
        symbol: str,
        interval: str,
        start_str: str,
        **kwargs: Any,
    ) -> List[List[Any]]:
        """Récupère les données OHLCV historiques.

        Gère automatiquement le chunking pour les durées > 365 jours
        (limite IBKR par requête pour les barres horaires et journalières).
        Retourne une liste de barres au format Binance pour compatibilité
        avec data_fetcher.py : [timestamp_ms, open, high, low, close, volume, ...]
        """
        self.ensure_connected()
        contract = _build_forex_contract(symbol)

        # Mapping interval → barSizeSetting IBKR
        _bar_size_map = {
            "1m": "1 min", "5m": "5 mins", "15m": "15 mins",
            "30m": "30 mins", "1h": "1 hour", "4h": "4 hours",
            "1d": "1 day",
        }
        bar_size = _bar_size_map.get(interval, "1 hour")

        # Limite IBKR en jours par requête selon la taille de barre
        _max_days_per_req: Dict[str, int] = {
            "1 min": 7, "5 mins": 30, "15 mins": 60,
            "30 mins": 120, "1 hour": 365, "4 hours": 365, "1 day": 365,
        }
        max_chunk = _max_days_per_req.get(bar_size, 365)

        start_dt = _parse_start_date(start_str)
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        start_dt_utc = start_dt.replace(tzinfo=datetime.timezone.utc)
        total_days = max(1, (now_dt - start_dt_utc).days)

        if total_days <= max_chunk:
            # Requête unique
            bars = self._ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=f"{total_days} D",
                barSizeSetting=bar_size,
                whatToShow="MIDPOINT",
                useRTH=False,
                formatDate=2,
            )
            return _bars_to_binance_format(bars)

        # Chunked : découpe en tranches de max_chunk jours (en remontant depuis now)
        import time as _time_mod
        n_chunks = -(-total_days // max_chunk)  # ceil division
        logger.info(
            "[IBKR-CLIENT] %s : %d jours → %d requêtes (%d j/chunk)",
            symbol, total_days, n_chunks, max_chunk,
        )
        all_bars: List[Any] = []
        end_dt = now_dt
        while end_dt > start_dt_utc:
            chunk_start = max(start_dt_utc, end_dt - datetime.timedelta(days=max_chunk))
            chunk_days = max(1, (end_dt - chunk_start).days)
            end_dt_str = end_dt.strftime("%Y%m%d-%H:%M:%S")
            logger.debug(
                "[IBKR-CLIENT] Chunk %s → endDateTime=%s durationStr=%d D",
                symbol, end_dt_str, chunk_days,
            )
            chunk = self._ib.reqHistoricalData(
                contract,
                endDateTime=end_dt_str,
                durationStr=f"{chunk_days} D",
                barSizeSetting=bar_size,
                whatToShow="MIDPOINT",
                useRTH=False,
                formatDate=2,
            )
            all_bars = list(chunk) + all_bars  # préfixer (barres plus anciennes)
            end_dt = chunk_start
            if end_dt > start_dt_utc:
                _time_mod.sleep(10)  # Anti pacing violation IBKR (max 60 req / 10 min)

        return _bars_to_binance_format(all_bars)

    # ─── Helpers privés ──────────────────────────────────────────────────────

    @staticmethod
    def _trade_to_dict(trade: "Trade") -> Dict[str, Any]:
        """Convertit un Trade ib_insync en dict compatible ExchangePort."""
        status_map = {
            "Submitted": "NEW",
            "PreSubmitted": "NEW",
            "Filled": "FILLED",
            "Cancelled": "CANCELED",
            "Inactive": "CANCELED",
        }
        raw_status = trade.orderStatus.status if trade.orderStatus else "UNKNOWN"
        mapped_status = status_map.get(raw_status, raw_status.upper())
        avg_price = trade.orderStatus.avgFillPrice if trade.orderStatus else 0.0
        filled_qty = trade.orderStatus.filled if trade.orderStatus else 0.0
        return {
            "orderId": trade.order.orderId,
            "clientOrderId": str(trade.order.orderId),
            "symbol": f"{trade.contract.symbol}{trade.contract.currency}",
            "side": trade.order.action,
            "type": trade.order.orderType,
            "status": mapped_status,
            "price": str(avg_price),
            "avgFillPrice": str(avg_price),   # A1: champ explicite pour _extract_fill_price
            "executedQty": str(filled_qty),
            "origQty": str(trade.order.totalQuantity),
        }

    @staticmethod
    def _fill_to_dict(fill: Any) -> Dict[str, Any]:
        return {
            "id": fill.execution.execId,
            "orderId": fill.execution.orderId,
            "price": str(fill.execution.price),
            "qty": str(fill.execution.shares),
            "commission": str(fill.commissionReport.commission if fill.commissionReport else 0),
            "time": fill.execution.time,
        }

    @property
    def ib(self) -> "IB":
        """Accès direct à l'objet IB ib_insync pour les usages avancés."""
        return self._ib


def _parse_start_date(start_str: str) -> datetime.datetime:
    """Parse une date de début sous différents formats.

    Formats supportés : '22 May 2023', '2023-05-22', '22/05/2023'
    Fallback : 3 ans en arrière.
    """
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(start_str, fmt)
        except ValueError:
            continue
    return datetime.datetime.now() - datetime.timedelta(days=3 * 365)


def _bars_to_binance_format(bars: list) -> List[List[Any]]:
    """Convertit des barres ib_insync en format compatible Binance (liste de listes)."""
    result = []
    for bar in bars:
        d = bar.date
        if isinstance(d, datetime.datetime):
            ts_ms = int(d.timestamp()) * 1000
        elif isinstance(d, datetime.date):
            ts_ms = int(datetime.datetime(d.year, d.month, d.day).timestamp()) * 1000
        else:
            ts_ms = int(d) * 1000
        result.append([
            ts_ms,
            str(bar.open), str(bar.high), str(bar.low), str(bar.close),
            str(bar.volume or 0),
            ts_ms + 3600_000,
            "0", 0, "0", "0", "0",
        ])
    return result
