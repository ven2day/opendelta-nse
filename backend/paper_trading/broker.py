"""PaperBroker: turns stored signals into simulated lots and manages them against candles.

Guarantees:
- a signal can open at most one paper order per account (database unique index),
- every lot has its own entry, quantity, target, stop and expiry and closes on its own,
- fees and slippage are applied on both sides through the market's fee model,
- portfolio state is rebuilt from the database on start,
- no real order endpoint is ever called.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

import pandas as pd

from backend.core.fifo import FifoInventory
from backend.data.repositories import PaperAccountRepository, PaperLotRepository, PaperOrderRepository, PaperPendingEntryRepository, PaperTradeRepository
from backend.markets.base import MarketSpec
from backend.paper_trading.accounting import Accounting
from backend.paper_trading.execution import ExecutionPolicy
from backend.paper_trading.portfolio import Portfolio
from backend.strategies.lot_policy import PriceBandLadder

logger = logging.getLogger("opendelta.paper")

NO_REAL_ORDERS = True
DEFAULT_STARTING_BALANCES = {"NSE": 1_000_000.0, "CRYPTO": 100_000.0}


@dataclass
class PaperRepositories:
    accounts: PaperAccountRepository
    orders: PaperOrderRepository
    lots: PaperLotRepository
    trades: PaperTradeRepository
    pending: PaperPendingEntryRepository


class PaperBroker:
    def __init__(
        self,
        *,
        market: MarketSpec,
        repositories: PaperRepositories,
        policy: ExecutionPolicy,
        timeframe: str,
        clock: Callable[[], datetime],
        starting_balance: float | None = None,
    ) -> None:
        self.market = market
        self.repositories = repositories
        self.policy = policy.validate()
        self.timeframe = timeframe
        self.bar_minutes = market.minutes(timeframe)
        self.clock = clock
        self._lock = threading.RLock()
        self.account = repositories.accounts.get_or_create(
            market.market,
            currency=market.currency,
            starting_balance=starting_balance if starting_balance is not None else DEFAULT_STARTING_BALANCES[market.market],
            risk_settings=self.policy.public(),
        )
        self._inventories = self._load_fifo_inventories()
        self.portfolio = self._rebuild_portfolio()
        self.rejected = 0
        self.filled = 0

    # ---- lifecycle -----------------------------------------------------------------

    def rebuild(self) -> Portfolio:
        with self._lock:
            account = self.repositories.accounts.get(self.market.market)
            if account is None:
                raise RuntimeError(f"Paper account for {self.market.market} disappeared")
            self.account = account
            self._inventories = self._load_fifo_inventories()
            self.portfolio = self._rebuild_portfolio()
            return self.portfolio

    def _rebuild_portfolio(self) -> Portfolio:
        account_id = self.account["accountId"]
        return Portfolio.rebuild(
            self.account,
            self.repositories.lots.open(account_id),
            self.repositories.pending.list(account_id),
        )

    def reset(self, *, starting_balance: float | None = None) -> dict[str, Any]:
        with self._lock:
            self.repositories.pending.clear(self.account["accountId"])
            self.account = self.repositories.accounts.reset(self.market.market, starting_balance=starting_balance, risk_settings=self.policy.public())
            self._inventories = {}
            self.portfolio = Portfolio.rebuild(self.account, [])
            return self.account

    # ---- signals in ------------------------------------------------------------------

    def on_signal(self, signal: Mapping[str, Any]) -> dict[str, Any] | None:
        """Consume a stored signal: fill now (SIGNAL_CLOSE) or queue for the next candle open."""
        if signal.get("market") != self.market.market or signal.get("signalType") != "BUY":
            return None
        with self._lock:
            open_lots = self.portfolio.lots_for(signal["symbol"])
            if open_lots and PriceBandLadder.from_config(open_lots[0].get("configurationSnapshot")) is not None:
                # The initial RSI signal starts the cycle. Further entries are
                # driven only by completed-candle dip thresholds below.
                return None
            if self.policy.price_model == "NEXT_OPEN":
                self._persist_pending_entry(dict(signal))
                return None
            return self._fill_entry(signal, float(signal["signalPrice"]), pd.Timestamp(signal["candleTimestamp"]).to_pydatetime())

    def _fill_entry(self, signal: Mapping[str, Any], reference_price: float, entry_time: datetime) -> dict[str, Any] | None:
        account_id = self.account["accountId"]
        symbol = signal["symbol"]
        open_lots = self.portfolio.lots_for(symbol)
        snapshot = signal.get("configurationSnapshot") or {}
        ladder = PriceBandLadder.from_config(snapshot)
        cycles, _ = self.repositories.lots.cycle_state(account_id, symbol)
        if open_lots and not self.policy.allow_additional_buys:
            return self._reject(signal, reference_price, "ADDITIONAL_BUYS_DISABLED")
        if open_lots:
            cycle_id = open_lots[0]["cycleId"]
            cycle_lots = self.repositories.lots.cycle(account_id, symbol, cycle_id)
            entry_number = max(int(lot["lotNumber"]) for lot in cycle_lots)
            cycle_number = int(cycle_id.rsplit("Cycle", 1)[1])
        else:
            cycle_lots = []
            entry_number = 0
            cycle_number = cycles + 1
        maximum_entries = min(self.policy.maximum_entries_per_cycle, ladder.maximum_entries) if ladder else self.policy.maximum_entries_per_cycle
        if entry_number >= maximum_entries:
            return self._reject(signal, reference_price, "MAXIMUM_ENTRIES_PER_CYCLE")
        if ladder is not None and entry_number > 0:
            last_entry = max(cycle_lots, key=lambda lot: int(lot["lotNumber"]))
            if not ladder.additional_entry_allowed(float(signal["signalPrice"]), float(last_entry["entryPrice"])):
                return self._reject(signal, reference_price, "DIP_THRESHOLD_NOT_REACHED")
        indicative_fill_price = self.market.fees.buy(reference_price, 1).price
        first_entry_price = float(min(cycle_lots, key=lambda lot: int(lot["lotNumber"]))["entryPrice"]) if cycle_lots else indicative_fill_price
        quantity = ladder.quantity(entry_number, first_entry_price) if ladder else self.policy.lot_quantity(entry_number, reference_price)
        fill = self.policy.buy(self.market.fees, reference_price, quantity)
        if ladder is not None:
            current_open_capital = self._fifo_inventory(symbol).cost if self.market.market == "NSE" else sum(float(lot["entryPrice"]) * float(lot["quantity"]) for lot in open_lots)
            if not ladder.within_capital(current_open_capital, fill.price, quantity):
                return self._reject(signal, reference_price, "MAXIMUM_POSITION_CAPITAL")
        cost = Accounting.entry_cost(fill.price, quantity, fill.fees)
        if cost > float(self.account["cashBalance"]):
            return self._reject(signal, reference_price, "INSUFFICIENT_FUNDS")
        order = self.repositories.orders.insert(
            account_id=account_id, market=self.market.market, signal_id=signal.get("signalId"),
            strategy_id=signal["strategyId"], strategy_version=signal["strategyVersion"], symbol=symbol, side="BUY",
            quantity=quantity, requested_price=reference_price, executed_price=round(fill.price, 4), fees=round(fill.fees, 4), slippage=round(fill.slippage, 4), status="FILLED",
        )
        if order is None:  # the unique index says this signal already opened an order
            self.rejected += 1
            return None
        target_pct = float(snapshot.get("target_pct") or ((float(signal["targetPrice"]) / float(signal["signalPrice"]) - 1) * 100 if signal.get("targetPrice") else 1.0))
        target, stop, expires = self.policy.targets(round(fill.price, 4), target_pct, entry_time, self.bar_minutes)
        if signal.get("stopPrice") is not None and stop is None:
            stop = float(signal["stopPrice"])
        lot = self.repositories.lots.insert(
            account_id=account_id, order_id=order["orderId"], signal_id=signal.get("signalId"), market=self.market.market,
            strategy_id=signal["strategyId"], strategy_version=signal["strategyVersion"], symbol=symbol, timeframe=self.timeframe,
            cycle_id=f"{symbol}-Cycle{cycle_number}", lot_number=entry_number + 1, entry_timestamp=entry_time, entry_price=round(fill.price, 4),
            quantity=quantity, target_price=target, stop_price=stop, expires_at=expires, fees=round(fill.fees, 4),
            unrealized_pnl=Accounting.unrealized_pnl(round(fill.price, 4), round(fill.price, 4), quantity, fill.fees), configuration_snapshot=snapshot,
        )
        self.repositories.trades.insert(account_id=account_id, lot_id=lot["lotId"], market=self.market.market, symbol=symbol, side="BUY", quantity=quantity, price=round(fill.price, 4), fees=round(fill.fees, 4), slippage=round(fill.slippage, 4), reason=signal.get("entryReason", "SIGNAL_ENTRY"), executed_at=entry_time)
        if self.market.market == "NSE":
            self._fifo_inventory(symbol).add(lot["lotId"], entry_time, round(fill.price, 4), quantity, round(fill.fees, 4))
        self.account["cashBalance"] = self.repositories.accounts.adjust_cash(account_id, -cost)
        self.portfolio.add_lot(lot)
        self.filled += 1
        return lot

    def _reject(self, signal: Mapping[str, Any], price: float, reason: str) -> None:
        self.repositories.orders.insert(
            account_id=self.account["accountId"], market=self.market.market, signal_id=signal.get("signalId"),
            strategy_id=signal["strategyId"], strategy_version=signal["strategyVersion"], symbol=signal["symbol"], side="BUY",
            quantity=0, requested_price=price, executed_price=None, fees=0.0, slippage=0.0, status="REJECTED", reason=reason,
        )
        self.rejected += 1
        return None

    # ---- candles in --------------------------------------------------------------------

    def on_completed_candle(self, symbol: str, candle: Mapping[str, Any] | pd.Series, timestamp: datetime | None = None) -> list[dict[str, Any]]:
        """Fill queued entries at this candle's open, then update and possibly close open lots."""
        stamp = pd.Timestamp(timestamp if timestamp is not None else candle["timestamp"]).to_pydatetime()
        open_, high, low, close = (float(candle[key]) for key in ("Open", "High", "Low", "Close"))
        closed: list[dict[str, Any]] = []
        with self._lock:
            deferred: list[dict[str, Any]] = []
            for signal in self.portfolio.pending_entries.pop(symbol, []):
                if pd.Timestamp(signal["candleTimestamp"]) < pd.Timestamp(stamp):
                    self._fill_entry(signal, open_, stamp)
                    if signal.get("pendingEntryId"):
                        self.repositories.pending.delete(signal["pendingEntryId"])
                else:
                    deferred.append(signal)
            if deferred:
                self.portfolio.pending_entries[symbol] = deferred
            survivors: dict[str, tuple[float, float]] = {}
            for lot in self.portfolio.lots_for(symbol):
                if pd.Timestamp(lot["entryTimestamp"]) >= pd.Timestamp(stamp):
                    continue
                entry_price = float(lot["entryPrice"])
                mae, mfe = Accounting.excursions(entry_price, low, high, lot.get("maePct"), lot.get("mfePct"))
                stop = lot.get("stopPrice")
                expires = lot.get("expiresAt")
                if stop is not None and low <= float(stop):
                    closed.append(self._close_lot(lot, "STOPPED", float(stop), stamp, mae, mfe))
                elif high >= float(lot["targetPrice"]):
                    closed.append(self._close_lot(lot, "TARGET_HIT", float(lot["targetPrice"]), stamp, mae, mfe))
                elif expires is not None and pd.Timestamp(stamp) >= pd.Timestamp(expires):
                    closed.append(self._close_lot(lot, "EXPIRED", close, stamp, mae, mfe))
                else:
                    survivors[lot["lotId"]] = (mae, mfe)
            self._mark_open_lots(symbol, close, survivors)
            self._queue_ladder_entry(symbol, close, stamp)
        return closed

    def _queue_ladder_entry(self, symbol: str, close: float, stamp: datetime) -> None:
        open_lots = self.portfolio.lots_for(symbol)
        if not open_lots or self.portfolio.pending_entries.get(symbol) or not self.policy.allow_additional_buys:
            return
        cycle_id = open_lots[0]["cycleId"]
        cycle_lots = self.repositories.lots.cycle(self.account["accountId"], symbol, cycle_id)
        if not cycle_lots:
            return
        first = min(cycle_lots, key=lambda lot: int(lot["lotNumber"]))
        latest = max(cycle_lots, key=lambda lot: int(lot["lotNumber"]))
        snapshot = first.get("configurationSnapshot") or {}
        ladder = PriceBandLadder.from_config(snapshot)
        if ladder is None:
            return
        maximum_entries = min(self.policy.maximum_entries_per_cycle, ladder.maximum_entries)
        if int(latest["lotNumber"]) >= maximum_entries or not ladder.additional_entry_allowed(close, float(latest["entryPrice"])):
            return
        target_pct = float(snapshot["target_pct"])
        signal = {
            "signalId": None,
            "market": self.market.market,
            "strategyId": first["strategyId"],
            "strategyVersion": first["strategyVersion"],
            "symbol": symbol,
            "timeframe": self.timeframe,
            "candleTimestamp": stamp.isoformat(),
            "signalType": "BUY",
            "signalPrice": close,
            "targetPrice": round(close * (1 + target_pct / 100), 4),
            "stopPrice": None,
            "configurationSnapshot": snapshot,
            "entryReason": "LADDER_DIP_ENTRY",
        }
        self._persist_pending_entry(signal, cycle_id=cycle_id, lot_number=int(latest["lotNumber"]) + 1)

    def _persist_pending_entry(self, signal: dict[str, Any], *, cycle_id: str | None = None, lot_number: int | None = None) -> None:
        stored = self.repositories.pending.insert(
            signal,
            account_id=self.account["accountId"],
            cycle_id=cycle_id,
            lot_number=lot_number,
        )
        if stored is not None:
            self.portfolio.pending_entries.setdefault(signal["symbol"], []).append(stored)

    def close_lot_manually(self, lot_id: str, *, price: float, timestamp: datetime | None = None) -> dict[str, Any]:
        with self._lock:
            lot = next((item for item in self.portfolio.all_open() if item["lotId"] == lot_id), None)
            if lot is None:
                raise KeyError(f"Open paper lot {lot_id} was not found")
            stamp = timestamp or self.clock()
            mae, mfe = Accounting.excursions(float(lot["entryPrice"]), price, price, lot.get("maePct"), lot.get("mfePct"))
            closed = self._close_lot(lot, "CLOSED", price, stamp, mae, mfe)
            self._mark_open_lots(lot["symbol"], price, {})
            return closed

    def _close_lot(self, lot: Mapping[str, Any], status: str, raw_price: float, stamp: datetime, mae: float, mfe: float) -> dict[str, Any]:
        quantity = float(lot["quantity"])
        fill = self.policy.sell(self.market.fees, raw_price, quantity)
        if self.market.market == "NSE":
            matched = self._fifo_inventory(lot["symbol"]).preview_allocations([quantity])[0]
            cost_basis_price = round(matched.cost_basis_price, 4)
            entry_fees = matched.entry_fees
            fifo_allocations = _public_fifo_allocations(matched.allocations)
        else:
            cost_basis_price = float(lot["entryPrice"])
            entry_fees = float(lot.get("fees") or 0.0)
            fifo_allocations = [{"lotId": lot["lotId"], "quantity": quantity, "entryPrice": cost_basis_price, "fees": entry_fees}]
        total_fees = round(entry_fees + fill.fees, 4)
        realized = Accounting.realized_pnl(cost_basis_price, round(fill.price, 4), quantity, total_fees)
        self.repositories.lots.mark(lot["lotId"], last_price=raw_price, cost_basis_price=cost_basis_price, fifo_allocations=fifo_allocations, entry_fees=entry_fees, unrealized_pnl=0.0, mae_pct=mae, mfe_pct=mfe)
        updated = self.repositories.lots.close(lot["lotId"], status=status, exit_timestamp=stamp, exit_price=round(fill.price, 4), cost_basis_price=cost_basis_price, fifo_allocations=fifo_allocations, realized_pnl=realized, fees=total_fees)
        self.repositories.trades.insert(account_id=self.account["accountId"], lot_id=lot["lotId"], market=self.market.market, symbol=lot["symbol"], side="SELL", quantity=quantity, price=round(fill.price, 4), fees=round(fill.fees, 4), slippage=round(fill.slippage, 4), reason=status, executed_at=stamp)
        if self.market.market == "NSE":
            self._fifo_inventory(lot["symbol"]).consume(quantity)
        self.account["cashBalance"] = self.repositories.accounts.adjust_cash(self.account["accountId"], Accounting.exit_proceeds(fill.price, quantity, fill.fees))
        self.portfolio.remove_lot(lot["lotId"], lot["symbol"])
        return updated

    def _fifo_inventory(self, symbol: str) -> FifoInventory:
        return self._inventories.setdefault(symbol, FifoInventory())

    def _load_fifo_inventories(self) -> dict[str, FifoInventory]:
        inventories: dict[str, FifoInventory] = {}
        if self.market.market != "NSE":
            return inventories
        for trade in self.repositories.trades.chronological(self.account["accountId"]):
            inventory = inventories.setdefault(trade["symbol"], FifoInventory())
            if trade["side"] == "BUY":
                inventory.add(trade["lotId"], pd.Timestamp(trade["executedAt"]).to_pydatetime(), float(trade["price"]), float(trade["quantity"]), float(trade.get("fees") or 0.0))
            else:
                inventory.consume(float(trade["quantity"]))
        return inventories

    def _mark_open_lots(self, symbol: str, close: float, excursions: Mapping[str, tuple[float, float]]) -> None:
        lots = sorted(self.portfolio.lots_for(symbol), key=lambda item: (item["entryTimestamp"], item["lotNumber"]))
        if not lots:
            return
        matches = self._fifo_inventory(symbol).preview_allocations([float(lot["quantity"]) for lot in lots]) if self.market.market == "NSE" else [None] * len(lots)
        for lot, matched in zip(lots, matches):
            cost_basis_price = round(matched.cost_basis_price, 4) if matched is not None else float(lot["entryPrice"])
            entry_fees = matched.entry_fees if matched is not None else float(lot.get("fees") or 0.0)
            fifo_allocations = _public_fifo_allocations(matched.allocations) if matched is not None else [{"lotId": lot["lotId"], "quantity": float(lot["quantity"]), "entryPrice": cost_basis_price, "fees": entry_fees}]
            mae, mfe = excursions.get(lot["lotId"], (float(lot.get("maePct") or 0.0), float(lot.get("mfePct") or 0.0)))
            unrealized = Accounting.unrealized_pnl(cost_basis_price, close, float(lot["quantity"]), entry_fees)
            self.repositories.lots.mark(lot["lotId"], last_price=close, cost_basis_price=cost_basis_price, fifo_allocations=fifo_allocations, entry_fees=entry_fees, unrealized_pnl=unrealized, mae_pct=mae, mfe_pct=mfe)
            self.portfolio.replace_lot({**lot, "lastPrice": close, "costBasisPrice": cost_basis_price, "fifoAllocations": fifo_allocations, "unrealizedPnl": unrealized, "maePct": mae, "mfePct": mfe})

    # ---- views ----------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        with self._lock:
            account = self.repositories.accounts.get(self.market.market) or self.account
            open_lots = self.portfolio.all_open()
            closed = [lot for lot in self.repositories.lots.list(account["accountId"], limit=5_000) if lot["status"] != "OPEN"]
            return {**Accounting.summary(account, open_lots, closed, timezone=self.market.timezone, now=self.clock()), "executionPolicy": self.policy.public(), "filled": self.filled, "rejected": self.rejected}

    def positions(self) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(self.portfolio.all_open(), key=lambda lot: (lot["symbol"], lot["entryTimestamp"], lot["lotNumber"]))


def _public_fifo_allocations(allocations) -> list[dict[str, Any]]:
    return [
        {"lotId": item.acquisition_id, "quantity": item.quantity, "entryPrice": item.price, "fees": round(item.fees, 4)}
        for item in allocations
    ]
