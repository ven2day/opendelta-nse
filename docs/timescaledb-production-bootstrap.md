# TimescaleDB production bootstrap

## Scope and safety boundary

This procedure provisions the canonical candle database and prepares backfill
jobs. Reader migration is a separate, explicit runtime setting; its safe
default is `PLATFORM_CANDLE_READ_MODE=legacy`. Keep the legacy Dhan files and
crypto SQLite database intact until production count, checksum, gap, and
full-market-cycle checks pass, then validate `timescale-fallback` before using
strict `timescale` mode.

The service uses `timescale/timescaledb:2.29.2-pg17`, a Docker named volume,
and the existing `opendelta-internal` network. PostgreSQL port 5432 is not
published on the host. Database credentials live only in root-readable server
environment files.

## 1. Preflight

Before installation, confirm:

- `/opt/opendelta/current` points to the reviewed release;
- `opendelta-backtest:current` was built from that release;
- `/etc/opendelta-dhan.env` exists and is mode `0600`;
- Docker and systemd are healthy;
- sufficient space exists for both the Docker volume and `/var/backups`.

Do not put production passwords, generated calendars, or database dumps in the
repository.

## 2. Provision and migrate

Run as root:

```bash
/opt/opendelta/current/web/deploy/install-timescale-service.sh
```

On first install, the script creates a 64-character random hexadecimal
password in `/etc/opendelta-timescale.env`, updates
`MARKET_DATA_DATABASE_URL` in `/etc/opendelta-dhan.env`, starts the private
database, waits for a healthy `pg_isready`, runs migration 001, and enables the
daily backup timer. Re-running the installer preserves the existing password.

Check the service without exposing the secret:

```bash
systemctl status opendelta-timescale.service
docker inspect --format '{{.State.Health.Status}}' opendelta-timescale
systemctl list-timers opendelta-timescale-backup.timer
```

## 3. Build the NSE session calendar

Obtain an exact trading-day list from an official `nseindia.com` source for the
entire intended range. The input CSV has one required column:

```text
session_date
2024-09-02
2024-09-03
```

Generate the explicit all-date calendar:

```bash
python -m backend.data.calendar \
  --trading-days /secure/path/official-nse-trading-days.csv \
  --output /secure/path/nse-sessions.csv \
  --start 2024-09-01 \
  --end 2026-09-01 \
  --calendar-version NSE-2024-2026-v1 \
  --source-url https://nsearchives.nseindia.com/path/to/source.csv
```

The builder does not infer weekdays. Only input dates become trading sessions;
all other dates are emitted as closed. Official special weekend sessions are
preserved. The adjacent `.metadata.json` records validity dates, row counts,
the source URL, source SHA-256, and generated-calendar SHA-256. Bootstrap
refuses a calendar whose hash no longer matches. The builder rejects duplicates, dates
outside the requested range, incomplete boundary coverage, and implausibly
long missing sections.

Review both generated files before loading them.

## 4. Load, inspect, and optionally enqueue

Prepare the database without queueing any historical work:

```bash
sudo /opt/opendelta/current/web/deploy/bootstrap-market-data.sh \
  /secure/path/nse-sessions.csv \
  2024-09-01T00:00:00Z \
  2026-09-01T00:00:00Z
```

The bootstrap checks database health and calendar metadata, reapplies the
idempotent migration, loads the sessions, and prints repair-job health. After
reviewing that output, queue both the NSE universe and configured OKX
instruments by repeating the command with `--enqueue`:

```bash
sudo /opt/opendelta/current/web/deploy/bootstrap-market-data.sh \
  /secure/path/nse-sessions.csv \
  2024-09-01T00:00:00Z \
  2026-09-01T00:00:00Z \
  --enqueue
```

Review queued counts and unmapped NSE symbols before enabling the worker. A
repeated queue command is idempotent because job identity is deterministic.

## 5. Backup and restore drill

The timer creates a compressed custom-format `pg_dump` at 02:30 UTC each day,
validates it with `pg_restore --list`, then atomically publishes it below
`/var/backups/opendelta/timescale`. The default local retention is 30 days and
never falls below seven days. Local backups are not an off-host disaster
recovery copy; replicate the directory using the server's approved encrypted
backup system.

Create an on-demand verified backup:

```bash
sudo /usr/local/sbin/opendelta-timescale-backup
```

Restore is destructive and intentionally requires both an exact backup path
and confirmation token. It validates the dump, creates a fresh pre-restore
backup, pauses the worker and backtest service, restores with `--exit-on-error`,
and restarts services that were previously active:

```bash
sudo /usr/local/sbin/opendelta-timescale-restore \
  /var/backups/opendelta/timescale/opendelta-YYYYMMDDTHHMMSSZ.dump \
  --confirm-restore-opendelta
```

Run a restore drill on a non-production host before relying on the procedure.

## 6. Acceptance gate

Do not switch readers until all criteria in
[Canonical market-data operations](market-data-operations.md) pass in
production. Reader migration and versioned Parquet snapshots belong in a
separate reviewed pull request.
