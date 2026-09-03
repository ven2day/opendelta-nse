"""Paper trading guarantees: one order per signal, independent lots, costs, separate accounts, restart rebuild, no real orders."""

from __future__ import annotations

import inspect
import os
import unittest
import uuid
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from backend.data.database import Database
from backend.data.repositories import PaperAccountRepository, PaperLotRepository, PaperOrderRepository, PaperTradeRepository
from backend.markets.base import market_spec
from backend.markets.nse.fees import NseFeeModel
from backend.paper_trading import Accounting, ExecutionPolicy, PaperBroker, PaperRepositories
from backend.paper_trading import broker as broker_module
from backend.paper_trading import execution as execution_module

IST = "Asia/Kolkata"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


# ---- in-memory repositories mirroring the schema's constraints ---------------------


class MemoryAccounts:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def get(self, market):
        return dict(self.rows[market]) if market in self.rows else None

    def get_or_create(self, market, *, currency, starting_balance, risk_settings=None):
        if market not in self.rows:
            self.rows[market] = {"accountId": str(uuid.uuid4()), "market": market, "currency": currency, "startingBalance": starting_balance, "cashBalance": starting_balance, "riskSettings": dict(risk_settings or {}), "createdAt": None, "updatedAt": None, "resetAt": None}
        return dict(self.rows[market])

    def reset(self, market, *, starting_balance=None, risk_settings=None):
        row = self.rows[market]
        balance = starting_balance if starting_balance is not None else row["startingBalance"]
        row.update(startingBalance=balance, cashBalance=balance)
        self.wiped = market
        return dict(row)

    def adjust_cash(self, account_id, delta):
        for row in self.rows.values():
            if row["accountId"] == account_id:
                row["cashBalance"] = row["cashBalance"] + delta
                return row["cashBalance"]
        raise KeyError(account_id)


class MemoryOrders:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def insert(self, **values):
        if values["status"] == "FILLED" and values["side"] == "BUY" and values["signal_id"] is not None:
            if any(r["signalId"] == values["signal_id"] and r["accountId"] == values["account_id"] and r["status"] == "FILLED" and r["side"] == "BUY" for r in self.rows):
                return None
        row = {"orderId": str(uuid.uuid4()), "accountId": values["account_id"], "market": values["market"], "signalId": values["signal_id"], "strategyId": values["strategy_id"], "strategyVersion": values["strategy_version"], "symbol": values["symbol"], "side": values["side"], "quantity": values["quantity"], "requestedPrice": values["requested_price"], "executedPrice": values["executed_price"], "fees": values["fees"], "slippage": values["slippage"], "status": values["status"], "reason": values.get("reason")}
        self.rows.append(row)
        return dict(row)

    def list(self, account_id, *, limit=200):
        return [dict(r) for r in self.rows if r["accountId"] == account_id][:limit]


class MemoryLots:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def insert(self, **values):
        lot_id = str(uuid.uuid4())
        row = {"lotId": lot_id, "accountId": values["account_id"], "orderId": values["order_id"], "signalId": values.get("signal_id"), "market": values["market"], "strategyId": values["strategy_id"], "strategyVersion": values["strategy_version"], "symbol": values["symbol"], "timeframe": values["timeframe"], "cycleId": values["cycle_id"], "lotNumber": values["lot_number"], "entryTimestamp": pd.Timestamp(values["entry_timestamp"]).isoformat(), "entryPrice": values["entry_price"], "quantity": values["quantity"], "targetPrice": values["target_price"], "stopPrice": values.get("stop_price"), "expiresAt": values["expires_at"].isoformat() if values.get("expires_at") else None, "status": "OPEN", "exitTimestamp": None, "exitPrice": None, "realizedPnl": None, "unrealizedPnl": values.get("unrealized_pnl", 0.0), "fees": values.get("fees", 0.0), "lastPrice": values["entry_price"], "maePct": 0.0, "mfePct": 0.0, "configurationSnapshot": dict(values.get("configuration_snapshot") or {})}
        self.rows[lot_id] = row
        return dict(row)

    def get(self, lot_id):
        return dict(self.rows[lot_id])

    def open(self, account_id, symbol=None):
        return [dict(r) for r in self.rows.values() if r["accountId"] == account_id and r["status"] == "OPEN" and (symbol is None or r["symbol"] == symbol)]

    def cycle(self, account_id, symbol, cycle_id):
        return sorted(
            [dict(r) for r in self.rows.values() if r["accountId"] == account_id and r["symbol"] == symbol and r["cycleId"] == cycle_id],
            key=lambda row: row["lotNumber"],
        )

    def list(self, account_id, *, status=None, limit=500):
        return [dict(r) for r in self.rows.values() if r["accountId"] == account_id and (status is None or r["status"] == status)][:limit]

    def cycle_state(self, account_id, symbol):
        lots = [r for r in self.rows.values() if r["accountId"] == account_id and r["symbol"] == symbol]
        cycles = max((int(r["cycleId"].rsplit("Cycle", 1)[1]) for r in lots), default=0)
        return cycles, sum(1 for r in lots if r["status"] == "OPEN")

    def mark(self, lot_id, *, last_price, unrealized_pnl, mae_pct, mfe_pct):
        if self.rows[lot_id]["status"] == "OPEN":
            self.rows[lot_id].update(lastPrice=last_price, unrealizedPnl=unrealized_pnl, maePct=mae_pct, mfePct=mfe_pct)

    def close(self, lot_id, *, status, exit_timestamp, exit_price, realized_pnl, fees):
        row = self.rows[lot_id]
        if row["status"] == "OPEN":
            row.update(status=status, exitTimestamp=pd.Timestamp(exit_timestamp).isoformat(), exitPrice=exit_price, realizedPnl=realized_pnl, fees=fees, unrealizedPnl=0.0, lastPrice=exit_price)
        return dict(row)


class MemoryTrades:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def insert(self, **values):
        self.rows.append(dict(values))

    def list(self, account_id, *, limit=500):
        return [dict(r) for r in self.rows if r["account_id"] == account_id][:limit]


def memory_repositories() -> PaperRepositories:
    return PaperRepositories(MemoryAccounts(), MemoryOrders(), MemoryLots(), MemoryTrades())  # type: ignore[arg-type]


def signal(symbol: str = "TCS", price: float = 100.0, minute: int = 0, *, market: str = "NSE", signal_id: str | None = None) -> dict[str, Any]:
    stamp = datetime(2026, 9, 1, 10, 0, tzinfo=pd.Timestamp.now(tz=IST).tzinfo) + timedelta(minutes=minute)
    return {"signalId": signal_id or str(uuid.uuid4()), "market": market, "strategyId": "ema_vwap_strong_buy", "strategyVersion": "1.0.0", "symbol": symbol, "timeframe": "5m", "candleTimestamp": stamp.isoformat(), "signalType": "BUY", "status": "STRONG_BUY", "signalPrice": price, "targetPrice": round(price * 1.01, 4), "stopPrice": None, "configurationSnapshot": {"target_pct": 1.0, "ema_fast": 9}}


def ladder_signal(symbol: str, price: float, minute: int) -> dict[str, Any]:
    draft = signal(symbol, price, minute)
    draft.update(
        strategyId="rsi_dip_ladder_v1",
        strategyVersion="1.0.0",
        targetPrice=round(price * 1.05, 4),
        configurationSnapshot={
            "target_pct": 5.0,
            "lot_sizing_mode": "PRICE_BAND_LADDER",
            "price_band_threshold": 1000.0,
            "high_price_quantities": [5, 10, 25, 50],
            "low_price_quantities": [10, 20, 50, 100],
            "dip_step_pct": 5.0,
            "maximum_position_capital": 250000.0,
        },
    )
    return draft


def candle(minute: int, *, open_: float, high: float, low: float, close: float) -> tuple[dict[str, float], datetime]:
    stamp = datetime(2026, 9, 1, 10, 0, tzinfo=pd.Timestamp.now(tz=IST).tzinfo) + timedelta(minutes=minute)
    return {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1000.0}, stamp


def make_broker(repositories: PaperRepositories | None = None, *, market: str = "NSE", policy: ExecutionPolicy | None = None, balance: float | None = None) -> PaperBroker:
    return PaperBroker(market=market_spec(market), repositories=repositories or memory_repositories(), policy=policy or ExecutionPolicy(), timeframe="5m", clock=lambda: datetime(2026, 9, 1, 12, 0, tzinfo=pd.Timestamp.now(tz=IST).tzinfo), starting_balance=balance)


class SizingAndCostTests(unittest.TestCase):
    def test_fixed_quantity_and_fixed_capital_sizing(self) -> None:
        fixed = ExecutionPolicy(initial_quantity=100, additional_quantity_pct=50)
        self.assertEqual([fixed.lot_quantity(index, 250.0) for index in range(4)], [100, 50, 25, 12])
        capital = ExecutionPolicy(sizing_mode="FIXED_CAPITAL", capital_per_lot=50_000, additional_sizing_mode="FIXED_PERCENTAGE_OF_FIRST_LOT")
        self.assertEqual(capital.lot_quantity(0, 250.0), 200)
        self.assertEqual(capital.lot_quantity(1, 250.0), 100)
        self.assertEqual(capital.lot_quantity(2, 250.0), 100)
        fractional = ExecutionPolicy(sizing_mode="FIXED_CAPITAL", capital_per_lot=1_000, whole_units=False)
        self.assertAlmostEqual(fractional.lot_quantity(0, 60_000.0), 0.01666667)
        with self.assertRaises(ValueError):
            ExecutionPolicy(sizing_mode="MAGIC").validate()
        with self.assertRaises(ValueError):
            ExecutionPolicy.from_mapping({"unknown": 1})
        self.assertEqual(ExecutionPolicy.from_mapping({"stopLossPct": 2, "priceModel": "NEXT_OPEN"}).stop_loss_pct, 2)

    def test_fees_and_slippage_are_applied_on_entry_and_exit(self) -> None:
        broker = make_broker()
        lot = broker.on_signal(signal(price=100.0))
        fees = NseFeeModel()
        buy = fees.buy(100.0, 100)
        self.assertAlmostEqual(lot["entryPrice"], round(buy.price, 4))
        self.assertAlmostEqual(lot["fees"], round(buy.fees, 4))
        self.assertAlmostEqual(broker.account["cashBalance"], 1_000_000.0 - Accounting.entry_cost(buy.price, 100, buy.fees))
        self.assertAlmostEqual(lot["targetPrice"], round(lot["entryPrice"] * 1.01, 4))
        row, stamp = candle(10, open_=101.0, high=101.5, low=100.5, close=101.2)
        [closed] = broker.on_completed_candle("TCS", row, stamp)
        sell = fees.sell(lot["targetPrice"], 100)
        self.assertEqual(closed["status"], "TARGET_HIT")
        self.assertAlmostEqual(closed["exitPrice"], round(sell.price, 4))
        self.assertAlmostEqual(closed["fees"], round(lot["fees"] + sell.fees, 4))  # stored entry fees + exit fees
        self.assertAlmostEqual(closed["realizedPnl"], round((round(sell.price, 4) - lot["entryPrice"]) * 100 - closed["fees"], 2))
        expected_cash = 1_000_000.0 - Accounting.entry_cost(buy.price, 100, buy.fees) + Accounting.exit_proceeds(sell.price, 100, sell.fees)
        self.assertAlmostEqual(broker.account["cashBalance"], expected_cash, places=4)
        self.assertEqual([trade["side"] for trade in broker.repositories.trades.rows], ["BUY", "SELL"])


class OrderAndLotTests(unittest.TestCase):
    def test_rsi_dip_ladder_uses_frozen_price_band_dip_gate_and_independent_targets_after_restart(self) -> None:
        repositories = memory_repositories()
        broker = make_broker(repositories)
        first = broker.on_signal(ladder_signal("M&M", 3000.0, 0))
        self.assertEqual((first["lotNumber"], first["quantity"]), (1, 5))
        # A repeated RSI signal does not control additional ladder entries.
        self.assertIsNone(broker.on_signal(ladder_signal("M&M", 2900.0, 5)))
        row, stamp = candle(5, open_=2900.0, high=2910.0, low=2890.0, close=2900.0)
        broker.on_completed_candle("M&M", row, stamp)
        self.assertEqual(len(broker.positions()), 1)
        # The completed close crosses the 5% level; lot 2 fills at the next open.
        row, stamp = candle(10, open_=2850.0, high=2860.0, low=2840.0, close=2850.0)
        broker.on_completed_candle("M&M", row, stamp)
        row, stamp = candle(15, open_=2840.0, high=2850.0, low=2830.0, close=2840.0)
        broker.on_completed_candle("M&M", row, stamp)
        second = max(broker.positions(), key=lambda lot: lot["lotNumber"])
        # Lot 3 is likewise triggered by price alone, without a new RSI signal.
        row, stamp = candle(20, open_=2700.0, high=2710.0, low=2690.0, close=2690.0)
        broker.on_completed_candle("M&M", row, stamp)
        row, stamp = candle(25, open_=2680.0, high=2690.0, low=2670.0, close=2680.0)
        broker.on_completed_candle("M&M", row, stamp)
        third = max(broker.positions(), key=lambda lot: lot["lotNumber"])
        self.assertEqual([second["quantity"], third["quantity"]], [10, 25])
        self.assertTrue(all(lot["targetPrice"] == round(lot["entryPrice"] * 1.05, 4) for lot in (first, second, third)))

        row, stamp = candle(30, open_=2800.0, high=float(third["targetPrice"]) + 1, low=2790.0, close=2800.0)
        closed = broker.on_completed_candle("M&M", row, stamp)
        self.assertEqual([lot["lotNumber"] for lot in closed], [3])
        self.assertEqual(sorted(lot["lotNumber"] for lot in broker.positions()), [1, 2])

        reborn = make_broker(repositories)
        row, stamp = candle(35, open_=2540.0, high=2550.0, low=2530.0, close=2530.0)
        reborn.on_completed_candle("M&M", row, stamp)
        row, stamp = candle(40, open_=2520.0, high=2530.0, low=2510.0, close=2520.0)
        reborn.on_completed_candle("M&M", row, stamp)
        fourth = max(reborn.positions(), key=lambda lot: lot["lotNumber"])
        self.assertEqual((fourth["cycleId"], fourth["lotNumber"], fourth["quantity"]), ("M&M-Cycle1", 4, 50))
        self.assertIsNone(reborn.on_signal(ladder_signal("M&M", 2400.0, 30)))
        self.assertEqual(len(reborn.positions()), 3)

    def test_the_same_signal_cannot_open_two_paper_orders(self) -> None:
        broker = make_broker()
        first = signal(signal_id="11111111-1111-1111-1111-111111111111")
        self.assertIsNotNone(broker.on_signal(first))
        self.assertIsNone(broker.on_signal(first))  # replayed / duplicated publish
        self.assertIsNone(make_broker(broker.repositories).on_signal(first))  # even from a rebuilt broker
        self.assertEqual(len([o for o in broker.repositories.orders.rows if o["status"] == "FILLED"]), 1)
        self.assertEqual(len(broker.repositories.lots.rows), 1)

    def test_each_strong_buy_lot_has_its_own_entry_target_and_closes_independently(self) -> None:
        broker = make_broker(policy=ExecutionPolicy(initial_quantity=100, additional_quantity_pct=50))
        first = broker.on_signal(signal(price=100.0, minute=0))
        second = broker.on_signal(signal(price=104.0, minute=5))
        third = broker.on_signal(signal(price=108.0, minute=10))
        self.assertEqual([lot["lotNumber"] for lot in (first, second, third)], [1, 2, 3])
        self.assertEqual([lot["quantity"] for lot in (first, second, third)], [100, 50, 25])
        self.assertEqual({lot["cycleId"] for lot in (first, second, third)}, {"TCS-Cycle1"})
        self.assertLess(first["targetPrice"], second["targetPrice"])
        row, stamp = candle(15, open_=101.0, high=101.5, low=100.8, close=101.2)  # only lot 1's target is reached
        closed = broker.on_completed_candle("TCS", row, stamp)
        self.assertEqual([lot["lotNumber"] for lot in closed], [1])
        self.assertEqual(sorted(lot["lotNumber"] for lot in broker.positions()), [2, 3])
        row, stamp = candle(20, open_=109.0, high=110.0, low=108.5, close=109.5)  # lots 2 and 3 both close
        closed = broker.on_completed_candle("TCS", row, stamp)
        self.assertEqual(sorted(lot["lotNumber"] for lot in closed), [2, 3])
        self.assertEqual(broker.positions(), [])
        fourth = broker.on_signal(signal(price=110.0, minute=25))
        self.assertEqual((fourth["cycleId"], fourth["lotNumber"], fourth["quantity"]), ("TCS-Cycle2", 1, 100))

    def test_stop_loss_expiry_and_rejections(self) -> None:
        broker = make_broker(policy=ExecutionPolicy(stop_loss_pct=1.0, maximum_holding_bars=2, maximum_entries_per_cycle=1))
        lot = broker.on_signal(signal(price=100.0))
        self.assertAlmostEqual(lot["stopPrice"], round(lot["entryPrice"] * 0.99, 4))
        self.assertIsNone(broker.on_signal(signal(price=100.5, minute=5)))  # cap of one entry per cycle
        self.assertEqual([o["reason"] for o in broker.repositories.orders.rows if o["status"] == "REJECTED"], ["MAXIMUM_ENTRIES_PER_CYCLE"])
        row, stamp = candle(5, open_=99.8, high=99.9, low=98.5, close=99.0)
        [stopped] = broker.on_completed_candle("TCS", row, stamp)
        self.assertEqual(stopped["status"], "STOPPED")
        self.assertLess(stopped["realizedPnl"], 0)
        expiring = make_broker(policy=ExecutionPolicy(maximum_holding_bars=2))
        lot = expiring.on_signal(signal(price=100.0))
        for minute in (5, 10):
            row, stamp = candle(minute, open_=100.0, high=100.2, low=99.9, close=100.0)
            closed = expiring.on_completed_candle("TCS", row, stamp)
        self.assertEqual(closed[0]["status"], "EXPIRED")
        broke = make_broker(balance=500.0)
        self.assertIsNone(broke.on_signal(signal(price=100.0)))
        self.assertEqual(broke.repositories.orders.rows[-1]["reason"], "INSUFFICIENT_FUNDS")

    def test_next_open_price_model_fills_on_the_following_candle(self) -> None:
        broker = make_broker(policy=ExecutionPolicy(price_model="NEXT_OPEN"))
        self.assertIsNone(broker.on_signal(signal(price=100.0, minute=0)))
        self.assertEqual(broker.positions(), [])
        row, stamp = candle(5, open_=100.4, high=100.6, low=100.1, close=100.3)
        broker.on_completed_candle("TCS", row, stamp)
        [lot] = broker.positions()
        self.assertAlmostEqual(lot["entryPrice"], round(NseFeeModel().buy(100.4, 100).price, 4))
        self.assertEqual(pd.Timestamp(lot["entryTimestamp"]), pd.Timestamp(stamp))


class AccountTests(unittest.TestCase):
    def test_nse_and_crypto_balances_positions_and_settings_stay_separate(self) -> None:
        repositories = memory_repositories()
        nse = make_broker(repositories, market="NSE")
        crypto = make_broker(repositories, market="CRYPTO", policy=ExecutionPolicy(sizing_mode="FIXED_CAPITAL", capital_per_lot=1_000, whole_units=False))
        self.assertEqual((nse.account["currency"], crypto.account["currency"]), ("INR", "USDT"))
        self.assertNotEqual(nse.account["accountId"], crypto.account["accountId"])
        nse.on_signal(signal("TCS", 100.0))
        crypto.on_signal(signal("BTC/USDT", 60_000.0, market="CRYPTO"))
        self.assertEqual([lot["symbol"] for lot in nse.positions()], ["TCS"])
        self.assertEqual([lot["symbol"] for lot in crypto.positions()], ["BTC/USDT"])
        self.assertAlmostEqual(crypto.positions()[0]["quantity"], 1_000 / 60_000, places=6)
        self.assertLess(nse.account["cashBalance"], 1_000_000.0)
        self.assertLess(crypto.account["cashBalance"], 100_000.0)
        self.assertIsNone(nse.on_signal(signal("ETH/USDT", 3_000.0, market="CRYPTO")))  # wrong market is ignored
        nse_summary, crypto_summary = nse.summary(), crypto.summary()
        self.assertEqual((nse_summary["currency"], nse_summary["openPositions"]), ("INR", 1))
        self.assertEqual((crypto_summary["currency"], crypto_summary["openPositions"]), ("USDT", 1))
        self.assertNotEqual(nse_summary["executionPolicy"], crypto_summary["executionPolicy"])

    def test_portfolio_and_balances_survive_a_restart(self) -> None:
        repositories = memory_repositories()
        broker = make_broker(repositories)
        broker.on_signal(signal("TCS", 100.0, minute=0))
        broker.on_signal(signal("INFY", 200.0, minute=5))
        cash_before = broker.account["cashBalance"]
        reborn = make_broker(repositories)  # a new process rebuilding from storage
        self.assertEqual(sorted(lot["symbol"] for lot in reborn.positions()), ["INFY", "TCS"])
        self.assertAlmostEqual(reborn.account["cashBalance"], cash_before)
        row, stamp = candle(10, open_=101.0, high=101.5, low=100.5, close=101.2)
        [closed] = reborn.on_completed_candle("TCS", row, stamp)
        self.assertEqual((closed["symbol"], closed["status"]), ("TCS", "TARGET_HIT"))
        self.assertEqual([lot["symbol"] for lot in reborn.positions()], ["INFY"])
        summary = reborn.summary()
        self.assertEqual(summary["closedLots"], 1)
        self.assertEqual(summary["realizedPnlToday"], closed["realizedPnl"])
        self.assertEqual(summary["dailyPnl"], round(closed["realizedPnl"] + summary["unrealizedPnl"], 2))
        self.assertTrue(summary["paperOnly"] and not summary["liveOrdersEnabled"])

    def test_reset_restores_balance_and_clears_positions(self) -> None:
        broker = make_broker()
        broker.on_signal(signal())
        broker.reset(starting_balance=250_000.0)
        self.assertEqual(broker.positions(), [])
        self.assertEqual(broker.account["cashBalance"], 250_000.0)

    def test_no_real_order_endpoint_is_ever_called(self) -> None:
        self.assertTrue(broker_module.NO_REAL_ORDERS)
        for module in (broker_module, execution_module):
            source = inspect.getsource(module)
            for forbidden in ("place_order", "create_order", "submit_order", "/orders", "requests.", "httpx", "urlopen", "websocket"):
                self.assertNotIn(forbidden, source)


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL is not set")
class PaperDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database = Database(TEST_DATABASE_URL, max_pool_size=2)
        cls.database.open()
        cls.database.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cls.database.migrate()
        cls.repositories = PaperRepositories(PaperAccountRepository(cls.database), PaperOrderRepository(cls.database), PaperLotRepository(cls.database), PaperTradeRepository(cls.database))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.database.close()

    def _stored_signal(self, symbol: str, price: float, minute: int) -> dict[str, Any]:
        """Paper orders reference live_signals by foreign key, so the signal must be stored first."""
        from backend.data.repositories import LiveSignalRepository

        draft = signal(symbol, price, minute)
        stored = LiveSignalRepository(self.database).insert_new(
            market="NSE", strategy_id=draft["strategyId"], strategy_version=draft["strategyVersion"], symbol=symbol, timeframe="5m",
            candle_timestamp=pd.Timestamp(draft["candleTimestamp"]).to_pydatetime(), signal_type="BUY", signal_price=price, target_price=draft["targetPrice"],
            stop_price=None, expires_at=None, reasons=["EMA_BULLISH_CROSS"], indicators={}, configuration_snapshot=draft["configurationSnapshot"],
        )
        assert stored is not None
        return stored

    def test_unique_index_blocks_a_second_order_for_the_same_signal_and_state_rebuilds(self) -> None:
        broker = make_broker(self.repositories)
        unkeyed = signal("TCS", 100.0)
        unkeyed["signalId"] = None  # manual/unstored entries carry no signal id; uniqueness applies when one is present
        self.assertIsNotNone(broker.on_signal(unkeyed))
        keyed = self._stored_signal("INFY", 200.0, 5)
        first = broker.on_signal(keyed)
        self.assertIsNotNone(first)
        self.assertIsNone(broker.on_signal(keyed))
        self.assertIsNone(make_broker(self.repositories).on_signal(keyed))
        self.assertEqual(len(self.repositories.orders.for_signal(broker.account["accountId"], keyed["signalId"])), 1)
        reborn = make_broker(self.repositories)
        self.assertEqual(sorted(lot["symbol"] for lot in reborn.positions()), ["INFY", "TCS"])
        self.assertAlmostEqual(reborn.account["cashBalance"], broker.account["cashBalance"], places=4)
        row, stamp = candle(10, open_=202.0, high=203.0, low=201.0, close=202.5)
        [closed] = reborn.on_completed_candle("INFY", row, stamp)
        self.assertEqual(closed["status"], "TARGET_HIT")
        self.assertEqual(self.repositories.lots.get(closed["lotId"])["status"], "TARGET_HIT")
        self.assertEqual([trade["side"] for trade in self.repositories.trades.list(reborn.account["accountId"])][:2], ["SELL", "BUY"])
        crypto = make_broker(self.repositories, market="CRYPTO")
        self.assertEqual((crypto.account["currency"], crypto.account["cashBalance"]), ("USDT", 100_000.0))
        self.assertNotEqual(crypto.account["accountId"], reborn.account["accountId"])
        reset = reborn.reset(starting_balance=10.0)
        self.assertEqual(reset["cashBalance"], 10.0)
        self.assertEqual(self.repositories.lots.open(reset["accountId"]), [])
