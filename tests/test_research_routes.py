from __future__ import annotations

import unittest
import uuid
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

from backend.api.research_routes import (
    ResearchPreviewRequest,
    ResearchServices,
    ResearchSubmissionRequest,
    create_research_router,
)
from backend.backtest.jobs import BacktestQueueFull
from backend.strategies import STRATEGIES
from backend.strategies.source_v2 import starter_source, validate_source
from fastapi import HTTPException


class FakeReservation:
    def __init__(self, runner: FakeRunner, count: int) -> None:
        self.runner = runner
        self.count = count

    def __enter__(self) -> FakeReservation:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def submit(self, requests) -> None:
        if len(requests) != self.count:
            raise AssertionError("reservation count mismatch")
        self.runner.submitted.extend(requests)


class FakeRunner:
    def __init__(self) -> None:
        self.reservations: list[int] = []
        self.submitted = []
        self.cancelled: list[str] = []

    def reserve(self, count: int) -> FakeReservation:
        self.reservations.append(count)
        return FakeReservation(self, count)

    def cancel(self, run_id: str) -> dict[str, Any]:
        if run_id not in self.cancelled:
            self.cancelled.append(run_id)
        return {"runId": run_id, "cancelRequested": True}


class FakeExperiments:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.by_key: dict[str, dict[str, Any]] = {}

    def create_generated(self, **values: Any) -> tuple[dict[str, Any], bool]:
        key = values["idempotency_key"]
        if key in self.by_key:
            return deepcopy(self.by_key[key]), False
        variants = []
        for position, generated in enumerate(values["variants"], start=1):
            run_id = str(uuid.uuid4())
            variants.append(
                {
                    "variantId": str(uuid.uuid4()),
                    "position": position,
                    "name": generated["name"],
                    "configuration": deepcopy(generated["configuration"]),
                    "execution": deepcopy(generated["execution"]),
                    "run": {
                        "runId": run_id,
                        "status": "QUEUED",
                        "metrics": None,
                        "symbolsCompleted": 0,
                    },
                }
            )
        row = {
            "experimentId": str(uuid.uuid4()),
            "name": values["name"],
            "mode": values["generation_mode"],
            "market": values["market"],
            "strategyId": values["strategy_id"],
            "strategyVersion": values["strategy_version"],
            "strategySourceId": values["strategy_source_id"],
            "timeframe": values["timeframe"],
            "symbols": list(values["symbols"]),
            "startDate": values["start_date"].isoformat(),
            "endDate": values["end_date"].isoformat(),
            "sweepDefinitions": deepcopy(values["sweep_definitions"]),
            "previewHash": values["preview_hash"],
            "variantCount": len(variants),
            "symbolCount": len(values["symbols"]),
            "estimatedSymbolRuns": len(variants) * len(values["symbols"]),
            "status": "QUEUED",
            "variantStatusCounts": {"QUEUED": len(variants)},
            "variants": variants,
            "createdAt": datetime.now(UTC).isoformat(),
        }
        self.rows.append(row)
        self.by_key[key] = row
        return deepcopy(row), True

    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        row = self.by_key.get(key)
        return deepcopy(row) if row else None

    def list(
        self,
        market: str | None = None,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return [
            deepcopy(row)
            for row in self.rows
            if market is None or row["market"] == market
        ][:limit]

    def get(self, experiment_id: str) -> dict[str, Any]:
        return deepcopy(
            next(row for row in self.rows if row["experimentId"] == experiment_id)
        )


class FakeStrategySources:
    def __init__(self, *, status: str = "VALIDATED") -> None:
        source = starter_source()
        validation = validate_source(source)
        self.row = {
            "sourceId": str(uuid.uuid4()),
            "sourceCode": source,
            "strategyId": validation.manifest["strategyId"],
            "strategyVersion": validation.manifest["version"],
            "manifest": validation.manifest,
            "validation": validation.public(),
            "status": status,
        }

    def get(self, source_id: str) -> dict[str, Any]:
        if source_id != self.row["sourceId"]:
            raise KeyError(source_id)
        return deepcopy(self.row)


class FakeUniverses:
    def __init__(self, *, active: bool = True, symbols: list[str] | None = None) -> None:
        self.row = {
            "universeId": str(uuid.uuid4()),
            "market": "NSE",
            "name": "Active NSE watchlist",
            "active": active,
        }
        self.symbol_values = ["TCS", "INFY"] if symbols is None else symbols

    def get(self, universe_id: str) -> dict[str, Any]:
        if universe_id != self.row["universeId"]:
            raise KeyError(universe_id)
        return deepcopy(self.row)

    def symbols(self, universe_id: str, *, market: str) -> list[str]:
        if universe_id != self.row["universeId"] or market != self.row["market"]:
            raise KeyError(universe_id)
        return list(self.symbol_values)


def endpoints(router):
    return {
        f"{method} {route.path}": route.endpoint
        for route in router.routes
        for method in route.methods
    }


class ResearchRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = FakeExperiments()
        self.runner = FakeRunner()
        self.services = ResearchServices(
            registry=STRATEGIES,
            experiments=lambda: self.repository,
            runner=lambda: self.runner,
        )
        self.api = endpoints(create_research_router(self.services))

    def grid_payload(self, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": "RSI sweep",
            "mode": "GRID",
            "market": "NSE",
            "strategyId": "rsi_dip_ladder_v1",
            "strategyVersion": "1.0.0",
            "symbols": ["TCS", "INFY"],
            "timeframe": "5m",
            "startDate": date(2026, 8, 1),
            "endDate": date(2026, 8, 31),
            "parameters": [
                {
                    "section": "strategy",
                    "parameter": "rsi_low",
                    "type": "number",
                    "method": "EXPLICIT_VALUES",
                    "values": [25, 30],
                },
                {
                    "section": "execution",
                    "parameter": "targetPct",
                    "type": "number",
                    "method": "EXPLICIT_VALUES",
                    "values": [0.5, 0.75],
                },
            ],
        }
        payload.update(overrides)
        return payload

    def preview(self, **overrides: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = self.grid_payload(**overrides)
        result = self.api["POST /v2/research/experiments/preview"](
            ResearchPreviewRequest(**payload)
        )
        return payload, result

    def test_preview_is_read_only_and_reports_deterministic_workload(self) -> None:
        _, first = self.preview()
        _, second = self.preview(symbols=["INFY", "TCS", "INFY"])
        self.assertEqual(first["previewHash"], second["previewHash"])
        self.assertEqual(first["variantCount"], 4)
        self.assertEqual(first["symbolCount"], 2)
        self.assertEqual(first["estimatedSymbolRuns"], 8)
        self.assertEqual(
            first["variants"][0]["name"],
            "rsi_low=25 · targetPct=0.5",
        )
        self.assertEqual(self.repository.rows, [])
        self.assertEqual(self.runner.reservations, [])
        self.assertEqual(self.runner.submitted, [])

    def test_submission_regenerates_exact_variants_and_is_idempotent(self) -> None:
        payload, preview = self.preview()
        submission = ResearchSubmissionRequest(
            **payload,
            previewHash=preview["previewHash"],
            idempotencyKey="research:test:stable-key",
        )
        first = self.api["POST /v2/research/experiments/from-preview"](submission)
        second = self.api["POST /v2/research/experiments/from-preview"](submission)
        self.assertEqual(first["experimentId"], second["experimentId"])
        self.assertEqual(len(self.repository.rows), 1)
        self.assertEqual(self.runner.reservations, [4])
        self.assertEqual(len(self.runner.submitted), 4)
        self.assertEqual(
            [item.configuration for item in self.runner.submitted],
            [variant["configuration"] for variant in preview["variants"]],
        )
        self.assertEqual(
            [item.run_id for item in self.runner.submitted],
            [variant["run"]["runId"] for variant in first["variants"]],
        )

    def test_bounded_queue_rejection_creates_no_rows(self) -> None:
        payload, preview = self.preview()

        class FullRunner(FakeRunner):
            def reserve(self, count: int) -> FakeReservation:
                raise BacktestQueueFull(f"queue full for {count} variants")

        api = endpoints(
            create_research_router(
                ResearchServices(
                    registry=STRATEGIES,
                    experiments=lambda: self.repository,
                    runner=FullRunner,
                )
            )
        )
        with self.assertRaises(HTTPException) as caught:
            api["POST /v2/research/experiments/from-preview"](
                ResearchSubmissionRequest(
                    **payload,
                    previewHash=preview["previewHash"],
                    idempotencyKey="research:test:queue-full",
                )
            )
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(self.repository.rows, [])

    def test_stale_hash_and_invalid_variant_create_nothing(self) -> None:
        payload, preview = self.preview()
        stale = ResearchSubmissionRequest(
            **payload,
            previewHash="sha256:" + ("0" * 64),
            idempotencyKey="research:test:stale",
        )
        with self.assertRaises(HTTPException) as caught:
            self.api["POST /v2/research/experiments/from-preview"](stale)
        self.assertEqual(caught.exception.status_code, 409)

        manual = ResearchPreviewRequest(
            name="Atomic validation",
            mode="MANUAL",
            market="NSE",
            strategyId="rsi_dip_ladder_v1",
            strategyVersion="1.0.0",
            symbols=["TCS"],
            timeframe="5m",
            startDate=date(2026, 8, 1),
            endDate=date(2026, 8, 31),
            variants=[
                {"name": "valid", "configuration": {"rsi_low": 25}},
                {"name": "invalid", "configuration": {"unknown": 1}},
            ],
        )
        with self.assertRaises(HTTPException) as invalid:
            self.api["POST /v2/research/experiments/preview"](manual)
        self.assertEqual(invalid.exception.status_code, 422)
        self.assertIn("Unknown strategy", invalid.exception.detail)
        self.assertEqual(self.repository.rows, [])
        self.assertEqual(self.runner.submitted, [])
        self.assertNotEqual(preview["previewHash"], stale.previewHash)

    def test_symbol_run_limit_is_rejected_before_mutation(self) -> None:
        parameters = [
            {
                "section": "strategy",
                "parameter": "rsi_low",
                "type": "number",
                "method": "EXPLICIT_VALUES",
                "values": list(range(10, 20)),
            },
            {
                "section": "execution",
                "parameter": "targetPct",
                "type": "number",
                "method": "EXPLICIT_VALUES",
                "values": list(range(1, 11)),
            },
        ]
        symbols = [f"SYM{index}" for index in range(201)]
        with self.assertRaises(HTTPException) as caught:
            self.preview(parameters=parameters, symbols=symbols)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("20,100 symbol-runs", caught.exception.detail)
        self.assertEqual(self.repository.rows, [])
        self.assertEqual(self.runner.reservations, [])

    def test_strategy_identity_timeframe_and_date_range_are_validated(self) -> None:
        for overrides in (
            {"strategyVersion": "0.0.0"},
            {"timeframe": "2m"},
            {"market": "CRYPTO", "symbols": ["BTC-USDT"], "timeframe": "2m"},
            {"startDate": date(2026, 9, 1), "endDate": date(2026, 8, 1)},
            {"symbols": ["   "]},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(HTTPException):
                self.preview(**overrides)
        self.assertEqual(self.repository.rows, [])

    def test_active_saved_watchlist_is_snapshotted_and_empty_or_inactive_is_rejected(self) -> None:
        universe = FakeUniverses()
        api = endpoints(
            create_research_router(
                ResearchServices(
                    registry=STRATEGIES,
                    experiments=lambda: self.repository,
                    runner=lambda: self.runner,
                    universes=lambda: universe,
                )
            )
        )
        payload = self.grid_payload(
            symbols=[],
            universeId=universe.row["universeId"],
        )
        preview = api["POST /v2/research/experiments/preview"](
            ResearchPreviewRequest(**payload)
        )
        self.assertEqual(preview["symbols"], ["INFY", "TCS"])
        self.assertEqual(preview["universeName"], "Active NSE watchlist")

        for invalid in (
            FakeUniverses(active=False),
            FakeUniverses(symbols=[]),
        ):
            invalid_api = endpoints(
                create_research_router(
                    ResearchServices(
                        registry=STRATEGIES,
                        experiments=lambda: self.repository,
                        runner=lambda: self.runner,
                        universes=lambda invalid=invalid: invalid,
                    )
                )
            )
            invalid_payload = self.grid_payload(
                symbols=[],
                universeId=invalid.row["universeId"],
            )
            with self.assertRaises(HTTPException) as caught:
                invalid_api["POST /v2/research/experiments/preview"](
                    ResearchPreviewRequest(**invalid_payload)
                )
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(self.repository.rows, [])

    def test_strategy_v2_requires_exact_validated_immutable_source(self) -> None:
        sources = FakeStrategySources()
        services = ResearchServices(
            registry=STRATEGIES,
            experiments=lambda: self.repository,
            runner=lambda: self.runner,
            sources=lambda: sources,
        )
        api = endpoints(create_research_router(services))
        payload = {
            "name": "V2 sweep",
            "mode": "GRID",
            "market": "NSE",
            "strategyId": sources.row["strategyId"],
            "strategyVersion": sources.row["strategyVersion"],
            "strategySourceId": sources.row["sourceId"],
            "symbols": ["TCS"],
            "timeframe": "5m",
            "startDate": date(2026, 8, 1),
            "endDate": date(2026, 8, 2),
            "parameters": [
                {
                    "section": "strategy",
                    "parameter": "rsi_length",
                    "type": "integer",
                    "method": "EXPLICIT_VALUES",
                    "values": [10, 14],
                }
            ],
        }
        preview = api["POST /v2/research/experiments/preview"](
            ResearchPreviewRequest(**payload)
        )
        self.assertEqual(preview["strategySourceId"], sources.row["sourceId"])
        with self.assertRaises(HTTPException) as identity:
            api["POST /v2/research/experiments/preview"](
                ResearchPreviewRequest(**{**payload, "strategyId": "wrong"})
            )
        self.assertEqual(identity.exception.status_code, 422)

        archived = FakeStrategySources(status="ARCHIVED")
        archived_api = endpoints(
            create_research_router(
                ResearchServices(
                    registry=STRATEGIES,
                    experiments=lambda: self.repository,
                    runner=lambda: self.runner,
                    sources=lambda: archived,
                )
            )
        )
        archived_payload = {
            **payload,
            "strategyId": archived.row["strategyId"],
            "strategyVersion": archived.row["strategyVersion"],
            "strategySourceId": archived.row["sourceId"],
        }
        with self.assertRaises(HTTPException) as rejected:
            archived_api["POST /v2/research/experiments/preview"](
                ResearchPreviewRequest(**archived_payload)
            )
        self.assertEqual(rejected.exception.status_code, 409)

    def test_experiment_cancellation_requests_each_active_child_once(self) -> None:
        payload, preview = self.preview()
        created = self.api["POST /v2/research/experiments/from-preview"](
            ResearchSubmissionRequest(
                **payload,
                previewHash=preview["previewHash"],
                idempotencyKey="research:test:cancel",
            )
        )
        created["variants"][0]["run"]["status"] = "COMPLETE"
        self.repository.rows[0] = created
        self.repository.by_key["research:test:cancel"] = created
        endpoint = self.api["DELETE /v2/research/experiments/{experiment_id}"]
        endpoint(created["experimentId"])
        endpoint(created["experimentId"])
        expected = [item["run"]["runId"] for item in created["variants"][1:]]
        self.assertEqual(self.runner.cancelled, expected)


if __name__ == "__main__":
    unittest.main()
