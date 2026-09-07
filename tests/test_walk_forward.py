from __future__ import annotations

from datetime import date

import pytest
from backend.backtest.engine import ExecutionSettings, PercentageFeeModel
from backend.research.walk_forward import (
    MAX_WALK_FORWARD_CANDLE_WORKLOAD,
    aggregate_unseen_metrics,
    canonical_hash,
    generate_folds,
    rank_completed_candidates,
    workload_estimate,
)


def test_anchored_schedule_expands_training_and_keeps_unseen_period_separate() -> None:
    folds = generate_folds(
        market="CRYPTO", start=date(2026, 1, 1), end=date(2026, 1, 12),
        training_window=3, testing_window=2, step=2, mode="ANCHORED", maximum_folds=4,
    )

    assert [(fold.training_start, fold.training_end, fold.testing_start, fold.testing_end) for fold in folds] == [
        (date(2026, 1, 1), date(2026, 1, 3), date(2026, 1, 4), date(2026, 1, 5)),
        (date(2026, 1, 1), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)),
        (date(2026, 1, 1), date(2026, 1, 7), date(2026, 1, 8), date(2026, 1, 9)),
        (date(2026, 1, 1), date(2026, 1, 9), date(2026, 1, 10), date(2026, 1, 11)),
    ]


def test_rolling_schedule_moves_both_boundaries() -> None:
    folds = generate_folds(
        market="CRYPTO", start=date(2026, 1, 1), end=date(2026, 1, 10),
        training_window=3, testing_window=1, step=1, mode="ROLLING", maximum_folds=3,
    )
    assert [(fold.training_start, fold.training_end, fold.testing_start) for fold in folds] == [
        (date(2026, 1, 1), date(2026, 1, 3), date(2026, 1, 4)),
        (date(2026, 1, 2), date(2026, 1, 4), date(2026, 1, 5)),
        (date(2026, 1, 3), date(2026, 1, 5), date(2026, 1, 6)),
    ]


def test_nse_schedule_uses_exact_sessions_and_never_weekends() -> None:
    sessions = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 8)]
    folds = generate_folds(
        market="NSE", start=date(2026, 1, 1), end=date(2026, 1, 9),
        training_window=2, testing_window=1, step=1, mode="ROLLING", maximum_folds=2,
        nse_sessions=sessions,
    )
    assert folds[0].training_end == date(2026, 1, 5)
    assert folds[0].testing_start == date(2026, 1, 6)
    assert all(value.weekday() < 5 for fold in folds for value in (
        fold.training_start, fold.training_end, fold.testing_start, fold.testing_end
    ))


@pytest.mark.parametrize("field,value", [("training_window", 0), ("testing_window", 0), ("step", 0)])
def test_invalid_windows_are_rejected(field: str, value: int) -> None:
    values = {"training_window": 3, "testing_window": 1, "step": 1}
    values[field] = value
    with pytest.raises(ValueError, match="positive integer"):
        generate_folds(
            market="CRYPTO", start=date(2026, 1, 1), end=date(2026, 1, 10),
            mode="ROLLING", maximum_folds=2, **values,
        )


def test_preview_hash_is_canonical_and_changes_with_a_fold_input() -> None:
    first = {"mode": "ROLLING", "symbols": ["BTC-USDT"], "step": 1}
    reordered = {"step": 1, "symbols": ["BTC-USDT"], "mode": "ROLLING"}
    assert canonical_hash(first) == canonical_hash(reordered)
    assert canonical_hash(first) != canonical_hash({**first, "step": 2})


def test_workload_includes_training_candidates_and_one_unseen_run_per_fold() -> None:
    folds = generate_folds(
        market="NSE", start=date(2026, 1, 1), end=date(2026, 1, 30),
        training_window=3, testing_window=1, step=1, mode="ROLLING", maximum_folds=2,
    )
    estimate = workload_estimate(
        market="NSE", timeframe="5m", folds=folds, candidate_count=3, symbol_count=2,
    )
    assert estimate["childRunCount"] == 8
    assert estimate["estimatedSymbolRuns"] == 16
    assert estimate["estimatedCandleWorkload"] == (3 * 3 + 1) * 75 * 2 * 2


def test_candle_workload_limit_is_server_enforced() -> None:
    folds = generate_folds(
        market="CRYPTO", start=date(2020, 1, 1), end=date(2026, 1, 1),
        training_window=1000, testing_window=100, step=100, mode="ANCHORED", maximum_folds=5,
    )
    with pytest.raises(ValueError, match=f"{MAX_WALK_FORWARD_CANDLE_WORKLOAD:,}"):
        workload_estimate(
            market="CRYPTO", timeframe="1m", folds=folds, candidate_count=20, symbol_count=10,
        )


def test_ranking_excludes_incomplete_and_minimum_trade_failures_with_stable_ties() -> None:
    candidates = [
        _candidate(1, "first", status="COMPLETE", pnl=10, drawdown=2, trades=3),
        _candidate(2, "second", status="RUNNING", pnl=100, drawdown=1, trades=10),
        _candidate(3, "third", status="COMPLETE", pnl=10, drawdown=2, trades=1),
        _candidate(4, "fourth", status="COMPLETE", pnl=10, drawdown=2, trades=3),
    ]
    ranked = rank_completed_candidates(candidates, objective="NET_PNL", minimum_trades=2)
    assert [row["name"] for row in ranked] == ["first", "fourth"]
    assert rank_completed_candidates(candidates, objective="LOWEST_DRAWDOWN", minimum_trades=2)[0]["name"] == "first"


def test_unseen_aggregation_uses_only_supplied_test_metrics() -> None:
    aggregate = aggregate_unseen_metrics([
        {
            "realizedPnl": 10, "maximumDrawdown": 4, "completedTrades": 2, "winRate": 50,
            "fees": 1, "targetHits": 1, "openTrades": 1, "exposureMinutes": 40,
        },
        {
            "realizedPnl": -2, "maximumDrawdown": 6, "completedTrades": 1, "winRate": 100,
            "fees": 0.5, "stoppedTrades": 1, "expiredTrades": 1, "averageHoldingMinutes": 12,
        },
    ])
    assert aggregate["netPnl"] == 8
    assert aggregate["maximumDrawdown"] == 6
    assert aggregate["completedTrades"] == 3
    assert aggregate["winRate"] == pytest.approx(66.67)
    assert aggregate["targetHits"] == 1
    assert aggregate["stoppedTrades"] == 1
    assert aggregate["expiredTrades"] == 1
    assert aggregate["openTrades"] == 1
    assert aggregate["exposureMinutes"] == 52
    assert aggregate["returnDrawdownScore"] == pytest.approx(8 / 6, abs=1e-6)


def test_explicit_research_cost_model_is_symmetric_and_persistable() -> None:
    settings = ExecutionSettings.from_mapping(
        {"transactionCostBps": 10, "slippageBps": 5}, whole_units=False
    )
    model = settings.fee_model(PercentageFeeModel(0, 0))
    buy = model.buy(100, 2)
    sell = model.sell(100, 2)
    assert buy.price == pytest.approx(100.05)
    assert sell.price == pytest.approx(99.95)
    assert buy.fees == pytest.approx(0.2001)
    assert settings.public()["transactionCostBps"] == 10


def _candidate(
    position: int,
    name: str,
    *,
    status: str,
    pnl: float,
    drawdown: float,
    trades: int,
) -> dict:
    return {
        "position": position,
        "name": name,
        "run": {
            "status": status,
            "metrics": {
                "realizedPnl": pnl,
                "maximumDrawdown": drawdown,
                "completedTrades": trades,
                "winRate": 50,
            },
        },
    }
