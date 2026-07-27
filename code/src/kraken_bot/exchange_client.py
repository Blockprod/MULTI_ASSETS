"""Kraken-only exchange compatibility layer.

This module intentionally does not expose BinanceFinalClient.  It provides the
small Binance-shaped helper surface consumed by the forked Kraken runtime.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Protocol

from exceptions import BalanceUnavailableError, OrderError


class ExchangePort(Protocol):
    broker: str

    def get_account(self, **kwargs: Any) -> Dict[str, Any]: ...
    def get_all_tickers(self, **kwargs: Any) -> List[Dict[str, Any]]: ...
    def get_exchange_info(self, **kwargs: Any) -> Dict[str, Any]: ...
    def get_symbol_ticker(self, **kwargs: Any) -> Dict[str, Any]: ...
    def get_symbol_info(self, symbol: str) -> Any: ...
    def get_order(self, **kwargs: Any) -> Dict[str, Any]: ...
    def get_all_orders(self, **kwargs: Any) -> List[Dict[str, Any]]: ...
    def get_open_orders(self, **kwargs: Any) -> List[Dict[str, Any]]: ...
    def get_my_trades(self, **kwargs: Any) -> List[Dict[str, Any]]: ...
    def get_trade_fee(self, **kwargs: Any) -> List[Dict[str, Any]]: ...
    def order_market_buy(self, **kwargs: Any) -> Dict[str, Any]: ...
    def order_market_sell(self, **kwargs: Any) -> Dict[str, Any]: ...
    def create_order(self, **kwargs: Any) -> Dict[str, Any]: ...
    def cancel_order(self, **kwargs: Any) -> Dict[str, Any]: ...


def set_circuit_alert_callback(_callback: Any) -> None:
    return None


def _get_coin_balance(account_info: Dict[str, Any], coin_symbol: str) -> tuple[bool, float, float, float]:
    coin_upper = coin_symbol.upper()
    for bal in account_info.get("balances", []):
        if str(bal.get("asset", "")).upper() == coin_upper:
            free = float(bal.get("free", 0) or 0)
            locked = float(bal.get("locked", 0) or 0)
            return True, free, locked, free + locked
    return False, 0.0, 0.0, 0.0


def get_spot_balance_usdc(client: ExchangePort) -> float:
    try:
        account_info = client.get_account()
    except Exception as exc:
        raise BalanceUnavailableError(f"Kraken balance unavailable: {exc}") from exc
    _, free, locked, _total = _get_coin_balance(account_info, "USDC")
    return free + locked


def get_symbol_filters(client: ExchangePort, symbol: str) -> Dict[str, Any]:
    info = client.get_symbol_info(symbol)
    if not info:
        raise OrderError(f"Kraken symbol filters unavailable: {symbol}", symbol=symbol)
    filters = info.get("filters", [])
    lot = next((f for f in filters if f.get("filterType") == "LOT_SIZE"), {})
    notional = next((f for f in filters if f.get("filterType") in {"MIN_NOTIONAL", "NOTIONAL"}), {})
    price = next((f for f in filters if f.get("filterType") == "PRICE_FILTER"), {})
    return {
        "min_qty": float(lot.get("minQty", 0) or 0),
        "max_qty": float(lot.get("maxQty", 0) or 0),
        "step_size": float(lot.get("stepSize", 0) or 0),
        "min_notional": float(notional.get("minNotional", 0) or 0),
        "tick_size": float(price.get("tickSize", 0) or 0),
    }


def _is_step_aligned(quantity: Decimal, step: Decimal) -> bool:
    if step <= 0:
        return True
    return ((quantity // step) * step) == quantity


def is_valid_stop_loss_order(
    client: ExchangePort,
    symbol: str,
    quantity: str,
    stop_price: float,
    *_args: Any,
    **_kwargs: Any,
) -> bool:
    info = client.get_symbol_info(symbol)
    if not info:
        return False
    filters = info.get("filters", [])
    lot = next((f for f in filters if f.get("filterType") == "LOT_SIZE"), {})
    price = next((f for f in filters if f.get("filterType") == "PRICE_FILTER"), {})
    notional = next((f for f in filters if f.get("filterType") in {"MIN_NOTIONAL", "NOTIONAL"}), {})
    qty_dec = Decimal(str(quantity))
    stop_dec = Decimal(str(stop_price))
    min_qty = Decimal(str(lot.get("minQty", "0") or "0"))
    step = Decimal(str(lot.get("stepSize", "0.00000001") or "0.00000001"))
    min_price = Decimal(str(price.get("minPrice", "0") or "0"))
    min_notional = Decimal(str(notional.get("minNotional", "0") or "0"))
    return (
        qty_dec > 0
        and stop_dec > 0
        and qty_dec >= min_qty
        and stop_dec >= min_price
        and qty_dec * stop_dec >= min_notional
        and _is_step_aligned(qty_dec, step)
    )


def place_stop_loss_order(client: ExchangePort, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    return place_exchange_stop_loss(client, *args, **kwargs)


def place_exchange_stop_loss(
    client: ExchangePort,
    symbol: str,
    quantity: str,
    stop_price: float,
    _limit_slippage: float = 0.005,
    send_alert: Any = None,
) -> Dict[str, Any]:
    del _limit_slippage
    result = client.create_order(
        symbol=symbol,
        side="SELL",
        type="STOP_LOSS",
        quantity=quantity,
        stopPrice=stop_price,
    )
    if send_alert:
        send_alert(
            subject=f"[KRAKEN] Stop-loss place {symbol}",
            body_main=f"STOP_LOSS Kraken place. Qty={quantity}, Stop={stop_price}, OrderId={result.get('orderId')}",
            client=client,
        )
    return result


def safe_market_buy(client: ExchangePort, symbol: str, quoteOrderQty: Any, **kwargs: Any) -> Dict[str, Any]:
    kwargs.pop("send_alert", None)
    return client.order_market_buy(symbol=symbol, quoteOrderQty=quoteOrderQty, **kwargs)


def safe_market_sell(client: ExchangePort, symbol: str, quantity: Any, **kwargs: Any) -> Dict[str, Any]:
    kwargs.pop("send_alert", None)
    return client.order_market_sell(symbol=symbol, quantity=quantity, **kwargs)


def can_execute_partial_safely(
    *,
    coin_balance: float,
    quantity: float,
    min_qty: float,
    min_notional: float,
    current_price: float,
    **_kwargs: Any,
) -> tuple[bool, str]:
    if quantity < min_qty:
        return False, "partial quantity below min_qty"
    if quantity * current_price < min_notional:
        return False, "partial notional below min_notional"
    remaining = max(coin_balance - quantity, 0.0)
    if 0 < remaining < min_qty:
        return False, "partial would leave dust below min_qty"
    return True, "ok"
