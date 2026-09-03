"""The screener: one symbol at a time, bounded memory, every outcome recorded with a reason."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from backend.core import indicators
from backend.core.models import normalize_candles
from backend.markets.base import CandleBatch, CandleSource, MarketSpec
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
    def __init__(self, *, market: MarketSpec, source: CandleSource, timeframe: str = "5m", batch_size: int = 50, clock: Callable[[], datetime] | None = None) -> None:
        if not 1 <= batch_size <= 250:
            raise ValueError("Screener batch_size must be between 1 and 250")
        self.market = market
        self.source = source
        self.timeframe = timeframe
        self.batch_size = batch_size
        self.clock = clock or (lambda: datetime.now(tz=_zone(market.timezone)))

    def run(self, run_id: str, symbols: Sequence[str], filters: ScreenerFilters, *, progress: Callable[[int, int], None] | None = None, cancel_event: threading.Event | None = None) -> ScreenerOutcome:
        filters.validate()
        now = self.clock()
        start = now - timedelta(days=filters.lookback_days * (2 if self.market.market == "NSE" else 1) + 1)
        outcome = ScreenerOutcome(run_id=run_id, market=self.market.market, filters=filters)
        unique = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        started = time.perf_counter()
        batch_reader = getattr(self.source, "candles_many", None)
        if not callable(batch_reader):
            for index, symbol in enumerate(unique):
                if cancel_event is not None and cancel_event.is_set():
                    break
                self._evaluate_symbol(outcome, symbol, self._read_one(symbol, start, now))
                if progress is not None:
                    progress(index + 1, len(unique))
        else:
            completed = 0
            for offset in range(0, len(unique), self.batch_size):
                if cancel_event is not None and cancel_event.is_set():
                    break
                symbols_batch = unique[offset : offset + self.batch_size]
                batch_started = time.perf_counter()
                try:
                    batch: CandleBatch = batch_reader(symbols_batch, self.timeframe, start, now, warmup_bars=0)
                except Exception as error:  # isolate an unexpected batch failure to its symbols
                    batch = CandleBatch(frames={}, errors={symbol: error for symbol in symbols_batch})
                for symbol in symbols_batch:
                    if cancel_event is not None and cancel_event.is_set():
                        break
                    self._evaluate_symbol(outcome, symbol, batch.errors.get(symbol) or batch.frames.get(symbol, pd.DataFrame()))
                    completed += 1
                    if progress is not None:
                        progress(completed, len(unique))
                logger.info("Screener %s read/scored batch %s-%s of %s in %.3fs", run_id, offset + 1, min(offset + len(symbols_batch), len(unique)), len(unique), time.perf_counter() - batch_started)
                del batch
        ranked = {row["symbol"]: row for row in rank_symbols(outcome.rows, filters.rank_by, filters.maximum_symbols)}
        outcome.rows = [ranked.get(row["symbol"], row) for row in outcome.rows]
        logger.info("Screener %s completed %s symbols in %.3fs (%s failed)", run_id, len(outcome.rows), time.perf_counter() - started, len(outcome.failed))
        return outcome

    def _read_one(self, symbol: str, start: datetime, end: datetime) -> pd.DataFrame | Exception:
        try:
            return self.source.candles(symbol, self.timeframe, start, end, warmup_bars=0)
        except Exception as error:  # a bad symbol is recorded, not fatal to the run
            return error

    def _evaluate_symbol(self, outcome: ScreenerOutcome, symbol: str, candles_or_error: pd.DataFrame | Exception) -> None:
        if isinstance(candles_or_error, Exception):
            message = str(candles_or_error)[:240]
            outcome.failed.append({"symbol": symbol, "message": message})
            outcome.rows.append({"symbol": symbol, "passed": False, "rank": None, "score": None, "rejection_reason": "CANDLE_DATA_UNAVAILABLE", "metrics": {"error": message}})
            return
        try:
            metrics = symbol_metrics(candles_or_error, timezone=self.market.timezone, timeframe=self.timeframe, market=self.market.market)
            reason = outcome.filters.evaluate(metrics)
            outcome.rows.append({"symbol": symbol, "passed": reason is None, "rank": None, "score": None, "rejection_reason": reason, "metrics": metrics})
        except Exception as error:  # metric failure remains isolated to the symbol
            message = str(error)[:240]
            outcome.failed.append({"symbol": symbol, "message": message})
            outcome.rows.append({"symbol": symbol, "passed": False, "rank": None, "score": None, "rejection_reason": "CANDLE_DATA_UNAVAILABLE", "metrics": {"error": message}})

        del candles_or_error


def apply_manual_selection(symbols: Sequence[str], *, includes: Sequence[str], excludes: Sequence[str]) -> list[str]:
    excluded = {item.strip().upper() for item in excludes if item.strip()}
    ordered = [*symbols, *(item.strip().upper() for item in includes if item.strip())]
    return [symbol for symbol in dict.fromkeys(ordered) if symbol not in excluded]


def _zone(timezone: str):
    from zoneinfo import ZoneInfo

    return ZoneInfo(timezone)
