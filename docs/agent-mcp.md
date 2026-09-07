# Agent and MCP access

OpenDelta exposes a stateless, JSON-only Streamable HTTP MCP endpoint at `/api/mcp` (the frontend forwards it to the backend `/mcp` endpoint). It is a research surface, not an execution or governance surface. The server has no tools for credentials, approvals, deployments, emergency-stop release, live orders, arbitrary Python, shell commands, filesystem access, or unrestricted network requests.

## Create and revoke a token

An authenticated platform administrator can create a token with `POST /api/v2/agent/tokens`. The raw `odt_...` token is returned exactly once. Only a SHA-256 digest of a cryptographically random 256-bit token is stored. Put the raw value in a secret manager; never add it to an environment sample, shell history, issue, log, or repository.

Example request body:

```json
{
  "name": "Codex research",
  "scopes": ["research:read", "backtests:submit", "experiments:submit"],
  "expiresAt": "2026-10-01T00:00:00Z",
  "rateLimitPerMinute": 30
}
```

Use `GET /api/v2/agent/tokens` to inspect prefixes, scopes, expiry, revocation, and last use. Use `DELETE /api/v2/agent/tokens/<token-id>` to revoke a token immediately. These administration endpoints require the existing authenticated web/proxy boundary and never accept an agent token as administrator authority.

## Scopes

| Scope | Authority |
| --- | --- |
| `research:read` | Read immutable strategy/indicator metadata, watchlists, backtest results/trades, experiments, comparisons, and walk-forward results. |
| `backtests:submit` | Submit research backtests to the existing bounded worker pool. |
| `experiments:submit` | Preview and submit parameter experiments through the existing preview-hash contract. |
| `walk-forward:submit` | Preview and submit walk-forward validations through the existing preview-hash contract. |
| `ai:use` | Request explicit-context AI research explanations when a backend provider is configured. |
| `monitoring:read` | Read the secret-free Operations health snapshot. |

Tokens expire within 365 days, can be revoked, record last use, and have a durable per-minute rate limit. Submission tools require idempotency keys. OpenDelta reserves an at-most-once request record before dispatch; a retry returns the saved response, while an uncertain pending request fails closed for reconciliation rather than creating a duplicate.

## Tools

The server publishes strict JSON Schemas and annotations for these tools:

- `opendelta_list_strategies`, `opendelta_read_strategy`
- `opendelta_list_indicators`, `opendelta_read_indicator`
- `opendelta_list_watchlists`
- `opendelta_preview_backtest`, `opendelta_submit_backtest`, `opendelta_poll_backtest`
- `opendelta_read_backtest_metrics`, `opendelta_read_backtest_trades`
- `opendelta_preview_experiment`, `opendelta_submit_experiment`, `opendelta_poll_experiment`, `opendelta_read_comparison`
- `opendelta_preview_walk_forward`, `opendelta_submit_walk_forward`, `opendelta_poll_walk_forward`
- `opendelta_explain_research`
- `opendelta_read_monitoring`

Tool discovery is paginated. List outputs and trade reads are paginated and bounded; all tool output is capped at 524,288 bytes. Source metadata responses remove source code and secret-like fields. Every tool call records a request ID, token ID, scope/tool, outcome, and stable error code in the append-only operational audit stream without recording input bodies.

## Codex configuration

Set the one-time token in the local environment as `OPENDELTA_AGENT_TOKEN`, then add this to `~/.codex/config.toml` or a trusted project's `.codex/config.toml`:

```toml
[mcp_servers.opendelta]
url = "https://delta.ventoday.com/api/mcp"
bearer_token_env_var = "OPENDELTA_AGENT_TOKEN"
tool_timeout_sec = 60
default_tools_approval_mode = "writes"
enabled_tools = [
  "opendelta_list_strategies",
  "opendelta_list_watchlists",
  "opendelta_preview_backtest",
  "opendelta_submit_backtest",
  "opendelta_poll_backtest",
  "opendelta_read_backtest_metrics"
]
```

Restart the Codex client and use `/mcp` or `codex mcp list` to verify discovery. Grant only the scopes and enabled tools needed for that agent. Preview before any submission and keep write-tool approval prompts enabled.

## Transport and errors

The endpoint accepts authenticated UTF-8 JSON-RPC POST requests. It supports MCP protocol versions `2025-03-26`, `2025-06-18`, and `2025-11-25`, paginated `tools/list`, and `tools/call`. Server-sent events and server-initiated messages are not needed, so GET returns 405. Browser `Origin` values are rejected unless explicitly listed in the backend-only `MCP_ALLOWED_ORIGINS` setting; normal server-to-server clients omit `Origin`.

Errors use stable codes such as `INVALID_TOKEN`, `RATE_LIMITED`, `SCOPE_DENIED`, `INVALID_ARGUMENTS`, `IDEMPOTENCY_CONFLICT`, `REQUEST_IN_PROGRESS`, `NOT_FOUND`, and `UNAVAILABLE`. A provider or database outage fails closed and does not grant a broader capability.
