from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pandas as pd
import pytest

from backtest_api import (
    BacktestRequest,
    Top5OpeningRangeBreakoutConfigurationRequest,
    run_top_5_opening_range_breakout_backtest,
)
from daily_scalping_watchlist import (
    DailyWatchlistConfig,
    DailyWatchlistResultCache,
    add_rolling_watchlist_features,
    build_watchlist_history,
    detect_opening_range_breakouts,
    detect_rolling_momentum_breakouts,
    execute_portfolio,
    score_rescan_rows,
    select_candidates_for_history,
    stable_fingerprint,
    trade_performance,
    validation_decision,
)
from main import IST


def test_result_cache_fingerprint_isolated_by_strategy_key(tmp_path) -> None:
    top_5_fingerprint = stable_fingerprint({
        "strategyKey": "top_5_opening_range_breakout",
        "configuration": {"watchlistMode": "FROZEN_OPEN"},
    })
    vwap_fingerprint = stable_fingerprint({
        "strategyKey": "market_aligned_vwap_pullback_scalper",
        "configuration": {"watchlistMode": "FROZEN_OPEN"},
    })
    assert top_5_fingerprint != vwap_fingerprint

    cache = DailyWatchlistResultCache(tmp_path / "top-5-results")
    cache.save(top_5_fingerprint, {
        "metadata": {
            "fingerprint": top_5_fingerprint,
            "strategyKey": "top_5_opening_range_breakout",
        }
    })
    assert cache.load(top_5_fingerprint)["metadata"]["strategyKey"] == "top_5_opening_range_breakout"
    assert cache.load(vwap_fingerprint) is None


def _row(score_value: float, timestamp: pd.Timestamp) -> dict[str, object]:
    return {
        "Open": 100.0, "High": 101.0, "Low": 99.5, "Close": 100.5,
        "Volume": 10_000.0, "RSI": 55.0, "EMAFast": 100.0, "EMASlow": 99.0,
        "ATR": 1.0, "SessionVWAP": 99.8, "AverageTradedValue": 1_000_000.0,
        "ValidOHLCV": True, "RollingWindowVolume": 50_000.0 * score_value,
        "RollingTradedValue": 5_000_000.0 * score_value,
        "RollingReturnPct": score_value / 10.0, "RollingWindowRvol": score_value,
        "PriceAccelerationPct": 0.2, "CloseLocation": 0.8, "UpperWickFraction": 0.1,
        "DistanceFromVwapAtr": 0.7, "CandleRangeAtr": 1.0,
        "BullishEmaTrend": True, "EmaFastRising": True, "AtrPct": 1.0,
        "MedianDailyTradedValue": 200_000_000.0,
        "OpeningTradedValue": 5_000_000.0,
        "DailyAtrPct": 1.5, "OpeningGapPct": 0.5,
        "SpreadPct": None, "source": timestamp,
    }


def _rank_frames(*, close_future: bool = False) -> dict[str, pd.DataFrame]:
    index = pd.DatetimeIndex([
        datetime(2026, 8, 28, 9, 30, tzinfo=IST),
        datetime(2026, 8, 28, 10, 0, tzinfo=IST),
        datetime(2026, 8, 28, 10, 30, tzinfo=IST),
    ])
    frames: dict[str, pd.DataFrame] = {}
    for position, symbol in enumerate("ABCDEFG"):
        opening = 7 - position
        ten = position + 1
        ten_thirty = (position + 1) * (10 if close_future else 1)
        frames[symbol] = pd.DataFrame(
            [_row(opening, index[0]), _row(ten, index[1]), _row(ten_thirty, index[2])],
            index=index,
        )
    return frames


def _history(config: DailyWatchlistConfig, frames: dict[str, pd.DataFrame] | None = None):
    frames = frames or _rank_frames()
    return build_watchlist_history(
        frames, context_frames=frames, nifty_frame=None, sector_by_symbol={}, config=config,
        minimum_average_traded_value=500_000, maximum_candle_range_atr=3,
        maximum_spread_pct=0.15, deterministic_seed="fixture",
    )


def test_rescans_use_no_future_candles() -> None:
    config = DailyWatchlistConfig(mode="ROLLING", historical_sessions=1)
    baseline = _history(config, _rank_frames(close_future=False))
    changed = _history(config, _rank_frames(close_future=True))
    assert [row for row in baseline if row["rescanTimestamp"].endswith("10:00:00+05:30")] == [
        row for row in changed if row["rescanTimestamp"].endswith("10:00:00+05:30")
    ]


def test_midday_promotion_cannot_generate_earlier_signal_and_removed_symbol_stops_entries() -> None:
    history = [
        {"sessionDate": "2026-08-28", "rescanTimestamp": "2026-08-28T09:30:00+05:30", "entries": [{
            "symbol": "A", "selectedAt": "2026-08-28T09:30:00+05:30",
            "earliestEligibleSignalTimestamp": "2026-08-28T09:35:00+05:30",
            "rankBefore": None, "rankAfter": 1, "tier": "PRIMARY", "score": 80,
            "promotionReason": "OPENING_SELECTION",
        }]},
        {"sessionDate": "2026-08-28", "rescanTimestamp": "2026-08-28T12:00:00+05:30", "entries": [{
            "symbol": "B", "selectedAt": "2026-08-28T12:00:00+05:30",
            "earliestEligibleSignalTimestamp": "2026-08-28T12:05:00+05:30",
            "rankBefore": None, "rankAfter": 1, "tier": "PRIMARY", "score": 90,
            "promotionReason": "SCORE_ADVANTAGE_20",
        }]},
    ]
    candidates = [
        {"candidateId": "b0", "symbol": "B", "signalTimestamp": "2026-08-28T11:55:00+05:30"},
        {"candidateId": "b1", "symbol": "B", "signalTimestamp": "2026-08-28T12:05:00+05:30"},
        {"candidateId": "a1", "symbol": "A", "signalTimestamp": "2026-08-28T12:05:00+05:30"},
    ]
    selected, excluded = select_candidates_for_history([], candidates, history, mode="ROLLING", opening_time="09:30")
    assert [row["candidateId"] for row in selected] == ["b1"]
    assert {row["candidateId"]: row["reason"] for row in excluded} == {
        "b0": "NOT_IN_WATCHLIST", "a1": "NOT_IN_WATCHLIST",
    }


def test_same_clock_historical_rvol_uses_prior_twenty_sessions() -> None:
    rows, stamps = [], []
    for day in pd.bdate_range("2026-07-01", periods=21):
        for minute in (30, 35):
            stamps.append(pd.Timestamp(day).tz_localize(IST) + pd.Timedelta(hours=9, minutes=minute))
            rows.append({"Open": 100, "High": 101, "Low": 99, "Close": 100, "Volume": 100.0,
                         "ATR": 1, "SessionVWAP": 100, "EMAFast": 101, "EMASlow": 100,
                         "ValidOHLCV": True})
    rows[-1]["Volume"] = 300.0
    frame = add_rolling_watchlist_features(pd.DataFrame(rows, index=stamps), DailyWatchlistConfig())
    assert frame.iloc[-1]["SameTimeHistoricalMedianVolume"] == 200.0
    assert frame.iloc[-1]["RollingWindowRvol"] == 2.0


def test_hysteresis_residence_replacement_cap_and_determinism() -> None:
    no_advantage = DailyWatchlistConfig(mode="ROLLING", historical_sessions=1, required_promotion_advantage=100)
    assert sum(row["replacements"] for row in _history(no_advantage)) == 0
    residence = replace(no_advantage, required_promotion_advantage=0, minimum_residence_minutes=60)
    assert next(row for row in _history(residence) if "T10:00:00" in row["rescanTimestamp"])["replacements"] == 0
    config = replace(residence, minimum_residence_minutes=30, maximum_replacements_per_rescan=2,
                     minimum_promotion_score=0)
    first, second = _history(config), _history(config)
    assert next(row for row in first if "T10:00:00" in row["rescanTimestamp"])["replacements"] <= 2
    assert first == second


def test_frozen_open_selects_once_and_keeps_five_symbols() -> None:
    history = _history(DailyWatchlistConfig(mode="FROZEN_OPEN", historical_sessions=1))
    assert len(history) == 1
    assert history[0]["rescanTimestamp"].endswith("09:30:00+05:30")
    assert history[0]["selectedSymbols"] == ["A", "B", "C", "D", "E"]


def _signal_frame() -> pd.DataFrame:
    index = pd.date_range("2026-08-28 09:20", periods=18, freq="5min", tz=IST)
    frame = pd.DataFrame(index=index)
    frame["Open"] = 100.0
    frame["High"] = 100.4
    frame["Low"] = 99.8
    frame["Close"] = 100.1
    frame["Volume"] = 100_000.0
    frame["RSI"] = 55.0
    frame["EMAFast"] = [100 + index * 0.01 for index in range(len(frame))]
    frame["EMASlow"] = 99.0
    frame["ATR"] = 1.0
    frame["SessionVWAP"] = 99.8
    frame["RollingWindowRvol"] = 2.0
    frame["CloseLocation"] = 0.8
    frame["DistanceFromVwapAtr"] = 0.4
    frame["AverageTradedValue"] = 1_000_000.0
    frame["CandleRangeAtr"] = 0.6
    frame["EmaFastRising"] = True
    frame["ValidOHLCV"] = True
    return frame


def test_opening_range_breakout_uses_completed_range_and_next_open() -> None:
    frame = _signal_frame()
    # Opening range consists of completed bars at 09:20, 09:25 and 09:30.
    frame.loc[frame.index[:3], "High"] = [100.4, 100.5, 100.6]
    signal_index = 3
    frame.iloc[signal_index, frame.columns.get_loc("Close")] = 100.8
    frame.iloc[signal_index, frame.columns.get_loc("High")] = 100.9
    frame.iloc[signal_index, frame.columns.get_loc("Low")] = 100.0
    frame.iloc[signal_index + 1, frame.columns.get_loc("Open")] = 100.85
    result = detect_opening_range_breakouts(
        "A", frame, DailyWatchlistConfig(maximum_stop_pct=2.0), analysis_start=frame.index[0]
    )
    assert result
    assert result[0]["breakoutLevel"] == 100.6
    assert result[0]["signalTimestamp"] == frame.index[signal_index].isoformat()
    assert result[0]["signalCandleStart"] == (frame.index[signal_index] - pd.Timedelta(minutes=5)).isoformat()
    assert result[0]["signalCandleEnd"] == frame.index[signal_index].isoformat()
    assert result[0]["decisionTimestamp"] == frame.index[signal_index].isoformat()
    assert result[0]["entryTimestamp"] == frame.index[signal_index].isoformat()
    assert result[0]["entryCandleStart"] == frame.index[signal_index].isoformat()
    assert result[0]["entryPriceTimestamp"] == frame.index[signal_index].isoformat()
    assert result[0]["entryDataTimestamp"] == frame.index[signal_index + 1].isoformat()
    assert result[0]["entryBarIndex"] == result[0]["signalBarIndex"] + 1
    assert result[0]["nextBarInvariant"] is True
    assert result[0]["executedQuantity"] == 50


def test_rolling_breakout_level_excludes_trigger_candle() -> None:
    frame = _signal_frame()
    trigger = 9
    frame.loc[frame.index[trigger - 6:trigger], "High"] = 100.5
    frame.iloc[trigger, frame.columns.get_loc("Close")] = 100.8
    frame.iloc[trigger, frame.columns.get_loc("High")] = 102.0
    frame.iloc[trigger, frame.columns.get_loc("Low")] = 100.0
    frame.iloc[trigger + 1, frame.columns.get_loc("Open")] = 100.85
    result = detect_rolling_momentum_breakouts(
        "A", frame, DailyWatchlistConfig(maximum_stop_pct=2.0), analysis_start=frame.index[0]
    )
    match = next(row for row in result if row["signalTimestamp"] == frame.index[trigger].isoformat())
    assert match["breakoutLevel"] == 100.5
    assert match["executedQuantity"] == 50


def _ready_candidate(identifier: str, entry: str, exit_stamp: str, symbol: str = "A") -> dict[str, object]:
    return {
        "candidateId": identifier, "symbol": symbol,
        "signalTimestamp": (pd.Timestamp(entry) - pd.Timedelta(minutes=5)).isoformat(),
        "entryTimestamp": entry, "exitTimestamp": exit_stamp, "rollingScore": 80,
        "capitalDeployed": 5_000, "netPnl": 100, "grossPnl": 110, "totalCosts": 10,
        "exitReason": "TARGET_EXIT", "entryPrice": 100, "stopPrice": 99,
        "targetPrice": 101.5, "quantity": 50, "executedQuantity": 50,
        "rMultiple": 1, "primaryReason": None,
    }


def test_open_position_continues_after_removal_and_one_trade_per_symbol_day() -> None:
    first = _ready_candidate("one", "2026-08-28T09:40:00+05:30", "2026-08-28T12:30:00+05:30")
    second = _ready_candidate("two", "2026-08-28T13:00:00+05:30", "2026-08-28T14:00:00+05:30")
    trades, rejected = execute_portfolio([first, second], DailyWatchlistConfig())
    assert trades[0]["exitTimestamp"] == "2026-08-28T12:30:00+05:30"
    assert len(trades) == 1
    assert rejected[0]["primaryReason"] == "SYMBOL_DAILY_TRADE_LIMIT"
    assert trades[0]["executedQuantity"] == 50


def test_frozen_and_rolling_use_identical_cost_and_risk_results() -> None:
    candidate = _ready_candidate("same", "2026-08-28T10:00:00+05:30", "2026-08-28T10:30:00+05:30")
    frozen, _ = execute_portfolio([{**candidate, "watchlistMode": "FROZEN_OPEN"}], DailyWatchlistConfig())
    rolling, _ = execute_portfolio([{**candidate, "watchlistMode": "ROLLING"}], DailyWatchlistConfig())
    for field in ("entryPrice", "stopPrice", "targetPrice", "totalCosts", "netPnl", "exitReason", "executedQuantity"):
        assert frozen[0][field] == rolling[0][field]


def test_fixed_quantity_is_authoritative_in_backend_schema() -> None:
    request = Top5OpeningRangeBreakoutConfigurationRequest(watchlistMode="ROLLING")
    assert request.quantityPerTrade == 50
    assert request.watchlistRescanIntervalMinutes == 30
    assert request.watchlistRescanEndTime == "14:00"
    assert request.watchlistSelectedSymbols == 5
    assert request.watchlistPrimarySymbols == 2
    assert request.maximumHoldingBars == 12
    assert request.minimumPrice == 100
    assert request.maximumPrice == 5000
    assert request.minimumMedianDailyTradedValue == 100_000_000
    assert request.minimumOpeningTradedValue == 2_500_000
    assert request.minimumDailyAtrPct == 0.8
    assert request.maximumDailyAtrPct == 4.0
    assert request.maximumOpeningGapPct == 3.0
    submitted_override = Top5OpeningRangeBreakoutConfigurationRequest(maximumHoldingBars=6)
    assert submitted_override.maximumHoldingBars == 6
    assert submitted_override.strategy_config().maximum_holding_bars == 6
    with pytest.raises(ValueError, match="50"):
        Top5OpeningRangeBreakoutConfigurationRequest(quantityPerTrade=49)
    with pytest.raises(ValueError, match="Primary symbols"):
        Top5OpeningRangeBreakoutConfigurationRequest(watchlistSelectedSymbols=2, watchlistPrimarySymbols=3)
    with pytest.raises(ValueError, match="Minimum price"):
        Top5OpeningRangeBreakoutConfigurationRequest(minimumPrice=5000, maximumPrice=5000)
    with pytest.raises(ValueError, match="daily ATR"):
        Top5OpeningRangeBreakoutConfigurationRequest(minimumDailyAtrPct=4, maximumDailyAtrPct=4)


def test_idea_below_one_hundred_rupees_is_ineligible() -> None:
    timestamp = pd.Timestamp("2026-08-28T09:30:00+05:30")
    idea = pd.Series(_row(2, timestamp), name=timestamp)
    idea["Open"] = 14.0
    idea["High"] = 14.2
    idea["Low"] = 13.8
    idea["Close"] = 14.0
    scored = score_rescan_rows(
        {"IDEA": idea}, nifty_row=None, sector_by_symbol={},
        minimum_average_traded_value=500_000,
        minimum_price=100, maximum_price=5000,
        minimum_median_daily_traded_value=100_000_000,
        minimum_opening_traded_value=2_500_000,
        minimum_daily_atr_pct=0.8, maximum_daily_atr_pct=4.0,
        maximum_opening_gap_pct=3.0, maximum_candle_range_atr=3.0,
        maximum_spread_pct=0.15,
    )
    assert scored[0]["eligible"] is False
    assert scored[0]["primaryEligibilityReason"] == "PRICE_BELOW_MINIMUM"
    assert "PRICE_BELOW_MINIMUM" in scored[0]["eligibilityReasons"]


def test_trades_per_session_and_active_day_are_distinct() -> None:
    trades = [
        _ready_candidate(str(index), f"2026-08-{20 + index:02d}T10:00:00+05:30", f"2026-08-{20 + index:02d}T10:30:00+05:30", symbol=chr(65 + index))
        for index in range(4)
    ]
    result = trade_performance(trades, tested_sessions=247)
    assert result["tradesPerCalendarSession"] == round(4 / 247, 6)
    assert result["tradesPerActiveDay"] == 1.0


def _metric(net: float, expectancy: float, trades: int = 20) -> dict[str, object]:
    return {"trades": trades, "netPnlAfterCosts": net, "expectancy": expectancy, "profitFactor": 1.2}


def test_selector_is_not_approved_for_signals_or_negative_validation() -> None:
    labels = (
        "FROZEN_OPEN_TOP_FIVE", "ROLLING_TOP_FIVE", "FROZEN_OPEN_TOP_TWO",
        "FULL_ELIGIBLE_UNIVERSE", "LIQUIDITY_ONLY_TOP_FIVE", "CAUSALLY_MATCHED_RANDOM_FIVE",
    )
    comparisons = {label: {"chronologicalFolds": [{"validation": _metric(-1, -0.5)}]} for label in labels}
    result = validation_decision(comparisons)
    assert result["status"] == "REJECTED_RESEARCH_ONLY"
    assert result["liveOrdersEnabled"] is False


def test_selector_requires_positive_validation_and_strict_baseline_outperformance() -> None:
    baseline = {"chronologicalFolds": [{"validation": _metric(10, 5)}]}
    comparisons = {
        "FROZEN_OPEN_TOP_FIVE": {"chronologicalFolds": [{"validation": _metric(20, 10)}]},
        "ROLLING_TOP_FIVE": {"chronologicalFolds": [{"validation": _metric(30, 15)}]},
        "FROZEN_OPEN_TOP_TWO": baseline,
        "FULL_ELIGIBLE_UNIVERSE": baseline,
        "LIQUIDITY_ONLY_TOP_FIVE": baseline,
        "CAUSALLY_MATCHED_RANDOM_FIVE": baseline,
    }
    result = validation_decision(comparisons)
    assert result["frozenApproved"] is True
    assert result["rollingApproved"] is True
    assert result["liveOrdersEnabled"] is False


def test_standalone_backend_response_contains_all_comparisons_and_effective_settings(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("BACKTEST_WORKERS", "1")

    class Store:
        cache_directory = tmp_path

        @staticmethod
        def universe() -> list[str]:
            return ["AAA", "BBB"]

        def _cache_path(self, symbol: str, interval: str, years: int):
            return self.cache_directory / f"{symbol}-{interval}-{years}y.csv.gz"

    sessions = pd.bdate_range("2026-07-20", periods=30)
    rows: list[dict[str, float]] = []
    stamps: list[pd.Timestamp] = []
    for day_number, day in enumerate(sessions):
        base = 100.0 + day_number * 0.03
        for bar in range(75):
            stamp = pd.Timestamp(day).tz_localize(IST) + pd.Timedelta(hours=9, minutes=15 + bar * 5)
            wave = ((bar % 12) - 6) * 0.015
            close = base + bar * 0.008 + wave
            rows.append({
                "Open": close - 0.03, "High": close + 0.12, "Low": close - 0.12,
                "Close": close, "Volume": 80_000.0 + bar * 100.0,
            })
            stamps.append(stamp)
    frame = pd.DataFrame(rows, index=stamps)
    for symbol, multiplier in (("AAA", 1.0), ("BBB", 1.001)):
        output = frame.copy()
        output[["Open", "High", "Low", "Close"]] *= multiplier
        output.to_csv(tmp_path / f"{symbol}-5-1y.csv.gz", index_label="Timestamp", compression="gzip")
    frame.to_csv(tmp_path / "NIFTY50-5-1y.csv.gz", index_label="Timestamp", compression="gzip")

    request = BacktestRequest(
        symbols=["AAA", "BBB"], strategyMode="top_5_opening_range_breakout",
        timeframe="5m", durationYears=1, cachePolicy="RUN_AGAIN",
        top5OpeningRangeBreakoutConfiguration=Top5OpeningRangeBreakoutConfigurationRequest(
            watchlistHistoricalSessions=20,
        ),
    )
    result = run_top_5_opening_range_breakout_backtest(
        request, Store(), datetime(2026, 8, 29, 12, 0, tzinfo=IST)
    )
    assert result["metadata"]["strategyKey"] == "top_5_opening_range_breakout"
    assert result["metadata"]["effectiveConfiguration"]["quantityPerTrade"] == 50
    assert result["metadata"]["submittedMaximumHoldingBars"] == 12
    assert result["metadata"]["effectiveMaximumHoldingBars"] == 12
    assert result["metadata"]["liveOrdersEnabled"] is False
    assert result["summary"]["executedQuantity"] == 50
    assert result["summary"]["frozenReplacements"] == 0
    assert result["summary"]["watchlistReplacements"] == 0
    assert result["metadata"]["universeEligibility"]["symbolsRequested"] == 2
    assert "symbolsActuallyScored" in result["metadata"]["universeEligibility"]
    assert "candidates" in result
    assert set(result["comparison"]) == {
        "FROZEN_OPEN_TOP_FIVE", "ROLLING_TOP_FIVE", "FROZEN_OPEN_TOP_TWO",
        "FULL_ELIGIBLE_UNIVERSE", "LIQUIDITY_ONLY_TOP_FIVE",
        "CAUSALLY_MATCHED_RANDOM_FIVE",
    }
    assert len(result["results"]) == 6
    assert all(int(trade["executedQuantity"]) == 50 for trade in result["trades"])
    assert result["validationDecision"]["liveOrdersEnabled"] is False
