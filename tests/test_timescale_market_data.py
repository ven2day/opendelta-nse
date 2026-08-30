from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import uuid

import pandas as pd
import pytest

from opendelta.timescale_market_data import (
    BackfillJob,
    BackfillWorker,
    CanonicalCandle,
    GapRepairService,
    ReconciliationResult,
    TimescaleDualWriter,
    candle_data_version,
    canonical_candles_from_dhan_frame,
    contiguous_missing_ranges,
    crypto_expected_open_times,
    nse_expected_open_times,
    timescale_health,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 28, hour, minute, tzinfo=UTC)


def candle(opened: datetime, close: float = 101) -> CanonicalCandle:
    return CanonicalCandle(
        market="NSE", provider="DHAN", instrument_id="1333", symbol="HDFCBANK",
        timeframe="1m", open_time=opened, close_time=opened + timedelta(minutes=1),
        open=100, high=102, low=99, close=close, volume=1000,
    )


def test_contiguous_missing_ranges_groups_only_adjacent_candles() -> None:
    expected = [at(10, minute) for minute in range(6)]
    actual = [expected[0], expected[3], expected[5]]

    result = contiguous_missing_ranges(expected, actual, "1m")

    assert [(item.start, item.end, item.missing_candles) for item in result] == [
        (expected[1], expected[3], 2),
        (expected[4], expected[5], 1),
    ]


def test_crypto_expected_times_are_continuous_and_exclude_incomplete_end() -> None:
    result = crypto_expected_open_times(at(10), at(10, 3), "1m")
    assert result == [at(10), at(10, 1), at(10, 2)]


def test_nse_expected_times_require_explicit_trading_sessions() -> None:
    start = datetime(2026, 8, 28, 3, 0, tzinfo=UTC)
    end = datetime(2026, 8, 31, 11, 0, tzinfo=UTC)
    result = nse_expected_open_times([date(2026, 8, 28)], start, end, "5m")

    assert len(result) == 75
    assert result[0] == datetime(2026, 8, 28, 3, 45, tzinfo=UTC)
    assert result[-1] == datetime(2026, 8, 28, 9, 55, tzinfo=UTC)
    assert all(item.date() == date(2026, 8, 28) for item in result)


def test_canonical_candle_rejects_invalid_ohlc() -> None:
    with pytest.raises(ValueError, match="Invalid OHLCV"):
        CanonicalCandle(
            market="CRYPTO", provider="OKX", instrument_id="BTC-USDT", symbol="BTC-USDT",
            timeframe="1m", open_time=at(10), close_time=at(10, 1),
            open=100, high=99, low=98, close=101, volume=1,
        )


def test_data_version_changes_when_candle_content_changes() -> None:
    first = candle(at(10), 101)
    second = candle(at(10), 101.5)
    assert candle_data_version([first]) == candle_data_version([first])
    assert candle_data_version([first]) != candle_data_version([second])


def test_timescale_health_fails_closed_until_configured() -> None:
    assert timescale_health(None) == {"status": "NOT_CONFIGURED", "sourceOfTruth": False}


def test_dhan_frame_conversion_writes_only_complete_valid_candles() -> None:
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 101.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Volume": [1000.0, 1100.0, 1200.0],
        },
        index=pd.to_datetime(["2026-08-28T10:00Z", "2026-08-28T10:01Z", "2026-08-28T10:02Z"]),
    )
    result = canonical_candles_from_dhan_frame(
        frame,
        instrument_id="1333",
        symbol="HDFCBANK",
        timeframe="1m",
        completed_before=at(10, 2),
    )
    assert [item.open_time for item in result] == [at(10), at(10, 1)]
    assert all(item.provider == "DHAN" and item.complete for item in result)


class WriterStore:
    def __init__(self, fail: bool = False, mismatch: bool = False) -> None:
        self.fail = fail
        self.mismatch = mismatch
        self.opened = False
        self.closed = False
        self.rows: list[CanonicalCandle] = []

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def upsert_candles(self, candles: list[CanonicalCandle]) -> int:
        if self.fail:
            raise OSError("database unavailable with secret details")
        self.rows.extend(candles)
        return len(candles)

    def reconcile(self, expected: list[CanonicalCandle], **_: object) -> ReconciliationResult:
        expected_version = candle_data_version(expected)
        canonical_version = "sha256:mismatch" if self.mismatch else expected_version
        return ReconciliationResult(
            "MISMATCH" if self.mismatch else "MATCHED",
            len(expected),
            len(expected),
            expected_version,
            canonical_version,
        )


def test_dual_writer_is_best_effort_and_exposes_sanitized_failure_state() -> None:
    disabled = TimescaleDualWriter(None)
    assert disabled.write([candle(at(10))]).status == "DISABLED"
    assert disabled.status()["status"] == "NOT_CONFIGURED"

    store = WriterStore(fail=True)
    writer = TimescaleDualWriter("postgresql://test", store_factory=lambda _: store)  # type: ignore[arg-type]
    result = writer.write([candle(at(10))])
    assert result.status == "FAILED"
    assert result.error_type == "OSError"
    assert writer.status()["lastErrorType"] == "OSError"
    assert "secret" not in str(writer.status())

    matched_store = WriterStore()
    matched = TimescaleDualWriter(
        "postgresql://test", store_factory=lambda _: matched_store  # type: ignore[arg-type]
    ).write([candle(at(10))])
    assert matched.status == "WRITTEN" and matched.reconciled is True

    mismatch_store = WriterStore(mismatch=True)
    mismatch_writer = TimescaleDualWriter(
        "postgresql://test", store_factory=lambda _: mismatch_store  # type: ignore[arg-type]
    )
    mismatch = mismatch_writer.write([candle(at(10))])
    assert mismatch.status == "MISMATCH"
    assert mismatch_writer.status()["mismatchBatches"] == 1


class ResumableStore:
    def __init__(self) -> None:
        self.job = BackfillJob(
            job_id=uuid.uuid4(),
            market="CRYPTO",
            provider="OKX",
            instrument_id="BTC-USDT",
            symbol="BTCUSDT",
            timeframe="1h",
            range_start=at(0),
            range_end=at(0) + timedelta(days=2),
            next_start=at(0),
            chunk_days=1,
            status="PENDING",
            attempts=0,
            max_attempts=3,
        )
        self.rows: dict[datetime, CanonicalCandle] = {}
        self.health_rows: list[dict[str, object]] = []

    def claim_backfill(self, worker_id: str) -> BackfillJob | None:
        if self.job.status != "PENDING":
            return None
        self.job = BackfillJob(**{**self.job.__dict__, "status": "RUNNING"})
        return self.job

    def upsert_candles(self, candles: list[CanonicalCandle]) -> int:
        self.rows.update({item.open_time: item for item in candles})
        return len(candles)

    def reconcile(self, expected: list[CanonicalCandle], **_: object) -> ReconciliationResult:
        version = candle_data_version(expected)
        return ReconciliationResult("MATCHED", len(expected), len(expected), version, version)

    def advance_backfill(
        self, job_id: uuid.UUID, *, next_start: datetime, received: int, written: int,
        reconciliation: ReconciliationResult,
    ) -> bool:
        complete = next_start >= self.job.range_end
        self.job = BackfillJob(
            **{
                **self.job.__dict__,
                "next_start": next_start,
                "status": "COMPLETE" if complete else "PENDING",
            }
        )
        return complete

    def fail_backfill(self, job_id: uuid.UUID, error: Exception) -> str:
        self.job = BackfillJob(**{**self.job.__dict__, "status": "RETRY"})
        return "RETRY"

    def candle_times(self, *args: object) -> list[datetime]:
        return sorted(self.rows)

    def record_health(self, **values: object) -> None:
        self.health_rows.append(values)


class HourlyProvider:
    def candles(
        self, instrument_id: str, timeframe: str, start: datetime, end: datetime
    ) -> list[CanonicalCandle]:
        rows: list[CanonicalCandle] = []
        current = start
        while current < end:
            rows.append(
                CanonicalCandle(
                    market="CRYPTO",
                    provider="OKX",
                    instrument_id=instrument_id,
                    symbol="BTCUSDT",
                    timeframe=timeframe,
                    open_time=current,
                    close_time=current + timedelta(hours=1),
                    open=100,
                    high=101,
                    low=99,
                    close=100,
                    volume=1,
                )
            )
            current += timedelta(hours=1)
        return rows


def test_backfill_worker_resumes_at_checkpoint_and_repairs_after_completion() -> None:
    store = ResumableStore()
    first_worker = BackfillWorker(store, {"OKX": HourlyProvider()}, worker_id="worker-one")  # type: ignore[arg-type]
    first = first_worker.run_once()
    assert first is not None and first["status"] == "PENDING"
    assert store.job.next_start == at(0) + timedelta(days=1)

    restarted_worker = BackfillWorker(store, {"OKX": HourlyProvider()}, worker_id="worker-two")  # type: ignore[arg-type]
    second = restarted_worker.run_once()
    assert second is not None and second["status"] == "COMPLETE"
    assert second["reconciliation"]["status"] == "MATCHED"
    assert second["repair"]["status"] == "HEALTHY"
    assert len(store.rows) == 48


def test_retried_completed_backfill_runs_gap_repair_without_empty_provider_window() -> None:
    store = ResumableStore()
    provider = HourlyProvider()
    all_rows = provider.candles(
        store.job.instrument_id,
        store.job.timeframe,
        store.job.range_start,
        store.job.range_end,
    )
    store.rows = {item.open_time: item for item in all_rows if item.open_time != at(5)}
    store.job = BackfillJob(
        **{
            **store.job.__dict__,
            "next_start": store.job.range_end,
            "status": "PENDING",
        }
    )

    result = BackfillWorker(store, {"OKX": provider}, worker_id="repair-retry").run_once()  # type: ignore[arg-type]

    assert result is not None and result["status"] == "COMPLETE"
    assert result["repair"]["missing_before"] == 1
    assert result["repair"]["candles_written"] == 1
    assert len(store.rows) == 48


class FakeStore:
    def __init__(self, actual: list[datetime]) -> None:
        self.actual = actual
        self.health_rows: list[dict[str, object]] = []

    def candle_times(self, *args: object) -> list[datetime]:
        return sorted(self.actual)

    def upsert_candles(self, candles: list[CanonicalCandle]) -> int:
        self.actual.extend(item.open_time for item in candles)
        return len(candles)

    def record_health(self, **values: object) -> None:
        self.health_rows.append(values)


class FakeProvider:
    def candles(self, instrument_id: str, timeframe: str, start: datetime, end: datetime) -> list[CanonicalCandle]:
        result = []
        current = start
        while current < end:
            result.append(CanonicalCandle(
                market="CRYPTO", provider="OKX", instrument_id=instrument_id, symbol=instrument_id,
                timeframe=timeframe, open_time=current, close_time=current + timedelta(minutes=1),
                open=100, high=101, low=99, close=100, volume=1,
            ))
            current += timedelta(minutes=1)
        return result


def test_gap_repair_fetches_only_missing_range_and_revalidates() -> None:
    store = FakeStore([at(10), at(10, 2)])
    service = GapRepairService(store, {"OKX": FakeProvider()})  # type: ignore[arg-type]

    result = service.repair(
        market="CRYPTO", provider="OKX", instrument_id="BTC-USDT", timeframe="1m",
        start=at(10), end=at(10, 3),
    )

    assert result.status == "HEALTHY"
    assert result.missing_before == 1
    assert result.candles_received == 1
    assert result.missing_after == 0
    assert [row["status"] for row in store.health_rows] == ["REPAIRING", "HEALTHY"]
