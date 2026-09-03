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

## Screener

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/v2/screener/filters` | Filter defaults, `rankBy` keys, markets. |
| GET | `/v2/screener/presets?market=` | Backend-owned, dated symbol presets. NSE currently provides `nifty_50` and `nifty_top_20`; Crypto returns an empty list. |
| POST | `/v2/screener/runs` | Body `{market, filters?, symbols?, presetId?}`; use either `presetId` or `symbols`, or omit both to screen the market's full catalogue. Returns 202 `{runId, status: RUNNING, …}`; poll. |
| GET | `/v2/screener/runs?market=&limit=` | Recent runs. |
| GET | `/v2/screener/runs/{id}` | Run status, counts, filters. |
| GET | `/v2/screener/runs/{id}/results?passed=` | `results[{symbol, passed, rank, score, rejectionReason, metrics}]`; every symbol is recorded with a pass or a reason (`PRICE_BELOW_MINIMUM`, `LIQUIDITY_BELOW_MINIMUM`, `INSUFFICIENT_CANDLE_COVERAGE`, `CANDLE_DATA_UNAVAILABLE`, `RANKED_OUT_BY_MAXIMUM_SYMBOLS`, …). |
| POST | `/v2/screener/universes` | Body `{runId, name, maximumSymbols?, manualIncludes, manualExcludes, activate}` → 201 saved universe. |
| GET | `/v2/screener/universes?market=` | Saved universes and the active one per market. |
| POST | `/v2/screener/universes/{id}/activate` | Make it the universe consumed by Backtest and Signals. |

Filters (camelCase): `lookbackDays, minimumPrice, maximumPrice, minimumAverageTradedValue, minimumAverageVolume, minimumVolatilityPct, maximumVolatilityPct, minimumCandleCoverage, minimumSessions, rankBy (liquidity|volume|volatility|price|coverage), maximumSymbols (null = keep every passing symbol)`.

## Backtests

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/v2/backtests` | Body `{market, strategyId, symbols?, universePresetId?, timeframe, startDate, endDate, configuration, execution}` → 202 run record; use either `symbols` or `universePresetId`. The server resolves a preset and stores its exact symbol snapshot before the background job starts. |
| GET | `/v2/backtests?market=&limit=` | Recent runs. |
| GET | `/v2/backtests/{id}` | Status (`QUEUED, RUNNING, COMPLETE, FAILED, CANCELLED, INTERRUPTED`), `symbolsCompleted/symbolsTotal`, `currentSymbol`, `failedSymbols`, `metrics`, `configurationSnapshot`, `strategyVersion`. |
| DELETE | `/v2/backtests/{id}` | Durable cancel request; honoured between symbols and every 500 bars. |
| GET | `/v2/backtests/{id}/trades?symbol=&limit=&offset=` | Paged trades, one row per lot. |

`execution`: `targetPct?, stopLossPct?, maximumHoldingBars?, initialQuantity, allowAdditionalBuys, additionalQuantityPct, additionalSizingMode (REDUCE_EVERY_NEW_LOT|FIXED_PERCENTAGE_OF_FIRST_LOT), minimumQuantity, maximumEntriesPerCycle, batchSize`.

`metrics`: `totalSignals, completedTrades, targetHits, stoppedTrades, expiredTrades, openTrades, realizedPnl, unrealizedPnl, fees, slippage, winRate, averageMaePct, averageMfePct, averageHoldingMinutes, medianHoldingMinutes, maximumDrawdown, symbolsProcessed, symbolsFailed`.

## Signals

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/v2/signals?market=&status=&symbol=&limit=` | Stored signals with `status` (`STRONG_BUY, HOLDING, TARGET_HIT, EXITED, EXPIRED`) and `colour` (`blue, orange, green, red, red`). |
| GET | `/v2/signals/health?market=` | `engines` (persisted `engine_status` rows) and `workers` (live status: connection, data age, last completed candle, symbols, counters). |

Signals are unique on `(market, strategyVersion, symbol, timeframe, candleTimestamp, signalType)`; a duplicate is silently rejected by the database.

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

## Legacy endpoints

The pre-existing routes (`/backtest`, `/live-signals`, `/paper-trades`, `/crypto/*`, `/live-universe/*`, `/market-data`, `/platform/overview|instruments|market-context`, …) remain for the `/legacy/*` pages and the production live-signal engine until the v2 workers are switched on.
