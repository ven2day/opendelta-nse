"""Read-only private connection tests for supported exchanges."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MAX_PRIVATE_RESPONSE_BYTES = 65_536


class ConnectionTestError(RuntimeError):
    """A deliberately generic private-provider failure."""


@dataclass(frozen=True)
class ConnectionPermissionReport:
    authenticated: bool
    read: bool
    trade: bool
    withdrawal: bool | None
    ip_allowlisted: bool | None
    environment: str
    account_status: str
    message: str

    def public(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "read": self.read,
            "trade": self.trade,
            "withdrawal": self.withdrawal,
            "ipAllowlisted": self.ip_allowlisted,
            "environment": self.environment,
            "accountStatus": self.account_status,
        }


class ConnectionTester(Protocol):
    provider: str

    def test(self, credentials: Mapping[str, str]) -> ConnectionPermissionReport: ...


PrivateTransport = Callable[[Request, float], Any]


def _transport(request: Request, timeout: float) -> Any:
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed deployment/provider URL
            body = response.read(MAX_PRIVATE_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as error:
        raise ConnectionTestError("Exchange authentication request failed") from error
    if len(body) > MAX_PRIVATE_RESPONSE_BYTES:
        raise ConnectionTestError("Exchange authentication response exceeded the safe limit")
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConnectionTestError("Exchange authentication returned an invalid response") from error


class OkxConnectionTester:
    provider = "OKX"

    def __init__(
        self,
        *,
        live_base_url: str = "https://www.okx.com",
        transport: PrivateTransport = _transport,
        clock: Callable[[], datetime] | None = None,
        timeout: float = 15,
    ) -> None:
        self.live_base_url = _safe_base_url(live_base_url)
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(UTC))
        self.timeout = min(max(float(timeout), 1), 20)

    def test(self, credentials: Mapping[str, str]) -> ConnectionPermissionReport:
        path = "/api/v5/account/config"
        timestamp = self.clock().astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        signature = base64.b64encode(
            hmac.new(
                credentials["apiSecret"].encode(),
                f"{timestamp}GET{path}".encode(),
                hashlib.sha256,
            ).digest()
        ).decode()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "OK-ACCESS-KEY": credentials["apiKey"],
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": credentials["passphrase"],
        }
        environment = credentials.get("environment", "LIVE")
        if environment == "DEMO":
            headers["x-simulated-trading"] = "1"
        payload = self.transport(Request(f"{self.live_base_url}{path}", headers=headers), self.timeout)
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise ConnectionTestError("OKX authentication failed")
        rows = payload.get("data")
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise ConnectionTestError("OKX account configuration was unavailable")
        row = rows[0]
        permissions = {item.strip().lower() for item in str(row.get("perm", "")).split(",") if item.strip()}
        withdrawal = "withdraw" in permissions
        read = "read_only" in permissions or "read" in permissions
        return ConnectionPermissionReport(
            authenticated=True,
            read=read,
            trade="trade" in permissions,
            withdrawal=withdrawal,
            ip_allowlisted=bool(str(row.get("ip", "")).strip()),
            environment=environment,
            account_status="AVAILABLE",
            message=(
                "Withdrawal permission detected; keep this connection disabled"
                if withdrawal else "OKX connection test succeeded" if read else "OKX read permission is missing"
            ),
        )


class ValrConnectionTester:
    provider = "VALR"

    def __init__(
        self,
        *,
        base_url: str = "https://api.valr.com",
        transport: PrivateTransport = _transport,
        clock_ms: Callable[[], int] | None = None,
        timeout: float = 15,
    ) -> None:
        self.base_url = _safe_base_url(base_url)
        self.transport = transport
        self.clock_ms = clock_ms or (lambda: round(time.time() * 1_000))
        self.timeout = min(max(float(timeout), 1), 20)

    def test(self, credentials: Mapping[str, str]) -> ConnectionPermissionReport:
        path = "/v1/account/api-keys/current"
        timestamp = str(self.clock_ms())
        signature = hmac.new(
            credentials["apiSecret"].encode(),
            f"{timestamp}GET{path}".encode(),
            hashlib.sha512,
        ).hexdigest()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-VALR-API-KEY": credentials["apiKey"],
            "X-VALR-SIGNATURE": signature,
            "X-VALR-TIMESTAMP": timestamp,
        }
        payload = self.transport(Request(f"{self.base_url}{path}", headers=headers), self.timeout)
        if not isinstance(payload, dict):
            raise ConnectionTestError("VALR authentication failed")
        raw_permissions = payload.get("permissions", [])
        if isinstance(raw_permissions, str):
            raw_permissions = raw_permissions.split(",")
        if not isinstance(raw_permissions, list):
            raise ConnectionTestError("VALR permission information was unavailable")
        permissions = {str(item).strip().upper() for item in raw_permissions if str(item).strip()}
        withdrawal = any("WITHDRAW" in item for item in permissions)
        read = any(item in {"VIEW", "READ", "VIEW_ONLY"} for item in permissions)
        ip_value = payload.get("ipWhitelist", payload.get("allowedIps"))
        return ConnectionPermissionReport(
            authenticated=True,
            read=read,
            trade=any("TRADE" in item for item in permissions),
            withdrawal=withdrawal,
            ip_allowlisted=_allowlist_state(ip_value),
            environment="LIVE",
            account_status="AVAILABLE",
            message=(
                "Withdrawal permission detected; keep this connection disabled"
                if withdrawal else "VALR connection test succeeded" if read else "VALR view permission is missing"
            ),
        )


def connection_testers(environ: Mapping[str, str] | None = None) -> dict[str, ConnectionTester]:
    values = os.environ if environ is None else environ
    return {
        "OKX": OkxConnectionTester(
            live_base_url=values.get("OKX_PRIVATE_API_BASE_URL", "https://www.okx.com")
        ),
        "VALR": ValrConnectionTester(
            base_url=values.get("VALR_PRIVATE_API_BASE_URL", "https://api.valr.com")
        ),
    }


def _allowlist_state(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, list):
        return bool(value)
    return bool(str(value).strip())


def _safe_base_url(value: str) -> str:
    parsed = urlparse(value)
    secure = parsed.scheme == "https" and bool(parsed.hostname)
    loopback = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if not secure and not loopback:
        raise ValueError("Private exchange API URL must use HTTPS or loopback")
    return value.rstrip("/")
