from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import ema_vwap_strong_buy as strategy


class StrongBuyConfigTests(unittest.TestCase):
    def test_reducing_lots_match_requested_sequence(self) -> None:
        config = strategy.StrongBuyConfig()
        self.assertEqual([config.quantity(index) for index in range(7)], [100, 50, 25, 12, 6, 3, 1])

    def test_fixed_percentage_mode(self) -> None:
        config = strategy.StrongBuyConfig(additional_sizing_mode="FIXED_PERCENTAGE_OF_FIRST_LOT")
        self.assertEqual([config.quantity(index) for index in range(4)], [100, 50, 50, 50])

    def test_requires_exactly_two_confirmations(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly two"):
            strategy.StrongBuyConfig(minimum_confirmations=3).validate()


class StrongBuySimulationTests(unittest.TestCase):
    def candles(self) -> pd.DataFrame:
        index = pd.date_range("2026-08-31 09:15", periods=5, freq="5min", tz="Asia/Kolkata")
        return pd.DataFrame({"Open": [100, 101, 101, 101, 102], "High": [101, 102, 102, 102, 103], "Low": [99, 100, 100, 100, 101], "Close": [100, 101, 101, 101, 102], "Volume": [1000] * 5}, index=index)

    def indicator_frame(self) -> pd.DataFrame:
        data = self.candles()
        data["EmaFast"] = data["Close"]
        data["EmaSlow"] = data["Close"] - 1
        data["SessionVwap"] = data["Close"] - 1
        data["PlusDi"] = 30.0
        data["MinusDi"] = 10.0
        data["Adx"] = 25.0
        data["RelativeVolume"] = 1.5
        data["HtfAlignment"] = True
        data["AdxConfirmation"] = True
        data["RvolConfirmation"] = True
        data["ConfirmationScore"] = 3
        data["BullishCross"] = False
        data["BaseBuy"] = False
        data["StrongBuy"] = [True, False, False, True, False]
        return data

    def test_each_lot_has_own_target_and_holding_state(self) -> None:
        with patch.object(strategy, "calculate_strong_buy_indicators", return_value=self.indicator_frame()):
            result = strategy.simulate_strong_buy_symbol("TEST", self.candles())
        self.assertEqual([lot["quantity"] for lot in result["lots"]], [100, 50])
        self.assertEqual(result["lots"][0]["status"], "TAKE_PROFIT_SOLD")
        self.assertEqual(result["lots"][1]["status"], "HOLDING")
        self.assertAlmostEqual(result["lots"][0]["targetPrice"], result["lots"][0]["entryPrice"] * 1.01, places=4)
        self.assertAlmostEqual(result["lots"][1]["targetPrice"], result["lots"][1]["entryPrice"] * 1.01, places=4)


if __name__ == "__main__":
    unittest.main()
