from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping


ProviderName = Literal["OKX", "VALR"]
MarketName = Literal["CRYPTO", "METAL"]
InstrumentType = Literal["SPOT", "PERPETUAL"]

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "6h": 21_600,
    "1d": 86_400,
}


def utc_datetime(value: datetime | str | int | float) -> datetime:
    if isinstance(value, datetime):
        resolved = value
    elif isinstance(value, (int, float)):
        resolved = datetime.fromtimestamp(float(value), tz=timezone.utc)
    else:
        resolved = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)


def iso_utc(value: datetime | str | int | float) -> str:
    return utc_datetime(value).isoformat().replace("+00:00", "Z")


def decimal_text(value: Any, default: str = "0") -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default
    if not number.is_finite():
        return default
    return format(number.normalize(), "f")


def finite_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def normalize_provider(value: str) -> ProviderName:
    provider = value.strip().upper()
    if provider not in {"OKX", "VALR"}:
        raise ValueError("Provider must be OKX or VALR")
    return provider  # type: ignore[return-value]


def normalize_provider_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not symbol or len(symbol) > 80:
        raise ValueError("Instrument symbol is required")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if any(character not in allowed for character in symbol):
        raise ValueError("Instrument symbol contains unsupported characters")
    return symbol


def classify_market(base_currency: str) -> MarketName:
    return "METAL" if base_currency.strip().upper() in {"XAU", "XAG"} else "CRYPTO"


def instrument_identifier(provider: str, provider_symbol: str) -> str:
    identity = f"{normalize_provider(provider)}|{normalize_provider_symbol(provider_symbol)}"
    return "INS-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20].upper()


def display_symbol(provider_symbol: str, instrument_type: InstrumentType) -> str:
    normalized = normalize_provider_symbol(provider_symbol)
    if normalized.endswith("-SWAP"):
        normalized = normalized.removesuffix("-SWAP") + "PERP"
    value = normalized.replace("-", "")
    return value if instrument_type == "SPOT" or value.endswith("PERP") else value + "PERP"


@dataclass(frozen=True)
class MarketInstrument:
    instrument_id: str
    provider: ProviderName
    provider_symbol: str
    display_symbol: str
    market: MarketName
    instrument_type: InstrumentType
    base_currency: str
    quote_currency: str
    tick_size: str
    quantity_step: str
    minimum_quantity: str
    minimum_notional: str
    contract_multiplier: str
    active: bool = True
    backtest_enabled: bool = True
    signals_enabled: bool = True

    def public(self) -> dict[str, Any]:
        return {
            "instrumentId": self.instrument_id,
            "provider": self.provider,
            "providerSymbol": self.provider_symbol,
            "displaySymbol": self.display_symbol,
            "market": self.market,
            "instrumentType": self.instrument_type,
            "baseCurrency": self.base_currency,
            "quoteCurrency": self.quote_currency,
            "tickSize": self.tick_size,
            "quantityStep": self.quantity_step,
            "minimumQuantity": self.minimum_quantity,
            "minimumNotional": self.minimum_notional,
            "contractMultiplier": self.contract_multiplier,
            "active": self.active,
            "backtestEnabled": self.backtest_enabled,
            "signalsEnabled": self.signals_enabled,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> MarketInstrument:
        return cls(
            instrument_id=str(row["instrument_id"]),
            provider=normalize_provider(str(row["provider"])),
            provider_symbol=normalize_provider_symbol(str(row["provider_symbol"])),
            display_symbol=str(row["display_symbol"]),
            market=str(row["market"]),  # type: ignore[arg-type]
            instrument_type=str(row["instrument_type"]),  # type: ignore[arg-type]
            base_currency=str(row["base_currency"]),
            quote_currency=str(row["quote_currency"]),
            tick_size=str(row["tick_size"]),
            quantity_step=str(row["quantity_step"]),
            minimum_quantity=str(row["minimum_quantity"]),
            minimum_notional=str(row["minimum_notional"]),
            contract_multiplier=str(row["contract_multiplier"]),
            active=bool(row["active"]),
            backtest_enabled=bool(row["backtest_enabled"]),
            signals_enabled=bool(row["signals_enabled"]),
        )


@dataclass(frozen=True)
class MarketCandle:
    provider: ProviderName
    provider_symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    base_volume: float
    quote_volume: float | None
    complete: bool

    def __post_init__(self) -> None:
        if self.timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported candle timeframe")
        if self.close_time <= self.open_time:
            raise ValueError("Candle close time must be after open time")
        values = (self.open, self.high, self.low, self.close, self.base_volume)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("Candle values must be finite")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Candle OHLC values are inconsistent")
        if self.low > self.high or self.base_volume < 0:
            raise ValueError("Candle range or volume is invalid")

    @classmethod
    def build(
        cls,
        *,
        provider: str,
        provider_symbol: str,
        timeframe: str,
        open_time: datetime | str | int | float,
        open: Any,
        high: Any,
        low: Any,
        close: Any,
        base_volume: Any,
        quote_volume: Any = None,
        complete: bool = True,
    ) -> MarketCandle:
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported candle timeframe")
        opened = utc_datetime(open_time)
        closed = opened + timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
        numbers = [finite_float(value) for value in (open, high, low, close, base_volume)]
        if any(value is None for value in numbers):
            raise ValueError("Candle contains a non-numeric OHLCV value")
        return cls(
            provider=normalize_provider(provider),
            provider_symbol=normalize_provider_symbol(provider_symbol),
            timeframe=timeframe,
            open_time=opened,
            close_time=closed,
            open=float(numbers[0]),
            high=float(numbers[1]),
            low=float(numbers[2]),
            close=float(numbers[3]),
            base_volume=float(numbers[4]),
            quote_volume=finite_float(quote_volume),
            complete=bool(complete),
        )

    def public(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "providerSymbol": self.provider_symbol,
            "timeframe": self.timeframe,
            "openTime": iso_utc(self.open_time),
            "closeTime": iso_utc(self.close_time),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "baseVolume": self.base_volume,
            "quoteVolume": self.quote_volume,
            "complete": self.complete,
        }
