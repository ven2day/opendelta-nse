from __future__ import annotations

import argparse
import csv
import os
from datetime import date, datetime
from pathlib import Path

from opendelta.timescale_market_data import TimescaleMarketDataStore


def database_url() -> str:
    value = os.environ.get("MARKET_DATA_DATABASE_URL", "").strip()
    if not value:
        raise RuntimeError("MARKET_DATA_DATABASE_URL is required")
    return value


def load_sessions(store: TimescaleMarketDataStore, path: Path, market: str) -> int:
    rows: list[tuple[object, ...]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            session_date = date.fromisoformat(row["session_date"])
            trading = row.get("is_trading_day", "true").strip().lower() == "true"
            opened = datetime.fromisoformat(row["open_time"]) if row.get("open_time") else None
            closed = datetime.fromisoformat(row["close_time"]) if row.get("close_time") else None
            rows.append((market.upper(), session_date, trading, opened, closed, row["calendar_version"]))
    with store.pool.connection() as connection, connection.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO market_sessions
                   (market, session_date, is_trading_day, open_time, close_time, calendar_version)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (market, session_date) DO UPDATE SET
                   is_trading_day=EXCLUDED.is_trading_day, open_time=EXCLUDED.open_time,
                   close_time=EXCLUDED.close_time, calendar_version=EXCLUDED.calendar_version""",
            rows,
        )
        connection.commit()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenDelta canonical market-data administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    sessions = commands.add_parser("load-sessions")
    sessions.add_argument("--market", default="NSE", choices=["NSE"])
    sessions.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    store = TimescaleMarketDataStore(database_url())
    store.open()
    try:
        if args.command == "migrate":
            store.migrate()
            print("TimescaleDB market-data migration completed")
        else:
            print(f"Loaded {load_sessions(store, args.file, args.market)} {args.market} market sessions")
    finally:
        store.close()


if __name__ == "__main__":
    main()
