from datetime import datetime, timedelta, timezone

import pytest

from backend.core.fifo import FifoInventory


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
