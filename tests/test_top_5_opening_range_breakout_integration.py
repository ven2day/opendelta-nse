from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import backtest_api


def _payload() -> dict[str, object]:
    return {
        "symbols": ["AAA", "BBB", "CCC", "DDD", "EEE"],
        "strategyMode": "top_5_opening_range_breakout",
        "strategyKey": "top_5_opening_range_breakout",
        "durationYears": 1,
        "timeframe": "5m",
        "top5OpeningRangeBreakoutConfiguration": {
            "watchlistMode": "FROZEN_OPEN",
            "quantityPerTrade": 50,
        },
    }


def test_top_5_request_still_validates_but_can_no_longer_be_launched(monkeypatch) -> None:
    """Top-5 is retired from new runs: its request contract and engine are preserved,
    but /backtest refuses to dispatch it so saved results stay readable and nothing new
    can be started."""

    def rejected_engine(*args, **kwargs):
        raise AssertionError("A retired engine must not be dispatched")

    store = object()
    monkeypatch.setattr(backtest_api, "get_store", lambda: store)
    monkeypatch.setattr(backtest_api, "run_top_5_opening_range_breakout_backtest", rejected_engine)
    monkeypatch.setattr(backtest_api, "run_vwap_pullback_backtest", rejected_engine)

    request_model = backtest_api.BacktestRequest.model_validate(_payload())
    assert request_model.strategyKey == "top_5_opening_range_breakout"
    assert request_model.strategyMode == "top_5_opening_range_breakout"
    assert request_model.top5OpeningRangeBreakoutConfiguration.watchlistMode == "FROZEN_OPEN"

    with pytest.raises(HTTPException) as caught:
        asyncio.run(backtest_api.backtest(request_model))
    assert caught.value.status_code == 422
    assert "EMA/VWAP Strong Buy" in str(caught.value.detail)

    with pytest.raises(HTTPException) as job_caught:
        backtest_api.start_backtest_job(request_model)
    assert job_caught.value.status_code == 422


def test_retired_and_legacy_keys_cannot_create_new_backtests() -> None:
    request_schema = backtest_api.BacktestRequest.model_json_schema()["properties"]
    assert "top5OpeningRangeBreakoutConfiguration" in request_schema
    assert "vwapPullbackConfiguration" not in request_schema
    for retired_key in (
        "market_aligned_vwap_pullback_scalper",
        "daily_scalping_watchlist",
        "market_aligned_rsi_scalper",
    ):
        payload = _payload()
        payload["strategyMode"] = retired_key
        payload["strategyKey"] = retired_key
        with pytest.raises(ValidationError):
            backtest_api.BacktestRequest.model_validate(payload)
