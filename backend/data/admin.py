from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

from backend.markets.crypto.engine import CryptoMarketRepository
from backend.collector import build_security_map, download_instrument_master, load_symbols
from backend.data.timescale import TimescaleMarketDataStore


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


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include a timezone")
    return parsed.astimezone(UTC)


def enqueue_manifest(store: TimescaleMarketDataStore, path: Path) -> list[str]:
    job_ids: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            job_ids.append(
                str(
                    store.enqueue_backfill(
                        market=row["market"],
                        provider=row["provider"],
                        instrument_id=row["instrument_id"],
                        symbol=row["symbol"],
                        timeframe=row["timeframe"],
                        start=timestamp(row["start"]),
                        end=timestamp(row["end"]),
                        chunk_days=int(row.get("chunk_days") or 30),
                        max_attempts=int(row.get("max_attempts") or 5),
                    )
                )
            )
    return job_ids


def enqueue_nse_universe(
    store: TimescaleMarketDataStore,
    *,
    symbols_file: Path,
    instrument_master_url: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    chunk_days: int,
    max_attempts: int,
) -> tuple[list[str], list[str]]:
    symbols = load_symbols(symbols_file)
    instruments = download_instrument_master(instrument_master_url)
    security_map, missing = build_security_map(symbols, instruments)
    job_ids = [
        str(
            store.enqueue_backfill(
                market="NSE",
                provider="DHAN",
                instrument_id=security_map[symbol],
                symbol=symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                chunk_days=chunk_days,
                max_attempts=max_attempts,
            )
        )
        for symbol in symbols
        if symbol in security_map
    ]
    return job_ids, missing


def enqueue_okx_configured(
    store: TimescaleMarketDataStore,
    *,
    database_path: Path,
    timeframe: str,
    start: datetime,
    end: datetime,
    chunk_days: int,
    max_attempts: int,
) -> list[str]:
    repository = CryptoMarketRepository(database_path)
    instruments = [
        item
        for item in repository.instruments()
        if item.active and item.market == "CRYPTO" and item.provider == "OKX"
    ]
    return [
        str(
            store.enqueue_backfill(
                market="CRYPTO",
                provider="OKX",
                instrument_id=item.instrument_id,
                symbol=item.display_symbol,
                timeframe=timeframe,
                start=start,
                end=end,
                chunk_days=chunk_days,
                max_attempts=max_attempts,
            )
        )
        for item in instruments
    ]


def backfill_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeframe", required=True, choices=["1m", "5m", "15m", "1h"])
    parser.add_argument("--start", type=timestamp, required=True)
    parser.add_argument("--end", type=timestamp, required=True)
    parser.add_argument("--chunk-days", type=int, default=30)
    parser.add_argument("--max-attempts", type=int, default=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenDelta canonical market-data administration")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    sessions = commands.add_parser("load-sessions")
    sessions.add_argument("--market", default="NSE", choices=["NSE"])
    sessions.add_argument("--file", type=Path, required=True)
    enqueue = commands.add_parser("enqueue-backfill")
    enqueue.add_argument("--market", required=True, choices=["NSE", "CRYPTO"])
    enqueue.add_argument("--provider", required=True, choices=["DHAN", "OKX"])
    enqueue.add_argument("--instrument-id", required=True)
    enqueue.add_argument("--symbol", required=True)
    backfill_arguments(enqueue)
    manifest = commands.add_parser("enqueue-manifest")
    manifest.add_argument("--file", type=Path, required=True)
    nse = commands.add_parser("enqueue-nse-universe")
    nse.add_argument(
        "--symbols-file",
        type=Path,
        default=Path(os.environ.get("SYMBOLS_FILE", "symbols.csv")).expanduser(),
    )
    nse.add_argument(
        "--instrument-master-url",
        default=os.environ.get(
            "DHAN_INSTRUMENT_MASTER_URL",
            "https://images.dhan.co/api-data/api-scrip-master.csv",
        ),
    )
    backfill_arguments(nse)
    okx = commands.add_parser("enqueue-okx-configured")
    default_crypto_root = Path(
        os.environ.get("CRYPTO_MARKET_DIR", "/var/lib/vento-nse/backtest/crypto-market")
    ).expanduser()
    okx.add_argument("--database", type=Path, default=default_crypto_root / "market.sqlite3")
    backfill_arguments(okx)
    commands.add_parser("health")
    args = parser.parse_args()
    store = TimescaleMarketDataStore(database_url())
    store.open()
    try:
        if args.command == "migrate":
            store.migrate()
            print("TimescaleDB market-data migration completed")
        elif args.command == "load-sessions":
            print(f"Loaded {load_sessions(store, args.file, args.market)} {args.market} market sessions")
        elif args.command == "enqueue-backfill":
            job_id = store.enqueue_backfill(
                market=args.market,
                provider=args.provider,
                instrument_id=args.instrument_id,
                symbol=args.symbol,
                timeframe=args.timeframe,
                start=args.start,
                end=args.end,
                chunk_days=args.chunk_days,
                max_attempts=args.max_attempts,
            )
            print(json.dumps({"jobId": str(job_id), "status": "PENDING"}))
        elif args.command == "enqueue-manifest":
            job_ids = enqueue_manifest(store, args.file)
            print(json.dumps({"jobIds": job_ids, "count": len(job_ids)}))
        elif args.command == "enqueue-nse-universe":
            job_ids, missing = enqueue_nse_universe(
                store,
                symbols_file=args.symbols_file,
                instrument_master_url=args.instrument_master_url,
                timeframe=args.timeframe,
                start=args.start,
                end=args.end,
                chunk_days=args.chunk_days,
                max_attempts=args.max_attempts,
            )
            print(json.dumps({"jobIds": job_ids, "count": len(job_ids), "unmappedSymbols": missing}))
        elif args.command == "enqueue-okx-configured":
            job_ids = enqueue_okx_configured(
                store,
                database_path=args.database,
                timeframe=args.timeframe,
                start=args.start,
                end=args.end,
                chunk_days=args.chunk_days,
                max_attempts=args.max_attempts,
            )
            print(json.dumps({"jobIds": job_ids, "count": len(job_ids)}))
        else:
            print(json.dumps(store.repair_job_health(), separators=(",", ":")))
    finally:
        store.close()


if __name__ == "__main__":
    main()
