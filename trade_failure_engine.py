from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from ema_vwap_strong_buy import StrongBuyConfig, calculate_strong_buy_indicators


FAILURE_FEATURES = (
    "below_vwap",
    "ema_bearish",
    "ema_slope_down",
    "support_break",
    "bearish_direction",
    "bearish_volume",
    "stalled_progress",
)


@dataclass(frozen=True)
class TradeFailureResearchConfig:
    mode: str = "OFF"
    maximum_holding_bars: int = 375
    support_lookback_bars: int = 20
    ema_slope_lookback_bars: int = 3
    progress_lookback_bars: int = 6
    minimum_progress_fraction: float = 0.25
    decision_persistence_bars: int = 2
    minimum_failed_groups: int = 2
    round_trip_cost_bps: float = 14.0
    prior_observations: float = 20.0
    walk_forward_folds: int = 2
    minimum_training_lots: int = 30
    minimum_test_lots: int = 10
    maximum_audit_rows: int = 5_000

    def validate(self) -> "TradeFailureResearchConfig":
        if self.mode not in {"OFF", "RESEARCH_COMPARE"}:
            raise ValueError("Failure Engine mode must be OFF or RESEARCH_COMPARE")
        positive_ints = (
            self.maximum_holding_bars,
            self.support_lookback_bars,
            self.ema_slope_lookback_bars,
            self.progress_lookback_bars,
            self.decision_persistence_bars,
            self.minimum_failed_groups,
            self.walk_forward_folds,
            self.minimum_training_lots,
            self.minimum_test_lots,
            self.maximum_audit_rows,
        )
        if any(value < 1 for value in positive_ints):
            raise ValueError("Failure Engine bar, fold, group, sample and audit limits must be positive")
        if not 0 < self.minimum_progress_fraction <= 1:
            raise ValueError("Failure Engine minimum progress fraction must be in (0, 1]")
        if self.minimum_failed_groups > 3:
            raise ValueError("Failure Engine has exactly three independent evidence groups")
        if self.round_trip_cost_bps < 0 or self.prior_observations <= 0:
            raise ValueError("Failure Engine costs cannot be negative and prior observations must be positive")
        return self

    @property
    def cost_pct(self) -> float:
        return self.round_trip_cost_bps / 100.0

    def public(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "maximumHoldingBars": self.maximum_holding_bars,
            "supportLookbackBars": self.support_lookback_bars,
            "emaSlopeLookbackBars": self.ema_slope_lookback_bars,
            "progressLookbackBars": self.progress_lookback_bars,
            "minimumProgressFraction": self.minimum_progress_fraction,
            "decisionPersistenceBars": self.decision_persistence_bars,
            "minimumFailedGroups": self.minimum_failed_groups,
            "roundTripCostBps": self.round_trip_cost_bps,
            "priorObservations": self.prior_observations,
            "walkForwardFolds": self.walk_forward_folds,
            "minimumTrainingLots": self.minimum_training_lots,
            "minimumTestLots": self.minimum_test_lots,
            "maximumAuditRows": self.maximum_audit_rows,
        }


def _as_ist(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("Asia/Kolkata") if stamp.tzinfo is None else stamp.tz_convert("Asia/Kolkata")


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if np.isfinite(number) else None


def _state_key(features: Mapping[str, bool]) -> str:
    return "".join("1" if features[name] else "0" for name in FAILURE_FEATURES)


def _feature_groups(features: Mapping[str, bool]) -> list[str]:
    groups: list[str] = []
    if any(features[name] for name in ("below_vwap", "ema_bearish", "ema_slope_down", "support_break")):
        groups.append("STRUCTURE")
    if any(features[name] for name in ("bearish_direction", "bearish_volume")):
        groups.append("MOMENTUM_PARTICIPATION")
    if features["stalled_progress"]:
        groups.append("PROGRESS")
    return groups


def _research_frame(
    candles: pd.DataFrame,
    entry_config: StrongBuyConfig,
    failure_config: TradeFailureResearchConfig,
) -> pd.DataFrame:
    data = calculate_strong_buy_indicators(candles, entry_config).copy()
    data["PreviousSupport"] = data["Low"].shift(1).rolling(
        failure_config.support_lookback_bars,
        min_periods=failure_config.support_lookback_bars,
    ).min()
    data["EmaFastPast"] = data["EmaFast"].shift(failure_config.ema_slope_lookback_bars)
    return data


def extract_failure_research_lots(
    symbol: str,
    candles: pd.DataFrame,
    baseline_result: Mapping[str, Any],
    *,
    entry_config: StrongBuyConfig,
    failure_config: TradeFailureResearchConfig,
) -> list[dict[str, Any]]:
    """Build causal per-candle observations; future candles are used only for labels."""
    cfg = failure_config.validate()
    data = _research_frame(candles, entry_config, cfg)
    if data.empty:
        return []
    positions = {stamp: index for index, stamp in enumerate(data.index)}
    research_lots: list[dict[str, Any]] = []
    for baseline_lot in baseline_result.get("lots", []):
        entry_stamp = _as_ist(baseline_lot["entryTimestamp"])
        entry_index = positions.get(entry_stamp)
        if entry_index is None or entry_index >= len(data) - 1:
            continue
        entry_price = float(baseline_lot["entryPrice"])
        target_price = float(baseline_lot["targetPrice"])
        horizon_index = min(len(data) - 1, entry_index + cfg.maximum_holding_bars)
        resolution_index = horizon_index
        success = False
        for candidate in range(entry_index + 1, horizon_index + 1):
            if float(data.iloc[candidate]["High"]) >= target_price:
                resolution_index = candidate
                success = True
                break
        resolution_price = target_price if success else float(data.iloc[resolution_index]["Close"])
        resolution_status = "TAKE_PROFIT" if success else "TIME_HORIZON_FAILURE"
        observations: list[dict[str, Any]] = []
        running_high = entry_price
        for decision_index in range(entry_index, resolution_index):
            if decision_index + 1 >= len(data):
                break
            row = data.iloc[decision_index]
            running_high = max(running_high, float(row["High"]))
            bars_held = decision_index - entry_index + 1
            mfe_pct = (running_high / entry_price - 1.0) * 100.0
            features = {
                "below_vwap": bool(np.isfinite(row["SessionVwap"]) and float(row["Close"]) < float(row["SessionVwap"])),
                "ema_bearish": bool(np.isfinite(row["EmaFast"]) and np.isfinite(row["EmaSlow"]) and float(row["EmaFast"]) <= float(row["EmaSlow"])),
                "ema_slope_down": bool(np.isfinite(row["EmaFast"]) and np.isfinite(row["EmaFastPast"]) and float(row["EmaFast"]) < float(row["EmaFastPast"])),
                "support_break": bool(np.isfinite(row["PreviousSupport"]) and float(row["Close"]) < float(row["PreviousSupport"])),
                "bearish_direction": bool(np.isfinite(row["MinusDi"]) and np.isfinite(row["PlusDi"]) and float(row["MinusDi"]) > float(row["PlusDi"])),
                "bearish_volume": bool(float(row["Close"]) < float(row["Open"]) and np.isfinite(row["RelativeVolume"]) and float(row["RelativeVolume"]) >= entry_config.minimum_rvol),
                "stalled_progress": bool(
                    bars_held >= cfg.progress_lookback_bars
                    and mfe_pct < entry_config.target_pct * cfg.minimum_progress_fraction
                ),
            }
            current_close = float(row["Close"])
            future = data.iloc[decision_index + 1 : resolution_index + 1]
            future_minimum = float(future["Low"].min()) if not future.empty else current_close
            observations.append(
                {
                    "decisionTimestamp": data.index[decision_index].isoformat(),
                    "nextTimestamp": data.index[decision_index + 1].isoformat(),
                    "nextOpen": float(data.iloc[decision_index + 1]["Open"]),
                    "barsHeld": bars_held,
                    "currentClose": current_close,
                    "remainingTargetPct": max(0.0, (target_price / current_close - 1.0) * 100.0),
                    "mfePct": mfe_pct,
                    "features": features,
                    "stateKey": _state_key(features),
                    "failedGroups": _feature_groups(features),
                    "success": success,
                    "futureAdverseLossPct": max(0.0, (current_close - future_minimum) / current_close * 100.0),
                }
            )
        research_lots.append(
            {
                "lotId": str(baseline_lot["lotId"]),
                "symbol": symbol,
                "quantity": int(baseline_lot["quantity"]),
                "entryTimestamp": entry_stamp.isoformat(),
                "entryPrice": entry_price,
                "targetPrice": target_price,
                "targetPct": float(baseline_lot["targetPct"]),
                "resolutionTimestamp": data.index[resolution_index].isoformat(),
                "resolutionPrice": resolution_price,
                "resolutionStatus": resolution_status,
                "success": success,
                "barsToResolution": resolution_index - entry_index,
                "observations": observations,
            }
        )
    return research_lots


class EmpiricalFailureModel:
    """Hierarchically smoothed state table; deliberately simpler than a black-box model."""

    def __init__(self, prior_observations: float):
        self.prior_observations = float(prior_observations)
        self.global_success_rate = 0.5
        self.global_failure_loss_pct = 1.0
        self.states: dict[str, dict[str, float]] = {}
        self.observation_count = 0

    def fit(self, lots: Iterable[Mapping[str, Any]]) -> "EmpiricalFailureModel":
        observations = [observation for lot in lots for observation in lot["observations"]]
        self.observation_count = len(observations)
        if not observations:
            return self
        self.global_success_rate = float(np.mean([bool(item["success"]) for item in observations]))
        failure_losses = [float(item["futureAdverseLossPct"]) for item in observations if not item["success"]]
        if failure_losses:
            self.global_failure_loss_pct = max(0.000001, float(np.mean(failure_losses)))
        buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for observation in observations:
            buckets[str(observation["stateKey"])].append(observation)
        for key, rows in buckets.items():
            successes = sum(bool(row["success"]) for row in rows)
            failures = len(rows) - successes
            loss_sum = sum(float(row["futureAdverseLossPct"]) for row in rows if not row["success"])
            success_probability = (
                successes + self.prior_observations * self.global_success_rate
            ) / (len(rows) + self.prior_observations)
            expected_failure_loss = (
                loss_sum + self.prior_observations * self.global_failure_loss_pct
            ) / (failures + self.prior_observations)
            self.states[key] = {
                "observations": float(len(rows)),
                "successes": float(successes),
                "failures": float(failures),
                "successProbability": success_probability,
                "expectedFailureLossPct": expected_failure_loss,
            }
        return self

    def predict(self, observation: Mapping[str, Any]) -> dict[str, float]:
        state = self.states.get(str(observation["stateKey"]))
        if state is None:
            return {
                "stateObservations": 0.0,
                "successProbability": self.global_success_rate,
                "expectedFailureLossPct": self.global_failure_loss_pct,
            }
        return {
            "stateObservations": state["observations"],
            "successProbability": state["successProbability"],
            "expectedFailureLossPct": state["expectedFailureLossPct"],
        }


def _resolved_trade(
    lot: Mapping[str, Any],
    *,
    status: str,
    exit_timestamp: str,
    exit_price: float,
    cost_pct: float = 0.0,
    decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entry_price = float(lot["entryPrice"])
    quantity = int(lot["quantity"])
    gross_return_pct = (exit_price / entry_price - 1.0) * 100.0
    cost = entry_price * quantity * cost_pct / 100.0
    return {
        "lotId": lot["lotId"],
        "symbol": lot["symbol"],
        "entryTimestamp": lot["entryTimestamp"],
        "entryPrice": entry_price,
        "quantity": quantity,
        "exitTimestamp": exit_timestamp,
        "exitPrice": exit_price,
        "status": status,
        "returnPct": gross_return_pct - cost_pct,
        "pnl": (exit_price - entry_price) * quantity - cost,
        "decision": dict(decision) if decision is not None else None,
    }


def _baseline_trade(lot: Mapping[str, Any], cost_pct: float = 0.0) -> dict[str, Any]:
    return _resolved_trade(
        lot,
        status=str(lot["resolutionStatus"]),
        exit_timestamp=str(lot["resolutionTimestamp"]),
        exit_price=float(lot["resolutionPrice"]),
        cost_pct=cost_pct,
    )


def _failure_engine_trade(
    lot: Mapping[str, Any],
    model: EmpiricalFailureModel,
    config: TradeFailureResearchConfig,
) -> dict[str, Any]:
    consecutive_failures = 0
    for observation in lot["observations"]:
        prediction = model.predict(observation)
        success_probability = prediction["successProbability"]
        expected_failure_loss = prediction["expectedFailureLossPct"]
        expected_value_pct = (
            success_probability * float(observation["remainingTargetPct"])
            - (1.0 - success_probability) * expected_failure_loss
            - config.cost_pct
        )
        failed_groups = list(observation["failedGroups"])
        thesis_failed = expected_value_pct < 0 and len(failed_groups) >= config.minimum_failed_groups
        consecutive_failures = consecutive_failures + 1 if thesis_failed else 0
        if consecutive_failures < config.decision_persistence_bars:
            continue
        next_open = float(observation["nextOpen"])
        if next_open >= float(lot["targetPrice"]):
            return _resolved_trade(
                lot,
                status="TAKE_PROFIT",
                exit_timestamp=str(observation["nextTimestamp"]),
                exit_price=max(float(lot["targetPrice"]), next_open),
                cost_pct=config.cost_pct,
            )
        decision = {
            "decisionTimestamp": observation["decisionTimestamp"],
            "executionModel": "NEXT_BAR_OPEN",
            "stateKey": observation["stateKey"],
            "features": observation["features"],
            "failedGroups": failed_groups,
            "successProbability": _finite(success_probability),
            "expectedFailureLossPct": _finite(expected_failure_loss),
            "remainingTargetPct": _finite(observation["remainingTargetPct"]),
            "expectedValuePct": _finite(expected_value_pct),
            "stateObservations": int(prediction["stateObservations"]),
            "persistenceBars": consecutive_failures,
        }
        return _resolved_trade(
            lot,
            status="THESIS_FAILED_EXIT",
            exit_timestamp=str(observation["nextTimestamp"]),
            exit_price=next_open,
            cost_pct=config.cost_pct,
            decision=decision,
        )
    return _baseline_trade(lot, config.cost_pct)


def _metrics(trades: list[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda row: (str(row["exitTimestamp"]), str(row["lotId"])))
    pnl = np.array([float(row["pnl"]) for row in ordered], dtype=float)
    returns = np.array([float(row["returnPct"]) for row in ordered], dtype=float)
    cumulative = np.cumsum(pnl) if len(pnl) else np.array([], dtype=float)
    peaks = np.maximum.accumulate(np.maximum(cumulative, 0.0)) if len(cumulative) else np.array([], dtype=float)
    drawdowns = cumulative - peaks if len(cumulative) else np.array([], dtype=float)
    winners = int(np.sum(pnl > 0)) if len(pnl) else 0
    return {
        "trades": len(ordered),
        "netPnl": _finite(pnl.sum() if len(pnl) else 0.0, 2),
        "averagePnl": _finite(pnl.mean() if len(pnl) else 0.0, 2),
        "winRate": _finite(winners / len(ordered) * 100.0 if ordered else 0.0, 2),
        "averageReturnPct": _finite(returns.mean() if len(returns) else 0.0, 4),
        "worstTradePct": _finite(returns.min() if len(returns) else 0.0, 4),
        "maximumDrawdownCurrency": _finite(abs(drawdowns.min()) if len(drawdowns) else 0.0, 2),
        "takeProfits": sum(row["status"] == "TAKE_PROFIT" for row in ordered),
        "thesisFailedExits": sum(row["status"] == "THESIS_FAILED_EXIT" for row in ordered),
        "timeHorizonFailures": sum(row["status"] == "TIME_HORIZON_FAILURE" for row in ordered),
    }


def _fold_boundaries(lot_count: int, folds: int) -> list[tuple[int, int]]:
    first_test = max(1, lot_count // 2)
    remaining = lot_count - first_test
    if remaining <= 0:
        return []
    boundaries = np.linspace(first_test, lot_count, folds + 1, dtype=int)
    return [(int(boundaries[index]), int(boundaries[index + 1])) for index in range(folds) if boundaries[index + 1] > boundaries[index]]


def run_trade_failure_research(
    lots: Iterable[Mapping[str, Any]],
    config: TradeFailureResearchConfig,
) -> dict[str, Any]:
    cfg = config.validate()
    ordered = sorted(lots, key=lambda lot: (str(lot["entryTimestamp"]), str(lot["lotId"])))
    folds: list[dict[str, Any]] = []
    baseline_all: list[dict[str, Any]] = []
    failure_all: list[dict[str, Any]] = []
    audit: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for fold_number, (test_start, test_end) in enumerate(_fold_boundaries(len(ordered), cfg.walk_forward_folds), start=1):
        testing = ordered[test_start:test_end]
        test_cutoff = pd.Timestamp(testing[0]["entryTimestamp"])
        training = [
            lot for lot in ordered[:test_start]
            if pd.Timestamp(lot["resolutionTimestamp"]) < test_cutoff
        ]
        if len(training) < cfg.minimum_training_lots or len(testing) < cfg.minimum_test_lots:
            skipped.append({"fold": fold_number, "trainingLots": len(training), "testLots": len(testing), "reason": "INSUFFICIENT_LOTS"})
            continue
        model = EmpiricalFailureModel(cfg.prior_observations).fit(training)
        baseline = [_baseline_trade(lot, cfg.cost_pct) for lot in testing]
        failure = [_failure_engine_trade(lot, model, cfg) for lot in testing]
        baseline_all.extend(baseline)
        failure_all.extend(failure)
        fold_baseline = _metrics(baseline)
        fold_failure = _metrics(failure)
        folds.append(
            {
                "fold": fold_number,
                "trainingStart": training[0]["entryTimestamp"],
                "trainingEnd": training[-1]["entryTimestamp"],
                "testStart": testing[0]["entryTimestamp"],
                "testEnd": testing[-1]["entryTimestamp"],
                "trainingLots": len(training),
                "trainingObservations": model.observation_count,
                "stateCount": len(model.states),
                "testLots": len(testing),
                "baseline": fold_baseline,
                "failureEngine": fold_failure,
                "netPnlDifference": _finite(float(fold_failure["netPnl"] or 0) - float(fold_baseline["netPnl"] or 0), 2),
                "worstTradeImprovementPct": _finite(float(fold_failure["worstTradePct"] or 0) - float(fold_baseline["worstTradePct"] or 0), 4),
            }
        )
        if len(audit) < cfg.maximum_audit_rows:
            audit.extend([trade for trade in failure if trade["decision"] is not None][: cfg.maximum_audit_rows - len(audit)])
    baseline_metrics = _metrics(baseline_all)
    failure_metrics = _metrics(failure_all)
    fully_evaluated = len(folds) == cfg.walk_forward_folds and not skipped
    stable = fully_evaluated and all(
        float(fold["failureEngine"]["netPnl"] or 0) >= float(fold["baseline"]["netPnl"] or 0)
        and float(fold["failureEngine"]["worstTradePct"] or 0) >= float(fold["baseline"]["worstTradePct"] or 0)
        for fold in folds
    )
    return {
        "mode": cfg.mode,
        "status": "RESEARCH_CANDIDATE" if stable else "REJECTED" if fully_evaluated else "INSUFFICIENT_DATA",
        "liveAutoExitEnabled": False,
        "configuration": cfg.public(),
        "methodology": {
            "model": "HIERARCHICALLY_SMOOTHED_EMPIRICAL_STATE_TABLE",
            "features": list(FAILURE_FEATURES),
            "evidenceGroups": ["STRUCTURE", "MOMENTUM_PARTICIPATION", "PROGRESS"],
            "label": "TARGET_BEFORE_MAXIMUM_HOLDING_HORIZON",
            "decision": "NEGATIVE_EXPECTED_VALUE_FOR_PERSISTENCE_BARS_AND_MINIMUM_INDEPENDENT_GROUPS",
            "execution": "NEXT_BAR_OPEN",
            "split": "EXPANDING_WINDOW_WALK_FORWARD_BY_LOT_ENTRY_TIME_WITH_RESOLUTION_EMBARGO",
            "lookahead": "FEATURES_CAUSAL; FUTURE_CANDLES_USED_ONLY_FOR_TRAINING_LABELS",
        },
        "lotsAvailable": len(ordered),
        "foldsCompleted": len(folds),
        "foldsSkipped": skipped,
        "folds": folds,
        "matchedTestComparison": {
            "baseline": baseline_metrics,
            "failureEngine": failure_metrics,
            "netPnlDifference": _finite(float(failure_metrics["netPnl"] or 0) - float(baseline_metrics["netPnl"] or 0), 2),
            "worstTradeImprovementPct": _finite(float(failure_metrics["worstTradePct"] or 0) - float(baseline_metrics["worstTradePct"] or 0), 4),
            "maximumDrawdownImprovementCurrency": _finite(float(baseline_metrics["maximumDrawdownCurrency"] or 0) - float(failure_metrics["maximumDrawdownCurrency"] or 0), 2),
        },
        "decisionAudit": audit,
        "warnings": [
            "Research comparison only. It never closes live or paper positions.",
            "The current model uses stock-only causal features; NIFTY and sector context are not fabricated when unavailable.",
            "No catastrophic stop is activated by this research run; that boundary requires a separately validated risk limit.",
            "RESEARCH_CANDIDATE requires every configured fold to complete and improve both net P&L and worst trade; it is not live approval.",
        ],
    }
