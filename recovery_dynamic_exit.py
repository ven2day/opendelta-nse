from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd

from main import IST
from recovery_backtest import (
    RecoveryConfig,
    calculate_wilder_rma,
    simulate_recovery_symbol,
)

DYNAMIC_EXIT_VERSION = "recovery-dynamic-exits-1.0.0"
ExitModel = Literal["FIXED_TP_SL", "ATR_DYNAMIC_TP_SL"]
PositionSizing = Literal["FIXED_QUANTITY", "RISK_BUDGET"]
ExitStatus = Literal[
    "TARGET_EXIT",
    "TARGET_GAP",
    "STOP_EXIT",
    "STOP_GAP",
    "TIME_EXIT",
    "OPEN",
]


@dataclass(frozen=True)
class DynamicExitConfig:
    exit_model: ExitModel = "ATR_DYNAMIC_TP_SL"
    fixed_take_profit_pct: float = 0.51
    fixed_stop_loss_pct: float = 1.0
    atr_length: int = 14
    stop_atr_multiplier: float = 1.25
    reward_risk_ratio: float = 1.5
    minimum_stop_pct: float = 0.75
    maximum_stop_pct: float = 3.0
    max_holding_sessions: int = 5
    max_open_lots_per_symbol: int = 1
    position_sizing: PositionSizing = "FIXED_QUANTITY"
    quantity_per_trade: int = 50
    rupee_risk_budget: float = 2_500.0
    maximum_quantity: int = 10_000
    maximum_capital_per_position: float = 1_000_000.0

    def __post_init__(self) -> None:
        if self.exit_model not in {"FIXED_TP_SL", "ATR_DYNAMIC_TP_SL"}:
            raise ValueError("Unsupported dynamic exit model")
        if self.fixed_take_profit_pct <= 0 or self.fixed_stop_loss_pct <= 0:
            raise ValueError("Fixed take-profit and stop-loss percentages must be positive")
        if self.atr_length <= 0:
            raise ValueError("ATR length must be positive")
        if self.stop_atr_multiplier <= 0 or self.reward_risk_ratio <= 0:
            raise ValueError("ATR multiplier and reward-to-risk ratio must be positive")
        if self.minimum_stop_pct <= 0 or self.maximum_stop_pct < self.minimum_stop_pct:
            raise ValueError("Stop bounds must be positive and maximum stop must be at least minimum stop")
        if self.max_holding_sessions <= 0 or self.max_open_lots_per_symbol <= 0:
            raise ValueError("Holding sessions and maximum open lots must be positive")
        if self.quantity_per_trade <= 0 or self.maximum_quantity <= 0:
            raise ValueError("Quantity limits must be positive whole shares")
        if self.rupee_risk_budget <= 0 or self.maximum_capital_per_position <= 0:
            raise ValueError("Risk budget and maximum capital must be positive")

    def public_parameters(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "exitModel": self.exit_model,
            "fixedTakeProfitPct": self.fixed_take_profit_pct,
            "fixedStopLossPct": self.fixed_stop_loss_pct,
            "atrLength": self.atr_length,
            "atrTimeframe": "SELECTED_STRATEGY_TIMEFRAME",
            "stopAtrMultiplier": self.stop_atr_multiplier,
            "rewardRiskRatio": self.reward_risk_ratio,
            "minimumStopPct": self.minimum_stop_pct,
            "maximumStopPct": self.maximum_stop_pct,
            "maxHoldingTradingDays": self.max_holding_sessions,
            "maxOpenLotsPerSymbol": self.max_open_lots_per_symbol,
            "positionSizing": self.position_sizing,
            "quantityPerTrade": self.quantity_per_trade,
            "rupeeRiskBudget": self.rupee_risk_budget,
            "maximumQuantity": self.maximum_quantity,
            "maximumCapitalPerPosition": self.maximum_capital_per_position,
            "timeExit": "NEXT_TRADING_SESSION_OPEN",
        }


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return round(numeric, digits) if math.isfinite(numeric) else None


def _timestamp(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def _iso(value: Any | None) -> str | None:
    return _timestamp(value).isoformat() if value is not None else None


def calculate_wilder_atr(candles: pd.DataFrame, length: int = 14) -> pd.Series:
    """Causal Wilder ATR using the completed candle and its previous close."""
    if length <= 0:
        raise ValueError("ATR length must be positive")
    high = pd.to_numeric(candles["High"], errors="coerce").astype(float)
    low = pd.to_numeric(candles["Low"], errors="coerce").astype(float)
    close = pd.to_numeric(candles["Close"], errors="coerce").astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        (high - low, (high - previous_close).abs(), (low - previous_close).abs()),
        axis=1,
    ).max(axis=1)
    return calculate_wilder_rma(true_range, length)


def _entry_cost(entry_price: float, quantity: int, config: RecoveryConfig) -> tuple[float, float]:
    turnover = entry_price * quantity
    return (
        turnover * config.buy_cost_bps / 10_000.0,
        turnover * config.slippage_bps / 10_000.0,
    )


def _exit_cost(exit_price: float, quantity: int, config: RecoveryConfig) -> tuple[float, float]:
    turnover = exit_price * quantity
    return (
        turnover * config.sell_cost_bps / 10_000.0,
        turnover * config.slippage_bps / 10_000.0,
    )


def _quantity_for(
    entry_price: float,
    stop_price: float,
    config: DynamicExitConfig,
) -> int:
    if config.position_sizing == "FIXED_QUANTITY":
        quantity = config.quantity_per_trade
    else:
        risk_per_share = entry_price - stop_price
        if risk_per_share <= 0:
            raise ValueError("Risk per share must be positive")
        quantity = math.floor(config.rupee_risk_budget / risk_per_share)
    capital_limit_quantity = math.floor(config.maximum_capital_per_position / entry_price)
    quantity = min(quantity, config.maximum_quantity, capital_limit_quantity)
    if quantity <= 0:
        raise ValueError("Position sizing produced zero shares; increase risk/capital limits")
    return int(quantity)


def _new_position(
    candidate: dict[str, Any],
    *,
    signal_index: int,
    entry_session_ordinal: int,
    atr_at_signal: float,
    timeframe: str,
    exit_config: DynamicExitConfig,
) -> dict[str, Any]:
    entry_price = float(candidate["entryPrice"])
    if not math.isfinite(atr_at_signal) or atr_at_signal <= 0:
        raise ValueError("ATR is unavailable at signal time")
    raw_atr_pct = atr_at_signal / entry_price * 100.0
    if exit_config.exit_model == "ATR_DYNAMIC_TP_SL":
        stop_pct = min(
            max(raw_atr_pct * exit_config.stop_atr_multiplier, exit_config.minimum_stop_pct),
            exit_config.maximum_stop_pct,
        )
        target_pct = stop_pct * exit_config.reward_risk_ratio
    else:
        stop_pct = exit_config.fixed_stop_loss_pct
        target_pct = exit_config.fixed_take_profit_pct
    stop_price = entry_price * (1.0 - stop_pct / 100.0)
    target_price = entry_price * (1.0 + target_pct / 100.0)
    quantity = _quantity_for(entry_price, stop_price, exit_config)
    return {
        "tradeId": candidate["tradeId"],
        "sequenceNumber": candidate["sequenceNumber"],
        "candidate": candidate,
        "signalIndex": signal_index,
        "entryIndex": int(candidate["entryBarIndex"]),
        "entryPrice": entry_price,
        "targetPrice": target_price,
        "stopLossPrice": stop_price,
        "quantity": quantity,
        "capitalDeployed": entry_price * quantity,
        "rupeeRiskAtEntry": (entry_price - stop_price) * quantity,
        "entrySessionOrdinal": entry_session_ordinal,
        "lowestPrice": None,
        "highestPrice": None,
        "atrLength": exit_config.atr_length,
        "atrTimeframe": timeframe,
        "atrAtSignal": atr_at_signal,
        "atrPctAtEntry": raw_atr_pct,
        "stopAtrMultiplier": exit_config.stop_atr_multiplier,
        "rewardRiskRatio": target_pct / stop_pct,
        "minimumStopPct": exit_config.minimum_stop_pct,
        "maximumStopPct": exit_config.maximum_stop_pct,
        "dynamicStopPct": stop_pct,
        "dynamicTargetPct": target_pct,
        "exitModel": exit_config.exit_model,
    }


def _update_excursion(position: dict[str, Any], low: float, high: float) -> None:
    position["lowestPrice"] = low if position["lowestPrice"] is None else min(position["lowestPrice"], low)
    position["highestPrice"] = high if position["highestPrice"] is None else max(position["highestPrice"], high)


def _skipped_signal(candidate: dict[str, Any], execution_model: str) -> dict[str, Any]:
    known = execution_model == "SIGNAL_CLOSE"
    return {
        "tradeId": candidate["tradeId"],
        "sequenceNumber": candidate["sequenceNumber"],
        "signalTimestamp": candidate["signalTimestamp"],
        "entryTimestamp": candidate["entryTimestamp"] if known else None,
        "entryPrice": candidate["entryPrice"] if known else None,
        "oiRegimeAtSignal": candidate.get("oiRegimeAtSignal"),
        "oiScoreAtSignal": candidate.get("oiScoreAtSignal"),
        "oiConfidence": candidate.get("oiConfidence"),
        "oiDecision": candidate.get("oiDecision"),
        "oiSourceTimestamp": candidate.get("oiSourceTimestamp"),
        "quantity": 0,
        "capitalDeployed": 0.0,
        "status": "SKIPPED_MAX_OPEN_LOTS",
        "exitReason": "SKIPPED_MAX_OPEN_LOTS",
        "reason": "A valid RSI Recovery signal occurred while the configured maximum open lots was already reached.",
    }


def _finish_position(
    position: dict[str, Any],
    *,
    candles: pd.DataFrame,
    end_index: int,
    status: ExitStatus,
    exit_price: float | None,
    holding_sessions: int,
    recovery_config: RecoveryConfig,
) -> dict[str, Any]:
    candidate = position["candidate"]
    entry_price = float(position["entryPrice"])
    quantity = int(position["quantity"])
    last_close = float(candles.iloc[end_index]["Close"])
    mark_or_exit = float(exit_price if exit_price is not None else last_close)
    lowest = float(position["lowestPrice"] if position["lowestPrice"] is not None else entry_price)
    highest = float(position["highestPrice"] if position["highestPrice"] is not None else entry_price)
    gross_pnl = (mark_or_exit - entry_price) * quantity
    buy_cost, buy_slippage = _entry_cost(entry_price, quantity, recovery_config)
    sell_cost, sell_slippage = _exit_cost(mark_or_exit, quantity, recovery_config)
    closed = status != "OPEN"
    realized_costs = buy_cost + buy_slippage + sell_cost + sell_slippage
    net_pnl = gross_pnl - realized_costs
    entry_stamp = _timestamp(candidate["entryTimestamp"])
    end_stamp = _timestamp(candles.index[end_index])
    duration_minutes = max((end_stamp - entry_stamp).total_seconds() / 60.0, 0.0)
    return {
        "tradeId": position["tradeId"],
        "sequenceNumber": position["sequenceNumber"],
        "signalTimestamp": candidate["signalTimestamp"],
        "entryTimestamp": candidate["entryTimestamp"],
        "entryBarIndex": int(position["entryIndex"]),
        "executionModel": candidate["executionModel"],
        "exitModel": position["exitModel"],
        "entryPrice": _finite(entry_price, 4),
        "quantity": quantity,
        "capitalDeployed": _finite(position["capitalDeployed"], 2),
        "atrLength": position["atrLength"],
        "atrTimeframe": position["atrTimeframe"],
        "atrAtSignal": _finite(position["atrAtSignal"], 6),
        "atrPctAtEntry": _finite(position["atrPctAtEntry"], 6),
        "stopAtrMultiplier": position["stopAtrMultiplier"],
        "rewardRiskRatio": _finite(position["rewardRiskRatio"], 6),
        "minimumStopPct": position["minimumStopPct"],
        "maximumStopPct": position["maximumStopPct"],
        "dynamicStopPct": _finite(position["dynamicStopPct"], 6),
        "dynamicTargetPct": _finite(position["dynamicTargetPct"], 6),
        "stopLossPrice": _finite(position["stopLossPrice"], 4),
        "targetPrice": _finite(position["targetPrice"], 4),
        "rupeeRiskAtEntry": _finite(position["rupeeRiskAtEntry"], 2),
        "exitTimestamp": _iso(candles.index[end_index]) if closed else None,
        "exitPrice": _finite(exit_price, 4),
        "exitReason": status if closed else None,
        "status": status,
        "holdingSessions": holding_sessions,
        "tradingSessionsHeld": holding_sessions,
        "barsHeld": max(end_index - int(position["entryIndex"]), 0),
        "durationMinutes": _finite(duration_minutes, 2),
        "durationHours": _finite(duration_minutes / 60.0, 4),
        "durationDays": _finite(duration_minutes / 1_440.0, 4),
        "lowestPriceAfterEntry": _finite(lowest, 4),
        "maxAdversePct": _finite((lowest / entry_price - 1.0) * 100.0, 6),
        "highestPriceAfterEntry": _finite(highest, 4),
        "maxFavorablePct": _finite((highest / entry_price - 1.0) * 100.0, 6),
        "grossPnl": _finite(gross_pnl, 2),
        "buyCost": _finite(buy_cost, 2),
        "sellCost": _finite(sell_cost if closed else 0.0, 2),
        "slippageCost": _finite(buy_slippage + (sell_slippage if closed else 0.0), 2),
        "tradingCosts": _finite(realized_costs if closed else buy_cost + buy_slippage, 2),
        "estimatedOpenExitCost": _finite((sell_cost + sell_slippage) if not closed else 0.0, 2),
        "totalCosts": _finite(realized_costs, 2),
        "netPnl": _finite(net_pnl, 2),
        "realizedPnl": _finite(net_pnl, 2) if closed else None,
        "unrealizedPnl": _finite(net_pnl, 2) if not closed else 0.0,
        "lastTimestamp": _iso(candles.index[end_index]),
        "lastClose": _finite(last_close, 4),
        "confirmationScore": candidate["confirmationScore"],
        "requiredConfirmations": candidate["requiredConfirmations"],
        "rsiAtEntry": candidate["rsiAtEntry"],
        "oiRegimeAtSignal": candidate.get("oiRegimeAtSignal"),
        "oiScoreAtSignal": candidate.get("oiScoreAtSignal"),
        "oiConfidence": candidate.get("oiConfidence"),
        "oiDecision": candidate.get("oiDecision"),
        "oiSourceTimestamp": candidate.get("oiSourceTimestamp"),
    }


def _summary(
    positions: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    *,
    total_valid_signals: int,
    maximum_concurrent: int,
    peak_capital: float,
    maximum_drawdown: float,
) -> dict[str, Any]:
    closed = [position for position in positions if position["status"] != "OPEN"]
    open_positions = [position for position in positions if position["status"] == "OPEN"]
    statuses = {name: sum(position["status"] == name for position in positions) for name in (
        "TARGET_EXIT", "TARGET_GAP", "STOP_EXIT", "STOP_GAP", "TIME_EXIT"
    )}
    realized = [float(position["realizedPnl"]) for position in closed]
    gross = [float(position["grossPnl"]) for position in closed]
    profits = [value for value in realized if value > 0]
    losses = [value for value in realized if value < 0]
    # Closed trades include both sides; OPEN trades include the entry side
    # actually incurred. Estimated closing costs are disclosed separately.
    costs = sum(float(position["tradingCosts"]) for position in positions)
    estimated_open_exit_costs = sum(
        float(position["estimatedOpenExitCost"]) for position in open_positions
    )
    unrealized = sum(float(position["unrealizedPnl"]) for position in open_positions)
    holding_sessions = [float(position["holdingSessions"]) for position in positions]
    dynamic_targets = [float(position["dynamicTargetPct"]) for position in positions]
    dynamic_stops = [float(position["dynamicStopPct"]) for position in positions]
    reward_risks = [float(position["rewardRiskRatio"]) for position in positions]
    realized_total = sum(realized)
    gross_profit = sum(value for value in gross if value > 0)
    gross_loss = abs(sum(value for value in gross if value < 0))
    return {
        "totalValidBuySignals": total_valid_signals,
        "totalBuySignals": total_valid_signals,
        "buySignals": total_valid_signals,
        "executedTrades": len(positions),
        "skippedMaxOpenLots": len(skipped),
        "targetExits": statuses["TARGET_EXIT"],
        "targetGapExits": statuses["TARGET_GAP"],
        "targetsHit": statuses["TARGET_EXIT"] + statuses["TARGET_GAP"],
        "stopExits": statuses["STOP_EXIT"],
        "stopGapExits": statuses["STOP_GAP"],
        "timeExits": statuses["TIME_EXIT"],
        "openPositions": len(open_positions),
        "openSignals": len(open_positions),
        "winningTrades": len(profits),
        "losingTrades": len(losses),
        "profitableClosedTrades": len(profits),
        "losingClosedTrades": len(losses),
        "winRate": _finite(len(profits) / len(closed) * 100.0, 2) if closed else 0.0,
        "targetHitRate": _finite((statuses["TARGET_EXIT"] + statuses["TARGET_GAP"]) / len(positions) * 100.0, 2) if positions else 0.0,
        "averageDynamicTargetPct": _finite(float(np.mean(dynamic_targets)), 6) if dynamic_targets else None,
        "averageDynamicStopPct": _finite(float(np.mean(dynamic_stops)), 6) if dynamic_stops else None,
        "averageRewardRisk": _finite(float(np.mean(reward_risks)), 6) if reward_risks else None,
        "grossProfit": _finite(gross_profit, 2),
        "grossLoss": _finite(gross_loss, 2),
        "realizedGrossProfit": _finite(gross_profit, 2),
        "realizedGrossLoss": _finite(gross_loss, 2),
        "tradingCosts": _finite(costs, 2),
        "estimatedOpenExitCosts": _finite(estimated_open_exit_costs, 2),
        "netRealizedPnl": _finite(realized_total, 2),
        "unrealizedPnl": _finite(unrealized, 2),
        "combinedPnl": _finite(realized_total + unrealized, 2),
        "averageWinner": _finite(float(np.mean(profits)), 2) if profits else None,
        "averageLoser": _finite(float(np.mean(losses)), 2) if losses else None,
        "averageProfitPerTrade": _finite(float(np.mean(profits)), 2) if profits else None,
        "averageLossPerTrade": _finite(float(np.mean(losses)), 2) if losses else None,
        "profitFactor": _finite(sum(profits) / abs(sum(losses)), 4) if losses else None,
        "expectancyPerTrade": _finite(float(np.mean(realized)), 2) if realized else None,
        "maximumDrawdown": _finite(maximum_drawdown, 2),
        "maximumDrawdownPct": _finite(maximum_drawdown / peak_capital * 100.0, 4) if peak_capital else 0.0,
        "maximumConcurrentPositions": maximum_concurrent,
        "maxConcurrentPositions": maximum_concurrent,
        "peakCapitalDeployed": _finite(peak_capital, 2),
        "averageHoldingSessions": _finite(float(np.mean(holding_sessions)), 2) if holding_sessions else None,
        "medianHoldingSessions": _finite(float(np.median(holding_sessions)), 2) if holding_sessions else None,
    }


def simulate_dynamic_exit_symbol(
    symbol: str,
    candles: pd.DataFrame,
    *,
    timeframe: str,
    recovery_config: RecoveryConfig,
    exit_config: DynamicExitConfig,
    run_id: str,
    analysis_start: datetime | None = None,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply fixed or ATR-frozen TP/SL exits to unchanged RSI Recovery signals."""
    observations = observations or simulate_recovery_symbol(
        symbol,
        candles,
        timeframe=timeframe,
        config=recovery_config,
        run_id=run_id,
        analysis_start=analysis_start,
    )
    candidates = sorted(observations["trades"], key=lambda item: int(item["sequenceNumber"]))
    data = candles.copy()
    if not isinstance(data.index, pd.DatetimeIndex):
        raise TypeError("Dynamic exit backtest requires a DatetimeIndex")
    data.index = data.index.tz_localize(IST) if data.index.tz is None else data.index.tz_convert(IST)
    data = data.sort_index()
    atr_values = calculate_wilder_atr(data, exit_config.atr_length).to_numpy(dtype=float, copy=False)
    start_position = int(data.index.searchsorted(_timestamp(analysis_start), side="left")) if analysis_start else 0
    open_values = data["Open"].to_numpy(dtype=float, copy=False)
    high_values = data["High"].to_numpy(dtype=float, copy=False)
    low_values = data["Low"].to_numpy(dtype=float, copy=False)
    close_values = data["Close"].to_numpy(dtype=float, copy=False)
    session_dates = list(data.index.date)
    ordered_sessions = list(dict.fromkeys(session_dates))
    session_ordinals = {session: index for index, session in enumerate(ordered_sessions)}
    first_index_by_session: dict[Any, int] = {}
    for index, session in enumerate(session_dates):
        first_index_by_session.setdefault(session, index)

    signal_candidates: dict[int, list[dict[str, Any]]] = {}
    for candidate in candidates:
        entry_index = int(candidate["entryBarIndex"])
        signal_index = entry_index if recovery_config.execution_model == "SIGNAL_CLOSE" else entry_index - 1
        signal_candidates.setdefault(signal_index, []).append(candidate)

    active: list[dict[str, Any]] = []
    pending_entries: dict[int, list[tuple[dict[str, Any], int, float]]] = {}
    positions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    realized_net = 0.0
    equity_peak = 0.0
    maximum_drawdown = 0.0
    maximum_concurrent = 0
    peak_capital = 0.0

    def capture_capacity() -> None:
        nonlocal maximum_concurrent, peak_capital
        maximum_concurrent = max(maximum_concurrent, len(active))
        peak_capital = max(peak_capital, sum(float(position["capitalDeployed"]) for position in active))

    def close_position(position: dict[str, Any], index: int, status: ExitStatus, price: float, held: int) -> None:
        nonlocal realized_net
        completed = _finish_position(
            position,
            candles=data,
            end_index=index,
            status=status,
            exit_price=price,
            holding_sessions=held,
            recovery_config=recovery_config,
        )
        positions.append(completed)
        realized_net += float(completed["realizedPnl"])

    for index in range(start_position, len(data)):
        session = session_dates[index]
        session_ordinal = session_ordinals[session]
        first_bar = first_index_by_session[session] == index
        open_price = float(open_values[index])

        after_open: list[dict[str, Any]] = []
        for position in active:
            if index <= int(position["entryIndex"]):
                after_open.append(position)
                continue
            held = session_ordinal - int(position["entrySessionOrdinal"]) + 1
            if open_price <= float(position["stopLossPrice"]):
                _update_excursion(position, open_price, open_price)
                close_position(position, index, "STOP_GAP", open_price, held)
            elif open_price >= float(position["targetPrice"]):
                _update_excursion(position, open_price, open_price)
                close_position(position, index, "TARGET_GAP", open_price, held)
            elif first_bar and session_ordinal >= int(position["entrySessionOrdinal"]) + exit_config.max_holding_sessions:
                _update_excursion(position, open_price, open_price)
                close_position(position, index, "TIME_EXIT", open_price, exit_config.max_holding_sessions)
            else:
                after_open.append(position)
        active = after_open

        for candidate, signal_index, atr_at_signal in pending_entries.pop(index, []):
            active.append(_new_position(
                candidate,
                signal_index=signal_index,
                entry_session_ordinal=session_ordinal,
                atr_at_signal=atr_at_signal,
                timeframe=timeframe,
                exit_config=exit_config,
            ))
        capture_capacity()

        after_intrabar: list[dict[str, Any]] = []
        for position in active:
            if index <= int(position["entryIndex"]):
                after_intrabar.append(position)
                continue
            low = float(low_values[index])
            high = float(high_values[index])
            _update_excursion(position, low, high)
            held = session_ordinal - int(position["entrySessionOrdinal"]) + 1
            stop_hit = low <= float(position["stopLossPrice"])
            target_hit = high >= float(position["targetPrice"])
            if stop_hit:
                close_position(position, index, "STOP_EXIT", float(position["stopLossPrice"]), held)
            elif target_hit:
                close_position(position, index, "TARGET_EXIT", float(position["targetPrice"]), held)
            else:
                after_intrabar.append(position)
        active = after_intrabar

        for candidate in signal_candidates.get(index, []):
            if len(active) >= exit_config.max_open_lots_per_symbol:
                skipped.append(_skipped_signal(candidate, recovery_config.execution_model))
                continue
            atr_at_signal = float(atr_values[index])
            if not math.isfinite(atr_at_signal) or atr_at_signal <= 0:
                raise ValueError(f"ATR({exit_config.atr_length}) is unavailable for signal at {data.index[index].isoformat()}")
            if recovery_config.execution_model == "NEXT_BAR_OPEN":
                pending_entries.setdefault(int(candidate["entryBarIndex"]), []).append((candidate, index, atr_at_signal))
            else:
                active.append(_new_position(
                    candidate,
                    signal_index=index,
                    entry_session_ordinal=session_ordinal,
                    atr_at_signal=atr_at_signal,
                    timeframe=timeframe,
                    exit_config=exit_config,
                ))
        capture_capacity()

        marked_pnl = realized_net
        for position in active:
            quantity = int(position["quantity"])
            price = float(close_values[index])
            gross = (price - float(position["entryPrice"])) * quantity
            buy_cost, buy_slippage = _entry_cost(float(position["entryPrice"]), quantity, recovery_config)
            sell_cost, sell_slippage = _exit_cost(price, quantity, recovery_config)
            marked_pnl += gross - buy_cost - buy_slippage - sell_cost - sell_slippage
        equity_peak = max(equity_peak, marked_pnl)
        maximum_drawdown = max(maximum_drawdown, equity_peak - marked_pnl)

    final_index = len(data) - 1
    final_session_ordinal = session_ordinals[session_dates[final_index]]
    for position in active:
        positions.append(_finish_position(
            position,
            candles=data,
            end_index=final_index,
            status="OPEN",
            exit_price=None,
            holding_sessions=final_session_ordinal - int(position["entrySessionOrdinal"]) + 1,
            recovery_config=recovery_config,
        ))
    positions.sort(key=lambda item: int(item["sequenceNumber"]))
    skipped.sort(key=lambda item: int(item["sequenceNumber"]))
    summary = _summary(
        positions,
        skipped,
        total_valid_signals=len(candidates),
        maximum_concurrent=maximum_concurrent,
        peak_capital=peak_capital,
        maximum_drawdown=maximum_drawdown,
    )
    return {
        "symbol": symbol,
        "firstCandle": observations["firstCandle"],
        "lastCandle": observations["lastCandle"],
        "bars": observations["bars"],
        **summary,
        "positions": positions,
        "skippedSignals": skipped,
        "trades": positions,
        "events": observations["events"],
        "chart": observations["chart"],
    }


def aggregate_dynamic_exit_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    positions = [position for result in results for position in result.get("positions", [])]
    skipped = [signal for result in results for signal in result.get("skippedSignals", [])]
    exposure_events: list[tuple[pd.Timestamp, int, float]] = []
    for position in positions:
        capital = float(position["capitalDeployed"])
        exposure_events.append((_timestamp(position["entryTimestamp"]), 1, capital))
        if position["exitTimestamp"] is not None:
            exposure_events.append((_timestamp(position["exitTimestamp"]), -1, -capital))
    exposure_events.sort(key=lambda event: (event[0], event[1]))
    active_count = 0
    active_capital = 0.0
    maximum_concurrent = 0
    peak_capital = 0.0
    for _, count_delta, capital_delta in exposure_events:
        active_count += count_delta
        active_capital += capital_delta
        maximum_concurrent = max(maximum_concurrent, active_count)
        peak_capital = max(peak_capital, active_capital)
    summary = _summary(
        positions,
        skipped,
        total_valid_signals=sum(int(result.get("totalValidBuySignals", 0)) for result in results),
        maximum_concurrent=maximum_concurrent,
        peak_capital=peak_capital,
        maximum_drawdown=sum(float(result.get("maximumDrawdown", 0.0) or 0.0) for result in results),
    )
    summary["candleRowsProcessed"] = sum(int(result.get("bars", 0)) for result in results)
    return summary
