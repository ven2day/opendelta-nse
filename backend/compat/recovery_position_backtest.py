from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd

from backend.collector import IST
from backend.compat.recovery_backtest import RecoveryConfig, simulate_recovery_symbol

POSITION_BACKTEST_VERSION = "recovery-exit-protection-1.0.0"


@dataclass(frozen=True)
class PositionProtectionConfig:
    enabled: bool = False
    quantity_per_trade: int = 50
    max_open_lots_per_symbol: int = 1
    max_holding_sessions: int = 5
    time_exit: Literal["NEXT_TRADING_SESSION_OPEN"] = "NEXT_TRADING_SESSION_OPEN"

    def public_parameters(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "quantityPerTrade": self.quantity_per_trade,
            "maxOpenLotsPerSymbol": self.max_open_lots_per_symbol,
            "maxHoldingTradingDays": self.max_holding_sessions,
            "timeExit": self.time_exit,
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


def _entry_cost(
    entry_price: float, quantity: int, config: RecoveryConfig
) -> tuple[float, float]:
    turnover = entry_price * quantity
    return (
        turnover * config.buy_cost_bps / 10_000.0,
        turnover * config.slippage_bps / 10_000.0,
    )


def _exit_cost(
    exit_price: float, quantity: int, config: RecoveryConfig
) -> tuple[float, float]:
    turnover = exit_price * quantity
    return (
        turnover * config.sell_cost_bps / 10_000.0,
        turnover * config.slippage_bps / 10_000.0,
    )


def _net_mark_to_market(
    position: dict[str, Any], price: float, config: RecoveryConfig
) -> float:
    quantity = int(position["quantity"])
    gross = (price - float(position["entryPrice"])) * quantity
    buy_cost, buy_slippage = _entry_cost(
        float(position["entryPrice"]), quantity, config
    )
    sell_cost, sell_slippage = _exit_cost(price, quantity, config)
    return gross - buy_cost - buy_slippage - sell_cost - sell_slippage


def _update_excursion(position: dict[str, Any], low: float, high: float) -> None:
    position["lowestPrice"] = (
        low if position["lowestPrice"] is None else min(position["lowestPrice"], low)
    )
    position["highestPrice"] = (
        high
        if position["highestPrice"] is None
        else max(position["highestPrice"], high)
    )


def _new_position(
    candidate: dict[str, Any],
    *,
    quantity: int,
    entry_session_ordinal: int,
) -> dict[str, Any]:
    entry_price = float(candidate["entryPrice"])
    return {
        "tradeId": candidate["tradeId"],
        "sequenceNumber": candidate["sequenceNumber"],
        "candidate": candidate,
        "entryIndex": int(candidate["entryBarIndex"]),
        "entryPrice": entry_price,
        "targetPrice": float(candidate["targetPrice"]),
        "quantity": quantity,
        "capitalDeployed": entry_price * quantity,
        "entrySessionOrdinal": entry_session_ordinal,
        "lowestPrice": None,
        "highestPrice": None,
    }


def _skipped_signal(candidate: dict[str, Any], execution_model: str) -> dict[str, Any]:
    entry_known_at_signal = execution_model == "SIGNAL_CLOSE"
    return {
        "tradeId": candidate["tradeId"],
        "sequenceNumber": candidate["sequenceNumber"],
        "signalTimestamp": candidate["signalTimestamp"],
        "entryTimestamp": candidate["entryTimestamp"]
        if entry_known_at_signal
        else None,
        "entryPrice": candidate["entryPrice"] if entry_known_at_signal else None,
        "quantity": 0,
        "capitalDeployed": 0.0,
        "targetPrice": candidate["targetPrice"] if entry_known_at_signal else None,
        "oiRegimeAtSignal": candidate.get("oiRegimeAtSignal"),
        "oiScoreAtSignal": candidate.get("oiScoreAtSignal"),
        "oiConfidence": candidate.get("oiConfidence"),
        "oiDecision": candidate.get("oiDecision"),
        "oiSourceTimestamp": candidate.get("oiSourceTimestamp"),
        "status": "SKIPPED_MAX_OPEN_LOTS",
        "exitReason": "SKIPPED_MAX_OPEN_LOTS",
        "reason": "A valid RSI Recovery signal occurred while the configured maximum open lots was already reached.",
    }


def _finish_position(
    position: dict[str, Any],
    *,
    candles: pd.DataFrame,
    end_index: int,
    exit_price: float | None,
    status: Literal["TARGET_EXIT", "TIME_EXIT", "OPEN"],
    exit_fill: str | None,
    holding_sessions: int,
    config: RecoveryConfig,
) -> dict[str, Any]:
    candidate = position["candidate"]
    entry_price = float(position["entryPrice"])
    quantity = int(position["quantity"])
    last_close = float(candles.iloc[end_index]["Close"])
    mark_or_exit = float(exit_price if exit_price is not None else last_close)
    lowest = float(
        position["lowestPrice"] if position["lowestPrice"] is not None else entry_price
    )
    highest = float(
        position["highestPrice"]
        if position["highestPrice"] is not None
        else entry_price
    )
    gross_pnl = (mark_or_exit - entry_price) * quantity
    buy_cost, buy_slippage = _entry_cost(entry_price, quantity, config)
    sell_cost, sell_slippage = _exit_cost(mark_or_exit, quantity, config)
    total_costs = buy_cost + buy_slippage + sell_cost + sell_slippage
    net_pnl = gross_pnl - total_costs
    entry_stamp = _timestamp(candidate["entryTimestamp"])
    end_stamp = _timestamp(candles.index[end_index])
    duration_minutes = max((end_stamp - entry_stamp).total_seconds() / 60.0, 0.0)
    closed = status != "OPEN"
    return {
        "tradeId": position["tradeId"],
        "sequenceNumber": position["sequenceNumber"],
        "signalTimestamp": candidate["signalTimestamp"],
        "entryTimestamp": candidate["entryTimestamp"],
        "entryBarIndex": int(position["entryIndex"]),
        "entryPrice": _finite(entry_price, 4),
        "quantity": quantity,
        "capitalDeployed": _finite(position["capitalDeployed"], 2),
        "targetPrice": _finite(position["targetPrice"], 4),
        "exitTimestamp": _iso(candles.index[end_index]) if closed else None,
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
        "lowestPriceAfterEntry": _finite(lowest, 4),
        "maxAdversePct": _finite((lowest / entry_price - 1.0) * 100.0, 6),
        "highestPriceAfterEntry": _finite(highest, 4),
        "maxFavorablePct": _finite((highest / entry_price - 1.0) * 100.0, 6),
        "grossPnl": _finite(gross_pnl, 2),
        "buyCost": _finite(buy_cost, 2),
        "sellCost": _finite(sell_cost if closed else 0.0, 2),
        "slippageCost": _finite(buy_slippage + (sell_slippage if closed else 0.0), 2),
        "estimatedOpenExitCost": _finite(
            (sell_cost + sell_slippage) if not closed else 0.0, 2
        ),
        "totalCosts": _finite(total_costs, 2),
        "realizedPnl": _finite(net_pnl, 2) if closed else None,
        "unrealizedPnl": _finite(net_pnl, 2) if not closed else 0.0,
        "lastTimestamp": _iso(candles.index[end_index]),
        "lastClose": _finite(last_close, 4),
        "confirmationScore": candidate["confirmationScore"],
        "requiredConfirmations": candidate["requiredConfirmations"],
        "rsiAtEntry": candidate["rsiAtEntry"],
        "executionModel": candidate["executionModel"],
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
    target_exits = [
        position for position in positions if position["status"] == "TARGET_EXIT"
    ]
    time_exits = [
        position for position in positions if position["status"] == "TIME_EXIT"
    ]
    open_positions = [
        position for position in positions if position["status"] == "OPEN"
    ]
    realized = [float(position["realizedPnl"]) for position in closed]
    gross = [float(position["grossPnl"]) for position in closed]
    profits = [value for value in realized if value > 0]
    losses = [value for value in realized if value < 0]
    unrealized = sum(float(position["unrealizedPnl"]) for position in open_positions)
    holding_minutes = [float(position["durationMinutes"]) for position in positions]
    holding_sessions = [float(position["holdingSessions"]) for position in positions]
    realized_total = sum(realized)
    return {
        "totalValidBuySignals": total_valid_signals,
        "totalBuySignals": total_valid_signals,
        "buySignals": total_valid_signals,
        "executedTrades": len(positions),
        "skippedMaxOpenLots": len(skipped),
        "targetExits": len(target_exits),
        "targetsHit": len(target_exits),
        "timeExits": len(time_exits),
        "openPositions": len(open_positions),
        "openSignals": len(open_positions),
        "targetHitRate": _finite(len(target_exits) / len(positions) * 100.0, 2)
        if positions
        else 0.0,
        "profitableClosedTrades": len(profits),
        "losingClosedTrades": len(losses),
        "realizedGrossProfit": _finite(sum(value for value in gross if value > 0), 2),
        "realizedGrossLoss": _finite(
            abs(sum(value for value in gross if value < 0)), 2
        ),
        "netRealizedPnl": _finite(realized_total, 2),
        "unrealizedPnl": _finite(unrealized, 2),
        "combinedPnl": _finite(realized_total + unrealized, 2),
        "averageProfitPerTrade": _finite(float(np.mean(profits)), 2)
        if profits
        else None,
        "averageLossPerTrade": _finite(float(np.mean(losses)), 2) if losses else None,
        "profitFactor": _finite(sum(profits) / abs(sum(losses)), 4) if losses else None,
        "maximumDrawdown": _finite(maximum_drawdown, 2),
        "maximumDrawdownPct": _finite(maximum_drawdown / peak_capital * 100.0, 4)
        if peak_capital
        else 0.0,
        "maximumConcurrentPositions": maximum_concurrent,
        "maxConcurrentPositions": maximum_concurrent,
        "peakCapitalDeployed": _finite(peak_capital, 2),
        "averageHoldingMinutes": _finite(float(np.mean(holding_minutes)), 2)
        if holding_minutes
        else None,
        "medianHoldingMinutes": _finite(float(np.median(holding_minutes)), 2)
        if holding_minutes
        else None,
        "averageHoldingSessions": _finite(float(np.mean(holding_sessions)), 2)
        if holding_sessions
        else None,
        "medianHoldingSessions": _finite(float(np.median(holding_sessions)), 2)
        if holding_sessions
        else None,
    }


def simulate_protected_recovery_symbol(
    symbol: str,
    candles: pd.DataFrame,
    *,
    timeframe: str,
    recovery_config: RecoveryConfig,
    protection_config: PositionProtectionConfig,
    run_id: str,
    analysis_start: datetime | None = None,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply position exits to the unchanged RSI Recovery observation stream."""
    observations = observations or simulate_recovery_symbol(
        symbol,
        candles,
        timeframe=timeframe,
        config=recovery_config,
        run_id=run_id,
        analysis_start=analysis_start,
    )
    candidates = sorted(
        observations["trades"], key=lambda item: int(item["sequenceNumber"])
    )
    if not protection_config.enabled:
        return observations

    data = candles.copy()
    if not isinstance(data.index, pd.DatetimeIndex):
        raise TypeError("Protected position backtest requires a DatetimeIndex")
    if data.index.tz is None:
        data.index = data.index.tz_localize(IST)
    else:
        data.index = data.index.tz_convert(IST)
    data = data.sort_index()
    start_position = (
        int(data.index.searchsorted(_timestamp(analysis_start), side="left"))
        if analysis_start
        else 0
    )
    open_values = data["Open"].to_numpy(dtype=float, copy=False)
    high_values = data["High"].to_numpy(dtype=float, copy=False)
    low_values = data["Low"].to_numpy(dtype=float, copy=False)
    close_values = data["Close"].to_numpy(dtype=float, copy=False)
    session_dates = list(data.index.date)
    ordered_sessions = list(dict.fromkeys(session_dates))
    session_ordinals = {
        session: index for index, session in enumerate(ordered_sessions)
    }
    first_index_by_session: dict[Any, int] = {}
    for index, session in enumerate(session_dates):
        first_index_by_session.setdefault(session, index)

    signal_candidates: dict[int, list[dict[str, Any]]] = {}
    for candidate in candidates:
        entry_index = int(candidate["entryBarIndex"])
        signal_index = (
            entry_index
            if recovery_config.execution_model == "SIGNAL_CLOSE"
            else entry_index - 1
        )
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
            peak_capital, sum(float(position["capitalDeployed"]) for position in active)
        )

    def close_position(
        position: dict[str, Any],
        index: int,
        status: Literal["TARGET_EXIT", "TIME_EXIT"],
        exit_price: float,
        exit_fill: str,
        holding_sessions: int,
    ) -> None:
        nonlocal realized_net
        completed = _finish_position(
            position,
            candles=data,
            end_index=index,
            exit_price=exit_price,
            status=status,
            exit_fill=exit_fill,
            holding_sessions=holding_sessions,
            config=recovery_config,
        )
        positions.append(completed)
        realized_net += float(completed["realizedPnl"])

    for index in range(start_position, len(data)):
        session = session_dates[index]
        session_ordinal = session_ordinals[session]
        first_bar = first_index_by_session[session] == index

        # Opening fills occur before NEXT_BAR_OPEN entries and before any intrabar high/low.
        after_open: list[dict[str, Any]] = []
        for position in active:
            if index <= int(position["entryIndex"]):
                after_open.append(position)
                continue
            open_price = float(open_values[index])
            held_sessions = session_ordinal - int(position["entrySessionOrdinal"]) + 1
            if open_price >= float(position["targetPrice"]):
                _update_excursion(position, open_price, open_price)
                close_position(
                    position,
                    index,
                    "TARGET_EXIT",
                    open_price,
                    "GAP_OPEN",
                    held_sessions,
                )
            elif (
                first_bar
                and session_ordinal
                >= int(position["entrySessionOrdinal"])
                + protection_config.max_holding_sessions
            ):
                _update_excursion(position, open_price, open_price)
                close_position(
                    position,
                    index,
                    "TIME_EXIT",
                    open_price,
                    "NEXT_TRADING_SESSION_OPEN",
                    protection_config.max_holding_sessions,
                )
            else:
                after_open.append(position)
        active = after_open

        for candidate in pending_entries.pop(index, []):
            position = _new_position(
                candidate,
                quantity=protection_config.quantity_per_trade,
                entry_session_ordinal=session_ordinal,
            )
            active.append(position)
        capture_capacity()

        # The entry candle remains excluded. Existing positions may hit intrabar targets.
        after_intrabar: list[dict[str, Any]] = []
        for position in active:
            if index <= int(position["entryIndex"]):
                after_intrabar.append(position)
                continue
            low = float(low_values[index])
            high = float(high_values[index])
            _update_excursion(position, low, high)
            if high >= float(position["targetPrice"]):
                held_sessions = (
                    session_ordinal - int(position["entrySessionOrdinal"]) + 1
                )
                close_position(
                    position,
                    index,
                    "TARGET_EXIT",
                    float(position["targetPrice"]),
                    "TARGET_PRICE",
                    held_sessions,
                )
            else:
                after_intrabar.append(position)
        active = after_intrabar

        # Capacity is evaluated when the completed-candle signal becomes known.
        for candidate in signal_candidates.get(index, []):
            if len(active) >= protection_config.max_open_lots_per_symbol:
                skipped.append(
                    _skipped_signal(candidate, recovery_config.execution_model)
                )
                continue
            if recovery_config.execution_model == "NEXT_BAR_OPEN":
                pending_entries.setdefault(int(candidate["entryBarIndex"]), []).append(
                    candidate
                )
            else:
                active.append(
                    _new_position(
                        candidate,
                        quantity=protection_config.quantity_per_trade,
                        entry_session_ordinal=session_ordinal,
                    )
                )
        capture_capacity()

        marked_pnl = realized_net + sum(
            _net_mark_to_market(position, float(close_values[index]), recovery_config)
            for position in active
        )
        equity_peak = max(equity_peak, marked_pnl)
        maximum_drawdown = max(maximum_drawdown, equity_peak - marked_pnl)

    final_index = len(data) - 1
    final_session_ordinal = session_ordinals[session_dates[final_index]]
    for position in active:
        holding_sessions = (
            final_session_ordinal - int(position["entrySessionOrdinal"]) + 1
        )
        positions.append(
            _finish_position(
                position,
                candles=data,
                end_index=final_index,
                exit_price=None,
                status="OPEN",
                exit_fill=None,
                holding_sessions=holding_sessions,
                config=recovery_config,
            )
        )
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


def aggregate_protected_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    positions = [
        position for result in results for position in result.get("positions", [])
    ]
    skipped = [
        signal for result in results for signal in result.get("skippedSignals", [])
    ]
    exposure_events: list[tuple[pd.Timestamp, int, float]] = []
    for position in positions:
        capital = float(position["capitalDeployed"])
        exposure_events.append((_timestamp(position["entryTimestamp"]), 1, capital))
        if position["exitTimestamp"] is not None:
            exposure_events.append(
                (_timestamp(position["exitTimestamp"]), -1, -capital)
            )
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
    maximum_drawdown = sum(
        float(result.get("maximumDrawdown", 0.0) or 0.0) for result in results
    )
    summary = _summary(
        positions,
        skipped,
        total_valid_signals=sum(
            int(result.get("totalValidBuySignals", 0)) for result in results
        ),
        maximum_concurrent=maximum_concurrent,
        peak_capital=peak_capital,
        maximum_drawdown=maximum_drawdown,
    )
    summary["candleRowsProcessed"] = sum(
        int(result.get("bars", 0)) for result in results
    )
    return summary
