"""Validated TradingView alert ingestion.

TradingView is an external signal source only. An alert is accepted when its
strategy, market, timeframe and symbol match an explicitly enabled OpenDelta
deployment. OpenDelta remains responsible for persistence, risk and paper
execution.
"""

from __future__ import annotations

import hmac
import os
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.data.repositories import (
    LiveSignalRepository,
    SavedUniverseRepository,
    StrategyConfigRepository,
    StrategyDeploymentRepository,
)
from backend.markets.base import market_spec
from backend.paper_trading.broker import PaperBroker
from backend.paper_trading.execution import ExecutionPolicy
from backend.strategies.registry import StrategyRegistry

TRADINGVIEW_SOURCE = "TRADINGVIEW"
DEFAULT_MAX_ALERT_AGE_SECONDS = 900
_EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class TradingViewAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal["1"] = "1"
    eventId: str = Field(min_length=8, max_length=128)
    webhookKey: str = Field(min_length=16, max_length=256, repr=False)
    market: Literal["NSE", "CRYPTO"]
    exchange: str | None = Field(default=None, max_length=32)
    symbol: str = Field(min_length=1, max_length=64)
    timeframe: str = Field(min_length=1, max_length=12)
    strategyId: str = Field(min_length=1, max_length=120)
    strategyVersion: str = Field(min_length=1, max_length=40)
    action: Literal["BUY"]
    candleTimestamp: datetime
    sentAt: datetime
    signalPrice: float = Field(gt=0)
    targetPrice: float | None = Field(default=None, gt=0)
    stopPrice: float | None = Field(default=None, gt=0)
    expiresAt: datetime | None = None
    reasons: list[str] = Field(default_factory=lambda: ["TRADINGVIEW_ALERT"], max_length=20)
    indicators: dict[str, Any] = Field(default_factory=dict)

    @field_validator("eventId")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        if not _EVENT_ID.fullmatch(value):
            raise ValueError("eventId must use 8-128 letters, numbers, dots, colons, underscores or hyphens")
        return value

    @field_validator("candleTimestamp", "sentAt", "expiresAt")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value


class TradingViewRejected(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class TradingViewIngestionService:
    def __init__(
        self,
        *,
        registry: StrategyRegistry,
        deployments: Callable[[], StrategyDeploymentRepository],
        configs: Callable[[], StrategyConfigRepository],
        universes: Callable[[], SavedUniverseRepository],
        signals: Callable[[], LiveSignalRepository],
        broker: Callable[[str], PaperBroker],
        clock: Callable[[], datetime],
    ) -> None:
        self.registry = registry
        self.deployments = deployments
        self.configs = configs
        self.universes = universes
        self.signals = signals
        self.broker = broker
        self.clock = clock

    @staticmethod
    def configured() -> bool:
        return bool(os.environ.get("TRADINGVIEW_WEBHOOK_KEY", "").strip())

    def status(self, market: str, strategy_id: str) -> dict[str, Any]:
        deployment = self.deployments().get(market, strategy_id)
        return {
            "configured": self.configured(),
            "paperOnly": True,
            "deployment": deployment,
            "ready": bool(
                self.configured()
                and deployment
                and deployment.get("signalSource") == TRADINGVIEW_SOURCE
                and deployment.get("mode") in {"SIGNALS", "PAPER"}
                and deployment.get("universeId")
            ),
        }

    def ingest(self, alert: TradingViewAlert) -> dict[str, Any]:
        self._authenticate(alert.webhookKey)
        now = self.clock()
        now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        sent_at = alert.sentAt.astimezone(UTC)
        try:
            maximum_age = int(
                os.environ.get("TRADINGVIEW_MAX_ALERT_AGE_SECONDS", str(DEFAULT_MAX_ALERT_AGE_SECONDS))
            )
        except ValueError as error:
            raise TradingViewRejected(503, "TRADINGVIEW_MAX_ALERT_AGE_SECONDS must be an integer") from error
        if maximum_age < 60 or maximum_age > 86_400:
            raise TradingViewRejected(503, "TRADINGVIEW_MAX_ALERT_AGE_SECONDS must be between 60 and 86400")
        age = (now - sent_at).total_seconds()
        if age < -60 or age > maximum_age:
            raise TradingViewRejected(409, "Alert is outside the accepted delivery window")

        try:
            strategy = self.registry.get(alert.strategyId)
        except KeyError as error:
            raise TradingViewRejected(404, f"Unknown strategy: {alert.strategyId}") from error
        if alert.market not in strategy.supported_markets:
            raise TradingViewRejected(422, f"{alert.strategyId} does not support {alert.market}")

        deployment = self.deployments().get(alert.market, alert.strategyId)
        if deployment is None:
            raise TradingViewRejected(409, "Strategy is not deployed for this market")
        if deployment.get("signalSource") != TRADINGVIEW_SOURCE:
            raise TradingViewRejected(409, "Strategy deployment is not accepting TradingView alerts")
        if deployment["mode"] == "OFF":
            raise TradingViewRejected(409, "Strategy deployment is off")
        if deployment["strategyVersion"] != alert.strategyVersion:
            raise TradingViewRejected(409, "Alert strategy version does not match the approved deployment")
        if deployment["timeframe"] != alert.timeframe:
            raise TradingViewRejected(409, "Alert timeframe does not match the approved deployment")
        universe_id = deployment.get("universeId")
        if not universe_id:
            raise TradingViewRejected(409, "Assign a watchlist before accepting TradingView alerts")
        configured_symbols = self.universes().symbols(universe_id, market=alert.market)
        symbol = resolve_watchlist_symbol(alert.symbol, configured_symbols)
        if symbol is None:
            raise TradingViewRejected(422, f"{alert.symbol} is not in the deployment watchlist")

        active = self.configs().active(alert.market, alert.strategyId)
        if active is None or active["configId"] != deployment.get("configId"):
            raise TradingViewRejected(409, "The approved strategy configuration is no longer active")

        configuration = active["configuration"]
        execution = ExecutionPolicy.from_mapping(
            active.get("riskSettings"), whole_units=(alert.market == "NSE")
        )
        target_pct = float(configuration.get("target_pct", 1.0))
        target_price = round(alert.signalPrice * (1 + target_pct / 100), 4)
        stop_price = (
            round(alert.signalPrice * (1 - execution.stop_loss_pct / 100), 4)
            if execution.stop_loss_pct is not None
            else None
        )
        expires_at = (
            alert.candleTimestamp
            + timedelta(minutes=market_spec(alert.market).minutes(alert.timeframe) * execution.maximum_holding_bars)
            if execution.maximum_holding_bars
            else None
        )

        repository = self.signals()
        existing = repository.get_external_event(TRADINGVIEW_SOURCE, alert.eventId)
        if existing is not None:
            return {"accepted": True, "duplicate": True, "mode": deployment["mode"], "signal": existing}

        stored = repository.insert_new(
            market=alert.market,
            strategy_id=alert.strategyId,
            strategy_version=alert.strategyVersion,
            symbol=symbol,
            timeframe=alert.timeframe,
            candle_timestamp=alert.candleTimestamp,
            signal_type=alert.action,
            signal_price=alert.signalPrice,
            target_price=target_price,
            stop_price=stop_price,
            expires_at=expires_at,
            reasons=alert.reasons or ["TRADINGVIEW_ALERT"],
            indicators={
                **alert.indicators,
                "tradingViewSymbol": alert.symbol,
                "tradingViewTargetPrice": alert.targetPrice,
                "tradingViewStopPrice": alert.stopPrice,
                "exchange": alert.exchange,
            },
            configuration_snapshot=configuration,
            source=TRADINGVIEW_SOURCE,
            external_event_id=alert.eventId,
            received_at=now,
        )
        if stored is None:
            duplicate = repository.get_external_event(TRADINGVIEW_SOURCE, alert.eventId)
            if duplicate is not None:
                return {"accepted": True, "duplicate": True, "mode": deployment["mode"], "signal": duplicate}
            raise TradingViewRejected(409, "A signal already exists for this strategy candle")
        if deployment["mode"] == "PAPER":
            self.broker(alert.market).on_signal(stored)
        return {"accepted": True, "duplicate": False, "mode": deployment["mode"], "signal": stored}

    @staticmethod
    def _authenticate(supplied: str) -> None:
        expected = os.environ.get("TRADINGVIEW_WEBHOOK_KEY", "").strip()
        if not expected:
            raise TradingViewRejected(503, "TradingView webhook ingestion is not configured")
        if not hmac.compare_digest(supplied, expected):
            raise TradingViewRejected(401, "Invalid webhook credentials")


def resolve_watchlist_symbol(value: str, configured_symbols: Sequence[str]) -> str | None:
    """Resolve TradingView's exchange/ticker spelling only within the approved watchlist."""
    ticker = value.strip().upper().split(":", 1)[-1]
    compact = re.sub(r"[^A-Z0-9]", "", ticker)
    matches = [
        symbol
        for symbol in configured_symbols
        if ticker == symbol.upper() or compact == re.sub(r"[^A-Z0-9]", "", symbol.upper())
    ]
    return matches[0] if len(matches) == 1 else None
