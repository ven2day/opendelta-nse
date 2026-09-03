from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from backend.markets.common import (
    TIMEFRAME_SECONDS,
    MarketCandle,
    MarketInstrument,
    classify_market,
    decimal_text,
    display_symbol,
    instrument_identifier,
    normalize_provider,
    normalize_provider_symbol,
    utc_datetime,
)


class MarketProviderError(RuntimeError):
    """Raised when a public market-data provider is unavailable or invalid."""


class PublicMarketProvider(Protocol):
    name: str

    def instruments(self) -> list[MarketInstrument]: ...

    def candles(
        self,
        instrument: MarketInstrument,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketCandle]: ...


JsonTransport = Callable[[str], Any]


class PublicJsonClient:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep

    def get(self, url: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "User-Agent": "OpenDelta-market-research/1.0",
                    },
                )
                with urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = error
                if attempt < self.retries:
                    self.sleep(0.4 * (2**attempt))
        raise MarketProviderError("Public market-data request failed") from last_error


def _query_url(base_url: str, path: str, parameters: Mapping[str, Any]) -> str:
    values = {key: str(value) for key, value in parameters.items() if value is not None}
    return f"{base_url.rstrip('/')}{path}?{urlencode(values)}"


def _unique_candles(candles: Iterable[MarketCandle]) -> list[MarketCandle]:
    unique = {(item.provider_symbol, item.timeframe, item.open_time): item for item in candles}
    return sorted(unique.values(), key=lambda item: item.open_time)


class OkxPublicProvider:
    name = "OKX"
    _BAR = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1H",
        "6h": "6Hutc",
        "1d": "1Dutc",
    }

    def __init__(
        self,
        *,
        base_url: str = "https://www.okx.com",
        transport: JsonTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = transport or PublicJsonClient().get

    def _request(self, path: str, parameters: Mapping[str, Any]) -> list[Any]:
        payload = self.transport(_query_url(self.base_url, path, parameters))
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise MarketProviderError("OKX returned an invalid public-market response")
        data = payload.get("data")
        if not isinstance(data, list):
            raise MarketProviderError("OKX public-market response has no data array")
        return data

    @staticmethod
    def _instrument(row: Mapping[str, Any]) -> MarketInstrument:
        provider_symbol = normalize_provider_symbol(str(row.get("instId", "")))
        raw_type = str(row.get("instType", "")).upper()
        instrument_type = "SPOT" if raw_type == "SPOT" else "PERPETUAL"
        base = str(row.get("baseCcy") or row.get("ctValCcy") or provider_symbol.split("-")[0]).upper()
        quote_currency = str(row.get("quoteCcy") or row.get("settleCcy") or "").upper()
        return MarketInstrument(
            instrument_id=instrument_identifier("OKX", provider_symbol),
            provider="OKX",
            provider_symbol=provider_symbol,
            display_symbol=display_symbol(provider_symbol, instrument_type),
            market=classify_market(base),
            instrument_type=instrument_type,
            base_currency=base,
            quote_currency=quote_currency,
            tick_size=decimal_text(row.get("tickSz")),
            quantity_step=decimal_text(row.get("lotSz")),
            minimum_quantity=decimal_text(row.get("minSz")),
            minimum_notional="0",
            contract_multiplier=decimal_text(row.get("ctVal"), "1") or "1",
            active=str(row.get("state", "live")).lower() == "live",
        )

    def instruments(self) -> list[MarketInstrument]:
        rows: list[Any] = []
        for instrument_type in ("SPOT", "SWAP"):
            rows.extend(self._request("/api/v5/public/instruments", {"instType": instrument_type}))
        instruments = [self._instrument(row) for row in rows if isinstance(row, Mapping)]
        return sorted((item for item in instruments if item.active), key=lambda item: item.provider_symbol)

    def candles(
        self,
        instrument: MarketInstrument,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketCandle]:
        if timeframe not in self._BAR:
            raise ValueError("OKX does not support the requested timeframe")
        start_utc, end_utc = utc_datetime(start), utc_datetime(end)
        if start_utc >= end_utc:
            raise ValueError("Candle start must be earlier than end")
        cursor = int(end_utc.timestamp() * 1_000) + 1
        earliest = cursor
        result: list[MarketCandle] = []
        while earliest > int(start_utc.timestamp() * 1_000):
            rows = self._request(
                "/api/v5/market/history-candles",
                {
                    "instId": instrument.provider_symbol,
                    "bar": self._BAR[timeframe],
                    "after": cursor,
                    "limit": 300,
                },
            )
            if not rows:
                break
            page_times: list[int] = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 6:
                    continue
                timestamp_ms = int(row[0])
                page_times.append(timestamp_ms)
                opened = datetime.fromtimestamp(timestamp_ms / 1_000, tz=timezone.utc)
                if opened < start_utc or opened >= end_utc:
                    continue
                confirm = str(row[8]) == "1" if len(row) > 8 else True
                result.append(
                    MarketCandle.build(
                        provider="OKX",
                        provider_symbol=instrument.provider_symbol,
                        timeframe=timeframe,
                        open_time=opened,
                        open=row[1],
                        high=row[2],
                        low=row[3],
                        close=row[4],
                        base_volume=row[5],
                        quote_volume=row[7] if len(row) > 7 else None,
                        complete=confirm,
                    )
                )
            if not page_times:
                break
            next_cursor = min(page_times)
            if next_cursor >= earliest:
                break
            earliest = next_cursor
            cursor = next_cursor
            if len(rows) < 300:
                break
        return [item for item in _unique_candles(result) if item.complete]


class ValrPublicProvider:
    name = "VALR"
    _PERIOD = {key: value for key, value in TIMEFRAME_SECONDS.items()}

    def __init__(
        self,
        *,
        base_url: str = "https://api.valr.com",
        transport: JsonTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = transport or PublicJsonClient().get

    def _request(self, path: str, parameters: Mapping[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        if parameters:
            url = _query_url(self.base_url, path, parameters)
        payload = self.transport(url)
        if isinstance(payload, dict) and "result" in payload:
            payload = payload["result"]
        return payload

    @staticmethod
    def _instrument(row: Mapping[str, Any]) -> MarketInstrument:
        provider_symbol = normalize_provider_symbol(str(row.get("symbol", "")))
        instrument_type = "PERPETUAL" if str(row.get("currencyPairType", "SPOT")).upper() == "FUTURE" else "SPOT"
        base = str(row.get("baseCurrency", "")).upper()
        try:
            decimal_places = max(0, min(24, int(row.get("baseDecimalPlaces", 0))))
        except (TypeError, ValueError):
            decimal_places = 0
        return MarketInstrument(
            instrument_id=instrument_identifier("VALR", provider_symbol),
            provider="VALR",
            provider_symbol=provider_symbol,
            display_symbol=display_symbol(provider_symbol, instrument_type),
            market=classify_market(base),
            instrument_type=instrument_type,
            base_currency=base,
            quote_currency=str(row.get("quoteCurrency", "")).upper(),
            tick_size=decimal_text(row.get("tickSize")),
            quantity_step=decimal_text(Decimal("1").scaleb(-decimal_places)),
            minimum_quantity=decimal_text(row.get("minBaseAmount")),
            minimum_notional=decimal_text(row.get("minQuoteAmount")),
            contract_multiplier="1",
            active=bool(row.get("active", False)),
        )

    def instruments(self) -> list[MarketInstrument]:
        rows = self._request("/v1/public/pairs")
        if not isinstance(rows, list):
            raise MarketProviderError("VALR returned an invalid currency-pairs response")
        instruments = [self._instrument(row) for row in rows if isinstance(row, Mapping)]
        return sorted((item for item in instruments if item.active), key=lambda item: item.provider_symbol)

    def candles(
        self,
        instrument: MarketInstrument,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketCandle]:
        if timeframe not in self._PERIOD:
            raise ValueError("VALR does not support the requested timeframe")
        start_utc, end_utc = utc_datetime(start), utc_datetime(end)
        if start_utc >= end_utc:
            raise ValueError("Candle start must be earlier than end")
        period = self._PERIOD[timeframe]
        result: list[MarketCandle] = []
        cursor = start_utc
        now = datetime.now(timezone.utc)
        while cursor < end_utc:
            chunk_end = min(end_utc, cursor + timedelta(seconds=period * 100))
            rows = self._request(
                f"/v1/public/{quote(instrument.provider_symbol, safe='')}/buckets",
                {
                    "periodSeconds": period,
                    "startTime": int(cursor.timestamp()),
                    "endTime": int(chunk_end.timestamp()),
                    "skip": 0,
                    "limit": 100,
                    "includeEmpty": "false",
                },
            )
            if not isinstance(rows, list):
                raise MarketProviderError("VALR returned an invalid price-buckets response")
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                opened = utc_datetime(str(row.get("startTime", "")))
                if opened < start_utc or opened >= end_utc:
                    continue
                result.append(
                    MarketCandle.build(
                        provider="VALR",
                        provider_symbol=instrument.provider_symbol,
                        timeframe=timeframe,
                        open_time=opened,
                        open=row.get("open"),
                        high=row.get("high"),
                        low=row.get("low"),
                        close=row.get("close"),
                        base_volume=row.get("volume"),
                        quote_volume=row.get("quoteVolume"),
                        complete=opened + timedelta(seconds=period) <= now,
                    )
                )
            cursor = chunk_end
        return [item for item in _unique_candles(result) if item.complete]


class ProviderFactory:
    def __init__(self, providers: Mapping[str, PublicMarketProvider] | None = None) -> None:
        configured = providers or {
            "OKX": OkxPublicProvider(),
            "VALR": ValrPublicProvider(),
        }
        self._providers = {normalize_provider(name): provider for name, provider in configured.items()}

    def get(self, name: str) -> PublicMarketProvider:
        provider = self._providers.get(normalize_provider(name))
        if provider is None:
            raise ValueError("Market-data provider is not configured")
        return provider

    def names(self) -> list[str]:
        return sorted(self._providers)
