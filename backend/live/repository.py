"""Persistence for live deployments, intents, fills, reconciliation, and stops."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from backend.data.database import Database, jsonb
from backend.live.adapters import ProviderFill, ProviderOrder


class LiveExecutionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_risk_policy(self, values: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
        policy_id = uuid.uuid4()
        row = self.database.fetch_one(
            """
            INSERT INTO live_risk_policies (
                risk_policy_id, name, max_order_value, max_position_value, max_total_exposure,
                max_open_positions, max_daily_trades, max_daily_loss, max_price_deviation_pct,
                max_signal_age_seconds, max_candle_age_seconds, symbol_allowlist, market_allowlist,
                strategy_allowlist, timeframe_allowlist, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                policy_id,
                values["name"],
                values["maxOrderValue"],
                values["maxPositionValue"],
                values["maxTotalExposure"],
                values["maxOpenPositions"],
                values["maxDailyTrades"],
                values["maxDailyLoss"],
                values["maxPriceDeviationPct"],
                values["maxSignalAgeSeconds"],
                values["maxCandleAgeSeconds"],
                jsonb(values["symbolAllowlist"]),
                jsonb(values["marketAllowlist"]),
                jsonb(values["strategyAllowlist"]),
                jsonb(values["timeframeAllowlist"]),
                actor,
            ),
        )
        assert row is not None
        return _public_policy(row)

    def list_risk_policies(self) -> list[dict[str, Any]]:
        return [
            _public_policy(row)
            for row in self.database.fetch_all("SELECT * FROM live_risk_policies ORDER BY created_at DESC")
        ]

    def create_deployment(
        self,
        *,
        approval_id: str,
        risk_policy_id: str,
        provider: str,
        connection_id: str | None,
        actor: str,
    ) -> dict[str, Any]:
        approval = self.database.fetch_one(
            "SELECT * FROM strategy_approvals WHERE approval_id = %s", (uuid.UUID(approval_id),)
        )
        if approval is None:
            raise KeyError("Approval was not found")
        if approval["mode"] != "PAPER":
            raise ValueError("Live deployment requires an explicit Paper approval")
        if provider == "DHAN" and approval["market"] != "NSE":
            raise ValueError("Dhan live deployments require NSE approval evidence")
        if provider in {"OKX", "VALR"} and approval["market"] != "CRYPTO":
            raise ValueError("Crypto live deployments require Crypto approval evidence")
        if provider == "DHAN" and connection_id:
            raise ValueError("Dhan credentials remain deployment-managed")
        if provider in {"OKX", "VALR"}:
            connection = self.database.fetch_one(
                "SELECT provider FROM exchange_connections WHERE connection_id = %s", (uuid.UUID(str(connection_id)),)
            )
            if connection is None or connection["provider"] != provider:
                raise ValueError("Live deployment connection does not match its provider")
        row = self.database.fetch_one(
            """
            INSERT INTO live_deployments (
                live_deployment_id, provider, connection_id, approval_id, risk_policy_id, market,
                strategy_id, strategy_version, strategy_source_id, config_id, universe_id, timeframe,
                configuration_snapshot, execution_settings, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                uuid.uuid4(),
                provider,
                uuid.UUID(connection_id) if connection_id else None,
                uuid.UUID(approval_id),
                uuid.UUID(risk_policy_id),
                approval["market"],
                approval["strategy_id"],
                approval["strategy_version"],
                approval.get("strategy_source_id"),
                approval["config_id"],
                approval["universe_id"],
                approval["timeframe"],
                jsonb(approval["configuration_snapshot"]),
                jsonb(approval["execution_settings"]),
                actor,
            ),
        )
        assert row is not None
        return _public_deployment(row)

    def list_deployments(self) -> list[dict[str, Any]]:
        return [
            _public_deployment(row)
            for row in self.database.fetch_all("SELECT * FROM live_deployments ORDER BY created_at DESC")
        ]

    def eligible_approvals(self) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            """
            SELECT approval_id, run_id, market, strategy_id, strategy_version, config_id,
                   universe_id, timeframe, strategy_source_id, approved_at
            FROM strategy_approvals WHERE mode = 'PAPER' ORDER BY approved_at DESC LIMIT 100
            """
        )
        return [
            {
                "approvalId": str(row["approval_id"]),
                "runId": str(row["run_id"]),
                "market": row["market"],
                "strategyId": row["strategy_id"],
                "strategyVersion": row["strategy_version"],
                "configId": str(row["config_id"]),
                "universeId": str(row["universe_id"]),
                "timeframe": row["timeframe"],
                "strategySourceId": str(row["strategy_source_id"]) if row.get("strategy_source_id") else None,
                "approvedAt": _iso(row["approved_at"]),
            }
            for row in rows
        ]

    def deployment_context(self, deployment_id: str) -> dict[str, Any]:
        row = self.database.fetch_one(
            """
            SELECT d.*, p.*, u.symbols AS universe_symbols,
                   c.status AS connection_status, c.disabled AS connection_disabled,
                   c.permissions AS connection_permissions, c.last_test_success, c.last_tested_at
            FROM live_deployments d
            JOIN live_risk_policies p ON p.risk_policy_id = d.risk_policy_id
            JOIN saved_universes u ON u.universe_id = d.universe_id
            LEFT JOIN exchange_connections c ON c.connection_id = d.connection_id
            WHERE d.live_deployment_id = %s
            """,
            (uuid.UUID(deployment_id),),
        )
        if row is None:
            raise KeyError("Live deployment was not found")
        return dict(row)

    def set_deployment_status(self, deployment_id: str, status: str, *, actor: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        row = self.database.fetch_one(
            """
            UPDATE live_deployments SET status = %s,
                activated_by = CASE WHEN %s = 'ACTIVE' THEN %s ELSE activated_by END,
                activated_at = CASE WHEN %s = 'ACTIVE' THEN %s ELSE activated_at END,
                disabled_by = CASE WHEN %s = 'DISABLED' THEN %s ELSE disabled_by END,
                disabled_at = CASE WHEN %s = 'DISABLED' THEN %s ELSE disabled_at END,
                updated_at = %s WHERE live_deployment_id = %s RETURNING *
            """,
            (status, status, actor, status, now, status, actor, status, now, now, uuid.UUID(deployment_id)),
        )
        if row is None:
            raise KeyError("Live deployment was not found")
        return _public_deployment(row)

    def signal_context(self, deployment_id: str, signal_id: str) -> dict[str, Any]:
        deployment = self.deployment_context(deployment_id)
        signal = self.database.fetch_one("SELECT * FROM live_signals WHERE signal_id = %s", (uuid.UUID(signal_id),))
        if signal is None:
            raise KeyError("Signal was not found")
        deployment["signal"] = signal
        return deployment

    def risk_snapshot(self, deployment_id: str, symbol: str, *, now: datetime) -> dict[str, Any]:
        row = self.database.fetch_one(
            """
            SELECT
                count(*) FILTER (WHERE created_at >= date_trunc('day', %s::timestamptz)
                    AND state NOT IN ('BLOCKED', 'REJECTED')) AS daily_trades,
                count(DISTINCT symbol) FILTER (WHERE state IN ('SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED')) AS open_positions,
                COALESCE(sum((requested_order->>'notional')::numeric)
                    FILTER (WHERE state IN ('SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED')), 0) AS total_exposure,
                COALESCE(sum((requested_order->>'notional')::numeric)
                    FILTER (WHERE symbol = %s AND state IN ('SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED')), 0) AS position_value
            FROM live_order_intents WHERE live_deployment_id = %s
            """,
            (now, symbol, uuid.UUID(deployment_id)),
        )
        return {
            "dailyTrades": int(row["daily_trades"] or 0),
            "openPositions": int(row["open_positions"] or 0),
            "totalExposure": Decimal(row["total_exposure"] or 0),
            "positionValue": Decimal(row["position_value"] or 0),
            # Realized daily live P&L is populated by reconciliation as fills mature. No estimate is fabricated.
            "dailyLoss": Decimal("0"),
        }

    def active_stops(self, *, provider: str, market: str, strategy_id: str) -> list[dict[str, Any]]:
        rows = self.database.fetch_all(
            """
            SELECT * FROM emergency_stops WHERE active AND (
                (scope_type = 'GLOBAL' AND scope_key = '*') OR
                (scope_type = 'PROVIDER' AND scope_key = %s) OR
                (scope_type = 'MARKET' AND scope_key = %s) OR
                (scope_type = 'STRATEGY' AND scope_key = %s)
            ) ORDER BY activated_at
            """,
            (provider, market, strategy_id),
        )
        return [_public_stop(row) for row in rows]

    def list_stops(self) -> list[dict[str, Any]]:
        return [
            _public_stop(row)
            for row in self.database.fetch_all("SELECT * FROM emergency_stops ORDER BY active DESC, updated_at DESC")
        ]

    def set_stop(self, *, scope_type: str, scope_key: str, active: bool, reason: str, actor: str) -> dict[str, Any]:
        stop_id = uuid.uuid4()
        now = datetime.now(UTC)
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO emergency_stops (
                    emergency_stop_id, scope_type, scope_key, active, reason, changed_by, activated_at, cleared_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT emergency_stops_scope DO UPDATE SET
                    active = EXCLUDED.active, reason = EXCLUDED.reason, changed_by = EXCLUDED.changed_by,
                    activated_at = CASE WHEN EXCLUDED.active THEN %s ELSE emergency_stops.activated_at END,
                    cleared_at = CASE WHEN EXCLUDED.active THEN NULL ELSE %s END, updated_at = %s
                RETURNING *
                """,
                (stop_id, scope_type, scope_key, active, reason, actor, now, None if active else now, now, now, now),
            )
            row = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO emergency_stop_events (emergency_stop_id, scope_type, scope_key, active, actor, reason)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (row["emergency_stop_id"], scope_type, scope_key, active, actor, reason),
            )
        return _public_stop(row)

    def create_intent(
        self,
        *,
        context: Mapping[str, Any],
        idempotency_key: str,
        client_order_id: str,
        requested_order: Mapping[str, Any],
        actor: str,
    ) -> tuple[dict[str, Any], bool]:
        signal = context["signal"]
        intent_id = uuid.uuid4()
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO live_order_intents (
                    intent_id, idempotency_key, live_deployment_id, connection_id, signal_id, provider, market,
                    strategy_id, strategy_version, strategy_source_id, config_id, universe_id, timeframe, symbol,
                    client_order_id, requested_order, configuration_snapshot, execution_settings, state
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CREATED')
                ON CONFLICT (idempotency_key) DO NOTHING RETURNING *
                """,
                (
                    intent_id,
                    idempotency_key,
                    context["live_deployment_id"],
                    context.get("connection_id"),
                    signal["signal_id"],
                    context["provider"],
                    context["market"],
                    context["strategy_id"],
                    context["strategy_version"],
                    context.get("strategy_source_id"),
                    context["config_id"],
                    context["universe_id"],
                    context["timeframe"],
                    signal["symbol"],
                    client_order_id,
                    jsonb(requested_order),
                    jsonb(context["configuration_snapshot"]),
                    jsonb(context["execution_settings"]),
                ),
            )
            row = cursor.fetchone()
            created = row is not None
            if row is None:
                cursor.execute("SELECT * FROM live_order_intents WHERE idempotency_key = %s", (idempotency_key,))
                row = cursor.fetchone()
            else:
                _state_event(
                    cursor, intent_id, None, "CREATED", actor, "Intent persisted before provider submission", None
                )
        return _public_intent(row), created

    def transition(
        self,
        intent_id: str,
        state: str,
        *,
        actor: str,
        reason: str | None = None,
        provider_order: ProviderOrder | None = None,
        blocked_reasons: list[str] | None = None,
        reconciliation_status: str | None = None,
    ) -> dict[str, Any]:
        key = uuid.UUID(intent_id)
        now = datetime.now(UTC)
        with self.database.transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM live_order_intents WHERE intent_id = %s FOR UPDATE", (key,))
            current = cursor.fetchone()
            if current is None:
                raise KeyError("Live order intent was not found")
            if state != current["state"] and state not in _ALLOWED_TRANSITIONS[current["state"]]:
                raise ValueError(f"Invalid live order transition {current['state']} -> {state}")
            response = dict(provider_order.provider_metadata) if provider_order else current.get("provider_response")
            provider_id = provider_order.provider_order_id if provider_order else current.get("provider_order_id")
            reconcile = reconciliation_status or current["reconciliation_status"]
            cursor.execute(
                """
                UPDATE live_order_intents SET state = %s, provider_order_id = %s, provider_response = %s,
                    provider_metadata = %s, blocked_reasons = %s, last_error = %s,
                    reconciliation_status = %s,
                    submitted_at = CASE WHEN %s IN ('SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED') THEN COALESCE(submitted_at, %s) ELSE submitted_at END,
                    terminal_at = CASE WHEN %s IN ('BLOCKED', 'FILLED', 'CANCELLED', 'REJECTED') THEN %s ELSE NULL END,
                    updated_at = %s WHERE intent_id = %s RETURNING *
                """,
                (
                    state,
                    provider_id,
                    jsonb(response) if response is not None else None,
                    jsonb(response or current.get("provider_metadata") or {}),
                    jsonb(blocked_reasons or current["blocked_reasons"]),
                    reason,
                    reconcile,
                    state,
                    now,
                    state,
                    now,
                    now,
                    key,
                ),
            )
            row = cursor.fetchone()
            _state_event(cursor, key, current["state"], state, actor, reason, response)
        return _public_intent(row)

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM live_order_intents WHERE intent_id = %s", (uuid.UUID(intent_id),))
        if row is None:
            raise KeyError("Live order intent was not found")
        return _public_intent(row)

    def list_intents(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [
            _public_intent(row)
            for row in self.database.fetch_all(
                "SELECT * FROM live_order_intents ORDER BY created_at DESC LIMIT %s", (min(max(limit, 1), 500),)
            )
        ]

    def intents_requiring_reconciliation(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [
            _public_intent(row)
            for row in self.database.fetch_all(
                """
            SELECT * FROM live_order_intents
            WHERE reconciliation_status IN ('PENDING', 'MISMATCH', 'REQUIRED')
               OR state IN ('SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'CANCEL_REQUESTED', 'UNKNOWN')
            ORDER BY updated_at LIMIT %s
            """,
                (min(max(limit, 1), 500),),
            )
        ]

    def save_fill(self, intent_id: str, fill: ProviderFill) -> None:
        self.database.execute(
            """
            INSERT INTO live_order_fills (
                fill_id, intent_id, provider_fill_id, quantity, price, fee, fee_currency, filled_at, provider_metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT ON CONSTRAINT live_order_fills_provider DO NOTHING
            """,
            (
                uuid.uuid4(),
                uuid.UUID(intent_id),
                fill.provider_fill_id,
                fill.quantity,
                fill.price,
                fill.fee,
                fill.fee_currency,
                fill.filled_at,
                jsonb(dict(fill.provider_metadata)),
            ),
        )

    def add_finding(
        self, *, intent_id: str | None, provider: str, finding_type: str, details: Mapping[str, Any]
    ) -> None:
        self.database.execute(
            """
            INSERT INTO live_reconciliation_findings (finding_id, intent_id, provider, finding_type, details)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (uuid.uuid4(), uuid.UUID(intent_id) if intent_id else None, provider, finding_type, jsonb(dict(details))),
        )


_ALLOWED_TRANSITIONS = {
    "CREATED": {
        "BLOCKED",
        "SUBMITTED",
        "ACKNOWLEDGED",
        "PARTIALLY_FILLED",
        "FILLED",
        "REJECTED",
        "UNKNOWN",
    },
    "BLOCKED": set(),
    "SUBMITTED": {
        "ACKNOWLEDGED",
        "PARTIALLY_FILLED",
        "FILLED",
        "REJECTED",
        "UNKNOWN",
        "CANCEL_REQUESTED",
        "CANCELLED",
    },
    "ACKNOWLEDGED": {"PARTIALLY_FILLED", "FILLED", "REJECTED", "UNKNOWN", "CANCEL_REQUESTED", "CANCELLED"},
    "PARTIALLY_FILLED": {"PARTIALLY_FILLED", "FILLED", "UNKNOWN", "CANCEL_REQUESTED", "CANCELLED"},
    "FILLED": set(),
    "CANCEL_REQUESTED": {"CANCELLED", "FILLED", "PARTIALLY_FILLED", "REJECTED", "UNKNOWN"},
    "CANCELLED": set(),
    "REJECTED": set(),
    "UNKNOWN": {
        "ACKNOWLEDGED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCEL_REQUESTED",
        "CANCELLED",
        "REJECTED",
        "UNKNOWN",
    },
}


def _state_event(
    cursor: Any,
    intent_id: uuid.UUID,
    from_state: str | None,
    to_state: str,
    actor: str,
    reason: str | None,
    payload: Mapping[str, Any] | None,
) -> None:
    cursor.execute(
        """
        INSERT INTO live_order_state_events (intent_id, from_state, to_state, actor, reason, provider_payload)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (intent_id, from_state, to_state, actor, reason, jsonb(dict(payload)) if payload is not None else None),
    )


def _public_policy(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "riskPolicyId": str(row["risk_policy_id"]),
        "name": row["name"],
        "maxOrderValue": str(row["max_order_value"]),
        "maxPositionValue": str(row["max_position_value"]),
        "maxTotalExposure": str(row["max_total_exposure"]),
        "maxOpenPositions": row["max_open_positions"],
        "maxDailyTrades": row["max_daily_trades"],
        "maxDailyLoss": str(row["max_daily_loss"]),
        "maxPriceDeviationPct": str(row["max_price_deviation_pct"]),
        "maxSignalAgeSeconds": row["max_signal_age_seconds"],
        "maxCandleAgeSeconds": row["max_candle_age_seconds"],
        "symbolAllowlist": list(row["symbol_allowlist"]),
        "marketAllowlist": list(row["market_allowlist"]),
        "strategyAllowlist": list(row["strategy_allowlist"]),
        "timeframeAllowlist": list(row["timeframe_allowlist"]),
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
    }


def _public_deployment(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "liveDeploymentId": str(row["live_deployment_id"]),
        "provider": row["provider"],
        "connectionId": str(row["connection_id"]) if row.get("connection_id") else None,
        "approvalId": str(row["approval_id"]),
        "riskPolicyId": str(row["risk_policy_id"]),
        "market": row["market"],
        "strategyId": row["strategy_id"],
        "strategyVersion": row["strategy_version"],
        "strategySourceId": str(row["strategy_source_id"]) if row.get("strategy_source_id") else None,
        "configId": str(row["config_id"]),
        "universeId": str(row["universe_id"]),
        "timeframe": row["timeframe"],
        "configuration": dict(row["configuration_snapshot"]),
        "execution": dict(row["execution_settings"]),
        "status": row["status"],
        "activatedAt": _iso(row.get("activated_at")),
        "disabledAt": _iso(row.get("disabled_at")),
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
    }


def _public_stop(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "emergencyStopId": str(row["emergency_stop_id"]),
        "scopeType": row["scope_type"],
        "scopeKey": row["scope_key"],
        "active": row["active"],
        "reason": row["reason"],
        "changedBy": row["changed_by"],
        "activatedAt": _iso(row["activated_at"]),
        "clearedAt": _iso(row.get("cleared_at")),
        "updatedAt": _iso(row["updated_at"]),
    }


def _public_intent(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "intentId": str(row["intent_id"]),
        "idempotencyKey": row["idempotency_key"],
        "liveDeploymentId": str(row["live_deployment_id"]),
        "connectionId": str(row["connection_id"]) if row.get("connection_id") else None,
        "signalId": str(row["signal_id"]),
        "provider": row["provider"],
        "market": row["market"],
        "strategyId": row["strategy_id"],
        "strategyVersion": row["strategy_version"],
        "strategySourceId": str(row["strategy_source_id"]) if row.get("strategy_source_id") else None,
        "configId": str(row["config_id"]),
        "universeId": str(row["universe_id"]),
        "timeframe": row["timeframe"],
        "symbol": row["symbol"],
        "clientOrderId": row["client_order_id"],
        "providerOrderId": row.get("provider_order_id"),
        "requestedOrder": dict(row["requested_order"]),
        "configuration": dict(row["configuration_snapshot"]),
        "execution": dict(row["execution_settings"]),
        "providerMetadata": dict(row.get("provider_metadata") or {}),
        "state": row["state"],
        "reconciliationStatus": row["reconciliation_status"],
        "blockedReasons": list(row["blocked_reasons"]),
        "lastError": row.get("last_error"),
        "retryCount": row["retry_count"],
        "submittedAt": _iso(row.get("submitted_at")),
        "terminalAt": _iso(row.get("terminal_at")),
        "createdAt": _iso(row["created_at"]),
        "updatedAt": _iso(row["updated_at"]),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None
