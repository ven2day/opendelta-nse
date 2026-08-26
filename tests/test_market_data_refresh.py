from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from main import DhanAPIError
from market_data_refresh import MarketDataRefreshService


class MarketDataRefreshServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name).resolve()
        self.output_file = self.root / "nse_symbols_rsi_volume.csv"
        self.environment = patch.dict(
            os.environ,
            {
                "DHAN_CLIENT_ID": "1234567890",
                "DHAN_PIN": "123456",
                "DHAN_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
                "DHAN_TOKEN_CACHE_FILE": str(self.root / "token.json"),
                "NSE_DATA_FILE": str(self.root / "ignored.csv"),
                "SYMBOLS_FILE": str(self.root / "symbols.csv"),
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_refresh_publishes_all_rows_and_reports_file_timestamp(self) -> None:
        def runner(config):
            result = pd.DataFrame([{"symbol": "LUPIN"}, {"symbol": "SBIN"}])
            result.to_csv(config.output_file, index=False)
            return result

        service = MarketDataRefreshService(self.output_file, runner=runner)
        self.addCleanup(service.shutdown)

        started = service.start()
        completed = service.wait(timeout=2)

        self.assertTrue(started["accepted"])
        self.assertEqual(completed["state"], "SUCCEEDED")
        self.assertEqual(completed["rowsPublished"], 2)
        self.assertIsNotNone(completed["lastRefreshTimestamp"])
        self.assertEqual(pd.read_csv(self.output_file)["symbol"].tolist(), ["LUPIN", "SBIN"])

    def test_second_request_does_not_start_duplicate_refresh(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def runner(config):
            entered.set()
            release.wait(timeout=2)
            result = pd.DataFrame([{"symbol": "LUPIN"}])
            result.to_csv(config.output_file, index=False)
            return result

        service = MarketDataRefreshService(self.output_file, runner=runner)
        self.addCleanup(service.shutdown)

        first = service.start()
        self.assertTrue(entered.wait(timeout=1))
        second = service.start()
        release.set()
        completed = service.wait(timeout=2)

        self.assertTrue(first["accepted"])
        self.assertFalse(second["accepted"])
        self.assertTrue(second["running"])
        self.assertEqual(completed["state"], "SUCCEEDED")

    def test_sanitized_collector_failure_is_reported(self) -> None:
        def runner(config):
            raise DhanAPIError("Dhan Data API subscription is not active")

        service = MarketDataRefreshService(self.output_file, runner=runner)
        self.addCleanup(service.shutdown)

        service.start()
        completed = service.wait(timeout=2)

        self.assertEqual(completed["state"], "FAILED")
        self.assertFalse(completed["running"])
        self.assertEqual(completed["error"], "Dhan Data API subscription is not active")


if __name__ == "__main__":
    unittest.main()
