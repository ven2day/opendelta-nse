from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from dhan_oi import DhanInstrument, DhanInstrumentCatalog
from dhan_oi_history import (
    HistoricalOiImportConfig,
    HistoricalOiImporter,
    nominal_nifty_weekly_expiry,
)
from main import DhanAPIError
from nifty_oi_regime import IST, NiftyOiConfig, OiRegimeRepository, score_spot_trend


def _timestamps(count: int = 25) -> list[int]:
    start = datetime(2026, 8, 24, 9, 15, tzinfo=IST)
    return [int((start + timedelta(minutes=5 * index)).timestamp()) for index in range(count)]


def _series(option_type: str) -> dict:
    timestamps = _timestamps()
    close = [100.0 + index * (1 if option_type == "CALL" else -0.25) for index in range(len(timestamps))]
    side = {
        "timestamp": timestamps,
        "open": close,
        "high": [value + 1 for value in close],
        "low": [value - 1 for value in close],
        "close": close,
        "iv": [12.0 + index * 0.01 for index in range(len(timestamps))],
        "volume": [1000 + index for index in range(len(timestamps))],
        "strike": [24500.0 for _ in timestamps],
        "oi": [10000.0 + index * 100 for index in range(len(timestamps))],
        "spot": [24505.0 + index for index in range(len(timestamps))],
    }
    return {"data": {"ce" if option_type == "CALL" else "pe": side}}


class FakeHistoryClient:
    def __init__(self) -> None:
        self.rolling_calls = 0

    def rolling_option_history(self, *args, option_type: str, **kwargs):
        self.rolling_calls += 1
        return _series(option_type)

    def historical_intraday(self, *args, **kwargs):
        timestamps = _timestamps()
        close = [24500.0 + index for index in range(len(timestamps))]
        return {
            "timestamp": timestamps,
            "open": close,
            "high": [value + 2 for value in close],
            "low": [value - 2 for value in close],
            "close": close,
            "volume": [100000 + index for index in range(len(timestamps))],
            "open_interest": [0 for _ in timestamps],
        }


class SplitHistoryClient(FakeHistoryClient):
    def rolling_option_history(self, *args, option_type: str, from_date: date, to_date: date, **kwargs):
        self.rolling_calls += 1
        if (to_date - from_date).days > 2:
            raise DhanAPIError("Dhan API network request failed after 4 attempts")
        return _series(option_type)


def _catalog() -> DhanInstrumentCatalog:
    return DhanInstrumentCatalog([
        DhanInstrument(
            security_id="13",
            exchange="NSE",
            segment="I",
            instrument="INDEX",
            underlying_security_id="",
            underlying_symbol="NIFTY",
            symbol_name="NIFTY",
            display_name="NIFTY 50",
            expiry=None,
            strike=None,
            option_type=None,
            expiry_flag=None,
        )
    ])


def test_nominal_expiry_transition_and_completed_expiry_bar_rollover() -> None:
    transition = date(2025, 8, 29)
    schedule = ((date(2020, 1, 1), 3), (transition, 1))
    before = datetime.combine(transition - timedelta(days=1), datetime.min.time(), tzinfo=IST).replace(hour=10)
    assert nominal_nifty_weekly_expiry(before, expiry_schedule=schedule) == date(2025, 8, 28)
    assert nominal_nifty_weekly_expiry(before.replace(hour=15, minute=21), expiry_schedule=schedule) == date(2025, 9, 4)
    after = datetime(2025, 8, 29, 10, tzinfo=IST)
    assert nominal_nifty_weekly_expiry(after, expiry_schedule=schedule) == date(2025, 9, 2)


def test_historical_import_requires_an_audited_expiry_schedule() -> None:
    config = HistoricalOiImportConfig(date(2026, 8, 24), date(2026, 8, 25))
    with pytest.raises(ValueError, match="audited NIFTY weekly expiry schedule"):
        config.validate()


def test_vectorized_historical_spot_score_matches_canonical_score() -> None:
    index = pd.DatetimeIndex([datetime.fromtimestamp(value, IST) for value in _timestamps()])
    frame = pd.DataFrame({
        "Open": range(100, 125),
        "High": range(102, 127),
        "Low": range(98, 123),
        "Close": range(101, 126),
        "Volume": range(1000, 1025),
    }, index=index)
    vectorized = HistoricalOiImporter._spot_components(frame)[index[-1].to_pydatetime()]
    canonical = score_spot_trend(frame, index[-1].to_pydatetime(), NiftyOiConfig())
    assert vectorized["score"] == canonical["score"]
    assert vectorized["emaSlopePct"] == canonical["emaSlopePct"]


def test_import_is_resumable_and_marks_strict_history_not_enforcement_ready(tmp_path) -> None:
    client = FakeHistoryClient()
    repository = OiRegimeRepository(tmp_path / "oi")
    importer = HistoricalOiImporter(
        client,  # type: ignore[arg-type]
        repository,
        tmp_path / "cache",
        instrument_catalog=_catalog(),
    )
    config = HistoricalOiImportConfig(
        from_date=date(2026, 8, 24),
        to_date=date(2026, 8, 25),
        strikes_each_side=0,
        expiry_schedule=((date(2020, 1, 1), 1),),
    )
    first = importer.run(config)
    assert first["state"] == "COMPLETE"
    assert first["tasksTotal"] == 2
    assert first["optionRowsImported"] == 50
    assert first["regimeSnapshotsCreated"] > 0
    assert first["enforcementReady"] is False
    assert first["enforceableSnapshots"] == 0
    assert repository.history_status()["historicalDepthAvailable"] is False
    assert len(repository.regimes()) == first["regimeSnapshotsCreated"]
    assert client.rolling_calls == 2

    with repository.option_file.open("a", encoding="utf-8") as handle:
        handle.write("{interrupted-json\n")
    assert len(repository.option_history()) == 50

    second = importer.run(config)
    assert second["optionRowsImported"] == 50
    assert second["optionRowsAddedThisRun"] == 0
    assert second["regimeSnapshotsCreated"] == first["regimeSnapshotsCreated"]
    assert second["regimeSnapshotsAddedThisRun"] == 0
    assert client.rolling_calls == 2


def test_timed_out_expired_option_range_is_split_and_cached(tmp_path) -> None:
    client = SplitHistoryClient()
    importer = HistoricalOiImporter(
        client,  # type: ignore[arg-type]
        OiRegimeRepository(tmp_path / "oi"),
        tmp_path / "cache",
        instrument_catalog=_catalog(),
    )
    config = HistoricalOiImportConfig(
        date(2026, 8, 20),
        date(2026, 8, 24),
        strikes_each_side=0,
        expiry_schedule=((date(2020, 1, 1), 1),),
    )
    payload = importer._expired_payload("13", config.from_date, config.to_date, "ATM", "CALL", config)
    assert len(payload["data"]["ce"]["timestamp"]) == 50
    assert client.rolling_calls == 3
    cached = importer._expired_payload("13", config.from_date, config.to_date, "ATM", "CALL", config)
    assert cached == payload
    assert client.rolling_calls == 3
