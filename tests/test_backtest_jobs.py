from __future__ import annotations

import time

from backtest_jobs import BacktestJobService


def wait_terminal(service: BacktestJobService, job_id: str) -> dict[str, object]:
    for _ in range(100):
        status = service.get(job_id)
        if status["status"] in {"COMPLETE", "CANCELLED", "FAILED"}:
            return status
        time.sleep(0.01)
    raise AssertionError("job did not terminate")


def test_job_progress_and_complete_result() -> None:
    service = BacktestJobService()
    try:
        def runner(progress, _cancel):
            progress({
                "currentStage": "STOCK_FEATURES_AND_CANDIDATES",
                "symbolsCompleted": 2,
                "candlesProcessed": 100,
                "candidatesFound": 3,
                "workersActive": 2,
            })
            return {"metadata": {"runId": "done"}}

        created = service.start(symbols_total=2, runner=runner)
        completed = wait_terminal(service, str(created["jobId"]))
        assert completed["status"] == "COMPLETE"
        assert completed["symbolsCompleted"] == 2
        assert completed["candlesProcessed"] == 100
        assert completed["result"] == {"metadata": {"runId": "done"}}
    finally:
        service.shutdown()


def test_cancellation_never_exposes_partial_result() -> None:
    service = BacktestJobService()
    try:
        def runner(progress, cancel):
            progress({"symbolsCompleted": 1, "currentStage": "STOCK_FEATURES_AND_CANDIDATES"})
            while not cancel.is_set():
                time.sleep(0.005)
            return {"partial": True}

        created = service.start(symbols_total=5, runner=runner)
        service.cancel(str(created["jobId"]))
        cancelled = wait_terminal(service, str(created["jobId"]))
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["result"] is None
    finally:
        service.shutdown()
