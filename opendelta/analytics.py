from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from statistics import median
from typing import Any, Iterable


def maximum_drawdown(returns: Iterable[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1 + float(value)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return worst


def consecutive(values: list[float], predicate) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if predicate(value) else 0
        best = max(best, current)
    return best


def summarize_returns(
    returns: list[float],
    *,
    costs: list[float] | None = None,
    holding_minutes: list[float] | None = None,
    minimum_trades: int = 30,
) -> dict[str, Any]:
    costs = costs or [0.0] * len(returns)
    holding_minutes = holding_minutes or [0.0] * len(returns)
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_profit = sum(max(0.0, value + cost) for value, cost in zip(returns, costs, strict=False))
    gross_loss = abs(sum(min(0.0, value + cost) for value, cost in zip(returns, costs, strict=False)))
    drawdown = maximum_drawdown(returns)
    net = sum(returns)
    expectancy = net / len(returns) if returns else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss else None
    if gross_loss:
        profit_factor_state = "DEFINED"
    elif gross_profit:
        profit_factor_state = "NO_LOSING_TRADES"
    else:
        profit_factor_state = "NO_CLOSED_TRADES"
    return {
        "status": "CONCLUSIVE" if len(returns) >= minimum_trades else "INCONCLUSIVE",
        "minimumTrades": minimum_trades,
        "tradeCount": len(returns),
        "grossProfit": gross_profit,
        "costs": sum(costs),
        "netProfit": net,
        "winRate": len(wins) / len(returns) if returns else 0.0,
        "averageWin": sum(wins) / len(wins) if wins else 0.0,
        "averageLoss": sum(losses) / len(losses) if losses else 0.0,
        "expectancy": expectancy,
        "profitFactor": profit_factor,
        "profitFactorState": profit_factor_state,
        "maximumDrawdown": drawdown,
        "returnToDrawdown": net / abs(drawdown) if drawdown else 0.0,
        "averageHoldingMinutes": sum(holding_minutes) / len(holding_minutes) if holding_minutes else 0.0,
        "medianHoldingMinutes": median(holding_minutes) if holding_minutes else 0.0,
        "consecutiveWins": consecutive(returns, lambda value: value > 0),
        "consecutiveLosses": consecutive(returns, lambda value: value < 0),
    }


def summarize_trade_ledger(
    trades: list[dict[str, Any]], *, minimum_trades: int = 30
) -> dict[str, Any]:
    """Aggregate a normalized ledger without inventing unavailable trade fields."""
    closed = [trade for trade in trades if trade.get("netReturn") is not None]
    returns = [float(trade["netReturn"]) for trade in closed]
    costs = [float(trade.get("costRate", 0.0)) for trade in closed]
    holding = [float(trade.get("holdingMinutes", 0.0)) for trade in closed]
    result = summarize_returns(
        returns, costs=costs, holding_minutes=holding, minimum_trades=minimum_trades
    )
    reasons = Counter(str(trade.get("exitReason", "OPEN")) for trade in trades)
    by_month: dict[str, list[float]] = defaultdict(list)
    by_symbol: dict[str, list[float]] = defaultdict(list)
    by_session: dict[str, list[float]] = defaultdict(list)
    dates = set()
    for trade in closed:
        timestamp = trade.get("entryTimestamp")
        if timestamp:
            parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            by_month[parsed.strftime("%Y-%m")].append(float(trade["netReturn"]))
            dates.add(parsed.date())
        by_symbol[str(trade.get("symbol", "UNKNOWN"))].append(float(trade["netReturn"]))
        by_session[str(trade.get("session", "UNAVAILABLE"))].append(float(trade["netReturn"]))

    def stability(groups: dict[str, list[float]]) -> dict[str, Any]:
        rows = {key: sum(values) for key, values in sorted(groups.items())}
        positive = sum(1 for value in rows.values() if value > 0)
        return {
            "groups": len(rows),
            "positiveGroups": positive,
            "positiveRate": positive / len(rows) if rows else 0.0,
            "returns": rows,
        }

    slippage = {
        f"{bps}bps": sum(value - bps * 2 / 10_000 for value in returns)
        for bps in (0, 5, 10, 20)
    }
    return {
        **result,
        "grossLoss": abs(sum(min(0.0, value + cost) for value, cost in zip(returns, costs, strict=False))),
        "mae": sum(float(trade.get("mae", 0.0)) for trade in closed) / len(closed) if closed else None,
        "mfe": sum(float(trade.get("mfe", 0.0)) for trade in closed) / len(closed) if closed else None,
        "tradesPerDay": len(closed) / len(dates) if dates else 0.0,
        "targetExits": sum(count for reason, count in reasons.items() if "TARGET" in reason),
        "stopExits": sum(count for reason, count in reasons.items() if "STOP" in reason),
        "timeExits": sum(count for reason, count in reasons.items() if "TIME" in reason),
        "openTrades": reasons.get("OPEN", 0),
        "monthlyStability": stability(by_month),
        "symbolStability": stability(by_symbol),
        "sessionStability": stability(by_session),
        "slippageSensitivity": slippage,
    }
