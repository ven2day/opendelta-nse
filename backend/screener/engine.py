"""The screener: one symbol at a time, bounded memory, every outcome recorded with a reason."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from backend.core import indicators
from backend.core.models import normalize_candles
from backend.markets.base import CandleSource, MarketSpec
from backend.screener.filters import ScreenerFilters
from backend.screener.ranking import rank_symbols

logger = logging.getLogger("opendelta.screener")

EXPECTED_BARS_PER_SESSION = {"NSE": {"5m": 75, "15m": 25, "1h": 7}, "CRYPTO": {"5m": 288, "15m": 96, "1h": 24}}


@dataclass
class ScreenerOutcome:
    run_id: str
    market: str
    filters: ScreenerFilters
    rows: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    @property
    def passed(self) -> list[dict[str, Any]]:
        return [row for row in self.rows if row["passed"]]

    @property
    def rejected(self) -> list[dict[str, Any]]:
        return [row for row in self.rows if not row["passed"]]

    def passing_symbols(self) -> list[str]:
        return [row["symbol"] for row in sorted(self.passed, key=lambda row: row["rank"] or 0)]


def symbol_metrics(candles: pd.DataFrame, *, timezone: str, timeframe: str, market: str) -> dict[str, Any]:
    data = normalize_candles(candles, timezone)
    if data.empty:
        return {"bars": 0, "sessions": 0, "candleCoverage": 0.0, "lastPrice": None, "averageTradedValue": 0.0, "averageVolume": 0.0, "volatilityPct": None}
    sessions = pd.Series(data.index.date, index=data.index)
    session_count = int(sessions.nunique())
    expected = EXPECTED_BARS_PER_SESSION.get(market, {}).get(timeframe)
    coverage = min(1.0, len(data) / (expected * session_count)) if expected and session_count else 1.0
    traded_value = (data["Close"] * data["Volume"]).groupby(sessions).sum()
    atr = indicators.atr(data, 14, seeded=False)
    last_close = float(data["Close"].iloc[-1])
    volatility = float(atr.iloc[-1] / last_close * 100) if len(atr) and np.isfinite(atr.iloc[-1]) and last_close > 0 else None
    return {
        "bars": int(len(data)),
        "sessions": session_count,
        "candleCoverage": round(float(coverage), 4),
        "lastPrice": round(last_close, 4),
        "lastCandle": data.index[-1].isoformat(),
        "averageTradedValue": round(float(traded_value.mean()), 2),
        "averageVolume": round(float(data["Volume"].mean()), 2),
        "volatilityPct": round(volatility, 4) if volatility is not None else None,
    }


class ScreenerEngine:
    def __init__(self, *, market: MarketSpec, source: CandleSource, timeframe: str = "5m", clock: Callable[[], datetime] | None = None) -> None:
        self.market = market
        self.source = source
        self.timeframe = timeframe
        self.clock = clock or (lambda: datetime.now(tz=_zone(market.timezone)))

    def run(self, run_id: str, symbols: Sequence[str], filters: ScreenerFilters, *, progress: Callable[[int, int], None] | None = None, cancel_event: threading.Event | None = None) -> ScreenerOutcome:
        filters.validate()
        now = self.clock()
        start = now - timedelta(days=filters.lookback_days * (2 if self.market.market == "NSE" else 1) + 1)
        outcome = ScreenerOutcome(run_id=run_id, market=self.market.market, filters=filters)
        unique = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        for index, symbol in enumerate(unique):
            if cancel_event is not None and cancel_event.is_set():
                break
            try:
                candles = self.source.candles(symbol, self.timeframe, start, now, warmup_bars=0)
                metrics = symbol_metrics(candles, timezone=self.market.timezone, timeframe=self.timeframe, market=self.market.market)
                del candles
                reason = filters.evaluate(metrics)
                outcome.rows.append({"symbol": symbol, "passed": reason is None, "rank": None, "score": None, "rejection_reason": reason, "metrics": metrics})
            except Exception as error:  # noqa: BLE001 - a bad symbol is a recorded rejection, not a failed run
                message = str(error)[:240]
                outcome.failed.append({"symbol": symbol, "message": message})
                outcome.rows.append({"symbol": symbol, "passed": False, "rank": None, "score": None, "rejection_reason": "CANDLE_DATA_UNAVAILABLE", "metrics": {"error": message}})
            if progress is not None:
                progress(index + 1, len(unique))
        ranked = {row["symbol"]: row for row in rank_symbols(outcome.rows, filters.rank_by, filters.maximum_symbols)}
        outcome.rows = [ranked.get(row["symbol"], row) for row in outcome.rows]
        return outcome


def apply_manual_selection(symbols: Sequence[str], *, includes: Sequence[str], excludes: Sequence[str]) -> list[str]:
    excluded = {item.strip().upper() for item in excludes if item.strip()}
    ordered = [*symbols, *(item.strip().upper() for item in includes if item.strip())]
    return [symbol for symbol in dict.fromkeys(ordered) if symbol not in excluded]


def _zone(timezone: str):
    from zoneinfo import ZoneInfo

    return ZoneInfo(timezone)
