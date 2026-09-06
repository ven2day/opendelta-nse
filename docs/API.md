# API Reference

All unified-platform endpoints live under `/v2` on the backtest service
(FastAPI, port 8000 in the container, `127.0.0.1:3200` on the host). The web
app proxies them through `/api/v2/<path>` with the same session/proxy-token
authentication as the legacy routes. Every response is JSON, every operation
is paper-only, and every payload that touches a trade carries
`paperOnly: true` / `liveOrdersEnabled: false`.

Errors: `422 {detail}` for validation, `404` for unknown ids, `409` for a
state conflict, and `503 {detail}` when the platform database is not
configured or its schema is behind (`python -m backend.data.migrate`).

`market` is always `NSE` or `CRYPTO`.

## Dashboard

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/v2/dashboard?market=` | One call with `marketData`, `screener` (latest run + active universe), `backtests` (recent), `signalEngine` (stored status + live worker), `paper` (account summary + open positions). Each section is `{available, data, error?}` and degrades independently. |

## Strategies and settings

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/v2/strategies?market=` | Registry catalogue: `strategies[{strategyId,name,version,supportedMarkets,supportedTimeframes,configSchema,defaults}]`, `markets`, `riskDefaults`, `riskSchema`. UI dropdowns and forms are generated from `configSchema` / `riskSchema`. |
| GET | `/v2/strategies/{id}/config?market=` | `active` config (or null), `effectiveConfiguration`, `effectiveRiskSettings`, `all` saved configs for the market. |
| POST | `/v2/strategies/{id}/config` | Body `{market, name, configuration, riskSettings, activate}`. Validated through the strategy's schema and rules; one active config per market and strategy. Returns 201. |

`configSchema` entries are `{type: integer|integer_array|number|boolean|string, default, minimum?, maximum?, enum?, label?}`. Integer arrays also support `minItems` and `maxItems` and are used by finite quantity ladders.

## Indicator Studio V2

Indicator source is stored separately from strategy source. Saving a valid
source creates an immutable `(indicatorId, version)` snapshot; changing code
requires a new semantic version. Source is statically validated in the API and
is executed only by the resource-limited Strategy V2 runner.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/v2/indicator-studio/template` | Safe starter source implementing `calculate(context, data)`. |
| POST | `/v2/indicator-studio/validate` | Body `{sourceCode}`. Parses the source without importing or executing it. |
| POST | `/v2/indicator-studio/sources` | Save a valid immutable version. Returns 201. |
| GET | `/v2/indicator-studio/sources?status=` | List source metadata; optional status is `VALIDATED` or `ARCHIVED`. |
| GET | `/v2/indicator-studio/sources/{sourceId}` | Retrieve one version including its Python source. |
| POST | `/v2/indicator-studio/sources/{sourceId}/archive` | Archive a version. Archived source remains auditable but cannot be previewed. |
| POST | `/v2/indicator-studio/sources/{sourceId}/preview` | Body `{market,symbol,timeframe,params,candles?}`. With no candle columns, load recent stored completed candles; evaluate them in the isolated runner and return values plus the candle rows. |

`INDICATOR` declares `id`, `name`, semantic `version`, parameter defaults,
`requiredHistory`, and one or more outputs. Each output selects a chart
`display` (`LINE`, `HISTOGRAM`, `BAND`, `POINTS`) and `pane` (`OVERLAY`,
`PANEL`). The Phase 5 chart workspace will consume this output metadata.

## Screener

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/v2/screener/filters` | Filter defaults, `rankBy` keys, markets. |
| GET | `/v2/screener/presets?market=` | Backend-owned, dated symbol presets. NSE currently provides `nifty_50` and `nifty_top_20`; Crypto returns an empty list. |
| GET | `/v2/screener/profiles?market=` | Named watchlist rule profiles with immutable versions, newest first within each profile. |
| POST | `/v2/screener/profiles` | Create profile v1: `{market, name, filters, sourceKind: MARKET|PRESET|CUSTOM, presetId?, symbols?}`. |
| POST | `/v2/screener/profiles/{profileId}/versions` | Save the next immutable version. Existing versions and strategy watchlists are unchanged. |
| POST | `/v2/screener/runs` | Body `{market, profileVersionId}` to run an exact saved version, or `{market, filters?, symbols?, presetId?}` for unsaved rules. Returns 202 `{runId, status: RUNNING, …}`; poll. |
| GET | `/v2/screener/runs?market=&limit=` | Recent runs. |
| GET | `/v2/screener/runs/{id}` | Run status, counts, filters. |
| GET | `/v2/screener/runs/{id}/results?passed=` | `results[{symbol, passed, rank, score, rejectionReason, metrics}]`; every symbol is recorded with a pass or a reason (`PRICE_BELOW_MINIMUM`, `LIQUIDITY_BELOW_MINIMUM`, `INSUFFICIENT_CANDLE_COVERAGE`, `CANDLE_DATA_UNAVAILABLE`, `RANKED_OUT_BY_MAXIMUM_SYMBOLS`, …). |
| POST | `/v2/screener/universes` | Body `{runId, name, maximumSymbols?, manualIncludes, manualExcludes, activate}` → 201 saved universe. |
| GET | `/v2/screener/universes?market=` | Saved universes and the active one per market. |
| POST | `/v2/screener/universes/{id}/activate` | Make it the universe consumed by Backtest and Signals. |

A profile version freezes both the validated filter JSON and its starting universe source. A screener run records `profileVersionId`; saving its candidates creates a separate immutable symbol snapshot. Strategy deployments remain pinned to that saved watchlist until manually changed.

Filters (camelCase): `lookbackDays, minimumPrice, maximumPrice, minimumAverageTradedValue, minimumAverageVolume, minimumVolatilityPct, maximumVolatilityPct, minimumCandleCoverage, minimumSessions, rankBy (liquidity|volume|volatility|price|coverage), maximumSymbols (null = keep every passing symbol)`.

## Backtests

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/v2/backtests` | Body `{market, strategyId, symbols?, universePresetId?, timeframe, startDate, endDate, configuration, execution}` → 202 run record; use either `symbols` or `universePresetId`. The server resolves a preset and stores its exact symbol snapshot before the background job starts. |
| GET | `/v2/backtests?market=&limit=` | Recent runs. |
| GET | `/v2/backtests/{id}` | Status (`QUEUED, RUNNING, COMPLETE, FAILED, CANCELLED, INTERRUPTED`), `symbolsCompleted/symbolsTotal`, `currentSymbol`, `failedSymbols`, `metrics`, `configurationSnapshot`, `strategyVersion`. |
| DELETE | `/v2/backtests/{id}` | Durable cancel request; honoured between symbols and every 500 bars. |
| GET | `/v2/backtests/{id}/trades?symbol=&limit=&offset=` | Paged trades, one row per lot. |
| GET | `/v2/backtests/{id}/chart?symbol=&indicatorSourceId=` | Up to 5,000 completed candles for one run symbol, its immutable trade annotations, and optional isolated Indicator V2 output for the chart workspace. |

`execution`: `targetPct?, stopLossPct?, maximumHoldingBars?, initialQuantity, allowAdditionalBuys, additionalQuantityPct, additionalSizingMode (REDUCE_EVERY_NEW_LOT|FIXED_PERCENTAGE_OF_FIRST_LOT), minimumQuantity, maximumEntriesPerCycle, batchSize`.

`metrics`: `totalSignals, completedTrades, targetHits, stoppedTrades, expiredTrades, openTrades, realizedPnl, unrealizedPnl, fees, slippage, winRate, averageMaePct, averageMfePct, averageHoldingMinutes, medianHoldingMinutes, maximumDrawdown, symbolsProcessed, symbolsFailed`.

## Signals

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/v2/signals?market=&status=&symbol=&limit=` | Stored signals with `status` (`STRONG_BUY, HOLDING, TARGET_HIT, EXITED, EXPIRED`) and `colour` (`blue, orange, green, red, red`). |
| GET | `/v2/signals/health?market=` | `engines` (persisted `engine_status` rows) and `workers` (live status: connection, data age, last completed candle, symbols, counters). |

Signals are unique on `(market, strategyVersion, symbol, timeframe, candleTimestamp, signalType)`; a duplicate is silently rejected by the database.

## TradingView integration

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/v2/integrations/tradingview/status?market=&strategy=` | Authenticated UI status; reports whether the server key and selected deployment are ready without returning the key. |
| POST | `/v2/integrations/tradingview/webhook` | Internal FastAPI ingress for the public web proxy. Validates the v1 alert contract and returns 202. |
| POST | `/api/tradingview/webhook` | Public HTTPS URL configured in TradingView. It is body-size limited and forwards to the internal ingress. |

See [TradingView signal integration](tradingview-integration.md) for the alert
contract and production setup.

## Paper trading

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/v2/paper/accounts` | Summaries for both accounts (NSE in INR, CRYPTO in USDT). |
| GET | `/v2/paper/accounts/{market}` | `cashBalance, marketValue, equity, openPositions, closedLots, realizedPnl, realizedPnlToday, unrealizedPnl, dailyPnl, executionPolicy`. |
| POST | `/v2/paper/accounts` | Body `{market, startingBalance?}` → create (or, when no positions are open, re-base) the account. |
| POST | `/v2/paper/accounts/{market}/reset` | Clears orders, lots and trades and restores the balance. |
| GET | `/v2/paper/positions?market=` | Open lots, each with its own entry, quantity, target, stop, expiry and unrealized P&L. |
| GET | `/v2/paper/orders?market=` | Filled and rejected orders (rejection reasons: `INSUFFICIENT_FUNDS`, `MAXIMUM_ENTRIES_PER_CYCLE`, `ADDITIONAL_BUYS_DISABLED`). |
| GET | `/v2/paper/trades?market=` | Executions (BUY on entry, SELL on close) with fees and slippage. |
| GET | `/v2/paper/lots?market=&status=` | Lot history. |
| POST | `/v2/paper/lots/{id}/close?market=` | Body `{price}` — manual close at a price. |

A signal can open at most one filled paper order per account (database unique index). There is no order-placement client anywhere in the codebase.

## Operational (non-v2) routes

Served directly by the FastAPI service (the web app does not proxy these; they
back the shared chrome and operations tooling):

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/health` | Liveness + symbol count, uptime, database reachability, candle read mode. |
| GET | `/platform/overview` | Chrome data-freshness pill: environment, freshness, worker status, `paperOnly: true`. |
| GET | `/platform/instruments?market=` | Instrument master rows (NSE managed registry, crypto catalogue). |
| GET | `/platform/market-context?market=` | Session state per market; breadth data is reported as unsupported. |
| GET/PUT | `/application-settings` | Global current-price range bounding screener/backtest universes. |
| GET | `/market-data/symbols` | Managed NSE symbol registry with the price filter applied. |
| GET | `/market-data/status` | Snapshot refresh service state. |
| GET | `/market-data/csv` | Download the latest RSI/volume snapshot CSV. |
| POST | `/market-data/refresh` | Start a snapshot refresh run. |
| POST | `/market-data/symbols` | Add a Dhan-validated NSE symbol and refresh. |
| GET | `/nifty-oi/history/status` | NIFTY OI import coverage. |
| GET | `/crypto/*` | Public-exchange crypto catalogue and completed-candle data (OKX/VALR). |

The retired research endpoints (`/backtest`, `/backtest-history`,
`/live-universe/*`, `/live-signals*`, `/paper-trades`, `/recovery-analysis*`)
were removed together with their engines; see `docs/adr/0005-legacy-retirement.md`.
