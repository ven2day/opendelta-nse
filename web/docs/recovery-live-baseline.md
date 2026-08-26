# RSI Recovery Scalping: live Phase 1 baseline

- Run date: 25 August 2026
- Run ID: `baseline-5m-1y-2f9f49b8-0f6c-40bb-ad01-80f5aa4ac3e5`
- Strategy version: `rsi-recovery-1.0.0`

## Configuration

- Universe: all 750 symbols from the deployed `symbols.csv`
- Timeframe and duration: 5 minutes, 1 year
- Data: Dhan historical NSE equity candles; analysis spans 26 August 2025 09:20 IST through 25 August 2026 15:30 IST, with earlier candles used only for indicator warm-up
- RSI: Wilder RSI 14, arm at 30–40, recover above 40
- Confirmations: EMA 9/20, daily session VWAP, and volume EMA 20; require 2 of 3
- Target and expiry: +0.50%, 50 bars
- Execution: `SIGNAL_CLOSE`
- Costs: zero buy cost, sell cost, and slippage for the baseline
- Position lifecycle: no stop loss, no end-of-day exit, one active position per symbol, hold until target or dataset end

## Universe result

| Metric | Result |
| --- | ---: |
| Symbols requested | 750 |
| Symbols processed | 749 |
| Symbols failed | 1 (`IDEA`: negative volume rejected by data-quality validation) |
| Candle rows processed | 13,697,897 |
| First run including Dhan retrieval | 1,440.92 seconds |
| Canonical cache-backed replay | 690.32 seconds |
| BUY signals | 21,890 |
| Targets hit | 21,372 |
| Historical target-hit rate | 97.63% |
| Still open | 518 |
| Maximum concurrent positions | 634 |

## Target speed

The buckets below are mutually exclusive and use calendar time.

| Target time | Count | Completed targets |
| --- | ---: | ---: |
| <= 30 minutes | 8,035 | 37.60% |
| >30 minutes and <=2 hours | 3,078 | 14.40% |
| >2 hours and <=24 hours | 4,503 | 21.07% |
| >24 hours | 5,756 | 26.93% |

- Average target time: 5,914.65 minutes (98.58 hours)
- Median target time: 100 minutes
- Average bars to target: 202.89
- Median bars to target: 10
- Same trading session: 60.59%
- Next trading session: 21.52%
- Two to five trading days: 10.20%
- More than five trading days: 7.69%

## Excursion and trapped-capital result

| Metric | Result |
| --- | ---: |
| Average completed MAE | -1.7514% |
| Median completed MAE | -0.5555% |
| Worst completed MAE | -52.9274% |
| Average completed MFE | +0.9146% |
| Median completed MFE | +0.7033% |
| Average open age | 140.57 calendar days |
| Median open age | 90.67 calendar days |
| Oldest open position | 364.25 calendar days |
| Oldest open symbol | CMSINFO |
| Average open P&L | -14.7557% |
| Worst open P&L | -59.0859% |
| Average open MAE | -21.6456% |
| Worst open MAE | -69.0980% |

The 97.63% target-hit rate is historical target achievement under a signal-close, zero-cost assumption. It is not evidence of live profitability. The unresolved positions and severe adverse-excursion tail are material results, not incidental outliers.

## Quality ranking

The displayed score is a transparent research ranking:

```text
quality = 40% hit-rate score
        + 30% speed score
        + 20% MAE score
        + 10% (100 - open-position rate)
```

Speed gives bucket scores of 100, 75, 40, and 10 from fastest to slowest. MAE score is `clamp(100 + median_completed_MAE * 10, 0, 100)`. The score is not a probability, AI confidence, or guarantee.

Top 20: ATHERENERG (94.38), INDIAGLYCO (92.79), HSCL (92.76), ASKAUTOLTD (92.67), PFOCUS (92.63), ATLANTAELE (92.55), APOLLO (91.91), GABRIEL (91.91), GRAPHITE (91.87), UTLSOLAR (91.74), BALAMINES (91.55), RBA (91.34), LLOYDSENT (91.25), DATAPATTNS (91.20), REDINGTON (91.18), KIRLOSBROS (91.13), TI (91.09), JSFB (91.05), REFEX (91.04), MIDHANI (90.93).

Bottom 20: SHREECEM (0.00), CMSINFO (0.00), GILLETTE (38.99), AURIONPRO (42.91), BLS (43.55), SUMICHEM (44.65), BAJAJHFL (45.79), SUZLON (49.29), FSL (50.85), ZAGGLE (52.34), RCF (55.33), INDIGO (56.39), TMPV (56.47), DABUR (56.76), EMAMILTD (57.36), GMRP&UI (57.46), IRFC (57.93), POWERMECH (59.29), TARIL (59.80), BLUEJET (61.21).

## Interpretation and limitations

- The default `SIGNAL_CLOSE` entry is a TradingView-parity research reference. It assumes execution at the just-closed candle's close; `NEXT_BAR_OPEN` is the live-realistic alternative.
- Transaction costs and slippage are zero in this baseline. Gross target achievement must not be treated as net return.
- Dhan corporate-action adjustment status is not explicit and has not been independently verified.
- The current universe introduces survivorship bias because delisted securities are absent.
- One failed symbol was not silently accepted: `IDEA` had negative volume in the source data.
- The next research run should use `NEXT_BAR_OPEN`, realistic cost/slippage inputs, an audited adjusted dataset, and a held-out out-of-sample period. Parameter experiments should follow only after those controls are in place.

The machine-readable aggregate is stored at `benchmarks/opendelta-rsi-recovery-baseline.json`.
