from datetime import datetime, timedelta, timezone

import pytest

from backend.core.fifo import FifoInventory, net_profit_target_price
from backend.markets.nse.fees import NseFeeModel


def test_fifo_consumes_oldest_shares_and_recalculates_remaining_cost() -> None:
    ledger = FifoInventory()
    first = datetime(2026, 9, 1, tzinfo=timezone.utc)
    ledger.add("lot-1", first, 3000.0, 5, 5.0)
    ledger.add("lot-2", first + timedelta(days=1), 2850.0, 10, 10.0)

    sold = ledger.consume(10)

    assert sold.cost_basis_price == 2925.0
    assert sold.entry_fees == 10.0
    assert [(item.acquisition_id, item.quantity) for item in sold.allocations] == [("lot-1", 5.0), ("lot-2", 5.0)]
    assert ledger.quantity == 5
    assert ledger.cost == 14_250
    [remaining] = ledger.preview_allocations([5])
    assert remaining.cost_basis_price == 2850.0
    assert remaining.entry_fees == 5.0


def test_fifo_rejects_overselling() -> None:
    ledger = FifoInventory()
    ledger.add("lot-1", datetime(2026, 9, 1, tzinfo=timezone.utc), 100.0, 5)
    with pytest.raises(ValueError, match="contains only 5"):
        ledger.consume(6)


def test_fifo_preserves_insertion_order_when_buys_share_a_timestamp() -> None:
    ledger = FifoInventory()
    stamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
    ledger.add("z-first", stamp, 100.0, 1)
    ledger.add("a-second", stamp, 90.0, 1)

    sold = ledger.consume(1)

    assert [item.acquisition_id for item in sold.allocations] == ["z-first"]


def test_fifo_target_guarantees_configured_net_profit_after_all_costs() -> None:
    ledger = FifoInventory()
    stamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
    ledger.add("lot-1", stamp, 3639.82, 5, 40.20)
    ledger.add("lot-2", stamp + timedelta(days=1), 3278.94, 10, 56.40)
    ledger.add("lot-3", stamp + timedelta(days=2), 2903.45, 25, 100.60)
    match = ledger.preview_allocations([25])[0]
    fees = NseFeeModel()

    target = net_profit_target_price(match, fees, 10)
    fill = fees.sell(target, match.quantity)
    net_profit = fill.price * match.quantity - fill.fees - match.acquisition_cost

    assert match.cost_basis_price == 3200.92
    assert net_profit / match.acquisition_cost * 100 >= 10
    previous_fill = fees.sell(target - 0.01, match.quantity)
    previous_profit = previous_fill.price * match.quantity - previous_fill.fees - match.acquisition_cost
    assert previous_profit / match.acquisition_cost * 100 < 10
