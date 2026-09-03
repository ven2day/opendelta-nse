# RSI Recovery Overlapping-Observation Change Report

## Scope and architecture

This release modifies only the existing RSI Recovery Scalping strategy. The RSI Range strategy, indicator formulae, confirmations, target/expiry rules, Dhan retrieval, candle cache, NSE universe, authentication, API proxy, styling, and deployment topology are unchanged.

The former single `active_position` state is now a per-symbol `active_trades` list. Every accepted fresh RSI arm -> recovery cycle creates a uniquely identified independent observation. On each later candle, the engine scans only this currently open list, updates each observation's MAE/MFE and target independently, removes only observations whose own target hit, and retains every unresolved observation at dataset end. Completed historical observations are not rescanned.

The RSI state machine is unchanged: enter the configured arm zone, remain armed even below the arm low, require a causal upward recovery crossover plus the enabled confirmation threshold, create exactly one observation, then reset. An existing OPEN observation no longer gates this lifecycle. `SIGNAL_CLOSE` and `NEXT_BAR_OPEN` both use the same independent-observation model, and entry-candle high/low remain excluded from target and excursion tracking.

## Files changed

- `backend/compat/recovery_backtest.py`: overlapping active list, unique trade identity/sequence, isolated lifecycle state, and symbol/universe concurrency metrics.
- `backend/app.py`: recovery-only compatibility terminology and strategy version `rsi-recovery-1.1.0`.
- `tests/test_recovery_backtest.py` and `tests/fixtures/pine_recovery_expected.json`: replacement overlap lifecycle and deterministic Pine-parity coverage.
- `benchmarks/recovery_benchmark.py` and `benchmarks/run_live_recovery_universe.py`: overlap throughput and old/new production reporting.
- `web/app/backtest/backtest-dashboard.tsx`, `web/app/backtest/recovery-results.tsx`, and `web/app/globals.css`: native OpenDelta controls, cards, concurrency columns, independent trade detail, and signal-observation wording.
- `web/tests/rendered-html.test.mjs`: frontend regression assertions.
- `docs/legacy/backtest-architecture.md`, `docs/legacy/recovery-benchmark.md`, and this report.
- `benchmarks/opendelta-rsi-recovery-overlap-baseline.json`: cache-warm production baseline.
- `benchmarks/opendelta-rsi-recovery-overlap-baseline-cold.json`: matching cache-cold production baseline.

## Verification

- Python: 59 tests passed. Coverage includes fresh arm/recovery requirements, one recovery/one BUY, overlapping `SIGNAL_CLOSE` and `NEXT_BAR_OPEN`, independent completion, isolated MAE/MFE, multiple unresolved observations, entry-candle exclusion, future-candle signal invariance, aggregation, and unchanged RSI Range regressions.
- Pine/Python deterministic fixture: matching EMA9, EMA20, Wilder RSI, daily-reset VWAP, volume EMA20, arm/recovery timestamps, two BUY timestamps/prices/targets, second-target completion while the first remains OPEN, and isolated MAE/MFE.
- Frontend: ESLint passed, production build passed, and 2 authenticated rendered integration tests passed.
- Deployed smoke: authenticated page -> frontend proxy -> backtest API returned RSI Recovery version 1.1.0; unchanged RSI Range version 1.0.0 also returned successfully.
- Real 5-minute LUPIN smoke: 18,411 bars, 58 unique observations, 56 targets hit, 2 open, and maximum same-symbol concurrency of 3.

## Deterministic engine benchmark

Four workers, 18,900 five-minute candles per symbol:

| Symbols | Rows | Old runtime | New runtime | Change |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 18,900 | 1.1495 s | 1.3563 s | +18.0% |
| 100 | 1,890,000 | 7.4453 s | 11.9099 s | +60.0% |
| 300 | 5,670,000 | 15.9616 s | 29.8397 s | +87.0% |
| 750 | 14,175,000 | 35.9232 s | 70.7006 s | +96.8% |

The 750-symbol fixture produced 62,250 observations and a maximum same-symbol overlap of 6. Peak process memory was not available from the Windows benchmark runner.

## Production Phase 1 baseline

- Data: 26 August 2025 09:20 IST through 25 August 2026 15:30 IST.
- 5-minute candles: 13,697,897.
- Symbols: 750 requested; 749 processed; IDEA rejected because its source data contains negative volume.
- Exact settings: RSI 14, arm 30-40, recovery 40, EMA 9/20, session VWAP, volume EMA20, 2-of-3 confirmations, +0.5% target, 50-bar expiry, `SIGNAL_CLOSE`, zero cost/slippage.
- BUY observations: 57,510.
- Targets hit: 56,088 (historical target achievement rate 97.53%).
- OPEN observations: 1,422 across 524 symbols; 339 symbols have at least 2 open and 82 have at least 5 open.
- Maximum concurrent observations: 2,129 universe-wide and 16 for one symbol.
- Median target time: 115 minutes; average 5,870.78 minutes; median 11 bars; average 201.43 bars.
- Target speed: 20,125 (35.88%) <=30m; 8,244 (14.70%) >30m-2h; 12,446 (22.19%) >2h-24h; 15,273 (27.23%) >24h.
- Completed MAE: average -1.8125%, median -0.5494%, worst -52.9274%.
- Completed MFE: average 0.9269%, median 0.7011%.
- OPEN state: average age 167,955.32 minutes; median age 72,277.5 minutes; oldest 524,515 minutes (CMSINFO); average P&L -12.7753%; worst P&L -59.0859%; average MAE -18.6783%; worst MAE -69.0980%.
- Runtime: 1,191.72 seconds cache-warm and 1,972.40 seconds cache-cold. Both runs produced identical summaries.

## Old versus new

| Metric | Old gated engine | New overlap engine | Change |
| --- | ---: | ---: | ---: |
| BUY observations | 21,890 | 57,510 | +35,620 (+162.72%) |
| Targets hit | 21,372 | 56,088 | +34,716 (+162.44%) |
| Historical hit rate | 97.63% | 97.53% | -0.10 pp |
| OPEN observations | 518 | 1,422 | +904 (+174.52%) |
| Median target time | 100 min | 115 min | +15 min |
| Average completed MAE | -1.7514% | -1.8125% | -0.0611 pp |
| Median completed MAE | -0.5555% | -0.5494% | +0.0061 pp |
| Worst completed MAE | -52.9274% | -52.9274% | unchanged |
| Maximum universe concurrency | 634 | 2,129 | +1,495 |
| Maximum same-symbol concurrency | 1 (structural) | 16 | +15 |
| Cache-warm runtime | 690.32 s | 1,191.72 s | +501.40 s (+72.64%) |

The old gate suppressed 35,620 valid fresh-cycle BUY observations over this dataset. Of the newly included observations, the aggregate target-hit count increased by 34,716 and unresolved observations increased by 904. These deltas are aggregate old/new differences; they do not represent portfolio orders or incremental capital deployment.

## Ranking snapshot

Top 20 by the unchanged transparent quality formula: ATHERENERG, PFOCUS, ATLANTAELE, APOLLO, REFEX, DATAPATTNS, LLOYDSENT, HFCL, MRPL, JINDALSAW, AVALON, UTLSOLAR, JSFB, GRAPHITE, KIRLOSBROS, SCI, HSCL, LENSKART, ENDURANCE, TDPOWERSYS.

Bottom 20: DABUR, ITC, MRF, INFY, TATACHEM, INDIGO, COALINDIA, ULTRACEMCO, IRFC, HDFCBANK, HINDUNILVR, PATANJALI, TMPV, GILLETTE, UTIAMC, WIPRO, HGINFRA, IRCTC, ACC, HDFCLIFE.

## Limitations and interpretation

- This is a signal-quality backtest, not a portfolio-capital simulator. Concurrency describes simultaneous observations, not funded positions.
- Historical target achievement is not actual live profitability. `SIGNAL_CLOSE`, liquidity, fillability, and the configured zero-cost assumptions materially affect interpretation.
- Dhan corporate-action adjustment status remains unverified; source candles are used as received.
- IDEA remains excluded rather than silently accepting invalid negative volume.
- A target-hit timestamp is the matching candle's close timestamp; exact intra-candle execution time is unknown.
- The quality score remains a transparent research ranking, not a probability or guarantee.

Recommended next research step: use the recorded concurrency distribution to define a separate, explicit capital-allocation model without changing or suppressing this signal-observation baseline.
