from __future__ import annotations

import unittest
from datetime import UTC, datetime

import pandas as pd
from backend.core.models import MarketContext
from backend.strategies.adapter_v2 import StrategyV2BacktestAdapter
from backend.strategies.runner_v2 import evaluate_isolated
from backend.strategies.source_v2 import starter_source, validate_source


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
        with self.assertRaisesRegex(RuntimeError, "Unsupported strategy decision"):
            evaluate_isolated(self.payload(source), timeout_seconds=10)

    def test_nse_strategy_receives_completed_candle_timestamp_in_ist(self) -> None:
        source = starter_source().replace(
            'return "HOLD"',
            'stamp = data.candles.index[-1].tz_convert("Asia/Kolkata")\n'
            '    third_close = close[2] if len(close) == 3 else 0\n'
            '    return {"decision": "BUY", "reasons": ["IST_SESSION"]} if stamp.hour == 9 and stamp.minute == 20 and third_close == 102 else "HOLD"',
        )
        payload = self.payload(source)
        payload["market"] = "NSE"
        payload["symbol"] = "ADANIENT"
        payload["candles"]["timestamp"] = [
            "2026-09-07T03:40:00Z", "2026-09-07T03:45:00Z", "2026-09-07T03:50:00Z",
        ]

        rows = evaluate_isolated(payload, timeout_seconds=10)["rows"]

        self.assertEqual([row["decision"] for row in rows], ["NONE", "NONE", "BUY"])
        self.assertEqual(rows[-1]["reasons"], ["IST_SESSION"])

    def test_integer_lookback_one_past_warmup_waits_for_the_next_candle(self) -> None:
        source = starter_source().replace(
            '"parameters": {"rsi_length": 14, "rsi_low": 30},',
            '"parameters": {"rsi_length": 14, "rsi_low": 30},\n    "requiredHistory": 20,',
        ).replace(
            'return "HOLD"',
            'return {"decision": "BUY", "reasons": ["LOOKBACK_READY"]} if close[20] == 120 else "HOLD"',
        )
        payload = self.payload(source)
        payload["candles"] = {
            "timestamp": [f"2026-09-06T00:{minute:02d}:00Z" for minute in range(21)],
            "open": list(range(100, 121)), "high": list(range(101, 122)),
            "low": list(range(99, 120)), "close": list(range(100, 121)),
            "volume": [10] * 21,
        }

        rows = evaluate_isolated(payload, timeout_seconds=10)["rows"]

        self.assertEqual(rows[19]["decision"], "NONE")
        self.assertEqual(rows[20]["decision"], "BUY")

    def test_adapter_exposes_the_live_signal_decision_contract(self) -> None:
        source_code = starter_source().replace(
            'return "HOLD"',
            'return {"decision": "BUY", "target_pct": 2, "reasons": ["V2_TEST"], "indicators": {"score": 90}}',
        )
        validation = validate_source(source_code)
        source = {
            "sourceId": "00000000-0000-0000-0000-000000000001",
            "sourceCode": source_code,
            "manifest": validation.manifest,
        }

        class Client:
            def evaluate(self, payload):
                return evaluate_isolated(dict(payload), timeout_seconds=10)

        adapter = StrategyV2BacktestAdapter(source, Client())  # type: ignore[arg-type]
        index = pd.date_range("2026-09-06T00:00:00Z", periods=3, freq="5min")
        candles = pd.DataFrame(
            {"Open": [100, 101, 102], "High": [102, 103, 104], "Low": [99, 100, 101], "Close": [101, 102, 103], "Volume": [10, 20, 30]},
            index=index,
        )
        context = MarketContext(
            market="CRYPTO", symbol="BTC-USDT", timeframe="5m", timezone="UTC",
            as_of=datetime(2026, 9, 6, 0, 10, tzinfo=UTC),
        )
        decision = adapter.evaluate(candles, context, {"rsi_length": 2, "rsi_low": 30})

        self.assertEqual(decision.decision, "BUY")
        self.assertEqual(decision.candle_timestamp, index[-1].to_pydatetime())
        self.assertEqual(decision.reasons, ("V2_TEST",))
        self.assertEqual(decision.indicators, {"score": 90})
        self.assertAlmostEqual(decision.target_price, 105.06)
