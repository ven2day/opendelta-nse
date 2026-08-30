from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from opendelta.analytics import maximum_drawdown, summarize_returns, summarize_trade_ledger
from opendelta.backtests import (
    ExecutionPolicy,
    configuration_snapshot,
    costed_return,
    next_bar_entry,
    resolve_long_exit,
)
from opendelta.core import UNSUPPORTED_DATA_REQUIREMENT, PlatformSettings
from opendelta.factors import FactorEngine, FactorRegistry
from opendelta.jobs import JobRepository, JobService
from opendelta.market_data import (
    FeatureCache,
    FeatureCacheKey,
    align_completed_timeframe,
    normalize_candles,
)
from opendelta.market_context import MarketContextService
from opendelta.platform import (
    PlatformRuntime,
    create_platform_router,
)
from opendelta.research import (
    ResearchExperimentRequest,
    ResearchService,
    chronological_split,
)
from opendelta.risk import RiskService
from opendelta.strategies import StrategyRegistry


def candle_frame(rows: int = 420) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    close = 100 + index * 0.03 + np.sin(index / 5)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=rows, freq="5min", tz="UTC"),
            "open": close - 0.05,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1_000 + (index % 24) * 20,
        }
    )


def settings(tmp_path: Path) -> PlatformSettings:
    symbols = tmp_path / "symbols.csv"
    symbols.write_text("symbol,company_name\nLUPIN,Lupin Limited\n", encoding="utf-8")
    market_data = tmp_path / "market.csv"
    market_data.write_text("symbol,close\nLUPIN,1200\n", encoding="utf-8")
    return PlatformSettings(
        data_root=tmp_path,
        database_path=tmp_path / "platform.sqlite3",
        market_data_file=market_data,
        symbols_file=symbols,
        maximum_workers=1,
        maximum_pending_jobs=3,
        job_retry_limit=0,
        environment="test",
    )


def test_candle_normalization_deduplicates_rejects_invalid_and_incomplete() -> None:
    frame = pd.DataFrame(
        [
            {"timestamp": "2026-01-01T00:00:00Z", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 5},
            {"timestamp": "2026-01-01T00:00:00Z", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 6},
            {"timestamp": "2026-01-01T00:05:00Z", "open": 11, "high": 10, "low": 9, "close": 11, "volume": 5},
            {"timestamp": "2026-01-01T00:10:00Z", "open": 11, "high": 12, "low": 10, "close": 11, "volume": 5},
        ]
    )
    normalized, report = normalize_candles(
        frame, timeframe="5m", now=datetime(2026, 1, 1, 0, 12, tzinfo=UTC)
    )
    assert normalized["timestamp"].tolist() == [pd.Timestamp("2026-01-01T00:00:00Z")]
    assert report.duplicate_timestamps == 1
    assert report.incomplete_candles == 1
    assert "INVALID_OHLCV_REMOVED" in report.issues


def test_candle_normalization_reports_missing_bars() -> None:
    frame = candle_frame(3).iloc[[0, 2]]
    _, report = normalize_candles(
        frame, timeframe="5m", now=datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert report.missing_candles == 1
    assert report.status == "DEGRADED"


def test_unknown_timeframe_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        normalize_candles(candle_frame(3), timeframe="3m")


def test_completed_timeframe_alignment_never_uses_future_context() -> None:
    lower = pd.DataFrame(
        {"timestamp": pd.to_datetime(["2026-01-01T09:55Z", "2026-01-01T10:00Z", "2026-01-01T10:05Z"])}
    )
    higher = pd.DataFrame(
        {"timestamp": pd.to_datetime(["2026-01-01T10:00Z"]), "close": [101.0]}
    )
    aligned = align_completed_timeframe(lower, higher)
    assert pd.isna(aligned.loc[0, "context_close"])
    assert aligned.loc[1, "context_close"] == 101.0
    assert aligned.loc[2, "context_close"] == 101.0


def test_feature_cache_key_contains_every_dependency_and_invalidates(tmp_path: Path) -> None:
    cache = FeatureCache(tmp_path / "cache.sqlite3")
    base = dict(
        market="NSE",
        symbol="LUPIN",
        provider="DHAN",
        data_version="data-v1",
        date_range=("2026-01-01", "2026-02-01"),
        timeframe="5m",
        factor_id="relative_nifty",
        factor_version="1.0.0",
        parameters={"length": 20},
        benchmark_dependency="NIFTY50-v1",
        sector_dependency=None,
    )
    key = FeatureCacheKey(**base)
    changed = FeatureCacheKey(**{**base, "benchmark_dependency": "NIFTY50-v2"})
    assert key.key != changed.key
    cache.put(key, {"values": [1, 2]})
    assert cache.get(key) == {"values": [1, 2]}
    assert cache.invalidate_data_version("data-v1") == 1
    assert cache.get(key) is None


def test_factor_catalog_has_unique_ids_versions_and_all_families() -> None:
    registry = FactorRegistry()
    definitions = registry.list()
    assert len({item.factor_id for item in definitions}) == len(definitions)
    assert all(item.version and item.description and item.misunderstanding for item in definitions)
    assert {item.family for item in definitions} == {
        "TREND_DIRECTION", "TREND_STRENGTH", "MOMENTUM", "VOLATILITY", "VOLUME",
        "RELATIVE_STRENGTH", "MARKET_STRUCTURE", "LIQUIDITY_EXECUTION",
        "MARKET_REGIME", "TIME_SESSION",
    }


@pytest.mark.parametrize(
    ("factor_id", "reason"),
    [
        ("relative_nifty", "benchmark_close"),
        ("relative_sector", "sector_close"),
        ("historical_spread", "ask"),
    ],
)
def test_missing_context_is_explicitly_unsupported(factor_id: str, reason: str) -> None:
    output = FactorEngine().calculate(
        factor_id, candle_frame(), market="NSE", timeframe="5m"
    )
    assert output.status == UNSUPPORTED_DATA_REQUIREMENT
    assert reason in (output.reason or "")


def test_adx_is_strength_not_direction() -> None:
    frame = candle_frame()
    rising = FactorEngine().calculate("adx", frame, market="NSE", timeframe="5m")
    falling_frame = frame.copy()
    falling_frame[["open", "high", "low", "close"]] = 250 - frame[["open", "high", "low", "close"]]
    falling_frame["high"], falling_frame["low"] = (
        falling_frame[["high", "low"]].max(axis=1),
        falling_frame[["high", "low"]].min(axis=1),
    )
    falling = FactorEngine().calculate("adx", falling_frame, market="NSE", timeframe="5m")
    assert rising.values is not None and falling.values is not None
    assert rising.values.dropna().iloc[-1] >= 0
    assert falling.values.dropna().iloc[-1] >= 0
    assert "never direction" in rising.definition.measures.casefold()


def test_rsi_and_volatility_metadata_avoids_directional_misstatements() -> None:
    registry = FactorRegistry()
    assert "not an automatic BUY" in registry.get("rsi_recovery").misunderstanding
    assert "not direction" in registry.get("atr_percentile").misunderstanding
    assert "not RSI" in registry.get("relative_nifty").misunderstanding


def test_session_factor_separates_nse_and_crypto_weekend() -> None:
    frame = candle_frame(3)
    frame["timestamp"] = pd.to_datetime(
        ["2026-01-03T01:00Z", "2026-01-03T10:00Z", "2026-01-03T18:00Z"]
    )
    crypto = FactorEngine().calculate("session_bucket", frame, market="CRYPTO", timeframe="5m")
    nse = FactorEngine().calculate("session_bucket", frame, market="NSE", timeframe="5m")
    assert crypto.values is not None and all(value.endswith("_WEEKEND") for value in crypto.values)
    assert nse.values is not None and all(value.startswith("NSE_") for value in nse.values)


def test_market_context_never_manufactures_benchmark_or_sector_data(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    context = MarketContextService(configured.market_data_file).snapshot("NSE")
    assert context["breadth"]["status"] == UNSUPPORTED_DATA_REQUIREMENT
    assert context["benchmarkDirection"]["status"] == UNSUPPORTED_DATA_REQUIREMENT
    assert context["sectorDirection"]["status"] == UNSUPPORTED_DATA_REQUIREMENT
    crypto = MarketContextService(configured.market_data_file).snapshot("CRYPTO")
    assert crypto["session"]["status"] == "OPEN_24_7"


def test_market_context_relative_return_requires_aligned_benchmark() -> None:
    close = pd.Series([100.0, 102.0, 103.0])
    assert MarketContextService.relative_return(close, None, 1)["status"] == UNSUPPORTED_DATA_REQUIREMENT
    assert MarketContextService.relative_return(close, pd.Series([100.0, 101.0]), 1)["reason"] == "BENCHMARK_ALIGNMENT_INVALID"
    supported = MarketContextService.relative_return(close, pd.Series([100.0, 101.0, 101.5]), 1)
    assert supported["status"] == "SUPPORTED"


def test_next_bar_open_and_conservative_collision() -> None:
    frame = pd.DataFrame([{"open": 100}, {"open": 102}])
    assert next_bar_entry(frame, 0) == (1, 102.0)
    with pytest.raises(ValueError, match="next bar"):
        next_bar_entry(frame, 1)
    candle = pd.Series({"open": 100, "high": 110, "low": 90})
    assert resolve_long_exit(candle, 95, 105) == ("STOP_EXIT", 95)


def test_costs_and_slippage_are_explicit() -> None:
    policy = ExecutionPolicy(buy_cost_bps=10, sell_cost_bps=10, slippage_bps_per_side=5)
    result = costed_return(100, 110, "LONG", policy)
    assert result == pytest.approx({"grossReturn": 0.1, "costRate": 0.003, "netReturn": 0.097})
    snapshot = configuration_snapshot("demo", "1.0", "data-1", policy, {"length": 20})
    assert snapshot["executionPolicy"]["entry"] == "NEXT_BAR_OPEN"
    assert snapshot["configurationId"].startswith("config-")


def test_execution_policy_rejects_lookahead_and_optimistic_collision() -> None:
    invalid = ExecutionPolicy(entry="SAME_BAR_CLOSE", collision="TARGET_FIRST")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="next-bar"):
        invalid.validate()


def test_risk_limits_and_missing_sector_warning() -> None:
    result = RiskService().evaluate(
        open_positions=2,
        proposed_notional=30_000,
        symbol_exposure=0,
        sector_exposure=None,
        daily_pnl=-2_000,
        consecutive_losses=3,
    )
    assert result["accepted"] is False
    assert {"MAXIMUM_OPEN_POSITIONS", "SYMBOL_CONCENTRATION", "SECTOR_DATA_UNAVAILABLE", "MAXIMUM_DAILY_LOSS", "CONSECUTIVE_LOSS_LIMIT"}.issubset(result["reasons"])
    assert result["liveOrdersEnabled"] is False


def test_analytics_uses_inconclusive_and_not_win_rate_alone() -> None:
    result = summarize_returns([0.01, -0.02, 0.01], minimum_trades=30)
    assert result["status"] == "INCONCLUSIVE"
    assert result["netProfit"] == pytest.approx(0)
    assert result["expectancy"] == pytest.approx(0)
    assert maximum_drawdown([0.1, -0.2]) < 0


def test_trade_ledger_reports_execution_and_stability_metrics() -> None:
    trades = [
        {"symbol": "AAA", "entryTimestamp": "2026-01-02T10:00:00Z", "session": "NSE_OPEN", "netReturn": 0.01, "costRate": 0.001, "holdingMinutes": 15, "mae": -0.003, "mfe": 0.012, "exitReason": "TARGET_EXIT"},
        {"symbol": "BBB", "entryTimestamp": "2026-01-02T11:00:00Z", "session": "NSE_MID", "netReturn": -0.005, "costRate": 0.001, "holdingMinutes": 25, "mae": -0.008, "mfe": 0.002, "exitReason": "STOP_EXIT"},
        {"symbol": "CCC", "entryTimestamp": "2026-02-02T12:00:00Z", "session": "NSE_MID", "netReturn": None, "exitReason": "OPEN"},
    ]
    result = summarize_trade_ledger(trades, minimum_trades=5)
    assert result["targetExits"] == 1 and result["stopExits"] == 1 and result["openTrades"] == 1
    assert result["mae"] == pytest.approx(-0.0055)
    assert result["mfe"] == pytest.approx(0.007)
    assert result["tradesPerDay"] == 2
    assert {"monthlyStability", "symbolStability", "sessionStability", "slippageSensitivity"}.issubset(result)


def test_chronological_split_keeps_final_test_untouched() -> None:
    split = chronological_split(100, 0.2, 0.2)
    assert split.training == (0, 60)
    assert split.validation == (60, 80)
    assert split.test == (80, 100)
    assert split.validation[1] == split.test[0]


def test_research_modes_are_bounded_and_tournament_is_single_family() -> None:
    service = ResearchService(lambda request: candle_frame())
    exact = ResearchExperimentRequest(symbol="LUPIN", factorIds=["ema_alignment"])
    assert service.estimate(exact)["plannedEvaluations"] == 1
    invalid = ResearchExperimentRequest(
        mode="TOURNAMENT", symbol="LUPIN", factorIds=["ema_alignment", "rvol"]
    )
    with pytest.raises(ValueError, match="one factor family"):
        service.estimate(invalid)
    forward = ResearchExperimentRequest(
        mode="FORWARD_SELECTION", symbol="LUPIN", factorIds=["ema_alignment", "rvol", "roc"]
    )
    estimate = service.estimate(forward)
    assert estimate["bounded"] is True
    assert estimate["plannedEvaluations"] <= 100


def test_small_research_experiment_stores_versions_split_and_safety() -> None:
    service = ResearchService(lambda request: candle_frame())
    result = service.run(
        ResearchExperimentRequest(
            symbol="LUPIN", factorIds=["ema_alignment"], minimumTrades=5
        ).snapshot(),
        lambda value: None,
        lambda: None,
    )
    assert result["experimentId"].startswith("experiment-")
    assert result["configurationId"].startswith("research-config-")
    assert result["split"]["test"]["startIndex"] == result["split"]["validation"]["endIndexExclusive"]
    assert result["paperOnly"] is True
    assert result["liveOrdersEnabled"] is False


def test_job_idempotency_retry_and_migration(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    service = JobService(repository, maximum_workers=1, maximum_pending=3, retry_limit=1)
    attempts = 0

    def handler(payload, progress, cancel):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")
        progress(50)
        return {"ok": True}

    first = service.submit("TEST", {"value": 1}, handler, "same-key")
    replay = service.submit("TEST", {"value": 1}, handler, "same-key")
    assert replay["jobId"] == first["jobId"]
    assert replay["idempotentReplay"] is True
    for _ in range(50):
        current = repository.get(first["jobId"])
        if current["status"] == "COMPLETE":
            break
        time.sleep(0.05)
    assert current["status"] == "COMPLETE"
    assert current["attempt"] == 2
    assert repository.migrations() == [1]
    service.shutdown()


def test_job_cancellation_is_cooperative(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "cancel.sqlite3")
    service = JobService(repository, maximum_workers=1, maximum_pending=2, retry_limit=0)
    started = threading.Event()

    def handler(payload, progress, cancel):
        started.set()
        while True:
            cancel()
            time.sleep(0.01)

    job = service.submit("LONG", {}, handler, "cancel-key")
    assert started.wait(1)
    service.cancel(job["jobId"])
    for _ in range(50):
        current = repository.get(job["jobId"])
        if current["status"] == "CANCELLED":
            break
        time.sleep(0.02)
    assert current["status"] == "CANCELLED"
    service.shutdown()


def test_strategy_registry_preserves_existing_and_blocks_retired_dispatch() -> None:
    registry = StrategyRegistry()
    assert registry.get("rsi_recovery").status == "ACTIVE"
    assert registry.get("market_aligned_rsi_scalper").status == "RETIRED"
    assert registry.get("market_aligned_vwap_pullback_scalper").status == "RETIRED"
    with pytest.raises(ValueError, match="Retired strategy"):
        registry.validate("market_aligned_vwap_pullback_scalper", "NSE", "5m", for_execution=True)


def test_platform_api_health_catalog_and_safety(tmp_path: Path) -> None:
    runtime = PlatformRuntime.build(settings(tmp_path), lambda request: candle_frame())
    router = create_platform_router(lambda: runtime)
    paths = {route.path for route in router.routes}
    assert {"/platform/health/live", "/platform/health/ready", "/platform/research/experiments", "/platform/jobs/{job_id}"}.issubset(paths)
    health = runtime.health()
    assert health["liveOrdersEnabled"] is False
    assert health["paperOnly"] is True
    assert len(runtime.factors.registry.list()) >= 20
    assert any(row.key == "rsi_recovery" for row in runtime.strategies.list("NSE"))
    assert runtime.instruments.list("NSE", 0, 10)["rows"][0]["symbol"] == "LUPIN"
    runtime.shutdown()
