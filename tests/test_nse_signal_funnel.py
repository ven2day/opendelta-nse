from __future__ import annotations

from pathlib import Path

import pandas as pd

from main import IST
from market_aligned_vwap_pullback_scalper import STRATEGY_KEY
from nse_signal_funnel import NseSignalFunnelRepository, build_nse_signal_funnel


def _feature_frame(symbol_offset: float = 0.0) -> pd.DataFrame:
    index = pd.date_range("2026-08-28 09:15", periods=12, freq="5min", tz=IST)
    frame = pd.DataFrame(index=index)
    frame["Open"] = 100.45 + symbol_offset
    frame["High"] = 100.70 + symbol_offset
    frame["Low"] = 100.40 + symbol_offset
    frame["Close"] = 100.50 + symbol_offset
    frame["Volume"] = 1_000_000.0
    frame["ValidOHLCV"] = True
    frame["RSI"] = 70.0
    frame["EMAFast"] = [100.30 + symbol_offset + offset * 0.1 for offset in range(12)]
    frame["EMASlow"] = [100.00 + symbol_offset + offset * 0.1 for offset in range(12)]
    frame["ATR"] = 1.0
    frame["SessionVWAP"] = 100.30 + symbol_offset
    frame["RVOL"] = 1.6
    frame["RollingWindowRvol"] = 1.8
    frame["AverageTradedValue"] = 100_000_000.0
    frame["ReturnPct"] = 1.0
    frame["HighQualityTrigger"] = True
    arm = 3
    trigger = 4
    frame.iloc[arm, frame.columns.get_loc("RSI")] = 45.0
    frame.iloc[trigger, frame.columns.get_loc("Open")] = 100.75 + symbol_offset
    frame.iloc[trigger, frame.columns.get_loc("High")] = 101.20 + symbol_offset
    frame.iloc[trigger, frame.columns.get_loc("Low")] = 100.70 + symbol_offset
    frame.iloc[trigger, frame.columns.get_loc("Close")] = 101.00 + symbol_offset
    frame.iloc[trigger, frame.columns.get_loc("RSI")] = 55.0
    return frame


def _ranked(symbols: list[str]) -> list[dict]:
    return [
        {
            "symbol": symbol,
            "rank": rank,
            "eligible": True,
            "score": 95.0 - rank,
            "rollingRvol": 1.8,
            "relativeToNiftyPct": 0.8,
            "relativeToSectorPct": 0.5,
        }
        for rank, symbol in enumerate(symbols, start=1)
    ]


def test_signal_first_funnel_checks_every_eligible_symbol_and_does_not_force_trades() -> None:
    symbols = [f"S{number}" for number in range(1, 7)]
    frames = {symbol: _feature_frame(number) for number, symbol in enumerate(symbols)}
    as_of = pd.Timestamp("2026-08-28 09:35", tz=IST)
    result = build_nse_signal_funnel(frames, _ranked(symbols), as_of=as_of)

    assert result["counts"]["tradeable"] == 6
    assert result["counts"]["strategyEvaluations"] == 12
    assert result["counts"]["validSetups"] == 6
    assert len(result["tradeReady"]) == 0
    assert len(result["watch"]) == 3
    assert {row["strategyKey"] for row in result["watch"]} == {STRATEGY_KEY}
    assert all(row["strategyStatus"] == "RETIRED_RESEARCH_ONLY" for row in result["watch"])
    assert all(row["executionModel"] == "NEXT_BAR_OPEN" for row in result["watch"])
    assert all(row["paperOnly"] and not row["liveOrdersEnabled"] for row in result["watch"])

    empty = build_nse_signal_funnel({}, [], as_of=as_of)
    assert empty["tradeReady"] == []
    assert empty["watch"] == []


def test_future_candles_cannot_change_an_earlier_funnel_decision() -> None:
    as_of = pd.Timestamp("2026-08-28 09:35", tz=IST)
    baseline_frame = _feature_frame()
    changed_frame = baseline_frame.copy()
    changed_frame.loc[changed_frame.index > as_of, ["Close", "Volume", "RVOL"]] = [999.0, 99_000_000.0, 99.0]
    ranked = _ranked(["TEST"])

    baseline = build_nse_signal_funnel({"TEST": baseline_frame}, ranked, as_of=as_of)
    changed = build_nse_signal_funnel({"TEST": changed_frame}, ranked, as_of=as_of)
    assert baseline == changed


def _manual_funnel(timestamp: str, symbols: list[str]) -> dict:
    rows = [
        {
            "eventId": f"EVENT-{timestamp}-{symbol}",
            "symbol": symbol,
            "strategyKey": STRATEGY_KEY,
            "signalTimestamp": timestamp,
            "status": "TRADE_READY",
        }
        for symbol in symbols
    ]
    return {
        "metadata": {
            "generatedAt": timestamp,
            "configuration": {"maximumTradesPerDay": 5},
        },
        "counts": {"tradeReady": len(rows), "rejected": 0},
        "tradeReady": rows,
        "watch": [],
        "rejected": [],
        "rejectionCounts": {},
    }


def test_repository_deduplicates_refreshes_and_enforces_five_trades_per_day(tmp_path: Path) -> None:
    repository = NseSignalFunnelRepository((tmp_path / "events.sqlite3").resolve())
    first, inserted = repository.enforce_daily_controls_and_persist(
        _manual_funnel("2026-08-28T09:35:00+05:30", ["A", "B"])
    )
    repeated, repeated_inserted = repository.enforce_daily_controls_and_persist(first)
    second, _ = repository.enforce_daily_controls_and_persist(
        _manual_funnel("2026-08-28T09:50:00+05:30", ["C", "D"])
    )
    third, _ = repository.enforce_daily_controls_and_persist(
        _manual_funnel("2026-08-28T10:05:00+05:30", ["E", "F"])
    )

    assert inserted == 2
    assert repeated_inserted == 0
    assert len(repeated["tradeReady"]) == 2
    assert len(second["tradeReady"]) == 2
    assert [row["symbol"] for row in third["tradeReady"]] == ["E"]
    assert third["rejectionCounts"]["DAILY_TRADE_LIMIT"] == 1
    assert third["metadata"]["dailyTradeReadyAccepted"] == 5
    assert len(repository.recent()) == 6
