from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from backend.live.adapters import (
    DhanOrderAdapter,
    OkxOrderAdapter,
    OrderRequest,
    ProviderFill,
    ProviderMutationDisabled,
    ProviderOrder,
    ProviderResponseUncertain,
    ValrOrderAdapter,
)
from backend.live.service import GateFailure, LiveExecutionConfig, LiveExecutionService

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


class FakeRepository:
    def __init__(self, *, status: str = "ACTIVE") -> None:
        self.context = self._context(status)
        self.intents: dict[str, dict[str, Any]] = {}
        self.transitions: list[str] = []
        self.stops: list[dict[str, Any]] = []
        self.findings: list[str] = []

    @staticmethod
    def _context(status: str) -> dict[str, Any]:
        configuration = {"rsi_low": 30}
        return {
            "live_deployment_id": "00000000-0000-0000-0000-000000000001",
            "provider": "OKX",
            "connection_id": "00000000-0000-0000-0000-000000000002",
            "market": "CRYPTO",
            "strategy_id": "rsi_dip_ladder",
            "strategy_version": "1.0.0",
            "strategy_source_id": None,
            "config_id": "00000000-0000-0000-0000-000000000003",
            "universe_id": "00000000-0000-0000-0000-000000000004",
            "timeframe": "5m",
            "configuration_snapshot": configuration,
            "execution_settings": {"targetPct": 1},
            "status": status,
            "connection_status": "CONNECTED",
            "connection_disabled": False,
            "connection_permissions": {"read": True, "trade": True, "withdrawal": False},
            "last_test_success": True,
            "last_tested_at": NOW - timedelta(minutes=1),
            "max_order_value": Decimal("10000"),
            "max_position_value": Decimal("20000"),
            "max_total_exposure": Decimal("50000"),
            "max_open_positions": 5,
            "max_daily_trades": 10,
            "max_daily_loss": Decimal("5000"),
            "max_price_deviation_pct": Decimal("2"),
            "max_signal_age_seconds": 300,
            "max_candle_age_seconds": 600,
            "symbol_allowlist": ["BTC-USDT"],
            "market_allowlist": ["CRYPTO"],
            "strategy_allowlist": ["rsi_dip_ladder"],
            "timeframe_allowlist": ["5m"],
            "universe_symbols": ["BTC-USDT"],
            "signal": {
                "signal_id": "00000000-0000-0000-0000-000000000005",
                "market": "CRYPTO",
                "strategy_id": "rsi_dip_ladder",
                "strategy_version": "1.0.0",
                "timeframe": "5m",
                "symbol": "BTC-USDT",
                "status": "STRONG_BUY",
                "configuration_snapshot": configuration,
                "signal_price": 100,
                "last_price": 100,
                "created_at": NOW - timedelta(seconds=30),
                "candle_timestamp": NOW - timedelta(minutes=5),
            },
        }

    def list_deployments(self):
        return []

    def list_risk_policies(self):
        return []

    def eligible_approvals(self):
        return []

    def list_stops(self):
        return self.stops

    def list_intents(self, *, limit=100):
        return list(self.intents.values())[:limit]

    def deployment_context(self, _deployment_id):
        return self.context

    def signal_context(self, _deployment_id, _signal_id):
        return self.context

    def set_deployment_status(self, _deployment_id, status, *, actor):
        del actor
        self.context["status"] = status
        return {"status": status}

    def active_stops(self, **_):
        return [item for item in self.stops if item["active"]]

    def risk_snapshot(self, *_args, **_kwargs):
        return {
            "dailyTrades": 0,
            "openPositions": 0,
            "totalExposure": Decimal("0"),
            "positionValue": Decimal("0"),
            "dailyLoss": Decimal("0"),
        }

    def create_intent(self, *, idempotency_key, client_order_id, requested_order, **_):
        if idempotency_key in self.intents:
            return self.intents[idempotency_key], False
        row = {
            "intentId": idempotency_key[:32],
            "idempotencyKey": idempotency_key,
            "liveDeploymentId": self.context["live_deployment_id"],
            "signalId": self.context["signal"]["signal_id"],
            "provider": self.context["provider"],
            "market": self.context["market"],
            "strategyId": self.context["strategy_id"],
            "strategyVersion": self.context["strategy_version"],
            "symbol": requested_order["symbol"],
            "clientOrderId": client_order_id,
            "providerOrderId": None,
            "state": "CREATED",
            "reconciliationStatus": "NOT_REQUIRED",
            "requestedOrder": requested_order,
            "blockedReasons": [],
        }
        self.intents[idempotency_key] = row
        return row, True

    def transition(self, intent_id, state, **values):
        row = next(item for item in self.intents.values() if item["intentId"] == intent_id)
        row["state"] = state
        row["blockedReasons"] = values.get("blocked_reasons") or row["blockedReasons"]
        row["reconciliationStatus"] = values.get("reconciliation_status") or row["reconciliationStatus"]
        provider_order = values.get("provider_order")
        if provider_order:
            row["providerOrderId"] = provider_order.provider_order_id
        self.transitions.append(state)
        return row

    def get_intent(self, intent_id):
        return next(item for item in self.intents.values() if item["intentId"] == intent_id)

    def intents_requiring_reconciliation(self, *, limit):
        return list(self.intents.values())[:limit]

    def save_fill(self, *_):
        pass

    def add_finding(self, *, finding_type, **_):
        self.findings.append(finding_type)


class FakeAdapter:
    provider = "OKX"

    def __init__(self, *, uncertain: bool = False) -> None:
        self.submit_count = 0
        self.cancel_count = 0
        self.uncertain = uncertain

    def fetch_balances(self):
        return [{"details": [{"ccy": "USDT", "availBal": "100000"}]}]

    def fetch_positions(self):
        return []

    def submit_order(self, order, client_order_id):
        del order, client_order_id
        self.submit_count += 1
        if self.uncertain:
            raise ProviderResponseUncertain("uncertain")
        return ProviderOrder("provider-1", "ACKNOWLEDGED", {"ordId": "provider-1"})

    def cancel_order(self, provider_order_id, **_):
        self.cancel_count += 1
        return ProviderOrder(provider_order_id, "CANCEL_REQUESTED", {})

    def query_order(self, provider_order_id, **_):
        return ProviderOrder(provider_order_id, "FILLED", {})

    def fetch_fills(self, **_):
        return [ProviderFill("fill-1", Decimal("1"), Decimal("100"))]


def enabled_config() -> LiveExecutionConfig:
    return LiveExecutionConfig(True, True, "PRODUCTION", ("PRODUCTION",), 900)


def order(**overrides: Any) -> OrderRequest:
    values = {
        "symbol": "BTC-USDT",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": Decimal("1"),
        "price": Decimal("100"),
        "provider_fields": {"quoteCurrency": "USDT"},
    }
    values.update(overrides)
    return OrderRequest(**values)


class LiveExecutionGateTests(unittest.TestCase):
    def test_environment_defaults_disable_every_live_mutation(self) -> None:
        config = LiveExecutionConfig.from_environment({})
        self.assertFalse(config.live_trading_enabled)
        self.assertFalse(config.deployment_permission)
        self.assertFalse(config.environment_allowed)
        self.assertEqual(config.public()["defaultState"], "Live trading disabled")

    def test_disabled_submission_persists_blocked_intent_without_calling_adapter(self) -> None:
        repository = FakeRepository()
        adapter = FakeAdapter()
        service = LiveExecutionService(
            repository,
            adapters=lambda _: adapter,
            config=LiveExecutionConfig(),
            dhan_status=lambda: {"configured": False},
            clock=lambda: NOW,
        )
        result = service.submit_order(
            repository.context["live_deployment_id"], repository.context["signal"]["signal_id"], order()
        )
        self.assertEqual(result["state"], "BLOCKED")
        self.assertEqual(adapter.submit_count, 0)
        self.assertIn("Global LIVE_TRADING_ENABLED flag is false", result["blockedReasons"])

    def test_safe_submission_is_idempotent_and_provider_receives_one_order(self) -> None:
        repository = FakeRepository()
        adapter = FakeAdapter()
        service = LiveExecutionService(
            repository,
            adapters=lambda _: adapter,
            config=enabled_config(),
            dhan_status=lambda: {"configured": True},
            clock=lambda: NOW,
        )
        first = service.submit_order(
            repository.context["live_deployment_id"], repository.context["signal"]["signal_id"], order()
        )
        second = service.submit_order(
            repository.context["live_deployment_id"], repository.context["signal"]["signal_id"], order()
        )
        self.assertEqual(first["idempotencyKey"], second["idempotencyKey"])
        self.assertEqual(first["state"], "ACKNOWLEDGED")
        self.assertEqual(adapter.submit_count, 1)

    def test_risk_and_identity_failures_are_auditable_blocked_intents(self) -> None:
        repository = FakeRepository()
        repository.context["max_order_value"] = Decimal("50")
        repository.context["signal"]["configuration_snapshot"] = {"rsi_low": 99}
        adapter = FakeAdapter()
        service = LiveExecutionService(
            repository,
            adapters=lambda _: adapter,
            config=enabled_config(),
            dhan_status=lambda: {"configured": True},
            clock=lambda: NOW,
        )
        result = service.submit_order(
            repository.context["live_deployment_id"], repository.context["signal"]["signal_id"], order()
        )
        self.assertEqual(result["state"], "BLOCKED")
        self.assertTrue(any("configuration" in reason.lower() for reason in result["blockedReasons"]))
        self.assertTrue(any("order value" in reason.lower() for reason in result["blockedReasons"]))
        self.assertEqual(adapter.submit_count, 0)

    def test_unknown_provider_outcome_is_never_assumed_failed(self) -> None:
        repository = FakeRepository()
        adapter = FakeAdapter(uncertain=True)
        service = LiveExecutionService(
            repository,
            adapters=lambda _: adapter,
            config=enabled_config(),
            dhan_status=lambda: {"configured": True},
            clock=lambda: NOW,
        )
        result = service.submit_order(
            repository.context["live_deployment_id"], repository.context["signal"]["signal_id"], order()
        )
        self.assertEqual((result["state"], result["reconciliationStatus"]), ("UNKNOWN", "REQUIRED"))

    def test_withdrawal_permission_stale_test_and_emergency_stop_fail_activation(self) -> None:
        repository = FakeRepository(status="DRAFT")
        repository.context["connection_permissions"]["withdrawal"] = True
        repository.context["last_tested_at"] = NOW - timedelta(hours=2)
        repository.stops.append({"active": True})
        service = LiveExecutionService(
            repository,
            adapters=lambda _: FakeAdapter(),
            config=enabled_config(),
            dhan_status=lambda: {"configured": True},
            clock=lambda: NOW,
        )
        with self.assertRaises(GateFailure) as raised:
            service.activate_deployment(
                repository.context["live_deployment_id"], confirmation="ENABLE LIVE rsi_dip_ladder"
            )
        message = str(raised.exception)
        self.assertIn("Withdrawal permission", message)
        self.assertIn("stale", message)
        self.assertIn("emergency stop", message)

    def test_cancellation_remains_impossible_when_global_flag_is_off(self) -> None:
        repository = FakeRepository()
        adapter = FakeAdapter()
        service = LiveExecutionService(
            repository,
            adapters=lambda _: adapter,
            config=enabled_config(),
            dhan_status=lambda: {"configured": True},
            clock=lambda: NOW,
        )
        intent = service.submit_order(
            repository.context["live_deployment_id"], repository.context["signal"]["signal_id"], order()
        )
        disabled = LiveExecutionService(
            repository,
            adapters=lambda _: adapter,
            config=LiveExecutionConfig(),
            dhan_status=lambda: {"configured": True},
            clock=lambda: NOW,
        )
        with self.assertRaises(GateFailure):
            disabled.cancel_order(intent["intentId"], confirmation=f"CANCEL {intent['clientOrderId']}")
        self.assertEqual(adapter.cancel_count, 0)

    def test_emergency_stop_confirmation_is_exact_and_clear_is_idempotent_contract(self) -> None:
        repository = FakeRepository()
        repository.set_stop = lambda **values: values
        service = LiveExecutionService(
            repository,
            adapters=lambda _: FakeAdapter(),
            config=enabled_config(),
            dhan_status=lambda: {"configured": True},
            clock=lambda: NOW,
        )
        with self.assertRaises(ValueError):
            service.set_emergency_stop(
                scope_type="GLOBAL", scope_key="*", active=True, reason="test", confirmation="STOP"
            )
        saved = service.set_emergency_stop(
            scope_type="GLOBAL", scope_key="*", active=True, reason="test", confirmation="ACTIVATE EMERGENCY STOP"
        )
        self.assertTrue(saved["active"])


class AdapterContractTests(unittest.TestCase):
    def test_all_adapters_reject_mutation_when_disabled(self) -> None:
        adapters = [
            DhanOrderAdapter({"clientId": "1", "accessToken": "token"}),
            OkxOrderAdapter({"apiKey": "key", "apiSecret": "secret", "passphrase": "pass", "environment": "LIVE"}),
            ValrOrderAdapter({"apiKey": "key", "apiSecret": "secret"}),
        ]
        for adapter in adapters:
            with self.subTest(adapter=adapter.provider), self.assertRaises(ProviderMutationDisabled):
                adapter.submit_order(order(provider_fields={"securityId": "1"}), "client-id")

    def test_dhan_contract_uses_correlation_id_and_never_places_a_real_order(self) -> None:
        captured: list[Any] = []

        def transport(request, _timeout, mutation):
            captured.append((request, mutation, json.loads(request.data)))
            return {"orderId": "dhan-1", "orderStatus": "PENDING"}

        adapter = DhanOrderAdapter(
            {"clientId": "123", "accessToken": "token"}, mutations_enabled=True, transport=transport
        )
        result = adapter.submit_order(order(provider_fields={"securityId": "42"}), "client-key")
        self.assertEqual((result.state, captured[0][2]["correlationId"]), ("ACKNOWLEDGED", "client-key"))

    def test_okx_and_valr_contracts_propagate_deterministic_client_ids(self) -> None:
        okx_request: list[Any] = []

        def okx_transport(request, _timeout, _mutation):
            okx_request.append(json.loads(request.data))
            return {"code": "0", "data": [{"ordId": "okx-1"}]}

        okx = OkxOrderAdapter(
            {"apiKey": "key", "apiSecret": "secret", "passphrase": "pass", "environment": "DEMO"},
            mutations_enabled=True,
            transport=okx_transport,
            clock=lambda: NOW,
        )
        self.assertEqual(okx.submit_order(order(), "client-1").provider_order_id, "okx-1")
        self.assertEqual(okx_request[0]["clOrdId"], "client-1")

        valr_request: list[Any] = []

        def valr_transport(request, _timeout, _mutation):
            valr_request.append(json.loads(request.data))
            return {"id": "valr-1"}

        valr = ValrOrderAdapter(
            {"apiKey": "key", "apiSecret": "secret"},
            mutations_enabled=True,
            transport=valr_transport,
            clock_ms=lambda: 1,
        )
        self.assertEqual(valr.submit_order(order(), "client-2").provider_order_id, "valr-1")
        self.assertEqual(valr_request[0]["customerOrderId"], "client-2")


if __name__ == "__main__":
    unittest.main()
