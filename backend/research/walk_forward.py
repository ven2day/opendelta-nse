"""Deterministic walk-forward schedules, workload estimates, and metric ranking."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from backend.markets.common import TIMEFRAME_SECONDS

MAX_WALK_FORWARD_FOLDS = 12
MAX_CANDIDATES_PER_FOLD = 20
MAX_WALK_FORWARD_CHILD_RUNS = 120
MAX_WALK_FORWARD_SYMBOL_RUNS = 20_000
MAX_WALK_FORWARD_CANDLE_WORKLOAD = 25_000_000
LARGE_WALK_FORWARD_WORKLOAD = 10_000_000
RANKING_OBJECTIVES = {
    "NET_PNL",
    "RETURN_DRAWDOWN",
    "LOWEST_DRAWDOWN",
    "HIGHEST_WIN_RATE",
}


@dataclass(frozen=True)
class FoldDefinition:
    position: int
    training_start: date
    training_end: date
    testing_start: date
    testing_end: date
    training_sessions: int
    testing_sessions: int

    def public(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "trainingStart": self.training_start.isoformat(),
            "trainingEnd": self.training_end.isoformat(),
            "testingStart": self.testing_start.isoformat(),
            "testingEnd": self.testing_end.isoformat(),
            "trainingSessions": self.training_sessions,
            "testingSessions": self.testing_sessions,
        }


def canonical_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def walk_forward_sessions(
    market: str,
    start: date,
    end: date,
    *,
    nse_sessions: Sequence[date] | None = None,
) -> list[date]:
    if end < start:
        raise ValueError("The overall end date must not be before the start date")
    if market == "CRYPTO":
        return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    if market != "NSE":
        raise ValueError(f"Unsupported market {market!r}")
    if nse_sessions is None:
        # The database calendar is supplied in production. The weekday fallback
        # keeps pure scheduling utilities usable without pretending weekends trade.
        return [
            start + timedelta(days=offset)
            for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5
        ]
    return sorted({session for session in nse_sessions if start <= session <= end and session.weekday() < 5})


def generate_folds(
    *,
    market: str,
    start: date,
    end: date,
    training_window: int,
    testing_window: int,
    step: int,
    mode: Literal["ANCHORED", "ROLLING"],
    maximum_folds: int,
    nse_sessions: Sequence[date] | None = None,
) -> list[FoldDefinition]:
    for name, value in (
        ("trainingWindow", training_window),
        ("testingWindow", testing_window),
        ("step", step),
        ("maximumFolds", maximum_folds),
    ):
        if isinstance(value, bool) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if maximum_folds > MAX_WALK_FORWARD_FOLDS:
        raise ValueError(f"maximumFolds cannot exceed {MAX_WALK_FORWARD_FOLDS}")
    if mode not in {"ANCHORED", "ROLLING"}:
        raise ValueError("Walk-forward mode must be ANCHORED or ROLLING")

    sessions = walk_forward_sessions(market, start, end, nse_sessions=nse_sessions)
    if len(sessions) < training_window + testing_window:
        raise ValueError("The overall range is too short for one complete training and unseen-test fold")

    folds: list[FoldDefinition] = []
    for fold_index in range(maximum_folds):
        offset = fold_index * step
        training_start_index = 0 if mode == "ANCHORED" else offset
        training_end_index = training_window - 1 + offset
        testing_start_index = training_end_index + 1
        testing_end_index = testing_start_index + testing_window - 1
        if testing_end_index >= len(sessions):
            break
        folds.append(
            FoldDefinition(
                position=fold_index + 1,
                training_start=sessions[training_start_index],
                training_end=sessions[training_end_index],
                testing_start=sessions[testing_start_index],
                testing_end=sessions[testing_end_index],
                training_sessions=training_end_index - training_start_index + 1,
                testing_sessions=testing_window,
            )
        )
    if not folds:
        raise ValueError("No complete walk-forward folds could be generated")
    return folds


def workload_estimate(
    *,
    market: str,
    timeframe: str,
    folds: Sequence[FoldDefinition],
    candidate_count: int,
    symbol_count: int,
) -> dict[str, int]:
    if not 1 <= candidate_count <= MAX_CANDIDATES_PER_FOLD:
        raise ValueError(f"Candidate count must be between 1 and {MAX_CANDIDATES_PER_FOLD}")
    if symbol_count < 1:
        raise ValueError("A walk-forward validation needs at least one symbol")
    try:
        seconds = TIMEFRAME_SECONDS[timeframe]
    except KeyError as error:
        raise ValueError(f"Unsupported timeframe {timeframe!r}") from error
    bars_per_session = 1 if timeframe == "1d" else math.ceil((375 * 60 if market == "NSE" else 86_400) / seconds)
    child_runs = len(folds) * (candidate_count + 1)
    symbol_runs = child_runs * symbol_count
    candle_workload = sum(
        (fold.training_sessions * candidate_count + fold.testing_sessions) * bars_per_session * symbol_count
        for fold in folds
    )
    if child_runs > MAX_WALK_FORWARD_CHILD_RUNS:
        raise ValueError(f"The validation requires {child_runs} child runs; the limit is {MAX_WALK_FORWARD_CHILD_RUNS}")
    if symbol_runs > MAX_WALK_FORWARD_SYMBOL_RUNS:
        raise ValueError(
            f"The validation requires {symbol_runs:,} symbol-runs; the limit is {MAX_WALK_FORWARD_SYMBOL_RUNS:,}"
        )
    if candle_workload > MAX_WALK_FORWARD_CANDLE_WORKLOAD:
        raise ValueError(
            f"The validation requests about {candle_workload:,} candle evaluations; "
            f"the limit is {MAX_WALK_FORWARD_CANDLE_WORKLOAD:,}"
        )
    return {
        "foldCount": len(folds),
        "candidateCount": candidate_count,
        "childRunCount": child_runs,
        "symbolCount": symbol_count,
        "estimatedSymbolRuns": symbol_runs,
        "estimatedCandleWorkload": candle_workload,
    }


def net_pnl(metrics: Mapping[str, Any]) -> float:
    return float(metrics.get("realizedPnl") or 0.0) + float(metrics.get("unrealizedPnl") or 0.0)


def return_drawdown_score(metrics: Mapping[str, Any]) -> float:
    return net_pnl(metrics) / max(abs(float(metrics.get("maximumDrawdown") or 0.0)), 1.0)


def rank_completed_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    objective: str,
    minimum_trades: int,
) -> list[Mapping[str, Any]]:
    if objective not in RANKING_OBJECTIVES:
        raise ValueError(f"Unsupported ranking objective {objective!r}")
    eligible = [
        candidate
        for candidate in candidates
        if candidate.get("run", {}).get("status") == "COMPLETE"
        and int(candidate.get("run", {}).get("metrics", {}).get("completedTrades") or 0) >= minimum_trades
    ]

    def score(candidate: Mapping[str, Any]) -> tuple[float, int]:
        metrics = candidate["run"]["metrics"]
        if objective == "NET_PNL":
            value = net_pnl(metrics)
        elif objective == "RETURN_DRAWDOWN":
            value = return_drawdown_score(metrics)
        elif objective == "LOWEST_DRAWDOWN":
            value = -abs(float(metrics.get("maximumDrawdown") or 0.0))
        else:
            value = float(metrics.get("winRate") or 0.0)
        return value, -int(candidate.get("position") or 0)

    return sorted(eligible, key=score, reverse=True)


def aggregate_unseen_metrics(metrics_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    completed_trades = sum(int(row.get("completedTrades") or 0) for row in metrics_rows)
    weighted_wins = sum(
        int(row.get("completedTrades") or 0) * float(row.get("winRate") or 0.0) / 100.0
        for row in metrics_rows
    )
    holding_weight = sum(
        int(row.get("completedTrades") or 0) * float(row.get("averageHoldingMinutes") or 0.0)
        for row in metrics_rows
    )
    result = {
        "netPnl": round(sum(net_pnl(row) for row in metrics_rows), 2),
        "realizedPnl": round(sum(float(row.get("realizedPnl") or 0.0) for row in metrics_rows), 2),
        "unrealizedPnl": round(sum(float(row.get("unrealizedPnl") or 0.0) for row in metrics_rows), 2),
        "maximumDrawdown": round(max((abs(float(row.get("maximumDrawdown") or 0.0)) for row in metrics_rows), default=0.0), 2),
        "winRate": round(weighted_wins / completed_trades * 100.0, 2) if completed_trades else None,
        "fees": round(sum(float(row.get("fees") or 0.0) for row in metrics_rows), 2),
        "slippage": round(sum(float(row.get("slippage") or 0.0) for row in metrics_rows), 2),
        "completedTrades": completed_trades,
        "openTrades": sum(int(row.get("openTrades") or 0) for row in metrics_rows),
        "failedSymbols": sum(int(row.get("symbolsFailed") or 0) for row in metrics_rows),
        "exposureMinutes": round(sum(float(row.get("exposureMinutes") or 0.0) for row in metrics_rows), 2),
        "averageHoldingMinutes": round(holding_weight / completed_trades, 2) if completed_trades else None,
    }
    result["returnDrawdownScore"] = round(
        result["netPnl"] / max(float(result["maximumDrawdown"]), 1.0), 6
    )
    return result
