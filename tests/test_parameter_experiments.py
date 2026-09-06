from __future__ import annotations

import unittest

from backend.research.parameter_experiments import generate_variants, preview_hash
from backend.strategies import STRATEGIES


class ParameterExperimentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = STRATEGIES.get("rsi_dip_ladder_v1")

    def generate(self, parameters):
        return generate_variants(
            strategy=self.strategy,
            market="NSE",
            timeframe="5m",
            mode="GRID",
            base_configuration={},
            base_execution={},
            manual_variants=[],
            parameters=parameters,
        )

    def test_explicit_boolean_and_valid_enum_values(self) -> None:
        variants = self.generate(
            [
                {
                    "section": "execution",
                    "parameter": "allowAdditionalBuys",
                    "type": "boolean",
                    "method": "EXPLICIT_VALUES",
                    "values": [True, False],
                },
                {
                    "section": "execution",
                    "parameter": "additionalSizingMode",
                    "type": "enum",
                    "method": "EXPLICIT_VALUES",
                    "values": [
                        "REDUCE_EVERY_NEW_LOT",
                        "FIXED_PERCENTAGE_OF_FIRST_LOT",
                    ],
                },
            ]
        )
        self.assertEqual(len(variants), 4)
        self.assertEqual(
            [item.execution["allowAdditionalBuys"] for item in variants],
            [True, True, False, False],
        )
        self.assertEqual(
            variants[1].execution["additionalSizingMode"],
            "FIXED_PERCENTAGE_OF_FIRST_LOT",
        )

    def test_numeric_range_is_inclusive_and_decimal_safe(self) -> None:
        variants = self.generate(
            [
                {
                    "section": "strategy",
                    "parameter": "rsi_low",
                    "type": "number",
                    "method": "NUMERIC_RANGE",
                    "minimum": 20,
                    "maximum": 30,
                    "step": 5,
                },
                {
                    "section": "execution",
                    "parameter": "targetPct",
                    "type": "number",
                    "method": "NUMERIC_RANGE",
                    "minimum": 0.1,
                    "maximum": 0.3,
                    "step": 0.1,
                },
            ]
        )
        self.assertEqual(len(variants), 9)
        self.assertEqual(
            [item.execution["targetPct"] for item in variants[:3]],
            [0.1, 0.2, 0.3],
        )
        self.assertNotIn("0.30000000000000004", variants[2].name)

    def test_fixed_value_does_not_multiply_and_ordering_is_deterministic(self) -> None:
        parameters = [
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
            {
                "section": "execution",
                "parameter": "stopLossPct",
                "type": "number",
                "method": "FIXED",
                "value": 0.3,
            },
        ]
        first = self.generate(parameters)
        second = self.generate(parameters)
        self.assertEqual(len(first), 4)
        self.assertEqual(
            [item.name for item in first],
            [
                "rsi_low=25 · targetPct=0.5 · stopLossPct=0.3",
                "rsi_low=25 · targetPct=0.75 · stopLossPct=0.3",
                "rsi_low=30 · targetPct=0.5 · stopLossPct=0.3",
                "rsi_low=30 · targetPct=0.75 · stopLossPct=0.3",
            ],
        )
        self.assertEqual(
            [item.public() for item in first],
            [item.public() for item in second],
        )

    def test_preview_hash_is_canonical_and_deterministic(self) -> None:
        first = preview_hash({"b": [2, 1], "a": {"value": 3}})
        second = preview_hash({"a": {"value": 3}, "b": [2, 1]})
        self.assertEqual(first, second)
        self.assertRegex(first, r"^sha256:[0-9a-f]{64}$")

    def test_manual_variants_are_resolved_and_validated_by_the_same_engine_contract(self) -> None:
        variants = generate_variants(
            strategy=self.strategy,
            market="NSE",
            timeframe="5m",
            mode="MANUAL",
            base_configuration={},
            base_execution={"stopLossPct": 0.5},
            manual_variants=[
                {"name": "Conservative", "configuration": {"rsi_low": 25}},
                {"name": "Baseline", "configuration": {"rsi_low": 30}},
            ],
            parameters=[],
        )
        self.assertEqual([item.name for item in variants], ["Conservative", "Baseline"])
        self.assertEqual(variants[0].configuration["rsi_length"], 14)
        self.assertEqual(variants[0].execution["stopLossPct"], 0.5)
        with self.assertRaisesRegex(ValueError, "names must be unique"):
            generate_variants(
                strategy=self.strategy,
                market="NSE",
                timeframe="5m",
                mode="MANUAL",
                base_configuration={},
                base_execution={},
                manual_variants=[
                    {"name": "Same", "configuration": {"rsi_low": 25}},
                    {"name": "same", "configuration": {"rsi_low": 30}},
                ],
                parameters=[],
            )

    def test_invalid_parameter_definitions_are_rejected(self) -> None:
        valid = {
            "section": "strategy",
            "parameter": "rsi_low",
            "type": "number",
            "method": "EXPLICIT_VALUES",
            "values": [25],
        }
        cases = {
            "duplicate path": [valid, dict(valid)],
            "empty values": [{**valid, "values": []}],
            "zero step": [
                {
                    **valid,
                    "method": "NUMERIC_RANGE",
                    "minimum": 20,
                    "maximum": 30,
                    "step": 0,
                }
            ],
            "negative step": [
                {
                    **valid,
                    "method": "NUMERIC_RANGE",
                    "minimum": 20,
                    "maximum": 30,
                    "step": -1,
                }
            ],
            "reversed range": [
                {
                    **valid,
                    "method": "NUMERIC_RANGE",
                    "minimum": 30,
                    "maximum": 20,
                    "step": 1,
                }
            ],
            "type mismatch": [{**valid, "type": "integer"}],
            "numeric string": [{**valid, "values": ["25"]}],
            "unknown section": [{**valid, "section": "deployment"}],
            "unknown strategy parameter": [{**valid, "parameter": "mystery"}],
            "unknown execution parameter": [
                {**valid, "section": "execution", "parameter": "mystery"}
            ],
            "below schema minimum": [{**valid, "values": [0]}],
            "above schema maximum": [{**valid, "values": [91]}],
            "non-finite value": [{**valid, "values": [float("inf")]}],
        }
        for label, parameters in cases.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.generate(parameters)

    def test_invalid_enum_and_duplicate_configurations_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "published enum"):
            self.generate(
                [
                    {
                        "section": "execution",
                        "parameter": "additionalSizingMode",
                        "type": "enum",
                        "method": "FIXED",
                        "value": "SIGNAL_CLOSE",
                    }
                ]
            )
        with self.assertRaisesRegex(ValueError, "duplicate resolved"):
            generate_variants(
                strategy=self.strategy,
                market="NSE",
                timeframe="5m",
                mode="MANUAL",
                base_configuration={},
                base_execution={},
                manual_variants=[
                    {"name": "First", "configuration": {"rsi_low": 25}},
                    {"name": "Second", "configuration": {"rsi_low": 25.0}},
                ],
                parameters=[],
            )

    def test_variant_limit_is_enforced_before_generation(self) -> None:
        with self.assertRaisesRegex(ValueError, "110 variants"):
            self.generate(
                [
                    {
                        "section": "strategy",
                        "parameter": "rsi_low",
                        "type": "number",
                        "method": "EXPLICIT_VALUES",
                        "values": list(range(10, 21)),
                    },
                    {
                        "section": "execution",
                        "parameter": "targetPct",
                        "type": "number",
                        "method": "EXPLICIT_VALUES",
                        "values": list(range(1, 11)),
                    },
                ]
            )

    def test_parameter_and_value_limits_are_enforced(self) -> None:
        definition = {
            "section": "strategy",
            "parameter": "rsi_low",
            "type": "number",
            "method": "EXPLICIT_VALUES",
            "values": [25],
        }
        with self.assertRaisesRegex(ValueError, "at most 8 parameters"):
            self.generate([definition] * 9)
        with self.assertRaisesRegex(ValueError, "at most 20 values"):
            self.generate([{**definition, "values": list(range(1, 22))}])


if __name__ == "__main__":
    unittest.main()
