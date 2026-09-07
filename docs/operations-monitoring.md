# Production monitoring

OpenDelta's Operations workspace (`/operations`) presents durable worker, market-data, queue, connection, live-reconciliation, alert, and audit state. It is operational observability only: the workspace cannot approve a strategy or enable live execution.

## Worker leases

Migration `022_production_monitoring.sql` adds database-backed leases for market data, signals, backtests, research experiments, walk-forward validation, paper execution, live reconciliation, and monitoring. Acquisition is transactional and compare-and-set: a worker can take a task only when no lease exists, it already owns the lease, or the previous lease expired. Heartbeats extend active ownership and a crashed worker becomes recoverable after expiry.

Lease owner tokens are never returned by Operations APIs. The UI receives only a short fingerprint. Operators should investigate `FAILED` and `EXPIRED` leases before restarting a worker that repeatedly loses ownership.

## Alerts and notifications

Alerts have a stable fingerprint, occurrence history, acknowledgement, resolution, and cooldown. Repeated observations update one active alert rather than creating a storm. Resolving an alert preserves its history; a later occurrence opens a new alert.

The built-in notification adapters are:

- durable UI alerts (always enabled);
- structured application logging (always enabled);
- an optional HTTPS webhook configured through `MONITORING_WEBHOOK_URL`.

Webhook failures are recorded as notification deliveries and do not discard the alert. Payloads are bounded and secret-like fields are removed.

## Audit history

Important mutations are recorded in `operational_audit_events`. The HTTP audit middleware stores the request ID, actor, route template, action, result, and timestamp; it never records request bodies. Database triggers reject updates and deletes from the audit and alert occurrence streams.

## Running and triage

The singleton monitoring worker starts with the API runtime. Its interval is controlled by `MONITORING_INTERVAL_SECONDS` (minimum five seconds). It checks market-data freshness, missing candles, exchange connectivity, runner availability, queue pressure, reconciliation findings, emergency stops, and worker lease failures.

For an active alert:

1. Open `/operations` and inspect its exact timestamp and bounded context.
2. Acknowledge it to record ownership of the investigation.
3. Correct the underlying condition; do not expose credentials in notes or logs.
4. Resolve the alert with a short operator note after verification.

If the database is unavailable, the Operations health endpoint fails closed with HTTP 503. Existing market and execution safety controls remain independent.
