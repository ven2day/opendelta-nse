from __future__ import annotations

import asyncio
import concurrent.futures
import io
import math
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from atr_exit_optimizer import (
    AtrOptimizationGrid,
    evaluate_atr_exit_grid,
)
from live_signals import (
    LiveSignalEngine,
    LiveSignalRepository,
    LiveSignalSettings,
)
from market_data_refresh import MarketDataRefreshService
from main import (
    DEFAULT_SYMBOLS_FILE,
    IST,
    DhanAPIError,
    DhanClient,
    DhanConfig,
    calculate_rsi,
    download_instrument_master,
    historical_payload_to_frame,
    load_symbols,
)
from recovery_backtest import (
    QUALITY_WEIGHTS,
    RecoveryConfig,
    aggregate_recovery_results,
    simulate_recovery_symbol,
)
from recovery_backtest import (
    STRATEGY_VERSION as RECOVERY_STRATEGY_VERSION,
)
from recovery_feature_analysis import (
    REPORT_FILENAMES,
    build_feature_analysis,
    filter_feature_snapshots,
    load_feature_analysis,
    load_feature_snapshots,
)
from recovery_dynamic_exit import (
    DYNAMIC_EXIT_VERSION,
    DynamicExitConfig,
    aggregate_dynamic_exit_results,
    simulate_dynamic_exit_symbol,
)
from recovery_position_backtest import (
    POSITION_BACKTEST_VERSION,
    PositionProtectionConfig,
    aggregate_protected_results,
    simulate_protected_recovery_symbol,
)
from universe_selection import (
    DEFAULT_MAXIMUM_PRICE,
    DEFAULT_MINIMUM_BUY_OBSERVATIONS,
    DEFAULT_MINIMUM_PRICE,
    DEFAULT_TOP_N,
    RANKING_LABELS,
    UniverseRepository,
    UniverseSelectionConfig,
    UniverseService,
    signal_count_distribution,
)

INITIAL_CAPITAL = 100_000.0
RSI_PERIOD = 14
VARIABLE_FEE_RATE = 0.00111
FIXED_FEE_PER_ORDER = 20.0
SLIPPAGE_RATE = 0.0005
MINIMUM_NET_PROFIT_PCT = 1.0
MAX_SYMBOLS_PER_RUN = 10
MAX_CHART_POINTS = 360
MAX_EVENTS = 300
INTRADAY_CHUNK_DAYS = 89
CACHE_TTL_SECONDS = 60 * 60
NIFTY_DISPLAY_NAME = "NIFTY 50"


@dataclass(frozen=True)
class TimeframeSpec:
    source: Literal["daily", "intraday"]
    source_interval: str | None
    minutes: int | None
    resample_minutes: int | None = None


TIMEFRAMES: dict[str, TimeframeSpec] = {
    "5m": TimeframeSpec("intraday", "5", 5),
    "15m": TimeframeSpec("intraday", "15", 15),
    "30m": TimeframeSpec("intraday", "15", 15, 30),
    "1h": TimeframeSpec("intraday", "60", 60),
    "2h": TimeframeSpec("intraday", "60", 60, 120),
    "4h": TimeframeSpec("intraday", "60", 60, 240),
    "1d": TimeframeSpec("daily", None, None),
}

ANNUALIZATION = {
    "5m": 252 * 75,
    "15m": 252 * 25,
    "30m": 252 * 13,
    "1h": 252 * 6.25,
    "2h": 252 * 3.125,
    "4h": 252 * 1.6,
    "1d": 252,
}


class BacktestRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS_PER_RUN)
    strategyMode: Literal["rsi_range", "rsi_recovery"] = "rsi_range"
    universeMode: Literal["selected", "all"] = "selected"
    runId: str | None = Field(default=None, min_length=1, max_length=80)
    durationYears: Literal[1, 3] = 1
    timeframe: Literal["5m", "15m", "30m", "1h", "2h", "4h", "1d"] = "1d"
    entryLow: float = Field(default=20, ge=0, le=100)
    entryHigh: float = Field(default=30, ge=0, le=100)
    exitLow: float = Field(default=50, ge=0, le=100)
    exitHigh: float = Field(default=70, ge=0, le=100)
    rsiLength: int = Field(default=14, gt=0, le=500)
    rsiArmLow: float = Field(default=30, ge=0, le=100)
    rsiArmHigh: float = Field(default=40, ge=0, le=100)
    rsiRecovery: float = Field(default=40, ge=0, le=100)
    emaEnabled: bool = True
    emaFast: int = Field(default=9, gt=0, le=500)
    emaSlow: int = Field(default=20, gt=0, le=500)
    vwapEnabled: bool = True
    volumeEnabled: bool = True
    volumeEma: int = Field(default=20, gt=0, le=500)
    minimumConfirmations: int = Field(default=2, ge=0, le=3)
    targetPct: float = Field(default=0.5, gt=0, le=100)
    setupExpiryBars: int = Field(default=50, ge=0, le=100_000)
    executionModel: Literal["SIGNAL_CLOSE", "NEXT_BAR_OPEN"] = "SIGNAL_CLOSE"
    buyCostBps: float = Field(default=0, ge=0, le=10_000)
    sellCostBps: float = Field(default=0, ge=0, le=10_000)
    slippageBps: float = Field(default=0, ge=0, le=10_000)
    exitModel: Literal[
        "LEGACY_FIXED_TARGET", "FIXED_TP_SL", "ATR_DYNAMIC_TP_SL"
    ] = "LEGACY_FIXED_TARGET"
    exitProtectionEnabled: bool = False
    fixedStopLossPct: float = Field(default=1.0, gt=0, le=100)
    atrLength: int = Field(default=14, ge=1, le=500)
    stopAtrMultiplier: float = Field(default=1.25, gt=0, le=100)
    rewardRiskRatio: float = Field(default=1.5, gt=0, le=100)
    minimumStopPct: float = Field(default=0.75, gt=0, le=100)
    maximumStopPct: float = Field(default=3.0, gt=0, le=100)
    positionSizing: Literal["FIXED_QUANTITY", "RISK_BUDGET"] = "FIXED_QUANTITY"
    quantityPerTrade: int = Field(default=50, ge=1, le=1_000_000)
    rupeeRiskBudget: float = Field(default=2_500, gt=0, le=1_000_000_000)
    maximumQuantity: int = Field(default=10_000, ge=1, le=1_000_000)
    maximumCapitalPerPosition: float = Field(
        default=1_000_000, gt=0, le=100_000_000_000
    )
    maxOpenLotsPerSymbol: int = Field(default=1, ge=1, le=1_000)
    maxHoldingTradingDays: int = Field(default=5, ge=1, le=1_000)
    timeExit: Literal["NEXT_TRADING_SESSION_OPEN"] = "NEXT_TRADING_SESSION_OPEN"

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, symbols: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(symbol.strip().upper().removesuffix(".NS") for symbol in symbols))
        if any(not symbol or not symbol.replace("&", "").replace("-", "").isalnum() for symbol in normalized):
            raise ValueError("Symbols may only contain letters, numbers, ampersands, and hyphens")
        return normalized

    @model_validator(mode="after")
    def validate_strategy_parameters(self) -> "BacktestRequest":
        if self.strategyMode == "rsi_range":
            if self.exitProtectionEnabled or self.exitModel != "LEGACY_FIXED_TARGET":
                raise ValueError("Position exit models are available only for RSI Recovery")
            if not self.entryLow < self.entryHigh < self.exitLow < self.exitHigh:
                raise ValueError("RSI ranges must be ordered: entry low < entry high < exit low < exit high")
            return self

        if (
            self.exitProtectionEnabled
            and "exitModel" not in self.model_fields_set
            and "targetPct" not in self.model_fields_set
        ):
            self.targetPct = 0.51

        if self.exitModel != "LEGACY_FIXED_TARGET":
            self.exitProtectionEnabled = True
        if self.minimumStopPct > self.maximumStopPct:
            raise ValueError("Minimum stop percentage cannot exceed maximum stop percentage")

        if not self.rsiArmLow < self.rsiArmHigh:
            raise ValueError("RSI arm low must be lower than RSI arm high")
        enabled = sum((self.emaEnabled, self.vwapEnabled, self.volumeEnabled))
        if self.minimumConfirmations > enabled:
            raise ValueError(
                f"Minimum confirmations cannot exceed the {enabled} enabled confirmation filters"
            )
        return self

    def recovery_config(self) -> RecoveryConfig:
        return RecoveryConfig(
            rsi_length=self.rsiLength,
            rsi_arm_low=self.rsiArmLow,
            rsi_arm_high=self.rsiArmHigh,
            rsi_recovery=self.rsiRecovery,
            ema_enabled=self.emaEnabled,
            ema_fast=self.emaFast,
            ema_slow=self.emaSlow,
            vwap_enabled=self.vwapEnabled,
            volume_enabled=self.volumeEnabled,
            volume_ema=self.volumeEma,
            minimum_confirmations=self.minimumConfirmations,
            target_pct=self.targetPct,
            setup_expiry_bars=self.setupExpiryBars,
            execution_model=self.executionModel,
            buy_cost_bps=self.buyCostBps,
            sell_cost_bps=self.sellCostBps,
            slippage_bps=self.slippageBps,
        )

    def protection_config(self) -> PositionProtectionConfig:
        return PositionProtectionConfig(
            enabled=self.exitProtectionEnabled,
            quantity_per_trade=self.quantityPerTrade,
            max_open_lots_per_symbol=self.maxOpenLotsPerSymbol,
            max_holding_sessions=self.maxHoldingTradingDays,
            time_exit=self.timeExit,
        )

    def resolved_exit_model(self) -> str:
        if (
            self.exitProtectionEnabled
            and self.exitModel == "LEGACY_FIXED_TARGET"
            and "exitModel" not in self.model_fields_set
        ):
            return "LEGACY_PROTECTED_TARGET"
        return self.exitModel

    def dynamic_exit_config(self) -> DynamicExitConfig:
        model = self.resolved_exit_model()
        if model not in {"FIXED_TP_SL", "ATR_DYNAMIC_TP_SL"}:
            raise ValueError("Dynamic exit configuration requested for a legacy exit model")
        return DynamicExitConfig(
            exit_model=model,
            fixed_take_profit_pct=self.targetPct,
            fixed_stop_loss_pct=self.fixedStopLossPct,
            atr_length=self.atrLength,
            stop_atr_multiplier=self.stopAtrMultiplier,
            reward_risk_ratio=self.rewardRiskRatio,
            minimum_stop_pct=self.minimumStopPct,
            maximum_stop_pct=self.maximumStopPct,
            max_holding_sessions=self.maxHoldingTradingDays,
            max_open_lots_per_symbol=self.maxOpenLotsPerSymbol,
            position_sizing=self.positionSizing,
            quantity_per_trade=self.quantityPerTrade,
            rupee_risk_budget=self.rupeeRiskBudget,
            maximum_quantity=self.maximumQuantity,
            maximum_capital_per_position=self.maximumCapitalPerPosition,
        )


class AtrOptimizationRequest(BacktestRequest):
    symbols: list[str] = Field(min_length=1, max_length=750)
    strategyMode: Literal["rsi_recovery"] = "rsi_recovery"
    exitModel: Literal["ATR_DYNAMIC_TP_SL"] = "ATR_DYNAMIC_TP_SL"
    atrLengths: list[int] = Field(default=[14], min_length=1, max_length=20)
    stopAtrMultipliers: list[float] = Field(
        default=[0.75, 1.0, 1.25, 1.5, 2.0], min_length=1, max_length=20
    )
    rewardRiskRatios: list[float] = Field(
        default=[1.0, 1.25, 1.5, 2.0], min_length=1, max_length=20
    )
    maxHoldingSessionsGrid: list[int] = Field(
        default=[1, 3, 5], min_length=1, max_length=20
    )
    minimumStopPcts: list[float] = Field(
        default=[0.5, 0.75, 1.0], min_length=1, max_length=20
    )
    maximumStopPcts: list[float] = Field(
        default=[2.0, 3.0, 5.0], min_length=1, max_length=20
    )
    minimumValidationTrades: int = Field(default=20, ge=1, le=1_000_000)

    @field_validator(
        "atrLengths", "maxHoldingSessionsGrid"
    )
    @classmethod
    def positive_integer_grid(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("ATR lengths and holding-session grid values must be positive")
        return values

    @field_validator(
        "stopAtrMultipliers", "rewardRiskRatios", "minimumStopPcts", "maximumStopPcts"
    )
    @classmethod
    def positive_float_grid(cls, values: list[float]) -> list[float]:
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("ATR optimization grid values must be finite and positive")
        return values

    @model_validator(mode="after")
    def validate_grid_size(self) -> "AtrOptimizationRequest":
        combinations = (
            len(set(self.atrLengths))
            * len(set(self.stopAtrMultipliers))
            * len(set(self.rewardRiskRatios))
            * len(set(self.maxHoldingSessionsGrid))
            * len(set(self.minimumStopPcts))
            * len(set(self.maximumStopPcts))
        )
        if combinations > 5_000:
            raise ValueError("ATR optimization grid is limited to 5,000 configurations per run")
        return self

    def optimization_grid(self) -> AtrOptimizationGrid:
        return AtrOptimizationGrid(
            atr_lengths=tuple(self.atrLengths),
            stop_atr_multipliers=tuple(self.stopAtrMultipliers),
            reward_risk_ratios=tuple(self.rewardRiskRatios),
            max_holding_sessions=tuple(self.maxHoldingSessionsGrid),
            minimum_stop_pcts=tuple(self.minimumStopPcts),
            maximum_stop_pcts=tuple(self.maximumStopPcts),
        )


class RecoveryAnalysisQuery(BaseModel):
    symbol: str | None = Field(default=None, max_length=40)
    timeframe: Literal["5m", "15m", "30m", "1h", "2h", "4h", "1d"] | None = None
    dateFrom: date | None = None
    dateTo: date | None = None
    confirmationCombination: str | None = Field(default=None, max_length=80)
    timeOfDayBucket: Literal["OPENING", "MORNING", "MIDDAY", "AFTERNOON", "LATE"] | None = None
    targetOutcome: Literal[
        "FAST_30M", "FAST_2H", "SAME_DAY", "SLOW", "TRAPPED", "GOOD", "BAD", "NEUTRAL"
    ] | None = None

    @field_validator("symbol")
    @classmethod
    def normalize_optional_symbol(cls, symbol: str | None) -> str | None:
        if symbol is None:
            return None
        normalized = symbol.strip().upper().removesuffix(".NS")
        if not normalized or not normalized.replace("&", "").replace("-", "").isalnum():
            raise ValueError("Symbol may only contain letters, numbers, ampersands, and hyphens")
        return normalized

    @model_validator(mode="after")
    def validate_dates(self) -> "RecoveryAnalysisQuery":
        if self.dateFrom and self.dateTo and self.dateFrom > self.dateTo:
            raise ValueError("Feature analysis start date must not be after the end date")
        return self


class LiveUniverseRequest(BaseModel):
    topN: int = Field(default=DEFAULT_TOP_N, ge=1, le=750)
    minimumPrice: float = Field(default=DEFAULT_MINIMUM_PRICE, ge=0)
    maximumPrice: float = Field(default=DEFAULT_MAXIMUM_PRICE, gt=0)
    rankingMode: Literal[
        "QUALITY", "GOOD_RATE", "TARGET_SPEED", "LOW_MAE", "TARGET_HIT_RATE"
    ] = "QUALITY"
    minimumBuyObservations: int = Field(
        default=DEFAULT_MINIMUM_BUY_OBSERVATIONS, ge=1, le=1_000_000
    )
    manualPins: list[str] = Field(default_factory=list, max_length=750)
    manualExclusions: list[str] = Field(default_factory=list, max_length=750)
    minimumGoodRate: float | None = Field(default=None, ge=0, le=100)
    maximumOpenRate: float | None = Field(default=None, ge=0, le=100)
    maximumMedianTargetMinutes: float | None = Field(default=None, gt=0)
    minimumTargetHitRate: float | None = Field(default=None, ge=0, le=100)
    minimumMedianMaePct: float | None = Field(default=None, ge=-100, le=0)
    dynamicPriceFilter: bool = False

    @field_validator("manualPins", "manualExclusions")
    @classmethod
    def normalize_universe_symbols(cls, symbols: list[str]) -> list[str]:
        normalized = list(
            dict.fromkeys(symbol.strip().upper().removesuffix(".NS") for symbol in symbols)
        )
        if any(
            not symbol or not symbol.replace("&", "").replace("-", "").isalnum()
            for symbol in normalized
        ):
            raise ValueError("Symbols may only contain letters, numbers, ampersands, and hyphens")
        return normalized

    @model_validator(mode="after")
    def validate_live_universe(self) -> "LiveUniverseRequest":
        if self.maximumPrice <= self.minimumPrice:
            raise ValueError("Maximum share price must be greater than minimum share price")
        overlap = sorted(set(self.manualPins) & set(self.manualExclusions))
        if overlap:
            raise ValueError("A symbol cannot be both pinned and excluded: " + ", ".join(overlap))
        if self.dynamicPriceFilter:
            raise ValueError("Dynamic price filtering is reserved for a future study and is not enabled")
        return self

    def selection_config(self) -> UniverseSelectionConfig:
        return UniverseSelectionConfig(
            top_n=self.topN,
            minimum_price=self.minimumPrice,
            maximum_price=self.maximumPrice,
            ranking_mode=self.rankingMode,
            minimum_buy_observations=self.minimumBuyObservations,
            manual_pins=tuple(self.manualPins),
            manual_exclusions=tuple(self.manualExclusions),
            minimum_good_rate=self.minimumGoodRate,
            maximum_open_rate=self.maximumOpenRate,
            maximum_median_target_minutes=self.maximumMedianTargetMinutes,
            minimum_target_hit_rate=self.minimumTargetHitRate,
            minimum_median_mae_pct=self.minimumMedianMaePct,
            dynamic_price_filter=self.dynamicPriceFilter,
        )


class LiveUniverseSaveRequest(LiveUniverseRequest):
    configurationHash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class LiveSignalSettingsRequest(BaseModel):
    entryRangeMethod: Literal["FIXED_PERCENT", "ATR_BASED"] = "FIXED_PERCENT"
    fixedLowerPct: float = Field(default=0.15, ge=0, le=10)
    fixedUpperPct: float = Field(default=0.10, ge=0, le=10)
    atrLowerMultiplier: float = Field(default=0.25, ge=0, le=10)
    atrUpperMultiplier: float = Field(default=0.15, ge=0, le=10)
    paperAllocation: float = Field(default=25_000, gt=0, le=100_000_000)
    staleDataSeconds: int = Field(default=90, ge=10, le=3_600)
    freshMinutes: int = Field(default=15, ge=1, le=1_440)
    recentMinutes: int = Field(default=60, ge=2, le=10_080)
    supportLookbackShort: int = Field(default=20, ge=2, le=500)
    supportLookbackLong: int = Field(default=50, ge=2, le=1_000)

    @model_validator(mode="after")
    def validate_live_settings(self) -> "LiveSignalSettingsRequest":
        if self.freshMinutes >= self.recentMinutes:
            raise ValueError("Fresh signal minutes must be lower than recent signal minutes")
        if self.supportLookbackShort > self.supportLookbackLong:
            raise ValueError("Short support lookback cannot exceed long support lookback")
        return self

    def settings(self) -> LiveSignalSettings:
        return LiveSignalSettings(
            entry_range_method=self.entryRangeMethod,
            fixed_lower_pct=self.fixedLowerPct,
            fixed_upper_pct=self.fixedUpperPct,
            atr_lower_multiplier=self.atrLowerMultiplier,
            atr_upper_multiplier=self.atrUpperMultiplier,
            paper_allocation=self.paperAllocation,
            stale_data_seconds=self.staleDataSeconds,
            fresh_minutes=self.freshMinutes,
            recent_minutes=self.recentMinutes,
            support_lookback_short=self.supportLookbackShort,
            support_lookback_long=self.supportLookbackLong,
        ).validate()


class LiveSignalDecisionRequest(BaseModel):
    action: Literal["WATCH", "IGNORE", "NO_ACTION"]
    reason: Literal[
        "Near resistance",
        "Weak support",
        "Price ran away",
        "Market weak",
        "Already holding similar stock",
        "Manual chart rejection",
        "Other",
    ] | None = None
    notes: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_ignore_reason(self) -> "LiveSignalDecisionRequest":
        if self.action == "IGNORE" and self.reason is None:
            raise ValueError("Choose an ignore reason")
        return self


class PaperBuyRequest(BaseModel):
    actualEntryPrice: float = Field(gt=0, le=10_000_000)
    actualQuantity: int = Field(gt=0, le=10_000_000)
    notes: str | None = Field(default=None, max_length=1_000)


class PaperCloseRequest(BaseModel):
    actualExitPrice: float = Field(gt=0, le=10_000_000)
    notes: str | None = Field(default=None, max_length=1_000)


def _finite(value: Any, digits: int = 4) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return round(numeric, digits)


def _iso_ist(value: pd.Timestamp | datetime | None) -> str | None:
    if value is None or pd.isna(value):
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(IST)
    else:
        stamp = stamp.tz_convert(IST)
    return stamp.isoformat()


def _fee(turnover: float) -> float:
    return turnover * VARIABLE_FEE_RATE + FIXED_FEE_PER_ORDER


def _entry_signal(rsi: pd.Series, low: float, high: float) -> pd.Series:
    inside = rsi.between(low, high, inclusive="both").fillna(False)
    return inside & ~inside.shift(1, fill_value=False)


def _resample_session(frame: pd.DataFrame, target_minutes: int, base_minutes: int) -> pd.DataFrame:
    if frame.empty:
        return frame

    pieces: list[pd.DataFrame] = []
    rule = f"{target_minutes}min"
    for _, session in frame.groupby(frame.index.date):
        session = session.sort_index()
        session_date = session.index[0].date()
        origin = pd.Timestamp(datetime.combine(session_date, datetime_time(9, 15)), tz=IST)
        aggregated = session.resample(
            rule,
            origin=origin,
            label="right",
            closed="left",
        ).agg(
            {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum",
            }
        )
        aggregated = aggregated.dropna(subset=["Open", "High", "Low", "Close"])
        if not aggregated.empty:
            exchange_close = pd.Timestamp(
                datetime.combine(session_date, datetime_time(15, 30)),
                tz=IST,
            )
            actual_session_end = min(
                session.index[-1] + pd.Timedelta(minutes=base_minutes),
                exchange_close,
            )
            adjusted_index = [min(stamp, actual_session_end) for stamp in aggregated.index]
            aggregated.index = pd.DatetimeIndex(adjusted_index)
            pieces.append(aggregated)

    if not pieces:
        return pd.DataFrame(columns=frame.columns)
    result = pd.concat(pieces).sort_index()
    return result[~result.index.duplicated(keep="last")]


def prepare_candles(
    frame: pd.DataFrame,
    timeframe: str,
    analysis_start: datetime,
    now_ist: datetime,
    warmup_bars: int = 0,
) -> pd.DataFrame:
    spec = TIMEFRAMES[timeframe]
    required = ["Open", "High", "Low", "Close", "Volume"]
    if frame.empty or any(column not in frame.columns for column in required):
        return pd.DataFrame(columns=[*required, "RSI"])

    data = frame[required].apply(pd.to_numeric, errors="coerce").dropna(subset=required[:4])
    data = data[(data["Open"] > 0) & (data["High"] > 0) & (data["Low"] > 0) & (data["Close"] > 0)]
    data = data.sort_index()

    if spec.source == "intraday" and spec.minutes:
        latest_complete_start = pd.Timestamp(now_ist) - pd.Timedelta(minutes=spec.minutes)
        data = data[data.index <= latest_complete_start]
        if spec.resample_minutes:
            data = _resample_session(data, spec.resample_minutes, spec.minutes)
        else:
            data.index = data.index + pd.Timedelta(minutes=spec.minutes)
    elif now_ist.time() < datetime_time(15, 31):
        data = data[data.index.date < now_ist.date()]

    if data.empty:
        return pd.DataFrame(columns=[*required, "RSI"])
    data["RSI"] = calculate_rsi(data["Close"], RSI_PERIOD)
    analysis_position = int(data.index.searchsorted(pd.Timestamp(analysis_start), side="left"))
    output_position = max(analysis_position - max(warmup_bars, 0), 0)
    return data.iloc[output_position:].copy()


def _sample_indices(length: int, required: set[int]) -> list[int]:
    if length <= MAX_CHART_POINTS:
        return list(range(length))
    evenly_spaced = set(np.linspace(0, length - 1, MAX_CHART_POINTS, dtype=int).tolist())
    return sorted(evenly_spaced | {index for index in required if 0 <= index < length})


def simulate_symbol(
    symbol: str,
    candles: pd.DataFrame,
    *,
    timeframe: str,
    entry_low: float,
    entry_high: float,
    exit_low: float,
    exit_high: float,
    nifty_return_pct: float | None,
) -> dict[str, Any]:
    if len(candles) < RSI_PERIOD + 2:
        raise ValueError("Not enough candles to calculate RSI and execute trades")

    rsi = candles["RSI"]
    entry_signals = _entry_signal(rsi, entry_low, entry_high)
    high_events = _entry_signal(rsi, exit_low, exit_high)
    cash = INITIAL_CAPITAL
    quantity = 0
    entry: dict[str, Any] | None = None
    pending_entry: dict[str, Any] | None = None
    pending_exit: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    event_indices: set[int] = set()
    action_by_index: dict[int, str] = {}
    decision_by_index: dict[int, dict[str, Any]] = {}
    held_exit_signals = 0
    equities: list[float] = []

    for index in range(len(candles)):
        row = candles.iloc[index]
        next_row = candles.iloc[index + 1] if index + 1 < len(candles) else None

        if pending_exit is not None and entry is not None and quantity > 0:
            execution_price = float(row["Open"]) * (1 - SLIPPAGE_RATE)
            turnover = quantity * execution_price
            exit_fee = _fee(turnover)
            net_pnl = (turnover - exit_fee) - entry["capital"]
            return_pct = net_pnl / entry["capital"] * 100
            decision = {
                "entryPrice": _finite(entry["price"], 2),
                "candidateExitPrice": _finite(execution_price, 2),
                "netReturnPct": _finite(return_pct, 2),
                "requiredNetProfitPct": MINIMUM_NET_PROFIT_PCT,
                "estimatedFees": _finite(entry["fee"] + exit_fee, 2),
                "signalRsi": _finite(pending_exit["rsi"], 2),
            }

            if return_pct >= MINIMUM_NET_PROFIT_PCT:
                cash += turnover - exit_fee
                trades.append(
                    {
                        "status": "closed",
                        "entrySignalTime": _iso_ist(entry["signal_time"]),
                        "entryTime": _iso_ist(entry["time"]),
                        "entryRsi": _finite(entry["rsi"], 2),
                        "entryPrice": _finite(entry["price"], 2),
                        "exitSignalTime": _iso_ist(pending_exit["signal_time"]),
                        "exitTime": _iso_ist(candles.index[index]),
                        "exitRsi": _finite(pending_exit["rsi"], 2),
                        "exitPrice": _finite(execution_price, 2),
                        "quantity": quantity,
                        "holdingBars": index - entry["index"],
                        "netPnl": _finite(net_pnl, 2),
                        "returnPct": _finite(return_pct, 2),
                        "fees": _finite(entry["fee"] + exit_fee, 2),
                    }
                )
                action_by_index[index] = "sell"
                decision_by_index[index] = {
                    **decision,
                    "reason": "High RSI and the estimated net profit is at least 1% after fees and slippage.",
                }
                quantity = 0
                entry = None
            else:
                held_exit_signals += 1
                action_by_index[index] = "hold"
                decision_by_index[index] = {
                    **decision,
                    "reason": "High RSI, but the estimated net profit is below 1% after fees and slippage. Position kept open.",
                }
                event_indices.add(index)
            pending_exit = None

        if pending_entry is not None and entry is None and quantity == 0:
            execution_price = float(row["Open"]) * (1 + SLIPPAGE_RATE)
            affordable = math.floor(
                (cash - FIXED_FEE_PER_ORDER)
                / (execution_price * (1 + VARIABLE_FEE_RATE))
            )
            if affordable > 0:
                turnover = affordable * execution_price
                entry_fee = _fee(turnover)
                cash -= turnover + entry_fee
                quantity = affordable
                entry = {
                    "signal_index": pending_entry["signal_index"],
                    "index": index,
                    "signal_time": pending_entry["signal_time"],
                    "time": candles.index[index],
                    "rsi": pending_entry["rsi"],
                    "price": execution_price,
                    "fee": entry_fee,
                    "turnover": turnover,
                    "capital": turnover + entry_fee,
                }
                action_by_index[index] = "buy"
                decision_by_index[index] = {
                    "entryPrice": _finite(execution_price, 2),
                    "candidateExitPrice": None,
                    "netReturnPct": None,
                    "requiredNetProfitPct": MINIMUM_NET_PROFIT_PCT,
                    "estimatedFees": _finite(entry_fee, 2),
                    "signalRsi": _finite(pending_entry["rsi"], 2),
                    "reason": "Low-RSI signal executed at the next candle open with buy-side fees and slippage.",
                }
            pending_entry = None

        equities.append(cash + quantity * float(row["Close"]))

        if bool(entry_signals.iloc[index]) or bool(high_events.iloc[index]):
            range_name = "entry" if bool(entry_signals.iloc[index]) else "exit"
            events.append(
                {
                    "range": range_name,
                    "signalTime": _iso_ist(candles.index[index]),
                    "rsi": _finite(row["RSI"], 2),
                    "signalClose": _finite(row["Close"], 2),
                    "nextCandleTime": _iso_ist(candles.index[index + 1]) if next_row is not None else None,
                    "nextOpen": _finite(next_row["Open"], 2) if next_row is not None else None,
                }
            )

        if (
            entry is None
            and quantity == 0
            and pending_entry is None
            and bool(entry_signals.iloc[index])
            and next_row is not None
        ):
            pending_entry = {
                "signal_index": index,
                "signal_time": candles.index[index],
                "rsi": float(row["RSI"]),
            }
            event_indices.update({index, index + 1})
        elif (
            entry is not None
            and quantity > 0
            and pending_exit is None
            and exit_low <= float(row["RSI"]) <= exit_high
            and next_row is not None
        ):
            pending_exit = {
                "signal_index": index,
                "signal_time": candles.index[index],
                "rsi": float(row["RSI"]),
            }
            event_indices.update({index, index + 1})

    open_position: dict[str, Any] | None = None
    if entry is not None and quantity > 0:
        final_price = float(candles.iloc[-1]["Close"]) * (1 - SLIPPAGE_RATE)
        final_turnover = quantity * final_price
        estimated_exit_fee = _fee(final_turnover)
        liquidation_value = cash + final_turnover - estimated_exit_fee
        unrealized_pnl = (final_turnover - estimated_exit_fee) - entry["capital"]
        equities[-1] = liquidation_value
        open_position = {
            "entrySignalTime": _iso_ist(entry["signal_time"]),
            "entryTime": _iso_ist(entry["time"]),
            "entryRsi": _finite(entry["rsi"], 2),
            "entryPrice": _finite(entry["price"], 2),
            "quantity": quantity,
            "lastPrice": _finite(final_price, 2),
            "unrealizedPnl": _finite(unrealized_pnl, 2),
            "estimatedReturnPct": _finite(unrealized_pnl / entry["capital"] * 100, 2),
        }

    equity = pd.Series(equities, index=candles.index, dtype=float)
    returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    annualization = ANNUALIZATION[timeframe]
    return_std = float(returns.std(ddof=0)) if not returns.empty else 0.0
    downside = returns[returns < 0]
    downside_std = float(downside.std(ddof=0)) if not downside.empty else 0.0
    sharpe = float(returns.mean() / return_std * math.sqrt(annualization)) if return_std > 0 else None
    sortino = float(returns.mean() / downside_std * math.sqrt(annualization)) if downside_std > 0 else None
    drawdown = equity / equity.cummax() - 1
    max_drawdown_pct = float(drawdown.min() * 100) if not drawdown.empty else 0.0
    ending_capital = float(equity.iloc[-1])
    total_return_pct = (ending_capital / INITIAL_CAPITAL - 1) * 100
    elapsed_days = max((candles.index[-1] - candles.index[0]).total_seconds() / 86_400, 1)
    cagr_pct = ((ending_capital / INITIAL_CAPITAL) ** (365.25 / elapsed_days) - 1) * 100 if ending_capital > 0 else -100.0

    trade_returns = [float(trade["returnPct"]) for trade in trades if trade["returnPct"] is not None]
    trade_pnls = [float(trade["netPnl"]) for trade in trades if trade["netPnl"] is not None]
    wins = [value for value in trade_returns if value > 0]
    losses = [value for value in trade_returns if value < 0]
    gross_profit = sum(value for value in trade_pnls if value > 0)
    gross_loss = abs(sum(value for value in trade_pnls if value < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (None if gross_profit == 0 else float("inf"))
    first_close = float(candles.iloc[0]["Close"])
    last_close = float(candles.iloc[-1]["Close"])
    buy_hold_return_pct = (last_close / first_close - 1) * 100

    sample = _sample_indices(len(candles), event_indices)
    chart = [
        {
            "time": _iso_ist(candles.index[index]),
            "close": _finite(candles.iloc[index]["Close"], 2),
            "rsi": _finite(candles.iloc[index]["RSI"], 2),
            "equity": _finite(equity.iloc[index], 2),
            "action": action_by_index.get(index),
            **decision_by_index.get(index, {}),
        }
        for index in sample
    ]

    if not trades:
        verdict = "no-trades"
    else:
        verdict = "profitable" if total_return_pct > 0 else "unprofitable"

    return {
        "symbol": symbol,
        "verdict": verdict,
        "firstCandle": _iso_ist(candles.index[0]),
        "lastCandle": _iso_ist(candles.index[-1]),
        "bars": len(candles),
        "closedTrades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": _finite(len(wins) / len(trades) * 100, 2) if trades else None,
        "strategyReturnPct": _finite(total_return_pct, 2),
        "cagrPct": _finite(cagr_pct, 2),
        "buyHoldReturnPct": _finite(buy_hold_return_pct, 2),
        "niftyReturnPct": _finite(nifty_return_pct, 2),
        "maxDrawdownPct": _finite(max_drawdown_pct, 2),
        "sharpe": _finite(sharpe, 2),
        "sortino": _finite(sortino, 2),
        "profitFactor": _finite(profit_factor, 2),
        "averageTradeReturnPct": _finite(np.mean(trade_returns), 2) if trade_returns else None,
        "averageWinPct": _finite(np.mean(wins), 2) if wins else None,
        "averageLossPct": _finite(np.mean(losses), 2) if losses else None,
        "endingCapital": _finite(ending_capital, 2),
        "heldExitSignals": held_exit_signals,
        "openPosition": open_position,
        "trades": trades,
        "events": events[-MAX_EVENTS:],
        "chart": chart,
    }


class HistoricalDataStore:
    def __init__(self, config: DhanConfig, cache_directory: Path) -> None:
        self.config = config
        self.client = DhanClient(config)
        self.cache_directory = cache_directory
        self._mapping_lock = threading.Lock()
        self._security_map: dict[str, str] | None = None
        self._nifty_security_id: str | None = None

    def universe(self) -> list[str]:
        return load_symbols(self.config.symbols_file)

    def _load_security_ids(self) -> None:
        if self._security_map is not None and self._nifty_security_id is not None:
            return
        with self._mapping_lock:
            if self._security_map is None:
                instruments = download_instrument_master(self.config.instrument_master_url)
                self._security_map = dict(
                    zip(instruments["symbol"], instruments["SEM_SMST_SECURITY_ID"], strict=False)
                )
            if self._nifty_security_id is None:
                request = Request(self.config.instrument_master_url, headers={"User-Agent": "vento-nse-backtest/1.0"})
                try:
                    with urlopen(request, timeout=60) as response:
                        instruments = pd.read_csv(io.BytesIO(response.read()), dtype=str).fillna("")
                except (HTTPError, URLError, TimeoutError) as error:
                    raise DhanAPIError("Unable to map the NIFTY 50 benchmark") from error
                matches = instruments[
                    (instruments["SEM_EXM_EXCH_ID"] == "NSE")
                    & (instruments["SEM_SEGMENT"] == "I")
                    & (instruments["SEM_INSTRUMENT_NAME"] == "INDEX")
                    & (instruments["SEM_CUSTOM_SYMBOL"].str.casefold() == "nifty 50")
                ]
                if matches.empty:
                    raise DhanAPIError("NIFTY 50 was not found in the Dhan instrument master")
                self._nifty_security_id = str(matches.iloc[0]["SEM_SMST_SECURITY_ID"])

    def security_id(self, symbol: str) -> str:
        self._load_security_ids()
        assert self._security_map is not None
        security_id = self._security_map.get(symbol)
        if not security_id:
            raise ValueError("Symbol is unavailable in the current Dhan instrument master")
        return security_id

    def _cache_path(self, symbol: str, source_interval: str, duration_years: int) -> Path:
        safe_symbol = "".join(character for character in symbol if character.isalnum() or character in "-&")
        return self.cache_directory / f"{safe_symbol}-{source_interval}-{duration_years}y.csv.gz"

    def _read_cache(self, path: Path) -> pd.DataFrame | None:
        try:
            age = datetime.now().timestamp() - path.stat().st_mtime
            if age > CACHE_TTL_SECONDS:
                return None
            frame = pd.read_csv(path, index_col="Timestamp", parse_dates=["Timestamp"])
            frame.index = pd.DatetimeIndex(frame.index)
            if frame.index.tz is None:
                frame.index = frame.index.tz_localize(IST)
            else:
                frame.index = frame.index.tz_convert(IST)
            return frame
        except (OSError, ValueError, KeyError, pd.errors.ParserError):
            return None

    def _write_cache(self, path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.stem}.", suffix=".csv.gz", delete=False) as handle:
                temporary = Path(handle.name)
            frame.to_csv(temporary, index_label="Timestamp", compression="gzip")
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _fetch_raw(
        self,
        security_id: str,
        spec: TimeframeSpec,
        fetch_start: datetime,
        now_ist: datetime,
        *,
        exchange_segment: str = "NSE_EQ",
        instrument: str = "EQUITY",
    ) -> pd.DataFrame:
        if spec.source == "daily":
            payload = self.client.historical_daily(
                security_id,
                fetch_start.date(),
                now_ist.date() + timedelta(days=1),
                exchange_segment=exchange_segment,
                instrument=instrument,
            )
            return historical_payload_to_frame(payload)

        assert spec.source_interval is not None
        chunks: list[pd.DataFrame] = []
        cursor = fetch_start
        while cursor < now_ist:
            chunk_end = min(cursor + timedelta(days=INTRADAY_CHUNK_DAYS), now_ist)
            payload = self.client.historical_intraday(
                security_id,
                spec.source_interval,
                cursor,
                chunk_end,
                exchange_segment=exchange_segment,
                instrument=instrument,
            )
            frame = historical_payload_to_frame(payload)
            if not frame.empty:
                chunks.append(frame)
            cursor = chunk_end
        if not chunks:
            return pd.DataFrame()
        result = pd.concat(chunks).sort_index()
        return result[~result.index.duplicated(keep="last")]

    def candles(
        self,
        symbol: str,
        timeframe: str,
        duration_years: int,
        analysis_start: datetime,
        now_ist: datetime,
        *,
        benchmark: bool = False,
        warmup_bars: int = 0,
    ) -> pd.DataFrame:
        spec = TIMEFRAMES[timeframe]
        source_key = spec.source_interval or "daily"
        cache_symbol = "NIFTY50" if benchmark else symbol
        cache_path = self._cache_path(cache_symbol, source_key, duration_years)
        raw = self._read_cache(cache_path)
        if raw is None:
            warmup_days = 90 if spec.source == "daily" else 14
            fetch_start = analysis_start - timedelta(days=warmup_days)
            if benchmark:
                self._load_security_ids()
                assert self._nifty_security_id is not None
                raw = self._fetch_raw(
                    self._nifty_security_id,
                    spec,
                    fetch_start,
                    now_ist,
                    exchange_segment="IDX_I",
                    instrument="INDEX",
                )
            else:
                raw = self._fetch_raw(self.security_id(symbol), spec, fetch_start, now_ist)
            if not raw.empty:
                self._write_cache(cache_path, raw)
        return prepare_candles(raw, timeframe, analysis_start, now_ist, warmup_bars=warmup_bars)


def run_recovery_backtest(
    request: BacktestRequest,
    store: HistoricalDataStore,
    now_ist: datetime | None = None,
) -> dict[str, Any]:
    started_clock = time.perf_counter()
    started_at = datetime.now(IST)
    now = (now_ist or started_at).astimezone(IST)
    analysis_start = now - timedelta(days=round(365.25 * request.durationYears))
    run_id = request.runId or str(uuid.uuid4())
    config = request.recovery_config()
    protection = request.protection_config()
    exit_model = request.resolved_exit_model()
    dynamic_exit = (
        request.dynamic_exit_config()
        if exit_model in {"FIXED_TP_SL", "ATR_DYNAMIC_TP_SL"}
        else None
    )
    legacy_protection = exit_model == "LEGACY_PROTECTED_TARGET"
    position_backtest = legacy_protection or dynamic_exit is not None

    universe = set(store.universe())
    unavailable = [symbol for symbol in request.symbols if symbol not in universe]
    if unavailable:
        raise ValueError("Symbols are not in symbols.csv: " + ", ".join(unavailable))

    warnings = [
        "RSI recovery is a mandatory gate; confirmation scoring includes EMA, session VWAP, and volume only.",
        "Signals use completed candles. SIGNAL_CLOSE is a research reference; NEXT_BAR_OPEN is the more live-realistic option.",
        "Target checks, MAE, and MFE start on the candle after entry; the entry candle high and low are excluded.",
        "Dhan historical candles are used as received. Corporate-action adjustment status is not explicit and has not been independently verified.",
        "Results use the current symbols.csv universe, so delisted securities are not represented (survivorship bias).",
        "Historical target achievement and signal quality do not establish live profitability.",
    ]
    if legacy_protection:
        warnings.extend([
            "Exit protection is ON: valid RSI Recovery signals become fixed-quantity positions subject to the configured per-symbol open-lot limit.",
            "There is no stop loss. Positions that miss the target through the configured holding sessions exit at the next available NSE session's first candle open.",
            "Skipped max-open-lot signals are preserved separately and never enter trade-profitability calculations.",
            "Position drawdown is exact per symbol; multi-symbol maximum drawdown is a conservative sum of independent symbol drawdowns rather than a cash-shared portfolio simulation.",
        ])
    elif dynamic_exit is not None:
        warnings.extend([
            f"{exit_model} is ON: valid RSI Recovery signals become positions with frozen take-profit and stop-loss levels.",
            "Gap exits use the candle open. If both stop and target are touched inside one OHLC candle, the conservative stop-first assumption is used.",
            "TP/SL monitoring begins after the entry candle. Time exits use actual NSE session dates and the next available session open.",
            "Skipped max-open-lot signals are preserved separately and never enter trade-profitability calculations.",
            "Position drawdown is exact per symbol; multi-symbol maximum drawdown is a conservative sum of independent symbol drawdowns rather than a cash-shared portfolio simulation.",
        ])
    else:
        warnings.extend([
            "There is no stop loss, no forced end-of-day exit, and no leverage. Signal observations remain open until target or dataset end.",
            "Every fresh RSI arm/recovery cycle is an independent signal observation, even while earlier observations for the same symbol remain open; this is not a portfolio-capital simulation.",
        ])
    requested_workers = int(os.environ.get("BACKTEST_WORKERS", "4"))
    worker_count = max(1, min(requested_workers, len(request.symbols), MAX_SYMBOLS_PER_RUN))
    warmup_bars = max(
        config.rsi_length + 2,
        config.ema_fast,
        config.ema_slow,
        config.volume_ema,
        dynamic_exit.atr_length if dynamic_exit is not None else 0,
    ) + 5

    def process(symbol: str) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
        try:
            candles = store.candles(
                symbol,
                request.timeframe,
                request.durationYears,
                analysis_start,
                now,
                warmup_bars=warmup_bars,
            )
            if legacy_protection:
                result = simulate_protected_recovery_symbol(
                    symbol,
                    candles,
                    timeframe=request.timeframe,
                    recovery_config=config,
                    protection_config=protection,
                    run_id=run_id,
                    analysis_start=analysis_start,
                )
            elif dynamic_exit is not None:
                result = simulate_dynamic_exit_symbol(
                    symbol,
                    candles,
                    timeframe=request.timeframe,
                    recovery_config=config,
                    exit_config=dynamic_exit,
                    run_id=run_id,
                    analysis_start=analysis_start,
                )
            else:
                result = simulate_recovery_symbol(
                    symbol,
                    candles,
                    timeframe=request.timeframe,
                    config=config,
                    run_id=run_id,
                    analysis_start=analysis_start,
                )
            return result, None
        except (DhanAPIError, ValueError, OSError, KeyError) as error:
            return None, {"symbol": symbol, "message": str(error)}

    if worker_count == 1:
        processed = [process(symbol) for symbol in request.symbols]
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="recovery-symbol",
        ) as executor:
            processed = list(executor.map(process, request.symbols))

    results = [result for result, _ in processed if result is not None]
    errors = [error for _, error in processed if error is not None]
    if not results:
        warnings.append(
            "No symbol in this batch produced enough valid historical data; the universe run continued to the next batch."
        )

    completed_at = datetime.now(IST)
    runtime_seconds = time.perf_counter() - started_clock
    data_from = min((result["firstCandle"] for result in results), default=None)
    data_to = max((result["lastCandle"] for result in results), default=None)
    if legacy_protection:
        summary = aggregate_protected_results(results)
    elif dynamic_exit is not None:
        summary = aggregate_dynamic_exit_results(results)
    else:
        summary = aggregate_recovery_results(results)
    public_exit_parameters = (
        dynamic_exit.public_parameters()
        if dynamic_exit is not None
        else protection.public_parameters()
    )
    public_exit_parameters["exitModel"] = exit_model
    return {
        "metadata": {
            "runId": run_id,
            "strategyMode": "rsi_recovery",
            "strategyVersion": RECOVERY_STRATEGY_VERSION,
            "startedAt": started_at.isoformat(),
            "completedAt": completed_at.isoformat(),
            "generatedAt": completed_at.isoformat(),
            "analysisStart": analysis_start.isoformat(),
            "dataFrom": data_from,
            "dataTo": data_to,
            "durationYears": request.durationYears,
            "timeframe": request.timeframe,
            "universeMode": request.universeMode,
            "symbolsRequested": len(request.symbols),
            "symbolsProcessed": len(results),
            "symbolsFailed": len(errors),
            "workerCount": worker_count,
            "runtimeSeconds": _finite(runtime_seconds, 4),
            "timezone": "Asia/Kolkata",
            "executionModel": config.execution_model,
            "strategyParameters": config.public_parameters(),
            "backtestSemantics": "POSITION" if position_backtest else "SIGNAL_OBSERVATION",
            "exitModel": exit_model,
            "exitProtection": public_exit_parameters,
            "positionBacktestVersion": (
                DYNAMIC_EXIT_VERSION
                if dynamic_exit is not None
                else POSITION_BACKTEST_VERSION if legacy_protection else None
            ),
            "costModel": {
                "buyCostBps": config.buy_cost_bps,
                "sellCostBps": config.sell_cost_bps,
                "slippageBpsPerSide": config.slippage_bps,
                "estimatedRoundTripCostPct": config.estimated_round_trip_cost_pct,
            },
            "qualityFormula": {
                "weights": QUALITY_WEIGHTS,
                "formula": "0.40*hit_rate_score + 0.30*speed_score + 0.20*mae_score + 0.10*(100-open_penalty)",
                "speedScore": "Completed targets score 100/75/40/10 for <=30m, <=2h, <=24h, and >24h.",
                "maeScore": "clamp(100 + median_completed_MAE_pct*10, 0, 100)",
                "openPenalty": "open_signal_observations / buy_signal_observations * 100",
            },
            "corporateActionAdjustment": "UNVERIFIED_SOURCE_AS_RECEIVED",
            "gitCommitSha": os.environ.get("GIT_COMMIT_SHA") or None,
        },
        "summary": summary,
        "results": results,
        "errors": errors,
        "warnings": warnings,
    }


def run_atr_exit_optimization(
    request: AtrOptimizationRequest,
    store: HistoricalDataStore,
    now_ist: datetime | None = None,
) -> dict[str, Any]:
    now = (now_ist or datetime.now(IST)).astimezone(IST)
    analysis_start = now - timedelta(days=round(365.25 * request.durationYears))
    universe = set(store.universe())
    unavailable = [symbol for symbol in request.symbols if symbol not in universe]
    if unavailable:
        raise ValueError("Symbols are not in symbols.csv: " + ", ".join(unavailable))
    grid = request.optimization_grid()
    warmup_bars = max(
        request.rsiLength + 2,
        request.emaFast,
        request.emaSlow,
        request.volumeEma,
        max(grid.atr_lengths),
    ) + 5
    requested_workers = int(os.environ.get("BACKTEST_WORKERS", "4"))
    worker_count = max(1, min(requested_workers, len(request.symbols), 8))

    def load(symbol: str) -> tuple[str, pd.DataFrame | None, dict[str, str] | None]:
        try:
            candles = store.candles(
                symbol,
                request.timeframe,
                request.durationYears,
                analysis_start,
                now,
                warmup_bars=warmup_bars,
            )
            return symbol, candles, None
        except (DhanAPIError, ValueError, OSError, KeyError) as error:
            return symbol, None, {"symbol": symbol, "message": str(error)}

    if worker_count == 1:
        loaded = [load(symbol) for symbol in request.symbols]
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="atr-optimizer-load",
        ) as executor:
            loaded = list(executor.map(load, request.symbols))
    symbol_candles = {
        symbol: candles
        for symbol, candles, error in loaded
        if error is None and candles is not None
    }
    errors = [error for _, _, error in loaded if error is not None]
    if not symbol_candles:
        raise ValueError("No selected symbol produced valid historical candles for optimization")
    payload = evaluate_atr_exit_grid(
        symbol_candles,
        timeframe=request.timeframe,
        recovery_config=request.recovery_config(),
        base_exit_config=request.dynamic_exit_config(),
        grid=grid,
        analysis_start=analysis_start,
        analysis_end=now,
        duration_years=request.durationYears,
        run_id=request.runId or str(uuid.uuid4()),
        minimum_validation_trades=request.minimumValidationTrades,
    )
    payload["metadata"].update({
        "strategyMode": "rsi_recovery",
        "strategyVersion": RECOVERY_STRATEGY_VERSION,
        "exitModel": "ATR_DYNAMIC_TP_SL",
        "executionModel": request.executionModel,
        "durationYears": request.durationYears,
        "analysisStart": analysis_start.isoformat(),
        "analysisEnd": now.isoformat(),
        "symbolsRequested": len(request.symbols),
        "symbolsProcessed": len(symbol_candles),
        "symbolsFailed": len(errors),
        "costModel": {
            "buyCostBps": request.buyCostBps,
            "sellCostBps": request.sellCostBps,
            "slippageBpsPerSide": request.slippageBps,
        },
    })
    payload["errors"] = errors
    return payload


def run_backtest(request: BacktestRequest, store: HistoricalDataStore, now_ist: datetime | None = None) -> dict[str, Any]:
    if request.strategyMode == "rsi_recovery":
        return run_recovery_backtest(request, store, now_ist)

    if not request.entryLow < request.entryHigh < request.exitLow < request.exitHigh:
        raise ValueError("RSI ranges must be ordered: entry low < entry high < exit low < exit high")

    universe = set(store.universe())
    unavailable = [symbol for symbol in request.symbols if symbol not in universe]
    if unavailable:
        raise ValueError("Symbols are not in symbols.csv: " + ", ".join(unavailable))

    now = (now_ist or datetime.now(IST)).astimezone(IST)
    analysis_start = now - timedelta(days=round(365.25 * request.durationYears))
    errors: list[dict[str, str]] = []
    warnings = [
        "Signals use candle-close RSI and execute at the next candle open to avoid look-ahead bias.",
        "High-RSI exits execute only when the estimated return is at least 1% net of entry and exit fees and slippage; otherwise the position remains open.",
        "Results use the current symbols.csv universe, so delisted securities are not represented (survivorship bias).",
        "Past performance is not a guarantee of future returns.",
    ]

    nifty_return: float | None = None
    try:
        nifty = store.candles(NIFTY_DISPLAY_NAME, request.timeframe, request.durationYears, analysis_start, now, benchmark=True)
        if len(nifty) >= 2:
            nifty_return = (float(nifty.iloc[-1]["Close"]) / float(nifty.iloc[0]["Close"]) - 1) * 100
    except (DhanAPIError, ValueError) as error:
        warnings.append(f"NIFTY 50 benchmark unavailable: {error}")

    results: list[dict[str, Any]] = []
    for symbol in request.symbols:
        try:
            candles = store.candles(symbol, request.timeframe, request.durationYears, analysis_start, now)
            results.append(
                simulate_symbol(
                    symbol,
                    candles,
                    timeframe=request.timeframe,
                    entry_low=request.entryLow,
                    entry_high=request.entryHigh,
                    exit_low=request.exitLow,
                    exit_high=request.exitHigh,
                    nifty_return_pct=nifty_return,
                )
            )
        except (DhanAPIError, ValueError, OSError) as error:
            errors.append({"symbol": symbol, "message": str(error)})

    if not results:
        warnings.append(
            "No symbol in this batch produced enough valid historical data; the universe run continued to the next batch."
        )

    return {
        "metadata": {
            "runId": request.runId or str(uuid.uuid4()),
            "strategyMode": "rsi_range",
            "strategyVersion": "rsi-range-1.0.0",
            "generatedAt": now.isoformat(),
            "analysisStart": analysis_start.isoformat(),
            "durationYears": request.durationYears,
            "timeframe": request.timeframe,
            "rsiPeriod": RSI_PERIOD,
            "entryRange": [request.entryLow, request.entryHigh],
            "exitRange": [request.exitLow, request.exitHigh],
            "initialCapitalPerSymbol": INITIAL_CAPITAL,
            "minimumNetProfitPct": MINIMUM_NET_PROFIT_PCT,
            "costModel": {
                "variableFeePerSidePct": VARIABLE_FEE_RATE * 100,
                "fixedFeePerOrder": FIXED_FEE_PER_ORDER,
                "slippagePerSidePct": SLIPPAGE_RATE * 100,
            },
            "benchmark": NIFTY_DISPLAY_NAME,
            "timezone": "Asia/Kolkata",
        },
        "results": results,
        "errors": errors,
        "warnings": warnings,
    }


def create_store() -> HistoricalDataStore:
    config = DhanConfig.from_environment()
    cache_directory = Path(os.environ.get("BACKTEST_CACHE_DIR", "/var/lib/vento-nse/backtest")).expanduser()
    if not cache_directory.is_absolute():
        raise RuntimeError("BACKTEST_CACHE_DIR must be an absolute path")
    return HistoricalDataStore(config, cache_directory)


app = FastAPI(title="OpenDelta Backtest API", docs_url=None, redoc_url=None)
_run_lock = asyncio.Lock()
_store: HistoricalDataStore | None = None
_feature_snapshot_lock = threading.Lock()
_feature_snapshot_cache: tuple[int, pd.DataFrame] | None = None
_universe_service: UniverseService | None = None
_live_signal_engine: LiveSignalEngine | None = None
_market_data_refresh_service: MarketDataRefreshService | None = None


def get_store() -> HistoricalDataStore:
    global _store
    if _store is None:
        _store = create_store()
    return _store


def get_report_directory() -> Path:
    directory = Path(
        os.environ.get("BACKTEST_REPORT_DIR", "/var/lib/vento-nse/backtest/reports")
    ).expanduser()
    if not directory.is_absolute():
        raise RuntimeError("BACKTEST_REPORT_DIR must be an absolute path")
    return directory


def get_universe_service() -> UniverseService:
    global _universe_service
    if _universe_service is None:
        default_root = Path(
            os.environ.get("BACKTEST_CACHE_DIR", "/var/lib/vento-nse/backtest")
        ).expanduser() / "live-universe"
        root = Path(os.environ.get("LIVE_UNIVERSE_DIR", str(default_root))).expanduser()
        market_data = Path(
            os.environ.get(
                "LIVE_MARKET_DATA_FILE",
                "/var/lib/vento-nse/data/nse_symbols_rsi_volume.csv",
            )
        ).expanduser()
        if not root.is_absolute() or not market_data.is_absolute():
            raise RuntimeError("Live-universe persistence and market-data paths must be absolute")
        _universe_service = UniverseService(UniverseRepository(root), market_data)
    return _universe_service


def get_live_signal_engine() -> LiveSignalEngine:
    global _live_signal_engine
    if _live_signal_engine is None:
        default_root = Path(
            os.environ.get("BACKTEST_CACHE_DIR", "/var/lib/vento-nse/backtest")
        ).expanduser() / "live-signals"
        root = Path(os.environ.get("LIVE_SIGNAL_DIR", str(default_root))).expanduser()
        if not root.is_absolute():
            raise RuntimeError("LIVE_SIGNAL_DIR must be an absolute path")
        _live_signal_engine = LiveSignalEngine(
            LiveSignalRepository(root),
            get_store(),
            get_universe_service(),
        )
    return _live_signal_engine


def get_market_data_refresh_service() -> MarketDataRefreshService:
    global _market_data_refresh_service
    if _market_data_refresh_service is None:
        output_file = Path(
            os.environ.get(
                "LIVE_MARKET_DATA_FILE",
                "/var/lib/vento-nse/data/nse_symbols_rsi_volume.csv",
            )
        ).expanduser()
        _market_data_refresh_service = MarketDataRefreshService(output_file)
    return _market_data_refresh_service


def get_recovery_baseline_metadata() -> dict[str, Any]:
    analysis = load_feature_analysis(get_report_directory())
    metadata = analysis.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("Recovery feature analysis metadata is unavailable")
    return metadata


def _live_universe_preview(request: LiveUniverseRequest) -> dict[str, Any]:
    service = get_universe_service()
    build = service.preview(
        get_feature_snapshots(),
        get_recovery_baseline_metadata(),
        request.selection_config(),
    )
    return build.payload


def get_feature_snapshots() -> pd.DataFrame:
    global _feature_snapshot_cache
    directory = get_report_directory()
    path = directory / "recovery_signal_features.parquet"
    try:
        modified = path.stat().st_mtime_ns
    except OSError as error:
        raise FileNotFoundError(
            "Recovery feature analysis has not been generated for the production baseline yet"
        ) from error
    with _feature_snapshot_lock:
        if _feature_snapshot_cache is None or _feature_snapshot_cache[0] != modified:
            _feature_snapshot_cache = (modified, load_feature_snapshots(directory))
        return _feature_snapshot_cache[1]


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        symbols = len(load_symbols(Path(os.environ.get("SYMBOLS_FILE", DEFAULT_SYMBOLS_FILE))))
        return {"status": "ok", "symbols": symbols}
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/live-universe/config")
def live_universe_config() -> dict[str, Any]:
    try:
        service = get_universe_service()
        metrics = service.metrics(get_feature_snapshots())
        active = service.repository.load_active()
        return {
            "defaults": {
                "topN": DEFAULT_TOP_N,
                "minimumPrice": DEFAULT_MINIMUM_PRICE,
                "maximumPrice": DEFAULT_MAXIMUM_PRICE,
                "rankingMode": "QUALITY",
                "minimumBuyObservations": DEFAULT_MINIMUM_BUY_OBSERVATIONS,
                "dynamicPriceFilter": False,
            },
            "rankingModes": [
                {"value": value, "label": label} for value, label in RANKING_LABELS.items()
            ],
            "historicalBuyObservationDistribution": signal_count_distribution(metrics),
            "minimumBuyObservationRationale": (
                "Default 50 is below the historical P10 of 59 and removes only thin samples; "
                "it does not change RSI Recovery signals."
            ),
            "active": active,
        }
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/live-universe/preview")
async def live_universe_preview(request: LiveUniverseRequest) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(_live_universe_preview, request)
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/live-universe/rebuild")
async def live_universe_rebuild(request: LiveUniverseRequest) -> dict[str, Any]:
    """Preview a rebuild. This endpoint never replaces the frozen active universe."""
    try:
        preview = await asyncio.to_thread(_live_universe_preview, request)
        active = get_universe_service().repository.load_active()
        preview["rebuildOf"] = active.get("universeVersion") if active else None
        preview["requiresConfirmation"] = True
        return preview
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/live-universe/save")
async def live_universe_save(request: LiveUniverseSaveRequest) -> dict[str, Any]:
    """Explicitly confirm, version, freeze, and activate the current preview."""
    try:
        preview = await asyncio.to_thread(_live_universe_preview, request)
        if preview["configurationHash"] != request.configurationHash:
            raise HTTPException(
                status_code=409,
                detail="Reference prices or source data changed after preview; review a fresh preview before freezing",
            )
        return await asyncio.to_thread(
            get_universe_service().repository.save_and_activate,
            preview,
        )
    except HTTPException:
        raise
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/live-universe/active")
def live_universe_active() -> dict[str, Any]:
    active = get_universe_service().repository.load_active()
    return {"active": active}


@app.get("/live-universe/history")
def live_universe_history() -> dict[str, Any]:
    return {"versions": get_universe_service().repository.history()}


@app.get("/live-universe/symbols")
def live_universe_symbols() -> dict[str, Any]:
    symbols, active = get_universe_service().get_active_live_universe()
    return {
        "symbols": symbols,
        "metadata": {
            "active": active is not None,
            "universeVersion": active.get("universeVersion") if active else None,
            "frozen": active.get("frozen") if active else False,
            "priceAsOf": active.get("source", {}).get("priceAsOf") if active else None,
            "configurationHash": active.get("configurationHash") if active else None,
        },
    }


@app.get("/live-universe/export")
def live_universe_export(
    version: str = Query(default="active", min_length=1, max_length=40),
) -> FileResponse:
    try:
        path = get_universe_service().repository.export_for(version)
        return FileResponse(path, media_type="text/csv", filename="live_universe.csv")
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/live-signals/status")
def live_signals_status() -> dict[str, Any]:
    try:
        return get_live_signal_engine().status()
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/market-data/status")
def market_data_status() -> dict[str, Any]:
    try:
        return get_market_data_refresh_service().status()
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/market-data/csv")
def market_data_csv() -> FileResponse:
    path = Path(
        os.environ.get(
            "LIVE_MARKET_DATA_FILE",
            "/var/lib/vento-nse/data/nse_symbols_rsi_volume.csv",
        )
    ).expanduser()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Live market data is not available")
    return FileResponse(path, media_type="text/csv", filename="nse_symbols_rsi_volume.csv")


@app.post("/market-data/refresh")
def refresh_market_data() -> dict[str, Any]:
    try:
        return get_market_data_refresh_service().start()
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/live-signals/settings")
def live_signals_settings() -> dict[str, Any]:
    engine = get_live_signal_engine()
    return {
        "settings": engine.repository.settings().public(),
        "strategy": {
            "strategyVersion": RECOVERY_STRATEGY_VERSION,
            "timeframe": "5m",
            "rsiLength": 14,
            "rsiArm": [30, 40],
            "rsiRecovery": 40,
            "ema": [9, 20],
            "vwap": True,
            "volumeEma": 20,
            "minimumConfirmations": 2,
            "targetPct": 0.5,
            "setupExpiryBars": 50,
            "noStopLoss": True,
            "noLeverage": True,
            "brokerExecution": False,
        },
    }


@app.put("/live-signals/settings")
def update_live_signals_settings(request: LiveSignalSettingsRequest) -> dict[str, Any]:
    try:
        return {"settings": get_live_signal_engine().repository.save_settings(request.settings())}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/live-signals")
def list_live_signals(
    action: Literal["PAPER_BUY", "WATCH", "IGNORE", "NO_ACTION"] | None = Query(default=None),
) -> dict[str, Any]:
    engine = get_live_signal_engine()
    return {
        "signals": engine.list_signals(action),
        "status": engine.status(),
        "study": engine.study_summary(),
    }


@app.post("/live-signals/{signal_id}/decision")
def decide_live_signal(signal_id: str, request: LiveSignalDecisionRequest) -> dict[str, Any]:
    try:
        return {
            "signal": get_live_signal_engine().repository.decide(
                signal_id,
                request.action,
                reason=request.reason,
                notes=request.notes,
            )
        }
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/live-signals/{signal_id}/paper-buy")
def paper_buy_live_signal(signal_id: str, request: PaperBuyRequest) -> dict[str, Any]:
    """Persist a manual paper observation. This endpoint has no broker client or order path."""
    try:
        return {
            "paperTrade": get_live_signal_engine().repository.create_paper_trade(
                signal_id,
                request.actualEntryPrice,
                request.actualQuantity,
                request.notes,
            )
        }
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/paper-trades")
def list_paper_trades() -> dict[str, Any]:
    return {"paperTrades": get_live_signal_engine().list_paper_trades()}


@app.post("/paper-trades/{paper_trade_id}/close")
def close_paper_trade(paper_trade_id: str, request: PaperCloseRequest) -> dict[str, Any]:
    try:
        return {
            "paperTrade": get_live_signal_engine().repository.close_paper_trade(
                paper_trade_id,
                request.actualExitPrice,
                request.notes,
            )
        }
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/live-signals/study")
def live_signal_study() -> dict[str, Any]:
    return get_live_signal_engine().study_summary()


@app.post("/recovery-analysis")
async def recovery_analysis(query: RecoveryAnalysisQuery) -> dict[str, Any]:
    has_filters = any(
        value is not None
        for value in (
            query.symbol,
            query.timeframe,
            query.dateFrom,
            query.dateTo,
            query.confirmationCombination,
            query.timeOfDayBucket,
            query.targetOutcome,
        )
    )
    try:
        if not has_filters:
            return await asyncio.to_thread(load_feature_analysis, get_report_directory())
        source = await asyncio.to_thread(get_feature_snapshots)
        filtered = filter_feature_snapshots(
            source,
            symbol=query.symbol,
            timeframe=query.timeframe,
            date_from=query.dateFrom.isoformat() if query.dateFrom else None,
            date_to=query.dateTo.isoformat() if query.dateTo else None,
            confirmation_combination=query.confirmationCombination,
            time_of_day_bucket=query.timeOfDayBucket,
            target_outcome=query.targetOutcome,
        )
        if filtered.empty:
            raise HTTPException(status_code=422, detail="No BUY observations match these analysis filters")
        baseline = await asyncio.to_thread(load_feature_analysis, get_report_directory())
        metadata = {
            **baseline.get("metadata", {}),
            "filtered": True,
            "sourceObservations": len(source),
            "activeFilters": query.model_dump(exclude_none=True, mode="json"),
        }
        bundle = await asyncio.to_thread(build_feature_analysis, filtered, metadata)
        return bundle.payload
    except FileNotFoundError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/recovery-analysis/report")
def recovery_analysis_report(
    filename: str = Query(min_length=1, max_length=100),
) -> FileResponse:
    if filename not in REPORT_FILENAMES:
        raise HTTPException(status_code=404, detail="Unknown recovery analysis report")
    path = get_report_directory() / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Recovery analysis report is not available")
    media_type = "application/octet-stream" if path.suffix == ".parquet" else "text/csv"
    if path.suffix == ".json":
        media_type = "application/json"
    return FileResponse(path, media_type=media_type, filename=filename)


@app.post("/backtest")
async def backtest(request: BacktestRequest) -> dict[str, Any]:
    async with _run_lock:
        try:
            return await asyncio.to_thread(run_backtest, request, get_store())
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except DhanAPIError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/backtest/optimize-atr")
async def optimize_atr_exits(request: AtrOptimizationRequest) -> dict[str, Any]:
    async with _run_lock:
        try:
            return await asyncio.to_thread(
                run_atr_exit_optimization, request, get_store()
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except DhanAPIError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error


@app.on_event("startup")
def start_live_signal_runtime() -> None:
    if os.environ.get("LIVE_SIGNAL_ENGINE_ENABLED", "false").strip().casefold() in {"1", "true", "yes", "on"}:
        get_live_signal_engine().start()


@app.on_event("shutdown")
def stop_live_signal_runtime() -> None:
    if _live_signal_engine is not None:
        _live_signal_engine.stop()
    if _market_data_refresh_service is not None:
        _market_data_refresh_service.shutdown()
