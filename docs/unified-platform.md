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
`(market, strategy_version, symbol, timeframe, candle_timestamp, signal_type)`;
`paper_orders` allows one filled BUY per signal per account; `backtest_trades`
is unique per `(run_id, lot_id)`.

## Runtime flags (all default off / safe)

| Variable | Effect |
|---|---|
| `MARKET_DATA_DATABASE_URL` | PostgreSQL URL; without it every `/v2/*` route answers 503 |
| `PLATFORM_AUTO_MIGRATE=true` | apply migrations at startup (otherwise explicit) |
| `NSE_SIGNAL_ENGINE_V2_ENABLED=true` | start the NSE live-signal worker (session-aware Dhan polling) |
| `CRYPTO_SIGNAL_ENGINE_V2_ENABLED=true` | start the Crypto live-signal worker (24/7) |
| `NSE_PAPER_TRADING_V2_ENABLED` / `CRYPTO_PAPER_TRADING_V2_ENABLED` | paper broker per market (default true when the worker runs) |
| `NSE_LIVE_STRATEGY` / `CRYPTO_LIVE_STRATEGY` | strategy id for live signals (default `ema_vwap_strong_buy`) |
| `NSE_SIGNAL_POLL_SECONDS` / `CRYPTO_SIGNAL_POLL_SECONDS` | poll cadence (120 / 60) |

The legacy NSE live-signal engine and legacy pages keep running unchanged
until the v2 workers are switched on and the legacy routes are retired.

## API (all JSON, all paper-only)

- `GET /v2/dashboard?market=` — everything the Dashboard shows, per section
- `GET /v2/strategies?market=`, `GET|POST /v2/strategies/{id}/config`
- `GET /v2/screener/filters`, `POST /v2/screener/runs`, `GET /v2/screener/runs[/{id}[/results]]`,
  `POST /v2/screener/universes`, `GET /v2/screener/universes`, `POST /v2/screener/universes/{id}/activate`
- `POST|GET /v2/backtests`, `GET|DELETE /v2/backtests/{id}`, `GET /v2/backtests/{id}/trades`
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
