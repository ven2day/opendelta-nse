from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest
from pydantic import ValidationError

import backtest_api
from crypto_strategy import CryptoPullbackConfig, candle_frame as crypto_candle_frame, generate_signals
from main import calculate_rsi
from market_core import MarketCandle, TIMEFRAME_SECONDS
from opendelta.factors import FactorEngine, FactorOutput, factor_pass_mask
from opendelta.market_data import FeatureCache, FeatureCacheKey, align_completed_timeframe, normalize_candles
from opendelta.research_v2 import (
    DeterministicTradeEngine,
    ResearchEngineV2,
    ResearchExperimentRequestV2,
    SymbolFrames,
    candle_data_reference,
    chronological_boundaries,
)
from opendelta.strategy_adapters import (
    CryptoTrendPullbackAdapter,
    RsiRangeAdapter,
    RsiRecoveryAdapter,
    StrategySignal,
)
from recovery_backtest import RecoveryConfig, simulate_recovery_symbol
from universe_selection import UniverseRepository, UniverseService


def request_v2(**updates: object) -> ResearchExperimentRequestV2:
    values: dict[str, object] = {
        "symbols": ["TEST"],
        "startDate": date(2026, 1, 5),
        "endDate": date(2026, 1, 7),
        "contextTimeframe": "15m",
        "setupTimeframe": "5m",
        "executionTimeframe": "1m",
        "baseStrategyId": "neutral_research_trigger",
        "minimumTrades": 1,
        "maximumTradesPerDay": 20,
        "maximumOpenPositions": 5,
        "quantityPerTrade": 1,
        "capitalPerPosition": 100_000,
        "totalCapital": 1_000_000,
    }
    values.update(updates)
    return ResearchExperimentRequestV2(**values)


def execution_frame(
    *,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    start: str = "2026-01-05 03:46",
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(start, periods=len(opens), freq="1min", tz="UTC"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000.0] * len(opens),
        }
    )


def signal(frame: pd.DataFrame, index: int, *, symbol: str = "TEST", side: str = "LONG") -> StrategySignal:
    return StrategySignal(
        symbol=symbol,
        signal_index=index,
        signal_timestamp=pd.Timestamp(frame.loc[index, "timestamp"]),
        side=side,  # type: ignore[arg-type]
        explanation="deterministic fixture",
    )


def run_lifecycle(
    frame: pd.DataFrame,
    signals: list[StrategySignal],
    **request_updates: object,
) -> dict:
    request = request_v2(**request_updates)
    return DeterministicTradeEngine().run(
        request,
        {"TEST": frame},
        signals,
        (pd.Timestamp(frame.timestamp.iloc[0]), pd.Timestamp(frame.timestamp.iloc[-1]) + pd.Timedelta(1, "ns")),
    )


def test_research_v2_request_is_versioned_and_defaults_are_market_specific() -> None:
    nse = request_v2(contextTimeframe=None, setupTimeframe=None, executionTimeframe=None)
    crypto = request_v2(
        market="CRYPTO",
        provider="OKX",
        contextTimeframe=None,
        setupTimeframe=None,
        executionTimeframe=None,
    )
    assert nse.researchVersion == "2"
    assert (nse.contextTimeframe, nse.setupTimeframe, nse.executionTimeframe) == ("15m", "5m", "1m")
    assert (crypto.contextTimeframe, crypto.setupTimeframe, crypto.executionTimeframe) == ("1h", "15m", "5m")
    assert nse.totalCapital == 1_000_000


def test_frozen_saved_universe_resolves_by_id_version_or_active_without_path_access(tmp_path) -> None:
    repository = UniverseRepository(tmp_path / "universe")
    record = {
        "universeId": "fixture-id",
        "universeVersion": "LIVE-20260105-001",
        "frozen": True,
        "selectedSymbols": ["AAA", "BBB"],
    }
    repository._write_json(repository.active_path, record)
    service = UniverseService(repository, tmp_path / "market.csv")
    for identifier in ("active", "fixture-id", "LIVE-20260105-001"):
        assert service.get_frozen_universe(identifier)[0] == ["AAA", "BBB"]
    with pytest.raises(ValueError, match="identifier is invalid"):
        service.get_frozen_universe("../../secret")
    request = request_v2(symbols=[], universeId="fixture-id")
    engine = ResearchEngineV2(lambda _: pd.DataFrame(), universe_resolver=lambda _: ["AAA", "BBB"])
    assert engine.symbols(request) == ["AAA", "BBB"]


@pytest.mark.parametrize(
    "updates, message",
    [
        ({"symbols": [], "universeId": None}, "either symbols"),
        ({"universeId": "saved", "symbols": ["TEST"]}, "either symbols"),
        ({"startDate": date(2026, 1, 7), "endDate": date(2026, 1, 5)}, "startDate"),
        ({"trainingFraction": 0.7, "validationFraction": 0.2, "testFraction": 0.2}, "sum to 1"),
        ({"capitalPerPosition": 2_000_000}, "cannot exceed"),
        ({"market": "CRYPTO", "provider": "DHAN"}, "requires OKX or VALR"),
    ],
)
def test_research_v2_request_rejects_invalid_combinations(updates: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        request_v2(**updates)


def test_exact_mode_rejects_two_candidates_from_one_family() -> None:
    request = request_v2(factorSelections=["roc", "macd_histogram_acceleration"])
    with pytest.raises(ValueError, match="one candidate per factor family"):
        ResearchEngineV2(lambda _: pd.DataFrame()).validate(request)


def test_base_strategy_locks_its_existing_momentum_family() -> None:
    request = request_v2(
        baseStrategyId="rsi_recovery",
        factorSelections=["roc"],
    )
    with pytest.raises(ValueError, match="MOMENTUM is locked"):
        ResearchEngineV2(lambda _: pd.DataFrame()).validate(request)


def test_rsi_range_adapter_has_signal_parity_with_legacy_entry_rule() -> None:
    closes = [100.0] * 20 + [90, 85, 80, 82, 84, 86, 88, 90, 92]
    frame = execution_frame(
        opens=closes,
        highs=[value + 0.2 for value in closes],
        lows=[value - 0.2 for value in closes],
        closes=closes,
    )
    rsi = calculate_rsi(frame["close"], 14)
    legacy_indices = list(frame.index[backtest_api._entry_signal(rsi, 20, 40)])
    adapter = RsiRangeAdapter().signals(
        "TEST", frame, "5m", {"rsiLength": 14, "entryLow": 20, "entryHigh": 40}
    )
    assert [row.signal_index for row in adapter] == legacy_indices == [25]


def test_rsi_recovery_adapter_has_signal_parity_with_existing_engine() -> None:
    closes = (
        [100 + index * 0.4 for index in range(20)]
        + [108 - index for index in range(12)]
        + [94.4, 94.8, 95.2, 95.8, 96.4, 97.2, 98.4]
        + [98.4 - index * 0.9 for index in range(12)]
        + [88.5, 89, 90, 91, 92, 94, 97]
    )
    frame = execution_frame(
        opens=closes,
        highs=[value + 0.4 for value in closes],
        lows=[value - 0.4 for value in closes],
        closes=closes,
    )
    parameters = {
        "emaEnabled": False,
        "vwapEnabled": False,
        "volumeEnabled": False,
        "minimumConfirmations": 0,
    }
    config = RecoveryConfig(
        ema_enabled=False,
        vwap_enabled=False,
        volume_enabled=False,
        minimum_confirmations=0,
        target_pct=1_000_000,
        execution_model="NEXT_BAR_OPEN",
    )
    legacy = simulate_recovery_symbol(
        "TEST",
        frame.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}).set_index("timestamp"),
        timeframe="5m",
        config=config,
        run_id="parity",
    )
    expected = sorted({int(trade["entryBarIndex"]) - 1 for trade in legacy["trades"]})
    adapter = RsiRecoveryAdapter().signals("TEST", frame, "5m", parameters)
    assert [row.signal_index for row in adapter] == expected == [38, 55]


def test_crypto_adapter_has_signal_parity_with_existing_generator() -> None:
    closes = [100 + index * 0.4 for index in range(60)] + [124 - index * 0.6 for index in range(10)] + [125]
    opens = [closes[0], *closes[:-1]]
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=len(closes), freq="15min", tz="UTC"),
            "open": opens,
            "high": [max(left, right) + 0.2 for left, right in zip(opens, closes, strict=True)],
            "low": [min(left, right) - 0.2 for left, right in zip(opens, closes, strict=True)],
            "close": closes,
            "volume": [100.0] * 70 + [1_000.0],
        }
    )
    config = CryptoPullbackConfig().validate()
    seconds = TIMEFRAME_SECONDS["15m"]
    candles = [
        MarketCandle.build(
            provider="OKX",
            provider_symbol="BTC-USDT",
            timeframe="15m",
            open_time=row.timestamp.to_pydatetime() - pd.Timedelta(seconds=seconds),
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            base_volume=row.volume,
        )
        for row in frame.itertuples(index=False)
    ]
    expected = [(row.signal_index, row.side) for row in generate_signals(crypto_candle_frame(candles, config), config)]
    actual = CryptoTrendPullbackAdapter().signals("BTC-USDT", frame, "15m", {}, "LONG")
    assert [(row.signal_index, "BUY" if row.side == "LONG" else "SELL") for row in actual] == expected == [(70, "BUY")]


def test_trade_lifecycle_fixture_reconciles_target_stop_time_and_mae_mfe() -> None:
    frame = execution_frame(
        opens=[100] * 6 + [100.1],
        highs=[100.1, 100.6, 100.1, 100.6, 100.1, 100.2, 100.2],
        lows=[99.9, 99.4, 99.9, 99.8, 99.9, 99.8, 99.9],
        closes=[100, 100, 100, 100, 100, 100, 100.1],
    )
    result = run_lifecycle(
        frame,
        [signal(frame, 0), signal(frame, 2), signal(frame, 4)],
        targetPct=0.5,
        stopLossPct=0.5,
        maximumHoldingBars=2,
    )
    ledger = result["tradeLedger"]
    assert [row["exitReason"] for row in ledger] == ["STOP_FIRST_COLLISION", "TARGET_EXIT", "TIME_EXIT"]
    assert [row["entryPrice"] for row in ledger] == [100, 100, 100]
    assert [row["exitPrice"] for row in ledger] == pytest.approx([99.5, 100.5, 100.1])
    assert [row["netPnl"] for row in ledger] == pytest.approx([-0.5, 0.5, 0.1])
    assert result["metrics"]["netProfitCurrency"] == pytest.approx(0.1)
    assert result["metrics"]["targetExits"] == 1
    assert result["metrics"]["stopExits"] == 1
    assert result["metrics"]["timeExits"] == 1
    assert ledger[0]["mae"] == pytest.approx(-0.006)
    assert ledger[1]["mfe"] == pytest.approx(0.006)


def test_trade_costs_and_both_sides_of_slippage_are_realized() -> None:
    frame = execution_frame(
        opens=[100, 100],
        highs=[100.1, 100.7],
        lows=[99.9, 99.7],
        closes=[100, 100.6],
    )
    result = run_lifecycle(
        frame,
        [signal(frame, 0)],
        targetPct=0.5,
        stopLossPct=0.5,
        buyCostBps=10,
        sellCostBps=20,
        slippageBpsPerSide=10,
    )
    trade = result["tradeLedger"][0]
    expected_entry = 100 * 1.001
    expected_raw_target = expected_entry * 1.005
    expected_exit = expected_raw_target * 0.999
    expected_cost = expected_entry * 0.001 + expected_exit * 0.002
    assert trade["entryPrice"] == pytest.approx(expected_entry)
    assert trade["exitPrice"] == pytest.approx(expected_exit)
    assert trade["costs"] == pytest.approx(expected_cost)
    assert trade["netPnl"] == pytest.approx((expected_exit - expected_entry) - expected_cost)


@pytest.mark.parametrize(
    "side,next_open,expected_reason,expected_exit",
    [
        ("LONG", 98.0, "STOP_GAP", 98.0),
        ("SHORT", 102.0, "STOP_GAP", 102.0),
    ],
)
def test_price_gaps_exit_at_the_available_open(side: str, next_open: float, expected_reason: str, expected_exit: float) -> None:
    frame = execution_frame(
        opens=[100, 100, next_open],
        highs=[100.1, 100.2, next_open + 0.1],
        lows=[99.9, 99.8, next_open - 0.1],
        closes=[100, 100, next_open],
    )
    result = run_lifecycle(
        frame,
        [signal(frame, 0, side=side)],
        direction=side,
        targetPct=0.5,
        stopLossPct=0.5,
        maximumHoldingBars=10,
    )
    trade = result["tradeLedger"][0]
    assert (trade["exitReason"], trade["exitPrice"]) == (expected_reason, expected_exit)


def test_open_trade_is_reported_separately_and_json_is_finite() -> None:
    frame = execution_frame(
        opens=[100, 100],
        highs=[100.1, 100.2],
        lows=[99.9, 99.8],
        closes=[100, 100],
    )
    result = run_lifecycle(frame, [signal(frame, 0)], targetPct=10, stopLossPct=10)
    assert result["tradeLedger"] == []
    assert len(result["openPositions"]) == 1
    assert result["metrics"]["profitFactor"] is None
    assert result["metrics"]["profitFactorState"] == "NO_CLOSED_TRADES"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "high,low,profit_factor,profit_state",
    [
        (100.6, 99.8, None, "NO_LOSING_TRADES"),
        (100.2, 99.4, 0.0, "DEFINED"),
    ],
)
def test_all_win_and_all_loss_results_use_finite_profit_factor_states(
    high: float,
    low: float,
    profit_factor: float | None,
    profit_state: str,
) -> None:
    frame = execution_frame(
        opens=[100, 100],
        highs=[100.1, high],
        lows=[99.9, low],
        closes=[100, 100],
    )
    result = run_lifecycle(frame, [signal(frame, 0)], targetPct=0.5, stopLossPct=0.5)
    assert result["metrics"]["profitFactor"] == profit_factor
    assert result["metrics"]["profitFactorState"] == profit_state
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "one_per_symbol,max_positions,expected_reason",
    [
        (True, 5, "ONE_OPEN_POSITION_PER_SYMBOL"),
        (False, 1, "MAXIMUM_OPEN_POSITIONS"),
    ],
)
def test_position_limits_prevent_prohibited_overlap(
    one_per_symbol: bool,
    max_positions: int,
    expected_reason: str,
) -> None:
    frame = execution_frame(
        opens=[100, 100, 100],
        highs=[100.1, 100.2, 100.2],
        lows=[99.9, 99.8, 99.8],
        closes=[100, 100, 100],
    )
    result = run_lifecycle(
        frame,
        [signal(frame, 0), signal(frame, 1)],
        targetPct=10,
        stopLossPct=10,
        oneOpenPositionPerSymbol=one_per_symbol,
        maximumOpenPositions=max_positions,
    )
    assert len(result["openPositions"]) == 1
    assert result["rejectedSignals"][-1]["reason"] == expected_reason


def test_position_sizing_enforces_capital_and_risk_budgets() -> None:
    frame = execution_frame(
        opens=[100, 100, 100],
        highs=[100.1, 100.2, 100.2],
        lows=[99.9, 99.8, 99.8],
        closes=[100, 100, 100],
    )
    result = run_lifecycle(
        frame,
        [signal(frame, 0), signal(frame, 1)],
        targetPct=10,
        stopLossPct=10,
        oneOpenPositionPerSymbol=False,
        maximumOpenPositions=5,
        quantityPerTrade=10,
        capitalPerPosition=100,
        totalCapital=150,
        riskPerTradePct=10,
    )
    assert result["openPositions"][0]["quantity"] == 1
    assert result["openPositions"][0]["capital"] == 100
    assert result["rejectedSignals"][-1]["reason"] == "CAPITAL_LIMIT"


@pytest.mark.parametrize(
    "updates, expected_reason",
    [
        ({"maximumTradesPerDay": 1}, "MAXIMUM_TRADES_PER_DAY"),
        ({"stopAfterFirstLoss": True}, "STOP_AFTER_FIRST_LOSS"),
        ({"maximumDailyLossPct": 0.00001}, "MAXIMUM_DAILY_LOSS"),
    ],
)
def test_daily_trade_risk_controls_reject_later_signals(updates: dict, expected_reason: str) -> None:
    frame = execution_frame(
        opens=[100] * 4,
        highs=[100.1] * 4,
        lows=[99.9, 99.4, 99.9, 99.4],
        closes=[100] * 4,
    )
    result = run_lifecycle(
        frame,
        [signal(frame, 0), signal(frame, 2)],
        targetPct=0.5,
        stopLossPct=0.5,
        **updates,
    )
    assert result["tradeLedger"][0]["exitReason"] == "STOP_EXIT"
    assert result["rejectedSignals"][-1]["reason"] == expected_reason


def test_factor_predicates_use_explicit_semantics_not_a_universal_percentile() -> None:
    engine = FactorEngine()
    cases = [
        ("ema_alignment", pd.Series([-1, 0, 1]), {}, [False, False, True]),
        ("adx", pd.Series([10, 25, 40]), {"minimum": 25}, [False, True, True]),
        ("atr_percentile", pd.Series([0.1, 0.5, 0.9]), {"minimum": 0.2, "maximum": 0.8}, [False, True, False]),
        ("historical_spread", pd.Series([5, 10, 15]), {"maximum": 10}, [True, True, False]),
        ("market_regime", pd.Series(["TREND", "RANGE", "EXPANSION"]), {"categories": ["TREND"]}, [True, False, False]),
        ("rsi_recovery", pd.Series([0, 1, 0]), {}, [False, True, False]),
    ]
    for factor_id, values, parameters, expected in cases:
        output = FactorOutput(engine.registry.get(factor_id), "SUPPORTED", values)
        assert factor_pass_mask(output, parameters).tolist() == expected


def test_factor_context_dependencies_do_not_eagerly_read_missing_frame_columns() -> None:
    engine = FactorEngine()
    length = 25
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-05", periods=length, freq="5min", tz="UTC"),
            "open": range(100, 100 + length),
            "high": range(101, 101 + length),
            "low": range(99, 99 + length),
            "close": range(100, 100 + length),
            "volume": [1_000.0] * length,
        }
    )
    benchmark = pd.Series(range(200, 200 + length), dtype=float)
    spread = engine.calculate(
        "historical_spread",
        frame,
        market="NSE",
        timeframe="5m",
        context={"bid": frame.close - 0.05, "ask": frame.close + 0.05},
    )
    relative = engine.calculate(
        "relative_nifty",
        frame,
        market="NSE",
        timeframe="5m",
        parameters={"length": 2},
        context={"benchmark_close": benchmark},
    )
    slippage = engine.calculate(
        "slippage_sensitivity",
        frame,
        market="NSE",
        timeframe="5m",
        context={"trade_returns": pd.Series([0.01] * length)},
    )
    assert spread.status == relative.status == slippage.status == "SUPPORTED"
    assert spread.values is not None and spread.values.dropna().iloc[0] > 0
    assert relative.values is not None and relative.values.dropna().map(pd.notna).all()
    assert slippage.values is not None and slippage.values.dropna().iloc[0] == pytest.approx(0.009)


def test_factor_warmup_cross_parameter_finite_and_category_rules() -> None:
    engine = FactorEngine()
    frame = execution_frame(
        opens=[100.0] * 140,
        highs=[101.0] * 140,
        lows=[99.0] * 140,
        closes=[100.0] * 140,
    )
    output = engine.calculate("rvol", frame, market="NSE", timeframe="5m", parameters={"length": 5})
    assert output.values is not None
    assert output.values.iloc[:20].isna().all()
    assert not output.values.dropna().isin([float("inf"), float("-inf")]).any()
    with pytest.raises(ValueError, match="fast must be less than slow"):
        engine.calculate("ema_alignment", frame, market="NSE", timeframe="5m", parameters={"fast": 50, "slow": 20})
    session = engine.calculate("session_bucket", frame, market="NSE", timeframe="5m")
    assert session.values is not None
    assert session.values.iloc[0] is None
    assert set(session.values.dropna()).issubset({"NSE_OPEN", "NSE_MID", "NSE_CLOSE", "CLOSED_SESSION"})


def test_nse_sessions_and_opening_range_are_anchored_to_exchange_hours() -> None:
    engine = FactorEngine()
    timestamps = pd.to_datetime(
        [
            "2026-01-05T03:40Z",  # 09:10 IST, closed
            "2026-01-05T03:45Z",
            "2026-01-05T03:50Z",
            "2026-01-05T03:55Z",
            "2026-01-05T04:00Z",
            "2026-01-05T10:00Z",  # 15:30 IST, still the completed close boundary
            "2026-01-05T10:01Z",
        ]
    )
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100, 100, 100, 100, 103, 103, 103],
            "high": [999, 100, 101, 102, 104, 104, 104],
            "low": [99, 99, 99, 99, 102, 102, 102],
            "close": [100, 100, 100, 100, 103, 103, 103],
            "volume": [1_000.0] * 7,
        }
    )
    opening = engine.calculate(
        "opening_range_breakout", frame, market="NSE", timeframe="5m", parameters={"bars": 3}
    )
    session = engine.calculate("session_bucket", frame, market="NSE", timeframe="5m")
    assert opening.values is not None and opening.values.iloc[4] == 1
    assert session.values is not None
    assert session.values.iloc[4] == "NSE_OPEN"
    assert session.values.iloc[5] == "NSE_CLOSE"
    assert session.values.iloc[6] == "CLOSED_SESSION"


def test_crypto_sessions_are_utc_and_explicitly_distinguish_weekends() -> None:
    engine = FactorEngine()
    timestamps = pd.to_datetime(
        ["2026-01-09T07:00Z", "2026-01-09T12:00Z", "2026-01-09T20:00Z", "2026-01-10T07:00Z"]
    )
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0] * 4,
            "high": [101.0] * 4,
            "low": [99.0] * 4,
            "close": [100.0] * 4,
            "volume": [1_000.0] * 4,
        }
    )
    output = engine.calculate("session_bucket", frame, market="CRYPTO", timeframe="5m")
    assert output.values is not None
    assert output.values.tolist() == [None, "EUROPE_UTC_WEEKDAY", "AMERICAS_UTC_WEEKDAY", "ASIA_UTC_WEEKEND"]


def test_multitimeframe_alignment_obeys_close_boundaries_and_first_context() -> None:
    lower = pd.DataFrame(
        {"timestamp": pd.to_datetime(["2026-01-05T03:59Z", "2026-01-05T04:00Z", "2026-01-05T04:14Z", "2026-01-05T04:15Z"])}
    )
    higher = pd.DataFrame(
        {"timestamp": pd.to_datetime(["2026-01-05T04:00Z", "2026-01-05T04:15Z"]), "value": [1, 2]}
    )
    aligned = align_completed_timeframe(lower, higher, market="NSE")
    assert pd.isna(aligned.loc[0, "context_value"])
    assert aligned["context_value"].iloc[1:].tolist() == [1, 1, 2]
    available = aligned.dropna(subset=["context_available_at"])
    assert (available["context_available_at"] <= available["timestamp"]).all()


def test_nse_multitimeframe_alignment_never_fills_overnight_or_weekend() -> None:
    higher = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-09T10:00Z"]), "value": [7]})
    monday = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-12T03:46Z"])})
    nse = align_completed_timeframe(monday, higher, market="NSE")
    crypto = align_completed_timeframe(monday, higher, market="CRYPTO")
    assert pd.isna(nse.loc[0, "context_value"])
    assert crypto.loc[0, "context_value"] == 7


def test_nse_data_quality_does_not_report_closed_overnight_as_missing_candles() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-09T10:00Z", "2026-01-12T03:46Z"]),
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.0, 101.0],
            "volume": [1_000.0, 1_000.0],
        }
    )
    _, nse = normalize_candles(
        frame,
        timeframe="1m",
        now=pd.Timestamp("2026-01-12T04:00Z").to_pydatetime(),
        timestamp_represents="CLOSE",
        market="NSE",
    )
    _, crypto = normalize_candles(
        frame,
        timeframe="1m",
        now=pd.Timestamp("2026-01-12T04:00Z").to_pydatetime(),
        timestamp_represents="CLOSE",
        market="CRYPTO",
    )
    assert nse.missing_candles == 0
    assert crypto.missing_candles > 0


def test_chronological_splits_are_disjoint_and_final_test_is_last() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=20, freq="1h", tz="UTC"))
    split = chronological_boundaries(timestamps, 0.6, 0.2)
    assert split.training[1] == split.validation[0]
    assert split.validation[1] == split.test[0]
    assert split.training[1] < split.test[0]
    assert split.test[1] > timestamps[-1]


def test_feature_cache_keys_exact_data_and_reports_hit_miss_invalidation(tmp_path) -> None:
    cache = FeatureCache(tmp_path / "features.sqlite3")
    key = FeatureCacheKey(
        market="NSE",
        symbol="TEST",
        provider="DHAN",
        data_version="sha256:one",
        date_range=("2026-01-01", "2026-01-02"),
        timeframe="5m",
        factor_id="rvol",
        factor_version="1.0.0",
        parameters={"length": 20},
        benchmark_dependency=None,
        sector_dependency=None,
        session_calendar_version="NSE-SESSION-2026.1",
    )
    assert cache.get(key) is None
    cache.put(key, [None, 1.2])
    assert cache.get(key) == [None, 1.2]
    assert cache.invalidate_data_version("sha256:one") == 1
    metrics = {key: cache.health()[key] for key in ("hits", "misses", "writes", "invalidations")}
    assert metrics == {"hits": 1, "misses": 1, "writes": 1, "invalidations": 1}


def test_candle_data_reference_changes_when_any_exact_candle_changes() -> None:
    frame = execution_frame(
        opens=[100, 100], highs=[101, 101], lows=[99, 99], closes=[100, 100],
    )
    changed = frame.astype({"close": float}).copy()
    changed.loc[1, "close"] = 100.01
    first = candle_data_reference("NSE", "DHAN", {"TEST": {"1m": frame}})
    second = candle_data_reference("NSE", "DHAN", {"TEST": {"1m": changed}})
    assert first.startswith("sha256:")
    assert first != second


def test_feature_cache_is_connected_to_actual_factor_calculation(tmp_path) -> None:
    frame = execution_frame(
        opens=[100.0] * 40,
        highs=[101.0] * 40,
        lows=[99.0] * 40,
        closes=[100.0] * 40,
    )
    data = SymbolFrames("TEST", {"15m": frame, "5m": frame, "1m": frame}, {}, "sha256:fixture")
    cache = FeatureCache(tmp_path / "wired.sqlite3")
    engine = ResearchEngineV2(lambda _: frame, feature_cache=cache)
    request = request_v2(factorSelections=["rvol"], factorParameters={"rvol": {"length": 20, "minimum": 1.0}})
    first, problem = engine._factor_values(request, data, "rvol")
    second, second_problem = engine._factor_values(request, data, "rvol")
    assert problem is second_problem is None
    assert first is not None and second is not None and first.equals(second)
    health = cache.health()
    assert {key: health[key] for key in ("hits", "misses", "writes", "invalidations")} == {
        "hits": 1,
        "misses": 1,
        "writes": 1,
        "invalidations": 0,
    }


def test_deterministic_research_smoke_has_real_nonzero_final_trade_ledger() -> None:
    frame = execution_frame(
        opens=[100.0] * 12,
        highs=[100.6] * 12,
        lows=[99.8] * 12,
        closes=[100.0] * 12,
        start="2026-01-05 03:50",
    )
    request = request_v2(
        contextTimeframe="5m",
        setupTimeframe="5m",
        executionTimeframe="5m",
        strategyParameters={"warmupBars": 1},
        targetPct=0.5,
        stopLossPct=0.5,
        maximumHoldingBars=5,
    )
    engine = ResearchEngineV2(lambda _: frame)
    result = engine.run(request, lambda _: None, lambda: None)
    final = result["untouchedTestResult"]
    assert final["metrics"]["tradeCount"] == 2
    assert final["metrics"]["netProfitCurrency"] == pytest.approx(1.0)
    assert [trade["exitReason"] for trade in final["tradeLedger"]] == ["TARGET_EXIT", "TARGET_EXIT"]
    assert [trade["entryPrice"] for trade in final["tradeLedger"]] == [100.0, 100.0]
    assert [trade["exitPrice"] for trade in final["tradeLedger"]] == pytest.approx([100.5, 100.5])
    assert result["paperOnly"] is True
    assert result["liveOrdersEnabled"] is False
    assert result["researchValidity"] == "RESEARCH_V2_REAL_TRADE_LIFECYCLE"
    json.dumps(result, allow_nan=False)


def scripted_metrics(score: float) -> dict:
    return {
        "status": "CONCLUSIVE",
        "tradeCount": 10,
        "netProfitCurrency": score * 100,
        "expectancy": score,
        "profitFactor": max(0.1, score + 1),
        "maximumDrawdown": -0.1,
        "returnToDrawdown": score,
        "mae": -0.01,
        "mfe": 0.02,
        "averageHoldingMinutes": 5,
        "monthlyStability": {"positiveRate": score},
    }


def scripted_engine(monkeypatch: pytest.MonkeyPatch, request: ResearchExperimentRequestV2):
    frame = execution_frame(
        opens=[100.0] * 20,
        highs=[101.0] * 20,
        lows=[99.0] * 20,
        closes=[100.0] * 20,
    )
    data = SymbolFrames("TEST", {"15m": frame, "5m": frame, "1m": frame}, {}, "sha256:scripted")
    engine = ResearchEngineV2(lambda _: frame)
    calls: list[tuple[tuple[str, ...], tuple[pd.Timestamp, pd.Timestamp]]] = []
    monkeypatch.setattr(engine, "_load", lambda *_: ({"TEST": data}, []))

    def evaluate(_request, _loaded, factor_ids, bounds):
        calls.append((tuple(factor_ids), bounds))
        score = 0.1 + len(factor_ids)
        if "rvol" in factor_ids:
            score += 0.2
        return {
            "status": "CONCLUSIVE",
            "metrics": scripted_metrics(score),
            "tradeLedger": [{"netPnl": score}],
            "openPositions": [],
            "rejectedSignals": [],
            "unsupported": [],
        }

    monkeypatch.setattr(engine, "_evaluation", evaluate)
    split = chronological_boundaries(list(frame.timestamp), request.trainingFraction, request.validationFraction)
    return engine, calls, split


def test_tournament_replaces_only_one_family_and_compares_every_candidate(monkeypatch) -> None:
    request = request_v2(mode="TOURNAMENT", factorSelections=["roc", "macd_histogram_acceleration"])
    engine, calls, split = scripted_engine(monkeypatch, request)
    result = engine.run(request, lambda _: None, lambda: None)
    configurations = result["evaluatedConfigurations"]
    assert [row["factorIds"] for row in configurations] == [["roc"], ["macd_histogram_acceleration"]]
    assert all(row["validationDelta"]["tradeCount"] == 0 for row in configurations)
    assert {(ids, bounds) for ids, bounds in calls if bounds == split.test} == {
        ((), split.test),
        (("roc",), split.test),
        (("macd_histogram_acceleration",), split.test),
    }


def test_forward_selection_uses_no_final_test_data_until_selection_finishes(monkeypatch) -> None:
    request = request_v2(mode="FORWARD_SELECTION", factorSelections=["roc", "rvol"], beamWidth=2)
    engine, calls, split = scripted_engine(monkeypatch, request)
    result = engine.run(request, lambda _: None, lambda: None)
    test_calls = [(ids, bounds) for ids, bounds in calls if bounds == split.test]
    assert test_calls == [(tuple(result["selectedFactorIds"]), split.test)]
    assert set(result["selectedFactorIds"]) == {"roc", "rvol"}
    assert result["untouchedTestResult"]["tradeLedger"]
    json.dumps(result, allow_nan=False)
