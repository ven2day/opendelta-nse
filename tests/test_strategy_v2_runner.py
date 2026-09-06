from __future__ import annotations

import unittest

from backend.strategies.runner_v2 import evaluate_isolated
from backend.strategies.source_v2 import starter_source


class StrategyV2RunnerTests(unittest.TestCase):
    def payload(self, source: str) -> dict:
        return {
            "sourceCode": source,
            "market": "CRYPTO",
            "symbol": "BTC-USDT",
            "timeframe": "5m",
            "params": {"rsi_length": 2, "rsi_low": 30},
            "candles": {
                "timestamp": ["2026-09-06T00:00:00Z", "2026-09-06T00:05:00Z", "2026-09-06T00:10:00Z"],
                "open": [100, 99, 101], "high": [101, 100, 103], "low": [98, 97, 100],
                "close": [99, 98, 102], "volume": [10, 12, 20],
            },
        }

    def test_executes_each_completed_prefix_and_normalises_buy(self) -> None:
        source = starter_source().replace(
            'return "HOLD"',
            'return {"decision": "BUY", "target_pct": 2, "reasons": ["TEST"]} if len(close) == 3 else "HOLD"',
        )
        rows = evaluate_isolated(self.payload(source), timeout_seconds=10)["rows"]
        self.assertEqual([row["decision"] for row in rows], ["NONE", "NONE", "BUY"])
        self.assertAlmostEqual(rows[-1]["targetPrice"], 104.04)
        self.assertEqual(rows[-1]["reasons"], ["TEST"])

    def test_revalidates_source_inside_worker(self) -> None:
        source = starter_source().replace('return "HOLD"', 'open("/etc/passwd"); return "HOLD"')
        with self.assertRaisesRegex(RuntimeError, "failed validation"):
            evaluate_isolated(self.payload(source), timeout_seconds=10)

    def test_rejects_invalid_decision_output(self) -> None:
        source = starter_source().replace('return "HOLD"', 'return "MAYBE"')
        with self.assertRaisesRegex(RuntimeError, "Unsupported Strategy V2 decision"):
            evaluate_isolated(self.payload(source), timeout_seconds=10)
