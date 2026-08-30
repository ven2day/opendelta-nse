from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

from .analytics import summarize_trade_ledger
from .core import PLATFORM_VERSION, stable_id, utc_now_iso
from .factors import FactorEngine, FactorOutput, factor_pass_mask
from .market_data import FeatureCache, FeatureCacheKey, align_completed_timeframe, normalize_candles
from .strategy_adapters import StrategyAdapterRegistry, StrategySignal


Timeframe = Literal["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d"]
ResearchMode = Literal["EXACT", "TOURNAMENT", "FORWARD_SELECTION"]
RankingMetric = Literal["NET_PROFIT", "EXPECTANCY", "PROFIT_FACTOR", "MAX_DRAWDOWN", "RETURN_TO_DRAWDOWN", "STABILITY"]


class ResearchExperimentRequestV2(BaseModel):
    researchVersion: Literal["2"] = "2"
    mode: ResearchMode = "EXACT"
    market: Literal["NSE", "CRYPTO"] = "NSE"
    provider: Literal["DHAN", "OKX", "VALR"] = "DHAN"
    baseStrategyId: str = Field(default="rsi_recovery", min_length=1, max_length=100)
    strategyParameters: dict[str, Any] = Field(default_factory=dict)
    symbols: list[str] = Field(default_factory=list, max_length=1_000)
    universeId: str | None = Field(default=None, min_length=1, max_length=120)
    startDate: date
    endDate: date
    contextTimeframe: Timeframe | None = None
    setupTimeframe: Timeframe | None = None
    executionTimeframe: Timeframe | None = None
    direction: Literal["LONG", "SHORT", "BOTH"] = "LONG"
    factorSelections: list[str] = Field(default_factory=list, max_length=40)
    factorParameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    advancedMultiplePerFamily: bool = False
    entryExecution: Literal["NEXT_BAR_OPEN"] = "NEXT_BAR_OPEN"
    collisionPolicy: Literal["STOP_FIRST"] = "STOP_FIRST"
    targetPct: float = Field(default=0.5, gt=0, le=100)
    stopLossPct: float = Field(default=0.5, gt=0, le=100)
    maximumHoldingBars: int = Field(default=50, ge=1, le=100_000)
    maximumTradesPerDay: int = Field(default=5, ge=1, le=1_000)
    maximumOpenPositions: int = Field(default=2, ge=1, le=100)
    oneOpenPositionPerSymbol: bool = True
    stopAfterFirstLoss: bool = False
    maximumDailyLossPct: float = Field(default=2.0, gt=0, le=100)
    quantityPerTrade: int = Field(default=1, ge=1, le=10_000_000)
    capitalPerPosition: float = Field(default=100_000, gt=0, le=1_000_000_000)
    totalCapital: float = Field(default=1_000_000, gt=0, le=10_000_000_000)
    riskPerTradePct: float = Field(default=0.0, ge=0, le=100)
    buyCostBps: float = Field(default=0.0, ge=0, le=10_000)
    sellCostBps: float = Field(default=0.0, ge=0, le=10_000)
    slippageBpsPerSide: float = Field(default=0.0, ge=0, le=10_000)
    sessionSquareOff: bool = False
    minimumTrades: int = Field(default=30, ge=1, le=100_000)
    trainingFraction: float = Field(default=0.6, gt=0, lt=1)
    validationFraction: float = Field(default=0.2, gt=0, lt=1)
    testFraction: float = Field(default=0.2, gt=0, lt=1)
    beamWidth: int = Field(default=2, ge=1, le=3)
    rankingMetric: RankingMetric = "EXPECTANCY"
    maximumSelectionDrawdownPct: float = Field(default=25.0, gt=0, le=100)
    dataVersion: str | None = Field(default=None, max_length=160)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            symbol = value.strip().upper()
            if not symbol or len(symbol) > 80 or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._&:-" for character in symbol):
                raise ValueError("Symbols contain an invalid provider identifier")
            if symbol not in normalized:
                normalized.append(symbol)
        return normalized

    @model_validator(mode="after")
    def validate_contract(self) -> "ResearchExperimentRequestV2":
        if bool(self.symbols) == bool(self.universeId):
            raise ValueError("Provide either symbols or one frozen universeId")
        if self.startDate >= self.endDate:
            raise ValueError("startDate must be before endDate")
        if (self.endDate - self.startDate).days > 1_500:
            raise ValueError("Research date range is too large for an interactive experiment")
        if self.market == "NSE" and self.provider != "DHAN":
            raise ValueError("NSE research requires DHAN")
        if self.market == "CRYPTO" and self.provider == "DHAN":
            raise ValueError("Crypto research requires OKX or VALR")
        if self.contextTimeframe is None:
            self.contextTimeframe = "15m" if self.market == "NSE" else "1h"
        if self.setupTimeframe is None:
            self.setupTimeframe = "5m" if self.market == "NSE" else "15m"
        if self.executionTimeframe is None:
            self.executionTimeframe = "1m" if self.market == "NSE" else "5m"
        if not math.isclose(self.trainingFraction + self.validationFraction + self.testFraction, 1.0, abs_tol=1e-9):
            raise ValueError("Training, validation and test fractions must sum to 1")
        if self.capitalPerPosition > self.totalCapital:
            raise ValueError("capitalPerPosition cannot exceed totalCapital")
        if len(set(self.factorSelections)) != len(self.factorSelections):
            raise ValueError("Factor selections must be unique")
        if set(self.factorParameters).difference(self.factorSelections):
            raise ValueError("Factor parameters may only reference selected factors")
        return self

    @property
    def symbol(self) -> str:
        return self.symbols[0] if self.symbols else str(self.universeId)

    @property
    def timeframe(self) -> str:
        return str(self.executionTimeframe)

    @property
    def durationYears(self) -> int:
        return max(1, math.ceil((self.endDate - self.startDate).days / 366))

    @property
    def durationDays(self) -> int:
        return max(1, (self.endDate - self.startDate).days)

    def snapshot(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class SplitBoundaries:
    training: tuple[pd.Timestamp, pd.Timestamp]
    validation: tuple[pd.Timestamp, pd.Timestamp]
    test: tuple[pd.Timestamp, pd.Timestamp]

    def public(self) -> dict[str, Any]:
        return {
            name: {"start": bounds[0].isoformat(), "endExclusive": bounds[1].isoformat()}
            for name, bounds in (("training", self.training), ("validation", self.validation), ("test", self.test))
        }


@dataclass
class SymbolFrames:
    symbol: str
    frames: dict[str, pd.DataFrame]
    quality: dict[str, dict[str, Any]]
    data_reference: str


def _finite_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return pd.Timestamp(value).isoformat()
    return value


def candle_data_reference(market: str, provider: str, symbol_frames: dict[str, dict[str, pd.DataFrame]]) -> str:
    digest = hashlib.sha256()
    digest.update(f"research-data-v2|{market}|{provider}".encode())
    for symbol in sorted(symbol_frames):
        for timeframe in sorted(symbol_frames[symbol]):
            digest.update(f"|{symbol}|{timeframe}|".encode())
            frame = symbol_frames[symbol][timeframe]
            canonical = frame[["timestamp", "open", "high", "low", "close", "volume"]].copy()
            canonical["timestamp"] = pd.to_datetime(canonical["timestamp"], utc=True).astype(str)
            digest.update(canonical.to_csv(index=False, float_format="%.12g", lineterminator="\n").encode())
    return f"sha256:{digest.hexdigest()}"


def chronological_boundaries(timestamps: list[pd.Timestamp], training: float, validation: float) -> SplitBoundaries:
    ordered = sorted(set(pd.to_datetime(timestamps, utc=True)))
    if len(ordered) < 5:
        raise ValueError("At least five completed setup candles are required for chronological splitting")
    train_index = max(1, min(len(ordered) - 3, int(len(ordered) * training)))
    validation_index = max(train_index + 1, min(len(ordered) - 1, int(len(ordered) * (training + validation))))
    end = ordered[-1] + pd.Timedelta(nanoseconds=1)
    return SplitBoundaries(
        training=(ordered[0], ordered[train_index]),
        validation=(ordered[train_index], ordered[validation_index]),
        test=(ordered[validation_index], end),
    )


def _session(timestamp: pd.Timestamp, market: str) -> str:
    stamp = pd.Timestamp(timestamp).tz_convert("Asia/Kolkata" if market == "NSE" else "UTC")
    if market == "CRYPTO":
        label = "ASIA_UTC" if stamp.hour < 8 else "EUROPE_UTC" if stamp.hour < 16 else "AMERICAS_UTC"
        return f"{label}_{'WEEKEND' if stamp.dayofweek >= 5 else 'WEEKDAY'}"
    minute = stamp.hour * 60 + stamp.minute
    if stamp.dayofweek >= 5 or not 555 <= minute <= 930:
        return "CLOSED_SESSION"
    return "NSE_OPEN" if minute < 630 else "NSE_MID" if minute < 780 else "NSE_CLOSE"


class DeterministicTradeEngine:
    def run(
        self,
        request: ResearchExperimentRequestV2,
        frames: dict[str, pd.DataFrame],
        signals: list[StrategySignal],
        bounds: tuple[pd.Timestamp, pd.Timestamp],
        rejected: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        start, end = bounds
        pending: dict[tuple[pd.Timestamp, str], list[StrategySignal]] = defaultdict(list)
        rejected_signals = list(rejected or [])
        for signal in signals:
            stamp = pd.Timestamp(signal.signal_timestamp)
            if not start <= stamp < end:
                continue
            frame = frames[signal.symbol]
            entry_index = int(frame["timestamp"].searchsorted(stamp, side="right"))
            if entry_index >= len(frame):
                rejected_signals.append({"symbol": signal.symbol, "signalTimestamp": stamp.isoformat(), "reason": "NO_NEXT_EXECUTION_BAR"})
                continue
            pending[(pd.Timestamp(frame.loc[entry_index, "timestamp"]), signal.symbol)].append(signal)

        timeline = sorted(
            (pd.Timestamp(row.timestamp), symbol, int(index))
            for symbol, frame in frames.items()
            for index, row in frame.iterrows()
            if start < pd.Timestamp(row.timestamp) <= end
        )
        positions: dict[str, dict[str, Any]] = {}
        closed: list[dict[str, Any]] = []
        daily: dict[date, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "losses": 0})
        committed = 0.0
        equity_curve = [{"timestamp": start.isoformat(), "equity": request.totalCapital}]

        def finish(position: dict[str, Any], row: pd.Series, index: int, reason: str, raw_exit: float) -> None:
            nonlocal committed
            side = position["side"]
            slip = request.slippageBpsPerSide / 10_000
            exit_price = raw_exit * (1 - slip if side == "LONG" else 1 + slip)
            quantity = position["quantity"]
            direction = 1 if side == "LONG" else -1
            gross_pnl = direction * (exit_price - position["entryPrice"]) * quantity
            entry_rate = request.buyCostBps if side == "LONG" else request.sellCostBps
            exit_rate = request.sellCostBps if side == "LONG" else request.buyCostBps
            costs = position["entryPrice"] * quantity * entry_rate / 10_000 + exit_price * quantity * exit_rate / 10_000
            net_pnl = gross_pnl - costs
            net_return = net_pnl / position["capital"]
            stamp = pd.Timestamp(row["timestamp"])
            trade = {
                **position,
                "status": "CLOSED",
                "exitTimestamp": stamp.isoformat(),
                "exitPrice": exit_price,
                "exitReason": reason,
                "holdingBars": index - position["entryIndex"] + 1,
                "holdingMinutes": (stamp - position["entryTimestamp"]).total_seconds() / 60,
                "grossPnl": gross_pnl,
                "costs": costs,
                "netPnl": net_pnl,
                "grossReturn": gross_pnl / position["capital"],
                "costRate": costs / position["capital"],
                "netReturn": net_return,
                "mae": position["mae"],
                "mfe": position["mfe"],
            }
            closed.append(trade)
            day = stamp.tz_convert("Asia/Kolkata" if request.market == "NSE" else "UTC").date()
            daily[day]["pnl"] += net_pnl
            if net_pnl < 0:
                daily[day]["losses"] += 1
            committed -= position["capital"]
            positions.pop(position["tradeId"], None)
            equity_curve.append({"timestamp": stamp.isoformat(), "equity": request.totalCapital + sum(item["netPnl"] for item in closed)})

        for stamp, symbol, index in timeline:
            frame = frames[symbol]
            row = frame.loc[index]
            symbol_positions = [
                position
                for position in list(positions.values())
                if position["symbol"] == symbol
            ]
            for existing in symbol_positions:
                entry = existing["entryPrice"]
                if existing["side"] == "LONG":
                    existing["mae"] = min(existing["mae"], (float(row["low"]) - entry) / entry)
                    existing["mfe"] = max(existing["mfe"], (float(row["high"]) - entry) / entry)
                    if float(row["open"]) <= existing["stopPrice"]:
                        finish(existing, row, index, "STOP_GAP", float(row["open"])); existing = None
                    elif float(row["open"]) >= existing["targetPrice"]:
                        finish(existing, row, index, "TARGET_GAP", float(row["open"])); existing = None
                    elif float(row["low"]) <= existing["stopPrice"]:
                        finish(existing, row, index, "STOP_FIRST_COLLISION" if float(row["high"]) >= existing["targetPrice"] else "STOP_EXIT", existing["stopPrice"]); existing = None
                    elif float(row["high"]) >= existing["targetPrice"]:
                        finish(existing, row, index, "TARGET_EXIT", existing["targetPrice"]); existing = None
                else:
                    existing["mae"] = min(existing["mae"], (entry - float(row["high"])) / entry)
                    existing["mfe"] = max(existing["mfe"], (entry - float(row["low"])) / entry)
                    if float(row["open"]) >= existing["stopPrice"]:
                        finish(existing, row, index, "STOP_GAP", float(row["open"])); existing = None
                    elif float(row["open"]) <= existing["targetPrice"]:
                        finish(existing, row, index, "TARGET_GAP", float(row["open"])); existing = None
                    elif float(row["high"]) >= existing["stopPrice"]:
                        finish(existing, row, index, "STOP_FIRST_COLLISION" if float(row["low"]) <= existing["targetPrice"] else "STOP_EXIT", existing["stopPrice"]); existing = None
                    elif float(row["low"]) <= existing["targetPrice"]:
                        finish(existing, row, index, "TARGET_EXIT", existing["targetPrice"]); existing = None
                if existing is not None and index - existing["entryIndex"] + 1 >= request.maximumHoldingBars:
                    finish(existing, row, index, "TIME_EXIT", float(row["close"])); existing = None
                if existing is not None and request.sessionSquareOff and request.market == "NSE":
                    local = stamp.tz_convert("Asia/Kolkata")
                    if local.time() >= time(15, 25):
                        finish(existing, row, index, "SESSION_SQUARE_OFF", float(row["close"])); existing = None

            for signal in pending.get((stamp, symbol), []):
                local_day = stamp.tz_convert("Asia/Kolkata" if request.market == "NSE" else "UTC").date()
                day = daily[local_day]
                reason = None
                if request.oneOpenPositionPerSymbol and any(position["symbol"] == symbol for position in positions.values()):
                    reason = "ONE_OPEN_POSITION_PER_SYMBOL"
                elif len(positions) >= request.maximumOpenPositions:
                    reason = "MAXIMUM_OPEN_POSITIONS"
                elif day["trades"] >= request.maximumTradesPerDay:
                    reason = "MAXIMUM_TRADES_PER_DAY"
                elif request.stopAfterFirstLoss and day["losses"]:
                    reason = "STOP_AFTER_FIRST_LOSS"
                elif day["pnl"] <= -(request.totalCapital * request.maximumDailyLossPct / 100):
                    reason = "MAXIMUM_DAILY_LOSS"
                if reason:
                    rejected_signals.append({"symbol": symbol, "signalTimestamp": signal.signal_timestamp.isoformat(), "reason": reason})
                    continue
                slip = request.slippageBpsPerSide / 10_000
                entry = float(row["open"]) * (1 + slip if signal.side == "LONG" else 1 - slip)
                stop = entry * (1 - request.stopLossPct / 100 if signal.side == "LONG" else 1 + request.stopLossPct / 100)
                target = entry * (1 + request.targetPct / 100 if signal.side == "LONG" else 1 - request.targetPct / 100)
                realized_equity = request.totalCapital + sum(float(trade["netPnl"]) for trade in closed)
                maximum_notional = min(request.capitalPerPosition, realized_equity - committed)
                quantity = min(request.quantityPerTrade, int(maximum_notional // entry))
                if request.riskPerTradePct > 0:
                    risk_budget = request.totalCapital * request.riskPerTradePct / 100
                    quantity = min(quantity, int(risk_budget // abs(entry - stop)))
                if quantity < 1:
                    rejected_signals.append({"symbol": symbol, "signalTimestamp": signal.signal_timestamp.isoformat(), "reason": "CAPITAL_LIMIT"})
                    continue
                capital = entry * quantity
                trade_id = stable_id("research-trade", {"symbol": symbol, "signal": signal.signal_timestamp.isoformat(), "side": signal.side, "sequence": len(closed) + len(positions)})
                positions[trade_id] = {
                    "tradeId": trade_id,
                    "symbol": symbol,
                    "side": signal.side,
                    "signalTimestamp": signal.signal_timestamp.isoformat(),
                    "entryTimestamp": stamp,
                    "entryIndex": index,
                    "entryPrice": entry,
                    "stopPrice": stop,
                    "targetPrice": target,
                    "quantity": quantity,
                    "capital": capital,
                    "session": _session(stamp, request.market),
                    "explanation": signal.explanation,
                    "mae": 0.0,
                    "mfe": 0.0,
                }
                committed += capital
                day["trades"] += 1
                # The entry bar occurs after its open and is therefore eligible for conservative stop/target handling.
                opened = positions[trade_id]
                opened["mae"] = min(0.0, (float(row["low"]) - entry) / entry if signal.side == "LONG" else (entry - float(row["high"])) / entry)
                opened["mfe"] = max(0.0, (float(row["high"]) - entry) / entry if signal.side == "LONG" else (entry - float(row["low"])) / entry)
                if signal.side == "LONG" and float(row["low"]) <= stop:
                    finish(opened, row, index, "STOP_FIRST_COLLISION" if float(row["high"]) >= target else "STOP_EXIT", stop)
                elif signal.side == "LONG" and float(row["high"]) >= target:
                    finish(opened, row, index, "TARGET_EXIT", target)
                elif signal.side == "SHORT" and float(row["high"]) >= stop:
                    finish(opened, row, index, "STOP_FIRST_COLLISION" if float(row["low"]) <= target else "STOP_EXIT", stop)
                elif signal.side == "SHORT" and float(row["low"]) <= target:
                    finish(opened, row, index, "TARGET_EXIT", target)

        open_positions = [
            {
                **position,
                "entryTimestamp": position["entryTimestamp"].isoformat(),
                "status": "OPEN",
                "netReturn": None,
                "netPnl": None,
                "exitTimestamp": None,
                "exitReason": "OPEN",
            }
            for position in sorted(positions.values(), key=lambda item: (item["entryTimestamp"], item["symbol"]))
        ]
        metrics = summarize_trade_ledger(closed, minimum_trades=request.minimumTrades)
        metrics.update(
            grossProfitCurrency=sum(max(0.0, float(trade["grossPnl"])) for trade in closed),
            grossLossCurrency=abs(sum(min(0.0, float(trade["grossPnl"])) for trade in closed)),
            totalCostsCurrency=sum(float(trade["costs"]) for trade in closed),
            netProfitCurrency=sum(float(trade["netPnl"]) for trade in closed),
            openTrades=len(open_positions),
            rejectedSignals=len(rejected_signals),
        )
        return _finite_json({
            "status": metrics["status"],
            "metrics": metrics,
            "tradeLedger": closed,
            "openPositions": open_positions,
            "rejectedSignals": rejected_signals,
            "equityCurve": equity_curve,
        })


class ResearchEngineV2:
    def __init__(
        self,
        candle_loader: Callable[[Any], pd.DataFrame],
        factor_engine: FactorEngine | None = None,
        feature_cache: FeatureCache | None = None,
        universe_resolver: Callable[[str], list[str]] | None = None,
    ) -> None:
        self.candle_loader = candle_loader
        self.factor_engine = factor_engine or FactorEngine()
        self.feature_cache = feature_cache
        self.universe_resolver = universe_resolver
        self.adapters = StrategyAdapterRegistry()
        self.trade_engine = DeterministicTradeEngine()

    def symbols(self, request: ResearchExperimentRequestV2) -> list[str]:
        if request.symbols:
            return request.symbols
        if request.market != "NSE":
            raise ValueError("UNSUPPORTED_DATA_REQUIREMENT: frozen crypto universes are not configured")
        if self.universe_resolver is None:
            raise ValueError("UNSUPPORTED_DATA_REQUIREMENT: frozen universe resolver is not configured")
        symbols = self.universe_resolver(str(request.universeId))
        if not symbols:
            raise ValueError("Frozen universe is empty or unavailable")
        return symbols

    def validate(self, request: ResearchExperimentRequestV2) -> None:
        adapter = self.adapters.get(request.baseStrategyId)
        metadata = adapter.metadata
        if request.market not in metadata.supported_markets or str(request.setupTimeframe) not in metadata.supported_timeframes:
            raise ValueError("Base strategy is incompatible with the selected market or setup timeframe")
        requested_directions = {"LONG", "SHORT"} if request.direction == "BOTH" else {request.direction}
        if request.market == "NSE" and requested_directions != {"LONG"}:
            raise ValueError("NSE Research V2 supports LONG trades only")
        if not requested_directions.issubset(metadata.supported_directions):
            raise ValueError("Base strategy does not support the selected direction")
        families: dict[str, list[str]] = defaultdict(list)
        for factor_id in request.factorSelections:
            definition = self.factor_engine.registry.get(factor_id)
            if request.market not in definition.supported_markets:
                raise ValueError(f"{factor_id} is incompatible with {request.market}")
            if definition.family in metadata.locked_factor_families:
                raise ValueError(f"{definition.family} is locked by base strategy {request.baseStrategyId}")
            families[definition.family].append(factor_id)
            calculation = self.factor_engine.calculation_parameters(factor_id, request.factorParameters.get(factor_id))
            self.factor_engine.parameters(definition, calculation)
            dummy = FactorOutput(definition, "SUPPORTED", pd.Series([1.0]))
            factor_pass_mask(dummy, request.factorParameters.get(factor_id), target_pct=request.targetPct, stop_loss_pct=request.stopLossPct)
        if request.mode == "EXACT" and not request.advancedMultiplePerFamily:
            duplicates = [family for family, rows in families.items() if len(rows) > 1]
            if duplicates:
                raise ValueError("Exact mode allows OFF or one candidate per factor family")
        if request.mode == "TOURNAMENT" and (not families or len(families) != 1):
            raise ValueError("Tournament mode must compare candidates from exactly one family")

    def estimate(self, request: ResearchExperimentRequestV2) -> dict[str, Any]:
        self.validate(request)
        symbols = self.symbols(request)
        family_counts: dict[str, int] = defaultdict(int)
        for factor_id in request.factorSelections:
            family_counts[self.factor_engine.registry.get(factor_id).family] += 1
        possible = 1
        for count in family_counts.values():
            possible *= count + 1
        possible = max(1, possible - 1)
        planned = 1 if request.mode == "EXACT" else len(request.factorSelections)
        if request.mode == "FORWARD_SELECTION":
            planned = min(100, request.beamWidth * max(1, len(request.factorSelections)) * max(1, len(family_counts)))
        days = (request.endDate - request.startDate).days
        timeframe_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "6h": 360, "1d": 1440}
        session_minutes = 375 if request.market == "NSE" else 1440
        candle_estimate = len(symbols) * days * max(1, session_minutes // timeframe_minutes[str(request.executionTimeframe)])
        return {
            "researchVersion": "2",
            "mode": request.mode,
            "symbols": len(symbols),
            "candlesEstimate": candle_estimate,
            "plannedFactorCalculations": len(symbols) * len(request.factorSelections),
            "plannedBacktests": planned,
            "possibleCombinations": possible,
            "beamWidth": request.beamWidth,
            "bounded": planned <= 100,
        }

    def _load(self, request: ResearchExperimentRequestV2, symbols: list[str], cancel: Callable[[], None]) -> tuple[dict[str, SymbolFrames], list[dict[str, Any]]]:
        loaded: dict[str, SymbolFrames] = {}
        failed = []
        timeframes = {str(request.contextTimeframe), str(request.setupTimeframe), str(request.executionTimeframe)}
        for chunk_start in range(0, len(symbols), 25):
            for symbol in symbols[chunk_start : chunk_start + 25]:
                cancel()
                frames: dict[str, pd.DataFrame] = {}
                quality: dict[str, dict[str, Any]] = {}
                try:
                    for timeframe in sorted(timeframes):
                        child = request.model_copy(update={"symbols": [symbol], "executionTimeframe": timeframe})
                        source = self.candle_loader(child).copy()
                        if "timestamp" not in source:
                            source = source.reset_index().rename(columns={source.index.name or "index": "timestamp"})
                        normalized, report = normalize_candles(
                            source,
                            timeframe=timeframe,
                            now=datetime.combine(request.endDate, time.max, tzinfo=UTC),
                            timestamp_represents="CLOSE",
                            market=request.market,
                        )
                        if normalized.empty:
                            raise ValueError(f"No completed {timeframe} candles")
                        frames[timeframe] = normalized
                        quality[timeframe] = report.public()
                    reference = candle_data_reference(request.market, request.provider, {symbol: frames})
                    loaded[symbol] = SymbolFrames(symbol, frames, quality, reference)
                except Exception as error:
                    failed.append({"symbol": symbol, "reason": str(error), "errorType": type(error).__name__})
        return loaded, failed

    def _factor_values(
        self,
        request: ResearchExperimentRequestV2,
        data: SymbolFrames,
        factor_id: str,
    ) -> tuple[pd.Series | None, dict[str, Any] | None]:
        definition = self.factor_engine.registry.get(factor_id)
        role_timeframe = str(request.contextTimeframe if definition.entry_role == "CONTEXT" else request.executionTimeframe if definition.entry_role == "EXECUTION" else request.setupTimeframe)
        frame = data.frames[role_timeframe]
        calculation = self.factor_engine.calculation_parameters(factor_id, request.factorParameters.get(factor_id))
        key = FeatureCacheKey(
            market=request.market,
            symbol=data.symbol,
            provider=request.provider,
            data_version=data.data_reference,
            date_range=(request.startDate.isoformat(), request.endDate.isoformat()),
            timeframe=role_timeframe,
            factor_id=factor_id,
            factor_version=definition.version,
            parameters=calculation,
            benchmark_dependency=request.dataVersion if "benchmark_close" in definition.required_data else None,
            sector_dependency=request.dataVersion if "sector_close" in definition.required_data else None,
            session_calendar_version="NSE-SESSION-2026.1" if request.market == "NSE" else "CRYPTO-UTC-24X7-1",
        )
        cached = self.feature_cache.get(key) if self.feature_cache else None
        if cached is None:
            output = self.factor_engine.calculate(
                factor_id,
                frame,
                market=request.market,
                timeframe=role_timeframe,
                parameters=calculation,
            )
            if output.status != "SUPPORTED" or output.values is None:
                return None, {"factorId": factor_id, "status": output.status, "reason": output.reason}
            values = output.values
            payload = [None if pd.isna(value) else value.item() if hasattr(value, "item") else value for value in values]
            if self.feature_cache:
                self.feature_cache.put(key, payload)
        else:
            output = FactorOutput(definition, "SUPPORTED", pd.Series(cached, index=frame.index))
        mask = factor_pass_mask(output, request.factorParameters.get(factor_id), target_pct=request.targetPct, stop_loss_pct=request.stopLossPct)
        setup = data.frames[str(request.setupTimeframe)]
        if role_timeframe == str(request.setupTimeframe):
            return mask.reset_index(drop=True), None
        aligned = align_completed_timeframe(
            setup[["timestamp"]],
            pd.DataFrame({"timestamp": frame["timestamp"], "pass": mask.astype("boolean")}),
            prefix="factor_",
            market=request.market,
        )
        return aligned["factor_pass"].fillna(False).astype(bool), None

    def _signals(self, request: ResearchExperimentRequestV2, loaded: dict[str, SymbolFrames], factor_ids: list[str]) -> tuple[list[StrategySignal], list[dict[str, Any]], list[dict[str, Any]]]:
        adapter = self.adapters.get(request.baseStrategyId)
        accepted: list[StrategySignal] = []
        rejected: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        for symbol, data in sorted(loaded.items()):
            setup = data.frames[str(request.setupTimeframe)]
            signals = adapter.signals(symbol, setup, str(request.setupTimeframe), request.strategyParameters, request.direction)
            masks = []
            for factor_id in factor_ids:
                mask, problem = self._factor_values(request, data, factor_id)
                if problem:
                    unsupported.append({"symbol": symbol, **problem})
                elif mask is not None:
                    masks.append((factor_id, mask))
            if any(row["symbol"] == symbol for row in unsupported):
                continue
            for signal in signals:
                failed = [factor_id for factor_id, mask in masks if signal.signal_index >= len(mask) or not bool(mask.iloc[signal.signal_index])]
                if failed:
                    rejected.append({"symbol": symbol, "signalTimestamp": signal.signal_timestamp.isoformat(), "reason": "FACTOR_FILTER_REJECTED", "factorIds": failed})
                else:
                    accepted.append(signal)
        return accepted, rejected, unsupported

    def _evaluation(self, request: ResearchExperimentRequestV2, loaded: dict[str, SymbolFrames], factor_ids: list[str], bounds: tuple[pd.Timestamp, pd.Timestamp]) -> dict[str, Any]:
        signals, rejected, unsupported = self._signals(request, loaded, factor_ids)
        if unsupported:
            return {"status": "UNSUPPORTED_DATA_REQUIREMENT", "factorIds": factor_ids, "unsupported": unsupported, "metrics": None, "tradeLedger": [], "openPositions": [], "rejectedSignals": rejected}
        execution = {symbol: data.frames[str(request.executionTimeframe)] for symbol, data in loaded.items()}
        result = self.trade_engine.run(request, execution, signals, bounds, rejected)
        return {"factorIds": factor_ids, **result, "unsupported": []}

    @staticmethod
    def _objective(result: dict[str, Any], metric: RankingMetric) -> float:
        metrics = result.get("metrics") or {}
        mapping = {
            "NET_PROFIT": metrics.get("netProfitCurrency"),
            "EXPECTANCY": metrics.get("expectancy"),
            "PROFIT_FACTOR": metrics.get("profitFactor"),
            "MAX_DRAWDOWN": -abs(float(metrics.get("maximumDrawdown") or 0.0)),
            "RETURN_TO_DRAWDOWN": metrics.get("returnToDrawdown"),
            "STABILITY": (metrics.get("monthlyStability") or {}).get("positiveRate"),
        }
        value = mapping[metric]
        return float(value) if value is not None and math.isfinite(float(value)) else -math.inf

    @staticmethod
    def _comparison(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        left, right = baseline.get("metrics") or {}, candidate.get("metrics") or {}
        fields = ("tradeCount", "netProfitCurrency", "expectancy", "profitFactor", "maximumDrawdown", "mae", "mfe", "averageHoldingMinutes")
        delta = {}
        for field in fields:
            before, after = left.get(field), right.get(field)
            delta[field] = None if before is None or after is None else float(after) - float(before)
        return _finite_json(delta)

    @staticmethod
    def _passes_selection_guardrails(
        request: ResearchExperimentRequestV2, result: dict[str, Any]
    ) -> bool:
        metrics = result.get("metrics") or {}
        if metrics.get("status") != "CONCLUSIVE":
            return False
        drawdown = metrics.get("maximumDrawdown")
        if drawdown is None or not math.isfinite(float(drawdown)):
            return False
        return abs(float(drawdown)) * 100 <= request.maximumSelectionDrawdownPct

    def run(self, request: ResearchExperimentRequestV2, progress: Callable[[float], None], cancel: Callable[[], None]) -> dict[str, Any]:
        self.validate(request)
        estimate = self.estimate(request)
        symbols = self.symbols(request)
        progress(5)
        loaded, failed = self._load(request, symbols, cancel)
        if not loaded:
            raise ValueError("No requested symbol could be loaded")
        all_timestamps = [pd.Timestamp(value) for data in loaded.values() for value in data.frames[str(request.setupTimeframe)]["timestamp"]]
        split = chronological_boundaries(all_timestamps, request.trainingFraction, request.validationFraction)
        progress(25)
        baseline_training = self._evaluation(request, loaded, [], split.training)
        baseline_validation = self._evaluation(request, loaded, [], split.validation)
        evaluated: list[dict[str, Any]] = []
        selected: list[str] = []
        final_test_results: list[dict[str, Any]] = []

        if request.mode == "EXACT":
            selected = list(request.factorSelections)
            training = self._evaluation(request, loaded, selected, split.training)
            validation = self._evaluation(request, loaded, selected, split.validation)
            evaluated.append({"phase": "EXACT", "training": training, "validation": validation, "validationDelta": self._comparison(baseline_validation, validation)})
            final_test_results.append(self._evaluation(request, loaded, selected, split.test))
        elif request.mode == "TOURNAMENT":
            for index, factor_id in enumerate(request.factorSelections):
                cancel()
                training = self._evaluation(request, loaded, [factor_id], split.training)
                validation = self._evaluation(request, loaded, [factor_id], split.validation)
                test = self._evaluation(request, loaded, [factor_id], split.test)
                evaluated.append({
                    "phase": "TOURNAMENT", "factorIds": [factor_id], "training": training, "validation": validation,
                    "validationDelta": self._comparison(baseline_validation, validation), "finalTest": test,
                    "finalTestDelta": self._comparison(self._evaluation(request, loaded, [], split.test), test),
                })
                progress(25 + (index + 1) / max(1, len(request.factorSelections)) * 55)
            conclusive = [
                row
                for row in evaluated
                if self._passes_selection_guardrails(request, row["validation"])
            ]
            if conclusive:
                selected = list(max(conclusive, key=lambda row: self._objective(row["validation"], request.rankingMetric))["factorIds"])
        else:
            beam: list[tuple[list[str], dict[str, Any]]] = [([], baseline_validation)]
            visited = {()}
            while True:
                cancel()
                candidates: list[tuple[list[str], dict[str, Any], dict[str, Any]]] = []
                for current, current_result in beam:
                    used = {self.factor_engine.registry.get(item).family for item in current}
                    for factor_id in request.factorSelections:
                        family = self.factor_engine.registry.get(factor_id).family
                        configuration = tuple(sorted([*current, factor_id]))
                        if family in used or configuration in visited:
                            continue
                        visited.add(configuration)
                        training = self._evaluation(request, loaded, list(configuration), split.training)
                        validation = self._evaluation(request, loaded, list(configuration), split.validation)
                        record = {"phase": "FORWARD_SELECTION", "factorIds": list(configuration), "training": training, "validation": validation, "validationDelta": self._comparison(baseline_validation, validation)}
                        evaluated.append(record)
                        if not self._passes_selection_guardrails(request, validation):
                            continue
                        if self._objective(validation, request.rankingMetric) > self._objective(current_result, request.rankingMetric):
                            candidates.append((list(configuration), validation, record))
                if not candidates:
                    break
                candidates.sort(key=lambda row: (self._objective(row[1], request.rankingMetric), tuple(row[0])), reverse=True)
                beam = [(row[0], row[1]) for row in candidates[: request.beamWidth]]
                selected = beam[0][0]
            final_test_results.append(self._evaluation(request, loaded, selected, split.test))

        progress(90)
        combined_reference = candle_data_reference(request.market, request.provider, {symbol: data.frames for symbol, data in loaded.items()})
        coverage_status = "COMPLETE" if not failed else "PARTIAL"
        if len(loaded) * 2 < len(symbols):
            coverage_status = "FAILED_INSUFFICIENT_SYMBOL_COVERAGE"
        cache = self.feature_cache.health() if self.feature_cache else {"status": "DISABLED"}
        result = {
            "researchVersion": "2",
            "researchValidity": "RESEARCH_V2_REAL_TRADE_LIFECYCLE",
            "experimentId": stable_id("research-v2-experiment", {"configuration": request.snapshot(), "dataReference": combined_reference}),
            "platformVersion": PLATFORM_VERSION,
            "generatedAt": utc_now_iso(),
            "configuration": request.snapshot(),
            "configurationId": stable_id("research-v2-configuration", request.snapshot()),
            "dataReference": combined_reference,
            "requestedDataVersion": request.dataVersion,
            "split": split.public(),
            "estimate": estimate,
            "baseStrategy": self.adapters.get(request.baseStrategyId).metadata.public(),
            "baselineTraining": baseline_training,
            "baselineValidation": baseline_validation,
            "evaluatedConfigurations": evaluated,
            "selectedFactorIds": selected,
            "untouchedTestResult": final_test_results[0] if len(final_test_results) == 1 else None,
            "tournamentFinalTests": final_test_results if len(final_test_results) > 1 else [],
            "symbolCoverage": {
                "status": coverage_status,
                "requestedSymbols": symbols,
                "processedSymbols": sorted(loaded),
                "failedSymbols": failed,
                "candleCounts": {symbol: {timeframe: len(frame) for timeframe, frame in data.frames.items()} for symbol, data in loaded.items()},
            },
            "cacheMetrics": cache,
            "warnings": [
                "Research results are paper-only and are not live-trading approval",
                "NSE price factors require corporate-action-adjusted provider data for cross-action comparisons",
            ],
            "paperOnly": True,
            "liveOrdersEnabled": False,
        }
        progress(100)
        return _finite_json(result)
