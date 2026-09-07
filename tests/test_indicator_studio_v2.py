from __future__ import annotations

import unittest

from backend.api.indicator_studio_routes import (
    CandleColumns,
    IndicatorPreviewRequest,
    IndicatorSourceRequest,
    create_indicator_studio_router,
)
from backend.indicators.source_v2 import starter_source, validate_source
from backend.strategies.runner_v2 import evaluate_indicator_payload
from fastapi import HTTPException

from test_backtest_routes import endpoints


class FakeSources:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def create(self, *, source_code, code_hash, manifest, validation):
        row = {
            "sourceId": "source-1", "sourceCode": source_code, "codeHash": code_hash,
            "manifest": manifest, "validation": validation, "indicatorId": manifest["indicatorId"],
            "indicatorVersion": manifest["version"], "name": manifest["name"], "status": "VALIDATED",
        }
        self.rows.append(row)
        return row

    def list(self, *, status=None):
        return [row for row in self.rows if status is None or row["status"] == status]

    def get(self, source_id):
        if source_id != "source-1" or not self.rows:
            raise KeyError(source_id)
        return self.rows[0]

    def archive(self, source_id):
        row = self.get(source_id)
        row["status"] = "ARCHIVED"
        return row


class FakeRunner:
    def evaluate(self, payload):
        return evaluate_indicator_payload(dict(payload))


def candles(count: int = 25) -> CandleColumns:
    return CandleColumns(
        timestamp=[f"2026-01-01T00:{minute:02d}:00Z" for minute in range(count)],
        open=[float(value) for value in range(1, count + 1)],
        high=[float(value + 1) for value in range(1, count + 1)],
        low=[float(value - 1) for value in range(1, count + 1)],
        close=[float(value) for value in range(1, count + 1)],
        volume=[100.0] * count,
    )


class IndicatorSourceValidationTests(unittest.TestCase):
    def test_template_is_valid(self) -> None:
        result = validate_source(starter_source())
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.manifest["indicatorId"], "simple_moving_average")
        self.assertEqual(result.manifest["outputs"][0]["pane"], "OVERLAY")

    def test_rejects_unsafe_source_and_invalid_outputs(self) -> None:
        source = starter_source().replace('"""OpenDelta Indicator Studio example: simple moving average."""', "import requests")
        source = source.replace('"name": "sma"', '"name": "Bad Name"')
        result = validate_source(source)
        self.assertFalse(result.valid)
        self.assertTrue(any("requests" in error for error in result.errors))
        self.assertTrue(any("output names" in error for error in result.errors))

    def test_isolated_contract_returns_declared_rows(self) -> None:
        result = evaluate_indicator_payload(
            {
                "sourceCode": starter_source(), "market": "NSE", "symbol": "TCS", "timeframe": "5m",
                "params": {"length": 5}, "candles": candles().model_dump(),
            }
        )
        self.assertEqual(len(result["rows"]), 25)
        self.assertIsNone(result["rows"][18]["sma"])
        self.assertEqual(result["rows"][-1]["sma"], 23.0)


class IndicatorStudioRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = FakeSources()
        self.api = endpoints(create_indicator_studio_router(lambda: self.sources, lambda: FakeRunner()))

    def test_validate_save_preview_and_archive(self) -> None:
        request = IndicatorSourceRequest(sourceCode=starter_source())
        self.assertTrue(self.api["POST /v2/indicator-studio/validate"](request)["valid"])
        saved = self.api["POST /v2/indicator-studio/sources"](request)
        preview = self.api["POST /v2/indicator-studio/sources/{source_id}/preview"](
            "source-1", IndicatorPreviewRequest(market="NSE", symbol="TCS", timeframe="5m", candles=candles())
        )
        self.assertEqual(preview["rows"][-1]["sma"], 15.5)
        archived = self.api["POST /v2/indicator-studio/sources/{source_id}/archive"]("source-1")
        self.assertEqual(archived["status"], "ARCHIVED")
        self.assertEqual(saved["indicatorVersion"], "1.0.0")

    def test_preview_rejects_mismatched_candle_columns(self) -> None:
        self.api["POST /v2/indicator-studio/sources"](IndicatorSourceRequest(sourceCode=starter_source()))
        request = IndicatorPreviewRequest(market="NSE", symbol="TCS", timeframe="5m", candles=candles())
        request.candles.volume.pop()
        with self.assertRaises(HTTPException) as error:
            self.api["POST /v2/indicator-studio/sources/{source_id}/preview"]("source-1", request)
        self.assertEqual(error.exception.status_code, 422)
