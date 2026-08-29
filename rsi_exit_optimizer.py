from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterable

import pandas as pd

from atr_exit_optimizer import WalkForwardFold, build_walk_forward_folds
from recovery_backtest import RecoveryConfig, simulate_recovery_symbol
from recovery_rsi_profit_exit import (
    RsiProfitExitConfig,
    aggregate_rsi_profit_exit_results,
    simulate_rsi_profit_exit_symbol,
)

RSI_EXIT_OPTIMIZER_VERSION = "rsi-profit-exit-walk-forward-1.0.0"


@dataclass(frozen=True)
class RsiExitOptimizationGrid:
    arm_zones: tuple[tuple[float, float], ...] = (
        (20.0, 35.0),
        (25.0, 35.0),
        (30.0, 40.0),
    )
    recovery_thresholds: tuple[float, ...] = (35.0, 40.0, 45.0)
    profit_exit_rsi_levels: tuple[float, ...] = (50.0, 60.0, 70.0)
    minimum_profit_pcts: tuple[float, ...] = (0.5, 1.0)
    stop_loss_pcts: tuple[float, ...] = (1.0, 1.5, 2.0, 3.0)
    max_holding_sessions: tuple[int, ...] = (3, 5, 10)

    def combinations(self) -> list[dict[str, float | int]]:
        combinations: list[dict[str, float | int]] = []
        zones = sorted(set((float(low), float(high)) for low, high in self.arm_zones))
        for zone, recovery, profit_rsi, minimum_profit, stop_loss, holding in itertools.product(
            zones,
            sorted(set(self.recovery_thresholds)),
            sorted(set(self.profit_exit_rsi_levels)),
            sorted(set(self.minimum_profit_pcts)),
            sorted(set(self.stop_loss_pcts)),
            sorted(set(self.max_holding_sessions)),
        ):
            low, high = zone
            if not 0 <= low < high <= 100:
                continue
            combinations.append({
                "rsiArmLow": low,
                "rsiArmHigh": high,
                "rsiRecovery": float(recovery),
                "profitExitRsi": float(profit_rsi),
                "minimumProfitPct": float(minimum_profit),
                "hardStopLossPct": float(stop_loss),
                "maxHoldingSessions": int(holding),
            })
        return combinations


def _period_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = aggregate_rsi_profit_exit_results(results)
    closed = int(summary["executedTrades"]) - int(summary["openPositions"])
    return {
        "executedTrades": int(summary["executedTrades"]),
        "closedTrades": closed,
        "winningTrades": int(summary["winningTrades"]),
        "losingTrades": int(summary["losingTrades"]),
        "openPositions": int(summary["openPositions"]),
        "netPnl": float(summary["netRealizedPnl"]),
        "netProfit": float(summary["netProfit"]),
        "netLoss": float(summary["netLoss"]),
        "profitFactor": summary["profitFactor"],
        "maximumDrawdown": float(summary["maximumDrawdown"]),
        "expectancy": summary["expectancyPerTrade"],
        "winRate": float(summary["winRate"]),
        "tradingCosts": float(summary["tradingCosts"]),
    }


def _combine_period_metrics(metrics: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(metrics)
    executed = sum(int(item["executedTrades"]) for item in values)
    closed = sum(int(item["closedTrades"]) for item in values)
    winners = sum(int(item["winningTrades"]) for item in values)
    losers = sum(int(item["losingTrades"]) for item in values)
    net_profit = sum(float(item["netProfit"]) for item in values)
    net_loss = sum(float(item["netLoss"]) for item in values)
    net_pnl = sum(float(item["netPnl"]) for item in values)
    return {
        "executedTrades": executed,
        "closedTrades": closed,
        "winningTrades": winners,
        "losingTrades": losers,
        "openPositions": sum(int(item["openPositions"]) for item in values),
        "netPnl": round(net_pnl, 2),
        "netProfit": round(net_profit, 2),
        "netLoss": round(net_loss, 2),
        "profitFactor": round(net_profit / net_loss, 4) if net_loss else None,
        "maximumDrawdown": round(sum(float(item["maximumDrawdown"]) for item in values), 2),
        "expectancy": round(net_pnl / closed, 2) if closed else None,
        "winRate": round(winners / closed * 100.0, 2) if closed else 0.0,
        "tradingCosts": round(sum(float(item["tradingCosts"]) for item in values), 2),
    }


def _parameter_key(parameters: dict[str, Any]) -> tuple[Any, ...]:
    return (
        parameters["rsiArmLow"],
        parameters["rsiArmHigh"],
        parameters["rsiRecovery"],
        parameters["profitExitRsi"],
        parameters["minimumProfitPct"],
        parameters["hardStopLossPct"],
        parameters["maxHoldingSessions"],
    )


def _add_stability(rows: list[dict[str, Any]]) -> None:
    by_key = {_parameter_key(row["parameters"]): row for row in rows}
    for key, row in by_key.items():
        neighbours: list[float] = []
        for other_key, other in by_key.items():
            differences = sum(left != right for left, right in zip(key, other_key, strict=True))
            if differences == 1:
                neighbours.append(float(other["validation"]["netPnl"]))
        current = float(row["validation"]["netPnl"])
        median_neighbour = float(pd.Series(neighbours).median()) if neighbours else None
        sharp_difference = bool(
            median_neighbour is not None
            and abs(current - median_neighbour) > max(abs(median_neighbour), 1.0)
        )
        fold_values = [float(fold["validation"]["netPnl"]) for fold in row["folds"]]
        unstable_folds = len(fold_values) > 1 and any(value < 0 for value in fold_values)
        row["stability"] = {
            "neighbourCount": len(neighbours),
            "medianNeighbourValidationPnl": round(median_neighbour, 2) if median_neighbour is not None else None,
            "sharpNeighbourDifference": sharp_difference,
            "foldValidationPnl": [round(value, 2) for value in fold_values],
            "unstableBetweenFolds": unstable_folds,
            "warning": sharp_difference or unstable_folds,
        }


def _slice_fold_candles(
    candles: pd.DataFrame,
    fold: WalkForwardFold,
    period: str,
) -> tuple[pd.DataFrame, pd.Timestamp]:
    if period == "training":
        return candles.loc[candles.index < fold.training_end].copy(), fold.training_start
    return candles.loc[candles.index <= fold.validation_end].copy(), fold.validation_start


def evaluate_rsi_exit_grid(
    symbol_candles: dict[str, pd.DataFrame],
    *,
    timeframe: str,
    base_recovery_config: RecoveryConfig,
    base_exit_config: RsiProfitExitConfig,
    grid: RsiExitOptimizationGrid,
    analysis_start: datetime | pd.Timestamp,
    analysis_end: datetime | pd.Timestamp,
    duration_years: int,
    run_id: str,
    minimum_validation_trades: int = 20,
) -> dict[str, Any]:
    if not symbol_candles:
        raise ValueError("RSI exit comparison requires at least one symbol")
    started = time.perf_counter()
    folds = build_walk_forward_folds(analysis_start, analysis_end, duration_years)
    combinations = grid.combinations()
    if not combinations:
        raise ValueError("RSI exit comparison grid contains no valid configurations")

    entry_settings = sorted({
        (
            float(parameters["rsiArmLow"]),
            float(parameters["rsiArmHigh"]),
            float(parameters["rsiRecovery"]),
        )
        for parameters in combinations
    })
    prepared: dict[
        tuple[int, str, str, float, float, float],
        tuple[pd.DataFrame, dict[str, Any], RecoveryConfig],
    ] = {}
    for fold in folds:
        for symbol in sorted(symbol_candles):
            source_candles = symbol_candles[symbol].sort_index()
            for period in ("training", "validation"):
                sliced, period_start = _slice_fold_candles(source_candles, fold, period)
                if sliced.empty:
                    continue
                for arm_low, arm_high, recovery in entry_settings:
                    recovery_config = replace(
                        base_recovery_config,
                        rsi_arm_low=arm_low,
                        rsi_arm_high=arm_high,
                        rsi_recovery=recovery,
                    )
                    observations = simulate_recovery_symbol(
                        symbol,
                        sliced,
                        timeframe=timeframe,
                        config=recovery_config,
                        run_id=f"{run_id}:{fold.fold}:{period}:{arm_low}:{arm_high}:{recovery}",
                        analysis_start=period_start.to_pydatetime(),
                    )
                    prepared[(fold.fold, period, symbol, arm_low, arm_high, recovery)] = (
                        sliced,
                        observations,
                        recovery_config,
                    )

    rows: list[dict[str, Any]] = []
    for parameters in combinations:
        arm_low = float(parameters["rsiArmLow"])
        arm_high = float(parameters["rsiArmHigh"])
        recovery = float(parameters["rsiRecovery"])
        exit_config = replace(
            base_exit_config,
            profit_exit_rsi=float(parameters["profitExitRsi"]),
            minimum_profit_pct=float(parameters["minimumProfitPct"]),
            stop_loss_pct=float(parameters["hardStopLossPct"]),
            max_holding_sessions=int(parameters["maxHoldingSessions"]),
        )
        fold_rows: list[dict[str, Any]] = []
        for fold in folds:
            period_results: dict[str, list[dict[str, Any]]] = {"training": [], "validation": []}
            for period, period_start in (
                ("training", fold.training_start),
                ("validation", fold.validation_start),
            ):
                for symbol in sorted(symbol_candles):
                    source = prepared.get((fold.fold, period, symbol, arm_low, arm_high, recovery))
                    if source is None:
                        continue
                    candles, observations, recovery_config = source
                    period_results[period].append(simulate_rsi_profit_exit_symbol(
                        symbol,
                        candles,
                        timeframe=timeframe,
                        recovery_config=recovery_config,
                        exit_config=exit_config,
                        run_id=f"{run_id}:{fold.fold}:{period}",
                        analysis_start=period_start.to_pydatetime(),
                        observations=observations,
                    ))
            fold_rows.append({
                **fold.public(),
                "training": _period_metrics(period_results["training"]),
                "validation": _period_metrics(period_results["validation"]),
            })
        training = _combine_period_metrics(item["training"] for item in fold_rows)
        validation = _combine_period_metrics(item["validation"] for item in fold_rows)
        warnings: list[str] = []
        if validation["closedTrades"] < minimum_validation_trades:
            warnings.append("INSUFFICIENT_VALIDATION_TRADES")
        if validation["netPnl"] < 0:
            warnings.append("NEGATIVE_VALIDATION_PNL")
        if validation["profitFactor"] is None or validation["profitFactor"] < 1:
            warnings.append("VALIDATION_PROFIT_FACTOR_BELOW_ONE")
        rows.append({
            "parameters": parameters,
            "training": training,
            "validation": validation,
            "folds": fold_rows,
            "criteriaPassed": not warnings,
            "criteriaWarnings": warnings,
            "label": "Research candidate — not live approved",
        })

    _add_stability(rows)
    for row in rows:
        if row["stability"]["warning"]:
            row["criteriaWarnings"].append("UNSTABLE_NEIGHBOUR_OR_FOLD_RESULTS")
            row["criteriaPassed"] = False
    rows.sort(key=lambda row: (
        -int(row["criteriaPassed"]),
        -float(row["validation"]["netPnl"]),
        -float(row["validation"]["profitFactor"] or 0.0),
        -float(row["validation"]["expectancy"] or 0.0),
        float(row["validation"]["maximumDrawdown"]),
        -int(row["validation"]["closedTrades"]),
        _parameter_key(row["parameters"]),
    ))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return {
        "metadata": {
            "optimizerVersion": RSI_EXIT_OPTIMIZER_VERSION,
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
        "warning": "Research candidates only. Validation results remain separate and do not approve live use.",
    }
