from __future__ import annotations

import unittest

from backend.api.strategy_studio_routes import StrategySourceRequest, create_strategy_studio_router
from backend.strategies.source_v2 import starter_source, validate_source
from fastapi import HTTPException

from test_backtest_routes import endpoints


class FakeSources:
    def __init__(self) -> None:
        self.rows = []

    def create(self, *, source_code, code_hash, manifest, validation):
        row = {"sourceId": "source-1", "sourceCode": source_code, "codeHash": code_hash, "manifest": manifest, "validation": validation, "strategyId": manifest["strategyId"], "strategyVersion": manifest["version"], "name": manifest["name"], "status": "VALIDATED"}
        self.rows.append(row)
        return row

    def list(self, market=None):
        return [row for row in self.rows if market is None or market in row["manifest"]["supportedMarkets"]]

    def get(self, source_id):
        if source_id != "source-1":
            raise KeyError(source_id)
        return self.rows[0]


class StrategySourceValidationTests(unittest.TestCase):
    def test_template_is_valid_and_extracts_manifest_without_execution(self) -> None:
        result = validate_source(starter_source())
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.manifest["strategyId"], "my_strategy_v2")
        self.assertEqual(result.manifest["supportedMarkets"], ["NSE", "CRYPTO"])

    def test_rejects_unsafe_imports_calls_and_lookahead(self) -> None:
        source = starter_source().replace('"""OpenDelta Strategy V2 example."""', "import os").replace('return "HOLD"', 'open("secret"); close.shift(-1); return "HOLD"')
        result = validate_source(source)
        self.assertFalse(result.valid)
        self.assertTrue(any("Import 'os'" in error for error in result.errors))
        self.assertTrue(any("Call 'open'" in error for error in result.errors))
        self.assertTrue(any("look-ahead" in error for error in result.errors))

    def test_requires_contract_and_literal_metadata(self) -> None:
        result = validate_source("STRATEGY = dict(id='bad')\n")
        self.assertFalse(result.valid)
        self.assertTrue(any("literal" in error for error in result.errors))
        self.assertTrue(any("Missing required" in error for error in result.errors))

    def test_parameters_are_limited_to_supported_flat_defaults(self) -> None:
        nested = starter_source().replace(
            '"parameters": {"rsi_length": 14, "rsi_low": 30}',
            '"parameters": {"nested": {"length": 14}}',
        )
        result = validate_source(nested)
        self.assertFalse(result.valid)
        self.assertTrue(any("whole-number array" in error for error in result.errors))

    def test_whole_number_array_parameter_is_supported(self) -> None:
        ladder = starter_source().replace(
            '"parameters": {"rsi_length": 14, "rsi_low": 30}',
            '"parameters": {"quantities": [1, 2, 5]}',
        )
        result = validate_source(ladder)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.manifest["parameters"]["quantities"], [1, 2, 5])


class StrategyStudioRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = FakeSources()
        self.api = endpoints(create_strategy_studio_router(lambda: self.sources))

    def test_validate_save_and_list(self) -> None:
        request = StrategySourceRequest(sourceCode=starter_source())
        self.assertTrue(self.api["POST /v2/strategy-studio/validate"](request)["valid"])
        saved = self.api["POST /v2/strategy-studio/sources"](request)
        self.assertEqual(saved["strategyVersion"], "1.0.0")
        self.assertEqual(len(self.api["GET /v2/strategy-studio/sources"](market="CRYPTO")["sources"]), 1)

    def test_invalid_source_is_not_saved(self) -> None:
        with self.assertRaises(HTTPException) as error:
            self.api["POST /v2/strategy-studio/sources"](StrategySourceRequest(sourceCode="x = 1"))
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.sources.rows, [])
