from __future__ import annotations

import time

from backtest_api import (
    _completed_job_progress,
    _execute_market_batches,
    _job_history_record,
    _market_task_batches,
    BacktestRequest,
)
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


def test_cached_result_reports_terminal_progress_counts() -> None:
    result = {
        "metadata": {
            "cachedResult": True,
            "symbolsProcessed": 10,
        },
        "summary": {
            "candleRowsProcessed": 185_000,
            "rawCandidates": 37,
            "funnel": {"executedTrades": 4},
        },
    }
    assert _completed_job_progress(result, 10) == {
        "currentStage": "CACHED_RESULT",
        "symbolsCompleted": 10,
        "symbolsTotal": 10,
        "candlesProcessed": 185_000,
        "candidatesFound": 37,
        "acceptedSignals": 4,
        "workersActive": 0,
    }


def test_market_batches_report_progress_per_symbol(monkeypatch) -> None:
    monkeypatch.delenv("BACKTEST_SYMBOL_BATCH_SIZE", raising=False)
    tasks = [{"symbol": symbol} for symbol in ("AAA", "BBB", "CCC")]
    progress: list[int] = []

    assert [len(batch) for batch in _market_task_batches(tasks, 2)] == [1, 1, 1]
    result = _execute_market_batches(
        tasks,
        1,
        lambda batch: [{"symbol": item["symbol"]} for item in batch],
        progress_callback=progress.append,
    )

    assert progress == [1, 2, 3]
    assert [item["symbol"] for item in result] == ["AAA", "BBB", "CCC"]


def test_market_batch_size_can_be_bounded_explicitly(monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_SYMBOL_BATCH_SIZE", "2")
    tasks = [{"symbol": str(index)} for index in range(5)]
    assert [len(batch) for batch in _market_task_batches(tasks, 4)] == [2, 2, 1]


def test_job_history_record_uses_completed_top_5_metadata() -> None:
    request = BacktestRequest(
        symbols=["AAA", "BBB"],
        strategyMode="top_5_opening_range_breakout",
        strategyKey="top_5_opening_range_breakout",
        durationYears=1,
        timeframe="5m",
    )
    result = {
        "metadata": {
            "runId": "full-universe-run",
            "completedAt": "2026-08-29T04:37:39+05:30",
            "strategyMode": "top_5_opening_range_breakout",
            "strategyKey": "top_5_opening_range_breakout",
            "strategyName": "Top-5 Opening Range Breakout",
            "timeframe": "5m",
            "durationYears": 1,
            "symbolsProcessed": 2,
        },
        "results": [{"symbol": "AAA"}, {"symbol": "BBB"}],
    }

    record = _job_history_record(result, request)

    assert record["id"] == "full-universe-run"
    assert record["strategyMode"] == "top_5_opening_range_breakout"
    assert record["symbolCount"] == 2
    assert record["response"] == result
