# Extending OpenDelta safely

## Add a market provider

1. Implement catalogue and completed-candle protocols outside strategy code.
2. Define exact symbol normalization; never alias an unsupported broker instrument.
3. Register markets, timeframes, data types, timezone, public/private boundary, and rate limits.
4. Normalize UTC timestamps, preserve provenance, reject incomplete/invalid candles, and upsert idempotently.
5. Return typed unsupported results; do not synthesize spreads, volume, sectors, OI, or order books.
6. Test catalogue parsing, exact resolution, rate limits, deduplication, incomplete candles, and failures.
7. Add health and environment documentation; keep credentials server-side and redacted.

Provider additions must not modify factor/strategy calculations.

## Add a factor

1. Choose one family, stable ID, and semantic version.
2. Add description, measure, use/avoid guidance, and common misunderstanding.
3. Declare data, markets/timeframes, bounded parameters, warm-up, and missing-data behavior.
4. Implement a vectorized point-in-time calculation without future or incomplete bars.
5. Include all benchmark/sector/provider/data dependencies in the cache key.
6. Test known values, bounds, warm-up, missing inputs, MTF alignment, invalidation, and educational claims.
7. Surface it in Research Lab through the registry.

Semantic changes require a new version; never silently alter history.

## Add a strategy

1. Register key/name/version, market, lifecycle, directions, timeframes, and execution model.
2. Define validated parameters and factor compatibility.
3. Use closed context and next-bar-open execution.
4. Declare collision, cost, slippage, limits, expiry, and rejection reasons.
5. Persist configuration, strategy/factor/data versions, and configuration ID.
6. Add fixtures, no-lookahead, cost/collision, compatibility, and sample-size tests.
7. Start `RESEARCH_ONLY`; activation/retirement is a reviewed change.

No V1 strategy may enable live broker execution.
