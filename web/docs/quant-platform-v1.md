# OpenDelta production quant platform V1

## Scope and architecture

OpenDelta V1 is a modular monolith for NSE and public crypto market-data research. It preserves the authenticated Dashboard, Scanner, NSE Signals, NSE Backtest, stored results, strategies, URLs, and existing production environment contract. New modules are additive adapters around proven paths.

V1 deliberately excludes live broker execution, user-supplied executable strategies, order-book reconstruction, machine-learning prediction, microservices, and Kubernetes. New strategy, risk, and experiment responses report `paperOnly=true` and `liveOrdersEnabled=false`.

```text
Authenticated vinext application
  ├── common platform shell
  ├── existing Dashboard / Scanner / Signals / Backtests
  └── Markets / Research / Strategies / Risk / Data Health / Jobs
                    │ authenticated allow-listed proxy
                    ▼
FastAPI modular monolith
  ├── existing Dhan/NSE and RSI Recovery engines
  ├── existing OKX/VALR public crypto engine
  └── opendelta modules
       core → instruments / market data → factors → strategies
            → backtests / research / signals → risk / analytics
            → jobs / audit / observability
                    │
                    ▼
Existing bind-mounted persistence
  ├── Dhan and crypto candle caches
  ├── signal, settings, report, and history data
  └── platform.sqlite3 (jobs, audit, feature cache, migrations)
```

The modular monolith matches the existing deployment, authentication, and persistence topology. Stable module interfaces permit later extraction after measured scale justifies it.

## Module dependency map

| Module | Owns | Boundary |
|---|---|---|
| `core` | validated settings, IDs, logs, request IDs, metrics | standard library only |
| `instruments` | normalized NSE/crypto records and pagination | injected repositories |
| `market_data` | capabilities, normalization, quality, MTF alignment, feature cache | core |
| `factors` | definitions, parameters, warm-up, calculations, unsupported results | core + pandas |
| `strategies` | versioned lifecycle and compatibility | domain records |
| `backtests` | next-bar execution, collision, costs, snapshots | core |
| `analytics` | drawdown, expectancy, profit factor, consecutive outcomes | independent |
| `risk` | capital/concentration/loss policy and rejection reasons | domain records |
| `jobs` | SQLite store, idempotency, progress, retry, cancellation | core |
| `research` | splits, tournament, forward selection, result snapshots | factors + analytics + injected data |
| `platform` | composition root and API | public module interfaces |

`backtest_api.py` is the compatibility composition boundary. It adapts the existing Dhan candle store and crypto service; modules do not randomly import legacy implementations.

## Data flow and provider interface

1. The exact provider instrument supplies candles.
2. Complete candles normalize to UTC, deduplicate, validate, and retain provider/data-version provenance.
3. Higher-timeframe context becomes visible only at its close timestamp.
4. The factor engine validates market, timeframe, parameters, warm-up, and required inputs.
5. Missing benchmark, sector, spread, quote, order-book, or OI inputs return `UNSUPPORTED_DATA_REQUIREMENT`; they are never fabricated.
6. A versioned strategy or experiment consumes factors.
7. Signals use closed data and default to next-bar-open execution.
8. Explicit costs/slippage produce analytics and an immutable configuration snapshot.
9. Jobs retain progress, versions, result or sanitized failure, and audit metadata.

Provider capabilities declare markets, timeframes, data types, timezone, and public/private boundary:

- Dhan: NSE historical candles and configured live quotes. Existing credentials remain server-side.
- OKX: public instrument catalogue and completed candles.
- VALR: public instrument catalogue and completed candles.

Crypto research resolves provider plus exact configured `instrumentId`, `providerSymbol`, or `displaySymbol`. `XAUUSD.p` and `XAGUSD.p` are rejected unless the provider publishes and the user configures that exact instrument. No alias is substituted.

## Factor interface and catalogue

Every definition includes ID, version, family, plain-English education metadata, required data, markets/timeframes, bounded parameters, warm-up, missing-data behavior, and calculation output.

- Trend direction: EMA alignment, VWAP slope, higher-high/higher-low structure.
- Trend strength: ADX, normalized EMA slope, trend efficiency. ADX is strength, never direction.
- Momentum: RSI recovery, ROC, MACD histogram acceleration. RSI thresholds are not automatic orders.
- Volatility: ATR percentile, Bollinger-width percentile, candle-range percentile. Volatility is movement size, not direction.
- Volume: RVOL, volume Z-score, volume breakout.
- Relative strength: NIFTY return, sector return, ratio slope. Relative strength is not RSI.
- Market structure: opening-range breakout, swing breakout, room to resistance/support.
- Liquidity/execution: average traded value/volume, historical spread only with quotes, slippage sensitivity.
- Regime: trend, range, compression, expansion, extreme/chaotic volatility.
- Session: NSE buckets and crypto UTC weekday/weekend buckets.

## Strategy and backtest lifecycle

Strategy definitions declare key/version, market, lifecycle (`ACTIVE`, `RESEARCH_ONLY`, `RETIRED`), directions, timeframes, execution model, and safety flags. Existing RSI Range and RSI Recovery code paths remain authoritative and are not silently rewritten. Market-Aligned RSI Scalper and Market-Aligned VWAP Pullback remain retired; VWAP scanner candidates remain WATCH-only.

Backtest correctness contract:

1. Load complete, deduplicated, exact-instrument candles.
2. Freeze data and configuration versions.
3. Calculate only data available at the decision timestamp.
4. Generate after trigger-candle close and enter at next-bar open. Same-bar close is invalid.
5. Apply capital, position, daily-trade/loss, consecutive-loss, and holding constraints.
6. When target/stop order is unknowable, apply stop first; gap exits use the bar open.
7. Apply explicit fees and per-side slippage.
8. Retain strategy/factor/data versions, configuration ID, ledger, MAE/MFE where the underlying strategy supplies them, and analytics.

Existing production engines remain active for existing strategies. The modular contract is independently regression-tested for incremental migration.

## Signal, research, and job lifecycles

Existing RSI Recovery evaluation remains unchanged. The signal-first scanner prevents duplicates, records rejection reasons, limits trade-ready/watch candidates, and remains paper-only. Research is not connected to broker execution.

The original Research implementation is disabled because it used one-bar
next-open-to-next-close observations rather than an actual base strategy and
complete trade lifecycle. Retained results are read-only and labelled
`LEGACY_INVALID_RESEARCH_MODEL`. The server-side
`RESEARCH_ENGINE_V2_ENABLED` flag defaults to false and cannot be overridden by
the browser.

Research V2 is implemented behind that disabled gate. It starts with a
versioned base-strategy adapter, filters only causal strategy signals, and then
uses a deterministic trade lifecycle: next-execution-bar open, per-side
slippage, actual-entry target/stop, conservative `STOP_FIRST` collision,
gap-at-open handling, bounded holding, costs, capital/position/daily controls,
MAE/MFE, rejected signals, and a separate unresolved-trade list. The result is
finite JSON; an undefined profit factor is `null` with an explanatory state.

The engine uses chronological training, validation, and untouched test splits:

- Exact evaluates one configuration.
- Tournament compares one family against a fixed baseline.
- Forward selection keeps at most one factor per family only when validation expectancy and minimum-trade requirements improve; beam width is 1–3.

The forward-selection objective is configurable; sample-size and maximum-
drawdown guardrails must pass, and selection cannot read the final test
interval. The API reports possible combinations before dispatch and avoids
exhaustive search. Results below the configured trade minimum are `INCONCLUSIVE`.

Factor filters no longer share a generic percentile rule. Each registered
factor declares its output type, directionality, predicate, threshold schema,
valid range, role, and warm-up. Higher-timeframe values are backward-as-of
aligned at their close/availability timestamp and NSE context is never carried
across an overnight or weekend boundary. Feature cache keys include the exact
candle SHA-256 reference and every market, provider, timeframe, factor,
parameter, benchmark/sector, and calendar dependency.

```text
request → idempotency lookup → QUEUED → RUNNING → COMPLETE
                                  ├─→ RETRYING (bounded backoff)
                                  ├─→ CANCELLED (cooperative)
                                  └─→ FAILED (sanitized)
```

Workers and pending work are bounded. Restarted active jobs become `FAILED/WORKER_RESTARTED`, preventing false running state.

## Database and migrations

V1 reuses current files/SQLite rather than adding another database product. `platform.sqlite3` uses WAL and schema version 1:

- `schema_migrations(version, applied_at)`
- `platform_jobs` with unique idempotency key, status/progress, attempts, payload/result, sanitized error, timestamps
- `audit_events` with actor/entity metadata
- `feature_cache` keyed by market, symbol, provider, data version, range, timeframe, factor/version/parameters, benchmark and sector dependencies

Indexes cover job status/update and factor/data version. Migrations are idempotent. Back up SQLite files consistently before any future schema change.

## APIs and frontend

Backend `/platform` routes:

- `GET /health/live`, `/health/ready`, `/overview`, `/instruments`, `/factors`, `/strategies`, `/risk`, `/data-health`, `/metrics`
- `GET /jobs`, `GET /jobs/{id}`, `DELETE /jobs/{id}`
- `POST /research/estimate`, `POST /research/experiments`

The browser uses authenticated `/api/platform`, which has a strict action allow-list, validates job IDs, sends no-store responses, and forwards bounded idempotency keys. Raw exceptions and credentials are not returned.

The common shell provides market selector/clock, freshness, environment, worker state, user exit, responsive navigation, and paper-only status. Feature routes own typed clients and loading/error/empty states:

- `/` existing Dashboard
- `/markets`, `/scanner`
- `/research`, `/research/experiments`, `/research/results`
- `/strategies`, `/backtest`, `/backtest/crypto`
- `/signals`, `/signals/funnel`, `/signals/crypto`
- `/risk`, `/data-health`, `/jobs`, `/settings`

Tables are contained and scroll within their panel. Mobile navigation is off-canvas. Existing page-specific search/theme controls remain available in contextual toolbars.

## Security and observability

- Secrets stay in environment files; password/secret/token/cookie/authorization metadata is redacted.
- API routes require the existing secure session or internal proxy token.
- Symbols/parameters are validated; job IDs and proxy actions are allow-listed.
- No user Python, arbitrary file path, order, account, balance, withdrawal, or private exchange endpoint exists.
- Request IDs are validated/generated and returned in `X-Request-ID`.
- Metrics include HTTP status/duration, job health, and research submissions.
- Readiness covers migrations, worker, cache, freshness, and instrument-master state.
- Container hardening remains read-only, non-root, capability-dropped, no-new-privileges, and memory/PID limited.

## Development, test, and deployment

```bash
PYTHONPATH=. .venv/bin/pytest -q
cd web
npm ci
npm run lint
npx tsc --noEmit
npm run build
node --test tests/*.test.mjs
```

On this Windows workspace, Python is `../.venv/Scripts/python.exe` relative to repository root. Migration tests construct repositories against a temporary file. Run the synthetic performance check with `PYTHONPATH=. python benchmarks/benchmark_quant_platform.py`; the recorded result and its limits are in `performance-v1.md`.

Deploy only `git archive` of merged `main`. Build immutable images, back up persistent databases, start an isolated candidate, run authenticated checks, and promote only after all validation passes. Do not restart the unrelated scheduled NSE collector. The service sets `PLATFORM_DATA_DIR=/var/lib/vento-nse/backtest/platform` inside the existing bind mount.

Rollback restores the recorded release symlink, dashboard/backtest images and service definition, and nginx config, then restarts only the prior dashboard/backtest. See checked-in deploy scripts and `deployment.md`.

## Known limitations

- Legacy engines are adapted, not fully moved, to preserve behavior safely.
- Sector-relative factors require audited mappings and point-in-time sector candles.
- Historical spread requires synchronized bid/ask quotes.
- Dhan 1-minute research remains unavailable when the configured store does not expose it.
- OKX/VALR coverage and rate limits differ; exact provider instruments only.
- Research V2 remains disabled until its deterministic strategy-backtest and production acceptance gates pass.
- Jobs use bounded in-process threads. A durable external queue is an extension point only if future measured workload requires multi-host workers.
