# Dynamic ATR exits

OpenDelta keeps the original RSI Recovery signal engine unchanged and applies
this module only after a valid observation has been emitted. The exit model is
selected per request:

- `LEGACY_FIXED_TARGET`: original target-only observation lifecycle.
- `FIXED_TP_SL`: fixed take-profit and stop-loss percentages.
- `ATR_DYNAMIC_TP_SL`: Wilder ATR-based frozen take-profit and stop-loss.

## ATR and frozen levels

True range is the maximum of the current high-low range and the two absolute
gaps from the previous close. ATR is Wilder's causal RMA of true range. For
`SIGNAL_CLOSE`, the completed signal candle supplies both ATR and entry close.
For `NEXT_BAR_OPEN`, the completed signal candle still supplies ATR while the
following candle supplies only the entry open. No entry-candle high or low is
used for an exit.

At execution:

```text
raw ATR %       = ATR at signal / actual entry price * 100
dynamic stop %  = clamp(raw ATR % * stop multiplier, minimum stop %, maximum stop %)
dynamic TP %    = dynamic stop % * reward:risk
stop price      = entry * (1 - dynamic stop % / 100)
target price    = entry * (1 + dynamic TP % / 100)
```

These values are frozen when the position is created.

## Exit ordering

Starting with the candle after entry, a long position is evaluated in this
order:

1. open at/below stop: `STOP_GAP`, filled at the open;
2. open at/above target: `TARGET_GAP`, filled at the open;
3. first available session after the holding limit: `TIME_EXIT`, filled at its
   open after the two gap checks above;
4. low touches stop: `STOP_EXIT`, filled at the stop;
5. high touches target: `TARGET_EXIT`, filled at the target.

When one OHLC candle touches both levels, the stop is deliberately applied
first because intrabar ordering is unknowable. Session counts use dates present
in the NSE candle data, with the entry session counted as session one.

## Position sizing and costs

Fixed-quantity mode uses a positive whole-share quantity. Risk-budget mode
uses `floor(rupee risk budget / risk per share)`, then applies both maximum
quantity and maximum-capital caps. A result that would produce zero shares is
rejected.

Closed-position P&L is:

```text
gross P&L = (exit - entry) * quantity
buy cost = entry turnover * buy cost bps / 10,000
sell cost = exit turnover * sell cost bps / 10,000
slippage = entry turnover * slippage bps / 10,000
           + exit turnover * slippage bps / 10,000
net P&L = gross P&L - buy cost - sell cost - slippage
```

Open positions are marked to the final close and include estimated closing cost
and closing slippage in their unrealized net P&L. Those estimates are reported
separately from costs already incurred.

## Optimization safeguards

ATR optimization is explicit and never runs during a normal backtest. Every
configuration is common to the selected universe, includes configured costs,
and uses chronological development/validation folds. Training and validation
candles are disjoint. Results are ranked primarily on validation outcomes and
are always labeled `Research candidate — not live approved`.
