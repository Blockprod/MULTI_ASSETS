"""Minimal type stub for binance.client (python-binance 1.0.35).

Covers only the Client methods and class-level constants actually used
in MULTI_ASSETS.  Public attributes api_key and api_secret are accessed
directly in _direct_market_order (exchange_client.py) for HMAC signing.
"""

from typing import Any


class Client:
    # ── KLINE interval constants used in cache_manager.py ──────────────────
    KLINE_INTERVAL_1MINUTE: str
    KLINE_INTERVAL_3MINUTE: str
    KLINE_INTERVAL_5MINUTE: str
    KLINE_INTERVAL_15MINUTE: str
    KLINE_INTERVAL_30MINUTE: str
    KLINE_INTERVAL_1HOUR: str
    KLINE_INTERVAL_2HOUR: str
    KLINE_INTERVAL_4HOUR: str
    KLINE_INTERVAL_6HOUR: str
    KLINE_INTERVAL_8HOUR: str
    KLINE_INTERVAL_12HOUR: str
    KLINE_INTERVAL_1DAY: str
    KLINE_INTERVAL_3DAY: str
    KLINE_INTERVAL_1WEEK: str
    KLINE_INTERVAL_1MONTH: str

    # ── Public attributes used for HMAC signing ─────────────────────────────
    api_key: str
    api_secret: str

    # ── Internal session object (accessed by close_connection / tests) ──────
    session: Any

    def __init__(
        self,
        api_key: str | None,
        api_secret: str | None,
        **kwargs: Any,
    ) -> None: ...

    def ping(self) -> dict[str, Any]: ...

    def _request(
        self,
        method: str,
        uri: str,
        signed: bool,
        force_params: bool = ...,
        **kwargs: Any,
    ) -> Any: ...

    def get_server_time(self) -> dict[str, int]: ...

    def get_exchange_info(self) -> dict[str, Any]: ...

    def get_symbol_info(self, symbol: str) -> dict[str, Any] | None: ...

    def get_symbol_ticker(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_all_tickers(self) -> list[dict[str, Any]]: ...

    def get_account(self) -> dict[str, Any]: ...

    def get_order(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_open_orders(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def get_all_orders(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def cancel_order(self, **kwargs: Any) -> dict[str, Any]: ...

    def create_order(self, **kwargs: Any) -> dict[str, Any]: ...

    # get_klines: raw klines by symbol/interval/limit (no date range)
    def get_klines(self, **kwargs: Any) -> list[list[Any]]: ...

    # get_historical_klines: start_str can be a date string or an int limit
    def get_historical_klines(
        self,
        symbol: str,
        interval: str,
        start_str: str | int = ...,
        end_str: str | None = ...,
        limit: int = ...,
        klines_type: Any = ...,
    ) -> list[list[Any]]: ...
