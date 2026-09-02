"""Turns provider rows into completed candles and keeps a bounded per-symbol history.

Two independent completeness checks protect the strategy from a still-forming
candle: a provider ``Complete`` flag when present, and the clock — a candle
whose close time is in the future is never complete no matter what the
provider says.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

from backend.core.models import CANDLE_COLUMNS, COMPLETE_COLUMN


class CandleHistory:
    """Bounded, deduplicated, chronologically ordered completed candles per symbol."""

    def __init__(self, maximum_bars: int) -> None:
        if maximum_bars < 1:
            raise ValueError("maximum_bars must be positive")
        self.maximum_bars = maximum_bars
        self._frames: dict[str, pd.DataFrame] = {}
        self._lock = threading.RLock()

    def symbols(self) -> list[str]:
        with self._lock:
            return sorted(self._frames)

    def get(self, symbol: str) -> pd.DataFrame:
        with self._lock:
            frame = self._frames.get(symbol)
            return frame.copy() if frame is not None else pd.DataFrame(columns=list(CANDLE_COLUMNS))

    def latest_timestamp(self, symbol: str) -> pd.Timestamp | None:
        with self._lock:
            frame = self._frames.get(symbol)
            return frame.index[-1] if frame is not None and len(frame) else None

    def latest_overall(self) -> pd.Timestamp | None:
        with self._lock:
            stamps = [frame.index[-1] for frame in self._frames.values() if len(frame)]
            return max(stamps) if stamps else None

    def seed(self, symbol: str, frame: pd.DataFrame) -> None:
        with self._lock:
            self._frames[symbol] = self._normalised(frame).tail(self.maximum_bars)

    def append(self, symbol: str, frame: pd.DataFrame) -> list[pd.Timestamp]:
        """Add rows newer than the latest known candle; returns the timestamps that were new."""
        incoming = self._normalised(frame)
        with self._lock:
            existing = self._frames.get(symbol)
            latest = existing.index[-1] if existing is not None and len(existing) else None
            fresh = incoming[incoming.index > latest] if latest is not None else incoming
            if fresh.empty:
                return []
            combined = pd.concat([existing, fresh]) if existing is not None else fresh
            combined = combined[~combined.index.duplicated(keep="last")].sort_index().tail(self.maximum_bars)
            self._frames[symbol] = combined
            return list(fresh.index)

    @staticmethod
    def _normalised(frame: pd.DataFrame) -> pd.DataFrame:
        """Completed rows only: a provider ``Complete`` flag of False is honoured here too."""
        if frame.empty:
            return pd.DataFrame(columns=list(CANDLE_COLUMNS))
        source = frame[frame[COMPLETE_COLUMN].astype(bool)] if COMPLETE_COLUMN in frame else frame
        data = source[list(CANDLE_COLUMNS)].apply(pd.to_numeric, errors="coerce").dropna()
        data.index = pd.DatetimeIndex(data.index)
        return data[~data.index.duplicated(keep="last")].sort_index()


class CandleProcessor:
    def __init__(self, *, bar_minutes: int, timezone: str, clock: Callable[[], datetime]) -> None:
        self.bar = timedelta(minutes=bar_minutes)
        self.timezone = timezone
        self.clock = clock

    def completed(self, frame: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
        """Only rows that are complete by both the provider flag and the clock."""
        if frame.empty:
            return pd.DataFrame(columns=list(CANDLE_COLUMNS))
        moment = pd.Timestamp(now or self.clock())
        moment = moment.tz_localize(self.timezone) if moment.tzinfo is None else moment.tz_convert(self.timezone)
        data = frame.copy()
        data.index = pd.DatetimeIndex(data.index)
        data.index = data.index.tz_localize(self.timezone) if data.index.tz is None else data.index.tz_convert(self.timezone)
        closed_by_clock = (data.index + self.bar) <= moment
        if COMPLETE_COLUMN in data:
            closed_by_clock &= data[COMPLETE_COLUMN].astype(bool).to_numpy()
        return data.loc[closed_by_clock, list(CANDLE_COLUMNS)]
