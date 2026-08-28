from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from backtest_api import (
    BacktestRequest,
    _market_candidate_funnel,
    _market_support_plan,
    run_backtest,
)
from main import IST
from market_aligned_rsi_scalper import (
    STRATEGY_KEY,
    STRATEGY_NAME,
    STRATEGY_VERSION,
    MarketAlignedConfig,
    evaluate_market_alignment,
    load_sector_mapping,
)
from tests.test_recovery_backtest import (
    baseline_rsi,
    injected_indicators,
    state_frame,
)


def trend_frame(
    *,
    step: float,
    periods: int = 40,
    last_volume: float = 5_000.0,
) -> pd.DataFrame:
    index = pd.date_range("2025-01-02 09:15", periods=periods, freq="5min", tz=IST)
    close = [100.0 + step * number for number in range(periods)]
    volume = [1_000.0] * periods
    volume[-1] = last_volume
    return pd.DataFrame(
        {
            "Open": [value - 0.1 for value in close],
            "High": [value + 2.0 for value in close],
            "Low": [value - 0.2 for value in close],
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )


def candidate(frame: pd.DataFrame) -> dict[str, object]:
    return {
        "tradeId": "market:TEST:1",
        "symbol": "TEST",
        "signalTimestamp": frame.index[-1].isoformat(),
        "entryTimestamp": frame.index[-1].isoformat(),
        "rsiAtEntry": 45.0,
    }


def semantic_result(value: object) -> object:
    volatile = {"startedAt", "completedAt", "generatedAt", "runtimeSeconds", "gitCommitSha"}
    if isinstance(value, dict):
        return {
            key: semantic_result(item)
            for key, item in value.items()
            if key not in volatile
        }
    if isinstance(value, list):
        return [semantic_result(item) for item in value]
    return value


def legacy_fingerprint_result(value: object, path: str = "") -> object:
    identity = {"strategyKey", "strategyName", "strategyDescription", "configuration"}
    volatile = {"startedAt", "completedAt", "generatedAt", "runtimeSeconds", "gitCommitSha"}
    if isinstance(value, dict):
        return {
            key: legacy_fingerprint_result(item, f"{path}.{key}" if path else key)
            for key, item in value.items()
            if key not in identity
            and key not in volatile
            and not (key == "strategyVersion" and path.startswith("results"))
        }
    if isinstance(value, list):
        return [legacy_fingerprint_result(item, path) for item in value]
    return value


class FixtureStore:
    @staticmethod
    def universe() -> list[str]:
        return ["TEST"]

    @staticmethod
    def candles(*_args: object, **_kwargs: object) -> pd.DataFrame:
        return state_frame(baseline_rsi())


def test_market_alignment_accepts_only_when_every_completed_bar_gate_passes() -> None:
    stock = trend_frame(step=0.20)
    peer = trend_frame(step=0.08)
    nifty = trend_frame(step=0.05)
    config = MarketAlignedConfig(
        minimum_breadth_symbols=2,
        minimum_sector_members=2,
        minimum_average_traded_value=0,
        sector_by_symbol={"TEST": "DEMO", "PEER": "DEMO"},
    ).validate()
    result = evaluate_market_alignment(
        candidate(stock),
        symbol_frame=stock,
        nifty_frame=nifty,
        universe_frames={"TEST": stock, "PEER": peer},
        config=config,
    )
    assert result["allowed"] is True
    assert result["score"] == 100.0
    assert all(result["gates"].values())


def test_market_alignment_does_not_read_future_candles() -> None:
    stock = trend_frame(step=0.20)
    peer = trend_frame(step=0.08)
    nifty = trend_frame(step=0.05)
    timestamp = stock.index[-1]
    future_index = timestamp + pd.Timedelta(minutes=5)
    config = MarketAlignedConfig(
        minimum_breadth_symbols=2,
        minimum_sector_members=2,
        minimum_average_traded_value=0,
        sector_by_symbol={"TEST": "DEMO", "PEER": "DEMO"},
    ).validate()
    before = evaluate_market_alignment(
        candidate(stock), symbol_frame=stock, nifty_frame=nifty,
        universe_frames={"TEST": stock, "PEER": peer}, config=config,
    )
    stock_after = pd.concat([stock, pd.DataFrame(
        {"Open": [10.0], "High": [10.0], "Low": [1.0], "Close": [1.0], "Volume": [99_000.0]},
        index=pd.DatetimeIndex([future_index]),
    )])
    after = evaluate_market_alignment(
        candidate(stock), symbol_frame=stock_after, nifty_frame=nifty,
        universe_frames={"TEST": stock_after, "PEER": peer}, config=config,
    )
    assert after == before


def test_missing_sector_context_is_rejected_not_fabricated() -> None:
    stock = trend_frame(step=0.20)
    result = evaluate_market_alignment(
        candidate(stock),
        symbol_frame=stock,
        nifty_frame=trend_frame(step=0.05),
        universe_frames={"TEST": stock},
        config=MarketAlignedConfig(
            minimum_breadth_symbols=1,
            minimum_sector_members=1,
            minimum_average_traded_value=0,
        ),
    )
    assert result["allowed"] is False
    assert result["decision"] == "SKIPPED_INSUFFICIENT_MARKET_ALIGNMENT_DATA"
    assert "sector" in result["reason"]
    diagnostic = result["candidateDiagnostic"]
    assert diagnostic["sectorMappingFound"] is False
    assert "MISSING_SECTOR_MAPPING" in diagnostic["rejectionReasons"]
    assert diagnostic["finalStatus"] == "SKIPPED_DATA_UNAVAILABLE"


def test_candidate_diagnostic_stores_every_gate_and_explicit_reasons() -> None:
    stock = trend_frame(step=-0.05, last_volume=1.0)
    result = evaluate_market_alignment(
        candidate(stock),
        symbol_frame=stock,
        nifty_frame=trend_frame(step=-0.05),
        universe_frames={"TEST": stock},
        config=MarketAlignedConfig(
            minimum_breadth_symbols=2,
            minimum_sector_members=2,
            minimum_average_traded_value=1_000_000,
            sector_by_symbol={"TEST": "DEMO"},
        ),
    )
    diagnostic = result["candidateDiagnostic"]
    assert diagnostic["candidateTimestamp"] == candidate(stock)["signalTimestamp"]
    assert diagnostic["previousRsi"] is not None
    assert diagnostic["signalRsi"] == 45.0
    assert diagnostic["niftyDataAvailable"] is True
    assert diagnostic["sectorMappingFound"] is True
    assert diagnostic["sectorDataAvailable"] is False
    assert diagnostic["breadthDataAvailable"] is False
    assert diagnostic["rvolPass"] is False
    assert diagnostic["liquidityPass"] is False
    assert "INSUFFICIENT_SECTOR_MEMBERS" in diagnostic["rejectionReasons"]
    assert "INSUFFICIENT_BREADTH_SYMBOLS" in diagnostic["rejectionReasons"]
    assert "RVOL_FAILED" in diagnostic["rejectionReasons"]
    assert "LIQUIDITY_FAILED" in diagnostic["rejectionReasons"]


def test_off_mode_is_not_evaluated_and_does_not_change_alignment_score() -> None:
    stock = trend_frame(step=0.20)
    peer = trend_frame(step=0.08)
    config = MarketAlignedConfig(
        minimum_breadth_symbols=2,
        minimum_sector_members=2,
        minimum_average_traded_value=0,
        sector_by_symbol={"TEST": "DEMO", "PEER": "DEMO"},
    ).validate()
    evaluation = evaluate_market_alignment(
        candidate(stock), symbol_frame=stock, nifty_frame=trend_frame(step=0.05),
        universe_frames={"TEST": stock, "PEER": peer}, config=config,
    )
    from market_aligned_rsi_scalper import apply_market_alignment_chronologically

    result = apply_market_alignment_chronologically(
        [{"symbol": "TEST", "trades": [candidate(stock)], "events": []}],
        frames_by_symbol={"TEST": stock, "PEER": peer},
        nifty_frame=trend_frame(step=0.05), config=config, oi_mode="OFF",
    )[0]
    diagnostic = result["candidateDiagnostics"][0]
    assert diagnostic["oiMode"] == "OFF"
    assert diagnostic["oiResult"] == "NOT_EVALUATED"
    assert diagnostic["alignmentScore"] == evaluation["score"] == 100.0
    assert diagnostic["executed"] is True


def test_official_constituent_csv_industry_column_is_a_sector_mapping(tmp_path: Path) -> None:
    mapping_file = tmp_path / "constituents.csv"
    mapping_file.write_text(
        "Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Example Ltd.,Information Technology,TEST,EQ,INE000000001\n",
        encoding="utf-8",
    )
    assert load_sector_mapping(mapping_file) == {"TEST": "Information Technology"}


def test_support_plan_is_independent_from_selected_trading_symbols() -> None:
    universe = {"TEST", "PEER", *(f"BREADTH{number:02d}" for number in range(20))}
    plan = _market_support_plan(
        universe=universe,
        requested_symbols=["TEST"],
        config=MarketAlignedConfig(
            minimum_breadth_symbols=10,
            sector_by_symbol={"TEST": "DEMO", "PEER": "DEMO"},
        ),
        breadth_file=None,
        breadth_sample_size=12,
    )
    assert len(plan["breadthSymbols"]) == 12
    assert plan["breadthSymbols"] != ["TEST"]
    assert plan["sectorSymbols"] == ["PEER", "TEST"]


def test_candidate_funnel_is_cumulative_and_reconciles_execution() -> None:
    base = {
        "timeWindowPassed": True, "niftyPass": True, "sectorPass": True,
        "breadthPass": True, "relativeStrengthPass": True, "vwapPass": True,
        "emaPass": True, "rvolPass": True, "liquidityPass": True,
        "roomToTargetPass": True, "scorePass": True, "executed": True,
    }
    second = {**base, "sectorPass": False, "executed": False}
    funnel = _market_candidate_funnel([{
        "rsiArmedCount": 3,
        "candidateDiagnostics": [base, second],
    }])
    assert funnel["rsiArmed"] == 3
    assert funnel["rsiRecoveryCandidates"] == 2
    assert funnel["niftyPassed"] == 2
    assert funnel["sectorPassed"] == 1
    assert funnel["breadthPassed"] == 1
    assert funnel["executedTrades"] == 1


def test_legacy_and_market_aligned_strategies_have_distinct_keys_and_defaults() -> None:
    legacy = BacktestRequest(symbols=["TEST"], strategyMode="rsi_recovery", timeframe="5m")
    aligned = BacktestRequest(
        symbols=["TEST"], strategyMode="market_aligned_rsi_scalper", timeframe="5m"
    )
    assert legacy.strategyMode == "rsi_recovery"
    assert aligned.strategyMode == STRATEGY_KEY
    assert aligned.marketAlignedConfiguration.oiMode == "ADVISORY"
    assert legacy.oiFilterMode == "OFF"


def test_existing_recovery_configuration_still_loads() -> None:
    request = BacktestRequest(
        symbols=["TEST"], strategyMode="rsi_recovery", timeframe="5m",
        rsiArmLow=30, rsiArmHigh=40, rsiRecovery=40,
        emaFast=9, emaSlow=20, minimumConfirmations=2,
    )
    assert request.recovery_config().public_parameters()["rsiArmLow"] == 30
    assert request.recovery_config().public_parameters()["minimumConfirmations"] == 2


def test_oi_mode_changes_cannot_change_rsi_recovery_results() -> None:
    now = datetime(2025, 2, 1, 15, 30, tzinfo=IST)
    with patch("recovery_backtest.calculate_recovery_indicators", side_effect=injected_indicators):
        off = run_backtest(
            BacktestRequest(
                symbols=["TEST"], strategyMode="rsi_recovery", timeframe="5m",
                runId="legacy-regression", oiFilterMode="OFF",
            ),
            FixtureStore(), now,
        )
    with patch("recovery_backtest.calculate_recovery_indicators", side_effect=injected_indicators):
        enforced = run_backtest(
            BacktestRequest(
                symbols=["TEST"], strategyMode="rsi_recovery", timeframe="5m",
                runId="legacy-regression", oiFilterMode="ENFORCED",
            ),
            FixtureStore(), now,
        )
    assert semantic_result(off) == semantic_result(enforced)
    assert off["results"][0]["buySignals"] == 1
    assert len(off["results"][0]["trades"]) == 1
    canonical = json.dumps(
        legacy_fingerprint_result(off), sort_keys=True, separators=(",", ":"), default=str
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == (
        "cacacdab45cf7da2a4f7669deefcc583ca9e7d11cb291fe32e01285b6bc74801"
    )


def test_new_strategy_result_identity_and_configuration_are_separate() -> None:
    request = BacktestRequest(
        symbols=["TEST"], strategyMode=STRATEGY_KEY, timeframe="5m", runId="aligned"
    )
    original = copy.deepcopy(request.marketAlignedConfiguration.model_dump())
    with patch("recovery_backtest.calculate_recovery_indicators", side_effect=injected_indicators):
        response = run_backtest(
            request, FixtureStore(), datetime(2025, 2, 1, 15, 30, tzinfo=IST)
        )
    assert response["metadata"]["strategyKey"] == STRATEGY_KEY
    assert response["metadata"]["strategyName"] == STRATEGY_NAME
    assert response["metadata"]["strategyVersion"] == STRATEGY_VERSION
    assert response["metadata"]["configuration"]["strategy"]["oiMode"] == "ADVISORY"
    assert response["results"][0]["strategyKey"] == STRATEGY_KEY
    assert request.marketAlignedConfiguration.model_dump() == original


def test_both_strategies_run_independently_in_one_deployment() -> None:
    now = datetime(2025, 2, 1, 15, 30, tzinfo=IST)
    with patch("recovery_backtest.calculate_recovery_indicators", side_effect=injected_indicators):
        legacy = run_backtest(
            BacktestRequest(symbols=["TEST"], strategyMode="rsi_recovery", timeframe="5m"),
            FixtureStore(), now,
        )
    with patch("recovery_backtest.calculate_recovery_indicators", side_effect=injected_indicators):
        aligned = run_backtest(
            BacktestRequest(symbols=["TEST"], strategyMode=STRATEGY_KEY, timeframe="5m"),
            FixtureStore(), now,
        )
    assert legacy["metadata"]["strategyKey"] == "rsi_recovery"
    assert aligned["metadata"]["strategyKey"] == STRATEGY_KEY
    assert legacy["summary"]["totalBuySignals"] == 1
    assert aligned["summary"]["marketAlignmentRejectedSignals"] == 1


def test_backtest_selector_shows_both_named_strategies_and_descriptions() -> None:
    source = (
        Path(__file__).parents[1] / "web" / "app" / "backtest" / "backtest-dashboard.tsx"
    ).read_text(encoding="utf-8")
    assert "RSI Recovery Scalping" in source
    assert "Market-Aligned RSI Scalper" in source
    assert "RSI recovery entries using the existing EMA, VWAP and volume confirmation logic." in source
    assert "High-selectivity RSI scalping aligned with NIFTY, sector, breadth" in source
    assert 'strategyMode === "rsi_recovery"' in source
    assert 'strategyMode === "market_aligned_rsi_scalper"' in source
