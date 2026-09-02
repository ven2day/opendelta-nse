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

from backend.data.repositories import PaperAccountRepository, PaperLotRepository, PaperOrderRepository, PaperTradeRepository
from backend.markets.base import MarketSpec
from backend.paper_trading.accounting import Accounting
from backend.paper_trading.execution import ExecutionPolicy
from backend.paper_trading.portfolio import Portfolio

logger = logging.getLogger("opendelta.paper")

NO_REAL_ORDERS = True
DEFAULT_STARTING_BALANCES = {"NSE": 1_000_000.0, "CRYPTO": 100_000.0}


@dataclass
class PaperRepositories:
    accounts: PaperAccountRepository
    orders: PaperOrderRepository
    lots: PaperLotRepository
    trades: PaperTradeRepository


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
        self.portfolio = Portfolio.rebuild(self.account, repositories.lots.open(self.account["accountId"]))
        self.rejected = 0
        self.filled = 0

    # ---- lifecycle -----------------------------------------------------------------

    def rebuild(self) -> Portfolio:
        with self._lock:
            account = self.repositories.accounts.get(self.market.market)
            if account is None:
                raise RuntimeError(f"Paper account for {self.market.market} disappeared")
            self.account = account
            self.portfolio = Portfolio.rebuild(account, self.repositories.lots.open(account["accountId"]))
            return self.portfolio

    def reset(self, *, starting_balance: float | None = None) -> dict[str, Any]:
        with self._lock:
            self.account = self.repositories.accounts.reset(self.market.market, starting_balance=starting_balance, risk_settings=self.policy.public())
            self.portfolio = Portfolio.rebuild(self.account, [])
            return self.account

    # ---- signals in ------------------------------------------------------------------

    def on_signal(self, signal: Mapping[str, Any]) -> dict[str, Any] | None:
        """Consume a stored signal: fill now (SIGNAL_CLOSE) or queue for the next candle open."""
        if signal.get("market") != self.market.market or signal.get("signalType") != "BUY":
            return None
        with self._lock:
            if self.policy.price_model == "NEXT_OPEN":
                self.portfolio.pending_entries.setdefault(signal["symbol"], []).append(dict(signal))
                return None
            return self._fill_entry(signal, float(signal["signalPrice"]), pd.Timestamp(signal["candleTimestamp"]).to_pydatetime())

    def _fill_entry(self, signal: Mapping[str, Any], reference_price: float, entry_time: datetime) -> dict[str, Any] | None:
        account_id = self.account["accountId"]
        symbol = signal["symbol"]
        open_lots = self.portfolio.lots_for(symbol)
        cycles, _ = self.repositories.lots.cycle_state(account_id, symbol)
        if open_lots and not self.policy.allow_additional_buys:
            return self._reject(signal, reference_price, "ADDITIONAL_BUYS_DISABLED")
        entry_number = len(open_lots)
        if entry_number >= self.policy.maximum_entries_per_cycle:
            return self._reject(signal, reference_price, "MAXIMUM_ENTRIES_PER_CYCLE")
        cycle_number = int(open_lots[0]["cycleId"].rsplit("Cycle", 1)[1]) if open_lots else cycles + 1
        quantity = self.policy.lot_quantity(entry_number, reference_price)
        fill = self.policy.buy(self.market.fees, reference_price, quantity)
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
        snapshot = signal.get("configurationSnapshot") or {}
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
        self.repositories.trades.insert(account_id=account_id, lot_id=lot["lotId"], market=self.market.market, symbol=symbol, side="BUY", quantity=quantity, price=round(fill.price, 4), fees=round(fill.fees, 4), slippage=round(fill.slippage, 4), reason="SIGNAL_ENTRY", executed_at=entry_time)
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
            for signal in self.portfolio.pending_entries.pop(symbol, []):
                if pd.Timestamp(signal["candleTimestamp"]) < pd.Timestamp(stamp):
                    self._fill_entry(signal, open_, stamp)
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
                    unrealized = Accounting.unrealized_pnl(entry_price, close, float(lot["quantity"]), float(lot.get("fees") or 0.0))
                    self.repositories.lots.mark(lot["lotId"], last_price=close, unrealized_pnl=unrealized, mae_pct=mae, mfe_pct=mfe)
                    self.portfolio.replace_lot({**lot, "lastPrice": close, "unrealizedPnl": unrealized, "maePct": mae, "mfePct": mfe})
        return closed

    def close_lot_manually(self, lot_id: str, *, price: float, timestamp: datetime | None = None) -> dict[str, Any]:
        with self._lock:
            lot = next((item for item in self.portfolio.all_open() if item["lotId"] == lot_id), None)
            if lot is None:
                raise KeyError(f"Open paper lot {lot_id} was not found")
            stamp = timestamp or self.clock()
            mae, mfe = Accounting.excursions(float(lot["entryPrice"]), price, price, lot.get("maePct"), lot.get("mfePct"))
            return self._close_lot(lot, "CLOSED", price, stamp, mae, mfe)

    def _close_lot(self, lot: Mapping[str, Any], status: str, raw_price: float, stamp: datetime, mae: float, mfe: float) -> dict[str, Any]:
        quantity = float(lot["quantity"])
        fill = self.policy.sell(self.market.fees, raw_price, quantity)
        entry_fees = float(lot.get("fees") or 0.0)
        total_fees = round(entry_fees + fill.fees, 4)
        realized = Accounting.realized_pnl(float(lot["entryPrice"]), round(fill.price, 4), quantity, total_fees)
        self.repositories.lots.mark(lot["lotId"], last_price=raw_price, unrealized_pnl=0.0, mae_pct=mae, mfe_pct=mfe)
        updated = self.repositories.lots.close(lot["lotId"], status=status, exit_timestamp=stamp, exit_price=round(fill.price, 4), realized_pnl=realized, fees=total_fees)
        self.repositories.trades.insert(account_id=self.account["accountId"], lot_id=lot["lotId"], market=self.market.market, symbol=lot["symbol"], side="SELL", quantity=quantity, price=round(fill.price, 4), fees=round(fill.fees, 4), slippage=round(fill.slippage, 4), reason=status, executed_at=stamp)
        self.account["cashBalance"] = self.repositories.accounts.adjust_cash(self.account["accountId"], Accounting.exit_proceeds(fill.price, quantity, fill.fees))
        self.portfolio.remove_lot(lot["lotId"], lot["symbol"])
        return updated

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
