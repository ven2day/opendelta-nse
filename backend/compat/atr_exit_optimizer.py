from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterable

import numpy as np
import pandas as pd

from backend.collector import IST
from backend.compat.recovery_backtest import RecoveryConfig, simulate_recovery_symbol
from backend.compat.recovery_dynamic_exit import (
    DynamicExitConfig,
    aggregate_dynamic_exit_results,
    simulate_dynamic_exit_symbol,
)

OPTIMIZER_VERSION = "atr-exit-walk-forward-1.0.0"


@dataclass(frozen=True)
class AtrOptimizationGrid:
    atr_lengths: tuple[int, ...] = (14,)
    stop_atr_multipliers: tuple[float, ...] = (0.75, 1.0, 1.25, 1.5, 2.0)
    reward_risk_ratios: tuple[float, ...] = (1.0, 1.25, 1.5, 2.0)
    max_holding_sessions: tuple[int, ...] = (1, 3, 5)
    minimum_stop_pcts: tuple[float, ...] = (0.5, 0.75, 1.0)
    maximum_stop_pcts: tuple[float, ...] = (2.0, 3.0, 5.0)

    def combinations(self) -> list[dict[str, float | int]]:
        values = []
        for atr_length, multiplier, reward_risk, holding, minimum_stop, maximum_stop in itertools.product(
            sorted(set(self.atr_lengths)),
            sorted(set(self.stop_atr_multipliers)),
            sorted(set(self.reward_risk_ratios)),
            sorted(set(self.max_holding_sessions)),
            sorted(set(self.minimum_stop_pcts)),
            sorted(set(self.maximum_stop_pcts)),
        ):
            if minimum_stop > maximum_stop:
                continue
            values.append({
                "atrLength": int(atr_length),
                "stopAtrMultiplier": float(multiplier),
                "rewardRiskRatio": float(reward_risk),
                "maxHoldingSessions": int(holding),
                "minimumStopPct": float(minimum_stop),
                "maximumStopPct": float(maximum_stop),
            })
        return values


@dataclass(frozen=True)
class WalkForwardFold:
    fold: int
    training_start: pd.Timestamp
    training_end: pd.Timestamp
    validation_start: pd.Timestamp
    validation_end: pd.Timestamp

    def public(self) -> dict[str, Any]:
        return {
            "fold": self.fold,
            "trainingStart": self.training_start.isoformat(),
            "trainingEnd": self.training_end.isoformat(),
            "validationStart": self.validation_start.isoformat(),
            "validationEnd": self.validation_end.isoformat(),
        }


def _ist(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def build_walk_forward_folds(
    analysis_start: datetime | pd.Timestamp,
    analysis_end: datetime | pd.Timestamp,
    duration_years: int,
) -> list[WalkForwardFold]:
    start = _ist(analysis_start)
    end = _ist(analysis_end)
    if start >= end:
        raise ValueError("Walk-forward start must be before end")
    folds: list[WalkForwardFold] = []
    if duration_years == 1:
        validation_start = start + pd.DateOffset(months=9)
        if validation_start >= end:
            raise ValueError("One-year walk-forward requires at least nine training months plus validation data")
        folds.append(WalkForwardFold(1, start, validation_start, validation_start, end))
        return folds
    if duration_years != 3:
        raise ValueError("ATR optimization supports one-year or three-year walk-forward windows")
    training_start = start
    fold_number = 1
    while True:
        validation_start = training_start + pd.DateOffset(months=12)
        validation_end = validation_start + pd.DateOffset(months=3)
        if validation_end > end:
            break
        folds.append(
            WalkForwardFold(
                fold_number,
                training_start,
                validation_start,
                validation_start,
                validation_end,
            )
        )
        fold_number += 1
        training_start += pd.DateOffset(months=3)
    if not folds:
        raise ValueError("Three-year walk-forward requires at least 15 months of data")
    return folds


def _period_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = aggregate_dynamic_exit_results(results)
    return {
        "trades": int(summary["executedTrades"]),
        "closedTrades": int(summary["executedTrades"] - summary["openPositions"]),
        "netPnl": float(summary["netRealizedPnl"] or 0.0),
        "profitFactor": summary["profitFactor"],
        "maximumDrawdown": float(summary["maximumDrawdown"] or 0.0),
        "expectancy": summary["expectancyPerTrade"],
        "winningTrades": int(summary["winningTrades"]),
        "losingTrades": int(summary["losingTrades"]),
        "grossProfit": float(summary["grossProfit"] or 0.0),
        "grossLoss": float(summary["grossLoss"] or 0.0),
        "tradingCosts": float(summary["tradingCosts"] or 0.0),
    }


def _combine_period_metrics(metrics: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(metrics)
    gross_profit = sum(float(item["grossProfit"]) for item in values)
    gross_loss = sum(float(item["grossLoss"]) for item in values)
    net_pnl = sum(float(item["netPnl"]) for item in values)
    closed = sum(int(item["closedTrades"]) for item in values)
    return {
        "trades": sum(int(item["trades"]) for item in values),
        "closedTrades": closed,
        "netPnl": round(net_pnl, 2),
        "profitFactor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "maximumDrawdown": round(max((float(item["maximumDrawdown"]) for item in values), default=0.0), 2),
        "expectancy": round(net_pnl / closed, 2) if closed else None,
        "winningTrades": sum(int(item["winningTrades"]) for item in values),
        "losingTrades": sum(int(item["losingTrades"]) for item in values),
        "grossProfit": round(gross_profit, 2),
        "grossLoss": round(gross_loss, 2),
        "tradingCosts": round(sum(float(item["tradingCosts"]) for item in values), 2),
    }


def _parameter_key(parameters: dict[str, Any]) -> tuple[Any, ...]:
    return (
        parameters["atrLength"],
        parameters["stopAtrMultiplier"],
        parameters["rewardRiskRatio"],
        parameters["maxHoldingSessions"],
        parameters["minimumStopPct"],
        parameters["maximumStopPct"],
    )


def _stability_details(rows: list[dict[str, Any]]) -> None:
    by_key = {_parameter_key(row["parameters"]): row for row in rows}
    for row in rows:
        key = _parameter_key(row["parameters"])
        neighbours = []
        for other_key, other in by_key.items():
            differences = sum(left != right for left, right in zip(key, other_key, strict=True))
            if differences == 1:
                neighbours.append(other)
        baseline = float(row["validation"]["expectancy"] or 0.0)
        neighbour_expectancies = [float(item["validation"]["expectancy"] or 0.0) for item in neighbours]
        median_neighbour = float(np.median(neighbour_expectancies)) if neighbour_expectancies else baseline
        denominator = max(abs(baseline), 1.0)
        sensitivity = abs(baseline - median_neighbour) / denominator
        fold_pnls = [float(item["validation"]["netPnl"]) for item in row["folds"]]
        positive_fold_pct = sum(value > 0 for value in fold_pnls) / len(fold_pnls) * 100.0 if fold_pnls else 0.0
        row["stability"] = {
            "neighbourCount": len(neighbours),
            "medianNeighbourExpectancy": round(median_neighbour, 2),
            "neighbourSensitivityPct": round(sensitivity * 100.0, 2),
            "positiveValidationFoldsPct": round(positive_fold_pct, 2),
            "warning": sensitivity > 0.5 or positive_fold_pct < 50.0,
        }


def evaluate_atr_exit_grid(
    symbol_candles: dict[str, pd.DataFrame],
    *,
    timeframe: str,
    recovery_config: RecoveryConfig,
    base_exit_config: DynamicExitConfig,
    grid: AtrOptimizationGrid,
    analysis_start: datetime | pd.Timestamp,
    analysis_end: datetime | pd.Timestamp,
    duration_years: int,
    run_id: str,
    minimum_validation_trades: int = 20,
) -> dict[str, Any]:
    if not symbol_candles:
        raise ValueError("ATR optimization requires at least one symbol")
    started = time.perf_counter()
    folds = build_walk_forward_folds(analysis_start, analysis_end, duration_years)
    combinations = grid.combinations()
    if not combinations:
        raise ValueError("ATR optimization grid contains no valid configurations")

    prepared: dict[tuple[int, str, str], tuple[pd.DataFrame, dict[str, Any]]] = {}
    for fold in folds:
        for symbol in sorted(symbol_candles):
            candles = symbol_candles[symbol].sort_index()
            for period, period_start, period_end in (
                ("training", fold.training_start, fold.training_end),
                ("validation", fold.validation_start, fold.validation_end),
            ):
                # Keep the chronological split disjoint: the first validation
                # candle must never also be evaluated as a training candle.
                if period == "training":
                    sliced = candles.loc[candles.index < period_end].copy()
                else:
                    sliced = candles.loc[candles.index <= period_end].copy()
                if sliced.empty:
                    continue
                observations = simulate_recovery_symbol(
                    symbol,
                    sliced,
                    timeframe=timeframe,
                    config=recovery_config,
                    run_id=f"{run_id}:{fold.fold}:{period}",
                    analysis_start=period_start.to_pydatetime(),
                )
                prepared[(fold.fold, period, symbol)] = (sliced, observations)

    rows: list[dict[str, Any]] = []
    for parameters in combinations:
        exit_config = replace(
            base_exit_config,
            exit_model="ATR_DYNAMIC_TP_SL",
            atr_length=int(parameters["atrLength"]),
            stop_atr_multiplier=float(parameters["stopAtrMultiplier"]),
            reward_risk_ratio=float(parameters["rewardRiskRatio"]),
            max_holding_sessions=int(parameters["maxHoldingSessions"]),
            minimum_stop_pct=float(parameters["minimumStopPct"]),
            maximum_stop_pct=float(parameters["maximumStopPct"]),
        )
        fold_rows = []
        for fold in folds:
            period_results: dict[str, list[dict[str, Any]]] = {"training": [], "validation": []}
            for period, period_start in (("training", fold.training_start), ("validation", fold.validation_start)):
                for symbol in sorted(symbol_candles):
                    source = prepared.get((fold.fold, period, symbol))
                    if source is None:
                        continue
                    candles, observations = source
                    period_results[period].append(
                        simulate_dynamic_exit_symbol(
                            symbol,
                            candles,
                            timeframe=timeframe,
                            recovery_config=recovery_config,
                            exit_config=exit_config,
                            run_id=f"{run_id}:{fold.fold}:{period}",
                            analysis_start=period_start.to_pydatetime(),
                            observations=observations,
                        )
                    )
            fold_rows.append({
                **fold.public(),
                "training": _period_metrics(period_results["training"]),
                "validation": _period_metrics(period_results["validation"]),
            })
        training = _combine_period_metrics(item["training"] for item in fold_rows)
        validation = _combine_period_metrics(item["validation"] for item in fold_rows)
        reasons = []
        if validation["closedTrades"] < minimum_validation_trades:
            reasons.append("INSUFFICIENT_VALIDATION_TRADES")
        if validation["netPnl"] < 0:
            reasons.append("NEGATIVE_VALIDATION_PNL")
        if validation["profitFactor"] is None or validation["profitFactor"] < 1:
            reasons.append("VALIDATION_PROFIT_FACTOR_BELOW_ONE")
        rows.append({
            "parameters": parameters,
            "training": training,
            "validation": validation,
            "folds": fold_rows,
            "criteriaPassed": not reasons,
            "criteriaWarnings": reasons,
            "label": "Research candidate — not live approved",
        })

    _stability_details(rows)
    for row in rows:
        if row["stability"]["warning"]:
            row["criteriaWarnings"].append("UNSTABLE_NEIGHBOUR_OR_FOLD_RESULTS")
            row["criteriaPassed"] = False
    rows.sort(key=lambda row: (
        -int(row["criteriaPassed"]),
        -float(row["validation"]["netPnl"]),
        -float(row["validation"]["profitFactor"] or 0.0),
        float(row["validation"]["maximumDrawdown"]),
        _parameter_key(row["parameters"]),
    ))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return {
        "metadata": {
            "optimizerVersion": OPTIMIZER_VERSION,
            "timeframe": timeframe,
            "symbols": sorted(symbol_candles),
            "symbolCount": len(symbol_candles),
            "configurationCount": len(rows),
            "foldCount": len(folds),
            "minimumValidationTrades": minimum_validation_trades,
            "chronological": True,
            "shuffled": False,
            "selectionScope": "One common parameter configuration across the selected universe",
            "runtimeSeconds": round(time.perf_counter() - started, 4),
        },
        "folds": [fold.public() for fold in folds],
        "results": rows,
        "topConfigurations": rows[:20],
        "warning": "Research candidates only. Validation ranking is descriptive and does not approve live use.",
    }
