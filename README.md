# 📈 OpenDelta — NSE & Crypto Trading Research Platform

> One application for screening, backtesting, live signals and paper trading across NSE and Crypto, built on a single shared strategy evaluator. Paper trading only — no broker or exchange order path exists.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/TimescaleDB%20%2F%20PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?style=for-the-badge&logo=react&logoColor=black)
![Vite](https://img.shields.io/badge/Vite-646CFF?style=for-the-badge&logo=vite&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-405%20backend%20%2B%2022%20web-2ea44f?style=for-the-badge)
![Paper only](https://img.shields.io/badge/execution-paper%20only-orange?style=for-the-badge)

---

## 🎯 Overview

**OpenDelta** runs at <https://nse.ventoday.com>. It selects a universe of
symbols, backtests a strategy over completed candles, watches live markets for
the same signals, and simulates execution in separate NSE (INR) and Crypto
(USDT) paper accounts — with exactly one strategy implementation used by all
four, so backtest and live results can never drift apart.

Every backtest run, live signal and paper lot stores the strategy id, version
and an immutable configuration snapshot, so historical results remain
reproducible after a strategy changes.

## ✨ Key Features

- **🔍 Screener** — configurable price, liquidity, volume, volatility and candle-coverage filters; every symbol is recorded as passed or rejected with a reason; rank by the key you choose and keep all passing symbols or a maximum you set; manual include/exclude; saved universes consumed by Backtest and Signals.
- **🔄 Backtest** — background jobs, one symbol at a time with bounded memory, next-candle-open entries, independent lots with their own target/stop/expiry, fees and slippage on both sides, MAE/MFE, drawdown, cancellation, failed-symbol isolation, trades written to PostgreSQL in batches.
- **📡 Live Signals** — an independent worker per market (NSE follows the session; Crypto runs 24/7), completed candles only, duplicate-safe storage via a database constraint, `STRONG_BUY → HOLDING → TARGET_HIT / EXITED / EXPIRED` lifecycle, restart recovery, connection/data-age/last-candle health.
- **💼 Paper Trading** — internal `PaperBroker`, fixed-quantity or fixed-capital sizing with Strong Buy lot multipliers, one order per signal, candle-driven exits, realized/unrealized/daily P&L, portfolio rebuilt from the database after a restart.
- **🧩 Strategy plug-ins** — one file + one registration; the screener, backtest, signals, paper trading, API and settings forms discover it automatically. No strategy-name `if/elif` anywhere.
- **🖥️ One website** — Dashboard · Screener · Backtest · Signals · Paper Trading · Settings, each with an NSE / Crypto switch.

## 🏗️ Architecture

```mermaid
graph TB
    subgraph Data["📊 Market data"]
        DH[Dhan REST · NSE 5m candles]
        CX[OKX / VALR public candles]
        TS[(TimescaleDB candles)]
    end
    subgraph Core["⚙️ Shared core"]
        IND[backend.core.indicators]
        REG[STRATEGIES registry]
        SB[STRONG_BUY_V1]
        REG --> SB
        SB --> IND
    end
    subgraph Engines["🚦 Engines"]
        SCR[Screener]
        BT[Backtest engine]
        SIG[Signal workers NSE · Crypto]
        PB[PaperBroker NSE · Crypto]
    end
    subgraph Store["🗄️ PostgreSQL"]
        DB[(screener_runs · saved_universes · backtest_runs/trades · live_signals · paper_* · engine_status)]
    end
    DH --> SCR & BT & SIG
    CX --> SCR & BT & SIG
    SCR --> DB
    BT --> REG
    SIG --> REG
    BT --> DB
    SIG --> DB
    SIG --> PB --> DB
    DB --> API["v2 API"]
    API --> UI["Dashboard · Screener · Backtest · Signals · Paper Trading · Settings"]
```

The evaluator contract is `Strategy.evaluate(candles, market_context, config) → SignalDecision`
(BUY / SELL / NONE with prices, reasons, indicators, version and snapshot),
called only on completed candles; entries happen at the next candle's open.
Details: [docs/unified-platform.md](docs/unified-platform.md).

## 🚀 Quick Start

### Prerequisites

- Python 3.12 and [uv](https://github.com/astral-sh/uv)
- Node.js 22
- PostgreSQL/TimescaleDB (optional locally; the `/v2` pages report "not configured" without it)
- Dhan API credentials for NSE data (see `web/deploy/vento-nse-dhan.env.example`)

### Backend

```bash
git clone https://github.com/ven2day/opendelta-nse.git
cd opendelta-nse
uv sync
export MARKET_DATA_DATABASE_URL=postgresql://user:pass@localhost:5432/opendelta   # optional
python -m backend.data.migrate                                                   # optional, explicit
PYTHONPATH=. uv run uvicorn backtest_api:app --port 8000
```

### Web

```bash
cd web
cp .env.example .env.local        # APP_USERNAME, APP_PASSWORD, AUTH_SECRET, BACKTEST_SERVICE_URL
npm ci
npm run dev                        # http://localhost:3000
```

## 🧪 Testing

```bash
PYTHONPATH=. pytest -q                                                       # 405 backend tests
TEST_DATABASE_URL=postgresql://…/opendelta_test PYTHONPATH=. pytest -q       # + PostgreSQL suites
cd web && npm run lint && npx tsc --noEmit && npm test                       # web (22 tests, builds first)
cd web && npm run test:browser                                               # Playwright shell checks
python scripts/security_scan.py                                              # secret scan
```

The suites prove, among other things: backtest and live evaluation agree bar
for bar; an incomplete candle never produces a signal; entries use the next
candle open; a different future never changes earlier trades; duplicate
signals and duplicate paper orders are rejected by the database; NSE and
Crypto balances stay separate; the paper portfolio survives a restart; fees
and slippage reconcile exactly; a 100-symbol one-year backtest writes
incrementally and stays under 256 MB; each Strong Buy lot closes
independently; and no order-placement code exists.

## 📁 Project Structure

```
opendelta-nse/
├── backend/                      # unified platform
│   ├── core/                     # models.py (SignalDecision, MarketContext), indicators.py
│   ├── strategies/               # base.py (interface), registry.py, strong_buy_v1.py
│   ├── markets/                  # base.py; nse/ and crypto/ fees, sessions, candle adapters
│   ├── screener/                 # engine.py, filters.py, ranking.py
│   ├── backtest/                 # engine.py, metrics.py, result_writer.py, jobs.py
│   ├── signals/                  # candle_processor.py, engine.py, recovery.py, workers.py
│   ├── paper_trading/            # broker.py, portfolio.py, execution.py, accounting.py
│   ├── data/                     # database.py, repositories.py, migrate.py, sql/001_platform.sql
│   ├── api/                      # screener, backtest, signal, paper_trading, settings, dashboard routes
│   └── platform_runtime.py       # wires the platform into the FastAPI app
├── opendelta/                    # TimescaleDB canonical candle store + SQL migration
├── backtest_api.py               # FastAPI app (legacy routes + /platform/* + mounts /v2)
├── main.py                       # Dhan client/credentials (the only place they are read) + collector
├── ema_vwap_strong_buy.py        # legacy Strong Buy module, now delegating to the plug-in
├── live_signals.py, crypto_*.py, recovery_*.py, market_data_*.py, …   # legacy runtime modules
├── web/
│   ├── app/                      # pages: / screener backtest signals paper-trading settings, legacy/*
│   │   ├── api/                  # authenticated proxies (api/v2/[...path] for the platform)
│   │   └── platform/             # chrome, market switch, schema-driven forms, v2 client
│   ├── deploy/                   # Dockerfiles, systemd units, install/promote/verify scripts
│   └── tests/                    # rendered/proxy tests and Playwright spec
├── tests/                        # pytest suites
├── docs/                         # unified-platform.md, API.md, DEPLOYMENT.md, TROUBLESHOOTING.md,
│   ├── adr/                      #   market-data-operations.md, timescaledb-production-bootstrap.md
│   └── legacy/                   #   retired-strategy reports
├── scripts/                      # security_scan.py, regression_existing_strategies.py
├── benchmarks/                   # legacy engine benchmarks and baselines
├── symbols.csv, nse_symbols_rsi_volume.csv, strategy-parameters.json   # data files used by deploy
├── PROJECT_OVERVIEW.md · CONTRIBUTING.md · SECURITY.md
└── pyproject.toml · uv.lock
```

## ⚙️ Configuration

| Variable | Purpose |
| --- | --- |
| `MARKET_DATA_DATABASE_URL` | PostgreSQL/TimescaleDB for candles and the platform tables |
| `PLATFORM_AUTO_MIGRATE` | `true` to migrate at startup; otherwise `python -m backend.data.migrate` |
| `NSE_SIGNAL_ENGINE_V2_ENABLED`, `CRYPTO_SIGNAL_ENGINE_V2_ENABLED` | start the v2 live-signal workers |
| `NSE_PAPER_TRADING_V2_ENABLED`, `CRYPTO_PAPER_TRADING_V2_ENABLED` | paper broker per market (default on with the worker) |
| `NSE_LIVE_STRATEGY`, `CRYPTO_LIVE_STRATEGY` | strategy id for live signals |
| `DHAN_*` | Dhan credentials, read only by `main.py` |
| `BACKTEST_WORKERS`, `BACKTEST_CACHE_DIR` | legacy backtest service tuning and candle cache |

All v2 features default to off; production behaviour is unchanged until they
are enabled. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## 📚 Documentation

- [Architecture and how to add a strategy](docs/unified-platform.md)
- [API reference](docs/API.md)
- [Deployment](docs/DEPLOYMENT.md) · [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Market-data operations](docs/market-data-operations.md) · [TimescaleDB bootstrap](docs/timescaledb-production-bootstrap.md)
- [Architecture decision records](docs/adr) · [Legacy strategy reports](docs/legacy)
- [Project overview](PROJECT_OVERVIEW.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## 🛠️ Tech Stack

**Backend:** Python 3.12, FastAPI, pandas, NumPy, psycopg 3, PostgreSQL 17 / TimescaleDB, pytest, uv
**Frontend:** TypeScript, React 19, vinext (Vite + React Server Components), Playwright
**Ops:** Docker, systemd, nginx, Cloudflare, GitHub Actions

## 🔒 Security

No live order execution exists and tests enforce it. Credentials stay in
`/etc/vento-nse-dhan.env` on the host and are never committed; see
[SECURITY.md](SECURITY.md). No strategy in this repository is represented as
guaranteed profitable.

## 📄 License

Not yet specified. Add a `LICENSE` file to declare one.

## 👤 Author

**Ven** — [ven2day](https://github.com/ven2day)
