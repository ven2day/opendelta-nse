"""The live signal engine: completed candle in, stored signal out.

Per completed candle for a symbol the engine (1) advances every open signal
for that symbol — HOLDING, TARGET_HIT, EXITED (stop) or EXPIRED — then
(2) asks the shared strategy for a decision on the bounded history and
(3) stores a BUY through the repository, which enforces the uniqueness
constraint; only a signal the database actually accepted is published.
No broker or exchange order API exists anywhere in this path.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Protocol

import pandas as pd

from backend.core.models import MarketContext, SignalDecision
from backend.markets.base import MarketSpec
from backend.signals.candle_processor import CandleHistory
from backend.strategies.base import Strategy

logger = logging.getLogger("opendelta.signals")

NO_ORDER_EXECUTION = True
HISTORY_BUFFER_BARS = 50


class SignalPersistence(Protocol):
    def insert_new(self, **values: Any) -> dict[str, Any] | None: ...

    def open(
        self,
        market: str,
        symbol: str | None = None,
        *,
        strategy_id: str | None = None,
        timeframe: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def mark_holding(self, signal_id: str, *, last_price: float) -> None: ...

    def update_last_price(self, signal_id: str, *, last_price: float) -> None: ...

    def close(self, signal_id: str, *, status: str, exit_timestamp: datetime, exit_price: float) -> dict[str, Any]: ...


class RiskSettings:
    """Engine-side exit rules for live signals (mirrors the backtest ExecutionSettings subset)."""

    def __init__(self, *, stop_loss_pct: float | None = None, maximum_holding_bars: int | None = None) -> None:
        if stop_loss_pct is not None and not 0 < stop_loss_pct < 100:
            raise ValueError("stop_loss_pct must be between 0 and 100")
        if maximum_holding_bars is not None and maximum_holding_bars < 1:
            raise ValueError("maximum_holding_bars must be at least 1")
        self.stop_loss_pct = stop_loss_pct
        self.maximum_holding_bars = maximum_holding_bars

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "RiskSettings":
        values = values or {}
        return cls(
            stop_loss_pct=values.get("stop_loss_pct", values.get("stopLossPct")),
            maximum_holding_bars=values.get("maximum_holding_bars", values.get("maximumHoldingBars")),
        )

    def public(self) -> dict[str, Any]:
        return {"stopLossPct": self.stop_loss_pct, "maximumHoldingBars": self.maximum_holding_bars}


class SignalEngine:
    def __init__(
        self,
        *,
        market: MarketSpec,
        strategy: Strategy,
        configuration: Mapping[str, Any] | None,
        risk: RiskSettings,
        timeframe: str,
        repository: SignalPersistence,
        clock: Callable[[], datetime],
        publish: Callable[[dict[str, Any]], None] | None = None,
        stale_after_seconds: float = 900.0,
    ) -> None:
        self.market = market
        self.strategy = strategy
        self.configuration = strategy.resolve(configuration) if hasattr(strategy, "resolve") else dict(configuration or {})
        strategy.validate_config(self.configuration)
        self.risk = risk
        self.timeframe = timeframe
        self.bar_minutes = market.minutes(timeframe)
        self.repository = repository
        self.clock = clock
        self.publish = publish or (lambda signal: None)
        self.stale_after_seconds = stale_after_seconds
        self.history = CandleHistory(strategy.required_history(self.configuration) + HISTORY_BUFFER_BARS)
        self._lock = threading.RLock()
        self.last_completed: pd.Timestamp | None = None
        self.last_decision: SignalDecision | None = None
        self.evaluations = 0
        self.signals_created = 0
        self.duplicates_rejected = 0

    # ---- candle intake ---------------------------------------------------------

    def process_completed_candle(self, symbol: str, candle: pd.DataFrame | Mapping[str, Any]) -> dict[str, Any] | None:
        """Handle one completed candle; returns the stored signal if a new one was created."""
        frame = candle if isinstance(candle, pd.DataFrame) else _frame_from_mapping(candle)
        new_stamps = self.history.append(symbol, frame)
        if not new_stamps:
            return None
        created: dict[str, Any] | None = None
        for stamp in new_stamps:
            row = self.history.get(symbol).loc[stamp]
            self._advance_open_signals(symbol, stamp, row)
            created = self._evaluate(symbol, stamp) or created
            with self._lock:
                self.last_completed = stamp if self.last_completed is None or stamp > self.last_completed else self.last_completed
        return created

    def _advance_open_signals(self, symbol: str, stamp: pd.Timestamp, row: pd.Series) -> None:
        exit_stamp = stamp.to_pydatetime()
        high, low, close = float(row["High"]), float(row["Low"]), float(row["Close"])
        for signal in self.repository.open(
            self.market.market,
            symbol,
            strategy_id=self.strategy.strategy_id,
            timeframe=self.timeframe,
        ):
            signal_stamp = pd.Timestamp(signal["candleTimestamp"])
            if stamp <= signal_stamp:
                continue
            stop = signal.get("stopPrice")
            target = signal.get("targetPrice")
            expires_at = signal.get("expiresAt")
            if stop is not None and low <= float(stop):
                self.repository.close(signal["signalId"], status="EXITED", exit_timestamp=exit_stamp, exit_price=float(stop))
            elif target is not None and high >= float(target):
                self.repository.close(signal["signalId"], status="TARGET_HIT", exit_timestamp=exit_stamp, exit_price=float(target))
            elif expires_at is not None and stamp >= pd.Timestamp(expires_at):
                self.repository.close(signal["signalId"], status="EXPIRED", exit_timestamp=exit_stamp, exit_price=close)
            elif signal["status"] == "STRONG_BUY":
                self.repository.mark_holding(signal["signalId"], last_price=close)
            else:
                self.repository.update_last_price(signal["signalId"], last_price=close)

    def _evaluate(self, symbol: str, stamp: pd.Timestamp) -> dict[str, Any] | None:
        context = MarketContext(market=self.market.market, symbol=symbol, timeframe=self.timeframe, timezone=self.market.timezone, as_of=stamp.to_pydatetime())
        decision = self.strategy.evaluate(self.history.get(symbol), context, self.configuration)
        with self._lock:
            self.evaluations += 1
            self.last_decision = decision
        if decision.decision != "BUY" or pd.Timestamp(decision.candle_timestamp) != stamp:
            return None
        stop = round(decision.signal_price * (1 - self.risk.stop_loss_pct / 100), 4) if self.risk.stop_loss_pct is not None and decision.stop_price is None else decision.stop_price
        expires_at = decision.candle_timestamp + timedelta(minutes=self.bar_minutes * self.risk.maximum_holding_bars) if self.risk.maximum_holding_bars else None
        stored = self.repository.insert_new(
            market=self.market.market,
            strategy_id=decision.strategy_id,
            strategy_version=decision.strategy_version,
            symbol=symbol,
            timeframe=self.timeframe,
            candle_timestamp=decision.candle_timestamp,
            signal_type=decision.decision,
            signal_price=decision.signal_price,
            target_price=decision.target_price,
            stop_price=stop,
            expires_at=expires_at,
            reasons=decision.reasons,
            indicators=decision.indicators,
            configuration_snapshot=decision.configuration_snapshot,
        )
        with self._lock:
            if stored is None:
                self.duplicates_rejected += 1
            else:
                self.signals_created += 1
        if stored is not None:
            self.publish(stored)
        return stored

    # ---- observability ---------------------------------------------------------

    def data_age_seconds(self) -> float | None:
        latest = self.history.latest_overall()
        if latest is None:
            return None
        now = pd.Timestamp(self.clock())
        now = now.tz_localize(self.market.timezone) if now.tzinfo is None else now.tz_convert(self.market.timezone)
        return max(0.0, (now - (latest + timedelta(minutes=self.bar_minutes))).total_seconds())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "market": self.market.market,
                "strategyId": self.strategy.strategy_id,
                "strategyVersion": self.strategy.version,
                "timeframe": self.timeframe,
                "symbols": len(self.history.symbols()),
                "lastCompletedCandle": self.last_completed.isoformat() if self.last_completed is not None else None,
                "dataAgeSeconds": self.data_age_seconds(),
                "evaluations": self.evaluations,
                "signalsCreated": self.signals_created,
                "duplicatesRejected": self.duplicates_rejected,
                "riskSettings": self.risk.public(),
                "paperOnly": True,
                "liveOrdersEnabled": False,
            }


def _frame_from_mapping(candle: Mapping[str, Any]) -> pd.DataFrame:
    stamp = pd.Timestamp(candle["timestamp"])
    values = {key: [float(candle[key])] for key in ("Open", "High", "Low", "Close", "Volume")}
    frame = pd.DataFrame(values, index=pd.DatetimeIndex([stamp]))
    if "complete" in candle or "Complete" in candle:
        frame["Complete"] = bool(candle.get("complete", candle.get("Complete")))
    return frame
