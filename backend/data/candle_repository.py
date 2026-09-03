"""Read completed canonical candles from the shared TimescaleDB database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

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
        provider_clause = " AND provider=%s" if provider_key else ""
        parameters: tuple[Any, ...] = (
            market.strip().upper(), timeframe, key,
            *((provider_key,) if provider_key else ()),
            market.strip().upper(), timeframe, key,
            *((provider_key,) if provider_key else ()),
        )
        rows = self.database.fetch_all(
            f"""
            SELECT DISTINCT provider, instrument_id
            FROM (
                SELECT provider, instrument_id
                FROM market_candles
                WHERE market=%s AND timeframe=%s AND complete
                  AND symbol=%s{provider_clause}
                UNION
                SELECT provider, instrument_id
                FROM market_candles
                WHERE market=%s AND timeframe=%s AND complete
                  AND instrument_id=%s{provider_clause}
            ) AS candidates
            ORDER BY provider, instrument_id
            """,
            parameters,
        )
        if not rows:
            return None
        if len(rows) > 1:
            choices = ", ".join(f"{row['provider']}:{row['instrument_id']}" for row in rows)
            raise CandleStreamAmbiguous(
                f"{market.upper()} symbol {key} maps to multiple candle streams: {choices}"
            )
        return CandleStream(str(rows[0]["provider"]), str(rows[0]["instrument_id"]))

    def resolve_streams(
        self,
        *,
        market: str,
        symbols: Sequence[str],
        timeframe: str,
        provider: str | None = None,
    ) -> tuple[dict[str, CandleStream], dict[str, CandleStreamAmbiguous]]:
        """Resolve a batch in one round trip while isolating ambiguous symbols."""
        keys = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
        if not keys:
            return {}, {}
        provider_key = provider.strip().upper() if provider else None
        provider_clause = " AND candles.provider=%s" if provider_key else ""
        parameters: tuple[Any, ...] = (
            keys,
            market.strip().upper(), timeframe, *((provider_key,) if provider_key else ()),
            market.strip().upper(), timeframe, *((provider_key,) if provider_key else ()),
        )
        rows = self.database.fetch_all(
            f"""
            WITH requested(requested_symbol) AS (
                SELECT unnest(%s::text[])
            )
            SELECT requested.requested_symbol, candles.provider, candles.instrument_id
            FROM requested
            JOIN market_candles AS candles
              ON candles.symbol=requested.requested_symbol
            WHERE candles.market=%s AND candles.timeframe=%s AND candles.complete{provider_clause}
            GROUP BY requested.requested_symbol, candles.provider, candles.instrument_id
            UNION
            SELECT requested.requested_symbol, candles.provider, candles.instrument_id
            FROM requested
            JOIN market_candles AS candles
              ON candles.instrument_id=requested.requested_symbol
            WHERE candles.market=%s AND candles.timeframe=%s AND candles.complete{provider_clause}
            GROUP BY requested.requested_symbol, candles.provider, candles.instrument_id
            ORDER BY requested.requested_symbol, candles.provider, candles.instrument_id
            """,
            parameters,
        )
        candidates: dict[str, list[CandleStream]] = {key: [] for key in keys}
        for row in rows:
            key = str(row["requested_symbol"])
            candidates.setdefault(key, []).append(CandleStream(str(row["provider"]), str(row["instrument_id"])))
        resolved: dict[str, CandleStream] = {}
        errors: dict[str, CandleStreamAmbiguous] = {}
        for key, choices in candidates.items():
            unique = list(dict.fromkeys(choices))
            if len(unique) == 1:
                resolved[key] = unique[0]
            elif len(unique) > 1:
                labels = ", ".join(f"{item.provider}:{item.instrument_id}" for item in unique)
                errors[key] = CandleStreamAmbiguous(f"{market.upper()} symbol {key} maps to multiple candle streams: {labels}")
        return resolved, errors

    def candles_many(
        self,
        *,
        market: str,
        streams: Mapping[str, CandleStream],
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, list[CanonicalCandle]]:
        """Read several resolved streams in one query; intended for bounded batches."""
        if not streams:
            return {}
        lower, upper = utc(start), utc(end)
        if upper <= lower:
            raise ValueError("Candle range end must be after start")
        requested = list(streams.items())
        rows = self.database.fetch_all(
            """
            WITH requested(requested_symbol, provider, instrument_id) AS (
                SELECT * FROM unnest(%s::text[], %s::text[], %s::text[])
            )
            SELECT requested.requested_symbol,
                   candles.market, candles.provider, candles.instrument_id,
                   candles.symbol, candles.timeframe, candles.open_time,
                   candles.close_time, candles.open, candles.high, candles.low,
                   candles.close, candles.volume, candles.quote_volume, candles.complete
            FROM requested
            JOIN market_candles AS candles
              ON candles.provider=requested.provider
             AND candles.instrument_id=requested.instrument_id
            WHERE candles.market=%s AND candles.timeframe=%s AND candles.complete
              AND candles.open_time >= %s AND candles.open_time < %s
              AND candles.close_time <= %s
            ORDER BY requested.requested_symbol, candles.open_time
            """,
            (
                [item[0] for item in requested],
                [item[1].provider for item in requested],
                [item[1].instrument_id for item in requested],
                market.strip().upper(), timeframe, lower, upper, upper,
            ),
        )
        grouped: dict[str, list[CanonicalCandle]] = {symbol: [] for symbol, _ in requested}
        for row in rows:
            values = dict(row)
            symbol = str(values.pop("requested_symbol"))
            grouped.setdefault(symbol, []).append(CanonicalCandle(**values))
        return grouped

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
