from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from backend.markets.nse.oi_regime import (
    FuturesOiObservation,
    NiftyOiConfig,
    OiRegimeRepository,
    OptionOiObservation,
    apply_oi_filter_chronologically,
    chronological_walk_forward_folds,
    classify_buildup,
    combine_regime_components,
    decide_long_trade,
    option_contract_is_eligible,
    option_direction,
    score_futures,
    score_options,
    score_spot_trend,
    select_atm_strikes,
    select_expiry,
)


IST = ZoneInfo("Asia/Kolkata")
EVALUATION = datetime(2026, 8, 27, 10, 15, tzinfo=IST)
EXPIRY = date(2026, 9, 3)


def option(
    *,
    strike: float = 25_050,
    option_type: str = "CALL",
    timestamp: datetime = EVALUATION,
    ltp: float = 101,
    oi: float = 1_100,
    iv: float | None = 12,
    volume: float = 1_000,
    bid: float = 100,
    ask: float = 102,
    expiry: date = EXPIRY,
) -> OptionOiObservation:
    return OptionOiObservation(
        timestamp=timestamp,
        source_timestamp=timestamp,
        underlying="NIFTY",
        expiry=expiry,
        strike=strike,
        option_type=option_type,  # type: ignore[arg-type]
        security_id=f"{expiry}-{strike}-{option_type}",
        ltp=ltp,
        previous_ltp=None,
        open_interest=oi,
        previous_open_interest=None,
        oi_change=None,
        oi_change_pct=None,
        volume=volume,
        implied_volatility=iv,
        bid=bid,
        ask=ask,
        spot_price=25_050,
        distance_from_atm=0,
        data_source="TEST",
        ingestion_timestamp=timestamp,
    )


def option_pairs(
    *,
    current_ltp: float = 101,
    previous_ltp: float = 100,
    current_oi: float = 1_100,
    previous_oi: float = 1_000,
    current_iv: float | None = 12,
    previous_iv: float | None = 11,
) -> tuple[list[OptionOiObservation], list[OptionOiObservation]]:
    current: list[OptionOiObservation] = []
    previous: list[OptionOiObservation] = []
    old_time = EVALUATION - timedelta(minutes=15)
    for strike in (25_000.0, 25_050.0, 25_100.0):
        for option_type in ("CALL", "PUT"):
            current.append(option(strike=strike, option_type=option_type, ltp=current_ltp, oi=current_oi, iv=current_iv))
            previous.append(option(strike=strike, option_type=option_type, timestamp=old_time, ltp=previous_ltp, oi=previous_oi, iv=previous_iv))
    return current, previous


def future(
    *,
    timestamp: datetime = EVALUATION,
    price: float = 25_100,
    oi: float = 11_000,
    security_id: str = "FUT-1",
    expiry: date = EXPIRY,
) -> FuturesOiObservation:
    return FuturesOiObservation(
        timestamp=timestamp,
        source_timestamp=timestamp,
        expiry=expiry,
        security_id=security_id,
        futures_price=price,
        previous_price=None,
        open_interest=oi,
        previous_open_interest=None,
        price_change_pct=None,
        oi_change_pct=None,
        volume=5_000,
        spot_price=25_050,
        basis=price - 25_050,
        data_source="TEST",
        ingestion_timestamp=timestamp,
    )


@pytest.mark.parametrize(
    ("price", "oi", "expected"),
    [
        (1, 1, "LONG_BUILDUP"),
        (-1, 1, "SHORT_BUILDUP"),
        (1, -1, "SHORT_COVERING"),
        (-1, -1, "LONG_UNWINDING"),
    ],
)
def test_option_and_futures_quadrant_classification(price: float, oi: float, expected: str) -> None:
    assert classify_buildup(price, oi) == expected


def test_tiny_or_missing_changes_are_neutral() -> None:
    assert classify_buildup(0.049, 50) == "NEUTRAL"
    assert classify_buildup(10, 0.49) == "NEUTRAL"
    assert classify_buildup(None, 5) == "NEUTRAL"


def test_call_and_put_direction_interpretation() -> None:
    assert [option_direction("CALL", state) for state in ("LONG_BUILDUP", "SHORT_COVERING", "SHORT_BUILDUP", "LONG_UNWINDING")] == [1, 1, -1, -1]
    assert [option_direction("PUT", state) for state in ("LONG_BUILDUP", "SHORT_COVERING", "SHORT_BUILDUP", "LONG_UNWINDING")] == [-1, -1, 1, 1]


def test_atm_selection_and_expiry_rollover_are_deterministic() -> None:
    assert select_atm_strikes([24_900, 24_950, 25_000, 25_050, 25_100], 25_026, 1) == [25_000.0, 25_050.0, 25_100.0]
    expiries = [date(2026, 8, 27), date(2026, 9, 3)]
    assert select_expiry(expiries, EVALUATION) == date(2026, 8, 27)
    after_rollover = EVALUATION.replace(hour=15, minute=20)
    assert select_expiry(expiries, after_rollover, rollover_time=datetime.strptime("15:20", "%H:%M").time()) == date(2026, 9, 3)


def test_contract_quality_rejects_zero_volume_bad_spread_and_stale_source() -> None:
    config = NiftyOiConfig()
    assert option_contract_is_eligible(option(), EVALUATION, config)
    assert not option_contract_is_eligible(option(volume=0), EVALUATION, config)
    assert not option_contract_is_eligible(option(bid=10, ask=20), EVALUATION, config)
    stale = option(timestamp=EVALUATION - timedelta(seconds=config.stale_data_seconds + 1))
    assert not option_contract_is_eligible(stale, EVALUATION, config)


def test_options_score_is_normalized_and_reports_directional_strengths() -> None:
    current, previous = option_pairs()
    result = score_options(current, previous, EVALUATION, NiftyOiConfig(strikes_each_side=1))
    assert result["available"] is True
    assert -100 <= result["score"] <= 100
    assert result["callLongBuildupStrength"] > 0
    assert result["putLongBuildupStrength"] > 0
    assert result["selectedExpiry"] == EXPIRY.isoformat()
    assert result["selectedStrikes"] == [25_000.0, 25_050.0, 25_100.0]
    assert result["pcrOi"] == 1


def test_atm_contract_receives_more_weight_than_wing() -> None:
    current, previous = option_pairs()
    result = score_options(current, previous, EVALUATION, NiftyOiConfig(strikes_each_side=1))
    weights = {(row["strike"], row["optionType"]): row["weight"] for row in result["contracts"]}
    assert weights[(25_050.0, "CALL")] > weights[(25_000.0, "CALL")]


def test_iv_expansion_is_low_confidence_and_not_directional() -> None:
    current, previous = option_pairs(current_ltp=103, previous_ltp=100, current_iv=15, previous_iv=12)
    result = score_options(current, previous, EVALUATION, NiftyOiConfig(strikes_each_side=1))
    assert result["volatilityExpansion"] is True
    assert result["classification"] == "VOLATILITY_EXPANSION"
    assert result["confidence"] == "LOW"
    combined = combine_regime_components(
        result,
        {"available": True, "score": 80, "confidence": "HIGH", "sourceTimestamp": EVALUATION.isoformat()},
        {"available": True, "score": -80, "confidence": "HIGH", "sourceTimestamp": EVALUATION.isoformat()},
        EVALUATION,
        NiftyOiConfig(),
    )
    assert combined["regime"] == "VOLATILITY_EXPANSION"
    assert combined["confidence"] == "LOW"


@pytest.mark.parametrize(
    ("current_price", "current_oi", "regime", "sign"),
    [(101, 1_100, "LONG_BUILDUP", 1), (99, 1_100, "SHORT_BUILDUP", -1), (101, 900, "SHORT_COVERING", 1), (99, 900, "LONG_UNWINDING", -1)],
)
def test_futures_quadrants(current_price: float, current_oi: float, regime: str, sign: int) -> None:
    previous = future(timestamp=EVALUATION - timedelta(minutes=15), price=100, oi=1_000)
    result = score_futures(future(price=current_price, oi=current_oi), previous, EVALUATION, NiftyOiConfig())
    assert result["regime"] == regime
    assert result["score"] * sign > 0


def test_futures_rollover_and_large_gap_are_unavailable() -> None:
    current = future()
    assert not score_futures(current, future(timestamp=EVALUATION - timedelta(minutes=15), security_id="OLD"), EVALUATION, NiftyOiConfig())["available"]
    assert not score_futures(current, future(timestamp=EVALUATION - timedelta(hours=2)), EVALUATION, NiftyOiConfig())["available"]


def test_missing_component_weights_renormalize_only_above_coverage() -> None:
    available = {"available": True, "score": 50, "confidence": "HIGH", "sourceTimestamp": EVALUATION.isoformat()}
    missing = {"available": False, "reason": "missing"}
    result = combine_regime_components(available, available, missing, EVALUATION, NiftyOiConfig(minimum_component_coverage=0.6))
    assert result["regime"] == "BULLISH"
    assert result["combinedScore"] == 50
    assert result["effectiveWeights"]["options"] == 0.5
    insufficient = combine_regime_components(available, missing, missing, EVALUATION, NiftyOiConfig(minimum_component_coverage=0.65))
    assert insufficient["regime"] == "INSUFFICIENT_OI_DATA"


def test_future_component_timestamp_is_rejected_without_lookahead() -> None:
    future_component = {"available": True, "score": 100, "confidence": "HIGH", "sourceTimestamp": (EVALUATION + timedelta(seconds=1)).isoformat()}
    result = combine_regime_components(future_component, {"available": False}, {"available": False}, EVALUATION, NiftyOiConfig())
    assert result["regime"] == "INSUFFICIENT_OI_DATA"


def test_spot_trend_uses_only_completed_candles_at_or_before_signal() -> None:
    index = pd.date_range(EVALUATION - timedelta(minutes=100), periods=22, freq="5min", tz=IST)
    frame = pd.DataFrame({"High": range(101, 123), "Low": range(99, 121), "Close": range(100, 122), "Volume": [1_000] * 22}, index=index)
    score = score_spot_trend(frame, EVALUATION, NiftyOiConfig(stale_data_seconds=600))
    frame.loc[EVALUATION + timedelta(minutes=5)] = [1, 1, 1, 1_000]
    assert score_spot_trend(frame, EVALUATION, NiftyOiConfig(stale_data_seconds=600)) == score


def test_policy_modes_and_elevated_quality_rules() -> None:
    config = NiftyOiConfig()
    bearish = {"regime": "BEARISH", "confidence": 0.8}
    assert decide_long_trade("OFF", bearish, stock_quality_score=0, config=config)["allowed"]
    assert decide_long_trade("ADVISORY", bearish, stock_quality_score=0, config=config)["allowed"]
    assert not decide_long_trade("ENFORCED", {"regime": "STRONGLY_BEARISH"}, stock_quality_score=100, config=config)["allowed"]
    assert not decide_long_trade("ENFORCED", bearish, stock_quality_score=94.9, config=config)["allowed"]
    assert decide_long_trade("ENFORCED", bearish, stock_quality_score=95, open_portfolio_positions=0, config=config)["allowed"]
    assert not decide_long_trade("ENFORCED", bearish, stock_quality_score=100, open_portfolio_positions=1, config=config)["allowed"]
    assert not decide_long_trade("ENFORCED", {"regime": "INSUFFICIENT_OI_DATA"}, stock_quality_score=100, config=config)["allowed"]


def test_minimum_confidence_is_advisory_only_until_filtering_is_enabled() -> None:
    config = NiftyOiConfig(minimum_confidence=0.5)
    low_confidence = {"regime": "BULLISH", "confidence": 0.25}
    assert decide_long_trade("ADVISORY", low_confidence, stock_quality_score=100, config=config)["allowed"]
    enforced = decide_long_trade("ENFORCED", low_confidence, stock_quality_score=100, config=config)
    assert not enforced["allowed"]
    assert enforced["decision"] == "SKIPPED_LOW_OI_CONFIDENCE"


def test_off_is_identical_advisory_does_not_block_and_portfolio_order_is_chronological(tmp_path: Path) -> None:
    repository = OiRegimeRepository(tmp_path)
    repository.append_regime({
        "timestamp": EVALUATION.isoformat(), "sourceTimestamp": EVALUATION.isoformat(),
        "regime": "BEARISH", "combinedScore": -30, "confidence": "HIGH",
    })
    first = {"symbol": "AAA", "trades": [{"tradeId": "1", "signalTimestamp": EVALUATION.isoformat(), "targetHitTimestamp": (EVALUATION + timedelta(hours=1)).isoformat(), "status": "TARGET_HIT"}]}
    second = {"symbol": "BBB", "trades": [{"tradeId": "2", "signalTimestamp": (EVALUATION + timedelta(minutes=5)).isoformat(), "targetHitTimestamp": (EVALUATION + timedelta(hours=1)).isoformat(), "status": "TARGET_HIT"}]}
    off = apply_oi_filter_chronologically([first, second], repository=repository, mode="OFF", config=NiftyOiConfig())
    assert off == [first, second]
    advisory = apply_oi_filter_chronologically([first, second], repository=repository, mode="ADVISORY", config=NiftyOiConfig())
    assert sum(len(row["trades"]) for row in advisory) == 2
    enforced = apply_oi_filter_chronologically([first, second], repository=repository, mode="ENFORCED", config=NiftyOiConfig(), quality_by_symbol={"AAA": 96, "BBB": 96})
    assert [trade["tradeId"] for row in enforced for trade in row["trades"]] == ["1"]
    assert enforced[1]["oiSkippedSignals"][0]["status"] == "SKIPPED_BEARISH_OI"


def test_repository_schema_round_trip_causal_lookup_and_determinism(tmp_path: Path) -> None:
    repository = OiRegimeRepository(tmp_path)
    current, _ = option_pairs()
    repository.append_options(current)
    repository.append_futures(future())
    restored = repository.option_history()[0]
    assert restored.public().keys() == current[0].public().keys()
    future_snapshot = {"timestamp": (EVALUATION + timedelta(minutes=5)).isoformat(), "sourceTimestamp": (EVALUATION + timedelta(minutes=5)).isoformat(), "regime": "BULLISH"}
    repository.append_regime(future_snapshot)
    assert repository.regime_at_or_before(EVALUATION, stale_seconds=360) is None
    stale_repository = OiRegimeRepository(tmp_path / "stale")
    stale_repository.append_regime({
        "timestamp": EVALUATION.isoformat(),
        "sourceTimestamp": (EVALUATION - timedelta(minutes=10)).isoformat(),
        "regime": "BULLISH",
    })
    assert stale_repository.regime_at_or_before(EVALUATION, stale_seconds=360) is None
    first = score_options(*option_pairs(), EVALUATION, NiftyOiConfig(strikes_each_side=1))
    second = score_options(*option_pairs(), EVALUATION, NiftyOiConfig(strikes_each_side=1))
    assert first == second


def test_walk_forward_separation() -> None:
    one_year = [datetime(2025, 1, 1, tzinfo=IST), datetime(2025, 12, 31, tzinfo=IST)]
    fold = chronological_walk_forward_folds(one_year, 1)[0]
    assert fold["validationFrom"].startswith("2025-10-01")
    assert datetime.fromisoformat(fold["trainingTo"]) < datetime.fromisoformat(fold["validationFrom"])
    three_year = [datetime(2023, 1, 1, tzinfo=IST), datetime(2026, 1, 2, tzinfo=IST)]
    folds = chronological_walk_forward_folds(three_year, 3)
    assert len(folds) >= 7
    assert all(datetime.fromisoformat(row["trainingTo"]) < datetime.fromisoformat(row["validationFrom"]) for row in folds)
