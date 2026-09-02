# Live Signal Universe

The Live Signal Universe is a selection layer over the unchanged RSI Recovery
`rsi-recovery-1.1.0` historical observations. It does not calculate new BUY signals,
change strategy parameters, simulate capital, or place broker orders.

## Default configuration

- Top N: 300
- Minimum completed-close price: ₹500, inclusive
- Maximum completed-close price: ₹2,000, inclusive
- Ranking: `QUALITY`
- Minimum historical BUY observations: 50
- Dynamic price filtering: disabled
- Optional quality guards: disabled

The minimum sample default is based on the production distribution: min 17, P10 59,
P25 68, median 77, P75 86, P90 93, max 113. A threshold of 50 is below P10 and
removes only unusually thin histories.

## Selection order

1. Begin with the 750-symbol NSE source universe.
2. Remove symbols absent from the validated 749-symbol historical result or explicitly
   listed as a data-quality failure.
3. Load the current reference price from the existing Dhan market-data cache.
4. If the cache represents an in-progress current session, use `previous_close`.
   Otherwise use the completed session's `entry_price` close. Record 15:30 IST as the
   deterministic completed-candle timestamp.
5. Apply the inclusive minimum and maximum price bounds.
6. Apply the minimum sample threshold and any explicitly enabled quality guards.
7. Rank eligible rows.
8. Take at most Top N.
9. Add valid manual pins outside the rank cutoff.
10. Remove manual exclusions.

Price filtering therefore occurs before Top-N selection. A frozen universe is not
re-evaluated when a symbol later crosses a price boundary.

## Quality ranking

The existing quality score is reused without modification:

`40% target-hit rate + 30% target speed + 20% MAE quality + 10% non-open rate`

For `QUALITY`, deterministic ordering is:

1. quality score descending, at the existing two-decimal production precision;
2. GOOD rate descending;
3. median completed target time ascending;
4. OPEN rate ascending;
5. median completed MAE closest to zero;
6. symbol alphabetically.

Quality is a transparent research ranking, not probability of profit.

## Persistence and freeze semantics

The backtest service stores small JSON/CSV artifacts under
`$LIVE_UNIVERSE_DIR`, defaulting to
`$BACKTEST_CACHE_DIR/live-universe`. Each explicit confirmation creates an immutable
`LIVE-YYYYMMDD-NNN` JSON/CSV version and atomically updates `active.json` and
`live_universe.csv`. Preview and rebuild endpoints never modify the active version.

Manual pins and exclusions are part of each version's configuration. A pin may bypass
only the rank cutoff; it cannot bypass data quality, reference-price, price-range,
sample-size, or enabled quality guards.

## API

- `GET /live-universe/config`
- `POST /live-universe/preview`
- `POST /live-universe/rebuild`
- `POST /live-universe/save`
- `GET /live-universe/active`
- `GET /live-universe/history`
- `GET /live-universe/symbols`
- `GET /live-universe/export`

`GET /live-universe/symbols` is the stable input contract for a future paper-signal
monitor. No live monitoring or Dhan order execution is part of this implementation.
