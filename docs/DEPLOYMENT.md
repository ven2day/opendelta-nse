# Deployment Guide

Production runs three Docker containers on one Ubuntu host behind nginx and
Cloudflare: the dashboard (`vento-nse-dashboard`), the backtest/platform API
(`vento-nse-backtest`), and TimescaleDB (`vento-nse-timescale`), plus the
post-close Dhan collector (`vento-nse-data`) and the market-data worker. All
units are systemd services whose templates live in `web/deploy/`.

## Table of contents

1. [Environment files](#environment-files)
2. [Building a release](#building-a-release)
3. [Backtest / platform service](#backtest--platform-service)
4. [Database and migrations](#database-and-migrations)
5. [Dashboard cutover and rollback](#dashboard-cutover-and-rollback)
6. [Enabling the unified platform](#enabling-the-unified-platform)
7. [Market-data services](#market-data-services)

## Environment files

- `/etc/vento-nse.env` — UI login only: `APP_USERNAME`, `APP_PASSWORD`, `AUTH_SECRET` (32+ random characters).
- `/etc/vento-nse-dhan.env` — Dhan credentials, `MARKET_DATA_DATABASE_URL`, provider URLs. Mode `0600`, never passed to the dashboard container, never copied into a release archive or log. Template: `web/deploy/vento-nse-dhan.env.example`.

## Building a release

```bash
release_id="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD)"
git archive --format=tar.gz -o "/tmp/vento-nse-deploy-${release_id}.tar.gz" HEAD
sudo web/deploy/install-release.sh "${release_id}"     # builds dashboard, collector and backtest images; tags :current
```

`install-release.sh` unpacks the archive under `/opt/vento-nse/releases/<id>`,
builds all images and points `/opt/vento-nse/current` at the release.

## Backtest / platform service

```bash
sudo web/deploy/install-backtest-service.sh   # installs/updates the systemd unit
sudo systemctl restart vento-nse-backtest.service
curl -fsS http://127.0.0.1:3200/health
```

The unit runs `vento-nse-backtest:current` read-only with dropped
capabilities, a PID limit and a memory limit (`--memory 4g`,
`BACKTEST_WORKERS=5` today). The image copies the legacy modules, `opendelta/`
and `backend/`.

## Database and migrations

TimescaleDB is installed once with `sudo web/deploy/install-timescale-service.sh`
(see [timescaledb-production-bootstrap.md](timescaledb-production-bootstrap.md)),
which writes `MARKET_DATA_DATABASE_URL` into `/etc/vento-nse-dhan.env` and
applies the candle schema with `python -m backend.data.admin migrate`.

The unified-platform tables are applied explicitly — the service never
migrates on its own:

```bash
python -m backend.data.migrate --check   # exit 1 while migrations are pending
python -m backend.data.migrate           # apply
```

Run this inside the backtest container (or any environment with the same
`MARKET_DATA_DATABASE_URL`). Until it has run, every `/v2/*` route answers 503
with the pending versions and the legacy routes are unaffected.

## Dashboard cutover and rollback

```bash
sudo web/deploy/run-container.sh <release_id> <port>   # start an isolated candidate
sudo web/deploy/verify-container.sh http://127.0.0.1:<port>
sudo web/deploy/promote-candidate.sh <release_id>      # atomic nginx + container swap, auto-rollback on failure
```

Before a cutover, record the active release directory and both image tags. To
roll back, stop only the dashboard and backtest units, restore the previous
release symlink/image tags with the same scripts, start those two units, and
run the authenticated smoke tests. Do not restart the unrelated market-data
collector.

## Enabling the unified platform

Everything is opt-in and defaults to off; production behaviour is unchanged
until these are set on `vento-nse-backtest.service`:

| Variable | Effect |
| --- | --- |
| `MARKET_DATA_DATABASE_URL` | already set by the TimescaleDB bootstrap |
| `PLATFORM_AUTO_MIGRATE=true` | apply migrations at startup instead of explicitly |
| `NSE_SIGNAL_ENGINE_V2_ENABLED=true` | start every configured NSE v2 live-signal worker |
| `CRYPTO_SIGNAL_ENGINE_V2_ENABLED=true` | start every configured Crypto v2 live-signal worker |
| `NSE_PAPER_TRADING_V2_ENABLED` / `CRYPTO_PAPER_TRADING_V2_ENABLED` | paper broker per market (default `true` with the worker) |
| `NSE_LIVE_STRATEGIES` / `CRYPTO_LIVE_STRATEGIES` | JSON array of `{strategyId,timeframe}` bindings; NSE defaults to daily `rsi_dip_ladder_v1` |
| `NSE_LIVE_STRATEGY` / `NSE_LIVE_TIMEFRAME` | legacy single binding, used only if the plural setting is absent |
| `NSE_SIGNAL_POLL_SECONDS` / `CRYPTO_SIGNAL_POLL_SECONDS` | poll cadence (120 / 60) |

Suggested order: apply migrations → restart the service → verify
`GET /v2/dashboard?market=NSE` answers 200 → run a screener and save a universe
→ enable the Crypto worker (24/7, public data) → enable the NSE worker →
retire the legacy live-signal engine (`LIVE_SIGNAL_ENGINE_ENABLED`) and the
`/legacy/*` pages.

## Market-data services

The Dhan collector (`vento-nse-data.service` + timer) runs after the NSE close
on weekdays, authenticates with TOTP, validates the data subscription, and
publishes atomically only when the coverage threshold is met. Historical
backfill and gap repair use `python -m backend.data.admin` and
`python -m backend.data.worker`;
see [market-data-operations.md](market-data-operations.md).
