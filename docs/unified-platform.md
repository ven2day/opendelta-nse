# Unified trading platform (NSE + Crypto)

One application, four capabilities — Screener, Backtest, Live Signals, Paper
Trading — for two markets, driven by a single strategy evaluator. Paper
trading only: there is no broker or exchange order client anywhere in the
codebase, and tests assert that.

## Layout

```
backend/
  core/         models.py (Market, MarketContext, SignalDecision, normalize_candles)
                indicators.py (the one indicator library)
  strategies/   base.py (Strategy interface), registry.py (STRATEGIES), strong_buy_v1.py
  markets/      base.py (MarketSpec, FeeModel, CandleSource); nse/ and crypto/ fees, sessions, adapters
  backtest/     engine.py, metrics.py, result_writer.py, jobs.py
  signals/      candle_processor.py, engine.py, recovery.py, workers.py
  paper_trading/ broker.py, portfolio.py, execution.py, accounting.py
  screener/     engine.py, filters.py, ranking.py
  data/         database.py, repositories.py, sql/001_platform.sql, migrate.py
  api/          screener_routes.py, backtest_routes.py, signal_routes.py,
                paper_trading_routes.py, settings_routes.py, dashboard_routes.py
  platform_runtime.py   wires everything into the FastAPI app (backend/app.py)
```

## The one strategy evaluator

`backend/strategies/base.py` defines the `Strategy` interface:

```python
class Strategy(Protocol):
    strategy_id: str
    name: str
    version: str
    supported_markets: tuple[Market, ...]
    supported_timeframes: tuple[str, ...]
    config_schema: ConfigSchema

    def required_history(self, config) -> int: ...
    def validate_config(self, config) -> None: ...
    def evaluate(self, candles, market_context, config) -> SignalDecision: ...
```

`evaluate` sees only completed candles (rows flagged incomplete are dropped by
`normalize_candles`, and the live feed additionally drops any candle whose
close time is in the future) and decides BUY / SELL / NONE for the last one.
The signal is confirmed at that candle's close; every engine enters at the
next candle's open. A strategy may optionally provide a vectorised
`decision_frame` (Strong Buy does) — the backtest engine falls back to
evaluating every history prefix otherwise, which is always correct.

The same evaluator is used by the NSE and Crypto backtest, live signals and
paper trading. `tests/test_strategy_engine.py` proves backtest and live
evaluation agree bar for bar, that evaluation is causal, and that the Strong
Buy indicator table is byte-identical to the pre-refactor implementation.

### Adding a strategy

1. Create `backend/strategies/<name>_v1.py` implementing the interface above
   (no database access, Dhan/exchange calls, WebSockets, backtest loops or
   accounting inside it).
2. Declare `config_schema` (`{field: {type, default, minimum?, maximum?, enum?, label?}}`)
   and `supported_markets` / `supported_timeframes`.
3. Register it in `backend/strategies/__init__.py`: `STRATEGIES.register(YourStrategy())`.
4. Add `tests/test_<name>.py`.

Screener, Backtest, Signals, Paper Trading, the APIs and the frontend
dropdown/settings forms discover it through `STRATEGIES`; there are no
strategy-name `if/elif` chains (`tests/test_strategy_engine.py` guards this).
Every backtest run, signal and paper lot stores `strategy_id`,
`strategy_version` and the immutable `configuration_snapshot`.

`RSI_DIP_LADDER_V1` also publishes a standard price-band lot policy in that
snapshot. Backtest and PaperBroker both use `backend/strategies/lot_policy.py`
to freeze the band from the first fill. The initial lot requires the strategy's
RSI-recovery signal; while that cycle remains open, each completed-candle close
at least the configured percentage below the previous fill schedules the next
lot at the following candle open without requiring another RSI signal. The
engines enforce the finite quantity array and capital ceiling. Each tranche
retains its sell quantity while the executable target changes with FIFO cost.

For an NSE `1d` backtest, RSI is still evaluated only after each completed
daily candle. Execution is replayed separately from stored completed `5m`
candles: the first lot fills at the next session's 09:15 candle open, dip lots
fill at the next 5-minute open, and target/stop timestamps identify the exact
5-minute candle that crossed the executable price. This is the same two-clock
model used by live signals and PaperBroker. The run snapshot records
`executionTimeframe: 5m`; older saved daily runs must be rerun to gain exact
execution timestamps.

For NSE/CNC, those targets are sell instructions rather than broker-selectable
inventory lots. Dhan accepts a symbol and quantity, then matches the sale to the
oldest available shares using FIFO. Before every NSE profit exit, Backtest and
Paper Trading preview those shares and calculate the market price needed to
retain the configured `target_pct` after allocated buy fees, sell fees and
adverse slippage. A completed candle below that executable FIFO target cannot
close the tranche. Only one profit exit is allowed per completed candle; after
it executes, the remaining FIFO cost and targets are recalculated.

## Database

The platform tables live in the existing PostgreSQL/TimescaleDB instance
(`MARKET_DATA_DATABASE_URL`); candles stay in the TimescaleDB hypertable.
Migrations are versioned, idempotent SQL files under `backend/data/sql/`
recorded in `schema_migrations`.

```
python -m backend.data.migrate --check   # report pending migrations
python -m backend.data.migrate           # apply them
```

The service never migrates implicitly. If the schema is behind, the v2
routes answer 503 with the reason until an operator runs the command (or sets
`PLATFORM_AUTO_MIGRATE=true` deliberately).

Duplicate protection: `live_signals` is unique on
`(market, strategy_id, strategy_version, symbol, timeframe, candle_timestamp, signal_type)`;
`paper_orders` allows one filled BUY per signal per account; `backtest_trades`
is unique per `(run_id, lot_id)`.

Research Lab parameter experiments are durable groups of immutable backtests.
Walk-forward validations build on those candidate groups: the database stores
the exact market-aware fold boundaries, training run graph, frozen winner, and
unseen test run. A single bounded coordinator observes the existing bounded
backtest pool; it does not create a worker or engine per variant. Preview is
read-only, submission revalidates a deterministic hash, and all related initial
rows are inserted in one transaction.

The comparison workspace reads those same immutable runs. It shows performance,
cost, outcome, holding, exposure, and operational-failure metrics without
hiding non-terminal variants. Walk-forward training and unseen-test rows are
kept visually and semantically separate. Equity curves are cumulative sums of
recorded closed-trade P&L and remain user-selectable with stable colours.

The optional AI Research Copilot sits outside execution and governance. Its
provider-neutral backend resolves only user-selected immutable records, enforces
size/timeout/rate limits, and writes metadata-only request audits. Provider
responses stay ephemeral unless the user explicitly saves the exact response as
a research draft. Drafts still pass through the existing V2 validation and
immutable-version workflow; the Copilot cannot approve or deploy them.

Private OKX and VALR connections are isolated from public market-data adapters
and governance. Each credential document is encrypted by a random per-record
DEK; AES-256-GCM wraps that DEK with a versioned deployment master key.
Read-only provider testers retain normalized permission booleans only. Dhan
remains deployment-managed through the existing collector.

## Runtime flags (all default off / safe)

| Variable | Effect |
|---|---|
| `MARKET_DATA_DATABASE_URL` | PostgreSQL URL; without it every `/v2/*` route answers 503 |
| `PLATFORM_CANDLE_READ_MODE` | shared engine source: `legacy` (default), `timescale-fallback`, or strict `timescale` |
| `SCREENER_CANDLE_BATCH_SIZE` | symbols read per TimescaleDB screener batch (default `50`, allowed `1`–`250`) |
| `PLATFORM_AUTO_MIGRATE=true` | apply migrations at startup (otherwise explicit) |
| `NSE_SIGNAL_ENGINE_V2_ENABLED=true` | start all configured NSE live-signal workers (session-aware polling) |
| `CRYPTO_SIGNAL_ENGINE_V2_ENABLED=true` | start all configured Crypto live-signal workers (24/7) |
| `NSE_PAPER_TRADING_V2_ENABLED` / `CRYPTO_PAPER_TRADING_V2_ENABLED` | paper broker per market (default true when the worker runs) |
| `NSE_LIVE_STRATEGIES` / `CRYPTO_LIVE_STRATEGIES` | JSON array of `{strategyId,timeframe}` bindings; NSE defaults to `rsi_dip_ladder_v1` on `1d` |
| `NSE_LIVE_STRATEGY` / `NSE_LIVE_TIMEFRAME` | backwards-compatible single binding, used only if the plural setting is absent |
| `NSE_SIGNAL_POLL_SECONDS` / `CRYPTO_SIGNAL_POLL_SECONDS` | poll cadence (120 / 60) |
| `WALK_FORWARD_QUEUE_LIMIT`, `WALK_FORWARD_POLL_SECONDS` | bounded validation coordinators and durable-run polling cadence |
| `EXCHANGE_CREDENTIAL_MASTER_KEY`, `EXCHANGE_CREDENTIAL_MASTER_KEY_VERSION` | backend-only envelope-encryption key and version; connection mutation fails closed if absent |
| `EXCHANGE_CREDENTIAL_PREVIOUS_KEYS` | temporary previous-version keyring for controlled re-encryption |
| `LIVE_TRADING_ENABLED` | global order/cancel mutation gate; default `false` |
| `LIVE_TRADING_DEPLOYMENT_ALLOWED` | independent deployment-level authority gate; default `false` |
| `DEPLOYMENT_ENVIRONMENT`, `LIVE_TRADING_ALLOWED_ENVIRONMENTS` | exact environment identity and allowlist; empty means blocked |
| `LIVE_CONNECTION_MAX_AGE_SECONDS` | freshness limit for private permission tests (default `900`) |

## Live execution boundary

`backend/live` is a separate, fail-closed boundary around the Dhan, OKX and
VALR order APIs. It does not replace the paper broker or signal engine. The
service resolves an exact persisted signal and Paper approval, evaluates
deployment and risk gates, commits an idempotent intent, and only then obtains a
credential-backed adapter. Provider uncertainty enters reconciliation instead
of being treated as a failed order. Emergency stops and the explicit state
machine are persisted by migration `021_live_execution_foundation`.

Example with the daily swing strategy plus a future scalping strategy:

```dotenv
NSE_LIVE_STRATEGIES=[{"strategyId":"rsi_dip_ladder_v1","timeframe":"1d"},{"strategyId":"scalping_v1","timeframe":"5m","enabled":false}]
```

Each binding has an independent worker, completed-candle history, health row,
deduplication identity and paper-lot grouping. Enable the second entry only
after `scalping_v1` is registered.

Daily strategies use two clocks. The strategy evaluates the completed `1d`
candle once after the NSE close. Its paper instruction is stored durably and
defaults to `NEXT_OPEN`; an independent completed `5m` feed fills it from the
next executable session bar and then updates open-signal state, unrealized P/L,
dip-ladder entries, FIFO targets and exits throughout the session. A restart
cannot replay a candle whose open predates creation of the pending instruction.

`rsi_dip_ladder_v1` also exposes `4h` for NSE backtests. It is intentionally
rejected as a live binding until the shortened 13:15–15:30 closing bar is
aggregated and completed with exchange-session semantics.

The legacy NSE live-signal engine and legacy pages keep running unchanged
until the v2 workers are switched on and the legacy routes are retired.

## API (all JSON, all paper-only)

- `GET /v2/dashboard?market=` — everything the Dashboard shows, per section
- `GET /v2/strategies?market=`, `GET|POST /v2/strategies/{id}/config`
- `GET /v2/screener/filters`, `GET /v2/screener/presets`, `POST /v2/screener/runs`, `GET /v2/screener/runs[/{id}[/results]]`,
  `POST /v2/screener/universes`, `GET /v2/screener/universes`, `POST /v2/screener/universes/{id}/activate`
- `POST|GET /v2/backtests`, `GET|DELETE /v2/backtests/{id}`, `GET /v2/backtests/{id}/trades`
- `POST /v2/research/walk-forward/preview`, `POST /v2/research/walk-forward/from-preview`, `GET|DELETE /v2/research/walk-forward[/{id}]`
- `GET /v2/signals`, `GET /v2/signals/health`
- `GET /v2/paper/accounts[/{market}]`, `POST /v2/paper/accounts[/{market}/reset]`,
  `GET /v2/paper/positions|orders|trades|lots`, `POST /v2/paper/lots/{id}/close`

Long backtests and screener runs execute as background jobs; the HTTP request
returns 202 with an id to poll. Run state is durable: runs left QUEUED or
RUNNING by a previous process are marked INTERRUPTED on startup.

## Tests

`PYTHONPATH=. pytest -q` runs everything; set
`TEST_DATABASE_URL=postgresql://…` to include the PostgreSQL repository,
migration, uniqueness and end-to-end tests (CI provides a TimescaleDB service).
