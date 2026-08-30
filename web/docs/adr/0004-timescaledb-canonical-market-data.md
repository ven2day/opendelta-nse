# ADR 0004: TimescaleDB is the canonical market-data store

## Status

Accepted for incremental rollout. Research V2 remains disabled.

## Decision

Completed NSE and crypto candles will converge on one TimescaleDB hypertable.
Operational metadata may remain in SQLite. Frozen research inputs may be
exported to Parquet. Redis is not part of the initial architecture.

TimescaleDB provides durable constraints, idempotent upserts, indexed range
queries and time-series partitioning. Redis would add a second copy of current
state before measurements show it is needed.

## Safety and rollout

Existing file and SQLite readers are not removed in this change. Provision
TimescaleDB, apply the migration, load an authoritative NSE session calendar,
run dual writes and reconcile counts/checksums before changing readers.
Research V2 remains fail-closed.

NSE gaps are derived only from explicit `market_sessions` rows. The repair
worker must never infer that every weekday is an exchange session. Crypto uses
continuous UTC time.

## Future Redis trigger

Redis Streams or a latest-value cache may be introduced only after observed
latency or worker fan-out shows a need. TimescaleDB remains authoritative, and
events may be published only after the database commit succeeds.
