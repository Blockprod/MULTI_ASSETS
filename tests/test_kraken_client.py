from __future__ import annotations

import base64
import threading
import time
from types import SimpleNamespace
from decimal import Decimal

import pytest

import exchange_client as ec
from broker_models import BrokerConfig
from exceptions import ExchangePermissionError, OrderError
from kraken_client import KrakenSpotClient


def _client(requests_params: dict[str, object] | None = None) -> KrakenSpotClient:
    secret = base64.b64encode(b"test-secret").decode("ascii")
    return KrakenSpotClient(
        BrokerConfig(
            broker="KRAKEN",
            api_key="key",
            api_secret=secret,
            api_url="https://api.kraken.test",
            ws_url="wss://ws-auth.kraken.test/v2",
        ),
        requests_params=requests_params,
    )


def _asset_pairs() -> dict:
    return {
        "ONDOUSD": {
            "altname": "ONDOUSD",
            "wsname": "ONDO/USD",
            "base": "ONDO",
            "quote": "ZUSD",
            "ordermin": "15",
            "costmin": "0.5",
            "pair_decimals": 5,
            "lot_decimals": 5,
            "status": "online",
        },
        "PEPE/USD": {
            "altname": "PEPEUSD",
            "wsname": "PEPE/USD",
            "base": "PEPE",
            "quote": "ZUSD",
            "ordermin": "1500000",
            "costmin": "0.5",
            "pair_decimals": 9,
            "lot_decimals": 5,
            "status": "online",
        },
        "XRP/USDC": {
            "altname": "XRPUSDC",
            "wsname": "XRP/USDC",
            "base": "XRP",
            "quote": "USDC",
            "ordermin": "1",
            "costmin": "0.5",
            "pair_decimals": 4,
            "lot_decimals": 6,
            "status": "online",
        },
        "CROUSDC": {
            "altname": "CROUSDC",
            "wsname": "CRO/USDC",
            "base": "CRO",
            "quote": "USDC",
            "ordermin": "90",
            "costmin": "0.5",
            "pair_decimals": 5,
            "lot_decimals": 2,
            "status": "online",
        },
    }


def test_nonce_is_monotonic() -> None:
    client = _client()

    values = [int(client._nonce()) for _ in range(5)]  # pylint: disable=protected-access

    assert values == sorted(values)
    assert len(set(values)) == 5


def test_nonce_is_shared_between_client_instances(tmp_path) -> None:  # noqa: ANN001
    nonce_path = tmp_path / "kraken_nonce_state.txt"
    client_a = _client({"nonce_state_file": str(nonce_path)})
    client_b = _client({"nonce_state_file": str(nonce_path)})

    first = int(client_a._nonce())  # pylint: disable=protected-access
    second = int(client_b._nonce())  # pylint: disable=protected-access

    assert second > first
    assert int(nonce_path.read_text(encoding="ascii")) == second


def test_private_retries_once_after_invalid_nonce(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    client = _client({"nonce_state_file": str(tmp_path / "kraken_nonce_state.txt")})
    nonces: list[int] = []

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def __init__(self, errors: list[str], result: dict) -> None:
            self._errors = errors
            self._result = result

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"error": self._errors, "result": self._result}

    responses = [
        FakeResponse(["EAPI:Invalid nonce"], {}),
        FakeResponse([], {"ok": True}),
    ]

    def fake_post(_url: str, data=None, headers=None, timeout=None):  # noqa: ANN001
        assert isinstance(data, dict)
        nonces.append(int(data["nonce"]))
        return responses.pop(0)

    monkeypatch.setattr(client.session, "post", fake_post)

    result = client._private("BalanceEx")  # pylint: disable=protected-access

    assert result == {"ok": True}
    assert len(nonces) == 2
    assert nonces[1] > nonces[0]
    assert client._private_circuit_open_until == 0.0  # pylint: disable=protected-access


def test_signature_matches_kraken_rest_auth_example() -> None:
    client = KrakenSpotClient(
        BrokerConfig(
            broker="KRAKEN",
            api_key="key",
            api_secret="kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg==",
            api_url="https://api.kraken.test",
            ws_url="wss://ws-auth.kraken.test/v2",
        )
    )
    payload = {
        "nonce": "1616492376594",
        "ordertype": "limit",
        "pair": "XBTUSD",
        "price": 37500,
        "type": "buy",
        "volume": 1.25,
    }

    headers = client._headers("/0/private/AddOrder", payload)  # pylint: disable=protected-access

    assert headers["API-Sign"] == (
        "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="
    )


def test_parse_start_accepts_french_and_iso_dates() -> None:
    french = KrakenSpotClient._parse_start("29 juin 2023")  # pylint: disable=protected-access
    iso = KrakenSpotClient._parse_start("2023-06-29")  # pylint: disable=protected-access

    assert french == iso


def test_private_calls_are_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    active = 0
    max_active = 0
    nonces: list[int] = []
    lock = threading.Lock()

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"error": [], "result": {"ok": True}}

    def fake_post(_url: str, data=None, headers=None, timeout=None):  # noqa: ANN001
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            assert isinstance(data, dict)
            nonces.append(int(data["nonce"]))
        time.sleep(0.01)
        with lock:
            active -= 1
        return FakeResponse()

    monkeypatch.setattr(client.session, "post", fake_post)

    threads = [threading.Thread(target=lambda: client._private("Balance")) for _ in range(8)]  # pylint: disable=protected-access
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active == 1
    assert nonces == sorted(nonces)
    assert len(set(nonces)) == len(nonces)


def test_permission_denied_is_preflight_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"error": ["EGeneral:Permission denied"], "result": {}}

    monkeypatch.setattr(client.session, "post", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(client, "ping", lambda: {})

    with pytest.raises(ExchangePermissionError):
        client._private("Balance")  # pylint: disable=protected-access

    result = client.preflight_private_api()
    assert result.balance_ok is False
    assert result.private_api_ok is False
    assert result.tradable is False
    assert result.permission_error is not None


def test_resolve_pair_maps_kraken_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "_public", lambda endpoint, params=None: _asset_pairs())

    cfg = client.resolve_pair("ONDOUSD")
    info = client.get_symbol_info("ONDOUSD")

    assert cfg.broker_symbol == "ONDO/USD"
    assert cfg.broker_pair_id == "ONDOUSD"
    assert cfg.base_asset == "ONDO"
    assert cfg.quote_asset == "USD"
    assert cfg.min_qty == Decimal("15")
    assert info is not None
    assert info["filters"][0]["filterType"] == "PRICE_FILTER"
    assert info["filters"][1]["stepSize"] == "0.00001"
    assert info["filters"][2]["minNotional"] == "0.5"


def test_pepe_usdc_unavailable_when_assetpairs_has_only_usd(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "_public", lambda endpoint, params=None: _asset_pairs())

    with pytest.raises(ValueError, match="PEPEUSDC.*PEPE/USD"):
        client.resolve_pair("PEPEUSDC")
    candidates = client.list_pair_candidates("PEPEUSDC")
    assert candidates[0]["broker_symbol"] == "PEPE/USD"
    assert candidates[0]["quote"] == "USD"


def test_pepe_usdc_resolves_when_assetpairs_confirms_usdc(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    pairs = dict(_asset_pairs())
    pairs["PEPE/USDC"] = {
        "altname": "PEPEUSDC",
        "wsname": "PEPE/USDC",
        "base": "PEPE",
        "quote": "USDC",
        "ordermin": "1500000",
        "costmin": "0.5",
        "pair_decimals": 9,
        "lot_decimals": 5,
        "status": "online",
    }
    monkeypatch.setattr(client, "_public", lambda endpoint, params=None: pairs)

    cfg = client.resolve_pair("PEPEUSDC")

    assert cfg.broker_symbol == "PEPE/USDC"
    assert cfg.quote_asset == "USDC"


def test_xrp_usdc_resolves_as_configured_kraken_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "_public", lambda endpoint, params=None: _asset_pairs())

    cfg = client.resolve_pair("XRPUSDC")
    info = client.get_symbol_info("XRPUSDC")

    assert cfg.broker_symbol == "XRP/USDC"
    assert cfg.broker_pair_id == "XRP/USDC"
    assert cfg.base_asset == "XRP"
    assert cfg.quote_asset == "USDC"
    assert cfg.min_qty == Decimal("1")
    assert info is not None
    assert info["filters"][1]["stepSize"] == "0.000001"


def test_get_trade_fee_uses_real_trade_volume_fees(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "_public", lambda endpoint, params=None: _asset_pairs())

    def fake_private(endpoint: str, data=None):
        assert endpoint == "TradeVolume"
        assert data == {"pair": "XRP/USDC", "fee-info": "true"}
        return {
            "fees": {"XRP/USDC": {"fee": "0.2600"}},
            "fees_maker": {"XRP/USDC": {"fee": "0.1600"}},
        }

    monkeypatch.setattr(client, "_private", fake_private)

    fee = client.get_trade_fee(symbol="XRPUSDC")[0]

    assert fee["source"] == "api"
    assert fee["broker_symbol"] == "XRP/USDC"
    assert fee["takerCommission"] == "0.0026"
    assert fee["makerCommission"] == "0.0016"


def test_get_trade_fee_does_not_fallback_when_api_fee_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "_public", lambda endpoint, params=None: _asset_pairs())
    monkeypatch.setattr(client, "_private", lambda endpoint, data=None: {"fees": {}})

    with pytest.raises(OrderError):
        client.get_trade_fee(symbol="XRPUSDC")


def test_public_rate_limit_opens_public_circuit_only() -> None:
    client = _client()

    with pytest.raises(ec.CircuitOpenError):
        client._handle_kraken_errors(  # pylint: disable=protected-access
            ["EGeneral:Too many requests"],
            "public OHLC",
        )

    assert client._public_circuit_open_until > time.time()  # pylint: disable=protected-access
    assert client._private_circuit_open_until == 0.0  # pylint: disable=protected-access


def test_public_rate_limit_defaults_are_conservative() -> None:
    client = _client()

    assert client._public_rate_limiter._rate <= 0.5  # pylint: disable=protected-access
    assert client._public_min_interval >= 1.0  # pylint: disable=protected-access


def test_ohlc_is_returned_in_binance_kline_shape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    client = _client()
    monkeypatch.setenv("KRAKEN_HISTORICAL_DATA_DIR", str(tmp_path))

    def fake_public(endpoint: str, params=None):
        if endpoint == "AssetPairs":
            return _asset_pairs()
        if endpoint == "OHLC":
            return {
                "XRP/USDC": [
                    [1_700_000_000, "100", "110", "90", "105", "101", "12.5", 17],
                ],
                "last": 1_700_000_000,
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_public", fake_public)

    rows = client.get_historical_klines("XRPUSDC", "1h", "2024-01-01")

    assert len(rows) == 1
    assert rows[0][0] == 1_700_000_000_000
    assert rows[0][1:6] == ["100", "110", "90", "105", "12.5"]
    assert len(rows[0]) == 12


def test_ohlc_uses_single_official_recent_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    client = _client()
    monkeypatch.setenv("KRAKEN_HISTORICAL_DATA_DIR", str(tmp_path))
    calls: list[dict] = []

    def fake_public(endpoint: str, params=None):
        if endpoint == "AssetPairs":
            return _asset_pairs()
        if endpoint == "OHLC":
            params_dict = dict(params or {})
            calls.append(params_dict)
            return {
                "XRP/USDC": [[1_700_003_600, "101", "102", "100", "101", "101", "2", 1]],
                "last": 1_700_003_600,
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_public", fake_public)

    rows = client.get_historical_klines("XRPUSDC", "1h", "2023-11-14")
    status = client.get_history_status("XRPUSDC", "1h")

    assert len(rows) == 1
    assert len(calls) == 1
    assert calls[0]["assetVersion"] == 1
    assert all(call["interval"] == 60 for call in calls)
    assert status is not None
    assert status.history_depth_limited is True


def test_ohlc_csv_import_records_history_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    client = _client()
    monkeypatch.setenv("KRAKEN_HISTORICAL_DATA_DIR", str(tmp_path))
    csv_path = tmp_path / "XRPUSDC_1h.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2024-01-01T00:00:00Z,100,110,90,105,12.5\n"
        "2024-01-01T01:00:00Z,105,111,100,108,8.0\n",
        encoding="utf-8",
    )

    def fake_public(endpoint: str, params=None):
        if endpoint == "AssetPairs":
            return _asset_pairs()
        if endpoint == "OHLC":
            return {"XRP/USDC": [], "last": int(time.time())}
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_public", fake_public)

    rows = client.get_historical_klines("XRPUSDC", "1h", "2024-01-01")
    status = client.get_history_status("XRPUSDC", "1h")

    assert len(rows) == 2
    assert status is not None
    assert status.source == "csv"
    assert status.bars_available == 2
    assert status.bars_required == 1500
    assert status.eligible is False


def test_ohlc_csv_import_accepts_headerless_numeric_interval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    client = _client()
    monkeypatch.setenv("KRAKEN_HISTORICAL_DATA_DIR", str(tmp_path))
    csv_path = tmp_path / "ONDOUSD_60.csv"
    csv_path.write_text(
        "1704067200,2000,2010,1990,2005,12.5,3\n"
        "1704070800,2005,2020,2000,2012,8.0,2\n",
        encoding="utf-8",
    )

    def fake_public(endpoint: str, params=None):
        if endpoint == "AssetPairs":
            return _asset_pairs()
        if endpoint == "OHLC":
            return {"ONDOUSD": [], "last": int(time.time())}
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_public", fake_public)

    rows = client.get_historical_klines("ONDOUSD", "1h", "2024-01-01")
    status = client.get_history_status("ONDOUSD", "1h")

    assert len(rows) == 2
    assert rows[0][0] == 1_704_067_200_000
    assert rows[0][1:6] == ["2000", "2010", "1990", "2005", "12.5"]
    assert rows[0][8] == 3
    assert status is not None
    assert status.source == "csv"
    assert status.bars_available == 2


def test_get_account_uses_balance_ex_available_and_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()

    def fake_private(endpoint: str, data=None):
        assert endpoint == "BalanceEx"
        return {
            "XETH": {
                "balance": "0.20",
                "credit": "0.01",
                "credit_used": "0.02",
                "hold_trade": "0.05",
            },
            "USDC": {
                "balance": "100.0",
                "credit": "0",
                "credit_used": "0",
                "hold_trade": "12.5",
            },
        }

    monkeypatch.setattr(client, "_private", fake_private)

    account = client.get_account()
    balances = {item["asset"]: item for item in account["balances"]}

    assert balances["ETH"]["free"] == "0.14"
    assert balances["ETH"]["locked"] == "0.05"
    assert balances["USDC"]["free"] == "87.5"
    assert balances["USDC"]["locked"] == "12.5"


def test_market_buy_quote_budget_uses_viqc_and_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()

    def fake_public(endpoint: str, params=None):
        if endpoint == "AssetPairs":
            return _asset_pairs()
        raise AssertionError(endpoint)

    private_calls = []

    def fake_private(endpoint: str, data=None):
        private_calls.append((endpoint, dict(data or {})))
        if endpoint == "AddOrder":
            return {"txid": ["ORDER-1"]}
        if endpoint == "QueryOrders":
            return {
                "ORDER-1": {
                    "status": "closed",
                    "vol": "1.0",
                    "vol_exec": "1.0",
                    "cost": "100.0",
                    "descr": {"pair": "XRP/USDC", "type": "buy", "ordertype": "market"},
                    "opentm": 1_700_000_000,
                    "closetm": 1_700_000_001,
                }
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_public", fake_public)
    monkeypatch.setattr(client, "_private", fake_private)

    order = client.order_market_buy(
        symbol="XRPUSDC",
        quoteOrderQty=100.0,
        newClientOrderId="buy-test",
    )

    assert order["status"] == "FILLED"
    assert order["orderId"] == "ORDER-1"
    assert private_calls[0][0] == "AddOrder"
    assert Decimal(private_calls[0][1]["volume"]) == Decimal("100.0")
    assert private_calls[0][1]["oflags"] == "viqc"
    assert private_calls[0][1]["cl_ord_id"] == "buy-test"


def test_market_buy_rejects_below_min_notional(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()

    def fake_public(endpoint: str, params=None):
        if endpoint == "AssetPairs":
            return _asset_pairs()
        if endpoint == "Ticker":
            return {"XRP/USDC": {"c": ["100.0", "1"]}}
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_public", fake_public)

    with pytest.raises(OrderError, match="minNotional|minQty"):
        client.order_market_buy(symbol="XRPUSDC", quoteOrderQty=0.01)


def test_get_order_lookup_scans_closed_orders_by_client_order_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()

    def fake_public(endpoint: str, params=None):
        if endpoint == "AssetPairs":
            return _asset_pairs()
        raise AssertionError(endpoint)

    def fake_private(endpoint: str, data=None):
        if endpoint == "OpenOrders":
            return {"open": {}}
        if endpoint == "ClosedOrders":
            return {
                "closed": {
                    "ORDER-42": {
                        "status": "closed",
                        "vol": "1.0",
                        "vol_exec": "1.0",
                        "cost": "100.0",
                        "cl_ord_id": "kbabcdef123456",
                        "descr": {"pair": "XRP/USDC", "type": "buy", "ordertype": "market"},
                        "opentm": 1_700_000_000,
                        "closetm": 1_700_000_001,
                    }
                }
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(client, "_public", fake_public)
    monkeypatch.setattr(client, "_private", fake_private)

    order = client.get_order(symbol="XRPUSDC", origClientOrderId="kbabcdef123456")

    assert order["status"] == "FILLED"
    assert order["orderId"] == "ORDER-42"
    assert order["clientOrderId"] == "kbabcdef123456"
    assert client._client_order_map["kbabcdef123456"] == "ORDER-42"  # pylint: disable=protected-access


def test_get_my_trades_paginates_and_matches_kraken_pair_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    monkeypatch.setattr(client, "_public", lambda endpoint, params=None: _asset_pairs())
    calls: list[dict] = []

    def non_matching_trade(index: int) -> dict:
        return {
            "pair": "ONDOUSD",
            "type": "buy",
            "vol": "1",
            "price": "1",
            "cost": "1",
            "fee": "0.01",
            "ordertxid": f"ONDO-{index}",
            "time": 1_700_000_000 - index,
            "ordertype": "market",
        }

    def fake_private(endpoint: str, data=None):
        assert endpoint == "TradesHistory"
        params = dict(data or {})
        calls.append(params)
        ofs = int(params.get("ofs", 0) or 0)
        if ofs == 0:
            return {
                "count": 51,
                "trades": {f"ONDO-T{i}": non_matching_trade(i) for i in range(50)},
            }
        if ofs == 50:
            return {
                "count": 51,
                "trades": {
                    "CRO-T1": {
                        "pair": "CRO/USDC",
                        "type": "sell",
                        "vol": "1000",
                        "price": "0.0600",
                        "cost": "60.0",
                        "fee": "0.156",
                        "ordertxid": "CRO-SELL-1",
                        "time": 1_700_000_100,
                        "ordertype": "market",
                    }
                },
            }
        return {"count": 51, "trades": {}}

    monkeypatch.setattr(client, "_private", fake_private)

    trades = client.get_my_trades(symbol="CROUSDC", limit=1)

    assert len(trades) == 1
    assert trades[0]["orderId"] == "CRO-SELL-1"
    assert trades[0]["symbol"] == "CROUSDC"
    assert trades[0]["broker_pair"] == "CRO/USDC"
    assert trades[0]["isBuyer"] is False
    assert trades[0]["quoteQty"] == "60.0"
    assert calls[0] == {"trades": "true"}
    assert calls[1] == {"trades": "true", "ofs": 50}


def test_get_my_trades_maps_start_time_to_kraken_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    monkeypatch.setattr(client, "_public", lambda endpoint, params=None: _asset_pairs())
    calls: list[dict] = []

    def fake_private(endpoint: str, data=None):
        assert endpoint == "TradesHistory"
        calls.append(dict(data or {}))
        return {"count": 0, "trades": {}}

    monkeypatch.setattr(client, "_private", fake_private)

    assert client.get_my_trades(symbol="CROUSDC", startTime=1_700_000_000_123, limit=1) == []
    assert calls == [{"trades": "true", "start": "1700000000.123"}]


def test_get_all_orders_paginates_closed_orders_for_symbol_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    monkeypatch.setattr(client, "_public", lambda endpoint, params=None: _asset_pairs())
    calls: list[tuple[str, dict]] = []

    def fake_private(endpoint: str, data=None):
        params = dict(data or {})
        calls.append((endpoint, params))
        if endpoint == "OpenOrders":
            return {"open": {}}
        assert endpoint == "ClosedOrders"
        ofs = int(params.get("ofs", 0) or 0)
        if ofs == 0:
            return {
                "count": 51,
                "closed": {
                    f"ONDO-ORDER-{i}": {
                        "status": "closed",
                        "vol": "1",
                        "vol_exec": "1",
                        "cost": "1",
                        "descr": {"pair": "ONDO/USD", "type": "buy", "ordertype": "market"},
                        "opentm": 1_700_000_000 - i,
                        "closetm": 1_700_000_001 - i,
                    }
                    for i in range(50)
                },
            }
        if ofs == 50:
            return {
                "count": 51,
                "closed": {
                    "CRO-SELL-ORDER": {
                        "status": "closed",
                        "vol": "1000",
                        "vol_exec": "1000",
                        "cost": "60.0",
                        "descr": {"pair": "CRO/USDC", "type": "sell", "ordertype": "market"},
                        "opentm": 1_700_000_100,
                        "closetm": 1_700_000_101,
                    }
                },
            }
        return {"count": 51, "closed": {}}

    monkeypatch.setattr(client, "_private", fake_private)

    orders = client.get_all_orders(symbol="CROUSDC", limit=1)

    assert len(orders) == 1
    assert orders[0]["orderId"] == "CRO-SELL-ORDER"
    assert orders[0]["side"] == "SELL"
    assert orders[0]["symbol"] == "CROUSDC"
    assert calls[0] == ("OpenOrders", {"trades": "true"})
    assert calls[1] == ("ClosedOrders", {"trades": "true"})
    assert calls[2] == ("ClosedOrders", {"trades": "true", "ofs": 50})


def test_exchange_helpers_delegate_to_kraken(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeKraken:
        broker = "KRAKEN"

        def __init__(self) -> None:
            self.calls = []

        def order_market_buy(self, **kwargs):
            self.calls.append(("buy", kwargs))
            return {"orderId": "B1", "status": "FILLED"}

        def order_market_sell(self, **kwargs):
            self.calls.append(("sell", kwargs))
            return {"orderId": "S1", "status": "FILLED"}

        def create_order(self, **kwargs):
            self.calls.append(("sl", kwargs))
            return {"orderId": "SL1", "status": "NEW"}

    fake = FakeKraken()
    monkeypatch.setattr(ec, "_config", SimpleNamespace(bot_mode="LIVE", recv_window=60000))

    buy = ec.safe_market_buy(fake, "XRPUSDC", quoteOrderQty=100.0)
    sell = ec.safe_market_sell(fake, "XRPUSDC", quantity="1")
    stop = ec.place_exchange_stop_loss(fake, "XRPUSDC", "1", 90.0)

    assert buy["status"] == "FILLED"
    assert sell["status"] == "FILLED"
    assert stop["status"] == "NEW"
    assert fake.calls[0][0] == "buy"
    assert fake.calls[0][1]["quoteOrderQty"] == 100.0
    assert fake.calls[1][0] == "sell"
    assert fake.calls[2][0] == "sl"
    assert fake.calls[2][1]["type"] == "STOP_LOSS"
