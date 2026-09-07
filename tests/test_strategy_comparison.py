from backend.backtest.metrics import MetricsAccumulator
from backend.research.walk_forward import aggregate_unseen_metrics


def test_exposure_is_the_sum_of_actual_recorded_trade_holding_time() -> None:
    metrics = MetricsAccumulator()
    metrics.add_trade({"status": "TARGET_HIT", "holding_minutes": 10})
    metrics.add_trade({"status": "STOPPED", "holding_minutes": 25})
    metrics.add_trade({"status": "OPEN", "holding_minutes": 5})

    assert metrics.public()["exposureMinutes"] == 40


def test_unseen_aggregation_preserves_outcomes_and_exposure() -> None:
    aggregate = aggregate_unseen_metrics([
        {
            "completedTrades": 2,
            "targetHits": 1,
            "stoppedTrades": 1,
            "expiredTrades": 0,
            "openTrades": 1,
            "exposureMinutes": 50,
        },
        {
            "completedTrades": 1,
            "targetHits": 0,
            "stoppedTrades": 0,
            "expiredTrades": 1,
            "openTrades": 0,
            "averageHoldingMinutes": 12,
        },
    ])

    assert aggregate["targetHits"] == 1
    assert aggregate["stoppedTrades"] == 1
    assert aggregate["expiredTrades"] == 1
    assert aggregate["openTrades"] == 1
    assert aggregate["exposureMinutes"] == 62
