# ADR 0006: Structured logging with structlog

Status: accepted

The backend emits structured, JSON-formatted log lines through `structlog`,
configured once in `backend/observability.py` and installed during the FastAPI
lifespan. Every request carries an `x-request-id` (generated when absent) that
is bound to the log context and echoed on the response, so a user-visible
failure can be traced end to end.

Rules:

- Modules obtain loggers via `backend.observability.get_logger(name)`; the
  logger name is the dotted module path (`opendelta.<area>`).
- Exception sinks must log the reason instead of swallowing it; degraded states
  (stale data, unreachable database) surface through health endpoints rather
  than silent passes.
- Third-party log records are routed through the same processor chain, keeping
  one machine-readable stream per container.

The human-readable console rendering is intentionally not supported in
production; local development reads the same JSON lines.