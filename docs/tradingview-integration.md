# TradingView signal integration

TradingView supplies chart-side BUY alerts. OpenDelta remains the control and
execution system: it authenticates the alert, checks the approved market,
strategy version, timeframe and watchlist, deduplicates it, stores it in the
normal Signals ledger and optionally sends it to paper execution. Real broker
orders remain disabled.

## Production setup

1. Apply migration `010_tradingview_signal_ingestion` with
   `python -m backend.data.migrate`.
2. Generate a dedicated random value and set `TRADINGVIEW_WEBHOOK_KEY` in
   `/etc/opendelta-dhan.env`. This is a revocable webhook credential, never an
   exchange or broker key.
3. Restart the backtest/platform service.
4. In **Strategies**, save an active configuration, choose an explicit saved
   watchlist, select **TradingView** as the signal source and select **Signals**
   or **Paper**. Selecting TradingView prevents the corresponding OpenDelta
   strategy worker from generating competing entries; candle monitoring stays
   active for paper-position management.
5. Add [`integrations/tradingview/rsi_dip_ladder_v1.pine`](../integrations/tradingview/rsi_dip_ladder_v1.pine)
   to TradingView and set the same webhook key in the indicator settings.
6. Create a TradingView alert using **Any alert() function call** and webhook
   URL `https://delta.ventoday.com/api/tradingview/webhook`.

The selected TradingView chart timeframe must exactly match the approved
OpenDelta deployment. A TradingView ticker is resolved only against that
deployment's saved watchlist: for example `OKX:BTCUSDT` can resolve to the
configured `BTC-USDT`, but an instrument outside the watchlist is rejected.

## Alert contract (version 1)

```json
{
  "schemaVersion": "1",
  "eventId": "rsi_dip_ladder_v1:OKX:BTCUSDT:15:1788696000000",
  "webhookKey": "a dedicated random webhook value",
  "market": "CRYPTO",
  "exchange": "OKX",
  "symbol": "OKX:BTCUSDT",
  "timeframe": "15m",
  "strategyId": "rsi_dip_ladder_v1",
  "strategyVersion": "1.0.0",
  "action": "BUY",
  "candleTimestamp": "2026-09-06T11:45:00Z",
  "sentAt": "2026-09-06T12:00:00Z",
  "signalPrice": 55000,
  "targetPrice": 57750,
  "reasons": ["RSI_LOW_ARMED", "RSI_RECOVERED"],
  "indicators": { "rsi": 35.2 }
}
```

`eventId` is an idempotency key. Repeating the same event returns an accepted
duplicate response and never opens a second paper order. Alerts older than
`TRADINGVIEW_MAX_ALERT_AGE_SECONDS` (900 seconds by default) are rejected.
Only BUY entry alerts are accepted in v1; OpenDelta manages target, stop,
expiry and manual exits using the approved paper-risk configuration. Any
target or stop included by TradingView is retained as signal evidence but is
not allowed to override the approved execution settings.
