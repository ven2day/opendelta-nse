from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from backend.data.calendar import generate_calendar, load_exact_trading_days


def write_sessions(path: Path, values: list[str]) -> None:
    path.write_text("session_date\n" + "\n".join(values) + "\n", encoding="utf-8")


def test_generate_calendar_is_exact_and_records_provenance(tmp_path: Path) -> None:
    source = tmp_path / "official-sessions.csv"
    write_sessions(source, ["2026-10-02", "2026-10-03", "2026-10-05"])
    output = tmp_path / "nse-calendar.csv"

    count, metadata_path = generate_calendar(
        trading_days_path=source,
        output_path=output,
        start=date(2026, 10, 2),
        end=date(2026, 10, 6),
        calendar_version="NSE-2026-v1",
        source_url="https://nsearchives.nseindia.com/content/circulars/example.csv",
    )

    rows = list(csv.DictReader(output.open(encoding="utf-8")))
    assert count == 5
    assert [row["is_trading_day"] for row in rows] == ["true", "true", "false", "true", "false"]
    assert rows[0]["open_time"] == "2026-10-02T09:15:00+05:30"
    assert rows[0]["close_time"] == "2026-10-02T15:30:00+05:30"
    assert rows[2]["open_time"] == ""
    assert rows[1]["session_date"] == "2026-10-03"  # exact special weekend sessions survive

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["validFrom"] == "2026-10-02"
    assert metadata["validThrough"] == "2026-10-06"
    assert metadata["tradingDayCount"] == 3
    assert metadata["sourceSha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert metadata["calendarSha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (["2026-01-02", "2026-01-02"], "duplicate trading day"),
        (["2025-12-31", "2026-01-02"], "outside requested range"),
        (["2026-01-02", "2026-01-20"], "implausible gap"),
    ],
)
def test_trading_day_source_validation(tmp_path: Path, values: list[str], message: str) -> None:
    source = tmp_path / "sessions.csv"
    write_sessions(source, values)
    with pytest.raises(ValueError, match=message):
        load_exact_trading_days(source, start=date(2026, 1, 1), end=date(2026, 1, 21))


def test_calendar_rejects_non_nse_provenance(tmp_path: Path) -> None:
    source = tmp_path / "sessions.csv"
    write_sessions(source, ["2026-01-02"])
    with pytest.raises(ValueError, match="official nseindia.com"):
        generate_calendar(
            trading_days_path=source,
            output_path=tmp_path / "calendar.csv",
            start=date(2026, 1, 1),
            end=date(2026, 1, 3),
            calendar_version="NSE-2026-v1",
            source_url="https://example.com/calendar.csv",
        )
