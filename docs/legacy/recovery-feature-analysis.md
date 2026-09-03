# RSI Recovery entry-feature analysis

## Scope

This is a descriptive research layer over the unchanged `rsi-recovery-1.1.0`
engine. The engine still owns RSI arming/recovery, confirmation decisions,
overlapping observations, target monitoring, and MAE/MFE. The feature layer
does not generate or suppress signals.

## Data boundary

For every engine trade, `signalTimestamp` selects the snapshot candle. This is
the closed recovery candle for both execution models; under `NEXT_BAR_OPEN`, it
is deliberately earlier than `entryTimestamp`. Vectorized rolling calculations
are backward-looking or current-bar-inclusive. The RSI arm path ends at the
signal row. NIFTY rows are backward-as-of aligned within the same NSE session.

The durable table separates columns by namespace:

- `feature_*`: observable at or before the BUY signal close.
- `outcome_*`: target result, duration, MAE/MFE, holding period, or end-of-data
  state calculated after the snapshot is frozen.

Outcome columns are never returned by `input_feature_columns()`.

## Outcome definitions

The five-way classes are exclusive: `FAST_30M` (up to 30 minutes), `FAST_2H`
(over 30 through 120 minutes), `SAME_DAY` (over 120 through 1,440 minutes),
`SLOW` (over 1,440 minutes), and `TRAPPED` (OPEN at the dataset end).

For the two-group comparison, `GOOD` is up to 120 minutes, `BAD` is over 1,440
minutes or OPEN, and the 121–1,440 minute group is `NEUTRAL` and excluded.

## Statistics

Numeric features receive per-class distribution summaries and a GOOD/BAD
Cliff's delta. Rankings are called *univariate separation strength*; they are
not predictive importance and do not imply causality. Ten predeclared features
receive quintile reports, and two predeclared pairs receive focused 2D
matrices. Confirmation, time-of-day, symbol, trapped-observation, and worst-tail
reports are produced separately.

Exploratory candidate diagnostics use only a predeclared upper or lower 40%
tail based on the sign of Cliff's delta. They report retained observations and
before/after GOOD, BAD, and OPEN rates. They are not installed as strategy
filters.

## Reproducibility and caching

The cache key contains the baseline run ID, strategy version, feature schema,
configuration hash, and data range. Each symbol is an atomic Parquet partition,
so an interrupted universe run can resume without returning a partition from a
different configuration. Final CSV, Parquet, and JSON reports are also written
atomically.

The production runner refuses to run if `backend/compat/recovery_backtest.py` differs from the
v1.1.0 source hash, and the exact baseline run fails unless it reconciles to
57,510 BUY observations, 56,088 targets hit, and 1,422 OPEN observations.

## Known data constraints

NIFTY context uses the existing Dhan/cache infrastructure. Sector features are
not generated because the project has no reliable sector mapping. Existing Dhan
equity cache files do not record an explicit adjusted-for-corporate-actions
flag, so splits and bonuses may affect historical feature values.
