# Live execution safety

OpenDelta contains Dhan, OKX, and VALR order adapters, but real provider
mutation is disabled by default. Paper trading remains the default execution
path. A browser control alone can never enable live trading.

## Authority chain

An order is eligible only when every check passes at submission time:

1. `LIVE_TRADING_ENABLED=true` and `LIVE_TRADING_DEPLOYMENT_ALLOWED=true`.
2. `DEPLOYMENT_ENVIRONMENT` is an exact member of
   `LIVE_TRADING_ALLOWED_ENVIRONMENTS`.
3. The deployment is explicitly active and pins a Paper-approved run,
   immutable strategy/source version, configuration, execution settings,
   watchlist snapshot, market, timeframe, provider, and risk policy.
4. An OKX/VALR connection is enabled, recently tested, trading-capable, and
   explicitly lacks withdrawal permission. Dhan uses its existing deployment
   authentication path.
5. No global, provider, market, or strategy emergency stop is active.
6. The persisted signal matches every deployment pin, belongs to the pinned
   universe, is a completed fresh candle, and is not a duplicate.
7. All order value, position, exposure, open-position, daily-trade, daily-loss,
   allowlist, price-deviation, freshness, session, and balance checks pass.

Risk failure persists a `BLOCKED` intent and reason. It never disappears
silently. The deterministic SHA-256 idempotency key covers the deployment,
signal, and canonical request; the unique database constraints prevent worker
retry, restart, timeout, and duplicate-signal submissions.

The intent is committed as `CREATED` before the adapter call. A transport
timeout is `UNKNOWN` with reconciliation required. Provider responses and
provider-specific fields are bounded JSON; credentials, decrypted material,
and encryption metadata are never part of intent responses.

## Provider boundary

The shared adapter interface includes submit/query/cancel/open orders,
balances, positions, fills, and health. Exchange-specific fields are carried in
a bounded `providerFields` object and stored with the immutable intent. Each
adapter independently refuses POST/DELETE mutation when its constructor gate is
false, even if a caller reaches it incorrectly.

Automated tests use transport mocks only. They must never use a production
credential or submit a production order.
