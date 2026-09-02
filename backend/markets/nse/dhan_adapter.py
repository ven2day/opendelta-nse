"""Adapts the existing Dhan-backed HistoricalDataStore to the CandleSource contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

import pandas as pd


class _HistoricalStore(Protocol):
    def candles(self, symbol: str, timeframe: str, duration_years: int, analysis_start: datetime, now: datetime, *, warmup_bars: int = 0, **kwargs: Any) -> pd.DataFrame: ...


class DhanCandleSource:
    def __init__(self, store: _HistoricalStore) -> None:
        self.store = store

    def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime, *, warmup_bars: int) -> pd.DataFrame:
        duration_years = 1 if (end - start).days <= 366 else 3
        frame = self.store.candles(symbol, timeframe, duration_years, start, end, warmup_bars=warmup_bars)
        return frame[frame.index <= end] if len(frame) else frame
