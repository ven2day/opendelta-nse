# NSE Signal Engine V2

## Boundary

V2 is long-only, completed-candle and paper-only. It does not modify the
existing RSI Recovery workspace and contains no broker-order path. Every
eligible symbol is evaluated for both registered setups. Sorting is display
order only; it cannot suppress a valid signal.

## Setups

- `nse_trend_pullback_continuation_v2`: supportive NIFTY and breadth, positive
  relative strength, rising EMA/VWAP trend, controlled pullback to VWAP/EMA9/
  EMA20, bullish completed-candle confirmation, RSI 45-65 and RVOL at least
  1.20.
- `nse_breakout_retest_v2`: completed opening range, structural breakout with
  RVOL at least 1.50, retest within three bars, no-chase distance, VWAP/EMA
  support and positive NIFTY-relative strength.

Both use a buy-stop entry above the trigger high. The entry is valid only
inside the trigger-high to trigger-high-plus-0.10-ATR range and expires after
two bars. The structural stop includes a 0.05-ATR buffer and must be between
0.35% and 1.00% from entry. The initial target is 1.50R and known resistance
must leave at least that much room.

SELL conditions are frozen into each signal: target, hard stop, setup
invalidation, six-bar stagnation, twelve-bar maximum holding time and 15:15 IST
session exit. Historical collision handling must remain stop-first.

## Evidence qualification

The scanner optionally reads `NSE_SIGNAL_EVIDENCE_PATH`; otherwise it reads
`BACKTEST_CACHE_DIR/nse-signal-v2/evidence.json`. Missing or malformed evidence
fails closed as `UNVALIDATED`. A strategy becomes `QUALIFIED` only when the
evidence record is `WALK_FORWARD_VALIDATED` and all gates pass:

- at least 200 out-of-sample completed trades;
- at least 50 distinct symbols;
- positive net expectancy in R;
- profit factor at least 1.20;
- positive lower confidence bound for net expectancy;
- positive expectancy under the stress-cost scenario.

The JSON uses snake-case fields because it is a backend research artifact:

```json
{
  "strategies": {
    "nse_trend_pullback_continuation_v2": {
      "status": "WALK_FORWARD_VALIDATED",
      "sample_size": 500,
      "distinct_symbols": 100,
      "target_hit_probability": 57.0,
      "stop_hit_probability": 35.0,
      "timeout_probability": 8.0,
      "expected_net_return_pct": 0.18,
      "expected_net_r": 0.22,
      "profit_factor": 1.35,
      "confidence_lower_net_r": 0.03,
      "confidence_upper_net_r": 0.41,
      "stress_expected_net_r": 0.10,
      "maximum_drawdown_r": 12.0,
      "tested_from": "2025-01-01",
      "tested_to": "2026-06-30",
      "evidence_version": "walk-forward-2026-07"
    }
  }
}
```

Do not hand-create a validated record. It must be produced from the versioned,
cost-inclusive, untouched-test research ledger matching the deployed strategy
version.

## Visible states

- `RESEARCH_SIGNAL`: technical setup passed; evidence gate did not.
- `QUALIFIED`: technical setup and walk-forward evidence passed.
- `PAPER_EXECUTED`: qualified signal accepted by paper risk controls.
- `PAPER_SKIPPED_RISK_LIMIT`: valid signal remained visible but the paper
  portfolio declined it.
- `REJECTED`: one or more explicit technical, context, execution or risk-plan
  rules failed.
