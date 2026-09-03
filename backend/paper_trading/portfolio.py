"""In-memory view of one paper account, always rebuilt from the database."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class Portfolio:
    account: dict[str, Any]
    open_lots: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # symbol -> lots
    pending_entries: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # symbol -> signals awaiting NEXT_OPEN

    @classmethod
    def rebuild(cls, account: Mapping[str, Any], lots: list[dict[str, Any]], pending_entries: list[dict[str, Any]] | None = None) -> "Portfolio":
        portfolio = cls(account=dict(account))
        for lot in lots:
            if lot["status"] == "OPEN":
                portfolio.open_lots.setdefault(lot["symbol"], []).append(dict(lot))
        for entry in pending_entries or []:
            portfolio.pending_entries.setdefault(entry["symbol"], []).append(dict(entry))
        return portfolio

    def lots_for(self, symbol: str, *, strategy_id: str | None = None, timeframe: str | None = None) -> list[dict[str, Any]]:
        lots = list(self.open_lots.get(symbol, []))
        if strategy_id is not None:
            lots = [lot for lot in lots if lot["strategyId"] == strategy_id]
        if timeframe is not None:
            lots = [lot for lot in lots if lot["timeframe"] == timeframe]
        return lots

    def add_lot(self, lot: Mapping[str, Any]) -> None:
        self.open_lots.setdefault(lot["symbol"], []).append(dict(lot))

    def replace_lot(self, lot: Mapping[str, Any]) -> None:
        lots = self.open_lots.get(lot["symbol"], [])
        for index, existing in enumerate(lots):
            if existing["lotId"] == lot["lotId"]:
                lots[index] = dict(lot)
                return
        lots.append(dict(lot))

    def remove_lot(self, lot_id: str, symbol: str) -> None:
        lots = [lot for lot in self.open_lots.get(symbol, []) if lot["lotId"] != lot_id]
        if lots:
            self.open_lots[symbol] = lots
        else:
            self.open_lots.pop(symbol, None)

    def all_open(self) -> list[dict[str, Any]]:
        return [lot for lots in self.open_lots.values() for lot in lots]

    def open_count(self) -> int:
        return sum(len(lots) for lots in self.open_lots.values())
