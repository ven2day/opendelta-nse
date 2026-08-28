from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from backtest_history import BacktestHistoryRepository


def owner(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record(run_id: str, completed: datetime, *, value: int = 1) -> dict[str, object]:
    return {
        "id": run_id,
        "completedAt": completed.isoformat(),
        "strategyMode": "rsi_recovery",
        "strategyName": "RSI Recovery Scalping",
        "timeframe": "5m",
        "durationYears": 1,
        "symbolCount": 1,
        "response": {
            "metadata": {"runId": run_id, "strategyMode": "rsi_recovery"},
            "results": [{"symbol": "TEST", "value": value}],
            "errors": [],
            "warnings": [],
        },
    }


class BacktestHistoryRepositoryTests(unittest.TestCase):
    def test_save_list_and_get_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            repository = BacktestHistoryRepository(Path(directory))
            saved = record("run-1", datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc))
            summary = repository.save(owner("alice"), saved)

            self.assertEqual(summary["id"], "run-1")
            self.assertEqual(repository.list(owner("alice")), [summary])
            self.assertEqual(repository.get(owner("alice"), "run-1"), saved)
            self.assertTrue((Path(directory) / "history.sqlite3").is_file())
            self.assertEqual(len(list((Path(directory) / "payloads").rglob("*.json.gz"))), 1)

    def test_history_is_isolated_by_owner(self) -> None:
        with TemporaryDirectory() as directory:
            repository = BacktestHistoryRepository(Path(directory))
            completed = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
            repository.save(owner("alice"), record("same-run", completed, value=1))
            repository.save(owner("bob"), record("same-run", completed, value=2))

            self.assertEqual(repository.get(owner("alice"), "same-run")["response"]["results"][0]["value"], 1)
            self.assertEqual(repository.get(owner("bob"), "same-run")["response"]["results"][0]["value"], 2)

    def test_only_newest_ten_are_retained(self) -> None:
        with TemporaryDirectory() as directory:
            repository = BacktestHistoryRepository(Path(directory), limit=10)
            start = datetime(2026, 8, 1, tzinfo=timezone.utc)
            for index in range(11):
                repository.save(owner("alice"), record(f"run-{index:02d}", start + timedelta(days=index)))

            self.assertEqual([item["id"] for item in repository.list(owner("alice"))], [f"run-{index:02d}" for index in range(10, 0, -1)])
            with self.assertRaises(KeyError):
                repository.get(owner("alice"), "run-00")
            self.assertEqual(len(list((Path(directory) / "payloads").rglob("*.json.gz"))), 10)

    def test_resaving_run_is_idempotent_and_replaces_payload(self) -> None:
        with TemporaryDirectory() as directory:
            repository = BacktestHistoryRepository(Path(directory))
            completed = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
            repository.save(owner("alice"), record("run-1", completed, value=1))
            repository.save(owner("alice"), record("run-1", completed, value=2))

            self.assertEqual(len(repository.list(owner("alice"))), 1)
            self.assertEqual(repository.get(owner("alice"), "run-1")["response"]["results"][0]["value"], 2)
            self.assertEqual(len(list((Path(directory) / "payloads").rglob("*.json.gz"))), 1)

    def test_delete_removes_metadata_and_payload(self) -> None:
        with TemporaryDirectory() as directory:
            repository = BacktestHistoryRepository(Path(directory))
            repository.save(owner("alice"), record("run-1", datetime.now(timezone.utc)))
            self.assertTrue(repository.delete(owner("alice"), "run-1"))
            self.assertFalse(repository.delete(owner("alice"), "run-1"))
            self.assertEqual(repository.list(owner("alice")), [])
            self.assertEqual(list((Path(directory) / "payloads").rglob("*.json.gz")), [])

    def test_invalid_owner_and_mismatched_response_id_are_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            repository = BacktestHistoryRepository(Path(directory))
            malformed = record("run-1", datetime.now(timezone.utc))
            malformed["response"]["metadata"]["runId"] = "different"
            with self.assertRaises(ValueError):
                repository.save("alice", malformed)
            with self.assertRaises(ValueError):
                repository.save(owner("alice"), malformed)


if __name__ == "__main__":
    unittest.main()
