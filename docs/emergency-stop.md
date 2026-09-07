# Emergency-stop runbook

Stops have four independent scopes: `GLOBAL` (`*`), `PROVIDER` (`DHAN`, `OKX`,
or `VALR`), `MARKET` (`NSE` or `CRYPTO`), and `STRATEGY` (strategy ID).

## Activate

1. Open Settings → Live execution foundation.
2. Select the smallest sufficient scope and enter a concrete reason.
3. Confirm `ACTIVATE EMERGENCY STOP`.
4. Verify the stop is active and new matching order attempts become `BLOCKED`.
5. Inspect monitoring and the live intent ledger.

Activation is an upsert and is idempotent. Every change appends an immutable
event recording scope, actor, reason, and timestamp. It blocks new orders
immediately. It does **not** cancel existing provider orders.

## Clear

Investigate and reconcile first. Clearing requires the exact phrase
`CLEAR <SCOPE_TYPE> <scope-key>`. Confirm the global live and deployment flags,
connection test, withdrawal-permission result, deployment pins, and risk policy
again. Clearing a stop does not activate a live deployment.

Cancel-all is intentionally absent. If existing orders must be cancelled, use
the separately confirmed per-order flow after identifying the provider state.
