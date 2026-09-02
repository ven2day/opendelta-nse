# Troubleshooting

## The unified pages show "Unified platform database not configured" (HTTP 503)

- `MARKET_DATA_DATABASE_URL` is not set for the backtest service, or
- the schema is behind. Run `python -m backend.data.migrate --check`; if it
  exits 1, run `python -m backend.data.migrate` (or set
  `PLATFORM_AUTO_MIGRATE=true` deliberately) and restart the service.

The 503 `detail` names the pending versions. Legacy pages and routes keep
working in the meantime.

## A backtest is `INTERRUPTED`

The service restarted while the run was `QUEUED` or `RUNNING`. Runs are not
resumed automatically; start it again. The trades already written for that
run remain readable.

## A backtest is slow

The first run after a market close refetches every symbol from Dhan (4
requests/second, chunked by 89 days), because the cache is only considered
fresh once it was written after that session's close. Later runs the same day
hit the cache and the fetch phase disappears. Memory is bounded per symbol;
raising `BACKTEST_WORKERS` does not speed up the fetch phase.

## Signals page: engine `MARKET_CLOSED` / `STALE_DATA` / `ERROR`

- `MARKET_CLOSED` — expected outside 09:15–15:30 IST on weekdays for NSE; the
  Crypto worker never reports it.
- `STALE_DATA` — completed candles are older than the stale threshold; check
  the Dhan feed and `GET /v2/signals/health`.
- `ERROR` with "Poll failed, retrying in Ns" — the worker backs off
  exponentially (max 300s) and reconnects on its own; the message carries the
  provider error.

## Duplicate signals or paper orders

They cannot be stored: `live_signals` is unique per candle/strategy version
and `paper_orders` allows one filled BUY per signal per account. If a replay
seems to "lose" a signal, that is the constraint rejecting a duplicate — see
`duplicatesRejected` in the worker status.

## `npm run build` fails with an engine error

The web app requires Node 22 (`engines` in `web/package.json`). On the
production host a matching binary is available through `npx`; in CI
`actions/setup-node` pins 22.

## Playwright cannot bind port 3000

Another `next-server`/`vinext` may already own it. Start the built app on a
different port (`vinext start --port 3100`) and point the spec at it.

## Tests that need PostgreSQL are skipped

Set `TEST_DATABASE_URL` to a throwaway database, e.g.

```bash
docker run -d --name opendelta-test-pg -p 127.0.0.1:45179:5432 \
  -e POSTGRES_USER=opendelta -e POSTGRES_PASSWORD=opendelta_test -e POSTGRES_DB=opendelta_test \
  timescale/timescaledb:2.29.2-pg17
TEST_DATABASE_URL=postgresql://opendelta:opendelta_test@127.0.0.1:45179/opendelta_test PYTHONPATH=. pytest -q
```

The database tests drop and recreate the `public` schema — never point them
at production.
