from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from opendelta.timescale_market_data import (
    CanonicalCandle,
    GapRepairService,
    candle_data_version,
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
