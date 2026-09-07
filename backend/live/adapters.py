"""Provider-specific live order adapters behind one deliberately narrow contract.

The shared models cover only portable intent fields. Exchange-specific request and
response data stays in ``provider_fields``/``provider_metadata`` rather than being
forced into a misleading universal order model.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

MAX_PROVIDER_RESPONSE_BYTES = 131_072


class ProviderRequestError(RuntimeError):
    """A generic, secret-free provider failure."""


class ProviderMutationDisabled(ProviderRequestError):
    """Raised by the adapter's defense-in-depth mutation gate."""


class ProviderResponseUncertain(ProviderRequestError):
    """The request may have reached the provider and requires reconciliation."""


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None = None
    time_in_force: str = "GTC"
    provider_fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderOrder:
    provider_order_id: str | None
    state: str
    provider_metadata: Mapping[str, Any]


@dataclass(frozen=True)
class ProviderFill:
    provider_fill_id: str
    quantity: Decimal
    price: Decimal
    fee: Decimal = Decimal("0")
    fee_currency: str | None = None
    filled_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


class OrderAdapter(Protocol):
    provider: str

    def submit_order(self, order: OrderRequest, client_order_id: str) -> ProviderOrder: ...

    def query_order(self, provider_order_id: str, *, symbol: str) -> ProviderOrder: ...

    def cancel_order(self, provider_order_id: str, *, symbol: str, client_order_id: str) -> ProviderOrder: ...

    def list_open_orders(self) -> list[Mapping[str, Any]]: ...

    def fetch_balances(self) -> list[Mapping[str, Any]]: ...

    def fetch_positions(self) -> list[Mapping[str, Any]]: ...

    def fetch_fills(self, *, symbol: str | None = None) -> list[ProviderFill]: ...

    def health(self) -> Mapping[str, Any]: ...


ProviderTransport = Callable[[Request, float, bool], Any]


def provider_transport(request: Request, timeout: float, mutation: bool) -> Any:
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is deployment/provider controlled
            body = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise ProviderRequestError("Provider rejected the request") from error
    except (URLError, TimeoutError) as error:
        if mutation:
            raise ProviderResponseUncertain("Provider response is uncertain; reconciliation is required") from error
        raise ProviderRequestError("Provider request failed") from error
    if len(body) > MAX_PROVIDER_RESPONSE_BYTES:
        raise ProviderRequestError("Provider response exceeded the safe limit")
    if not body:
        return {}
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProviderRequestError("Provider returned an invalid response") from error


class _AdapterBase:
    def __init__(self, *, mutations_enabled: bool, timeout: float, transport: ProviderTransport) -> None:
        self.mutations_enabled = mutations_enabled
        self.timeout = min(max(float(timeout), 1), 20)
        self.transport = transport

    def _mutation(self) -> None:
        if not self.mutations_enabled:
            raise ProviderMutationDisabled("Live provider mutations are disabled")


class DhanOrderAdapter(_AdapterBase):
    provider = "DHAN"

    def __init__(
        self,
        credentials: Mapping[str, str],
        *,
        base_url: str = "https://api.dhan.co/v2",
        mutations_enabled: bool = False,
        timeout: float = 15,
        transport: ProviderTransport = provider_transport,
    ) -> None:
        super().__init__(mutations_enabled=mutations_enabled, timeout=timeout, transport=transport)
        self.credentials = credentials
        self.base_url = _safe_base_url(base_url)

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None, *, mutation: bool = False
    ) -> Any:
        if mutation:
            self._mutation()
        data = _json_bytes(payload) if payload is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "access-token": self.credentials["accessToken"],
            },
        )
        return self.transport(request, self.timeout, mutation)

    def submit_order(self, order: OrderRequest, client_order_id: str) -> ProviderOrder:
        fields = dict(order.provider_fields)
        payload = {
            "dhanClientId": self.credentials["clientId"],
            "correlationId": client_order_id[:30],
            "transactionType": order.side.upper(),
            "exchangeSegment": fields.pop("exchangeSegment", "NSE_EQ"),
            "productType": fields.pop("productType", "CNC"),
            "orderType": order.order_type.upper(),
            "validity": "IOC" if order.time_in_force == "IOC" else "DAY",
            "securityId": fields.pop("securityId"),
            "quantity": int(order.quantity),
            "price": str(order.price or ""),
            "triggerPrice": fields.pop("triggerPrice", ""),
            "afterMarketOrder": False,
            **fields,
        }
        response = _object(self._request("POST", "/orders", payload, mutation=True))
        return ProviderOrder(
            str(response.get("orderId") or "") or None, _dhan_state(response.get("orderStatus")), response
        )

    def query_order(self, provider_order_id: str, *, symbol: str) -> ProviderOrder:
        del symbol
        response = _object(self._request("GET", f"/orders/{provider_order_id}"))
        return ProviderOrder(provider_order_id, _dhan_state(response.get("orderStatus")), response)

    def cancel_order(self, provider_order_id: str, *, symbol: str, client_order_id: str) -> ProviderOrder:
        del symbol, client_order_id
        response = _object(self._request("DELETE", f"/orders/{provider_order_id}", mutation=True))
        return ProviderOrder(provider_order_id, _dhan_state(response.get("orderStatus")), response)

    def list_open_orders(self) -> list[Mapping[str, Any]]:
        return [
            row
            for row in _rows(self._request("GET", "/orders"))
            if _dhan_state(row.get("orderStatus")) not in _TERMINAL
        ]

    def fetch_balances(self) -> list[Mapping[str, Any]]:
        return [_object(self._request("GET", "/fundlimit"))]

    def fetch_positions(self) -> list[Mapping[str, Any]]:
        return _rows(self._request("GET", "/positions"))

    def fetch_fills(self, *, symbol: str | None = None) -> list[ProviderFill]:
        return [
            _dhan_fill(row)
            for row in _rows(self._request("GET", "/trades"))
            if symbol is None or row.get("tradingSymbol") == symbol
        ]

    def health(self) -> Mapping[str, Any]:
        self._request("GET", "/profile")
        return {"provider": self.provider, "available": True, "mutationEnabled": self.mutations_enabled}


class OkxOrderAdapter(_AdapterBase):
    provider = "OKX"

    def __init__(
        self,
        credentials: Mapping[str, str],
        *,
        base_url: str = "https://www.okx.com",
        mutations_enabled: bool = False,
        timeout: float = 15,
        transport: ProviderTransport = provider_transport,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(mutations_enabled=mutations_enabled, timeout=timeout, transport=transport)
        self.credentials = credentials
        self.base_url = _safe_base_url(base_url)
        self.clock = clock or (lambda: datetime.now(UTC))

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None, *, mutation: bool = False
    ) -> Any:
        if mutation:
            self._mutation()
        body = json.dumps(payload, separators=(",", ":"), default=str) if payload is not None else ""
        timestamp = self.clock().astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        signature = base64.b64encode(
            hmac.new(
                self.credentials["apiSecret"].encode(), f"{timestamp}{method}{path}{body}".encode(), hashlib.sha256
            ).digest()
        ).decode()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "OK-ACCESS-KEY": self.credentials["apiKey"],
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.credentials["passphrase"],
        }
        if self.credentials.get("environment") == "DEMO":
            headers["x-simulated-trading"] = "1"
        response = self.transport(
            Request(f"{self.base_url}{path}", data=body.encode() or None, method=method, headers=headers),
            self.timeout,
            mutation,
        )
        if not isinstance(response, dict) or str(response.get("code", "0")) != "0":
            raise ProviderRequestError("OKX rejected the request")
        return response

    def submit_order(self, order: OrderRequest, client_order_id: str) -> ProviderOrder:
        payload = {
            "instId": order.symbol,
            "tdMode": "cash",
            "side": order.side.lower(),
            "ordType": order.order_type.lower(),
            "sz": str(order.quantity),
            "clOrdId": client_order_id[:32],
            **dict(order.provider_fields),
        }
        if order.price is not None:
            payload["px"] = str(order.price)
        row = _first_data(self._request("POST", "/api/v5/trade/order", payload, mutation=True))
        return ProviderOrder(str(row.get("ordId") or "") or None, "ACKNOWLEDGED", row)

    def query_order(self, provider_order_id: str, *, symbol: str) -> ProviderOrder:
        path = "/api/v5/trade/order?" + urlencode({"ordId": provider_order_id, "instId": symbol})
        row = _first_data(self._request("GET", path))
        return ProviderOrder(provider_order_id, _okx_state(row.get("state")), row)

    def cancel_order(self, provider_order_id: str, *, symbol: str, client_order_id: str) -> ProviderOrder:
        del client_order_id
        row = _first_data(
            self._request(
                "POST", "/api/v5/trade/cancel-order", {"instId": symbol, "ordId": provider_order_id}, mutation=True
            )
        )
        return ProviderOrder(provider_order_id, "CANCEL_REQUESTED", row)

    def list_open_orders(self) -> list[Mapping[str, Any]]:
        return _data(self._request("GET", "/api/v5/trade/orders-pending"))

    def fetch_balances(self) -> list[Mapping[str, Any]]:
        return _data(self._request("GET", "/api/v5/account/balance"))

    def fetch_positions(self) -> list[Mapping[str, Any]]:
        return _data(self._request("GET", "/api/v5/account/positions"))

    def fetch_fills(self, *, symbol: str | None = None) -> list[ProviderFill]:
        path = "/api/v5/trade/fills" + ("?" + urlencode({"instId": symbol}) if symbol else "")
        return [_okx_fill(row) for row in _data(self._request("GET", path))]

    def health(self) -> Mapping[str, Any]:
        self._request("GET", "/api/v5/account/config")
        return {"provider": self.provider, "available": True, "mutationEnabled": self.mutations_enabled}


class ValrOrderAdapter(_AdapterBase):
    provider = "VALR"

    def __init__(
        self,
        credentials: Mapping[str, str],
        *,
        base_url: str = "https://api.valr.com",
        mutations_enabled: bool = False,
        timeout: float = 15,
        transport: ProviderTransport = provider_transport,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(mutations_enabled=mutations_enabled, timeout=timeout, transport=transport)
        self.credentials = credentials
        self.base_url = _safe_base_url(base_url)
        self.clock_ms = clock_ms or (lambda: round(time.time() * 1000))

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None, *, mutation: bool = False
    ) -> Any:
        if mutation:
            self._mutation()
        body = json.dumps(payload, separators=(",", ":"), default=str) if payload is not None else ""
        timestamp = str(self.clock_ms())
        signature = hmac.new(
            self.credentials["apiSecret"].encode(), f"{timestamp}{method}{path}{body}".encode(), hashlib.sha512
        ).hexdigest()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-VALR-API-KEY": self.credentials["apiKey"],
            "X-VALR-SIGNATURE": signature,
            "X-VALR-TIMESTAMP": timestamp,
        }
        return self.transport(
            Request(f"{self.base_url}{path}", data=body.encode() or None, method=method, headers=headers),
            self.timeout,
            mutation,
        )

    def submit_order(self, order: OrderRequest, client_order_id: str) -> ProviderOrder:
        kind = "market" if order.order_type.upper() == "MARKET" else "limit"
        payload = {
            "pair": order.symbol,
            "side": order.side.upper(),
            "quantity": str(order.quantity),
            "customerOrderId": client_order_id,
            **dict(order.provider_fields),
        }
        if order.price is not None:
            payload["price"] = str(order.price)
        response = _object(self._request("POST", f"/v1/orders/{kind}", payload, mutation=True))
        order_id = response.get("id", response.get("orderId"))
        return ProviderOrder(str(order_id or "") or None, "ACKNOWLEDGED", response)

    def query_order(self, provider_order_id: str, *, symbol: str) -> ProviderOrder:
        del symbol
        response = _object(self._request("GET", f"/v1/orders/history/summary/orderid/{provider_order_id}"))
        return ProviderOrder(provider_order_id, _valr_state(response.get("status")), response)

    def cancel_order(self, provider_order_id: str, *, symbol: str, client_order_id: str) -> ProviderOrder:
        del symbol, client_order_id
        response = _object(self._request("DELETE", f"/v1/orders/orderid/{provider_order_id}", mutation=True))
        return ProviderOrder(provider_order_id, "CANCEL_REQUESTED", response)

    def list_open_orders(self) -> list[Mapping[str, Any]]:
        return _rows(self._request("GET", "/v1/orders/open"))

    def fetch_balances(self) -> list[Mapping[str, Any]]:
        return _rows(self._request("GET", "/v1/account/balances"))

    def fetch_positions(self) -> list[Mapping[str, Any]]:
        return _rows(self._request("GET", "/v1/positions/open"))

    def fetch_fills(self, *, symbol: str | None = None) -> list[ProviderFill]:
        path = "/v1/account/tradehistory" + ("?" + urlencode({"currencyPair": symbol}) if symbol else "")
        return [_valr_fill(row) for row in _rows(self._request("GET", path))]

    def health(self) -> Mapping[str, Any]:
        self._request("GET", "/v1/account/api-keys/current")
        return {"provider": self.provider, "available": True, "mutationEnabled": self.mutations_enabled}


_TERMINAL = {"FILLED", "CANCELLED", "REJECTED"}


def _safe_base_url(value: str) -> str:
    parsed = urlparse(value)
    if not (
        (parsed.scheme == "https" and parsed.hostname)
        or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
    ):
        raise ValueError("Private provider URL must use HTTPS or loopback")
    return value.rstrip("/")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), default=str).encode()


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderRequestError("Provider returned an unexpected response")
    return value


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ProviderRequestError("Provider returned an unexpected response")
    return value


def _data(value: Any) -> list[dict[str, Any]]:
    return _rows(_object(value).get("data"))


def _first_data(value: Any) -> dict[str, Any]:
    rows = _data(value)
    if not rows:
        raise ProviderRequestError("Provider returned an empty response")
    return rows[0]


def _dhan_state(value: Any) -> str:
    return {
        "TRANSIT": "SUBMITTED",
        "PENDING": "ACKNOWLEDGED",
        "TRADED": "FILLED",
        "CANCELLED": "CANCELLED",
        "REJECTED": "REJECTED",
        "EXPIRED": "REJECTED",
        "PART_TRADED": "PARTIALLY_FILLED",
    }.get(str(value).upper(), "UNKNOWN")


def _okx_state(value: Any) -> str:
    return {
        "live": "ACKNOWLEDGED",
        "partially_filled": "PARTIALLY_FILLED",
        "filled": "FILLED",
        "canceled": "CANCELLED",
        "mmp_canceled": "CANCELLED",
    }.get(str(value).lower(), "UNKNOWN")


def _valr_state(value: Any) -> str:
    return {
        "placed": "ACKNOWLEDGED",
        "open": "ACKNOWLEDGED",
        "partially filled": "PARTIALLY_FILLED",
        "filled": "FILLED",
        "cancelled": "CANCELLED",
        "canceled": "CANCELLED",
        "failed": "REJECTED",
    }.get(str(value).lower(), "UNKNOWN")


def _parse_datetime(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)


def _dhan_fill(row: Mapping[str, Any]) -> ProviderFill:
    return ProviderFill(
        str(row.get("exchangeTradeId") or row.get("orderId")),
        Decimal(str(row.get("tradedQuantity"))),
        Decimal(str(row.get("tradedPrice"))),
        filled_at=_parse_datetime(row.get("exchangeTime")),
        provider_metadata=dict(row),
    )


def _okx_fill(row: Mapping[str, Any]) -> ProviderFill:
    timestamp = datetime.fromtimestamp(int(row.get("ts", 0)) / 1000, UTC) if row.get("ts") else datetime.now(UTC)
    return ProviderFill(
        str(row.get("tradeId")),
        Decimal(str(row.get("fillSz"))),
        Decimal(str(row.get("fillPx"))),
        abs(Decimal(str(row.get("fee", 0)))),
        str(row.get("feeCcy") or "") or None,
        timestamp,
        dict(row),
    )


def _valr_fill(row: Mapping[str, Any]) -> ProviderFill:
    return ProviderFill(
        str(row.get("id") or row.get("orderId")),
        Decimal(str(row.get("quantity", row.get("executedQuantity")))),
        Decimal(str(row.get("price", row.get("executedPrice")))),
        abs(Decimal(str(row.get("fee", row.get("executedFee", 0))))),
        str(row.get("feeCurrency") or "") or None,
        _parse_datetime(row.get("tradedAt", row.get("createdAt"))),
        dict(row),
    )
