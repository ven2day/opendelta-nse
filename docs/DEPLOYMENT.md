# Deployment Guide

Production runs three Docker containers on one Ubuntu host behind nginx and
Cloudflare: the dashboard (`opendelta-dashboard`), the backtest/platform API
(`opendelta-backtest`), and TimescaleDB (`opendelta-timescale`), plus the
post-close Dhan collector (`opendelta-data`) and the market-data worker. All
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

- `/etc/opendelta.env` — UI login only: `APP_USERNAME`, `APP_PASSWORD`, `AUTH_SECRET` (32+ random characters).
- `/etc/opendelta-dhan.env` — Dhan credentials, `MARKET_DATA_DATABASE_URL`, provider URLs. Mode `0600`, never passed to the dashboard container, never copied into a release archive or log. Template: `web/deploy/opendelta-dhan.env.example`.

## Building a release

```bash
release_id="$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD)"
git archive --format=tar.gz -o "/tmp/opendelta-deploy-${release_id}.tar.gz" HEAD
sudo web/deploy/install-release.sh "${release_id}"     # builds dashboard, collector and backtest images; tags :current
```

`install-release.sh` unpacks the archive under `/opt/opendelta/releases/<id>`,
builds all images and points `/opt/opendelta/current` at the release.

## Backtest / platform service

```bash
sudo web/deploy/install-backtest-service.sh   # installs/updates the systemd unit
sudo systemctl restart opendelta-backtest.service
curl -fsS http://127.0.0.1:3200/health
```

The unit runs `opendelta-backtest:current` read-only with dropped
capabilities, a PID limit and a memory limit (`--memory 4g`,
`BACKTEST_WORKERS=5` today). `BACKTEST_QUEUE_LIMIT` bounds running plus queued
backtests (default `200`); keep it at least as large as `BACKTEST_WORKERS`.
Parameter experiments reserve all child queue slots before any experiment rows
are created. The image copies the legacy modules, `opendelta/` and `backend/`.
Walk-forward submissions reserve their coordinator slot and every training-run
queue slot before persistence. Testing runs are queued one fold at a time after
the corresponding training winner is frozen.

## Database and migrations

TimescaleDB is installed once with `sudo web/deploy/install-timescale-service.sh`
(see [timescaledb-production-bootstrap.md](timescaledb-production-bootstrap.md)),
which writes `MARKET_DATA_DATABASE_URL` into `/etc/opendelta-dhan.env` and
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
until these are set on `opendelta-backtest.service`:

| Variable | Effect |
| --- | --- |
| `MARKET_DATA_DATABASE_URL` | already set by the TimescaleDB bootstrap |
| `PLATFORM_AUTO_MIGRATE=true` | apply migrations at startup instead of explicitly |
| `TRADINGVIEW_WEBHOOK_KEY` | random, revocable key placed only in TradingView alert JSON |
| `TRADINGVIEW_MAX_ALERT_AGE_SECONDS` | delivery freshness window; default `900` seconds |
| `NSE_SIGNAL_ENGINE_V2_ENABLED=true` | start every configured NSE v2 live-signal worker |
| `CRYPTO_SIGNAL_ENGINE_V2_ENABLED=true` | start every configured Crypto v2 live-signal worker |
| `NSE_PAPER_TRADING_V2_ENABLED` / `CRYPTO_PAPER_TRADING_V2_ENABLED` | paper broker per market (default `true` with the worker) |
| `NSE_LIVE_STRATEGIES` / `CRYPTO_LIVE_STRATEGIES` | JSON array of `{strategyId,timeframe}` bindings; NSE defaults to daily `rsi_dip_ladder_v1` |
| `NSE_LIVE_STRATEGY` / `NSE_LIVE_TIMEFRAME` | legacy single binding, used only if the plural setting is absent |
| `NSE_SIGNAL_POLL_SECONDS` / `CRYPTO_SIGNAL_POLL_SECONDS` | poll cadence (120 / 60) |
| `WALK_FORWARD_QUEUE_LIMIT` | bounded queued/running walk-forward coordinators (default `10`) |
| `WALK_FORWARD_POLL_SECONDS` | durable child-run polling cadence (default `0.5`) |
| `AI_PROVIDER` | optional adapter; currently `openai-compatible`; unset fails closed |
| `AI_PROVIDER_ENDPOINT`, `AI_MODEL` | deployment-controlled completion endpoint and model |
| `AI_PROVIDER_API_KEY` | provider secret; backend-only and never returned to the browser |
| `AI_PROVIDER_TIMEOUT_SECONDS` | request timeout, clamped to 1–60 seconds (default `30`) |
| `AI_COPILOT_REQUESTS_PER_MINUTE` | durable per-actor request limit (default `10`, maximum `60`) |

Suggested order: apply migrations → restart the service → verify
`GET /v2/dashboard?market=NSE` answers 200 → run a screener and save a universe
→ enable the Crypto worker (24/7, public data) → enable the NSE worker →
retire the legacy live-signal engine (`LIVE_SIGNAL_ENGINE_ENABLED`) and the
`/legacy/*` pages.

For Phase 7 specifically, build the application images without promoting
traffic, apply `017_parameter_experiments`, restart and verify the backtest API,
then promote the dashboard. Roll back the application images without deleting
the additive migration; completed research child runs remain immutable.

For Phase 8, apply `018_walk_forward_validations` after
`017_parameter_experiments`, restart the backend, then exercise the preview
endpoint before promoting the web image. A rollback uses the previous backend
and web images while retaining the additive tables; do not delete completed
training or unseen-test child runs.

For Phase 10, apply `019_ai_research_copilot` before configuring a provider.
Deploy with no `AI_PROVIDER*` variables first and verify the Copilot reports
`AI provider not configured` while all research screens remain operational.
Then supply provider settings through deployment secrets and restart. Rollback
removes the provider variables and restores the prior image; retain the additive
audit/draft tables.

For Phase 11, apply `020_secure_exchange_connections` before deploying the
matching API/web release. The runtime now includes `cryptography` for AES-256-GCM.
Supply `EXCHANGE_CREDENTIAL_MASTER_KEY` and
`EXCHANGE_CREDENTIAL_MASTER_KEY_VERSION` through deployment secrets. Optional
provider base URL overrides must use HTTPS. Follow
[the credential encryption and rotation runbook](credential-encryption.md).

To roll back Phase 11, disable any saved connections, deploy the preceding
application version, and retain both additive connection tables. Do not drop
encrypted records or audit history. Provider-side keys remain independent and
must be revoked at the exchange when no longer required.

For the NSE daily swing worker, production must have all of the following:

```dotenv
PLATFORM_CANDLE_READ_MODE=timescale-fallback
NSE_SIGNAL_ENGINE_V2_ENABLED=true
NSE_PAPER_TRADING_V2_ENABLED=true
NSE_LIVE_STRATEGIES=[{"strategyId":"rsi_dip_ladder_v1","timeframe":"1d"}]
```

After restart, verify the signal-health response reports the daily worker and
that the paper account's execution policy is `NEXT_OPEN`. Daily signals are
created only after 15:30 IST; the independent 5-minute tracking feed supplies
the next-session paper fill and in-session marks. Do not certify the service for
a session if either feed is stale or the paper account is missing.

## Market-data services

The Dhan collector (`opendelta-data.service` + timer) runs after the NSE close
on weekdays, authenticates with TOTP, validates the data subscription, and
publishes atomically only when the coverage threshold is met. Historical
backfill and gap repair use `python -m backend.data.admin` and
`python -m backend.data.worker`;
see [market-data-operations.md](market-data-operations.md).
