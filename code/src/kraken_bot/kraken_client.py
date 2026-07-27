"""Kraken Pro Spot client with a Binance-compatible surface.

The trading engine historically consumes a small Binance-shaped interface:
``get_account()``, ``get_symbol_info()``, ``get_historical_klines()``,
``order_market_buy()``, ``create_order()``, and order query helpers.
This adapter keeps that surface stable while translating to Kraken Spot REST.
"""

from __future__ import annotations

import base64
import csv
from dataclasses import dataclass
import hashlib
import hmac
import logging
import math
import os
import re
import threading
import time
import unicodedata
import urllib.parse
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, cast

import pandas as pd
import requests

from broker_models import BrokerConfig, KrakenHistoryStatus, PairConfig
from exceptions import CircuitOpenError, ExchangePermissionError, OrderError

logger = logging.getLogger(__name__)


_KRAKEN_INTERVALS: Dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


_KNOWN_QUOTES = ("USDC", "USDT", "USD", "EUR", "GBP", "CAD", "AUD")
_HISTORY_REQUIRED_BARS = {"1h": 1500, "4h": 1500, "1d": 1150}
_NONCE_STATE_ENV = "KRAKEN_NONCE_STATE_FILE"
_NONCE_LOCK_BYTES = 1

_FRENCH_MONTHS = {
    "janvier": "january",
    "fevrier": "february",
    "février": "february",
    "mars": "march",
    "avril": "april",
    "mai": "may",
    "juin": "june",
    "juillet": "july",
    "aout": "august",
    "août": "august",
    "septembre": "september",
    "octobre": "october",
    "novembre": "november",
    "decembre": "december",
    "décembre": "december",
}


def _default_nonce_state_path() -> Path:
    module_dir = Path(__file__).resolve().parent
    if module_dir.name == "kraken_bot":
        return module_dir / "states" / "kraken_nonce_state.txt"
    return module_dir / "kraken_bot" / "states" / "kraken_nonce_state.txt"


@dataclass(frozen=True)
class KrakenPreflightResult:
    public_ok: bool
    balance_ok: bool
    open_orders_ok: bool
    closed_orders_ok: bool
    private_api_ok: bool
    permission_error: Optional[str] = None
    nonce_error: Optional[str] = None
    tradable: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "public_ok": self.public_ok,
            "balance_ok": self.balance_ok,
            "open_orders_ok": self.open_orders_ok,
            "closed_orders_ok": self.closed_orders_ok,
            "private_api_ok": self.private_api_ok,
            "permission_error": self.permission_error,
            "nonce_error": self.nonce_error,
            "tradable": self.tradable,
        }


class _TokenBucket:
    def __init__(self, rate: float, capacity: float) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)


class KrakenSpotClient:
    """Small Kraken Spot REST adapter used by the Binance-derived engine."""

    broker = "KRAKEN"

    def __init__(
        self,
        config: BrokerConfig,
        *,
        requests_params: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.config = config
        self.api_key = config.api_key
        self.api_secret = config.api_secret
        self.api_url = config.api_url.rstrip("/")
        self.ws_url = config.ws_url
        self.strict_pair_validation = config.strict_pair_validation
        self.requests_params = dict(requests_params or {})
        self.session = requests.Session()
        self._nonce_lock = threading.Lock()
        self._private_lock = threading.Lock()
        self._last_nonce = 0
        nonce_state_file = self.requests_params.get("nonce_state_file") or os.getenv(_NONCE_STATE_ENV)
        self._nonce_state_path = Path(str(nonce_state_file)) if nonce_state_file else _default_nonce_state_path()
        self._asset_pairs_cache: Optional[Dict[str, Any]] = None
        self._pair_config_cache: Dict[str, PairConfig] = {}
        self._pair_aliases_cache: Dict[str, set[str]] = {}
        self._pair_candidates_cache: Dict[str, List[Dict[str, str]]] = {}
        self._last_history_status: Dict[Tuple[str, str], KrakenHistoryStatus] = {}
        self._client_order_map: Dict[str, str] = {}
        public_rate = float(
            self.requests_params.get(
                "public_rate_per_second",
                os.getenv("KRAKEN_PUBLIC_RATE_PER_SECOND", "0.45"),
            )
        )
        self._public_rate_limiter = _TokenBucket(rate=public_rate, capacity=1.0)
        self._private_rate_limiter = _TokenBucket(rate=2.0, capacity=2.0)
        self._public_lock = threading.Lock()
        self._public_min_interval = float(
            self.requests_params.get(
                "public_min_interval_seconds",
                os.getenv("KRAKEN_PUBLIC_MIN_INTERVAL_SECONDS", "1.35"),
            )
        )
        self._last_public_request_at = 0.0
        self._circuit_lock = threading.Lock()
        self._circuit_open_until = 0.0
        self._public_circuit_open_until = 0.0
        self._private_circuit_open_until = 0.0
        self._failure_count = 0
        logger.info("[KRAKEN] Client Kraken Spot initialise")

    # ------------------------------------------------------------------
    # Low-level REST, auth and resilience
    # ------------------------------------------------------------------
    def _timeout(self) -> float:
        return float(self.requests_params.get("timeout", 30))

    @staticmethod
    def _lock_nonce_file(handle: Any) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, _NONCE_LOCK_BYTES)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    @staticmethod
    def _unlock_nonce_file(handle: Any) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, _NONCE_LOCK_BYTES)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _parse_nonce(raw: str) -> int:
        try:
            return int(str(raw).strip() or "0")
        except (TypeError, ValueError):
            return 0

    def _next_shared_nonce_locked(self, minimum: int = 0) -> int:
        self._nonce_state_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._nonce_state_path), os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+", encoding="ascii", newline="") as handle:
            self._lock_nonce_file(handle)
            try:
                handle.seek(0)
                persisted_nonce = self._parse_nonce(handle.read())
                next_nonce = max(time.time_ns(), persisted_nonce + 1, self._last_nonce + 1, minimum)
                handle.seek(0)
                handle.truncate()
                handle.write(str(next_nonce))
                handle.flush()
                os.fsync(handle.fileno())
                self._last_nonce = next_nonce
                return next_nonce
            finally:
                self._unlock_nonce_file(handle)

    def _nonce(self) -> str:
        with self._nonce_lock:
            return str(self._next_shared_nonce_locked())

    def _bump_nonce_after_invalid(self, used_nonce: Any) -> None:
        minimum = time.time_ns() + 1_000_000
        parsed_used_nonce = self._parse_nonce(str(used_nonce))
        if parsed_used_nonce > 0:
            minimum = max(minimum, parsed_used_nonce + 1)
        with self._nonce_lock:
            bumped = self._next_shared_nonce_locked(minimum=minimum)
        logger.warning("[KRAKEN-NONCE] Nonce partage avance apres rejet Kraken: %s -> %s", used_nonce, bumped)

    def _raise_if_circuit_open(self, scope: str = "global") -> None:
        with self._circuit_lock:
            open_until = self._circuit_open_until
            if scope == "public":
                open_until = max(open_until, self._public_circuit_open_until)
            elif scope == "private":
                open_until = max(open_until, self._private_circuit_open_until)
        if open_until > time.time():
            raise CircuitOpenError(
                f"Circuit breaker Kraken ouvert ({open_until - time.time():.0f}s restantes)."
            )

    def _open_circuit(self, seconds: float, reason: str, scope: str = "global") -> None:
        with self._circuit_lock:
            until = time.time() + seconds
            if scope == "public":
                self._public_circuit_open_until = max(self._public_circuit_open_until, until)
            elif scope == "private":
                self._private_circuit_open_until = max(self._private_circuit_open_until, until)
            else:
                self._circuit_open_until = max(self._circuit_open_until, until)
            self._failure_count = 0
        logger.critical("[KRAKEN-CIRCUIT] Circuit %s ouvert %.0fs: %s", scope, seconds, reason)

    def _record_success(self) -> None:
        with self._circuit_lock:
            self._failure_count = 0

    def _record_failure(self, reason: str) -> None:
        with self._circuit_lock:
            self._failure_count += 1
            failures = self._failure_count
        if failures >= 5:
            self._open_circuit(60.0, reason)

    def _headers(self, path: str, data: Mapping[str, Any]) -> Dict[str, str]:
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode("utf-8")
        message = path.encode("utf-8") + hashlib.sha256(encoded).digest()
        try:
            secret = base64.b64decode(self.api_secret)
        except Exception:
            secret = self.api_secret.encode("utf-8")
        signature = hmac.new(secret, message, hashlib.sha512)
        sigdigest = base64.b64encode(signature.digest()).decode("ascii")
        return {"API-Key": self.api_key, "API-Sign": sigdigest}

    def _handle_kraken_errors(self, errors: Iterable[str], operation: str) -> None:
        err_list = [str(err) for err in errors if str(err)]
        if not err_list:
            return
        msg = "; ".join(err_list)
        lower = msg.lower()
        scope = "private" if operation.startswith("private") else "public" if operation.startswith("public") else "global"
        if "rate limit" in lower or "too many requests" in lower:
            self._open_circuit(60.0, f"{operation}: {msg}", scope=scope)
            raise CircuitOpenError(f"Kraken rate-limit: {msg}")
        if "nonce" in lower:
            self._open_circuit(15.0, f"{operation}: {msg}", scope="private")
            raise CircuitOpenError(f"Kraken nonce error: {msg}")
        if "permission denied" in lower or "invalid key" in lower:
            raise ExchangePermissionError(f"Kraken permission error on {operation}: {msg}")
        if "maintenance" in lower or "service unavailable" in lower:
            self._open_circuit(120.0, f"{operation}: {msg}", scope=scope)
            raise CircuitOpenError(f"Kraken maintenance: {msg}")
        raise OrderError(f"Kraken error on {operation}: {msg}")

    @staticmethod
    def _is_nonce_error(errors: Iterable[str]) -> bool:
        return any("nonce" in str(err).lower() for err in errors if str(err))

    def _public(self, endpoint: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        self._raise_if_circuit_open("public")
        if not self._public_rate_limiter.acquire():
            raise CircuitOpenError("Kraken public rate limiter timeout")
        try:
            with self._public_lock:
                now = time.monotonic()
                wait_seconds = self._last_public_request_at + self._public_min_interval - now
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                self._last_public_request_at = time.monotonic()
                url = f"{self.api_url}/0/public/{endpoint}"
                response = self.session.get(url, params=dict(params or {}), timeout=self._timeout())
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "60"))
                    self._open_circuit(retry_after, f"HTTP 429 public {endpoint}", scope="public")
                    raise CircuitOpenError(f"Kraken HTTP 429; retry after {retry_after:.0f}s")
                response.raise_for_status()
                payload = response.json()
                self._handle_kraken_errors(payload.get("error", []), f"public {endpoint}")
                self._record_success()
                return cast(Dict[str, Any], payload.get("result", {}))
        except (CircuitOpenError, OrderError):
            raise
        except Exception as exc:
            self._record_failure(str(exc))
            raise

    def _private(self, endpoint: str, data: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        self._raise_if_circuit_open("private")
        if not self._private_rate_limiter.acquire():
            raise CircuitOpenError("Kraken private rate limiter timeout")
        with self._private_lock:
            self._raise_if_circuit_open("private")
            path = f"/0/private/{endpoint}"
            url = f"{self.api_url}{path}"
            for attempt in range(2):
                payload_data: Dict[str, Any] = dict(data or {})
                payload_data["nonce"] = self._nonce()
                try:
                    response = self.session.post(
                        url,
                        data=payload_data,
                        headers=self._headers(path, payload_data),
                        timeout=self._timeout(),
                    )
                    if response.status_code == 429:
                        retry_after = float(response.headers.get("Retry-After", "60"))
                        self._open_circuit(retry_after, f"HTTP 429 private {endpoint}", scope="private")
                        raise CircuitOpenError(f"Kraken HTTP 429; retry after {retry_after:.0f}s")
                    response.raise_for_status()
                    payload = response.json()
                    errors = payload.get("error", [])
                    if attempt == 0 and self._is_nonce_error(errors):
                        self._bump_nonce_after_invalid(payload_data["nonce"])
                        time.sleep(0.25)
                        continue
                    self._handle_kraken_errors(errors, f"private {endpoint}")
                    self._record_success()
                    return cast(Dict[str, Any], payload.get("result", {}))
                except (CircuitOpenError, ExchangePermissionError, OrderError):
                    raise
                except Exception as exc:
                    self._record_failure(str(exc))
                    raise
            raise CircuitOpenError("Kraken private nonce retry exhausted")

    # ------------------------------------------------------------------
    # Symbol resolution and exchange metadata
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", symbol.upper())

    @staticmethod
    def _canonical_asset(asset: str) -> str:
        aliases = {
            "XXBT": "BTC",
            "XBT": "BTC",
            "XETH": "ETH",
            "ZUSD": "USD",
            "ZEUR": "EUR",
            "ZGBP": "GBP",
            "ZCAD": "CAD",
            "ZAUD": "AUD",
        }
        return aliases.get(asset.upper(), asset.upper())

    @staticmethod
    def _split_internal_symbol(symbol: str) -> Tuple[str, str]:
        normalized = KrakenSpotClient._normalize_symbol(symbol)
        for quote in _KNOWN_QUOTES:
            if normalized.endswith(quote) and len(normalized) > len(quote):
                return normalized[:-len(quote)], quote
        raise ValueError(f"Impossible de determiner base/quote Kraken pour {symbol}")

    @staticmethod
    def _decimal_step(decimals: Any) -> Decimal:
        places = int(decimals or 0)
        return Decimal(1).scaleb(-places)

    @staticmethod
    def _ms_to_kraken_time(value: Any) -> str:
        """Convert a Binance-style millisecond timestamp to Kraken seconds."""
        try:
            numeric = Decimal(str(value))
        except Exception:
            return str(value)
        if numeric > Decimal("100000000000"):
            numeric = numeric / Decimal("1000")
        return format(numeric.normalize(), "f")

    @staticmethod
    def _history_scan_limit(env_name: str, default: int, requested_limit: int) -> int:
        try:
            configured = int(os.getenv(env_name, str(default)) or default)
        except ValueError:
            configured = default
        return max(requested_limit, configured)

    def get_server_time(self, **_kwargs: Any) -> Dict[str, Any]:
        result = self._public("Time")
        unixtime = int(result.get("unixtime", time.time()))
        return {"serverTime": unixtime * 1000}

    def ping(self) -> Dict[str, Any]:
        self.get_server_time()
        return {}

    def _sync_server_time(self) -> None:
        return None

    def _sync_server_time_robust(self) -> None:
        return None

    def _perform_ultra_robust_sync(self) -> None:
        return None

    def _get_synchronized_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _get_ultra_safe_timestamp(self) -> int:
        return int(time.time() * 1000)

    def get_asset_pairs(self, *, force: bool = False) -> Dict[str, Any]:
        if self._asset_pairs_cache is None or force:
            self._asset_pairs_cache = self._public("AssetPairs", {"assetVersion": 1})
        return self._asset_pairs_cache

    def _iter_pair_candidates(self, symbol: str) -> Iterable[Tuple[str, Mapping[str, Any]]]:
        normalized = self._normalize_symbol(symbol)
        base, quote = self._split_internal_symbol(normalized)
        base_aliases = {base}
        if base == "BTC":
            base_aliases.add("XBT")
        if base == "XBT":
            base_aliases.add("BTC")
        expected_norms = {f"{b}{quote}" for b in base_aliases}
        expected_ws = {f"{b}/{quote}" for b in base_aliases}

        pairs = self.get_asset_pairs()
        for pair_id, info_obj in pairs.items():
            info = cast(Mapping[str, Any], info_obj)
            altname = str(info.get("altname", ""))
            wsname = str(info.get("wsname", ""))
            norm_values = {
                self._normalize_symbol(str(pair_id)),
                self._normalize_symbol(altname),
                self._normalize_symbol(wsname),
            }
            if norm_values & expected_norms or wsname.upper() in expected_ws:
                yield str(pair_id), info

    def list_pair_candidates(self, symbol: str) -> List[Dict[str, str]]:
        """List Kraken Spot candidates for the requested base asset."""
        key = self._normalize_symbol(symbol)
        if key in self._pair_candidates_cache:
            return list(self._pair_candidates_cache[key])
        base, _quote = self._split_internal_symbol(key)
        base_aliases = {base}
        if base == "BTC":
            base_aliases.add("XBT")
        if base == "XBT":
            base_aliases.add("BTC")
        candidates: List[Dict[str, str]] = []
        for pair_id, info_obj in self.get_asset_pairs().items():
            info = cast(Mapping[str, Any], info_obj)
            wsname = str(info.get("wsname") or pair_id)
            altname = str(info.get("altname") or "")
            base_raw = self._canonical_asset(str(info.get("base") or ""))
            if base_raw not in base_aliases:
                continue
            candidates.append({
                "pair_id": str(pair_id),
                "broker_symbol": wsname,
                "altname": altname,
                "base": base_raw,
                "quote": self._canonical_asset(str(info.get("quote") or "")),
                "status": str(info.get("status", "online")),
            })
        candidates.sort(key=lambda item: item.get("broker_symbol", ""))
        self._pair_candidates_cache[key] = candidates
        return list(candidates)

    def resolve_pair(self, symbol: str) -> PairConfig:
        key = self._normalize_symbol(symbol)
        if key in self._pair_config_cache:
            return self._pair_config_cache[key]

        candidates = list(self._iter_pair_candidates(symbol))
        if not candidates:
            discovered = self.list_pair_candidates(symbol)
            discovered_text = ", ".join(
                f"{item['broker_symbol']} status={item['status']}" for item in discovered
            ) or "aucun candidat meme base"
            raise ValueError(
                f"Paire Kraken indisponible via API Spot: {symbol}; candidats: {discovered_text}"
            )
        pair_id, info = candidates[0]
        wsname = str(info.get("wsname") or pair_id)
        base, quote = self._split_internal_symbol(symbol)
        base_asset = self._canonical_asset(str(info.get("base") or base))
        quote_asset = self._canonical_asset(str(info.get("quote") or quote))
        tick_size = self._decimal_step(info.get("pair_decimals", 2))
        step_size = self._decimal_step(info.get("lot_decimals", 8))
        min_qty = Decimal(str(info.get("ordermin", "0")))
        min_notional = Decimal(str(info.get("costmin", "0.5")))
        status = str(info.get("status", "online"))
        pair_config = PairConfig(
            pair_key=key,
            broker_symbol=wsname,
            broker_pair_id=pair_id,
            base_asset=base_asset,
            quote_asset=quote_asset,
            quote_for_accounting=quote_asset,
            min_qty=min_qty,
            step_size=step_size,
            tick_size=tick_size,
            min_notional=min_notional,
            status=status,
        )
        self._pair_config_cache[key] = pair_config
        alias_values = {
            key,
            pair_id,
            wsname,
            str(info.get("altname") or ""),
            f"{base_asset}{quote_asset}",
            f"{base_asset}/{quote_asset}",
        }
        self._pair_aliases_cache[key] = {
            self._normalize_symbol(alias.replace("XBT", "BTC"))
            for alias in alias_values
            if alias
        }
        return pair_config

    def _matches_pair(self, raw_pair: Any, pair: PairConfig) -> bool:
        raw = str(raw_pair or "")
        raw_aliases = {
            self._normalize_symbol(raw),
            self._normalize_symbol(raw.replace("XBT", "BTC")),
        }
        pair_aliases = set(self._pair_aliases_cache.get(pair.pair_key, set()))
        pair_aliases.update({
            self._normalize_symbol(pair.pair_key),
            self._normalize_symbol(pair.broker_pair_id),
            self._normalize_symbol(pair.broker_symbol),
            self._normalize_symbol(f"{pair.base_asset}{pair.quote_asset}"),
            self._normalize_symbol(f"{pair.base_asset}/{pair.quote_asset}"),
        })
        return bool(raw_aliases & pair_aliases)

    def get_symbol_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        try:
            pair = self.resolve_pair(symbol)
        except Exception:
            return None
        return {
            "symbol": pair.pair_key,
            "status": "TRADING" if pair.status == "online" else pair.status.upper(),
            "baseAsset": pair.base_asset,
            "quoteAsset": pair.quote_asset,
            "broker_symbol": pair.broker_symbol,
            "broker_pair_id": pair.broker_pair_id,
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0",
                    "maxPrice": "0",
                    "tickSize": str(pair.tick_size),
                },
                {
                    "filterType": "LOT_SIZE",
                    "minQty": str(pair.min_qty),
                    "maxQty": "0",
                    "stepSize": str(pair.step_size),
                },
                {
                    "filterType": "MIN_NOTIONAL",
                    "minNotional": str(pair.min_notional),
                },
            ],
        }

    def get_exchange_info(self, **_kwargs: Any) -> Dict[str, Any]:
        symbols: List[Dict[str, Any]] = []
        for pair_id, info_obj in self.get_asset_pairs().items():
            info = cast(Mapping[str, Any], info_obj)
            wsname = str(info.get("wsname") or pair_id)
            if "/" not in wsname:
                continue
            base, quote = wsname.split("/", 1)
            base = "BTC" if base == "XBT" else base
            internal = self._normalize_symbol(f"{base}{quote}")
            symbol_info = self.get_symbol_info(internal)
            if symbol_info is not None:
                symbols.append(symbol_info)
        return {"symbols": symbols}

    def preflight_pairs(self, pairs: Iterable[str]) -> Dict[str, PairConfig]:
        resolved: Dict[str, PairConfig] = {}
        for pair in pairs:
            cfg = self.resolve_pair(pair)
            if cfg.status != "online":
                raise ValueError(f"Paire Kraken non online: {pair} status={cfg.status}")
            resolved[pair] = cfg
        return resolved

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------
    def get_symbol_ticker(self, **kwargs: Any) -> Dict[str, Any]:
        symbol = str(kwargs.get("symbol"))
        pair = self.resolve_pair(symbol)
        result = self._public("Ticker", {"pair": pair.broker_pair_id})
        first = next(iter(result.values()))
        return {"symbol": pair.pair_key, "price": str(first["c"][0])}

    def get_all_tickers(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        result = self._public("Ticker")
        tickers: List[Dict[str, Any]] = []
        for pair_id, ticker in result.items():
            try:
                info = cast(Mapping[str, Any], self.get_asset_pairs().get(pair_id, {}))
                wsname = str(info.get("wsname") or pair_id)
                if "/" not in wsname:
                    continue
                base, quote = wsname.split("/", 1)
                base = "BTC" if base == "XBT" else base
                tickers.append({
                    "symbol": self._normalize_symbol(f"{base}{quote}"),
                    "price": str(ticker["c"][0]),
                })
            except Exception:
                continue
        return tickers

    @staticmethod
    def _parse_start(start_date: str) -> int:
        raw = str(start_date).strip().lower()
        match = re.match(r"^(\d+)\s+(minute|minutes|min|hour|hours|day|days)\s+ago$", raw)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            seconds = amount * 60
            if unit.startswith("hour"):
                seconds = amount * 3600
            elif unit.startswith("day"):
                seconds = amount * 86400
            return int(time.time() - seconds)
        for fr_month, en_month in _FRENCH_MONTHS.items():
            raw = re.sub(rf"\b{fr_month}\b", en_month, raw)
        raw_ascii = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
        for fr_month, en_month in _FRENCH_MONTHS.items():
            raw_ascii = re.sub(rf"\b{fr_month}\b", en_month, raw_ascii)
        parsed = cast(Any, pd.Timestamp(raw_ascii))
        return int(parsed.timestamp())

    @staticmethod
    def _history_required_bars(interval: str) -> int:
        return _HISTORY_REQUIRED_BARS.get(str(interval), 1500)

    @staticmethod
    def _format_history_ts(timestamp_s: int) -> str:
        if timestamp_s <= 0:
            return ""
        parsed = cast(Any, pd.Timestamp(timestamp_s, unit="s", tz="UTC"))
        return str(parsed.isoformat())

    @staticmethod
    def _csv_timestamp_to_seconds(value: Any) -> int:
        text = str(value).strip()
        if re.fullmatch(r"\d+(\.\d+)?", text):
            raw = float(text)
            if raw > 10_000_000_000:
                raw /= 1000.0
            return int(raw)
        parsed = cast(Any, pd.Timestamp(text))
        return int(parsed.timestamp())

    def _csv_history_candidates(self, pair_key: str, interval: str) -> List[Path]:
        base_dir = os.environ.get("KRAKEN_HISTORICAL_DATA_DIR", "").strip()
        if not base_dir:
            return []
        root = Path(base_dir)
        interval_text = str(interval)
        interval_minutes = str(_KRAKEN_INTERVALS.get(interval_text, interval_text))
        interval_candidates = list(dict.fromkeys([interval_text, interval_minutes]))
        paths: List[Path] = []
        for candidate in interval_candidates:
            paths.extend(
                [
                    root / f"{pair_key}_{candidate}.csv",
                    root / f"{pair_key}-{candidate}.csv",
                    root / pair_key / f"{candidate}.csv",
                ]
            )
        return paths

    def _csv_row_to_ohlc(
        self,
        row: Mapping[str, Any],
        interval_minutes: int,
        requested_since: int,
    ) -> Optional[Tuple[int, List[Any]]]:
        ts = self._csv_timestamp_to_seconds(row["timestamp"])
        if ts < requested_since:
            return None
        open_ms = ts * 1000
        close_ms = open_ms + interval_minutes * 60 * 1000 - 1
        return ts, [
            open_ms,
            row["open"],
            row["high"],
            row["low"],
            row["close"],
            row["volume"],
            close_ms,
            "0",
            int(float(row.get("trades", 0) or 0)),
            "0",
            "0",
            "0",
        ]

    def _read_csv_history_file(
        self,
        path: Path,
        interval_minutes: int,
        requested_since: int,
    ) -> Dict[int, List[Any]]:
        rows_by_ts: Dict[int, List[Any]] = {}
        with path.open(newline="", encoding="utf-8") as fh:
            sample = fh.readline()
            fh.seek(0)
            first_fields = [field.strip().lower() for field in sample.strip().split(",")]
            has_header = "timestamp" in first_fields and "open" in first_fields
            if has_header:
                reader = csv.DictReader(fh)
                required = {"timestamp", "open", "high", "low", "close", "volume"}
                if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                    raise ValueError(
                        f"CSV historique Kraken invalide {path}: colonnes requises {sorted(required)}"
                    )
                for rec in reader:
                    parsed = self._csv_row_to_ohlc(rec, interval_minutes, requested_since)
                    if parsed is not None:
                        ts, row = parsed
                        rows_by_ts[ts] = row
                return rows_by_ts

            reader_plain = csv.reader(fh)
            for fields in reader_plain:
                if not fields or len(fields) < 6:
                    continue
                rec = {
                    "timestamp": fields[0],
                    "open": fields[1],
                    "high": fields[2],
                    "low": fields[3],
                    "close": fields[4],
                    "volume": fields[5],
                    "trades": fields[6] if len(fields) > 6 else 0,
                }
                parsed = self._csv_row_to_ohlc(rec, interval_minutes, requested_since)
                if parsed is not None:
                    ts, row = parsed
                    rows_by_ts[ts] = row
        return rows_by_ts

    def _has_csv_history_file(self, pair_key: str, interval: str) -> bool:
        return any(path.exists() for path in self._csv_history_candidates(pair_key, interval))

    def has_historical_csv(self, symbol: str, interval: str) -> bool:
        pair = self.resolve_pair(symbol)
        return self._has_csv_history_file(pair.pair_key, str(interval))

    def has_deep_historical_source(self, symbol: str, interval: str) -> bool:
        return self.has_historical_csv(symbol, interval)

    def _history_required_bars_for_interval(self, interval: str) -> int:
        return self._history_required_bars(interval)

    def get_history_required_bars(self, interval: str) -> int:
        return self._history_required_bars_for_interval(str(interval))

    def expected_history_bars(self, interval: str) -> int:
        return self._history_required_bars_for_interval(str(interval))

    def csv_history_available(self, symbol: str, interval: str) -> bool:
        return self.has_historical_csv(symbol, interval)

    def historical_csv_available(self, symbol: str, interval: str) -> bool:
        return self.has_historical_csv(symbol, interval)

    def _load_csv_history(self, pair_key: str, interval: str, requested_since: int) -> Dict[int, List[Any]]:
        rows_by_ts: Dict[int, List[Any]] = {}
        for path in self._csv_history_candidates(pair_key, interval):
            if not path.exists():
                continue
            interval_minutes = _KRAKEN_INTERVALS.get(str(interval), 60)
            rows_by_ts.update(self._read_csv_history_file(path, interval_minutes, requested_since))
            if rows_by_ts:
                logger.info("[KRAKEN-HISTORY] CSV charge %s %s: %d bougies (%s)", pair_key, interval, len(rows_by_ts), path)
                break
        return rows_by_ts

    def _record_history_status(
        self,
        pair: PairConfig,
        interval: str,
        requested_since: int,
        rows_by_ts: Mapping[int, List[Any]],
        *,
        source: str,
    ) -> None:
        timestamps = sorted(rows_by_ts)
        oldest = timestamps[0] if timestamps else 0
        newest = timestamps[-1] if timestamps else 0
        bars_required = self._history_required_bars(interval)
        bars_available = len(timestamps)
        status = KrakenHistoryStatus(
            pair_key=pair.pair_key,
            broker_symbol=pair.broker_symbol,
            timeframe=str(interval),
            requested_start=self._format_history_ts(requested_since),
            oldest_available=self._format_history_ts(oldest),
            newest_available=self._format_history_ts(newest),
            bars_available=bars_available,
            bars_required=bars_required,
            source=source,
            eligible=bars_available >= bars_required,
            history_depth_limited=oldest > requested_since or bars_available < bars_required,
        )
        self._last_history_status[(pair.pair_key, str(interval))] = status

    def get_history_status(self, symbol: str, interval: str) -> Optional[KrakenHistoryStatus]:
        key = self._normalize_symbol(symbol)
        return self._last_history_status.get((key, str(interval)))

    def get_historical_klines(
        self,
        symbol: str,
        interval: str,
        start_str: str,
        *_args: Any,
        **_kwargs: Any,
    ) -> List[List[Any]]:
        pair = self.resolve_pair(symbol)
        interval_minutes = _KRAKEN_INTERVALS.get(str(interval), 60)
        since = self._parse_start(start_str)
        requested_since = since
        rows_by_ts: Dict[int, List[Any]] = self._load_csv_history(pair.pair_key, str(interval), requested_since)
        source = "csv" if rows_by_ts else "api"
        if rows_by_ts:
            since = max(rows_by_ts)
        result = self._public(
            "OHLC",
            {
                "pair": pair.broker_pair_id,
                "interval": interval_minutes,
                "since": since,
                "assetVersion": 1,
            },
        )
        pair_rows = next((v for k, v in result.items() if k != "last"), [])
        api_rows_added = 0
        for row in pair_rows:
            ts = int(float(row[0]))
            open_ms = ts * 1000
            close_ms = open_ms + interval_minutes * 60 * 1000 - 1
            if ts not in rows_by_ts:
                api_rows_added += 1
            rows_by_ts[ts] = [
                open_ms,
                row[1],
                row[2],
                row[3],
                row[4],
                row[6],
                close_ms,
                "0",
                int(float(row[7])) if len(row) > 7 else 0,
                "0",
                "0",
                "0",
            ]
        if rows_by_ts and source == "csv" and api_rows_added:
            source = "csv+api"
        self._record_history_status(
            pair,
            str(interval),
            requested_since,
            rows_by_ts,
            source=source,
        )
        return [rows_by_ts[ts] for ts in sorted(rows_by_ts)]

    # ------------------------------------------------------------------
    # Account and orders
    # ------------------------------------------------------------------
    @staticmethod
    def _decimal_field(raw: Mapping[str, Any], name: str, default: str = "0") -> Decimal:
        try:
            return Decimal(str(raw.get(name, default) or default))
        except Exception:
            return Decimal(default)

    def get_account(self, **_kwargs: Any) -> Dict[str, Any]:
        result = self._private("BalanceEx")
        balances = []
        for asset, raw_balance in result.items():
            if isinstance(raw_balance, Mapping):
                balance = self._decimal_field(raw_balance, "balance")
                credit = self._decimal_field(raw_balance, "credit")
                credit_used = self._decimal_field(raw_balance, "credit_used")
                hold_trade = self._decimal_field(raw_balance, "hold_trade")
            else:
                balance = Decimal(str(raw_balance or "0"))
                credit = Decimal("0")
                credit_used = Decimal("0")
                hold_trade = Decimal("0")
            free = balance + credit - credit_used - hold_trade
            if free < 0:
                free = Decimal("0")
            balances.append({
                "asset": self._canonical_asset(str(asset)),
                "free": str(free),
                "locked": str(hold_trade),
            })
        return {
            "makerCommission": 10,
            "takerCommission": 10,
            "balances": balances,
        }

    @staticmethod
    def _status_to_binance(status: str, vol: Any = None, vol_exec: Any = None) -> str:
        raw = status.lower()
        if raw == "closed":
            return "FILLED"
        if raw in {"canceled", "expired"}:
            return "CANCELED"
        try:
            if Decimal(str(vol_exec or "0")) > 0 and Decimal(str(vol or "0")) > Decimal(str(vol_exec or "0")):
                return "PARTIALLY_FILLED"
        except Exception:
            pass
        return "NEW"

    def _map_order(self, txid: str, raw_order: Mapping[str, Any]) -> Dict[str, Any]:
        descr = cast(Mapping[str, Any], raw_order.get("descr", {}))
        pair_name = str(descr.get("pair", raw_order.get("pair", "")))
        symbol = self._normalize_symbol(pair_name.replace("XBT", "BTC"))
        vol = raw_order.get("vol", "0")
        vol_exec = raw_order.get("vol_exec", "0")
        price = raw_order.get("price") or descr.get("price") or "0"
        cost = raw_order.get("cost", "0")
        client_id = str(raw_order.get("cl_ord_id") or raw_order.get("userref") or "")
        return {
            "symbol": symbol,
            "broker_pair": pair_name,
            "orderId": txid,
            "clientOrderId": client_id,
            "origClientOrderId": client_id,
            "status": self._status_to_binance(str(raw_order.get("status", "")), vol, vol_exec),
            "type": self._map_order_type_to_binance(str(descr.get("ordertype", ""))),
            "side": str(descr.get("type", "")).upper(),
            "price": str(price),
            "stopPrice": str(descr.get("price", "")),
            "origQty": str(vol),
            "executedQty": str(vol_exec),
            "cummulativeQuoteQty": str(cost),
            "time": int(float(raw_order.get("opentm", time.time())) * 1000),
            "updateTime": int(float(raw_order.get("closetm", raw_order.get("opentm", time.time()))) * 1000),
            "fills": [],
        }

    @staticmethod
    def _map_order_type_to_binance(order_type: str) -> str:
        mapping = {
            "market": "MARKET",
            "limit": "LIMIT",
            "stop-loss": "STOP_LOSS",
            "stop-loss-limit": "STOP_LOSS_LIMIT",
            "take-profit": "TAKE_PROFIT",
            "take-profit-limit": "TAKE_PROFIT_LIMIT",
            "trailing-stop": "STOP_LOSS",
            "trailing-stop-limit": "STOP_LOSS_LIMIT",
        }
        return mapping.get(order_type.lower(), order_type.upper())

    def get_open_orders(self, **kwargs: Any) -> List[Dict[str, Any]]:
        symbol = kwargs.get("symbol")
        pair_id = self.resolve_pair(str(symbol)).broker_pair_id if symbol else None
        result = self._private("OpenOrders", {"trades": "true"})
        orders = result.get("open", {})
        mapped = [self._map_order(str(txid), cast(Mapping[str, Any], raw)) for txid, raw in orders.items()]
        if pair_id:
            cfg = self.resolve_pair(str(symbol))
            mapped = [o for o in mapped if self._normalize_symbol(o.get("symbol", "")) == cfg.pair_key]
        return mapped

    def find_order_by_client_order_id(
        self,
        client_order_id: str,
        *,
        symbol: Optional[str] = None,
        limit: int = 100,
    ) -> Optional[Dict[str, Any]]:
        client_id = str(client_order_id)
        mapped_txid = self._client_order_map.get(client_id)
        if mapped_txid:
            try:
                return self.get_order(orderId=mapped_txid)
            except Exception:
                self._client_order_map.pop(client_id, None)
        candidates: List[Dict[str, Any]] = []
        try:
            candidates.extend(self.get_open_orders(symbol=symbol) if symbol else self.get_open_orders())
        except Exception as exc:
            logger.debug("[KRAKEN] OpenOrders lookup cl_ord_id=%s indisponible: %s", client_id, exc)
        try:
            candidates.extend(self.get_all_orders(symbol=symbol, limit=limit))
        except Exception as exc:
            logger.debug("[KRAKEN] ClosedOrders lookup cl_ord_id=%s indisponible: %s", client_id, exc)
        for order in candidates:
            if str(order.get("clientOrderId") or order.get("origClientOrderId") or "") == client_id:
                txid = str(order.get("orderId") or "")
                if txid:
                    self._client_order_map[client_id] = txid
                return order
        return None

    def get_order(self, **kwargs: Any) -> Dict[str, Any]:
        order_id = kwargs.get("orderId") or kwargs.get("txid")
        client_id = kwargs.get("origClientOrderId")
        if not order_id and client_id:
            order_id = self._client_order_map.get(str(client_id))
            if not order_id:
                recovered = self.find_order_by_client_order_id(
                    str(client_id),
                    symbol=kwargs.get("symbol"),
                )
                if recovered:
                    return recovered
        if not order_id:
            raise OrderError("Kraken get_order requires orderId or known origClientOrderId")
        result = self._private("QueryOrders", {"txid": str(order_id), "trades": "true"})
        raw = cast(Mapping[str, Any], result.get(str(order_id), {}))
        if not raw:
            raise OrderError(f"Kraken order introuvable: {order_id}")
        return self._map_order(str(order_id), raw)

    def get_all_orders(self, **kwargs: Any) -> List[Dict[str, Any]]:
        symbol = kwargs.get("symbol")
        limit = int(kwargs.get("limit", 50) or 50)
        orders = self.get_open_orders(symbol=symbol) if symbol else self.get_open_orders()
        pair_cfg = self.resolve_pair(str(symbol)) if symbol else None
        scan_limit = self._history_scan_limit("KRAKEN_CLOSED_ORDERS_SCAN_LIMIT", 250, limit)
        try:
            seen_order_ids = {str(order.get("orderId") or "") for order in orders}
            ofs = 0
            while ofs < scan_limit:
                params: Dict[str, Any] = {"trades": "true"}
                if ofs:
                    params["ofs"] = ofs
                closed = self._private("ClosedOrders", params)
                closed_orders = closed.get("closed", {})
                if not isinstance(closed_orders, Mapping) or not closed_orders:
                    break
                page_count = 0
                for txid, raw in closed_orders.items():
                    page_count += 1
                    txid_str = str(txid)
                    if txid_str in seen_order_ids:
                        continue
                    mapped = self._map_order(txid_str, cast(Mapping[str, Any], raw))
                    if pair_cfg and not self._matches_pair(
                        mapped.get("broker_pair") or mapped.get("symbol"),
                        pair_cfg,
                    ):
                        continue
                    orders.append(mapped)
                    seen_order_ids.add(txid_str)
                if pair_cfg and len(orders) >= limit:
                    break
                ofs += page_count
                try:
                    total_count = int(closed.get("count", 0) or 0)
                except Exception:
                    total_count = 0
                if page_count <= 0 or page_count < 50 or (total_count and ofs >= total_count):
                    break
        except Exception as exc:
            logger.debug("[KRAKEN] ClosedOrders indisponible: %s", exc)
        if pair_cfg:
            orders = [
                o for o in orders
                if self._matches_pair(o.get("broker_pair") or o.get("symbol", ""), pair_cfg)
            ]
        return sorted(orders, key=lambda o: int(o.get("updateTime", 0)), reverse=True)[:limit]

    def get_my_trades(self, **kwargs: Any) -> List[Dict[str, Any]]:
        symbol = kwargs.get("symbol")
        limit = int(kwargs.get("limit", 100) or 100)
        pair = self.resolve_pair(str(symbol)) if symbol else None
        mapped: List[Dict[str, Any]] = []
        seen_trade_ids: set[str] = set()
        scan_limit = self._history_scan_limit("KRAKEN_TRADES_HISTORY_SCAN_LIMIT", 1000, limit)
        ofs = 0
        while ofs < scan_limit:
            params: Dict[str, Any] = {"trades": "true"}
            if ofs:
                params["ofs"] = ofs
            if kwargs.get("startTime") is not None:
                params["start"] = self._ms_to_kraken_time(kwargs.get("startTime"))
            if kwargs.get("endTime") is not None:
                params["end"] = self._ms_to_kraken_time(kwargs.get("endTime"))
            result = self._private("TradesHistory", params)
            trades = result.get("trades", {})
            if not isinstance(trades, Mapping) or not trades:
                break
            page_count = 0
            for trade_id, raw_obj in trades.items():
                page_count += 1
                trade_id_str = str(trade_id)
                if trade_id_str in seen_trade_ids:
                    continue
                raw = cast(Mapping[str, Any], raw_obj)
                if pair and not self._matches_pair(raw.get("pair"), pair):
                    continue
                qty = str(raw.get("vol", "0"))
                price = str(raw.get("price", "0"))
                cost = str(raw.get("cost", "0"))
                side = str(raw.get("type", "")).lower()
                mapped.append({
                    "id": trade_id_str,
                    "orderId": raw.get("ordertxid"),
                    "symbol": pair.pair_key if pair else str(raw.get("pair", "")),
                    "broker_pair": str(raw.get("pair", "")),
                    "isBuyer": side == "buy",
                    "isMaker": str(raw.get("ordertype", "")) == "limit",
                    "qty": qty,
                    "price": price,
                    "quoteQty": cost,
                    "commission": str(raw.get("fee", "0")),
                    "commissionAsset": pair.quote_asset if pair else "",
                    "time": int(float(raw.get("time", time.time())) * 1000),
                })
                seen_trade_ids.add(trade_id_str)
            if pair and len(mapped) >= limit:
                break
            ofs += page_count
            try:
                total_count = int(result.get("count", 0) or 0)
            except Exception:
                total_count = 0
            if page_count <= 0 or page_count < 50 or (total_count and ofs >= total_count):
                break
        return sorted(mapped, key=lambda t: int(t.get("time", 0)), reverse=True)[:limit]

    def get_trade_fee(self, **kwargs: Any) -> List[Dict[str, Any]]:
        symbol = kwargs.get("symbol")
        if not symbol:
            raise OrderError("Kraken TradeVolume requires an explicit symbol for real fee lookup")
        pair = self.resolve_pair(str(symbol))
        result = self._private(
            "TradeVolume",
            {"pair": pair.broker_pair_id, "fee-info": "true"},
        )
        fees = result.get("fees", {})
        maker_fees = result.get("fees_maker", {})
        if not isinstance(fees, Mapping) or not fees:
            raise OrderError(f"Kraken TradeVolume returned no taker fee for {pair.broker_symbol}")
        pair_fee = cast(Mapping[str, Any], fees.get(pair.broker_pair_id) or next(iter(fees.values())))
        maker_fee = (
            cast(Mapping[str, Any], maker_fees.get(pair.broker_pair_id))
            if isinstance(maker_fees, Mapping) and maker_fees.get(pair.broker_pair_id)
            else cast(Mapping[str, Any], next(iter(maker_fees.values()), {}))
            if isinstance(maker_fees, Mapping)
            else {}
        )
        taker_raw = pair_fee.get("fee")
        maker_raw = maker_fee.get("fee") or pair_fee.get("fee_maker")
        if taker_raw is None:
            raise OrderError(f"Kraken TradeVolume missing taker fee for {pair.broker_symbol}")
        if maker_raw is None:
            raise OrderError(f"Kraken TradeVolume missing maker fee for {pair.broker_symbol}")
        taker = Decimal(str(taker_raw)) / Decimal("100")
        maker = Decimal(str(maker_raw)) / Decimal("100")
        return [{
            "symbol": pair.pair_key,
            "broker_symbol": pair.broker_symbol,
            "takerCommission": str(taker),
            "makerCommission": str(maker),
            "source": "api",
        }]

    def preflight_private_api(self) -> KrakenPreflightResult:
        public_ok = False
        balance_ok = False
        open_orders_ok = False
        closed_orders_ok = False
        permission_error: Optional[str] = None
        nonce_error: Optional[str] = None

        def _record_error(exc: Exception) -> None:
            nonlocal permission_error, nonce_error
            msg = str(exc)
            if isinstance(exc, ExchangePermissionError) or "permission denied" in msg.lower():
                permission_error = msg
            if "nonce" in msg.lower():
                nonce_error = msg

        try:
            self.ping()
            public_ok = True
        except Exception as exc:
            logger.warning("[KRAKEN-PREFLIGHT] Public API KO: %s", exc)

        try:
            self.get_account()
            balance_ok = True
        except Exception as exc:
            _record_error(exc)
            logger.warning("[KRAKEN-PREFLIGHT] Balance KO: %s", exc)

        try:
            self.get_open_orders()
            open_orders_ok = True
        except Exception as exc:
            _record_error(exc)
            logger.warning("[KRAKEN-PREFLIGHT] OpenOrders KO: %s", exc)

        try:
            self._private("ClosedOrders", {"trades": "true"})
            closed_orders_ok = True
        except Exception as exc:
            _record_error(exc)
            logger.warning("[KRAKEN-PREFLIGHT] ClosedOrders KO: %s", exc)

        private_api_ok = balance_ok and open_orders_ok and closed_orders_ok
        return KrakenPreflightResult(
            public_ok=public_ok,
            balance_ok=balance_ok,
            open_orders_ok=open_orders_ok,
            closed_orders_ok=closed_orders_ok,
            private_api_ok=private_api_ok,
            permission_error=permission_error,
            nonce_error=nonce_error,
            tradable=public_ok and private_api_ok,
        )

    @staticmethod
    def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
        if step <= 0:
            return value
        return (value / step).to_integral_value(rounding=ROUND_DOWN) * step

    def _wait_for_order_fill(self, txid: str, timeout: float = 10.0) -> Dict[str, Any]:
        deadline = time.time() + timeout
        last: Optional[Dict[str, Any]] = None
        while time.time() < deadline:
            try:
                order = self.get_order(orderId=txid)
                last = order
                if order.get("status") in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                    return order
            except Exception:
                pass
            time.sleep(0.5)
        return last or {"orderId": txid, "status": "NEW"}

    def order_market_buy(self, **kwargs: Any) -> Dict[str, Any]:
        symbol = str(kwargs["symbol"])
        quote_order_qty = kwargs.get("quoteOrderQty")
        client_id = kwargs.get("newClientOrderId") or kwargs.get("origClientOrderId")
        if quote_order_qty is None:
            raise OrderError("Kraken market buy requires quoteOrderQty")
        pair = self.resolve_pair(symbol)
        quote_amount = Decimal(str(quote_order_qty))
        if quote_amount < pair.min_notional:
            raise OrderError(
                f"Kraken BUY notional {quote_amount} < minNotional {pair.min_notional}",
                symbol=symbol,
            )
        data: Dict[str, Any] = {
            "pair": pair.broker_pair_id,
            "type": "buy",
            "ordertype": "market",
            "volume": format(quote_amount, "f"),
            "oflags": "viqc",
        }
        if client_id:
            data["cl_ord_id"] = str(client_id)
        result = self._private("AddOrder", data)
        txid = str(result.get("txid", [""])[0])
        if client_id and txid:
            self._client_order_map[str(client_id)] = txid
        filled = self._wait_for_order_fill(txid)
        filled.setdefault("orderId", txid)
        filled.setdefault("clientOrderId", str(client_id or ""))
        filled.setdefault("executedQty", "0")
        filled.setdefault("cummulativeQuoteQty", str(quote_amount))
        return filled

    def order_market_sell(self, **kwargs: Any) -> Dict[str, Any]:
        symbol = str(kwargs["symbol"])
        quantity = Decimal(str(kwargs["quantity"]))
        client_id = kwargs.get("newClientOrderId") or kwargs.get("origClientOrderId")
        pair = self.resolve_pair(symbol)
        qty = self._floor_to_step(quantity, pair.step_size)
        if qty < pair.min_qty:
            raise OrderError(f"Kraken SELL qty {qty} < minQty {pair.min_qty}", symbol=symbol)
        data: Dict[str, Any] = {
            "pair": pair.broker_pair_id,
            "type": "sell",
            "ordertype": "market",
            "volume": format(qty, "f"),
        }
        if client_id:
            data["cl_ord_id"] = str(client_id)
        result = self._private("AddOrder", data)
        txid = str(result.get("txid", [""])[0])
        if client_id and txid:
            self._client_order_map[str(client_id)] = txid
        filled = self._wait_for_order_fill(txid)
        filled.setdefault("orderId", txid)
        filled.setdefault("clientOrderId", str(client_id or ""))
        filled.setdefault("executedQty", format(qty, "f"))
        return filled

    def create_order(self, **kwargs: Any) -> Dict[str, Any]:
        symbol = str(kwargs["symbol"])
        side = str(kwargs.get("side", "")).upper()
        order_type = str(kwargs.get("type", "")).upper()
        quantity = Decimal(str(kwargs.get("quantity")))
        client_id = kwargs.get("newClientOrderId") or kwargs.get("origClientOrderId")
        pair = self.resolve_pair(symbol)
        qty = self._floor_to_step(quantity, pair.step_size)
        if qty < pair.min_qty:
            raise OrderError(f"Kraken order qty {qty} < minQty {pair.min_qty}", symbol=symbol)
        kraken_type = "buy" if side == "BUY" else "sell"
        kraken_order_type = {
            "MARKET": "market",
            "LIMIT": "limit",
            "STOP_LOSS": "stop-loss",
            "STOP_LOSS_LIMIT": "stop-loss-limit",
            "TAKE_PROFIT": "take-profit",
            "TAKE_PROFIT_LIMIT": "take-profit-limit",
        }.get(order_type, order_type.lower())
        data: Dict[str, Any] = {
            "pair": pair.broker_pair_id,
            "type": kraken_type,
            "ordertype": kraken_order_type,
            "volume": format(qty, "f"),
        }
        if "stopPrice" in kwargs:
            stop = self._floor_to_step(Decimal(str(kwargs["stopPrice"])), pair.tick_size)
            data["price"] = format(stop, "f")
        if "price" in kwargs and order_type in {"LIMIT", "STOP_LOSS_LIMIT", "TAKE_PROFIT_LIMIT"}:
            price = self._floor_to_step(Decimal(str(kwargs["price"])), pair.tick_size)
            data["price"] = format(price, "f")
        if client_id:
            data["cl_ord_id"] = str(client_id)
        result = self._private("AddOrder", data)
        txid = str(result.get("txid", [""])[0])
        if client_id and txid:
            self._client_order_map[str(client_id)] = txid
        return {
            "symbol": pair.pair_key,
            "orderId": txid,
            "clientOrderId": str(client_id or ""),
            "status": "NEW",
            "type": order_type,
            "side": side,
            "origQty": format(qty, "f"),
            "executedQty": "0",
            "stopPrice": str(kwargs.get("stopPrice", "")),
        }

    def cancel_order(self, **kwargs: Any) -> Dict[str, Any]:
        order_id = str(kwargs.get("orderId") or kwargs.get("txid"))
        result = self._private("CancelOrder", {"txid": order_id})
        return {"orderId": order_id, "status": "CANCELED", "result": result}

    def close_connection(self) -> None:
        self.session.close()


def broker_symbol_from_pair_config(pair: PairConfig) -> str:
    return pair.broker_symbol


def qty_decimals_from_step(step: Decimal) -> int:
    if step <= 0:
        return 8
    return max(0, int(math.ceil(-step.log10())))
