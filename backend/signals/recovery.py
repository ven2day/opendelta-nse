"""Restart recovery: rebuild candle histories from the candle source and resume open signals from the database."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Sequence

from backend.markets.base import CandleSource
from backend.signals.candle_processor import CandleProcessor
from backend.signals.engine import SignalEngine

logger = logging.getLogger("opendelta.signals.recovery")


def rebuild_histories(engine: SignalEngine, source: CandleSource, processor: CandleProcessor, symbols: Sequence[str], *, now: datetime, lookback_days: int = 3) -> dict[str, Any]:
    """Seed the engine with completed candles for every symbol; failures are reported, not raised."""
    warmup = engine.history.maximum_bars
    failed: list[dict[str, str]] = []
    seeded = 0
    for symbol in symbols:
        try:
            frame = source.candles(symbol, engine.timeframe, now - timedelta(days=lookback_days), now, warmup_bars=warmup)
            engine.history.seed(symbol, processor.completed(frame, now))
            seeded += 1
        except Exception as error:  # noqa: BLE001 - one symbol must not block recovery
            failed.append({"symbol": symbol, "message": str(error)[:240]})
    latest = engine.history.latest_overall()
    engine.last_completed = latest
    open_signals = engine.repository.open(
        engine.market.market,
        strategy_id=engine.strategy.strategy_id,
        timeframe=engine.timeframe,
    )
    logger.info("Recovered %s %s symbols (%s failed) with %s open signal(s)", seeded, engine.market.market, len(failed), len(open_signals))
    return {"symbolsSeeded": seeded, "symbolsFailed": failed, "openSignals": len(open_signals), "lastCompletedCandle": latest.isoformat() if latest is not None else None}
