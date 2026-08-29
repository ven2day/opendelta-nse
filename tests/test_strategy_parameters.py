from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backtest_api import BacktestRequest, VwapPullbackConfigurationRequest
from strategy_parameters import parameter_definitions


ROOT = Path(__file__).resolve().parents[1]
MARKET = "market_aligned_vwap_pullback_scalper"


def _market_defaults() -> dict[str, object]:
    return {
        item["key"]: item["default"]
        for item in parameter_definitions(MARKET)
    }


@pytest.mark.parametrize(
    ("field", "value", "companions"),
    [
        ("buyCostBps", 5, {}),
        ("buyCostBps", 5.0, {}),
        ("buyCostBps", 5.00, {}),
        ("maximumEntryGapAtr", 0.5, {}),
        ("pullbackApproachAtr", 0.25, {}),
        ("minimumStopPct", 0.35, {}),
        ("volatilityStopAtr", 0.65, {}),
        ("rewardRiskRatio", 1.5, {}),
        ("maximumHoldingBars", 20, {}),
        ("minimumQualityScore", 60, {}),
        ("maximumTriggerRsi", 90, {}),
        ("minimumAverageTradedValue", 10000, {}),
    ],
)
def test_normal_numeric_values_are_accepted(
    field: str,
    value: float,
    companions: dict[str, float],
) -> None:
    payload = _market_defaults()
    payload.update(companions)
    payload[field] = value
    request = VwapPullbackConfigurationRequest(**payload)
    assert getattr(request, field) == value


@pytest.mark.parametrize(
    "field",
    [
        "rsiLength",
        "maximumHoldingBars",
        "maximumTradesPerDay",
        "maximumConcurrentTrades",
        "emaSlopeLookbackBars",
    ],
)
def test_integer_counts_reject_fractional_values(field: str) -> None:
    payload = _market_defaults()
    payload[field] = 2.5
    with pytest.raises(ValidationError):
        VwapPullbackConfigurationRequest(**payload)


def test_frontend_definitions_are_the_backend_defaults_and_limits() -> None:
    schema = VwapPullbackConfigurationRequest.model_json_schema()["properties"]
    for definition in parameter_definitions(MARKET):
        if definition["type"] not in {"number", "integer"}:
            continue
        field = schema[definition["key"]]
        assert field["default"] == definition["default"]
        assert field.get("minimum") == definition["minimum"]
        assert field.get("maximum") == definition["maximum"]

    legacy_schema = BacktestRequest.model_json_schema()["properties"]
    for strategy in ("rsi_range", "rsi_recovery"):
        for definition in parameter_definitions(strategy):
            if definition["type"] not in {"number", "integer"}:
                continue
            field = legacy_schema[definition["key"]]
            assert field["default"] == definition["default"]
            assert field.get("minimum") == definition["minimum"]
            assert field.get("maximum") == definition["maximum"]


def test_percentage_and_weight_steps_have_no_offset_mismatch() -> None:
    definitions = json.loads((ROOT / "strategy-parameters.json").read_text(encoding="utf-8"))
    for definition in definitions:
        if definition["type"] not in {"number", "integer"}:
            continue
        if definition["unit"] == "%":
            assert definition["minimum"] in {0, 0.01, None}
            assert definition["step"] in {0.01, 0.1, 1}
        if definition["unit"] == "weight":
            assert definition["minimum"] == 0
            assert definition["maximum"] == 1
            assert definition["step"] == 0.01
        if definition["type"] == "integer":
            assert definition["step"] == 1


def test_related_fields_are_rejected_by_authoritative_validation() -> None:
    payload = _market_defaults()
    payload.update(rsiPullbackMinimum=50, rsiPullbackMaximum=50)
    with pytest.raises(ValidationError, match="pullback minimum < pullback maximum"):
        VwapPullbackConfigurationRequest(**payload)

    payload = _market_defaults()
    payload.update(entryStartTime="14:45", lastEntryTime="09:30")
    with pytest.raises(ValidationError, match="entry start < last entry"):
        VwapPullbackConfigurationRequest(**payload)


def test_recommended_defaults_are_runnable() -> None:
    request = VwapPullbackConfigurationRequest(**_market_defaults())
    assert request.maximumTriggerRsi == 65
    assert request.maximumTradesPerDay == 5
    assert request.oiMode == "OFF"
