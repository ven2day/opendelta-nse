"""CandleSource implementation backed by canonical TimescaleDB candles."""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from backend.core.models import CANDLE_COLUMNS, COMPLETE_COLUMN
from backend.data.candle_repository import CandleStreamAmbiguous, CanonicalCandleRepository
from backend.markets.base import CandleSource

logger = logging.getLogger("opendelta.market-data.reader")

READ_MODES = {"legacy", "timescale", "timescale-fallback"}


class TimescaleCandleSource:
    def __init__(
        self,
        repository: CanonicalCandleRepository,
        *,
        market: str,
        provider: str | None = None,
    ) -> None:
        self.repository = repository
        self.market = market.strip().upper()
        self.provider = provider.strip().upper() if provider else None

    def candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        warmup_bars: int,
    ) -> pd.DataFrame:
        stream = self.repository.resolve_stream(
            market=self.market,
            symbol=symbol,
            timeframe=timeframe,
            provider=self.provider,
        )
        if stream is None:
            return _empty_frame()
        rows = self.repository.candles(
            market=self.market,
            stream=stream,
            timeframe=timeframe,
            start=start,
            end=end,
            warmup_bars=warmup_bars,
        )
        if not rows:
            return _empty_frame()
        frame = pd.DataFrame(
            [
                {
                    "Open": row.open,
                    "High": row.high,
                    "Low": row.low,
                    "Close": row.close,
                    "Volume": row.volume,
                    COMPLETE_COLUMN: row.complete,
                }
                for row in rows
            ],
            index=pd.DatetimeIndex([row.open_time for row in rows], name="Timestamp"),
        )
        return frame[~frame.index.duplicated(keep="last")].sort_index()


class FallbackCandleSource:
    """Use a legacy reader only when an explicitly configured rollout permits it."""

    def __init__(self, primary: CandleSource, fallback: CandleSource) -> None:
        self.primary = primary
        self.fallback = fallback

    def candles(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        warmup_bars: int,
    ) -> pd.DataFrame:
        try:
            frame = self.primary.candles(
                symbol, timeframe, start, end, warmup_bars=warmup_bars
            )
        except CandleStreamAmbiguous:
            raise
        except Exception as error:  # explicit migration mode keeps production available
            logger.warning(
                "Timescale candle read failed for %s %s; using legacy fallback: %s",
                symbol,
                timeframe,
                type(error).__name__,
            )
            return self.fallback.candles(
                symbol, timeframe, start, end, warmup_bars=warmup_bars
            )
        if len(frame):
            return frame
        logger.warning(
            "Timescale has no candles for %s %s; using legacy fallback",
            symbol,
            timeframe,
        )
        return self.fallback.candles(
            symbol, timeframe, start, end, warmup_bars=warmup_bars
        )


def select_candle_source(
    mode: str,
    *,
    timescale: CandleSource,
    legacy: CandleSource,
) -> CandleSource:
    key = mode.strip().lower()
    if key not in READ_MODES:
        raise ValueError(
            "PLATFORM_CANDLE_READ_MODE must be legacy, timescale, or timescale-fallback"
        )
    if key == "legacy":
        return legacy
    if key == "timescale":
        return timescale
    return FallbackCandleSource(timescale, legacy)


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=[*CANDLE_COLUMNS, COMPLETE_COLUMN])
