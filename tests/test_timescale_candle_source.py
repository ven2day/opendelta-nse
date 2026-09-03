from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from backend.data.candle_repository import (
    CandleStream,
    CandleStreamAmbiguous,
    CanonicalCandleRepository,
)
from backend.data.timescale import CanonicalCandle
from backend.markets.base import CandleBatch
from backend.markets.timescale_source import (
    FallbackCandleSource,
    TimescaleCandleSource,
    select_candle_source,
)


START = datetime(2026, 8, 28, 10, tzinfo=UTC)
END = START + timedelta(minutes=10)


def canonical(opened: datetime) -> CanonicalCandle:
    return CanonicalCandle(
        market="NSE",
        provider="DHAN",
        instrument_id="1333",
        symbol="HDFCBANK",
        timeframe="5m",
        open_time=opened,
        close_time=opened + timedelta(minutes=5),
        open=100,
        high=102,
        low=99,
        close=101,
        volume=1_000,
    )


class FakeRepository:
    def __init__(self, rows: list[CanonicalCandle], stream: CandleStream | None = None) -> None:
        self.rows = rows
        self.stream = stream if stream is not None else CandleStream("DHAN", "1333")
        self.read: dict[str, object] | None = None

    def resolve_stream(self, **_: object) -> CandleStream | None:
        return self.stream

    def candles(self, **values: object) -> list[CanonicalCandle]:
        self.read = values
        return self.rows


class FakeBatchRepository(FakeRepository):
    def __init__(self) -> None:
        super().__init__([])
        self.batch_reads = 0

    def resolve_streams(self, **_: object):
        return {
            "HDFCBANK": CandleStream("DHAN", "1333"),
            "TCS": CandleStream("DHAN", "11536"),
        }, {"AMBIGUOUS": CandleStreamAmbiguous("ambiguous")}

    def candles_many(self, **_: object):
        self.batch_reads += 1
        return {
            "HDFCBANK": [canonical(START)],
            "TCS": [],
        }


class FrameSource:
    def __init__(self, frame: pd.DataFrame | None = None, error: Exception | None = None) -> None:
        self.frame = frame if frame is not None else pd.DataFrame()
        self.error = error
        self.calls = 0

    def candles(self, *_: object, **__: object) -> pd.DataFrame:
        self.calls += 1
        if self.error:
            raise self.error
        return self.frame


class BatchFrameSource(FrameSource):
    def __init__(self, batch: CandleBatch) -> None:
        super().__init__()
        self.batch = batch
        self.batch_calls = 0

    def candles_many(self, *_: object, **__: object) -> CandleBatch:
        self.batch_calls += 1
        return self.batch


def test_timescale_source_returns_ordered_ohlcv_and_requests_exact_warmup() -> None:
    repository = FakeRepository([canonical(START - timedelta(minutes=5)), canonical(START)])
    source = TimescaleCandleSource(repository, market="NSE", provider="DHAN")  # type: ignore[arg-type]

    frame = source.candles("hdfcbank", "5m", START, END, warmup_bars=25)

    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume", "Complete"]
    assert list(frame.index) == [START - timedelta(minutes=5), START]
    assert str(frame.index.tz) == "UTC"
    assert frame["Complete"].all()
    assert repository.read is not None
    assert repository.read["warmup_bars"] == 25
    assert repository.read["stream"] == CandleStream("DHAN", "1333")


def test_timescale_source_returns_contract_compatible_empty_frame() -> None:
    repository = FakeRepository([], stream=None)
    repository.stream = None
    source = TimescaleCandleSource(repository, market="NSE")  # type: ignore[arg-type]

    frame = source.candles("MISSING", "5m", START, END, warmup_bars=0)

    assert frame.empty
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume", "Complete"]


def test_timescale_source_reads_a_batch_and_keeps_symbol_errors_isolated() -> None:
    repository = FakeBatchRepository()
    source = TimescaleCandleSource(repository, market="NSE", provider="DHAN")  # type: ignore[arg-type]

    batch = source.candles_many(["HDFCBANK", "TCS", "MISSING", "AMBIGUOUS"], "5m", START, END, warmup_bars=0)

    assert repository.batch_reads == 1
    assert list(batch.frames["HDFCBANK"]["Close"]) == [101]
    assert batch.frames["TCS"].empty
    assert batch.frames["MISSING"].empty
    assert isinstance(batch.errors["AMBIGUOUS"], CandleStreamAmbiguous)


def test_explicit_fallback_mode_uses_legacy_for_empty_or_failed_primary() -> None:
    expected = pd.DataFrame({"Close": [101.0]})
    legacy = FrameSource(expected)

    empty_router = FallbackCandleSource(FrameSource(), legacy)
    assert empty_router.candles("AAA", "5m", START, END, warmup_bars=0) is expected

    failed_router = FallbackCandleSource(FrameSource(error=OSError("down")), legacy)
    assert failed_router.candles("AAA", "5m", START, END, warmup_bars=0) is expected
    assert legacy.calls == 2


def test_batch_fallback_only_fetches_missing_symbols_and_never_hides_ambiguity() -> None:
    stored = pd.DataFrame({"Close": [101.0]})
    fetched = pd.DataFrame({"Close": [202.0]})
    primary = BatchFrameSource(
        CandleBatch(
            frames={"STORED": stored, "MISSING": pd.DataFrame()},
            errors={"AMBIGUOUS": CandleStreamAmbiguous("ambiguous")},
        )
    )
    legacy = FrameSource(fetched)

    batch = FallbackCandleSource(primary, legacy).candles_many(
        ["STORED", "MISSING", "AMBIGUOUS"], "5m", START, END, warmup_bars=0
    )

    assert primary.batch_calls == 1
    assert batch.frames["STORED"] is stored
    assert batch.frames["MISSING"] is fetched
    assert isinstance(batch.errors["AMBIGUOUS"], CandleStreamAmbiguous)
    assert legacy.calls == 1


def test_ambiguous_stream_never_silently_falls_back() -> None:
    primary = FrameSource(error=CandleStreamAmbiguous("ambiguous"))
    fallback = FrameSource(pd.DataFrame({"Close": [101.0]}))

    with pytest.raises(CandleStreamAmbiguous):
        FallbackCandleSource(primary, fallback).candles(
            "BTCUSDT", "5m", START, END, warmup_bars=0
        )
    assert fallback.calls == 0


def test_read_mode_is_explicit_and_validated() -> None:
    legacy, timescale = FrameSource(), FrameSource()
    assert select_candle_source("legacy", timescale=timescale, legacy=legacy) is legacy
    assert select_candle_source("timescale", timescale=timescale, legacy=legacy) is timescale
    assert isinstance(
        select_candle_source("timescale-fallback", timescale=timescale, legacy=legacy),
        FallbackCandleSource,
    )
    with pytest.raises(ValueError, match="PLATFORM_CANDLE_READ_MODE"):
        select_candle_source("automatic", timescale=timescale, legacy=legacy)


class CandidateDatabase:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows

    def fetch_all(self, _query: str, _parameters: object = None) -> list[dict[str, str]]:
        return self.rows


def test_repository_rejects_multiple_provider_streams_for_one_symbol() -> None:
    repository = CanonicalCandleRepository(
        CandidateDatabase(
            [
                {"provider": "OKX", "instrument_id": "ONE"},
                {"provider": "VALR", "instrument_id": "TWO"},
            ]
        )
    )
    with pytest.raises(CandleStreamAmbiguous, match="OKX:ONE.*VALR:TWO"):
        repository.resolve_stream(market="CRYPTO", symbol="BTCUSDT", timeframe="5m")
