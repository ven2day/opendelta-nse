"""CandleSource implementation backed by canonical TimescaleDB candles."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Sequence

import pandas as pd

from backend.core.models import CANDLE_COLUMNS, COMPLETE_COLUMN
from backend.data.candle_repository import CandleStreamAmbiguous, CanonicalCandleRepository
from backend.data.timescale import CanonicalCandle
from backend.markets.base import CandleBatch, CandleSource

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
        return _frame(rows)

    def candles_many(
        self,
        symbols: Sequence[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        warmup_bars: int,
    ) -> CandleBatch:
        if warmup_bars != 0:
            return _read_individually(self, symbols, timeframe, start, end, warmup_bars=warmup_bars)
        keys = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        streams, errors = self.repository.resolve_streams(
            market=self.market, symbols=keys, timeframe=timeframe, provider=self.provider
        )
        rows = self.repository.candles_many(
            market=self.market, streams=streams, timeframe=timeframe, start=start, end=end
        )
        return CandleBatch(
            frames={symbol: _frame(rows.get(symbol, [])) for symbol in keys},
            errors=errors,
        )


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

    def candles_many(
        self,
        symbols: Sequence[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        warmup_bars: int,
    ) -> CandleBatch:
        reader = getattr(self.primary, "candles_many", None)
        try:
            primary = reader(symbols, timeframe, start, end, warmup_bars=warmup_bars) if callable(reader) else _read_individually(self.primary, symbols, timeframe, start, end, warmup_bars=warmup_bars)
        except CandleStreamAmbiguous:
            raise
        except Exception as error:  # database-level batch failure: preserve availability through legacy
            logger.warning("Timescale batch read failed; using legacy fallback: %s", type(error).__name__)
            return _read_individually(self.fallback, symbols, timeframe, start, end, warmup_bars=warmup_bars)

        frames: dict[str, pd.DataFrame] = {}
        errors: dict[str, Exception] = {}
        for symbol in symbols:
            key = symbol.strip().upper()
            primary_error = primary.errors.get(key)
            if isinstance(primary_error, CandleStreamAmbiguous):
                errors[key] = primary_error
                continue
            frame = primary.frames.get(key, _empty_frame())
            if primary_error is None and len(frame):
                frames[key] = frame
                continue
            try:
                frames[key] = self.fallback.candles(key, timeframe, start, end, warmup_bars=warmup_bars)
            except Exception as error:  # the engine records this symbol without failing the batch
                errors[key] = error
        return CandleBatch(frames=frames, errors=errors)


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


def _frame(rows: Sequence[CanonicalCandle]) -> pd.DataFrame:
    if not rows:
        return _empty_frame()
    frame = pd.DataFrame(
        [{"Open": row.open, "High": row.high, "Low": row.low, "Close": row.close, "Volume": row.volume, COMPLETE_COLUMN: row.complete} for row in rows],
        index=pd.DatetimeIndex([row.open_time for row in rows], name="Timestamp"),
    )
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def _read_individually(source: CandleSource, symbols: Sequence[str], timeframe: str, start: datetime, end: datetime, *, warmup_bars: int) -> CandleBatch:
    frames: dict[str, pd.DataFrame] = {}
    errors: dict[str, Exception] = {}
    for symbol in symbols:
        key = symbol.strip().upper()
        try:
            frames[key] = source.candles(key, timeframe, start, end, warmup_bars=warmup_bars)
        except Exception as error:  # caller decides whether the failure is terminal
            errors[key] = error
    return CandleBatch(frames=frames, errors=errors)
