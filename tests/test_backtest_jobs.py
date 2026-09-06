from __future__ import annotations

import unittest

from backend.backtest.jobs import BacktestJobRunner, BacktestQueueFull


class BacktestQueueTests(unittest.TestCase):
    def test_reservation_is_bounded_and_releases_without_submission(self) -> None:
        runner = BacktestJobRunner(
            runs=object(),
            engine_factory=lambda *_args: None,
            max_workers=1,
            max_pending=2,
        )
        reservation = runner.reserve(2)
        self.assertEqual(runner.pending_count(), 2)
        with self.assertRaises(BacktestQueueFull):
            runner.reserve(1)
        reservation.release()
        self.assertEqual(runner.pending_count(), 0)
        runner.shutdown()


if __name__ == "__main__":
    unittest.main()
