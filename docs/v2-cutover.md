# V2 default cutover and rollback

OpenDelta V2 is the default product and API workflow. The unified top
navigation points to the V2-backed Dashboard, Screener, Backtest, Signals,
Paper Trading, Research, Strategy Studio, Indicator Studio, Settings and
Operations workspaces. The safe `/admin` bookmark redirect remains.

## Default-state decisions

- TimescaleDB is the canonical candle reader. An unset
  `PLATFORM_CANDLE_READ_MODE` means strict `timescale`.
- `legacy` and `timescale-fallback` remain explicit operator rollback modes;
  they are not selected automatically.
- Signal and paper workers are built only from durable strategy deployment
  records. Environment flags or strategy lists cannot create an implicit
  deployment, approve a strategy or enable paper execution.
- No saved deployment means no signal worker. A saved `OFF` deployment does not
  generate signals. `PAPER` still requires the normal Backtest → Signals → Paper
  approval sequence.
- `LIVE_TRADING_ENABLED` and `LIVE_TRADING_DEPLOYMENT_ALLOWED` remain false by
  default. V2-default is not live-trading-default.

## Pre-cutover gate

1. Review and merge the ordered Phase 8-14 and validation pull requests.
2. Back up TimescaleDB and validate the dump with `pg_restore --list`.
3. Apply migrations 018 through 023 with `python -m backend.data.migrate`.
4. Run `python -m backend.data.migrate --check`; it must report no pending
   migration.
5. Verify canonical candle counts, checksums, gaps and freshness for Dhan, OKX
   and configured VALR instruments.
6. Run `PYTHONPATH=. python -m scripts.validate_v2_cutover` from the release.
7. Verify every desired NSE/Crypto strategy has an exact saved source/version,
   configuration, watchlist, timeframe and governance mode in Settings.
8. Ensure global/provider/market/strategy emergency-stop state is understood.
9. Confirm `LIVE_TRADING_ENABLED=false` and
   `LIVE_TRADING_DEPLOYMENT_ALLOWED=false`.

## Promotion order

1. Build the backend, isolated runner and web images without routing traffic.
2. Start the networkless Strategy V2 runner and verify its Unix-socket health.
3. Restart the backend against the migrated database in strict Timescale mode.
4. Verify `/health`, `/v2/dashboard?market=NSE`,
   `/v2/dashboard?market=CRYPTO`, `/v2/operations/health`, experiment preview
   and walk-forward preview.
5. Verify no worker exists for an absent/`OFF` deployment and that configured
   workers report the exact pinned version, universe and timeframe.
6. Start the candidate web container, run authenticated smoke/browser checks,
   then promote it atomically.
7. Observe freshness, queue, lease, connection and reconciliation alerts for a
   full Crypto interval and the next complete NSE session cycle.

No production order submission belongs in this verification.

## Rollback

1. If any execution concern exists, activate the appropriate emergency stop.
   Do not automatically cancel open provider orders.
2. Keep both live feature flags false and disable live deployments.
3. Restore the previous backend/runner/web image tags and release symlink.
4. If canonical reads caused the incident, explicitly select
   `PLATFORM_CANDLE_READ_MODE=timescale-fallback`; use `legacy` only when the
   documented fallback stores are healthy and provider identity remains clear.
5. Restart application units, not TimescaleDB or unrelated collectors, and run
   authenticated smoke tests.
6. Retain migrations 018-023 and all experiment, walk-forward, credential,
   intent, monitoring and agent audit rows. Additive schema is backward-safe;
   never drop it during an application rollback.
7. Resolve/acknowledge alerts only after recording the incident outcome.

## Post-cutover cleanup

The follow-up removal release deletes only dead environment parsing and the
duplicate old Crypto scanner startup hook. Compatibility Crypto APIs, cache
history and fallback readers remain because the dependency audit classifies
them `UNCERTAIN` or `KEEP`.
