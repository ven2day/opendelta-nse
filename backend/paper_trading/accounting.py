"""Money math for paper accounts. Pure functions; no I/O."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd


class Accounting:
    @staticmethod
    def entry_cost(executed_price: float, quantity: float, fees: float) -> float:
        """Cash leaving the account on a buy."""
        return executed_price * quantity + fees

    @staticmethod
    def exit_proceeds(executed_price: float, quantity: float, fees: float) -> float:
        """Cash entering the account on a sell."""
        return executed_price * quantity - fees

    @staticmethod
    def realized_pnl(entry_price: float, exit_price: float, quantity: float, total_fees: float) -> float:
        return round((exit_price - entry_price) * quantity - total_fees, 2)

    @staticmethod
    def unrealized_pnl(entry_price: float, last_price: float, quantity: float, entry_fees: float) -> float:
        return round((last_price - entry_price) * quantity - entry_fees, 2)

    @staticmethod
    def excursions(entry_price: float, low: float, high: float, previous_mae: float | None, previous_mfe: float | None) -> tuple[float, float]:
        mae = min(previous_mae if previous_mae is not None else 0.0, round((low / entry_price - 1) * 100, 4))
        mfe = max(previous_mfe if previous_mfe is not None else 0.0, round((high / entry_price - 1) * 100, 4))
        return mae, mfe

    @staticmethod
    def summary(account: Mapping[str, Any], open_lots: Sequence[Mapping[str, Any]], closed_lots: Sequence[Mapping[str, Any]], *, timezone: str, now: datetime) -> dict[str, Any]:
        today = now.astimezone(ZoneInfo(timezone)).date()
        realized_total = round(sum(float(lot.get("realizedPnl") or 0.0) for lot in closed_lots), 2)
        realized_today = round(sum(float(lot.get("realizedPnl") or 0.0) for lot in closed_lots if _local_date(lot.get("exitTimestamp"), timezone) == today), 2)
        unrealized = round(sum(float(lot.get("unrealizedPnl") or 0.0) for lot in open_lots), 2)
        market_value = round(sum(float(lot.get("lastPrice") or lot["entryPrice"]) * float(lot["quantity"]) for lot in open_lots), 2)
        return {
            "market": account["market"],
            "currency": account["currency"],
            "startingBalance": account["startingBalance"],
            "cashBalance": round(float(account["cashBalance"]), 2),
            "marketValue": market_value,
            "equity": round(float(account["cashBalance"]) + market_value, 2),
            "openPositions": len(open_lots),
            "closedLots": len(closed_lots),
            "realizedPnl": realized_total,
            "realizedPnlToday": realized_today,
            "unrealizedPnl": unrealized,
            "dailyPnl": round(realized_today + unrealized, 2),
            "asOf": now.isoformat(),
            "paperOnly": True,
            "liveOrdersEnabled": False,
        }


def _local_date(value: Any, timezone: str) -> date | None:
    if not value:
        return None
    return pd.Timestamp(value).tz_convert(timezone).date()
