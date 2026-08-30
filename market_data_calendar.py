from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


NSE_ZONE = ZoneInfo("Asia/Kolkata")
NSE_OPEN = time(9, 15)
NSE_CLOSE = time(15, 30)
MAXIMUM_SESSION_GAP_DAYS = 14


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("dates must use YYYY-MM-DD") from error


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def load_exact_trading_days(path: Path, *, start: date, end: date) -> list[date]:
    if start > end:
        raise ValueError("start must not be after end")

    sessions: list[date] = []
    seen: set[date] = set()
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "session_date" not in reader.fieldnames:
            raise ValueError("trading-day CSV must contain a session_date column")
        for row_number, row in enumerate(reader, start=2):
            try:
                session = date.fromisoformat(row["session_date"].strip())
            except (AttributeError, ValueError) as error:
                raise ValueError(f"invalid session_date on CSV row {row_number}") from error
            if session in seen:
                raise ValueError(f"duplicate trading day: {session.isoformat()}")
            if not start <= session <= end:
                raise ValueError(f"trading day outside requested range: {session.isoformat()}")
            seen.add(session)
            sessions.append(session)

    sessions.sort()
    if not sessions:
        raise ValueError("trading-day CSV contains no sessions")
    if (sessions[0] - start).days > 7:
        raise ValueError("trading-day source does not cover the start of the requested range")
    if (end - sessions[-1]).days > 7:
        raise ValueError("trading-day source does not cover the end of the requested range")
    for previous, current in zip(sessions, sessions[1:]):
        if (current - previous).days > MAXIMUM_SESSION_GAP_DAYS:
            raise ValueError(
                "trading-day source contains an implausible gap: "
                f"{previous.isoformat()} to {current.isoformat()}"
            )
    return sessions


def build_calendar_rows(
    sessions: list[date], *, start: date, end: date, calendar_version: str
) -> list[dict[str, str]]:
    if not calendar_version.strip() or len(calendar_version) > 80:
        raise ValueError("calendar version must contain 1 to 80 characters")
    exact_sessions = set(sessions)
    rows: list[dict[str, str]] = []
    current = start
    while current <= end:
        trading = current in exact_sessions
        opened = datetime.combine(current, NSE_OPEN, NSE_ZONE).isoformat() if trading else ""
        closed = datetime.combine(current, NSE_CLOSE, NSE_ZONE).isoformat() if trading else ""
        rows.append(
            {
                "session_date": current.isoformat(),
                "is_trading_day": str(trading).lower(),
                "open_time": opened,
                "close_time": closed,
                "calendar_version": calendar_version,
            }
        )
        current += timedelta(days=1)
    return rows


def render_csv(rows: list[dict[str, str]]) -> str:
    fieldnames = [
        "session_date",
        "is_trading_day",
        "open_time",
        "close_time",
        "calendar_version",
    ]
    from io import StringIO

    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def generate_calendar(
    *,
    trading_days_path: Path,
    output_path: Path,
    start: date,
    end: date,
    calendar_version: str,
    source_url: str,
) -> tuple[int, Path]:
    parsed_source = urlparse(source_url)
    source_host = (parsed_source.hostname or "").lower()
    if parsed_source.scheme != "https" or not (
        source_host == "nseindia.com" or source_host.endswith(".nseindia.com")
    ) or parsed_source.username or parsed_source.password:
        raise ValueError("source URL must be an official nseindia.com HTTPS URL")
    source_bytes = trading_days_path.read_bytes()
    sessions = load_exact_trading_days(trading_days_path, start=start, end=end)
    rows = build_calendar_rows(
        sessions, start=start, end=end, calendar_version=calendar_version
    )
    rendered_calendar = render_csv(rows)
    _atomic_write(output_path, rendered_calendar)

    metadata_path = output_path.with_suffix(output_path.suffix + ".metadata.json")
    metadata = {
        "calendarVersion": calendar_version,
        "market": "NSE",
        "validFrom": start.isoformat(),
        "validThrough": end.isoformat(),
        "sourceUrl": source_url,
        "sourceSha256": hashlib.sha256(source_bytes).hexdigest(),
        "calendarSha256": hashlib.sha256(rendered_calendar.encode("utf-8")).hexdigest(),
        "tradingDayCount": len(sessions),
        "calendarRowCount": len(rows),
    }
    _atomic_write(metadata_path, json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return len(rows), metadata_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a fail-closed NSE calendar from an exact official trading-day list"
    )
    parser.add_argument("--trading-days", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=parse_date, required=True)
    parser.add_argument("--end", type=parse_date, required=True)
    parser.add_argument("--calendar-version", required=True)
    parser.add_argument("--source-url", required=True)
    args = parser.parse_args()
    count, metadata_path = generate_calendar(
        trading_days_path=args.trading_days,
        output_path=args.output,
        start=args.start,
        end=args.end,
        calendar_version=args.calendar_version,
        source_url=args.source_url,
    )
    print(json.dumps({"rows": count, "calendar": str(args.output), "metadata": str(metadata_path)}))


if __name__ == "__main__":
    main()
