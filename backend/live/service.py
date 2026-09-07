"""Live-execution gates, risk evaluation, idempotency, and reconciliation."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.live.adapters import (
    OrderAdapter,
    OrderRequest,
    ProviderMutationDisabled,
    ProviderRequestError,
    ProviderResponseUncertain,
)
from backend.live.repository import LiveExecutionRepository
from backend.markets.nse.session import nse_session_is_open

Actor = "authenticated-web-user"


@dataclass(frozen=True)
class LiveExecutionConfig:
    live_trading_enabled: bool = False
    deployment_permission: bool = False
    deployment_environment: str = "UNSET"
    allowed_environments: tuple[str, ...] = ()
    recent_connection_seconds: int = 900

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> LiveExecutionConfig:
        values = os.environ if environ is None else environ
        allowed = tuple(
            item.strip().upper()
            for item in values.get("LIVE_TRADING_ALLOWED_ENVIRONMENTS", "").split(",")
            if item.strip()
        )
        return cls(
            live_trading_enabled=_truthy(values.get("LIVE_TRADING_ENABLED")),
            deployment_permission=_truthy(values.get("LIVE_TRADING_DEPLOYMENT_ALLOWED")),
            deployment_environment=values.get("DEPLOYMENT_ENVIRONMENT", "UNSET").strip().upper() or "UNSET",
            allowed_environments=allowed,
            recent_connection_seconds=min(max(int(values.get("LIVE_CONNECTION_MAX_AGE_SECONDS", "900")), 60), 86400),
        )

    @property
    def environment_allowed(self) -> bool:
        return self.deployment_environment in self.allowed_environments

    def public(self) -> dict[str, Any]:
        return {
            "liveTradingEnabled": self.live_trading_enabled,
            "deploymentPermission": self.deployment_permission,
            "deploymentEnvironment": self.deployment_environment,
            "environmentAllowed": self.environment_allowed,
            "defaultState": "Live trading disabled" if not self.live_trading_enabled else "Live trading gated",
        }


AdapterFactory = Callable[[Mapping[str, Any]], OrderAdapter]
DhanStatus = Callable[[], Mapping[str, Any]]


class LiveExecutionService:
    def __init__(
        self,
        repository: LiveExecutionRepository,
        *,
        adapters: AdapterFactory,
        config: LiveExecutionConfig,
        dhan_status: DhanStatus,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.adapters = adapters
        self.config = config
        self.dhan_status = dhan_status
        self.clock = clock or (lambda: datetime.now(UTC))

    def status(self) -> dict[str, Any]:
        return {
            **self.config.public(),
            "deployments": self.repository.list_deployments(),
            "riskPolicies": self.repository.list_risk_policies(),
            "eligiblePaperApprovals": self.repository.eligible_approvals(),
            "emergencyStops": self.repository.list_stops(),
            "intents": self.repository.list_intents(limit=100),
            "reconciliationFindings": self.repository.list_findings(limit=100),
            "cancelAllSupported": False,
            "cancelAllMessage": "Existing provider orders are never cancelled by an emergency stop",
        }

    def create_risk_policy(self, values: Mapping[str, Any], *, actor: str = Actor) -> dict[str, Any]:
        return self.repository.create_risk_policy(values, actor=actor)

    def create_deployment(
        self,
        *,
        approval_id: str,
        risk_policy_id: str,
        provider: str,
        connection_id: str | None,
        actor: str = Actor,
    ) -> dict[str, Any]:
        return self.repository.create_deployment(
            approval_id=approval_id,
            risk_policy_id=risk_policy_id,
            provider=provider,
            connection_id=connection_id,
            actor=actor,
        )

    def set_emergency_stop(
        self,
        *,
        scope_type: str,
        scope_key: str,
        active: bool,
        reason: str,
        confirmation: str,
        actor: str = Actor,
    ) -> dict[str, Any]:
        required = "ACTIVATE EMERGENCY STOP" if active else f"CLEAR {scope_type} {scope_key}"
        if confirmation != required:
            raise ValueError(f"Type {required} to confirm")
        return self.repository.set_stop(
            scope_type=scope_type, scope_key=scope_key, active=active, reason=reason, actor=actor
        )

    def activation_gates(self, context: Mapping[str, Any]) -> list[str]:
        now = self.clock()
        failures: list[str] = []
        if not self.config.live_trading_enabled:
            failures.append("Global LIVE_TRADING_ENABLED flag is false")
        if not self.config.deployment_permission:
            failures.append("Deployment-level live trading permission is absent")
        if not self.config.environment_allowed:
            failures.append("Deployment environment is not allowlisted for live trading")
        if context["provider"] == "DHAN":
            if not self.dhan_status().get("configured"):
                failures.append("Dhan authentication is not configured")
        else:
            if context.get("connection_disabled"):
                failures.append("Exchange connection is disabled")
            if context.get("connection_status") != "CONNECTED" or context.get("last_test_success") is not True:
                failures.append("Exchange connection has no successful permission test")
            permissions = context.get("connection_permissions") or {}
            if permissions.get("trade") is not True:
                failures.append("Exchange connection has no trading permission")
            if permissions.get("withdrawal") is not False:
                failures.append("Withdrawal permission must be explicitly absent")
            tested_at = context.get("last_tested_at")
            if tested_at is None or now - tested_at > timedelta(seconds=self.config.recent_connection_seconds):
                failures.append("Exchange connection test is stale")
        stops = self.repository.active_stops(
            provider=context["provider"], market=context["market"], strategy_id=context["strategy_id"]
        )
        if stops:
            failures.append("An emergency stop is active")
        return failures

    def activate_deployment(self, deployment_id: str, *, confirmation: str, actor: str = Actor) -> dict[str, Any]:
        context = self.repository.deployment_context(deployment_id)
        required = f"ENABLE LIVE {context['strategy_id']}"
        if confirmation != required:
            raise ValueError(f"Type {required} to confirm")
        failures = self.activation_gates(context)
        if failures:
            raise GateFailure(failures)
        return self.repository.set_deployment_status(deployment_id, "ACTIVE", actor=actor)

    def disable_deployment(self, deployment_id: str, *, actor: str = Actor) -> dict[str, Any]:
        return self.repository.set_deployment_status(deployment_id, "DISABLED", actor=actor)

    def submit_order(
        self,
        deployment_id: str,
        signal_id: str,
        order: OrderRequest,
        *,
        actor: str = Actor,
    ) -> dict[str, Any]:
        context = self.repository.signal_context(deployment_id, signal_id)
        signal = context["signal"]
        reference_price = Decimal(str(signal.get("last_price") or signal["signal_price"]))
        notional = order.quantity * (order.price or reference_price)
        canonical_order = {
            "symbol": order.symbol,
            "side": order.side,
            "orderType": order.order_type,
            "quantity": str(order.quantity),
            "price": str(order.price) if order.price is not None else None,
            "timeInForce": order.time_in_force,
            "providerFields": dict(order.provider_fields),
            "referencePrice": str(reference_price),
            "notional": str(notional),
        }
        idempotency_key = _intent_key(deployment_id, signal_id, canonical_order)
        client_order_id = f"od{idempotency_key[:28]}"
        intent, created = self.repository.create_intent(
            context=context,
            idempotency_key=idempotency_key,
            client_order_id=client_order_id,
            requested_order=canonical_order,
            actor=actor,
        )
        if not created:
            return intent

        failures = self.activation_gates(context)
        if context["status"] != "ACTIVE":
            failures.append("Live deployment is not active")
        failures.extend(self._identity_failures(context, order))
        failures.extend(self._risk_failures(context, order, reference_price, notional))
        if failures:
            return self.repository.transition(
                intent["intentId"], "BLOCKED", actor=actor, reason="; ".join(failures), blocked_reasons=failures
            )

        adapter = self.adapters(context)
        try:
            balances = adapter.fetch_balances()
            positions = adapter.fetch_positions()
        except ProviderRequestError:
            return self.repository.transition(
                intent["intentId"],
                "BLOCKED",
                actor=actor,
                reason="Provider account risk could not be verified",
                blocked_reasons=["Provider account risk could not be verified"],
            )
        balance_failure = _balance_failure(balances, context["provider"], order, notional)
        account_failures = _account_risk_failures(
            positions, context["provider"], Decimal(context["max_daily_loss"])
        )
        if balance_failure or account_failures:
            balance_and_account = ([balance_failure] if balance_failure else []) + account_failures
            return self.repository.transition(
                intent["intentId"],
                "BLOCKED",
                actor=actor,
                reason="; ".join(balance_and_account),
                blocked_reasons=balance_and_account,
            )
        try:
            result = adapter.submit_order(order, client_order_id)
        except ProviderResponseUncertain as error:
            return self.repository.transition(
                intent["intentId"], "UNKNOWN", actor=actor, reason=str(error), reconciliation_status="REQUIRED"
            )
        except (ProviderMutationDisabled, ProviderRequestError) as error:
            return self.repository.transition(intent["intentId"], "REJECTED", actor=actor, reason=str(error))
        state = (
            result.state
            if result.state in {"SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "REJECTED"}
            else "UNKNOWN"
        )
        return self.repository.transition(
            intent["intentId"],
            state,
            actor=actor,
            provider_order=result,
            reconciliation_status="NOT_REQUIRED" if state in {"FILLED", "REJECTED"} else "PENDING",
        )

    def cancel_order(self, intent_id: str, *, confirmation: str, actor: str = Actor) -> dict[str, Any]:
        intent = self.repository.get_intent(intent_id)
        if confirmation != f"CANCEL {intent['clientOrderId']}":
            raise ValueError(f"Type CANCEL {intent['clientOrderId']} to confirm")
        if (
            not self.config.live_trading_enabled
            or not self.config.deployment_permission
            or not self.config.environment_allowed
        ):
            raise GateFailure(["Provider cancellation is disabled by server-side live trading controls"])
        if intent["state"] not in {"SUBMITTED", "ACKNOWLEDGED", "PARTIALLY_FILLED", "UNKNOWN"}:
            raise ValueError("Only a non-terminal submitted order can be cancelled")
        if not intent["providerOrderId"]:
            raise ValueError("Provider order identity is unavailable; reconciliation is required")
        context = self.repository.deployment_context(intent["liveDeploymentId"])
        if self.repository.active_stops(
            provider=context["provider"], market=context["market"], strategy_id=context["strategy_id"]
        ):
            raise GateFailure(["Emergency stop blocks cancellation; use a separately confirmed provider operation"])
        adapter = self.adapters(context)
        try:
            response = adapter.cancel_order(
                intent["providerOrderId"], symbol=intent["symbol"], client_order_id=intent["clientOrderId"]
            )
        except ProviderResponseUncertain as error:
            return self.repository.transition(
                intent_id, "UNKNOWN", actor=actor, reason=str(error), reconciliation_status="REQUIRED"
            )
        return self.repository.transition(
            intent_id, "CANCEL_REQUESTED", actor=actor, provider_order=response, reconciliation_status="PENDING"
        )

    def reconcile(self, *, limit: int = 100, actor: str = "live-reconciliation-worker") -> dict[str, int]:
        counts = {"checked": 0, "matched": 0, "findings": 0, "errors": 0}
        by_deployment: dict[str, tuple[Mapping[str, Any], OrderAdapter]] = {}
        for intent in self.repository.intents_requiring_reconciliation(limit=limit):
            counts["checked"] += 1
            try:
                deployment_id = intent["liveDeploymentId"]
                if deployment_id not in by_deployment:
                    context = self.repository.deployment_context(deployment_id)
                    by_deployment[deployment_id] = (context, self.adapters(context))
                _context, adapter = by_deployment[deployment_id]
                if not intent["providerOrderId"]:
                    self.repository.add_finding(
                        intent_id=intent["intentId"],
                        provider=intent["provider"],
                        finding_type="MISSING_ACKNOWLEDGEMENT",
                        details={"clientOrderId": intent["clientOrderId"]},
                    )
                    counts["findings"] += 1
                    continue
                current = adapter.query_order(intent["providerOrderId"], symbol=intent["symbol"])
                for fill in adapter.fetch_fills(symbol=intent["symbol"]):
                    self.repository.save_fill(intent["intentId"], fill)
                if current.state == "PARTIALLY_FILLED":
                    self.repository.add_finding(
                        intent_id=intent["intentId"],
                        provider=intent["provider"],
                        finding_type="PARTIAL_FILL",
                        details={"providerOrderId": intent["providerOrderId"]},
                    )
                    counts["findings"] += 1
                if intent["state"] == "CANCEL_REQUESTED" and current.state not in {"CANCELLED", "FILLED"}:
                    self.repository.add_finding(
                        intent_id=intent["intentId"],
                        provider=intent["provider"],
                        finding_type="CANCEL_FAILURE",
                        details={"providerState": current.state},
                    )
                    counts["findings"] += 1
                self.repository.transition(
                    intent["intentId"],
                    current.state,
                    actor=actor,
                    provider_order=current,
                    reconciliation_status="MATCHED" if current.state != "UNKNOWN" else "REQUIRED",
                )
                counts["matched"] += 1
            except ProviderRequestError:
                self.repository.add_finding(
                    intent_id=intent["intentId"],
                    provider=intent["provider"],
                    finding_type="PROVIDER_OUTAGE",
                    details={"operation": "reconcile"},
                )
                counts["errors"] += 1
            except (KeyError, ValueError):
                counts["errors"] += 1
        return counts

    def _identity_failures(self, context: Mapping[str, Any], order: OrderRequest) -> list[str]:
        signal = context["signal"]
        failures: list[str] = []
        checks = (
            (signal["market"] == context["market"], "Signal market does not match the pinned deployment"),
            (signal["strategy_id"] == context["strategy_id"], "Signal strategy does not match the pinned deployment"),
            (
                signal["strategy_version"] == context["strategy_version"],
                "Signal version does not match the pinned deployment",
            ),
            (signal["timeframe"] == context["timeframe"], "Signal timeframe does not match the pinned deployment"),
            (
                signal["configuration_snapshot"] == context["configuration_snapshot"],
                "Signal configuration does not match the pinned deployment",
            ),
            (signal["symbol"] == order.symbol, "Order symbol does not match its signal"),
            (signal["symbol"] in context["universe_symbols"], "Signal symbol is not in the pinned universe"),
            (signal["status"] == "STRONG_BUY", "Only an active STRONG_BUY signal can create an entry intent"),
            (order.side == "BUY", "A STRONG_BUY signal can only create a BUY entry intent"),
        )
        failures.extend(message for valid, message in checks if not valid)
        return failures

    def _risk_failures(
        self, context: Mapping[str, Any], order: OrderRequest, reference_price: Decimal, notional: Decimal
    ) -> list[str]:
        now = self.clock()
        signal = context["signal"]
        snapshot = self.repository.risk_snapshot(str(context["live_deployment_id"]), order.symbol, now=now)
        failures: list[str] = []
        allowlists = (
            (order.symbol, context["symbol_allowlist"], "Symbol is not allowed by the live risk policy"),
            (context["market"], context["market_allowlist"], "Market is not allowed by the live risk policy"),
            (context["strategy_id"], context["strategy_allowlist"], "Strategy is not allowed by the live risk policy"),
            (context["timeframe"], context["timeframe_allowlist"], "Timeframe is not allowed by the live risk policy"),
        )
        failures.extend(message for value, allowed, message in allowlists if value not in allowed)
        if notional > Decimal(context["max_order_value"]):
            failures.append("Maximum order value would be exceeded")
        if snapshot["positionValue"] + notional > Decimal(context["max_position_value"]):
            failures.append("Maximum position value would be exceeded")
        if snapshot["totalExposure"] + notional > Decimal(context["max_total_exposure"]):
            failures.append("Maximum total exposure would be exceeded")
        if snapshot["openPositions"] >= context["max_open_positions"] and snapshot["positionValue"] == 0:
            failures.append("Maximum open positions would be exceeded")
        if snapshot["dailyTrades"] >= context["max_daily_trades"]:
            failures.append("Maximum daily trades would be exceeded")
        if snapshot["dailyLoss"] >= Decimal(context["max_daily_loss"]):
            failures.append("Maximum daily loss has been reached")
        effective_price = order.price or reference_price
        deviation = abs(effective_price - reference_price) / reference_price * Decimal("100")
        if deviation > Decimal(context["max_price_deviation_pct"]):
            failures.append("Order price deviation exceeds the live risk policy")
        created_at = signal["created_at"]
        if now - created_at > timedelta(seconds=context["max_signal_age_seconds"]):
            failures.append("Signal is stale")
        if now - signal["candle_timestamp"] > timedelta(seconds=context["max_candle_age_seconds"]):
            failures.append("Source candle is stale")
        if signal["candle_timestamp"] >= now:
            failures.append("Source candle is not complete")
        if context["market"] == "NSE" and not nse_session_is_open(now):
            failures.append("NSE trading session is closed")
        return failures


class GateFailure(ValueError):
    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


def _intent_key(deployment_id: str, signal_id: str, order: Mapping[str, Any]) -> str:
    canonical = json.dumps(order, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(f"{deployment_id}|{signal_id}|{canonical}".encode()).hexdigest()


def _balance_failure(
    rows: list[Mapping[str, Any]], provider: str, order: OrderRequest, notional: Decimal
) -> str | None:
    quote = str(order.provider_fields.get("quoteCurrency", "INR" if provider == "DHAN" else "USDT")).upper()
    candidates: list[Any] = []
    for row in rows:
        if provider == "DHAN":
            candidates.extend(row.get(key) for key in ("availabelBalance", "availableBalance", "withdrawableBalance"))
        elif provider == "OKX":
            details = row.get("details", [])
            if isinstance(details, list):
                candidates.extend(
                    item.get("availBal") for item in details if isinstance(item, dict) and item.get("ccy") == quote
                )
        elif str(row.get("currency", row.get("currencySymbol", ""))).upper() == quote:
            candidates.extend(row.get(key) for key in ("available", "availableBalance"))
    for candidate in candidates:
        if candidate in (None, ""):
            continue
        try:
            if Decimal(str(candidate)) >= notional:
                return None
        except InvalidOperation:
            continue
    return "Available balance could not be verified or is insufficient"


def _account_risk_failures(
    rows: list[Mapping[str, Any]], provider: str, maximum_daily_loss: Decimal
) -> list[str]:
    if not rows:
        return []
    fields = {
        "DHAN": ("realizedProfit", "unrealizedProfit", "dayPnl"),
        "OKX": ("realizedPnl", "upl"),
        "VALR": ("realizedPnl", "unrealizedPnl", "profitLoss"),
    }[provider]
    values: list[Decimal] = []
    for row in rows:
        present = [row.get(field) for field in fields if row.get(field) not in (None, "")]
        for value in present:
            try:
                values.append(Decimal(str(value)))
            except InvalidOperation:
                return ["Provider account loss could not be verified"]
    if rows and not values:
        return ["Provider account loss could not be verified"]
    if sum(values, Decimal("0")) <= -maximum_daily_loss:
        return ["Maximum daily loss has been reached"]
    return []


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}
