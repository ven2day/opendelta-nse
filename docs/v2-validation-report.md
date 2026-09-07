# OpenDelta V2 validation report

Assessment date: 2026-09-07. Validation stack: migrations 001-023.

## Release decision

The codebase is ready for a staged V2-default cutover after the ordered Phase
8-14 pull requests and this validation change are reviewed and merged. The
automated lifecycle, policy and browser suites pass without credentials or
provider mutations. Real live trading remains disabled unless every server-side
gate is separately configured and an operator completes the explicit approval
flow.

Run the environment-free release contract with:

```bash
PYTHONPATH=. python -m scripts.validate_v2_cutover
```

The command validates the exact NSE/Crypto market boundary, supported private
providers, migrations, V2 routes, live-disabled defaults, MCP prohibition
surface and networkless Strategy V2 deployment. It makes no database writes,
provider calls or production mutations.

## Lifecycle evidence

| Requirement | NSE evidence | Crypto evidence |
| --- | --- | --- |
| Provider to completed canonical candles | Dhan parsing, session/calendar, repair and Timescale suites | OKX and VALR public provider contracts, completed-candle filtering and dual-write tests |
| Watchlist/universe pinning | screener, preset and backtest route suites | saved-universe and Crypto catalogue suites |
| Strategy/Indicator V2 identity | immutable source validation, exact version/source tests | same market-neutral V2 source contract |
| Backtest, costs/slippage, no lookahead | engine, real-cache and synthetic NSE E2E suites | shared engine plus Crypto fee/provider suites |
| Candlestick chart | immutable chart payload and exact run-link tests | same chart API/workspace with Crypto market parameter |
| Research/parameter comparison | experiment transaction/idempotency, sweep and comparison suites | same engine and immutable child-run contract |
| Walk-forward | NSE trading-session fold tests | continuous UTC fold tests |
| Signals to Paper | approval/pinning, restart, duplicate-signal, FIFO and NSE mock E2E | shared approval/paper tests with separate Crypto account |
| Monitoring | market freshness, leases, queues, alerts, audits and Operations browser tests | Crypto 24/7 freshness and provider connection alerts |
| Secure connections/live foundation | Dhan status and mocked order-adapter contracts | encrypted OKX/VALR records, permission tests and mocked adapters |
| Agent access | strict schema, scope/rate/idempotency/audit and no-secret tests | market parameter remains limited to NSE/CRYPTO |

## Safety and correctness conclusions

- Exact strategy version, source, configuration, timeframe, universe and date
  snapshots are persisted by the backtest/research/governance repositories.
- Candle normalization drops incomplete rows and all engines process ordered,
  timezone-aware data. Existing no-lookahead tests prove a changed future does
  not change earlier trades.
- NSE scheduling uses stored sessions and Asia/Kolkata; Crypto folds are UTC and
  continuous. Unknown markets now fail closed rather than inheriting Crypto.
- Fees and slippage are applied through the shared market execution settings;
  paper NSE exits preserve Dhan-compatible FIFO accounting.
- Backtest, experiment, walk-forward, paper and live-intent paths have durable
  idempotency/cancellation/restart coverage. Completed child runs remain
  immutable.
- Connection loss, stale data, queue pressure and reconciliation mismatches are
  durable monitoring conditions. Alert cooldowns prevent repeated storms.
- Copilot output is always a draft and MCP exposes no approvals, deployments,
  credentials, emergency-stop release, host execution or live-order tools.
- No automatic test sends a real order. A timeout is treated as unknown and
  reconciliation-required, never assumed failed.

## Verification record

Validation-branch results on Windows/Python 3.12/Node 22:

| Command | Result |
| --- | --- |
| `PYTHONPATH=. pytest -q` | 368 passed, 28 skipped in 18.97s |
| `PYTHONPATH=. python -m scripts.validate_v2_cutover` | PASS, seven checks; no credentials/mutations |
| `ruff check` on changed Python files | passed |
| `ruff check .` | 217 pre-existing findings outside this change; no new finding |
| `python scripts/security_scan.py` | passed |
| Git Bash `bash -n web/deploy/*.sh` | passed |
| `npm ci` | completed; audit reported development dependency findings |
| `npm run lint` | passed |
| `npx tsc --noEmit` | passed |
| `npm audit --omit=dev --audit-level=high` | zero production vulnerabilities |
| `npm run build` | passed |
| `node --test tests/*.test.mjs` | 39 passed |
| `npx playwright install --with-deps chromium` | passed |
| `npm run test:browser` | 10 passed |

The local host had neither `TEST_DATABASE_URL` nor a Docker server, so the 28
PostgreSQL/TimescaleDB tests were skipped locally. GitHub backend CI supplies a
PostgreSQL service, applies every migration from zero and executes those tests;
its result is the database release gate for this pull request.

## Deliberately deferred

- True rejected-trade analytics require a separate decision-event audit model.
  “Failed symbols” remains an operational metric; a NONE decision is not a
  rejected trade.
- Production-provider order submission is not a release test and remains
  separately authorized. Sandbox/manual certification is an operator activity.
- Compatibility Crypto API removal and Crypto SQLite history migration require
  owner/access-log decisions and are classified `UNCERTAIN`.
- Production table deletion is excluded. Obsolete tables, if identified later,
  must first become read-only and then follow a separate retention migration.
