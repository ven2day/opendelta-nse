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
    calculate_recovery_indicators,
    simulate_recovery_symbol,
)

RSI_PROFIT_EXIT_VERSION = "recovery-rsi-profit-risk-control-1.0.0"
RsiExitExecution = Literal["SIGNAL_CLOSE", "NEXT_BAR_OPEN"]
RsiProfitExitStatus = Literal[
    "RSI_PROFIT_EXIT",
    "RSI_OVERBOUGHT_PROFIT_EXIT",
    "STOP_EXIT",
    "STOP_GAP",
    "TIME_EXIT",
    "OPEN",
]


@dataclass(frozen=True)
class RsiProfitExitConfig:
    minimum_profit_pct: float = 0.5
    profit_exit_rsi: float = 50.0
    upper_rsi_level: float = 70.0
    stop_loss_pct: float = 1.5
    exit_execution_model: RsiExitExecution = "SIGNAL_CLOSE"
    max_holding_sessions: int = 5
    max_open_lots_per_symbol: int = 1
    quantity_per_trade: int = 50

    def __post_init__(self) -> None:
        if self.minimum_profit_pct <= 0:
            raise ValueError("Minimum profit percentage must be positive")
        if not 0 <= self.profit_exit_rsi <= 100:
            raise ValueError("Profit-exit RSI must be between 0 and 100")
        if not 0 <= self.upper_rsi_level <= 100:
            raise ValueError("Upper RSI level must be between 0 and 100")
        if self.upper_rsi_level < self.profit_exit_rsi:
            raise ValueError("Upper RSI level cannot be below the profit-exit RSI")
        if self.stop_loss_pct <= 0 or self.stop_loss_pct >= 100:
            raise ValueError("Hard stop-loss percentage must be greater than 0 and below 100")
        if self.exit_execution_model not in {"SIGNAL_CLOSE", "NEXT_BAR_OPEN"}:
            raise ValueError("Unsupported RSI exit execution model")
        if self.max_holding_sessions <= 0:
            raise ValueError("Maximum holding sessions must be positive")
        if self.max_open_lots_per_symbol <= 0:
            raise ValueError("Maximum open lots per symbol must be positive")
        if self.quantity_per_trade <= 0:
            raise ValueError("Quantity must be a positive whole number")

    def public_parameters(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "exitModel": "RSI_PROFIT_RISK_CONTROL",
            "minimumProfitPct": self.minimum_profit_pct,
            "profitExitRsi": self.profit_exit_rsi,
            "upperRsiLevel": self.upper_rsi_level,
            "hardStopLossPct": self.stop_loss_pct,
            "rsiExitExecutionModel": self.exit_execution_model,
            "maxHoldingTradingDays": self.max_holding_sessions,
            "maxOpenLotsPerSymbol": self.max_open_lots_per_symbol,
            "quantityPerTrade": self.quantity_per_trade,
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


def _new_position(
    candidate: dict[str, Any],
    *,
    symbol: str,
    run_id: str,
    entry_session_ordinal: int,
    exit_config: RsiProfitExitConfig,
) -> dict[str, Any]:
    entry_price = float(candidate["entryPrice"])
    minimum_exit_price = entry_price * (1.0 + exit_config.minimum_profit_pct / 100.0)
    stop_price = entry_price * (1.0 - exit_config.stop_loss_pct / 100.0)
    quantity = int(exit_config.quantity_per_trade)
    return {
        "tradeId": candidate["tradeId"],
        "sequenceNumber": candidate["sequenceNumber"],
        "runId": run_id,
        "symbol": symbol,
        "candidate": candidate,
        "entryIndex": int(candidate["entryBarIndex"]),
        "entryPrice": entry_price,
        "minimumProfitableExitPrice": minimum_exit_price,
        "stopLossPrice": stop_price,
        "quantity": quantity,
        "capitalDeployed": entry_price * quantity,
        "rupeeRiskAtEntry": (entry_price - stop_price) * quantity,
        "entrySessionOrdinal": entry_session_ordinal,
        "lowestPrice": None,
        "highestPrice": None,
        "pendingRsiExitReason": None,
        "pendingRsiExitRsi": None,
        "pendingRsiExitSignalIndex": None,
    }


def _update_excursion(position: dict[str, Any], low: float, high: float) -> None:
    position["lowestPrice"] = low if position["lowestPrice"] is None else min(position["lowestPrice"], low)
    position["highestPrice"] = high if position["highestPrice"] is None else max(position["highestPrice"], high)


def _skipped_signal(candidate: dict[str, Any], execution_model: str) -> dict[str, Any]:
    entry_known = execution_model == "SIGNAL_CLOSE"
    return {
        "tradeId": candidate["tradeId"],
        "sequenceNumber": candidate["sequenceNumber"],
        "symbol": candidate.get("symbol"),
        "signalTimestamp": candidate["signalTimestamp"],
        "entryTimestamp": candidate["entryTimestamp"] if entry_known else None,
        "entryPrice": candidate["entryPrice"] if entry_known else None,
        "rsiArmTimestamp": candidate.get("rsiArmTimestamp"),
        "rsiAtSignal": candidate.get("rsiAtEntry"),
        "confirmationScore": candidate.get("confirmationScore"),
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
    status: RsiProfitExitStatus,
    exit_price: float | None,
    exit_rsi: float | None,
    holding_sessions: int,
    timeframe: str,
    recovery_config: RecoveryConfig,
    exit_config: RsiProfitExitConfig,
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
    exit_fill = {
        "RSI_PROFIT_EXIT": exit_config.exit_execution_model,
        "RSI_OVERBOUGHT_PROFIT_EXIT": exit_config.exit_execution_model,
        "STOP_GAP": "GAP_OPEN",
        "STOP_EXIT": "STOP_PRICE",
        "TIME_EXIT": "NEXT_TRADING_SESSION_OPEN",
    }.get(status)
    return {
        "tradeId": position["tradeId"],
        "sequenceNumber": position["sequenceNumber"],
        "runId": position["runId"],
        "strategyMode": "rsi_recovery_position",
        "symbol": position["symbol"],
        "timeframe": timeframe,
        "signalTimestamp": candidate["signalTimestamp"],
        "entryTimestamp": candidate["entryTimestamp"],
        "entryBarIndex": int(position["entryIndex"]),
        "entryExecutionModel": candidate["executionModel"],
        "executionModel": candidate["executionModel"],
        "exitExecutionModel": exit_config.exit_execution_model,
        "exitModel": "RSI_PROFIT_RISK_CONTROL",
        "entryPrice": _finite(entry_price, 4),
        "quantity": quantity,
        "capitalDeployed": _finite(position["capitalDeployed"], 2),
        "minimumProfitPct": exit_config.minimum_profit_pct,
        "minimumProfitableExitPrice": _finite(position["minimumProfitableExitPrice"], 4),
        "targetPrice": _finite(position["minimumProfitableExitPrice"], 4),
        "dynamicTargetPct": exit_config.minimum_profit_pct,
        "profitExitRsi": exit_config.profit_exit_rsi,
        "upperRsiLevel": exit_config.upper_rsi_level,
        "stopLossPct": exit_config.stop_loss_pct,
        "dynamicStopPct": exit_config.stop_loss_pct,
        "stopLossPrice": _finite(position["stopLossPrice"], 4),
        "rupeeRiskAtEntry": _finite(position["rupeeRiskAtEntry"], 2),
        "exitTimestamp": _iso(candles.index[end_index]) if closed else None,
        "exitRsi": _finite(exit_rsi, 6),
        "exitPrice": _finite(exit_price, 4),
        "exitReason": status if closed else None,
        "exitFill": exit_fill,
        "status": status,
        "holdingSessions": holding_sessions,
        "tradingSessionsHeld": holding_sessions,
        "barsHeld": max(end_index - int(position["entryIndex"]), 0),
        "durationMinutes": _finite(duration_minutes, 2),
        "durationHours": _finite(duration_minutes / 60.0, 4),
        "durationDays": _finite(duration_minutes / 1_440.0, 4),
        "rsiArmTimestamp": candidate.get("rsiArmTimestamp"),
        "rsiArmValue": candidate.get("rsiArmValue"),
        "rsiAtSignal": candidate.get("rsiAtEntry"),
        "rsiAtEntry": candidate.get("rsiAtEntry"),
        "emaConfirmation": candidate.get("emaConfirmation"),
        "vwapConfirmation": candidate.get("vwapConfirmation"),
        "volumeConfirmation": candidate.get("volumeConfirmation"),
        "confirmationScore": candidate.get("confirmationScore"),
        "confirmationsPassed": candidate.get("confirmationScore"),
        "confirmationsEnabled": recovery_config.enabled_confirmations,
        "requiredConfirmations": candidate.get("requiredConfirmations"),
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
    }


def _maximum_consecutive_losses(positions: list[dict[str, Any]]) -> int:
    ordered = sorted(
        (position for position in positions if position["status"] != "OPEN"),
        key=lambda position: (
            _timestamp(position["exitTimestamp"]),
            str(position.get("symbol", "")),
            int(position["sequenceNumber"]),
        ),
    )
    current = 0
    maximum = 0
    for position in ordered:
        if float(position["realizedPnl"]) < 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


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
    status_names = (
        "RSI_PROFIT_EXIT",
        "RSI_OVERBOUGHT_PROFIT_EXIT",
        "STOP_EXIT",
        "STOP_GAP",
        "TIME_EXIT",
    )
    statuses = {
        name: sum(position["status"] == name for position in positions)
        for name in status_names
    }
    realized = [float(position["realizedPnl"]) for position in closed]
    gross = [float(position["grossPnl"]) for position in closed]
    profits = [value for value in realized if value > 0]
    losses = [value for value in realized if value < 0]
    net_profit = sum(profits)
    net_loss = abs(sum(losses))
    costs = sum(float(position["tradingCosts"]) for position in positions)
    closed_costs = [float(position["tradingCosts"]) for position in closed]
    estimated_open_exit_costs = sum(
        float(position["estimatedOpenExitCost"]) for position in open_positions
    )
    unrealized = sum(float(position["unrealizedPnl"]) for position in open_positions)
    holding_sessions = [float(position["holdingSessions"]) for position in positions]
    holding_minutes = [float(position["durationMinutes"]) for position in positions]
    realized_total = sum(realized)
    gross_profit = sum(value for value in gross if value > 0)
    gross_loss = abs(sum(value for value in gross if value < 0))
    gross_winners = [float(position["grossPnl"]) for position in closed if float(position["realizedPnl"]) > 0]
    gross_losers = [float(position["grossPnl"]) for position in closed if float(position["realizedPnl"]) < 0]
    win_rate_fraction = len(profits) / len(closed) if closed else 0.0
    loss_rate_fraction = len(losses) / len(closed) if closed else 0.0
    average_gross_winner = float(np.mean(gross_winners)) if gross_winners else 0.0
    average_gross_loser = float(np.mean(gross_losers)) if gross_losers else 0.0
    average_cost = float(np.mean(closed_costs)) if closed_costs else 0.0
    expectancy = (
        win_rate_fraction * average_gross_winner
        + loss_rate_fraction * average_gross_loser
        - average_cost
    ) if closed else None
    rsi_exits = statuses["RSI_PROFIT_EXIT"] + statuses["RSI_OVERBOUGHT_PROFIT_EXIT"]
    return {
        "totalValidBuySignals": total_valid_signals,
        "totalBuySignals": total_valid_signals,
        "buySignals": total_valid_signals,
        "executedTrades": len(positions),
        "skippedMaxOpenLots": len(skipped),
        "rsiProfitExits": statuses["RSI_PROFIT_EXIT"],
        "rsiOverboughtProfitExits": statuses["RSI_OVERBOUGHT_PROFIT_EXIT"],
        "profitableRsiExits": rsi_exits,
        "targetExits": 0,
        "targetGapExits": 0,
        "targetsHit": rsi_exits,
        "stopExits": statuses["STOP_EXIT"],
        "stopGapExits": statuses["STOP_GAP"],
        "timeExits": statuses["TIME_EXIT"],
        "openPositions": len(open_positions),
        "openSignals": len(open_positions),
        "winningTrades": len(profits),
        "losingTrades": len(losses),
        "profitableClosedTrades": len(profits),
        "losingClosedTrades": len(losses),
        "winRate": _finite(win_rate_fraction * 100.0, 2) if closed else 0.0,
        "profitableExitRate": _finite(rsi_exits / len(positions) * 100.0, 2) if positions else 0.0,
        "targetHitRate": _finite(rsi_exits / len(positions) * 100.0, 2) if positions else 0.0,
        "averageDynamicTargetPct": _finite(float(np.mean([position["minimumProfitPct"] for position in positions])), 6) if positions else None,
        "averageDynamicStopPct": _finite(float(np.mean([position["stopLossPct"] for position in positions])), 6) if positions else None,
        "averageRewardRisk": None,
        "grossProfit": _finite(gross_profit, 2),
        "grossLoss": _finite(gross_loss, 2),
        "realizedGrossProfit": _finite(gross_profit, 2),
        "realizedGrossLoss": _finite(gross_loss, 2),
        "netProfit": _finite(net_profit, 2),
        "netLoss": _finite(net_loss, 2),
        "tradingCosts": _finite(costs, 2),
        "averageCostsPerClosedTrade": _finite(average_cost, 2) if closed else None,
        "estimatedOpenExitCosts": _finite(estimated_open_exit_costs, 2),
        "netRealizedPnl": _finite(realized_total, 2),
        "unrealizedPnl": _finite(unrealized, 2),
        "combinedPnl": _finite(realized_total + unrealized, 2),
        "averageWinner": _finite(float(np.mean(profits)), 2) if profits else None,
        "averageLoser": _finite(float(np.mean(losses)), 2) if losses else None,
        "averageProfitPerTrade": _finite(float(np.mean(profits)), 2) if profits else None,
        "averageLossPerTrade": _finite(float(np.mean(losses)), 2) if losses else None,
        "profitFactor": _finite(net_profit / net_loss, 4) if net_loss else None,
        "expectancyPerTrade": _finite(expectancy, 2),
        "expectancyFormula": "(win_rate * average_gross_winner) - (loss_rate * absolute_average_gross_loser) - average_closed_trade_costs",
        "maximumDrawdown": _finite(maximum_drawdown, 2),
        "maximumDrawdownPct": _finite(maximum_drawdown / peak_capital * 100.0, 4) if peak_capital else 0.0,
        "maximumConsecutiveLosses": _maximum_consecutive_losses(positions),
        "maximumConcurrentPositions": maximum_concurrent,
        "maxConcurrentPositions": maximum_concurrent,
        "peakCapitalDeployed": _finite(peak_capital, 2),
        "averageHoldingMinutes": _finite(float(np.mean(holding_minutes)), 2) if holding_minutes else None,
        "medianHoldingMinutes": _finite(float(np.median(holding_minutes)), 2) if holding_minutes else None,
        "averageHoldingSessions": _finite(float(np.mean(holding_sessions)), 2) if holding_sessions else None,
        "medianHoldingSessions": _finite(float(np.median(holding_sessions)), 2) if holding_sessions else None,
    }


def simulate_rsi_profit_exit_symbol(
    symbol: str,
    candles: pd.DataFrame,
    *,
    timeframe: str,
    recovery_config: RecoveryConfig,
    exit_config: RsiProfitExitConfig,
    run_id: str,
    analysis_start: datetime | None = None,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply hard-stop, RSI-profit, and trading-session exits to RSI Recovery BUY candidates."""
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
        raise TypeError("RSI profit-exit backtest requires a DatetimeIndex")
    data.index = data.index.tz_localize(IST) if data.index.tz is None else data.index.tz_convert(IST)
    data = data.sort_index()
    indicators = calculate_recovery_indicators(data, recovery_config)
    rsi_values = indicators["RecoveryRSI"].to_numpy(dtype=float, copy=False)
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
    pending_entries: dict[int, list[dict[str, Any]]] = {}
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
        peak_capital = max(
            peak_capital,
            sum(float(position["capitalDeployed"]) for position in active),
        )

    def close_position(
        position: dict[str, Any],
        index: int,
        status: RsiProfitExitStatus,
        price: float,
        held: int,
        exit_rsi: float | None,
    ) -> None:
        nonlocal realized_net
        completed = _finish_position(
            position,
            candles=data,
            end_index=index,
            status=status,
            exit_price=price,
            exit_rsi=exit_rsi,
            holding_sessions=held,
            timeframe=timeframe,
            recovery_config=recovery_config,
            exit_config=exit_config,
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
                close_position(position, index, "STOP_GAP", open_price, held, None)
                continue
            pending_reason = position["pendingRsiExitReason"]
            if pending_reason is not None:
                if open_price >= float(position["minimumProfitableExitPrice"]):
                    _update_excursion(position, open_price, open_price)
                    close_position(
                        position,
                        index,
                        pending_reason,
                        open_price,
                        held,
                        position["pendingRsiExitRsi"],
                    )
                    continue
                position["pendingRsiExitReason"] = None
                position["pendingRsiExitRsi"] = None
                position["pendingRsiExitSignalIndex"] = None
            if first_bar and session_ordinal >= int(position["entrySessionOrdinal"]) + exit_config.max_holding_sessions:
                _update_excursion(position, open_price, open_price)
                close_position(
                    position,
                    index,
                    "TIME_EXIT",
                    open_price,
                    exit_config.max_holding_sessions,
                    None,
                )
            else:
                after_open.append(position)
        active = after_open

        for candidate in pending_entries.pop(index, []):
            active.append(_new_position(
                candidate,
                symbol=symbol,
                run_id=run_id,
                entry_session_ordinal=session_ordinal,
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
            if low <= float(position["stopLossPrice"]):
                close_position(
                    position,
                    index,
                    "STOP_EXIT",
                    float(position["stopLossPrice"]),
                    held,
                    None,
                )
            else:
                after_intrabar.append(position)
        active = after_intrabar

        after_rsi: list[dict[str, Any]] = []
        current_rsi = float(rsi_values[index])
        close_price = float(close_values[index])
        for position in active:
            if index <= int(position["entryIndex"]):
                after_rsi.append(position)
                continue
            profit_available = close_price >= float(position["minimumProfitableExitPrice"])
            if not (
                math.isfinite(current_rsi)
                and current_rsi >= exit_config.profit_exit_rsi
                and profit_available
            ):
                after_rsi.append(position)
                continue
            reason: RsiProfitExitStatus = (
                "RSI_OVERBOUGHT_PROFIT_EXIT"
                if current_rsi >= exit_config.upper_rsi_level
                else "RSI_PROFIT_EXIT"
            )
            held = session_ordinal - int(position["entrySessionOrdinal"]) + 1
            if exit_config.exit_execution_model == "SIGNAL_CLOSE":
                close_position(position, index, reason, close_price, held, current_rsi)
            else:
                position["pendingRsiExitReason"] = reason
                position["pendingRsiExitRsi"] = current_rsi
                position["pendingRsiExitSignalIndex"] = index
                after_rsi.append(position)
        active = after_rsi

        for candidate in signal_candidates.get(index, []):
            if len(active) >= exit_config.max_open_lots_per_symbol:
                skipped.append(_skipped_signal(candidate, recovery_config.execution_model))
                continue
            if recovery_config.execution_model == "NEXT_BAR_OPEN":
                pending_entries.setdefault(int(candidate["entryBarIndex"]), []).append(candidate)
            else:
                active.append(_new_position(
                    candidate,
                    symbol=symbol,
                    run_id=run_id,
                    entry_session_ordinal=session_ordinal,
                    exit_config=exit_config,
                ))
        capture_capacity()

        marked_pnl = realized_net
        for position in active:
            quantity = int(position["quantity"])
            gross = (close_price - float(position["entryPrice"])) * quantity
            buy_cost, buy_slippage = _entry_cost(float(position["entryPrice"]), quantity, recovery_config)
            sell_cost, sell_slippage = _exit_cost(close_price, quantity, recovery_config)
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
            exit_rsi=None,
            holding_sessions=final_session_ordinal - int(position["entrySessionOrdinal"]) + 1,
            timeframe=timeframe,
            recovery_config=recovery_config,
            exit_config=exit_config,
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


def aggregate_rsi_profit_exit_results(results: list[dict[str, Any]]) -> dict[str, Any]:
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
