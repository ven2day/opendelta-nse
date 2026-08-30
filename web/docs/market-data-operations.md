# Canonical market-data operations

## Rollout boundary

TimescaleDB receives completed candles from Dhan and OKX, but the existing Dhan
file/cache and crypto SQLite readers remain authoritative during reconciliation.
Do not switch research or signal reads as part of this rollout.

## Bootstrap

1. Set `MARKET_DATA_DATABASE_URL` in the protected server environment.
2. Run `python market_data_admin.py migrate`.
3. Load the versioned NSE session calendar with `market_data_admin.py load-sessions`.
4. Deploy and enable the worker with
   `sudo /opt/vento-nse/current/web/deploy/install-market-data-worker.sh`.
5. Confirm `/platform/data-health` reports the canonical store and dual writer.

The NSE calendar must contain every date in the requested backfill range. A
weekday is never assumed to be a trading day. Crypto expectations are continuous
UTC intervals and exclude the currently incomplete candle.

## Queue a backfill

The current NSE symbol registry and all active configured OKX instruments can
be queued directly:

```bash
python market_data_admin.py enqueue-nse-universe \
  --timeframe 5m --start 2024-09-01T00:00:00Z --end 2026-09-01T00:00:00Z
python market_data_admin.py enqueue-okx-configured \
  --timeframe 5m --start 2024-09-01T00:00:00Z --end 2026-09-01T00:00:00Z
```

Single instrument:

```bash
python market_data_admin.py enqueue-backfill \
  --market CRYPTO --provider OKX \
  --instrument-id INS-REPLACE --symbol BTCUSDT --timeframe 5m \
  --start 2024-09-01T00:00:00Z --end 2026-09-01T00:00:00Z \
  --chunk-days 30 --max-attempts 5
```

Bulk manifest:

```bash
python market_data_admin.py enqueue-manifest --file /secure/path/backfill.csv
```

Required columns are:

```text
market,provider,instrument_id,symbol,timeframe,start,end,chunk_days,max_attempts
```

The deterministic job ID makes repeated enqueue commands idempotent. Supported
production adapters are Dhan NSE intraday `1m`, `5m`, `15m`, `1h` and OKX
crypto timeframes supported by the public provider.

## Worker lifecycle

`market_data_worker.py` claims one durable job at a time with `FOR UPDATE SKIP
LOCKED`. A lease allows another process to recover work after a crash. A
successful chunk is upserted and read back; its count and exact candle SHA-256
must match before `next_start` advances. Failures retry with exponential backoff
and stop at `max_attempts`. When the final chunk completes, the worker detects
and repairs remaining gaps through the same provider adapter.

Run manually:

```bash
python market_data_worker.py --providers DHAN,OKX --maximum-chunks 100
python market_data_admin.py health
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
