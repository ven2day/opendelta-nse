from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pandas as pd
import pytest
from pydantic import ValidationError

from backtest_api import BacktestRequest, run_vwap_pullback_backtest
from main import IST
from market_aligned_vwap_pullback_scalper import (
    STRATEGY_KEY,
    VwapPullbackConfig,
    _plan_exit,
    calculate_vwap_pullback_features,
    detect_pullback_candidates,
    enrich_candidates,
    execute_portfolio,
    score_candidate_quality,
    stable_fingerprint,
)


def feature_frame(
    *,
    start: str = "2026-08-28 09:15",
    periods: int = 12,
    trigger_offset: int = 4,
    trigger_rsi: float = 55,
    next_open: float = 101.05,
) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq="5min", tz=IST)
    frame = pd.DataFrame(index=index)
    frame["Open"] = 100.45
    frame["High"] = 100.70
    frame["Low"] = 100.40
    frame["Close"] = 100.50
    frame["Volume"] = 1_000_000.0
    frame["RSI"] = 70.0
    frame["EMAFast"] = [100.30 + index * 0.1 for index in range(periods)]
    frame["EMASlow"] = [100.00 + index * 0.1 for index in range(periods)]
    frame["ATR"] = 1.0
    frame["SessionVWAP"] = 100.30
    frame["RVOL"] = 1.6
    frame["AverageTradedValue"] = 100_000_000.0
    frame["ReturnPct"] = 1.0
    frame["HighQualityTrigger"] = True
    arm = trigger_offset - 1
    frame.iloc[arm, frame.columns.get_loc("RSI")] = 45.0
    frame.iloc[arm, frame.columns.get_loc("Low")] = 100.40
    frame.iloc[trigger_offset, frame.columns.get_loc("Open")] = 100.75
    frame.iloc[trigger_offset, frame.columns.get_loc("High")] = 101.20
    frame.iloc[trigger_offset, frame.columns.get_loc("Low")] = 100.70
    frame.iloc[trigger_offset, frame.columns.get_loc("Close")] = 101.00
    frame.iloc[trigger_offset, frame.columns.get_loc("RSI")] = trigger_rsi
    frame.iloc[trigger_offset + 1, frame.columns.get_loc("Open")] = next_open
    frame.iloc[trigger_offset + 1, frame.columns.get_loc("High")] = max(next_open, 101.20)
    frame.iloc[trigger_offset + 1, frame.columns.get_loc("Low")] = min(next_open, 100.90)
    frame.iloc[trigger_offset + 1, frame.columns.get_loc("Close")] = next_open
    for offset in range(trigger_offset + 2, periods):
        frame.iloc[offset, frame.columns.get_loc("Open")] = 101.10
        frame.iloc[offset, frame.columns.get_loc("High")] = 101.25
        frame.iloc[offset, frame.columns.get_loc("Low")] = 100.90
        frame.iloc[offset, frame.columns.get_loc("Close")] = 101.10
    return frame


def ready_candidates(frame: pd.DataFrame | None = None, config: VwapPullbackConfig | None = None) -> list[dict]:
    result = detect_pullback_candidates("TEST", frame if frame is not None else feature_frame(), config or VwapPullbackConfig())
    return result["candidates"]


def portfolio_candidate(
    candidate_id: str,
    entry: str,
    exit_: str,
    *,
    quality: float = 50,
    pnl: float = 100,
    capital: float = 10_000,
) -> dict:
    return {
        "candidateId": candidate_id,
        "symbol": candidate_id,
        "entryTimestamp": pd.Timestamp(entry, tz=IST).isoformat(),
        "exitTimestamp": pd.Timestamp(exit_, tz=IST).isoformat(),
        "qualityScore": quality,
        "capitalDeployed": capital,
        "netPnl": pnl,
        "exitReason": "TARGET_EXIT" if pnl >= 0 else "STOP_EXIT",
        "primaryReason": None,
    }


def test_session_vwap_resets_daily() -> None:
    index = pd.to_datetime([
        "2026-08-27 09:15+05:30", "2026-08-27 09:20+05:30",
        "2026-08-28 09:15+05:30", "2026-08-28 09:20+05:30",
    ])
    candles = pd.DataFrame({
        "Open": [100, 110, 200, 210], "High": [101, 111, 201, 211],
        "Low": [99, 109, 199, 209], "Close": [100, 110, 200, 210],
        "Volume": [1_000_000] * 4,
    }, index=index)
    features = calculate_vwap_pullback_features(candles, replace(VwapPullbackConfig(), rsi_length=2, ema_fast=1, ema_slow=2, atr_length=2, rvol_period=2))
    assert features.iloc[2]["SessionVWAP"] == pytest.approx(200)


def test_invalid_ohlcv_row_is_quarantined_without_rejecting_the_symbol() -> None:
    index = pd.date_range("2026-08-28 09:15", periods=30, freq="5min", tz=IST)
    candles = pd.DataFrame({
        "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0,
        "Volume": 1_000_000.0,
    }, index=index)
    candles.iloc[10, candles.columns.get_loc("Volume")] = -1
    config = replace(
        VwapPullbackConfig(), rsi_length=2, ema_fast=1, ema_slow=2,
        atr_length=2, rvol_period=2,
    )

    features = calculate_vwap_pullback_features(candles, config)

    assert len(features) == len(candles)
    assert not bool(features.iloc[10]["ValidOHLCV"])
    assert pd.isna(features.iloc[10]["Volume"])


def test_invalid_bar_cancels_setup_and_invalid_next_open_is_rejected() -> None:
    cancelled = feature_frame()
    cancelled["ValidOHLCV"] = True
    cancelled.iloc[4, cancelled.columns.get_loc("ValidOHLCV")] = False
    result = detect_pullback_candidates("TEST", cancelled, VwapPullbackConfig())
    assert result["candidates"] == []
    assert any(event["type"] == "INVALID_OHLCV_CANCEL" for event in result["events"])

    invalid_entry = feature_frame()
    invalid_entry["ValidOHLCV"] = True
    invalid_entry.iloc[5, invalid_entry.columns.get_loc("ValidOHLCV")] = False
    candidate = detect_pullback_candidates(
        "TEST", invalid_entry, VwapPullbackConfig()
    )["candidates"][0]
    assert candidate["primaryReason"] == "INVALID_ENTRY_BAR"


def test_pullback_state_cannot_cross_sessions() -> None:
    first = feature_frame(periods=6, trigger_offset=4)
    second = feature_frame(start="2026-08-29 09:15", periods=7, trigger_offset=4)
    first.iloc[:, first.columns.get_loc("RSI")] = 70
    first.iloc[-1, first.columns.get_loc("RSI")] = 45
    second.iloc[:, second.columns.get_loc("RSI")] = 55
    result = detect_pullback_candidates("TEST", pd.concat([first, second]), VwapPullbackConfig())
    assert result["candidates"] == []
    assert any(event["type"] == "SESSION_END_CANCEL" for event in result["events"])


def test_ema_trend_qualification_and_rsi_arm_range() -> None:
    result = detect_pullback_candidates("TEST", feature_frame(), VwapPullbackConfig())
    assert result["trendQualifiedBars"] >= 1
    arm = next(event for event in result["events"] if event["type"] == "PULLBACK_ARMED")
    assert arm["rsiAtArm"] == 45
    assert arm["nearestPullbackReference"] in {"SESSION_VWAP", "EMA_FAST", "EMA_SLOW"}


@pytest.mark.parametrize(("rsi", "accepted"), [(50, False), (50.0001, True), (65, True), (65.0001, False)])
def test_trigger_rsi_bounds(rsi: float, accepted: bool) -> None:
    assert bool(ready_candidates(feature_frame(trigger_rsi=rsi))) is accepted


def test_pullback_expires_after_six_bars() -> None:
    frame = feature_frame(periods=14, trigger_offset=10)
    frame.iloc[3, frame.columns.get_loc("RSI")] = 45
    frame.iloc[4:10, frame.columns.get_loc("RSI")] = 45
    assert ready_candidates(frame) == []


def test_next_bar_open_and_gap_rejection_are_causal() -> None:
    normal = ready_candidates()[0]
    assert normal["entryBarIndex"] == normal["signalBarIndex"] + 1
    assert normal["entryTimestamp"] > normal["signalTimestamp"]
    gap = ready_candidates(feature_frame(next_open=102.0))[0]
    assert gap["primaryReason"] == "GAP_TOO_LARGE"


def test_stop_distance_minimum_maximum_and_target_1_5r() -> None:
    frame = feature_frame()
    candidate = ready_candidates(frame, replace(VwapPullbackConfig(), minimum_stop_pct=0.8, maximum_stop_pct=2))[0]
    assert candidate["riskPct"] == pytest.approx(0.8)
    assert candidate["targetPrice"] - candidate["entryPrice"] == pytest.approx(1.5 * candidate["riskPerShare"], abs=1e-4)
    wide = _plan_exit(frame, {**candidate, "pullbackSwingLow": 98.0}, VwapPullbackConfig())
    assert wide["primaryReason"] == "RISK_TOO_WIDE"


def test_structural_and_volatility_stop_use_lower_value() -> None:
    candidate = ready_candidates()[0]
    assert candidate["stopPrice"] == min(candidate["structuralStop"], candidate["volatilityStop"])


def test_same_candle_touching_stop_and_target_uses_stop_first() -> None:
    frame = feature_frame()
    base = ready_candidates(frame)[0]
    monitor = int(base["entryBarIndex"]) + 1
    frame.iloc[monitor, frame.columns.get_loc("Low")] = float(base["stopPrice"]) - 0.1
    frame.iloc[monitor, frame.columns.get_loc("High")] = float(base["targetPrice"]) + 0.1
    planned = _plan_exit(frame, base, VwapPullbackConfig())
    assert planned["exitReason"] == "STOP_EXIT"


def test_time_exit_and_session_exit() -> None:
    frame = feature_frame(periods=16)
    candidate = ready_candidates(frame)[0]
    timed = _plan_exit(frame, candidate, replace(VwapPullbackConfig(), maximum_holding_bars=2))
    assert timed["exitReason"] == "TIME_EXIT"
    late = feature_frame(start="2026-08-28 14:45", periods=8, trigger_offset=4)
    late_candidate = ready_candidates(late, replace(VwapPullbackConfig(), last_entry_time="15:10"))[0]
    assert late_candidate["exitReason"] == "SESSION_EXIT"


def test_maximum_daily_trades_and_concurrent_positions() -> None:
    candidates = [portfolio_candidate(str(index), "2026-08-28 10:00", "2026-08-28 11:00", quality=100-index) for index in range(7)]
    trades, rejected = execute_portfolio(candidates, replace(VwapPullbackConfig(), maximum_concurrent_trades=10))
    assert len(trades) == 5
    assert sum(item["primaryReason"] == "MAX_TRADES_PER_DAY" for item in rejected) == 2
    trades, rejected = execute_portfolio(candidates[:3], replace(VwapPullbackConfig(), maximum_concurrent_trades=2))
    assert len(trades) == 2
    assert rejected[0]["primaryReason"] == "MAX_CONCURRENT_TRADES"


def test_stop_after_two_realized_daily_losses() -> None:
    candidates = [
        portfolio_candidate("a", "2026-08-28 09:30", "2026-08-28 09:40", pnl=-100),
        portfolio_candidate("b", "2026-08-28 09:45", "2026-08-28 09:55", pnl=-100),
        portfolio_candidate("c", "2026-08-28 10:00", "2026-08-28 10:10"),
    ]
    trades, rejected = execute_portfolio(candidates, VwapPullbackConfig())
    assert len(trades) == 2
    assert rejected[0]["primaryReason"] == "DAILY_LOSS_COUNT"


def test_quality_ranking_uses_only_same_timestamp_candidates() -> None:
    candidates = [
        portfolio_candidate("low", "2026-08-28 10:00", "2026-08-28 11:00", quality=10),
        portfolio_candidate("high", "2026-08-28 10:00", "2026-08-28 11:00", quality=90),
        portfolio_candidate("future", "2026-08-28 11:05", "2026-08-28 11:10", quality=100),
    ]
    trades, _ = execute_portfolio(candidates, replace(VwapPullbackConfig(), maximum_concurrent_trades=1, maximum_trades_per_day=3))
    assert [trade["candidateId"] for trade in trades] == ["high", "future"]


def test_missing_market_context_obeys_policy_without_becoming_bearish() -> None:
    candidate = ready_candidates()[0]
    advisory = enrich_candidates([candidate], nifty_by_candidate={}, support_by_candidate={}, config=VwapPullbackConfig())
    assert advisory[0]["marketSafetyPassed"] is True
    assert advisory[0]["primaryReason"] is None
    rejected = enrich_candidates([candidate], nifty_by_candidate={}, support_by_candidate={}, config=replace(VwapPullbackConfig(), market_context_fail_policy="REJECT"))
    assert rejected[0]["primaryReason"] == "MARKET_CONTEXT_UNAVAILABLE"


def test_quality_score_components_and_optional_enforcement() -> None:
    candidate = {"stockReturnPct": 3, "rvol": 1.6, "highQualityTrigger": True}
    nifty = {"available": True, "supportive": True, "returnPct": 1}
    support = {"sectorSupportive": True, "breadthSupportive": True, "sectorReturnPct": 2}
    quality = score_candidate_quality(candidate, nifty, support, VwapPullbackConfig())
    assert quality["qualityScore"] == 100


def test_costs_and_slippage_are_applied() -> None:
    trade = ready_candidates()[0]
    assert trade["totalCosts"] > 0
    assert trade["netPnl"] == pytest.approx(trade["grossPnl"] - trade["totalCosts"], abs=0.02)


def test_configuration_fingerprint_changes_for_every_change() -> None:
    baseline = VwapPullbackConfig().public()
    assert stable_fingerprint(baseline) != stable_fingerprint({**baseline, "rsi_length": 15})
    assert stable_fingerprint(baseline) == stable_fingerprint(dict(reversed(list(baseline.items()))))


def test_multi_symbol_results_are_deterministic() -> None:
    candidates = [
        portfolio_candidate("b", "2026-08-28 10:00", "2026-08-28 10:30", quality=50),
        portfolio_candidate("a", "2026-08-28 10:00", "2026-08-28 10:30", quality=50),
    ]
    first = execute_portfolio(candidates, VwapPullbackConfig())[0]
    second = execute_portfolio(list(reversed(candidates)), VwapPullbackConfig())[0]
    assert [item["candidateId"] for item in first] == [item["candidateId"] for item in second]


def test_active_strategy_registry_and_retired_strategy() -> None:
    for key in ("rsi_range", "rsi_recovery", STRATEGY_KEY):
        request = BacktestRequest(symbols=["TEST"], strategyMode=key, timeframe="5m")
        assert request.strategyMode == key
    with pytest.raises(ValidationError):
        BacktestRequest(symbols=["TEST"], strategyMode="market_aligned_rsi_scalper", timeframe="5m")


def test_oi_off_is_not_evaluated_and_never_gates() -> None:
    candidate = ready_candidates()[0]
    enriched = enrich_candidates([candidate], nifty_by_candidate={}, support_by_candidate={}, config=VwapPullbackConfig(), oi_repository=object())
    assert enriched[0]["oiResult"] == "NOT_EVALUATED"
    assert enriched[0]["primaryReason"] is None


def test_cold_and_cached_runs_have_identical_strategy_semantics(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_WORKERS", "1")

    class Store:
        cache_directory = tmp_path

        @staticmethod
        def universe() -> list[str]:
            return ["TEST"]

        def _cache_path(self, symbol: str, interval: str, years: int):
            return self.cache_directory / f"{symbol}-{interval}-{years}y.csv.gz"

    index = pd.date_range("2026-08-24 09:15", periods=75, freq="5min", tz=IST)
    raw = pd.DataFrame({
        "Open": 100.0, "High": 100.2, "Low": 99.8, "Close": 100.0,
        "Volume": 1_000_000.0,
    }, index=index)
    raw.to_csv(tmp_path / "TEST-5-1y.csv.gz", index_label="Timestamp", compression="gzip")
    raw.to_csv(tmp_path / "NIFTY50-5-1y.csv.gz", index_label="Timestamp", compression="gzip")
    request = BacktestRequest(
        symbols=["TEST"], strategyMode=STRATEGY_KEY, timeframe="5m",
        durationYears=1, cachePolicy="RUN_AGAIN",
    )
    now = datetime(2026, 8, 29, 12, 0, tzinfo=IST)
    fresh = run_vwap_pullback_backtest(request, Store(), now)
    cached = run_vwap_pullback_backtest(
        request.model_copy(update={"cachePolicy": "USE_CACHE"}), Store(), now
    )
    assert fresh["metadata"]["resultSource"] == "FRESH_CALCULATION"
    assert cached["metadata"]["resultSource"] == "RESULT_CACHE"
    assert cached["metadata"]["configurationHash"] == fresh["metadata"]["configurationHash"]
    assert cached["summary"] == fresh["summary"]
    assert cached["trades"] == fresh["trades"]


def test_missing_optional_support_is_not_a_failed_trading_symbol(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BACKTEST_WORKERS", "1")
    mapping = tmp_path / "market-sector-map.csv"
    mapping.write_text(
        "symbol,sector\nTEST,Technology\nMISSING,Technology\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MARKET_CONTEXT_SECTOR_MAP_FILE", str(mapping))
    monkeypatch.setenv("MARKET_CONTEXT_BREADTH_UNIVERSE_FILE", str(mapping))

    class Store:
        cache_directory = tmp_path

        @staticmethod
        def universe() -> list[str]:
            return ["TEST", "MISSING"]

        def _cache_path(self, symbol: str, interval: str, years: int):
            return self.cache_directory / f"{symbol}-{interval}-{years}y.csv.gz"

    index = pd.date_range("2026-08-24 09:15", periods=75, freq="5min", tz=IST)
    raw = pd.DataFrame({
        "Open": 100.0, "High": 100.2, "Low": 99.8, "Close": 100.0,
        "Volume": 1_000_000.0,
    }, index=index)
    raw.to_csv(tmp_path / "TEST-5-1y.csv.gz", index_label="Timestamp", compression="gzip")
    raw.to_csv(tmp_path / "NIFTY50-5-1y.csv.gz", index_label="Timestamp", compression="gzip")

    result = run_vwap_pullback_backtest(
        BacktestRequest(
            symbols=["TEST"], strategyMode=STRATEGY_KEY, timeframe="5m",
            durationYears=1, cachePolicy="RUN_AGAIN",
        ),
        Store(),
        datetime(2026, 8, 29, 12, 0, tzinfo=IST),
    )

    assert result["metadata"]["symbolsProcessed"] == 1
    assert result["metadata"]["symbolsFailed"] == 0
    assert result["metadata"]["supportingData"]["supportSymbolsUnavailable"] == 1
    assert result["errors"] == []
    assert result["supportingDataErrors"] == [{
        "symbol": "MISSING",
        "message": "Local 5-minute candle cache is unavailable for MISSING",
    }]
