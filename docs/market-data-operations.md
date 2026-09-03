# Canonical market-data operations

## Rollout boundary

TimescaleDB receives completed candles from Dhan and OKX, but the existing Dhan
file/cache and crypto SQLite readers remain authoritative during reconciliation.
Do not switch research or signal reads as part of this rollout.

## Bootstrap

1. Install the private database service with
   `sudo /opt/vento-nse/current/web/deploy/install-timescale-service.sh`. The
   installer creates a protected credential and updates
   `MARKET_DATA_DATABASE_URL`; it never prints the credential.
2. Export the exact official NSE trading dates for the complete approved range
   and build the versioned calendar with `python -m backend.data.calendar`.
3. Load the generated calendar with `python -m backend.data.admin load-sessions`, or
   use `bootstrap-market-data.sh` to migrate, load, and health-check together.
4. Review the calendar metadata, requested range, symbol mappings, and job
   counts before passing `--enqueue` to the bootstrap script.
5. Deploy and enable the worker with
   `sudo /opt/vento-nse/current/web/deploy/install-market-data-worker.sh`.
6. Confirm `/platform/data-health` reports the canonical store and dual writer.

The NSE calendar must contain every date in the requested backfill range. A
weekday is never assumed to be a trading day. Crypto expectations are continuous
UTC intervals and exclude the currently incomplete candle.

Database provisioning, credential rotation constraints, backup verification,
and guarded restore steps are documented in
[TimescaleDB production bootstrap](timescaledb-production-bootstrap.md).

## Queue a backfill

The current NSE symbol registry and all active configured OKX instruments can
be queued directly:

```bash
python -m backend.data.admin enqueue-nse-universe \
  --timeframe 5m --start 2024-09-01T00:00:00Z --end 2026-09-01T00:00:00Z
python -m backend.data.admin enqueue-okx-configured \
  --timeframe 5m --start 2024-09-01T00:00:00Z --end 2026-09-01T00:00:00Z
```

Single instrument:

```bash
python -m backend.data.admin enqueue-backfill \
  --market CRYPTO --provider OKX \
  --instrument-id INS-REPLACE --symbol BTCUSDT --timeframe 5m \
  --start 2024-09-01T00:00:00Z --end 2026-09-01T00:00:00Z \
  --chunk-days 30 --max-attempts 5
```

Bulk manifest:

```bash
python -m backend.data.admin enqueue-manifest --file /secure/path/backfill.csv
```

Required columns are:

```text
market,provider,instrument_id,symbol,timeframe,start,end,chunk_days,max_attempts
```

The deterministic job ID makes repeated enqueue commands idempotent. Supported
production adapters are Dhan NSE intraday `1m`, `5m`, `15m`, `1h` and OKX
crypto timeframes supported by the public provider.

## Worker lifecycle

`backend.data.worker` claims one durable job at a time with `FOR UPDATE SKIP
LOCKED`. A lease allows another process to recover work after a crash. A
successful chunk is upserted and read back; its count and exact candle SHA-256
must match before `next_start` advances. Failures retry with exponential backoff
and stop at `max_attempts`. When the final chunk completes, the worker detects
and repairs remaining gaps through the same provider adapter.

Run manually:

```bash
python -m backend.data.worker --providers DHAN,OKX --maximum-chunks 100
python -m backend.data.admin health
```

## Acceptance before reader migration

- no failed jobs;
- no missing candles for the approved instruments and ranges;
- repeated ingestion creates no duplicate identities;
- interrupted jobs resume from their stored checkpoint;
- Dhan/OKX legacy outputs remain available;
- canonical count and checksum reconciliation passes;
- `/platform/data-health` remains healthy through a complete market cycle.

Only a later pull request may move research readers to TimescaleDB and create
versioned Parquet snapshots. Redis remains deferred until measured latency or
worker fan-out requires it.
