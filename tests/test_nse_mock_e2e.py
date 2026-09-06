"""Synthetic NSE signal-to-paper execution regression."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from backend.markets.base import market_spec
from backend.paper_trading import ExecutionPolicy
from backend.signals.engine import RiskSettings, SignalEngine
from backend.strategies import STRATEGIES

from test_paper_trading import make_broker
from test_rsi_dip_ladder import candles
from test_signal_engine import FakeSignalRepository

IST = "Asia/Kolkata"


def test_mock_nse_rsi_signal_fills_next_open_and_closes_at_target() -> None:
    """Exercise the real strategy, signal engine and paper broker without external services."""
    frame = candles([100, 98, 96, 94, 96, 98])
    repository = FakeSignalRepository()
    broker = make_broker(policy=ExecutionPolicy(price_model="NEXT_OPEN"))
    engine = SignalEngine(
        market=market_spec("NSE"),
        strategy=STRATEGIES.get("rsi_dip_ladder_v1"),
        configuration={"rsi_length": 2, "rsi_low": 30, "rsi_recovery": 35},
        risk=RiskSettings(),
        timeframe="5m",
        repository=repository,
        clock=lambda: datetime(2026, 8, 3, 9, 45, tzinfo=pd.Timestamp.now(tz=IST).tzinfo),
    )
    engine.publish = broker.on_signal

    created = [
        signal
        for position in range(5)
        if (signal := engine.process_completed_candle("MOCKNSE", frame.iloc[[position]])) is not None
    ]

    assert len(created) == 1
    assert created[0]["signalType"] == "BUY"
    assert pd.Timestamp(created[0]["candleTimestamp"]) == frame.index[4]
    assert broker.positions() == []
    assert len(broker.repositories.pending.list(broker.account["accountId"])) == 1

    # Replaying the completed signal candle must not queue a duplicate order.
    assert engine.process_completed_candle("MOCKNSE", frame.iloc[[4]]) is None
    assert len(broker.repositories.pending.list(broker.account["accountId"])) == 1

    broker.on_completed_candle("MOCKNSE", frame.iloc[5], frame.index[5].to_pydatetime())
    [lot] = broker.positions()
    assert lot["quantity"] == 10
    assert pd.Timestamp(lot["entryTimestamp"]) == frame.index[5]
    assert float(lot["entryPrice"]) > 0

    exit_stamp = frame.index[5] + pd.Timedelta(minutes=5)
    target = float(lot["targetPrice"])
    closed = broker.on_completed_candle(
        "MOCKNSE",
        {"Open": target, "High": target + 1, "Low": target - 0.5, "Close": target, "Volume": 100_000},
        exit_stamp.to_pydatetime(),
    )

    assert len(closed) == 1
    assert closed[0]["status"] == "TARGET_HIT"
    assert pd.Timestamp(closed[0]["exitTimestamp"]) == exit_stamp
    assert float(closed[0]["realizedPnl"]) > 0
    assert broker.positions() == []
