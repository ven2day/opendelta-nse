from __future__ import annotations

import asyncio

import pytest
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


def test_http_request_dispatches_to_top_5_engine_and_returns_matching_contract(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def top_5_engine(request, store, now_ist=None):
        captured["request"] = request
        captured["store"] = store
        return {
            "metadata": {
                "strategyMode": "top_5_opening_range_breakout",
                "strategyKey": "top_5_opening_range_breakout",
                "strategyName": "Top-5 Opening Range Breakout",
                "strategyVersion": "top-5-opening-range-breakout-1.0.0",
                "effectiveConfiguration": request.top5OpeningRangeBreakoutConfiguration.model_dump(),
                "watchlistMode": request.top5OpeningRangeBreakoutConfiguration.watchlistMode,
            },
            "summary": {"dailyWatchlists": 1},
            "dailySelections": [{
                "sessionDate": "2026-08-28",
                "selectionTimestamp": "2026-08-28T09:30:00+05:30",
                "symbols": [
                    {"rank": rank, "symbol": symbol, "tier": "PRIMARY" if rank <= 2 else "RESERVE"}
                    for rank, symbol in enumerate(("AAA", "BBB", "CCC", "DDD", "EEE"), start=1)
                ],
            }],
            "results": [],
            "errors": [],
            "warnings": [],
        }

    def rejected_vwap_engine(*args, **kwargs):
        raise AssertionError("The retired VWAP engine must not be dispatched")

    store = object()
    monkeypatch.setattr(backtest_api, "get_store", lambda: store)
    monkeypatch.setattr(backtest_api, "run_top_5_opening_range_breakout_backtest", top_5_engine)
    monkeypatch.setattr(backtest_api, "run_vwap_pullback_backtest", rejected_vwap_engine)

    request_model = backtest_api.BacktestRequest.model_validate(_payload())
    body = asyncio.run(backtest_api.backtest(request_model))
    request = captured["request"]
    assert captured["store"] is store
    assert request.strategyKey == "top_5_opening_range_breakout"
    assert request.strategyMode == "top_5_opening_range_breakout"
    assert body["metadata"]["strategyKey"] == request.strategyKey
    assert body["metadata"]["strategyName"] == "Top-5 Opening Range Breakout"
    assert body["metadata"]["effectiveConfiguration"]["watchlistMode"] == "FROZEN_OPEN"
    assert len(body["dailySelections"][0]["symbols"]) == 5


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
