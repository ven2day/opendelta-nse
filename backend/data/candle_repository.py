"""Read completed canonical candles from the shared TimescaleDB database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Sequence

from backend.data.timescale import CanonicalCandle, utc


class CandleDatabase(Protocol):
    def fetch_all(self, query: str, parameters: Sequence[Any] | None = None) -> list[dict[str, Any]]: ...


class CandleStreamAmbiguous(ValueError):
    """Raised when a display symbol identifies more than one provider stream."""


@dataclass(frozen=True)
class CandleStream:
    provider: str
    instrument_id: str


class CanonicalCandleRepository:
    """Small read-only repository over ``market_candles``.

    Symbol resolution is strict. If two providers expose the same display symbol,
    callers must configure a provider rather than receiving a mixed data series.
    """

    def __init__(self, database: CandleDatabase) -> None:
        self.database = database

    def resolve_stream(
        self,
        *,
        market: str,
        symbol: str,
        timeframe: str,
        provider: str | None = None,
    ) -> CandleStream | None:
        key = symbol.strip().upper()
        provider_key = provider.strip().upper() if provider else None
        rows = self.database.fetch_all(
            """
            SELECT DISTINCT provider, instrument_id
            FROM (
                SELECT provider, instrument_id
                FROM market_candles
                WHERE market=%s AND timeframe=%s AND complete
                  AND symbol=%s AND (%s IS NULL OR provider=%s)
                UNION
                SELECT provider, instrument_id
                FROM market_candles
                WHERE market=%s AND timeframe=%s AND complete
                  AND instrument_id=%s AND (%s IS NULL OR provider=%s)
            ) AS candidates
            ORDER BY provider, instrument_id
            """,
            (
                market.strip().upper(), timeframe, key, provider_key, provider_key,
                market.strip().upper(), timeframe, key, provider_key, provider_key,
            ),
        )
        if not rows:
            return None
        if len(rows) > 1:
            choices = ", ".join(f"{row['provider']}:{row['instrument_id']}" for row in rows)
            raise CandleStreamAmbiguous(
                f"{market.upper()} symbol {key} maps to multiple candle streams: {choices}"
            )
        return CandleStream(str(rows[0]["provider"]), str(rows[0]["instrument_id"]))

    def candles(
        self,
        *,
        market: str,
        stream: CandleStream,
        timeframe: str,
        start: datetime,
        end: datetime,
        warmup_bars: int,
    ) -> list[CanonicalCandle]:
        if warmup_bars < 0:
            raise ValueError("warmup_bars must not be negative")
        lower, upper = utc(start), utc(end)
        if upper <= lower:
            raise ValueError("Candle range end must be after start")
        rows = self.database.fetch_all(
            """
            WITH warmup AS (
                SELECT market, provider, instrument_id, symbol, timeframe,
                       open_time, close_time, open, high, low, close, volume,
                       quote_volume, complete
                FROM market_candles
                WHERE market=%s AND provider=%s AND instrument_id=%s
                  AND timeframe=%s AND complete AND open_time < %s
                  AND close_time <= %s
                ORDER BY open_time DESC
                LIMIT %s
            ), requested AS (
                SELECT market, provider, instrument_id, symbol, timeframe,
                       open_time, close_time, open, high, low, close, volume,
                       quote_volume, complete
                FROM market_candles
                WHERE market=%s AND provider=%s AND instrument_id=%s
                  AND timeframe=%s AND complete
                  AND open_time >= %s AND open_time < %s AND close_time <= %s
            )
            SELECT * FROM warmup
            UNION ALL
            SELECT * FROM requested
            ORDER BY open_time
            """,
            (
                market.strip().upper(), stream.provider, stream.instrument_id,
                timeframe, lower, upper, warmup_bars,
                market.strip().upper(), stream.provider, stream.instrument_id,
                timeframe, lower, upper, upper,
            ),
        )
        return [CanonicalCandle(**dict(row)) for row in rows]
