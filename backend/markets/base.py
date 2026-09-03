"""Market-agnostic contracts every engine uses to talk to a market."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Callable, Protocol, Sequence

import pandas as pd

from backend.core.models import Market, market_timezone


@dataclass(frozen=True)
class Fill:
    """A simulated execution: the price actually paid/received and the costs."""

    price: float
    fees: float
    slippage: float

    @property
    def total_cost(self) -> float:
        return self.fees + self.slippage


class FeeModel(Protocol):
    def buy(self, price: float, quantity: float) -> Fill: ...

    def sell(self, price: float, quantity: float) -> Fill: ...

    def public(self) -> dict[str, float]: ...


class CandleSource(Protocol):
    """Completed candles for one symbol; engines process one symbol at a time."""

    def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime, *, warmup_bars: int) -> pd.DataFrame: ...


@dataclass
class CandleBatch:
    """Per-symbol batch result; one bad instrument never poisons its neighbours."""

    frames: dict[str, pd.DataFrame]
    errors: dict[str, Exception]


class BatchCandleSource(CandleSource, Protocol):
    """Optional fast path used by universe-sized readers such as TimescaleDB."""

    def candles_many(
        self,
        symbols: Sequence[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        warmup_bars: int,
    ) -> CandleBatch: ...


@dataclass(frozen=True)
class MarketSpec:
    market: Market
    timezone: str
    currency: str
    fees: FeeModel
    session_is_open: Callable[[datetime], bool]
    bar_minutes: dict[str, int]
    daily_session_close: time | None = None

    def minutes(self, timeframe: str) -> int:
        try:
            return self.bar_minutes[timeframe]
        except KeyError as error:
            raise ValueError(f"{self.market} does not support the {timeframe} timeframe") from error


def market_spec(market: str) -> MarketSpec:
    from backend.markets.crypto.fees import CryptoFeeModel
    from backend.markets.crypto.session import crypto_session_is_open
    from backend.markets.nse.fees import NseFeeModel
    from backend.markets.nse.session import MARKET_CLOSE, nse_session_is_open

    key = market.strip().upper()
    timezone = market_timezone(key)
    if key == "NSE":
        return MarketSpec(
            "NSE",
            timezone,
            "INR",
            NseFeeModel(),
            nse_session_is_open,
            {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 375},
            daily_session_close=MARKET_CLOSE,
        )
    return MarketSpec("CRYPTO", timezone, "USDT", CryptoFeeModel(), crypto_session_is_open, {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440})
