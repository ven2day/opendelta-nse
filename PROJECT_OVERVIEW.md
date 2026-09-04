# OpenDelta — Project Overview

## Project status

Production at <https://delta.ventoday.com>. The unified NSE + Crypto platform
(screener, backtest, live signals, paper trading on one strategy evaluator)
is complete on the `feature/unified-trading-platform` branch and gated off in
production by environment flags until it is deliberately enabled.

## Key metrics

| | |
| --- | --- |
| Backend tests | 405 (+1 real-data memory test that runs where the candle cache exists) |
| Web tests | 22 rendered/proxy tests + 9 Playwright shell checks |
| Strategy plug-ins | 1 (`ema_vwap_strong_buy`, STRONG_BUY_V1); adding one is a file + a registration |
| Markets | NSE (Dhan, IST sessions, INR), Crypto (OKX/VALR public data, 24/7, USDT) |
| Order execution | none — paper only, enforced by tests |

## Project structure

```
backend/          unified platform (see README)
opendelta/        TimescaleDB canonical candle store and SQL migration
web/              vinext dashboard, API proxies, deploy templates
tests/            pytest suites (backend, engines, routes, database)
docs/             architecture, API, deployment, troubleshooting, ADRs, legacy notes
scripts/          security scan, legacy regression hash
benchmarks/       legacy engine benchmarks and baselines
*.py (root)       legacy runtime modules still used by the backtest service
```

## Features

- **Screener** — configurable price/liquidity/volume/volatility filters,
  candle-availability validation, ranking, recorded rejection reasons,
  manual include/exclude, saved universes (one active per market).
- **Backtest** — background runs, incremental per-symbol processing with
  bounded memory, independent lots, fees and slippage, target/stop/holding
  limit, MAE/MFE, drawdown, cancellation, per-symbol failure isolation,
  everything persisted with strategy id, version and configuration snapshot.
- **Live signals** — one worker per market, completed candles only,
  duplicate-safe storage, lifecycle statuses, restart recovery, health.
- **Paper trading** — separate INR/USDT accounts, fixed-quantity or
  fixed-capital sizing with Strong Buy lot rules, candle-driven exits,
  realized/unrealized/daily P&L, rebuilt from the database on restart.
- **Settings** — strategy and risk forms generated from the registry's
  schemas; per-market active configuration.

## Documentation

- Users/operators: [README](README.md), [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- Developers: [docs/unified-platform.md](docs/unified-platform.md), [docs/API.md](docs/API.md), [CONTRIBUTING.md](CONTRIBUTING.md), [docs/adr](docs/adr)
- Security: [SECURITY.md](SECURITY.md)

## Technology stack

Python 3.12, FastAPI, pandas/NumPy, psycopg 3, PostgreSQL/TimescaleDB, structlog;
TypeScript, React 19, vinext (Vite), Playwright; Docker, systemd, nginx,
GitHub Actions.

## Security

No broker or exchange order client exists. Dhan credentials are read only by
`backend/collector.py`; the web app never sees them. See [SECURITY.md](SECURITY.md).

## License

Apache-2.0 — see [LICENSE](LICENSE).
