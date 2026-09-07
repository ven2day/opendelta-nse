# Live reconciliation runbook

Reconciliation treats OpenDelta intent state and provider state as separate
facts. A timeout never means an order failed.

The bounded cycle examines submitted, acknowledged, partially filled,
cancel-requested, and unknown intents. It queries the exact provider order,
upserts provider fill IDs, advances only through allowed state-machine edges,
and marks matched or still-required reconciliation status.

Durable findings distinguish missing acknowledgement, unknown provider order,
duplicate provider order, partial fill, cancel failure, position mismatch,
balance mismatch, stale connection, and provider outage. Findings do not mutate
positions or manufacture fills.

## Incident procedure

1. Activate the narrowest emergency stop; use global scope when uncertain.
2. Do not retry an `UNKNOWN` order submission.
3. Run or wait for reconciliation and compare client/provider order IDs.
4. Inspect provider fills, positions, and balances using read-only adapter calls.
5. Resolve the mismatch at the provider only through an explicitly authorised
   operation; record the operator action.
6. Re-run reconciliation until the durable state matches.
7. Clear the alert/finding and emergency stop only after owner review.

The reconciliation worker remains idle while live trading is globally disabled.
Rollback retains intents, fills, events, and findings for audit history.
