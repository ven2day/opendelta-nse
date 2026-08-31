from __future__ import annotations

from pathlib import Path

import pandas as pd

from main import IST
from nse_signal_engine_v2 import TREND_PULLBACK_KEY
from nse_signal_funnel import NseSignalFunnelRepository, build_nse_signal_funnel


def _feature_frame(symbol_offset: float = 0.0) -> pd.DataFrame:
    index = pd.date_range("2026-08-28 09:15", periods=36, freq="5min", tz=IST)
    base = 99.0 + symbol_offset
    frame = pd.DataFrame(index=index)
    frame["Open"] = [base + position * 0.02 for position in range(len(index))]
    frame["High"] = frame["Open"] + 0.18
    frame["Low"] = frame["Open"] - 0.12
    frame["Close"] = frame["Open"] + 0.10
    frame["Volume"] = 1_000_000.0
    frame["ValidOHLCV"] = True
    frame["RSI"] = 56.0
    frame["EMAFast"] = [base - 0.10 + position * 0.02 for position in range(len(index))]
    frame["EMASlow"] = [base - 0.25 + position * 0.018 for position in range(len(index))]
    frame["ATR"] = 0.50
    frame["SessionVWAP"] = [base - 0.15 + position * 0.018 for position in range(len(index))]
    frame["RVOL"] = 1.40
    frame["RollingWindowRvol"] = 1.8
    frame["AverageTradedValue"] = 100_000_000.0

    arm = len(frame) - 2
    trigger = len(frame) - 1
    frame.iloc[arm, frame.columns.get_loc("Open")] = base + 0.48
    frame.iloc[arm, frame.columns.get_loc("High")] = base + 0.66
    frame.iloc[arm, frame.columns.get_loc("Low")] = base + 0.36
    frame.iloc[arm, frame.columns.get_loc("Close")] = base + 0.54
    frame.iloc[arm, frame.columns.get_loc("RSI")] = 46.0
    frame.iloc[arm, frame.columns.get_loc("EMAFast")] = base + 0.40
    frame.iloc[arm, frame.columns.get_loc("EMASlow")] = base + 0.30
    frame.iloc[arm, frame.columns.get_loc("SessionVWAP")] = base + 0.37
    frame.iloc[trigger, frame.columns.get_loc("Open")] = base + 0.55
    frame.iloc[trigger, frame.columns.get_loc("High")] = base + 0.98
    frame.iloc[trigger, frame.columns.get_loc("Low")] = base + 0.53
    frame.iloc[trigger, frame.columns.get_loc("Close")] = base + 0.94
    frame.iloc[trigger, frame.columns.get_loc("RSI")] = 58.0
    frame.iloc[trigger, frame.columns.get_loc("EMAFast")] = base + 0.75
    frame.iloc[trigger, frame.columns.get_loc("EMASlow")] = base + 0.55
    frame.iloc[trigger, frame.columns.get_loc("SessionVWAP")] = base + 0.60
    frame.iloc[trigger, frame.columns.get_loc("RVOL")] = 1.60
    return frame


def _nifty() -> pd.DataFrame:
    frame = _feature_frame(1_000.0)
    frame["Close"] = frame["SessionVWAP"] + 2.0
    frame["EMAFast"] = frame["EMASlow"] + 1.0
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


def _validated_evidence() -> dict:
    return {
        "status": "WALK_FORWARD_VALIDATED",
        "sample_size": 500,
        "distinct_symbols": 100,
        "target_hit_probability": 57.0,
        "stop_hit_probability": 35.0,
        "timeout_probability": 8.0,
        "expected_net_return_pct": 0.18,
        "expected_net_r": 0.22,
        "profit_factor": 1.35,
        "confidence_lower_net_r": 0.03,
        "confidence_upper_net_r": 0.41,
        "stress_expected_net_r": 0.10,
        "maximum_drawdown_r": 12.0,
        "tested_from": "2025-01-01",
        "tested_to": "2026-06-30",
        "evidence_version": "test-evidence-1",
    }


def test_v2_checks_every_eligible_symbol_and_never_applies_a_top_n_cap() -> None:
    symbols = [f"S{number}" for number in range(1, 7)]
    frames = {symbol: _feature_frame(number) for number, symbol in enumerate(symbols)}
    as_of = frames[symbols[0]].index[-1]
    result = build_nse_signal_funnel(frames, _ranked(symbols), as_of=as_of, nifty_frame=_nifty())

    trend_signals = [row for row in result["allSignals"] if row["strategyKey"] == TREND_PULLBACK_KEY]
    assert result["counts"]["tradeable"] == 6
    assert result["counts"]["strategyEvaluations"] == 12
    assert len(trend_signals) == 6
    assert len(result["allSignals"]) == result["counts"]["validSetups"]
    assert not {"SELECTION_CAP", "BELOW_SIGNAL_SCORE"}.intersection(result["rejectionCounts"])
    assert all(row["status"] == "RESEARCH_SIGNAL" for row in trend_signals)


def test_every_v2_signal_explains_entry_exit_evidence_and_passed_rules() -> None:
    frame = _feature_frame()
    result = build_nse_signal_funnel(
        {"TEST": frame},
        _ranked(["TEST"]),
        as_of=frame.index[-1],
        nifty_frame=_nifty(),
    )
    signal = next(row for row in result["allSignals"] if row["strategyKey"] == TREND_PULLBACK_KEY)

    assert signal["entryRange"]["minimum"] < signal["entryRange"]["maximum"]
    assert signal["estimatedStop"] < signal["entryRange"]["minimum"] < signal["estimatedTarget"]
    assert signal["whyBuy"]
    assert {row["type"] for row in signal["sellConditions"]} == {
        "TARGET", "STOP", "SETUP_INVALIDATION", "STAGNATION_TIMEOUT", "MAXIMUM_TIMEOUT", "SESSION_EXIT",
    }
    assert signal["historicalEvidence"]["status"] == "UNVALIDATED"
    assert signal["historicalEvidence"]["targetHitProbability"] is None
    assert all(rule["passed"] for rule in signal["rules"] if rule["required"])


def test_walk_forward_evidence_qualifies_without_hiding_other_signals() -> None:
    symbols = ["A", "B", "C"]
    frames = {symbol: _feature_frame(index) for index, symbol in enumerate(symbols)}
    result = build_nse_signal_funnel(
        frames,
        _ranked(symbols),
        as_of=frames["A"].index[-1],
        nifty_frame=_nifty(),
        evidence_by_strategy={TREND_PULLBACK_KEY: _validated_evidence()},
    )
    qualified = [row for row in result["tradeReady"] if row["strategyKey"] == TREND_PULLBACK_KEY]
    assert len(qualified) == 3
    assert all(row["historicalEvidence"]["passesQualificationGate"] for row in qualified)
    assert len(result["allSignals"]) == result["counts"]["validSetups"]


def test_future_candles_cannot_change_an_earlier_v2_decision() -> None:
    frame = _feature_frame()
    as_of = frame.index[-1]
    future = frame.iloc[-1:].copy()
    future.index = future.index + pd.Timedelta(minutes=5)
    future[["Close", "High", "Volume", "RVOL"]] = [999.0, 1_000.0, 99_000_000.0, 99.0]
    changed = pd.concat([frame, future])
    ranked = _ranked(["TEST"])

    baseline = build_nse_signal_funnel({"TEST": frame}, ranked, as_of=as_of, nifty_frame=_nifty())
    modified = build_nse_signal_funnel({"TEST": changed}, ranked, as_of=as_of, nifty_frame=_nifty())
    assert baseline == modified


def _manual_funnel(timestamp: str, symbols: list[str]) -> dict:
    rows = [
        {
            "eventId": f"EVENT-{timestamp}-{symbol}",
            "symbol": symbol,
            "strategyKey": TREND_PULLBACK_KEY,
            "signalTimestamp": timestamp,
            "status": "QUALIFIED",
        }
        for symbol in symbols
    ]
    return {
        "metadata": {"generatedAt": timestamp, "configuration": {"maximumTradesPerDay": 5}},
        "counts": {"tradeReady": len(rows), "qualified": len(rows), "rejected": 0},
        "allSignals": rows,
        "tradeReady": rows,
        "watch": [],
        "paperExecuted": [],
        "paperSkippedRisk": [],
        "rejected": [],
        "rejectionCounts": {},
    }


def test_paper_risk_limits_never_hide_or_reject_qualified_signals(tmp_path: Path) -> None:
    repository = NseSignalFunnelRepository((tmp_path / "events.sqlite3").resolve())
    first, inserted = repository.enforce_daily_controls_and_persist(
        _manual_funnel("2026-08-28T09:35:00+05:30", ["A", "B", "C", "D"])
    )
    second, _ = repository.enforce_daily_controls_and_persist(
        _manual_funnel("2026-08-28T09:40:00+05:30", ["E", "F"])
    )

    assert inserted == 4
    assert len(first["tradeReady"]) == 4
    assert len(first["paperExecuted"]) == 4
    assert len(second["tradeReady"]) == 2
    assert len(second["paperExecuted"]) == 1
    assert len(second["paperSkippedRisk"]) == 1
    assert second["paperSkippedRisk"][0]["status"] == "PAPER_SKIPPED_RISK_LIMIT"
    assert second["paperSkippedRisk"][0]["paperSkipReasons"] == ["DAILY_TRADE_LIMIT"]
    assert second["rejected"] == []
    assert len(repository.recent()) == 6
