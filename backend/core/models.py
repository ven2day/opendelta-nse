"""Shared data contracts for strategies, engines, and persistence.

Everything a strategy consumes or produces is defined here so that the backtest
replayer, the live signal engine, and the paper broker all speak one language
regardless of market.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping

import pandas as pd

Market = Literal["NSE", "CRYPTO"]
Decision = Literal["BUY", "SELL", "NONE"]

MARKETS: tuple[Market, ...] = ("NSE", "CRYPTO")
MARKET_TIMEZONES: Mapping[Market, str] = {"NSE": "Asia/Kolkata", "CRYPTO": "UTC"}

CANDLE_COLUMNS: tuple[str, ...] = ("Open", "High", "Low", "Close", "Volume")
COMPLETE_COLUMN = "Complete"


def market_timezone(market: str) -> str:
    key = market.strip().upper()
    if key not in MARKET_TIMEZONES:
        raise ValueError(f"Unsupported market {market!r}; expected one of {', '.join(MARKETS)}")
    return MARKET_TIMEZONES[key]  # type: ignore[index]


def normalize_candles(candles: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """Return a clean OHLCV frame indexed by tz-aware timestamps in ``timezone``.

    Rows flagged incomplete via an optional ``Complete`` column are dropped: a
    strategy must only ever see completed candles. Non-numeric or missing values
    are removed and the result is sorted chronologically.
    """
    missing = [name for name in CANDLE_COLUMNS if name not in candles]
    if missing:
        raise ValueError("Candles are missing: " + ", ".join(missing))
    source = candles
    if COMPLETE_COLUMN in source:
        source = source[source[COMPLETE_COLUMN].astype(bool)]
    data = source[list(CANDLE_COLUMNS)].copy()
    data.index = pd.DatetimeIndex(data.index)
    data.index = data.index.tz_localize(timezone) if data.index.tz is None else data.index.tz_convert(timezone)
    return data.apply(pd.to_numeric, errors="coerce").dropna().sort_index()


@dataclass(frozen=True)
class MarketContext:
    """What the caller knows about the market at evaluation time.

    Strategies must not fetch anything themselves; whatever context they need
    is handed to them here by the engine that owns the data.
    """

    market: Market
    symbol: str
    timeframe: str
    timezone: str
    session_open: bool | None = None
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        if self.market not in MARKETS:
            raise ValueError(f"Unsupported market {self.market!r}")
        if not self.symbol or not self.symbol.strip():
            raise ValueError("MarketContext requires a symbol")
        if not self.timeframe:
            raise ValueError("MarketContext requires a timeframe")


@dataclass(frozen=True)
class SignalDecision:
    """The single output contract of every strategy evaluation.

    ``configuration_snapshot`` is the fully-resolved, validated configuration
    the decision was made with; engines persist it verbatim so results remain
    reproducible after a strategy's defaults change.
    """

    decision: Decision
    strategy_id: str
    strategy_version: str
    market: Market
    symbol: str
    timeframe: str
    candle_timestamp: datetime
    signal_price: float | None
    target_price: float | None
    stop_price: float | None = None
    reasons: tuple[str, ...] = ()
    indicators: Mapping[str, Any] = field(default_factory=dict)
    configuration_snapshot: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.decision not in ("BUY", "SELL", "NONE"):
            raise ValueError(f"Unsupported decision {self.decision!r}")
        if self.decision != "NONE" and self.signal_price is None:
            raise ValueError("An actionable decision must carry a signal price")

    @property
    def actionable(self) -> bool:
        return self.decision != "NONE"

    def public(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "strategyId": self.strategy_id,
            "strategyVersion": self.strategy_version,
            "market": self.market,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candleTimestamp": self.candle_timestamp.isoformat(),
            "signalPrice": self.signal_price,
            "targetPrice": self.target_price,
            "stopPrice": self.stop_price,
            "reasons": list(self.reasons),
            "indicators": dict(self.indicators),
            "configurationSnapshot": dict(self.configuration_snapshot),
        }
