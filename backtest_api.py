from __future__ import annotations

import asyncio
import concurrent.futures
import csv
import io
import json
import math
import multiprocessing
import os
import sqlite3
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

try:
    import resource
except ImportError:  # pragma: no cover - Windows development/test runtime
    resource = None
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from atr_exit_optimizer import (
    AtrOptimizationGrid,
    evaluate_atr_exit_grid,
)
from application_settings import (
    ApplicationSettingsRepository,
    DEFAULT_MAXIMUM_PRICE as GLOBAL_DEFAULT_MAXIMUM_PRICE,
    filter_symbols_by_price,
    prices_by_symbol,
)
from backtest_history import BacktestHistoryRepository, HISTORY_LIMIT
from backtest_jobs import BacktestJobService
from live_signals import (
    LiveSignalEngine,
    LiveSignalRepository,
    LiveSignalSettings,
)
from market_data_refresh import MarketDataRefreshService
from market_symbol_registry import (
    MarketSymbolRegistry,
    SymbolAlreadyExistsError,
    SymbolNotFoundError,
)
from market_aligned_vwap_pullback_scalper import (
    FEATURE_CODE_VERSION as VWAP_PULLBACK_FEATURE_VERSION,
    PORTFOLIO_RULE_VERSION as VWAP_PULLBACK_PORTFOLIO_VERSION,
    SESSION_RULE_VERSION as VWAP_PULLBACK_SESSION_VERSION,
    STRATEGY_DESCRIPTION as VWAP_PULLBACK_DESCRIPTION,
    STRATEGY_KEY as VWAP_PULLBACK_STRATEGY_KEY,
    STRATEGY_NAME as VWAP_PULLBACK_STRATEGY_NAME,
    STRATEGY_VERSION as VWAP_PULLBACK_STRATEGY_VERSION,
    VwapPullbackConfig,
    VwapPullbackResultCache,
    build_nifty_candidate_context,
    build_supporting_context as build_vwap_supporting_context,
    enrich_candidates as enrich_vwap_candidates,
    execute_portfolio as execute_vwap_portfolio,
    file_stat_fingerprint as vwap_file_stat_fingerprint,
    load_sector_mapping as load_vwap_sector_mapping,
    prepare_symbol_batch as prepare_vwap_symbol_batch,
    stable_fingerprint as vwap_fingerprint,
    summarize_results as summarize_vwap_results,
)
from daily_scalping_watchlist import (
    FEATURE_CODE_VERSION as DAILY_WATCHLIST_FEATURE_VERSION,
    OPENING_RANGE_RULE_VERSION,
    MINIMUM_UNTOUCHED_VALIDATION_TRADES,
    PORTFOLIO_RULE_VERSION as DAILY_WATCHLIST_PORTFOLIO_VERSION,
    SESSION_RULE_VERSION as DAILY_WATCHLIST_SESSION_VERSION,
    STRATEGY_DESCRIPTION as DAILY_WATCHLIST_DESCRIPTION,
    STRATEGY_KEY as DAILY_WATCHLIST_STRATEGY_KEY,
    STRATEGY_NAME as DAILY_WATCHLIST_STRATEGY_NAME,
    STRATEGY_VERSION as DAILY_WATCHLIST_STRATEGY_VERSION,
    WATCHLIST_RULE_VERSION,
    DailyWatchlistConfig,
    DailyWatchlistResultCache,
    build_watchlist_history,
    calculate_watchlist_features,
    compare_watchlist_variant,
    execute_portfolio as execute_daily_watchlist_portfolio,
    file_stat_fingerprint as daily_watchlist_file_fingerprint,
    prepare_symbol_batch as prepare_daily_watchlist_symbol_batch,
    select_candidates_for_history,
    stable_fingerprint as daily_watchlist_fingerprint,
    summarize_watchlist_history,
    validation_decision as daily_watchlist_validation_decision,
)
from dhan_oi import build_oi_service_from_environment
from main import (
    ConfigurationError,
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
    summarize_recovery_trades,
)
from strategy_parameters import numeric_field_kwargs, parameter_definition
from nifty_oi_regime import (
    NiftyOiConfig,
    OiRegimeRepository,
    apply_oi_filter_chronologically,
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
from recovery_rsi_profit_exit import (
    RSI_PROFIT_EXIT_VERSION,
    RsiProfitExitConfig,
    aggregate_rsi_profit_exit_results,
    simulate_rsi_profit_exit_symbol,
)
from rsi_exit_optimizer import (
    RsiExitOptimizationGrid,
    evaluate_rsi_exit_grid,
)
from stock_scanner import StockScannerService
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
MAX_SYMBOLS_PER_RUN = int(os.environ.get("BACKTEST_MAX_SYMBOLS_PER_RUN", "2000"))
MAX_BACKTEST_WORKERS = 10
MAX_CHART_POINTS = 360
MAX_EVENTS = 300
INTRADAY_CHUNK_DAYS = 89
CACHE_TTL_SECONDS = 60 * 60
NIFTY_DISPLAY_NAME = "NIFTY 50"
RSI_RECOVERY_STRATEGY_KEY = "rsi_recovery"
RSI_RECOVERY_STRATEGY_NAME = "RSI Recovery Scalping"
RSI_RECOVERY_DESCRIPTION = (
    "RSI recovery entries using the existing EMA, VWAP and volume confirmation logic."
)


class GlobalPriceSettingsRequest(BaseModel):
    minimumPrice: float = Field(ge=0, le=GLOBAL_DEFAULT_MAXIMUM_PRICE)
    maximumPrice: float = Field(gt=0, le=GLOBAL_DEFAULT_MAXIMUM_PRICE)

    @model_validator(mode="after")
    def validate_price_range(self) -> "GlobalPriceSettingsRequest":
        if self.minimumPrice >= self.maximumPrice:
            raise ValueError("Minimum price must be less than maximum price")
        return self


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


class VwapPullbackConfigurationRequest(BaseModel):
    """Authoritative configuration for Market-Aligned VWAP Pullback Scalper."""

    executionModel: Literal["NEXT_BAR_OPEN"] = "NEXT_BAR_OPEN"
    entryStartTime: str = str(parameter_definition(VWAP_PULLBACK_STRATEGY_KEY, "entryStartTime")["default"])
    lastEntryTime: str = str(parameter_definition(VWAP_PULLBACK_STRATEGY_KEY, "lastEntryTime")["default"])
    squareOffTime: str = str(parameter_definition(VWAP_PULLBACK_STRATEGY_KEY, "squareOffTime")["default"])
    rsiLength: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "rsiLength"))
    rsiPullbackMinimum: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "rsiPullbackMinimum"))
    rsiPullbackMaximum: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "rsiPullbackMaximum"))
    rsiTriggerLevel: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "rsiTriggerLevel"))
    maximumTriggerRsi: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "maximumTriggerRsi"))
    setupExpiryBars: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "setupExpiryBars"))
    emaFast: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "emaFast"))
    emaSlow: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "emaSlow"))
    emaSlopeLookbackBars: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "emaSlopeLookbackBars"))
    atrLength: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "atrLength"))
    rvolPeriod: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "rvolPeriod"))
    minimumTriggerRvol: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "minimumTriggerRvol"))
    pullbackApproachAtr: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "pullbackApproachAtr"))
    materialBelowEmaAtr: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "materialBelowEmaAtr"))
    maximumEntryGapAtr: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "maximumEntryGapAtr"))
    structuralStopBufferAtr: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "structuralStopBufferAtr"))
    volatilityStopAtr: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "volatilityStopAtr"))
    minimumStopPct: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "minimumStopPct"))
    maximumStopPct: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "maximumStopPct"))
    rewardRiskRatio: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "rewardRiskRatio"))
    maximumHoldingBars: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "maximumHoldingBars"))
    minimumAverageTradedValue: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "minimumAverageTradedValue"))
    maximumCandleRangeAtr: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "maximumCandleRangeAtr"))
    historicalSpreadMode: Literal["ADVISORY"] = "ADVISORY"
    liveMaximumSpreadPct: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "liveMaximumSpreadPct"))
    marketContextFailPolicy: Literal["ADVISORY", "REJECT"] = "ADVISORY"
    marketContextStaleSeconds: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "marketContextStaleSeconds"))
    minimumBreadthPct: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "minimumBreadthPct"))
    minimumBreadthSymbols: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "minimumBreadthSymbols"))
    minimumSectorMembers: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "minimumSectorMembers"))
    minimumSectorBullishPct: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "minimumSectorBullishPct"))
    relativeStrengthLookbackBars: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "relativeStrengthLookbackBars"))
    qualityRvolThreshold: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "qualityRvolThreshold"))
    enforceMinimumQualityScore: bool = False
    minimumQualityScore: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "minimumQualityScore"))
    oiMode: Literal["OFF", "ADVISORY"] = "OFF"
    oiStaleDataSeconds: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "oiStaleDataSeconds"))
    positionSizing: Literal["FIXED_QUANTITY", "RISK_BUDGET"] = "FIXED_QUANTITY"
    quantityPerTrade: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "quantityPerTrade"))
    rupeeRiskBudget: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "rupeeRiskBudget"))
    maximumQuantity: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "maximumQuantity"))
    configuredCapital: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "configuredCapital"))
    maximumCapitalPerPosition: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "maximumCapitalPerPosition"))
    maximumTradesPerDay: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "maximumTradesPerDay"))
    maximumConcurrentTrades: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "maximumConcurrentTrades"))
    stopAfterDailyLosses: int = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "stopAfterDailyLosses"))
    maximumDailyLossPct: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "maximumDailyLossPct"))
    buyCostBps: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "buyCostBps"))
    sellCostBps: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "sellCostBps"))
    slippageBps: float = Field(**numeric_field_kwargs(VWAP_PULLBACK_STRATEGY_KEY, "slippageBps"))

    @model_validator(mode="after")
    def validate_configuration(self) -> "VwapPullbackConfigurationRequest":
        self.strategy_config().validate()
        return self

    def strategy_config(self) -> VwapPullbackConfig:
        return VwapPullbackConfig(
            execution_model=self.executionModel,
            entry_start_time=self.entryStartTime,
            last_entry_time=self.lastEntryTime,
            square_off_time=self.squareOffTime,
            rsi_length=self.rsiLength,
            rsi_pullback_minimum=self.rsiPullbackMinimum,
            rsi_pullback_maximum=self.rsiPullbackMaximum,
            rsi_trigger_level=self.rsiTriggerLevel,
            maximum_trigger_rsi=self.maximumTriggerRsi,
            setup_expiry_bars=self.setupExpiryBars,
            ema_fast=self.emaFast,
            ema_slow=self.emaSlow,
            ema_slope_lookback_bars=self.emaSlopeLookbackBars,
            atr_length=self.atrLength,
            rvol_period=self.rvolPeriod,
            minimum_trigger_rvol=self.minimumTriggerRvol,
            pullback_approach_atr=self.pullbackApproachAtr,
            material_below_ema_atr=self.materialBelowEmaAtr,
            maximum_entry_gap_atr=self.maximumEntryGapAtr,
            structural_stop_buffer_atr=self.structuralStopBufferAtr,
            volatility_stop_atr=self.volatilityStopAtr,
            minimum_stop_pct=self.minimumStopPct,
            maximum_stop_pct=self.maximumStopPct,
            reward_risk_ratio=self.rewardRiskRatio,
            maximum_holding_bars=self.maximumHoldingBars,
            minimum_average_traded_value=self.minimumAverageTradedValue,
            maximum_candle_range_atr=self.maximumCandleRangeAtr,
            historical_spread_mode=self.historicalSpreadMode,
            live_maximum_spread_pct=self.liveMaximumSpreadPct,
            market_context_fail_policy=self.marketContextFailPolicy,
            market_context_stale_seconds=self.marketContextStaleSeconds,
            minimum_breadth_pct=self.minimumBreadthPct,
            minimum_breadth_symbols=self.minimumBreadthSymbols,
            minimum_sector_members=self.minimumSectorMembers,
            minimum_sector_bullish_pct=self.minimumSectorBullishPct,
            relative_strength_lookback_bars=self.relativeStrengthLookbackBars,
            quality_rvol_threshold=self.qualityRvolThreshold,
            enforce_minimum_quality_score=self.enforceMinimumQualityScore,
            minimum_quality_score=self.minimumQualityScore,
            oi_mode=self.oiMode,
            oi_stale_data_seconds=self.oiStaleDataSeconds,
            position_sizing=self.positionSizing,
            quantity_per_trade=self.quantityPerTrade,
            rupee_risk_budget=self.rupeeRiskBudget,
            maximum_quantity=self.maximumQuantity,
            configured_capital=self.configuredCapital,
            maximum_capital_per_position=self.maximumCapitalPerPosition,
            maximum_trades_per_day=self.maximumTradesPerDay,
            maximum_concurrent_trades=self.maximumConcurrentTrades,
            stop_after_daily_losses=self.stopAfterDailyLosses,
            maximum_daily_loss_pct=self.maximumDailyLossPct,
            buy_cost_bps=self.buyCostBps,
            sell_cost_bps=self.sellCostBps,
            slippage_bps=self.slippageBps,
        )

    def public(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Top5OpeningRangeBreakoutConfigurationRequest(BaseModel):
    executionModel: Literal["NEXT_BAR_OPEN"] = "NEXT_BAR_OPEN"
    openingRangeStartTime: str = str(parameter_definition(DAILY_WATCHLIST_STRATEGY_KEY, "openingRangeStartTime")["default"])
    openingRangeEndTime: str = str(parameter_definition(DAILY_WATCHLIST_STRATEGY_KEY, "openingRangeEndTime")["default"])
    lastEntryTime: str = str(parameter_definition(DAILY_WATCHLIST_STRATEGY_KEY, "lastEntryTime")["default"])
    squareOffTime: str = str(parameter_definition(DAILY_WATCHLIST_STRATEGY_KEY, "squareOffTime")["default"])
    rsiLength: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "rsiLength"))
    emaFast: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "emaFast"))
    emaSlow: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "emaSlow"))
    atrLength: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "atrLength"))
    rvolPeriod: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "rvolPeriod"))
    watchlistMode: Literal["FROZEN_OPEN", "ROLLING"] = "FROZEN_OPEN"
    watchlistSelectionTime: str = str(parameter_definition(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistSelectionTime")["default"])
    watchlistRescanIntervalMinutes: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistRescanIntervalMinutes"))
    watchlistRescanEndTime: str = str(parameter_definition(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistRescanEndTime")["default"])
    watchlistSelectedSymbols: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistSelectedSymbols"))
    watchlistPrimarySymbols: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistPrimarySymbols"))
    watchlistMinimumPromotionScore: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistMinimumPromotionScore"))
    watchlistRequiredPromotionAdvantage: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistRequiredPromotionAdvantage"))
    watchlistMinimumResidenceMinutes: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistMinimumResidenceMinutes"))
    watchlistMaximumReplacementsPerRescan: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistMaximumReplacementsPerRescan"))
    watchlistMaximumSymbolsPerSector: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistMaximumSymbolsPerSector"))
    watchlistHistoricalSessions: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistHistoricalSessions"))
    watchlistRollingWindowMinutes: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "watchlistRollingWindowMinutes"))
    openingBreakoutMinimumRvol: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "openingBreakoutMinimumRvol"))
    rollingBreakoutLookbackBars: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "rollingBreakoutLookbackBars"))
    rollingBreakoutMinimumRvol: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "rollingBreakoutMinimumRvol"))
    rollingMaximumVwapDistanceAtr: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "rollingMaximumVwapDistanceAtr"))
    minimumCloseLocation: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "minimumCloseLocation"))
    maximumEntryGapAtr: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "maximumEntryGapAtr"))
    structuralStopBufferAtr: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "structuralStopBufferAtr"))
    volatilityStopAtr: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "volatilityStopAtr"))
    minimumStopPct: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "minimumStopPct"))
    maximumStopPct: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "maximumStopPct"))
    rewardRiskRatio: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "rewardRiskRatio"))
    maximumHoldingBars: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "maximumHoldingBars"))
    minimumAverageTradedValue: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "minimumAverageTradedValue"))
    minimumPrice: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "minimumPrice"))
    maximumPrice: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "maximumPrice"))
    minimumMedianDailyTradedValue: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "minimumMedianDailyTradedValue"))
    minimumOpeningTradedValue: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "minimumOpeningTradedValue"))
    minimumDailyAtrPct: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "minimumDailyAtrPct"))
    maximumDailyAtrPct: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "maximumDailyAtrPct"))
    maximumOpeningGapPct: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "maximumOpeningGapPct"))
    maximumCandleRangeAtr: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "maximumCandleRangeAtr"))
    liveMaximumSpreadPct: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "liveMaximumSpreadPct"))
    maximumTradesPerSymbolPerDay: Literal[1] = 1
    quantityPerTrade: Literal[50] = 50
    configuredCapital: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "configuredCapital"))
    maximumCapitalPerPosition: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "maximumCapitalPerPosition"))
    maximumTradesPerDay: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "maximumTradesPerDay"))
    maximumConcurrentTrades: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "maximumConcurrentTrades"))
    stopAfterDailyLosses: int = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "stopAfterDailyLosses"))
    maximumDailyLossPct: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "maximumDailyLossPct"))
    buyCostBps: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "buyCostBps"))
    sellCostBps: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "sellCostBps"))
    slippageBps: float = Field(**numeric_field_kwargs(DAILY_WATCHLIST_STRATEGY_KEY, "slippageBps"))

    @model_validator(mode="after")
    def validate_configuration(self) -> "Top5OpeningRangeBreakoutConfigurationRequest":
        self.strategy_config().validate()
        return self

    def strategy_config(self) -> DailyWatchlistConfig:
        return DailyWatchlistConfig(
            execution_model=self.executionModel,
            opening_range_start_time=self.openingRangeStartTime,
            opening_range_end_time=self.openingRangeEndTime,
            last_entry_time=self.lastEntryTime,
            square_off_time=self.squareOffTime,
            rsi_length=self.rsiLength,
            ema_fast=self.emaFast,
            ema_slow=self.emaSlow,
            atr_length=self.atrLength,
            rvol_period=self.rvolPeriod,
            mode=self.watchlistMode,
            selection_time=self.watchlistSelectionTime,
            rescan_interval_minutes=self.watchlistRescanIntervalMinutes,
            rescan_end_time=self.watchlistRescanEndTime,
            selected_symbols=self.watchlistSelectedSymbols,
            primary_symbols=self.watchlistPrimarySymbols,
            minimum_promotion_score=self.watchlistMinimumPromotionScore,
            required_promotion_advantage=self.watchlistRequiredPromotionAdvantage,
            minimum_residence_minutes=self.watchlistMinimumResidenceMinutes,
            maximum_replacements_per_rescan=self.watchlistMaximumReplacementsPerRescan,
            maximum_symbols_per_sector=self.watchlistMaximumSymbolsPerSector,
            historical_sessions=self.watchlistHistoricalSessions,
            rolling_window_minutes=self.watchlistRollingWindowMinutes,
            breakout_lookback_bars=self.rollingBreakoutLookbackBars,
            breakout_minimum_rvol=self.rollingBreakoutMinimumRvol,
            maximum_vwap_distance_atr=self.rollingMaximumVwapDistanceAtr,
            maximum_trades_per_symbol_per_day=self.maximumTradesPerSymbolPerDay,
            opening_breakout_minimum_rvol=self.openingBreakoutMinimumRvol,
            minimum_close_location=self.minimumCloseLocation,
            maximum_entry_gap_atr=self.maximumEntryGapAtr,
            structural_stop_buffer_atr=self.structuralStopBufferAtr,
            volatility_stop_atr=self.volatilityStopAtr,
            minimum_stop_pct=self.minimumStopPct,
            maximum_stop_pct=self.maximumStopPct,
            reward_risk_ratio=self.rewardRiskRatio,
            maximum_holding_bars=self.maximumHoldingBars,
            minimum_average_traded_value=self.minimumAverageTradedValue,
            minimum_price=self.minimumPrice,
            maximum_price=self.maximumPrice,
            minimum_median_daily_traded_value=self.minimumMedianDailyTradedValue,
            minimum_opening_traded_value=self.minimumOpeningTradedValue,
            minimum_daily_atr_pct=self.minimumDailyAtrPct,
            maximum_daily_atr_pct=self.maximumDailyAtrPct,
            maximum_opening_gap_pct=self.maximumOpeningGapPct,
            maximum_candle_range_atr=self.maximumCandleRangeAtr,
            live_maximum_spread_pct=self.liveMaximumSpreadPct,
            quantity_per_trade=self.quantityPerTrade,
            configured_capital=self.configuredCapital,
            maximum_capital_per_position=self.maximumCapitalPerPosition,
            maximum_trades_per_day=self.maximumTradesPerDay,
            maximum_concurrent_trades=self.maximumConcurrentTrades,
            stop_after_daily_losses=self.stopAfterDailyLosses,
            maximum_daily_loss_pct=self.maximumDailyLossPct,
            buy_cost_bps=self.buyCostBps,
            sell_cost_bps=self.sellCostBps,
            slippage_bps=self.slippageBps,
        )

    def public(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

ANNUALIZATION = {
    "5m": 252 * 75,
    "15m": 252 * 25,
    "30m": 252 * 13,
    "1h": 252 * 6.25,
    "2h": 252 * 3.125,
    "4h": 252 * 1.6,
    "1d": 252,
}


class BacktestHistorySaveRequest(BaseModel):
    id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    completedAt: datetime
    strategyMode: Literal[
        "rsi_range",
        "rsi_recovery",
        "top_5_opening_range_breakout",
        "daily_scalping_watchlist",
        "market_aligned_vwap_pullback_scalper",
        "market_aligned_rsi_scalper",
    ]
    strategyName: str = Field(min_length=1, max_length=120)
    timeframe: str = Field(min_length=1, max_length=20)
    durationYears: int = Field(ge=1, le=10)
    symbolCount: int = Field(ge=1, le=100_000)
    response: dict[str, Any]

    def persisted(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class BacktestRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS_PER_RUN)
    strategyMode: Literal[
        "rsi_range", "rsi_recovery", "top_5_opening_range_breakout",
    ] = "rsi_range"
    strategyKey: Literal[
        "rsi_range", "rsi_recovery", "top_5_opening_range_breakout",
    ] | None = None
    universeMode: Literal["selected", "all"] = "selected"
    runId: str | None = Field(default=None, min_length=1, max_length=80)
    cachePolicy: Literal["USE_CACHE", "RUN_AGAIN"] = "RUN_AGAIN"
    durationYears: Literal[1, 3] = 1
    timeframe: Literal["5m", "15m", "30m", "1h", "2h", "4h", "1d"] = "1d"
    entryLow: float = Field(**numeric_field_kwargs("rsi_range", "entryLow"))
    entryHigh: float = Field(**numeric_field_kwargs("rsi_range", "entryHigh"))
    exitLow: float = Field(**numeric_field_kwargs("rsi_range", "exitLow"))
    exitHigh: float = Field(**numeric_field_kwargs("rsi_range", "exitHigh"))
    rsiLength: int = Field(**numeric_field_kwargs("rsi_recovery", "rsiLength"))
    rsiArmLow: float = Field(**numeric_field_kwargs("rsi_recovery", "rsiArmLow"))
    rsiArmHigh: float = Field(**numeric_field_kwargs("rsi_recovery", "rsiArmHigh"))
    rsiRecovery: float = Field(**numeric_field_kwargs("rsi_recovery", "rsiRecovery"))
    emaEnabled: bool = True
    emaFast: int = Field(**numeric_field_kwargs("rsi_recovery", "emaFast"))
    emaSlow: int = Field(**numeric_field_kwargs("rsi_recovery", "emaSlow"))
    vwapEnabled: bool = True
    volumeEnabled: bool = True
    volumeEma: int = Field(**numeric_field_kwargs("rsi_recovery", "volumeEma"))
    minimumConfirmations: int = Field(**numeric_field_kwargs("rsi_recovery", "minimumConfirmations"))
    targetPct: float = Field(**numeric_field_kwargs("rsi_recovery", "targetPct"))
    setupExpiryBars: int = Field(**numeric_field_kwargs("rsi_recovery", "setupExpiryBars"))
    executionModel: Literal["SIGNAL_CLOSE", "NEXT_BAR_OPEN"] = "SIGNAL_CLOSE"
    buyCostBps: float = Field(**numeric_field_kwargs("rsi_recovery", "buyCostBps"))
    sellCostBps: float = Field(**numeric_field_kwargs("rsi_recovery", "sellCostBps"))
    slippageBps: float = Field(**numeric_field_kwargs("rsi_recovery", "slippageBps"))
    exitModel: Literal[
        "LEGACY_FIXED_TARGET",
        "FIXED_TP_SL",
        "ATR_DYNAMIC_TP_SL",
        "RSI_PROFIT_RISK_CONTROL",
    ] = "LEGACY_FIXED_TARGET"
    exitProtectionEnabled: bool = False
    fixedStopLossPct: float = Field(**numeric_field_kwargs("rsi_recovery", "fixedStopLossPct"))
    atrLength: int = Field(**numeric_field_kwargs("rsi_recovery", "atrLength"))
    stopAtrMultiplier: float = Field(**numeric_field_kwargs("rsi_recovery", "stopAtrMultiplier"))
    rewardRiskRatio: float = Field(**numeric_field_kwargs("rsi_recovery", "rewardRiskRatio"))
    minimumStopPct: float = Field(**numeric_field_kwargs("rsi_recovery", "minimumStopPct"))
    maximumStopPct: float = Field(**numeric_field_kwargs("rsi_recovery", "maximumStopPct"))
    positionSizing: Literal["FIXED_QUANTITY", "RISK_BUDGET"] = "FIXED_QUANTITY"
    quantityPerTrade: int = Field(**numeric_field_kwargs("rsi_recovery", "quantityPerTrade"))
    rupeeRiskBudget: float = Field(**numeric_field_kwargs("rsi_recovery", "rupeeRiskBudget"))
    maximumQuantity: int = Field(**numeric_field_kwargs("rsi_recovery", "maximumQuantity"))
    maximumCapitalPerPosition: float = Field(**numeric_field_kwargs("rsi_recovery", "maximumCapitalPerPosition"))
    maxOpenLotsPerSymbol: int = Field(**numeric_field_kwargs("rsi_recovery", "maxOpenLotsPerSymbol"))
    maxHoldingTradingDays: int = Field(**numeric_field_kwargs("rsi_recovery", "maxHoldingTradingDays"))
    timeExit: Literal["NEXT_TRADING_SESSION_OPEN"] = "NEXT_TRADING_SESSION_OPEN"
    minimumProfitPct: float = Field(**numeric_field_kwargs("rsi_recovery", "minimumProfitPct"))
    profitExitRsi: float = Field(**numeric_field_kwargs("rsi_recovery", "profitExitRsi"))
    upperRsiLevel: float = Field(**numeric_field_kwargs("rsi_recovery", "upperRsiLevel"))
    hardStopLossPct: float = Field(**numeric_field_kwargs("rsi_recovery", "hardStopLossPct"))
    rsiExitExecutionModel: Literal["SIGNAL_CLOSE", "NEXT_BAR_OPEN"] = "SIGNAL_CLOSE"
    oiFilterMode: Literal["OFF", "ADVISORY", "RESEARCH_FILTER", "ENFORCED"] = "OFF"
    oiLookbackBars: int = Field(default=3, ge=1, le=100)
    oiStrikesEachSide: int = Field(default=5, ge=0, le=20)
    oiMinimumPriceChangePct: float = Field(default=0.05, ge=0, le=100)
    oiMinimumChangePct: float = Field(default=0.5, ge=0, le=10_000)
    oiMaximumSpreadPct: float = Field(default=20.0, gt=0, le=1_000)
    oiStaleDataSeconds: int = Field(default=360, ge=1, le=86_400)
    oiMinimumValidContractFraction: float = Field(default=0.5, gt=0, le=1)
    oiMinimumFuturesVolume: float = Field(default=1, ge=0)
    oiVolatilityPriceRisePct: float = Field(default=0.25, ge=0, le=100)
    oiVolatilityIvRise: float = Field(default=0.5, ge=0, le=100)
    oiMinimumCoverage: float = Field(default=0.65, gt=0, le=1)
    oiOptionsWeight: float = Field(default=0.35, ge=0, le=1)
    oiFuturesWeight: float = Field(default=0.35, ge=0, le=1)
    oiSpotWeight: float = Field(default=0.30, ge=0, le=1)
    oiStronglyBearishThreshold: float = Field(default=-60, ge=-100, le=100)
    oiBearishThreshold: float = Field(default=-20, ge=-100, le=100)
    oiBullishThreshold: float = Field(default=20, ge=-100, le=100)
    oiStronglyBullishThreshold: float = Field(default=60, ge=-100, le=100)
    oiElevatedQualityThreshold: float = Field(default=95, ge=0, le=100)
    oiFailPolicy: Literal["SKIP", "ALLOW"] = "SKIP"
    top5OpeningRangeBreakoutConfiguration: Top5OpeningRangeBreakoutConfigurationRequest = Field(
        default_factory=Top5OpeningRangeBreakoutConfigurationRequest
    )

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, symbols: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(symbol.strip().upper().removesuffix(".NS") for symbol in symbols))
        if any(not symbol or not symbol.replace("&", "").replace("-", "").isalnum() for symbol in normalized):
            raise ValueError("Symbols may only contain letters, numbers, ampersands, and hyphens")
        return normalized

    @model_validator(mode="after")
    def validate_strategy_parameters(self) -> "BacktestRequest":
        if self.strategyKey is None:
            self.strategyKey = self.strategyMode
        if self.strategyKey != self.strategyMode:
            raise ValueError("strategyKey must match strategyMode")
        positive_recovery_values = (
            self.targetPct,
            self.fixedStopLossPct,
            self.stopAtrMultiplier,
            self.rewardRiskRatio,
            self.minimumStopPct,
            self.maximumStopPct,
            self.rupeeRiskBudget,
            self.maximumCapitalPerPosition,
            self.minimumProfitPct,
            self.hardStopLossPct,
        )
        if any(value <= 0 for value in positive_recovery_values):
            raise ValueError("RSI Recovery targets, stops, risk budgets, and capital limits must be positive")
        if self.hardStopLossPct >= 100:
            raise ValueError("RSI Recovery hard stop must be below 100 percent")
        if self.strategyMode == "rsi_range":
            if self.exitProtectionEnabled or self.exitModel != "LEGACY_FIXED_TARGET" or self.oiFilterMode != "OFF":
                raise ValueError("Position exit models and the NIFTY OI filter are available only for RSI Recovery")
            if not self.entryLow < self.entryHigh < self.exitLow < self.exitHigh:
                raise ValueError("RSI ranges must be ordered: entry low < entry high < exit low < exit high")
            return self

        if self.strategyMode == DAILY_WATCHLIST_STRATEGY_KEY:
            if self.timeframe != "5m":
                raise ValueError("Top-5 Opening Range Breakout requires completed 5-minute candles")
            self.top5OpeningRangeBreakoutConfiguration.validate_configuration()
            return self

        if (
            self.exitProtectionEnabled
            and "exitModel" not in self.model_fields_set
            and "targetPct" not in self.model_fields_set
        ):
            self.targetPct = 0.51

        if self.exitModel != "LEGACY_FIXED_TARGET":
            self.exitProtectionEnabled = True
        if self.exitModel == "RSI_PROFIT_RISK_CONTROL":
            if "rsiArmLow" not in self.model_fields_set:
                self.rsiArmLow = 20
            if "rsiArmHigh" not in self.model_fields_set:
                self.rsiArmHigh = 35
            if self.upperRsiLevel < self.profitExitRsi:
                raise ValueError("Upper RSI level cannot be below the profit-exit RSI")
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

    def rsi_profit_exit_config(self) -> RsiProfitExitConfig:
        if self.resolved_exit_model() != "RSI_PROFIT_RISK_CONTROL":
            raise ValueError("RSI profit-exit configuration requested for another exit model")
        return RsiProfitExitConfig(
            minimum_profit_pct=self.minimumProfitPct,
            profit_exit_rsi=self.profitExitRsi,
            upper_rsi_level=self.upperRsiLevel,
            stop_loss_pct=self.hardStopLossPct,
            exit_execution_model=self.rsiExitExecutionModel,
            max_holding_sessions=self.maxHoldingTradingDays,
            max_open_lots_per_symbol=self.maxOpenLotsPerSymbol,
            quantity_per_trade=self.quantityPerTrade,
        )

    def oi_config(self) -> NiftyOiConfig:
        return NiftyOiConfig(
            lookback_bars=self.oiLookbackBars,
            strikes_each_side=self.oiStrikesEachSide,
            minimum_price_change_pct=self.oiMinimumPriceChangePct,
            minimum_oi_change_pct=self.oiMinimumChangePct,
            maximum_spread_pct=self.oiMaximumSpreadPct,
            stale_data_seconds=self.oiStaleDataSeconds,
            minimum_valid_contract_fraction=self.oiMinimumValidContractFraction,
            minimum_futures_volume=self.oiMinimumFuturesVolume,
            minimum_component_coverage=self.oiMinimumCoverage,
            options_weight=self.oiOptionsWeight,
            futures_weight=self.oiFuturesWeight,
            spot_weight=self.oiSpotWeight,
            strongly_bearish_threshold=self.oiStronglyBearishThreshold,
            bearish_threshold=self.oiBearishThreshold,
            bullish_threshold=self.oiBullishThreshold,
            strongly_bullish_threshold=self.oiStronglyBullishThreshold,
            elevated_quality_threshold=self.oiElevatedQualityThreshold,
            volatility_price_rise_pct=self.oiVolatilityPriceRisePct,
            volatility_iv_rise=self.oiVolatilityIvRise,
            fail_policy=self.oiFailPolicy,
        ).validate()


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


class RsiExitComparisonRequest(BacktestRequest):
    symbols: list[str] = Field(min_length=1, max_length=750)
    strategyMode: Literal["rsi_recovery"] = "rsi_recovery"
    exitModel: Literal["RSI_PROFIT_RISK_CONTROL"] = "RSI_PROFIT_RISK_CONTROL"
    rsiArmZones: list[tuple[float, float]] = Field(
        default=[(20, 35), (25, 35), (30, 40)], min_length=1, max_length=20
    )
    rsiRecoveryThresholds: list[float] = Field(
        default=[35, 40, 45], min_length=1, max_length=20
    )
    profitExitRsiLevels: list[float] = Field(
        default=[50, 60, 70], min_length=1, max_length=20
    )
    minimumProfitPcts: list[float] = Field(
        default=[0.5, 1.0], min_length=1, max_length=20
    )
    hardStopLossPcts: list[float] = Field(
        default=[1.0, 1.5, 2.0, 3.0], min_length=1, max_length=20
    )
    maxHoldingSessionsGrid: list[int] = Field(
        default=[3, 5, 10], min_length=1, max_length=20
    )
    minimumValidationTrades: int = Field(default=20, ge=1, le=1_000_000)

    @field_validator("rsiArmZones")
    @classmethod
    def valid_arm_zones(
        cls, values: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        if any(not 0 <= low < high <= 100 for low, high in values):
            raise ValueError("Every RSI arm zone must satisfy 0 <= low < high <= 100")
        return values

    @field_validator("rsiRecoveryThresholds", "profitExitRsiLevels")
    @classmethod
    def valid_rsi_grid(cls, values: list[float]) -> list[float]:
        if any(not math.isfinite(value) or not 0 <= value <= 100 for value in values):
            raise ValueError("RSI comparison values must be finite and between 0 and 100")
        return values

    @field_validator("minimumProfitPcts", "hardStopLossPcts")
    @classmethod
    def positive_percentage_grid(cls, values: list[float]) -> list[float]:
        if any(not math.isfinite(value) or value <= 0 or value >= 100 for value in values):
            raise ValueError("Profit and stop comparison values must be between 0 and 100")
        return values

    @field_validator("maxHoldingSessionsGrid")
    @classmethod
    def positive_holding_grid(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("Holding-session comparison values must be positive")
        return values

    @model_validator(mode="after")
    def validate_grid_size(self) -> "RsiExitComparisonRequest":
        combinations = (
            len(set(self.rsiArmZones))
            * len(set(self.rsiRecoveryThresholds))
            * len(set(self.profitExitRsiLevels))
            * len(set(self.minimumProfitPcts))
            * len(set(self.hardStopLossPcts))
            * len(set(self.maxHoldingSessionsGrid))
        )
        if combinations > 5_000:
            raise ValueError("RSI exit comparison is limited to 5,000 configurations per run")
        return self

    def comparison_grid(self) -> RsiExitOptimizationGrid:
        return RsiExitOptimizationGrid(
            arm_zones=tuple(self.rsiArmZones),
            recovery_thresholds=tuple(self.rsiRecoveryThresholds),
            profit_exit_rsi_levels=tuple(self.profitExitRsiLevels),
            minimum_profit_pcts=tuple(self.minimumProfitPcts),
            stop_loss_pcts=tuple(self.hardStopLossPcts),
            max_holding_sessions=tuple(self.maxHoldingSessionsGrid),
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
    oiFilterMode: Literal["OFF", "ADVISORY", "ENFORCED"] = "OFF"
    oiLookbackBars: int = Field(default=3, ge=1, le=100)
    oiStrikesEachSide: int = Field(default=5, ge=0, le=20)
    oiMinimumPriceChangePct: float = Field(default=0.05, ge=0, le=100)
    oiMinimumChangePct: float = Field(default=0.5, ge=0, le=10_000)
    oiMaximumSpreadPct: float = Field(default=20.0, gt=0, le=1_000)
    oiStaleDataSeconds: int = Field(default=360, ge=1, le=86_400)
    oiMinimumValidContractFraction: float = Field(default=0.5, gt=0, le=1)
    oiMinimumFuturesVolume: float = Field(default=1, ge=0)
    oiVolatilityPriceRisePct: float = Field(default=0.25, ge=0, le=100)
    oiVolatilityIvRise: float = Field(default=0.5, ge=0, le=100)
    oiMinimumCoverage: float = Field(default=0.65, gt=0, le=1)
    oiOptionsWeight: float = Field(default=0.35, ge=0, le=1)
    oiFuturesWeight: float = Field(default=0.35, ge=0, le=1)
    oiSpotWeight: float = Field(default=0.30, ge=0, le=1)
    oiStronglyBearishThreshold: float = Field(default=-60, ge=-100, le=100)
    oiBearishThreshold: float = Field(default=-20, ge=-100, le=100)
    oiBullishThreshold: float = Field(default=20, ge=-100, le=100)
    oiStronglyBullishThreshold: float = Field(default=60, ge=-100, le=100)
    oiElevatedQualityThreshold: float = Field(default=95, ge=0, le=100)
    oiFailPolicy: Literal["SKIP", "ALLOW"] = "SKIP"

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
            oi_filter_mode=self.oiFilterMode,
            oi_lookback_bars=self.oiLookbackBars,
            oi_strikes_each_side=self.oiStrikesEachSide,
            oi_minimum_price_change_pct=self.oiMinimumPriceChangePct,
            oi_minimum_change_pct=self.oiMinimumChangePct,
            oi_maximum_spread_pct=self.oiMaximumSpreadPct,
            oi_stale_data_seconds=self.oiStaleDataSeconds,
            oi_minimum_valid_contract_fraction=self.oiMinimumValidContractFraction,
            oi_minimum_futures_volume=self.oiMinimumFuturesVolume,
            oi_volatility_price_rise_pct=self.oiVolatilityPriceRisePct,
            oi_volatility_iv_rise=self.oiVolatilityIvRise,
            oi_minimum_coverage=self.oiMinimumCoverage,
            oi_options_weight=self.oiOptionsWeight,
            oi_futures_weight=self.oiFuturesWeight,
            oi_spot_weight=self.oiSpotWeight,
            oi_strongly_bearish_threshold=self.oiStronglyBearishThreshold,
            oi_bearish_threshold=self.oiBearishThreshold,
            oi_bullish_threshold=self.oiBullishThreshold,
            oi_strongly_bullish_threshold=self.oiStronglyBullishThreshold,
            oi_elevated_quality_threshold=self.oiElevatedQualityThreshold,
            oi_fail_policy=self.oiFailPolicy,
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


class MarketSymbolRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_market_symbol(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().upper().removesuffix(".NS")


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
    # Fresh Dhan payloads use second-resolution epochs while CSV cache reads use
    # microseconds. Keep first and cached runs identical before comparing against
    # a Python datetime that can include microseconds.
    data.index = pd.DatetimeIndex(data.index).as_unit("us", round_ok=True)

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

    def _read_cache_without_ttl(self, path: Path) -> pd.DataFrame | None:
        """Read a persisted cache regardless of age for explicitly read-only consumers."""
        try:
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

    def cached_candles(
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
        """Read a local candle cache without fetching from Dhan on a miss."""
        spec = TIMEFRAMES[timeframe]
        source_key = spec.source_interval or "daily"
        cache_symbol = "NIFTY50" if benchmark else symbol
        raw = self._read_cache_without_ttl(
            self._cache_path(cache_symbol, source_key, duration_years)
        )
        if raw is None:
            raw = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
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
    rsi_profit_exit = (
        request.rsi_profit_exit_config()
        if exit_model == "RSI_PROFIT_RISK_CONTROL"
        else None
    )
    legacy_protection = exit_model == "LEGACY_PROTECTED_TARGET"
    position_backtest = legacy_protection or dynamic_exit is not None or rsi_profit_exit is not None
    # RSI Recovery deliberately does not consume the shared OI infrastructure.
    # Deprecated top-level OI request fields remain parseable for API compatibility,
    # but changing them cannot alter this strategy's candidates or execution.
    oi_config = request.oi_config()
    oi_repository = None
    quality_by_symbol: dict[str, float] = {}

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
    elif rsi_profit_exit is not None:
        warnings.extend([
            "RSI profitable exit with risk control is ON: valid RSI Recovery signals become fixed-quantity positions subject to the configured per-symbol open-lot limit.",
            "Exit priority is hard stop, profitable RSI exit, then next-session time exit. RSI alone never exits a position below the configured minimum profit.",
            "Stop monitoring and RSI exit evaluation begin after the entry candle. Gap stops fill at the following candle open.",
            "NEXT_BAR_OPEN RSI exits recheck the actual opening price; a gap that removes the required profit cancels that exit attempt.",
            "Skipped max-open-lot signals are preserved separately and never enter trade-profitability calculations.",
            "Position drawdown is exact per symbol; multi-symbol maximum drawdown is a conservative sum of independent symbol drawdowns rather than a cash-shared portfolio simulation.",
        ])
    else:
        warnings.extend([
            "There is no stop loss, no forced end-of-day exit, and no leverage. Signal observations remain open until target or dataset end.",
            "Every fresh RSI arm/recovery cycle is an independent signal observation, even while earlier observations for the same symbol remain open; this is not a portfolio-capital simulation.",
        ])
    requested_workers = int(os.environ.get("BACKTEST_WORKERS", "4"))
    worker_count = max(1, min(requested_workers, len(request.symbols), MAX_BACKTEST_WORKERS))
    warmup_bars = max(
        config.rsi_length + 2,
        config.ema_fast,
        config.ema_slow,
        config.volume_ema,
        dynamic_exit.atr_length if dynamic_exit is not None else 0,
    ) + 5

    def prepare(symbol: str) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
        try:
            candles = store.candles(
                symbol,
                request.timeframe,
                request.durationYears,
                analysis_start,
                now,
                warmup_bars=warmup_bars,
            )
            observations = (
                simulate_recovery_symbol(
                    symbol,
                    candles,
                    timeframe=request.timeframe,
                    config=config,
                    run_id=run_id,
                    analysis_start=analysis_start,
                )
                if oi_repository is not None
                else None
            )
            return {"symbol": symbol, "candles": candles, "observations": observations}, None
        except (DhanAPIError, ValueError, OSError, KeyError) as error:
            return None, {"symbol": symbol, "message": str(error)}

    if worker_count == 1:
        prepared_rows = [prepare(symbol) for symbol in request.symbols]
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="recovery-symbol",
        ) as executor:
            prepared_rows = list(executor.map(prepare, request.symbols))

    prepared = [item for item, _ in prepared_rows if item is not None]
    errors = [error for _, error in prepared_rows if error is not None]
    if oi_repository is not None and prepared:
        filtered = apply_oi_filter_chronologically(
            [item["observations"] for item in prepared],
            repository=oi_repository,
            mode=request.oiFilterMode,
            config=oi_config,
            quality_by_symbol=quality_by_symbol,
        )
        for item, observations in zip(prepared, filtered, strict=True):
            observations.update(
                summarize_recovery_trades(str(item["symbol"]), observations["trades"])
            )
            item["observations"] = observations

    def finalize(item: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
        symbol = str(item["symbol"])
        candles = item["candles"]
        observations = item["observations"]
        try:
            oi_skipped = list((observations or {}).get("oiSkippedSignals", []))
            if legacy_protection:
                result = simulate_protected_recovery_symbol(
                    symbol,
                    candles,
                    timeframe=request.timeframe,
                    recovery_config=config,
                    protection_config=protection,
                    run_id=run_id,
                    analysis_start=analysis_start,
                    observations=observations,
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
                    observations=observations,
                )
            elif rsi_profit_exit is not None:
                result = simulate_rsi_profit_exit_symbol(
                    symbol,
                    candles,
                    timeframe=request.timeframe,
                    recovery_config=config,
                    exit_config=rsi_profit_exit,
                    run_id=run_id,
                    analysis_start=analysis_start,
                    observations=observations,
                )
            else:
                result = observations or simulate_recovery_symbol(
                    symbol, candles, timeframe=request.timeframe, config=config,
                    run_id=run_id, analysis_start=analysis_start,
                )
            if oi_repository is not None:
                result["oiSkippedSignals"] = oi_skipped
                result["skippedOiSignals"] = len(oi_skipped)
                result["oiFilterMode"] = request.oiFilterMode
            return result, None
        except (DhanAPIError, ValueError, OSError, KeyError) as error:
            return None, {"symbol": symbol, "message": str(error)}

    if worker_count == 1:
        processed = [finalize(item) for item in prepared]
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="recovery-symbol",
        ) as executor:
            processed = list(executor.map(finalize, prepared))

    results = [result for result, _ in processed if result is not None]
    errors.extend(error for _, error in processed if error is not None)
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
    elif rsi_profit_exit is not None:
        summary = aggregate_rsi_profit_exit_results(results)
    else:
        summary = aggregate_recovery_results(results)
    public_exit_parameters = (
        dynamic_exit.public_parameters()
        if dynamic_exit is not None
        else rsi_profit_exit.public_parameters()
        if rsi_profit_exit is not None
        else protection.public_parameters()
    )
    public_exit_parameters["exitModel"] = exit_model
    legacy_configuration = {
        **config.public_parameters(),
        "exitModel": exit_model,
        "exitProtection": public_exit_parameters,
    }
    for result in results:
        result["strategyKey"] = RSI_RECOVERY_STRATEGY_KEY
        result["strategyName"] = RSI_RECOVERY_STRATEGY_NAME
        result["strategyVersion"] = RECOVERY_STRATEGY_VERSION
        result["configuration"] = legacy_configuration
    return {
        "metadata": {
            "runId": run_id,
            "strategyMode": "rsi_recovery",
            "strategyKey": RSI_RECOVERY_STRATEGY_KEY,
            "strategyName": RSI_RECOVERY_STRATEGY_NAME,
            "strategyVersion": RECOVERY_STRATEGY_VERSION,
            "strategyDescription": RSI_RECOVERY_DESCRIPTION,
            "configuration": legacy_configuration,
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
                else RSI_PROFIT_EXIT_VERSION
                if rsi_profit_exit is not None
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
            "oiFilter": {
                "mode": "OFF",
                "version": "nifty-oi-regime-filter-1.0.0",
                "parameters": oi_config.public(),
                "decisionOrder": "candidate BUY -> stock confirmations -> NIFTY OI regime -> position controls",
                "historyStatus": oi_repository.history_status() if oi_repository is not None else None,
            },
            "corporateActionAdjustment": "UNVERIFIED_SOURCE_AS_RECEIVED",
            "gitCommitSha": os.environ.get("GIT_COMMIT_SHA") or None,
        },
        "summary": summary,
        "results": results,
        "errors": errors,
        "warnings": warnings,
    }


def _support_symbols_from_file(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        headings = {
            str(name).strip().casefold(): str(name)
            for name in (reader.fieldnames or [])
        }
        symbol_heading = headings.get("symbol")
        if not symbol_heading:
            raise ValueError("Breadth universe CSV must contain a Symbol column")
        return sorted({
            str(row.get(symbol_heading) or "").strip().upper().removesuffix(".NS")
            for row in reader
            if str(row.get(symbol_heading) or "").strip()
        })


def _evenly_spaced_symbols(symbols: list[str], limit: int) -> list[str]:
    ordered = sorted(set(symbols))
    if len(ordered) <= limit:
        return ordered
    indices = np.linspace(0, len(ordered) - 1, num=limit, dtype=int)
    return [ordered[int(index)] for index in indices]


def _market_worker_default() -> int:
    available = os.cpu_count() or 2
    return max(1, min(available - 1, 8))


def _market_task_batches(tasks: list[dict[str, Any]], workers: int) -> list[list[dict[str, Any]]]:
    if not tasks:
        return []
    # A fixed process pool still reuses each process for many symbols. Keeping the
    # dispatch unit small makes progress observable and prevents one slow symbol
    # from hiding the completion of every other symbol in a large batch.
    configured = int(os.environ.get("BACKTEST_SYMBOL_BATCH_SIZE", "1"))
    batch_size = max(1, min(configured, len(tasks)))
    return [tasks[index:index + batch_size] for index in range(0, len(tasks), batch_size)]


def _execute_market_batches(
    tasks: list[dict[str, Any]],
    workers: int,
    worker: Any,
    *,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> list[dict[str, Any]]:
    batches = _market_task_batches(tasks, workers)
    if workers == 1:
        nested = []
        completed = 0
        for batch in batches:
            if cancel_event is not None and cancel_event.is_set():
                raise BacktestCancelledError("Backtest cancellation requested")
            nested.append(worker(batch))
            completed += len(batch)
            if progress_callback is not None:
                progress_callback(completed)
    else:
        executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
        )
        # Do not pickle and queue the complete universe at once. Two dispatch
        # units per worker keeps every process busy while bounding queued memory.
        batch_iterator = iter(enumerate(batches))
        futures: dict[concurrent.futures.Future[Any], tuple[int, int]] = {}

        def submit_next() -> bool:
            try:
                index, batch = next(batch_iterator)
            except StopIteration:
                return False
            futures[executor.submit(worker, batch)] = (index, len(batch))
            return True

        for _ in range(min(len(batches), workers * 2)):
            submit_next()
        completed = 0
        ordered: dict[int, list[dict[str, Any]]] = {}
        cancelled = False
        try:
            while futures:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    for pending in futures:
                        pending.cancel()
                    raise BacktestCancelledError("Backtest cancellation requested")
                done, _ = concurrent.futures.wait(
                    futures,
                    timeout=0.25,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in sorted(done, key=lambda item: futures[item][0]):
                    index, size = futures.pop(future)
                    ordered[index] = future.result()
                    completed += size
                    if progress_callback is not None:
                        progress_callback(completed)
                    submit_next()
            nested = [ordered[index] for index in range(len(batches))]
        finally:
            executor.shutdown(wait=not cancelled, cancel_futures=cancelled)
    return [row for batch in nested for row in batch]


def _parent_peak_memory_bytes() -> int:
    if resource is None:
        return 0
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak if os.uname().sysname == "Darwin" else peak * 1024


class BacktestCancelledError(RuntimeError):
    pass


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


def run_rsi_exit_comparison(
    request: RsiExitComparisonRequest,
    store: HistoricalDataStore,
    now_ist: datetime | None = None,
) -> dict[str, Any]:
    now = (now_ist or datetime.now(IST)).astimezone(IST)
    analysis_start = now - timedelta(days=round(365.25 * request.durationYears))
    universe = set(store.universe())
    unavailable = [symbol for symbol in request.symbols if symbol not in universe]
    if unavailable:
        raise ValueError("Symbols are not in symbols.csv: " + ", ".join(unavailable))
    grid = request.comparison_grid()
    warmup_bars = max(
        request.rsiLength + 2,
        request.emaFast,
        request.emaSlow,
        request.volumeEma,
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
            thread_name_prefix="rsi-exit-comparison-load",
        ) as executor:
            loaded = list(executor.map(load, request.symbols))
    symbol_candles = {
        symbol: candles
        for symbol, candles, error in loaded
        if error is None and candles is not None
    }
    errors = [error for _, _, error in loaded if error is not None]
    if not symbol_candles:
        raise ValueError("No selected symbol produced valid historical candles for RSI exit comparison")
    payload = evaluate_rsi_exit_grid(
        symbol_candles,
        timeframe=request.timeframe,
        base_recovery_config=request.recovery_config(),
        base_exit_config=request.rsi_profit_exit_config(),
        grid=grid,
        analysis_start=analysis_start,
        analysis_end=now,
        duration_years=request.durationYears,
        run_id=request.runId or str(uuid.uuid4()),
        minimum_validation_trades=request.minimumValidationTrades,
    )
    payload["metadata"].update({
        "strategyMode": "rsi_recovery_position",
        "strategyVersion": RECOVERY_STRATEGY_VERSION,
        "positionBacktestVersion": RSI_PROFIT_EXIT_VERSION,
        "exitModel": "RSI_PROFIT_RISK_CONTROL",
        "entryExecutionModel": request.executionModel,
        "rsiExitExecutionModel": request.rsiExitExecutionModel,
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


def _vwap_support_plan(
    *,
    universe: set[str],
    requested_symbols: list[str],
    config: VwapPullbackConfig,
    sector_by_symbol: Mapping[str, str],
    breadth_file: Path | None,
) -> dict[str, Any]:
    configured_breadth = _support_symbols_from_file(breadth_file)
    breadth_pool = [
        symbol for symbol in (configured_breadth or sorted(universe))
        if symbol in universe
    ]
    breadth_symbols = _evenly_spaced_symbols(
        breadth_pool,
        max(config.minimum_breadth_symbols, 50),
    )
    requested_sectors = {
        sector_by_symbol.get(symbol)
        for symbol in requested_symbols
        if sector_by_symbol.get(symbol)
    }
    sector_symbols = sorted({
        symbol
        for symbol, sector in sector_by_symbol.items()
        if symbol in universe and sector in requested_sectors
    })
    return {
        "breadthSymbols": breadth_symbols,
        "sectorSymbols": sector_symbols,
        "allSymbols": sorted(set(breadth_symbols) | set(sector_symbols)),
        "breadthSource": (
            str(breadth_file)
            if configured_breadth and breadth_file is not None
            else "DETERMINISTIC_LOCAL_UNIVERSE_SAMPLE"
        ),
    }


def _local_nifty_candles(
    store: HistoricalDataStore,
    duration_years: int,
    analysis_start: datetime,
    now: datetime,
) -> pd.DataFrame:
    path = store._cache_path("NIFTY50", "5", duration_years)
    if not path.is_file():
        return pd.DataFrame()
    raw = pd.read_csv(path, index_col="Timestamp", parse_dates=["Timestamp"])
    raw.index = pd.DatetimeIndex(raw.index)
    raw.index = raw.index.tz_localize(IST) if raw.index.tz is None else raw.index.tz_convert(IST)
    return prepare_candles(raw, "5m", analysis_start, now, warmup_bars=100)


def _vwap_period_metrics(
    trades: Sequence[Mapping[str, Any]],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    selected = [
        item for item in trades
        if _as_ist_timestamp(item["entryTimestamp"]) >= start
        and _as_ist_timestamp(item["entryTimestamp"]) < end
    ]
    net = [float(item["netPnl"]) for item in selected]
    winners = [value for value in net if value > 0]
    losers = [value for value in net if value < 0]
    gross_loss = abs(sum(losers))
    cumulative = np.cumsum(np.asarray(net, dtype=float)) if net else np.asarray([], dtype=float)
    equity = np.concatenate(([0.0], cumulative))
    peaks = np.maximum.accumulate(equity)
    maximum_drawdown = float(np.max(peaks - equity)) if len(equity) else 0.0
    consecutive = 0
    maximum_consecutive = 0
    for value in net:
        consecutive = consecutive + 1 if value < 0 else 0
        maximum_consecutive = max(maximum_consecutive, consecutive)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "trades": len(selected),
        "winRate": round(len(winners) / len(selected) * 100.0, 2) if selected else 0.0,
        "averageWinner": round(float(np.mean(winners)), 2) if winners else None,
        "averageLoser": round(float(np.mean(losers)), 2) if losers else None,
        "netPnlAfterCosts": round(sum(net), 2),
        "expectancy": round(float(np.mean(net)), 2) if net else None,
        "profitFactor": round(sum(winners) / gross_loss, 4) if gross_loss else None,
        "maximumDrawdown": round(maximum_drawdown, 2),
        "averageR": round(float(np.mean([float(item["rMultiple"]) for item in selected])), 6) if selected else None,
        "maximumConsecutiveLosses": maximum_consecutive,
        "tradesPerDay": round(len(selected) / max((end.date() - start.date()).days, 1), 6),
    }


def _as_ist_timestamp(value: Any) -> datetime:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(IST)
    else:
        stamp = stamp.tz_convert(IST)
    return stamp.to_pydatetime()


def _vwap_walk_forward(
    trades: Sequence[Mapping[str, Any]],
    analysis_start: datetime,
    analysis_end: datetime,
    duration_years: int,
) -> dict[str, Any]:
    folds: list[dict[str, Any]] = []
    if duration_years == 1:
        validation_start = (pd.Timestamp(analysis_start) + pd.DateOffset(months=9)).to_pydatetime()
        folds.append({
            "development": _vwap_period_metrics(trades, analysis_start, validation_start),
            "validation": _vwap_period_metrics(trades, validation_start, analysis_end),
        })
        method = "First 9 months development; final 3 months untouched validation"
    else:
        cursor = pd.Timestamp(analysis_start)
        end = pd.Timestamp(analysis_end)
        while cursor + pd.DateOffset(months=15) <= end:
            validation_start = cursor + pd.DateOffset(months=12)
            validation_end = validation_start + pd.DateOffset(months=3)
            folds.append({
                "development": _vwap_period_metrics(
                    trades, cursor.to_pydatetime(), validation_start.to_pydatetime()
                ),
                "validation": _vwap_period_metrics(
                    trades, validation_start.to_pydatetime(), validation_end.to_pydatetime()
                ),
            })
            cursor += pd.DateOffset(months=3)
        method = "12 months development; 3 months validation; roll forward by 3 months"
    return {
        "method": method,
        "parametersOptimizedOnValidation": False,
        "folds": folds,
        "label": "Research candidate — paper trading required",
    }


def run_vwap_pullback_backtest(
    request: BacktestRequest,
    store: HistoricalDataStore,
    now_ist: datetime | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    raise ValueError("Retired strategy - Market-Aligned VWAP Pullback Scalper cannot run again")

    # Retained temporarily below solely to preserve source-level recovery history.
    # The active request schema and dispatcher cannot reach this retired code.
    started_clock = time.perf_counter()
    started_at = datetime.now(IST)
    now = (now_ist or started_at).astimezone(IST)
    analysis_start = now - timedelta(days=round(365.25 * request.durationYears))
    run_id = request.runId or str(uuid.uuid4())
    requested = request.vwapPullbackConfiguration
    config = requested.strategy_config().validate()
    universe = set(store.universe())
    unavailable = [symbol for symbol in request.symbols if symbol not in universe]
    if unavailable:
        raise ValueError("Symbols are not in symbols.csv: " + ", ".join(unavailable))

    sector_value = (
        os.environ.get("MARKET_CONTEXT_SECTOR_MAP_FILE")
        or os.environ.get("MARKET_ALIGNED_SECTOR_MAP_FILE")
    )
    breadth_value = (
        os.environ.get("MARKET_CONTEXT_BREADTH_UNIVERSE_FILE")
        or os.environ.get("MARKET_ALIGNED_BREADTH_UNIVERSE_FILE")
    )
    sector_path = Path(sector_value).expanduser() if sector_value else None
    breadth_path = Path(breadth_value).expanduser() if breadth_value else None
    if sector_path is not None and not sector_path.is_absolute():
        raise ValueError("MARKET_CONTEXT_SECTOR_MAP_FILE must be an absolute path")
    if breadth_path is not None and not breadth_path.is_absolute():
        raise ValueError("MARKET_CONTEXT_BREADTH_UNIVERSE_FILE must be an absolute path")
    sector_by_symbol = load_vwap_sector_mapping(sector_path)
    support_plan = _vwap_support_plan(
        universe=universe,
        requested_symbols=request.symbols,
        config=config,
        sector_by_symbol=sector_by_symbol,
        breadth_file=breadth_path,
    )

    raw_paths = {
        symbol: store._cache_path(symbol, "5", request.durationYears)
        for symbol in sorted(set(request.symbols) | set(support_plan["allSymbols"]))
    }
    configuration_hash = vwap_fingerprint(requested.public())
    fingerprint = vwap_fingerprint({
        "strategyKey": VWAP_PULLBACK_STRATEGY_KEY,
        "strategyVersion": VWAP_PULLBACK_STRATEGY_VERSION,
        "featureVersion": VWAP_PULLBACK_FEATURE_VERSION,
        "sessionVersion": VWAP_PULLBACK_SESSION_VERSION,
        "portfolioVersion": VWAP_PULLBACK_PORTFOLIO_VERSION,
        "configuration": requested.public(),
        "symbols": request.symbols,
        "universeMode": request.universeMode,
        "durationYears": request.durationYears,
        "analysisStartCompletedBucket": pd.Timestamp(analysis_start).floor("5min").isoformat(),
        "analysisEndCompletedBucket": pd.Timestamp(now).floor("5min").isoformat(),
        "timeframe": request.timeframe,
        "executionModel": requested.executionModel,
        "data": {symbol: vwap_file_stat_fingerprint(path) for symbol, path in raw_paths.items()},
        "nifty": vwap_file_stat_fingerprint(store._cache_path("NIFTY50", "5", request.durationYears)),
        "sector": vwap_file_stat_fingerprint(sector_path),
        "breadth": vwap_file_stat_fingerprint(breadth_path),
        "oi": "OFF" if config.oi_mode == "OFF" else "POINT_IN_TIME_REPOSITORY",
    })
    result_cache = VwapPullbackResultCache(
        store.cache_directory / "vwap-pullback-results-v2"
    )
    if request.cachePolicy == "USE_CACHE":
        cached = result_cache.load(fingerprint)
        if cached is not None:
            return cached

    worker_limit = max(
        1,
        min(int(os.environ.get("BACKTEST_WORKERS", str(_market_worker_default()))), 8),
    )
    workers = min(worker_limit, len(request.symbols))
    feature_root = store.cache_directory / "vwap-pullback-features-v2"
    common = {
        "cacheDirectory": str(store.cache_directory),
        "featureCacheDirectory": str(feature_root),
        "config": config,
        "durationYears": request.durationYears,
        "analysisStart": analysis_start,
        "now": now,
    }
    if progress_callback is not None:
        progress_callback({
            "currentStage": "STOCK_FEATURES_AND_PULLBACKS",
            "symbolsCompleted": 0,
            "symbolsTotal": len(request.symbols),
            "workersActive": workers,
        })
    rows = _execute_market_batches(
        [{**common, "symbol": symbol, "detectCandidates": True} for symbol in request.symbols],
        workers,
        prepare_vwap_symbol_batch,
        cancel_event=cancel_event,
        progress_callback=(
            lambda completed: progress_callback({
                "currentStage": "STOCK_FEATURES_AND_PULLBACKS",
                "symbolsCompleted": completed,
                "symbolsTotal": len(request.symbols),
                "workersActive": workers,
            }) if progress_callback is not None else None
        ),
    )
    prepared = [row["item"] for row in rows if row.get("item") is not None]
    errors = [row["error"] for row in rows if row.get("error") is not None]
    support_errors: list[dict[str, str]] = []
    feature_paths = {str(item["symbol"]): str(item["featurePath"]) for item in prepared}
    symbol_results = [item["result"] for item in prepared if item.get("result") is not None]
    candidates = [candidate for result in symbol_results for candidate in result.get("candidates", [])]
    support_symbols = [symbol for symbol in support_plan["allSymbols"] if symbol not in feature_paths]
    if support_symbols:
        support_workers = max(1, min(worker_limit, len(support_symbols)))
        if progress_callback is not None:
            progress_callback({
                "currentStage": "SUPPORTING_MARKET_FEATURES",
                "symbolsCompleted": len(request.symbols),
                "symbolsTotal": len(request.symbols),
                "supportSymbolsCompleted": 0,
                "supportSymbolsTotal": len(support_symbols),
                "candidatesFound": len(candidates),
                "workersActive": support_workers,
            })
        support_rows = _execute_market_batches(
            [{**common, "symbol": symbol, "detectCandidates": False} for symbol in support_symbols],
            support_workers,
            prepare_vwap_symbol_batch,
            cancel_event=cancel_event,
        )
        feature_paths.update({
            str(row["item"]["symbol"]): str(row["item"]["featurePath"])
            for row in support_rows if row.get("item") is not None
        })
        support_errors.extend(
            row["error"] for row in support_rows if row.get("error") is not None
        )

    def unique_error_rows(values: Sequence[dict[str, str]]) -> list[dict[str, str]]:
        unique: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for error in values:
            key = (str(error.get("symbol") or ""), str(error.get("message") or ""))
            if key not in seen:
                unique.append(error)
                seen.add(key)
        return unique

    errors = unique_error_rows(errors)
    support_errors = unique_error_rows(support_errors)
    if cancel_event is not None and cancel_event.is_set():
        raise BacktestCancelledError("Backtest cancellation requested")

    if progress_callback is not None:
        progress_callback({
            "currentStage": "POINT_IN_TIME_MARKET_CONTEXT",
            "symbolsCompleted": len(request.symbols),
            "symbolsTotal": len(request.symbols),
            "candlesProcessed": sum(int(item["metrics"].get("candles", 0)) for item in prepared),
            "candidatesFound": len(candidates),
            "workersActive": 1,
        })
    nifty = _local_nifty_candles(store, request.durationYears, analysis_start, now)
    nifty_context = build_nifty_candidate_context(nifty, candidates, config)
    support_context = build_vwap_supporting_context(
        candidates,
        feature_paths_by_symbol=feature_paths,
        breadth_symbols=support_plan["breadthSymbols"],
        sector_by_symbol=sector_by_symbol,
        config=config,
    )
    oi_repository = None
    if config.oi_mode == "ADVISORY":
        try:
            oi_repository = get_oi_repository()
        except (OSError, RuntimeError, ValueError):
            oi_repository = None
    enriched = enrich_vwap_candidates(
        candidates,
        nifty_by_candidate=nifty_context,
        support_by_candidate=support_context,
        config=config,
        oi_repository=oi_repository,
    )
    if progress_callback is not None:
        progress_callback({
            "currentStage": "CHRONOLOGICAL_PORTFOLIO",
            "symbolsCompleted": len(request.symbols),
            "symbolsTotal": len(request.symbols),
            "candidatesFound": len(enriched),
            "acceptedSignals": sum(not item.get("primaryReason") for item in enriched),
            "workersActive": 1,
        })
    trades, rejected = execute_vwap_portfolio(enriched, config)
    by_symbol_candidates: dict[str, list[dict[str, Any]]] = {}
    by_symbol_trades: dict[str, list[dict[str, Any]]] = {}
    by_symbol_rejected: dict[str, list[dict[str, Any]]] = {}
    for candidate in enriched:
        by_symbol_candidates.setdefault(str(candidate["symbol"]), []).append(candidate)
    for trade in trades:
        by_symbol_trades.setdefault(str(trade["symbol"]), []).append(trade)
    for candidate in rejected:
        by_symbol_rejected.setdefault(str(candidate["symbol"]), []).append(candidate)
    results = []
    for result in symbol_results:
        symbol = str(result["symbol"])
        results.append({
            **{key: value for key, value in result.items() if key not in {"candidates", "events"}},
            "rawCandidates": len(by_symbol_candidates.get(symbol, [])),
            "executedTrades": len(by_symbol_trades.get(symbol, [])),
            "trades": by_symbol_trades.get(symbol, []),
            "candidateDiagnostics": by_symbol_candidates.get(symbol, []),
            "skippedCandidates": by_symbol_rejected.get(symbol, []),
        })
    summary = summarize_vwap_results(symbol_results, enriched, trades, rejected, config)
    completed_at = datetime.now(IST)
    response = {
        "metadata": {
            "runId": run_id,
            "strategyMode": VWAP_PULLBACK_STRATEGY_KEY,
            "strategyKey": VWAP_PULLBACK_STRATEGY_KEY,
            "strategyName": VWAP_PULLBACK_STRATEGY_NAME,
            "strategyDescription": VWAP_PULLBACK_DESCRIPTION,
            "strategyVersion": VWAP_PULLBACK_STRATEGY_VERSION,
            "featureCodeVersion": VWAP_PULLBACK_FEATURE_VERSION,
            "sessionRuleVersion": VWAP_PULLBACK_SESSION_VERSION,
            "portfolioRuleVersion": VWAP_PULLBACK_PORTFOLIO_VERSION,
            "startedAt": started_at.isoformat(),
            "completedAt": completed_at.isoformat(),
            "generatedAt": completed_at.isoformat(),
            "analysisStart": analysis_start.isoformat(),
            "analysisEnd": now.isoformat(),
            "durationYears": request.durationYears,
            "timeframe": request.timeframe,
            "universeMode": request.universeMode,
            "symbolsRequested": len(request.symbols),
            "symbolsProcessed": len(results),
            "symbolsFailed": len(errors),
            "workerCount": workers,
            "runtimeSeconds": round(time.perf_counter() - started_clock, 4),
            "configuration": requested.public(),
            "effectiveConfiguration": requested.public(),
            "configurationHash": configuration_hash,
            "fingerprint": fingerprint,
            "dataSnapshot": fingerprint,
            "cachedResult": False,
            "resultSource": "FRESH_CALCULATION",
            "researchLabel": "Research candidate — paper trading required",
            "historicalSpread": "UNAVAILABLE_ADVISORY",
            "supportingData": {
                "sectorMappingFile": str(sector_path) if sector_path else None,
                "breadthSource": support_plan["breadthSource"],
                "breadthSymbols": len(support_plan["breadthSymbols"]),
                "sectorSymbols": len(support_plan["sectorSymbols"]),
                "supportSymbolsUnavailable": len(support_errors),
            },
        },
        "summary": summary,
        "results": results,
        "trades": trades,
        "rejectedCandidates": rejected,
        "walkForwardValidation": _vwap_walk_forward(
            trades, analysis_start, now, request.durationYears
        ),
        "errors": errors,
        "supportingDataErrors": support_errors,
        "warnings": [
            "Signals and indicators use completed candles; NEXT_BAR_OPEN entries do not use the entry candle for signal decisions.",
            "Historical bid/ask spread is unavailable and is not fabricated.",
            "Sector, breadth, relative strength, and optional OI affect quality diagnostics or ranking; they are not mandatory gates by default.",
            "The current symbol universe introduces survivorship bias. Past performance does not guarantee future returns.",
            "Research candidate — paper trading required.",
        ],
    }
    response["metadata"]["resultCacheBytes"] = result_cache.save(fingerprint, response)
    return response


def _local_top_5_opening_range_nifty(
    store: HistoricalDataStore,
    duration_years: int,
    analysis_start: datetime,
    now: datetime,
) -> pd.DataFrame:
    path = store._cache_path("NIFTY50", "5", duration_years)
    if not path.is_file():
        return pd.DataFrame()
    raw = pd.read_csv(path, index_col="Timestamp", parse_dates=["Timestamp"])
    raw.index = pd.DatetimeIndex(raw.index)
    raw.index = raw.index.tz_localize(IST) if raw.index.tz is None else raw.index.tz_convert(IST)
    return prepare_candles(raw, "5m", analysis_start, now, warmup_bars=2_000)


def run_top_5_opening_range_breakout_backtest(
    request: BacktestRequest,
    store: HistoricalDataStore,
    now_ist: datetime | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    started_clock = time.perf_counter()
    started_at = datetime.now(IST)
    now = (now_ist or started_at).astimezone(IST)
    analysis_start = now - timedelta(days=round(365.25 * request.durationYears))
    run_id = request.runId or str(uuid.uuid4())
    requested = request.top5OpeningRangeBreakoutConfiguration
    config = requested.strategy_config().validate()
    universe = set(store.universe())
    unavailable = [symbol for symbol in request.symbols if symbol not in universe]
    if unavailable:
        raise ValueError("Symbols are not in symbols.csv: " + ", ".join(unavailable))

    sector_value = (
        os.environ.get("MARKET_CONTEXT_SECTOR_MAP_FILE")
        or os.environ.get("MARKET_ALIGNED_SECTOR_MAP_FILE")
    )
    sector_path = Path(sector_value).expanduser() if sector_value else None
    if sector_path is not None and not sector_path.is_absolute():
        raise ValueError("MARKET_CONTEXT_SECTOR_MAP_FILE must be an absolute path")
    sector_by_symbol = load_vwap_sector_mapping(sector_path)
    requested_sectors = {
        sector_by_symbol.get(symbol)
        for symbol in request.symbols
        if sector_by_symbol.get(symbol)
    }
    support_symbols = sorted({
        symbol for symbol, sector in sector_by_symbol.items()
        if symbol in universe and sector in requested_sectors and symbol not in request.symbols
    })
    all_data_symbols = sorted(set(request.symbols) | set(support_symbols))
    raw_paths = {
        symbol: store._cache_path(symbol, "5", request.durationYears)
        for symbol in all_data_symbols
    }
    configuration_hash = daily_watchlist_fingerprint(requested.public())
    fingerprint = daily_watchlist_fingerprint({
        "strategyKey": DAILY_WATCHLIST_STRATEGY_KEY,
        "strategyVersion": DAILY_WATCHLIST_STRATEGY_VERSION,
        "featureVersion": DAILY_WATCHLIST_FEATURE_VERSION,
        "sessionVersion": DAILY_WATCHLIST_SESSION_VERSION,
        "portfolioVersion": DAILY_WATCHLIST_PORTFOLIO_VERSION,
        "watchlistVersion": WATCHLIST_RULE_VERSION,
        "openingRangeVersion": OPENING_RANGE_RULE_VERSION,
        "configuration": requested.public(),
        "symbols": request.symbols,
        "universeMode": request.universeMode,
        "durationYears": request.durationYears,
        "timeframe": request.timeframe,
        "analysisStart": pd.Timestamp(analysis_start).floor("5min").isoformat(),
        "analysisEnd": pd.Timestamp(now).floor("5min").isoformat(),
        "data": {
            symbol: daily_watchlist_file_fingerprint(path)
            for symbol, path in raw_paths.items()
        },
        "nifty": daily_watchlist_file_fingerprint(
            store._cache_path("NIFTY50", "5", request.durationYears)
        ),
        "sector": daily_watchlist_file_fingerprint(sector_path),
    })
    result_cache = DailyWatchlistResultCache(
        store.cache_directory / "top-5-opening-range-breakout-results-v1"
    )
    if request.cachePolicy == "USE_CACHE":
        cached = result_cache.load(fingerprint)
        if cached is not None:
            return cached

    worker_limit = max(
        1,
        min(int(os.environ.get("BACKTEST_WORKERS", str(_market_worker_default()))), 8),
    )
    feature_root = store.cache_directory / "top-5-opening-range-breakout-features-v1"
    common = {
        "cacheDirectory": str(store.cache_directory),
        "featureCacheDirectory": str(feature_root),
        "config": config,
        "durationYears": request.durationYears,
        "analysisStart": analysis_start,
        "now": now,
    }
    workers = max(1, min(worker_limit, len(request.symbols)))
    if progress_callback is not None:
        progress_callback({
            "currentStage": "TOP_5_OPENING_RANGE_STOCK_FEATURES",
            "symbolsCompleted": 0,
            "symbolsTotal": len(request.symbols),
            "workersActive": workers,
        })
    rows = _execute_market_batches(
        [{**common, "symbol": symbol, "detectCandidates": True} for symbol in request.symbols],
        workers,
        prepare_daily_watchlist_symbol_batch,
        cancel_event=cancel_event,
        progress_callback=(
            lambda completed: progress_callback({
                "currentStage": "TOP_5_OPENING_RANGE_STOCK_FEATURES",
                "symbolsCompleted": completed,
                "symbolsTotal": len(request.symbols),
                "workersActive": workers,
            }) if progress_callback is not None else None
        ),
    )
    prepared = [row["item"] for row in rows if row.get("item") is not None]
    errors = [row["error"] for row in rows if row.get("error") is not None]
    feature_paths = {str(item["symbol"]): str(item["featurePath"]) for item in prepared}
    opening_candidates = [
        candidate for item in prepared for candidate in item.get("openingCandidates", [])
    ]
    midday_candidates = [
        candidate for item in prepared for candidate in item.get("middayCandidates", [])
    ]
    missing_support = [symbol for symbol in support_symbols if symbol not in feature_paths]
    support_errors: list[dict[str, str]] = []
    if missing_support:
        support_workers = max(1, min(worker_limit, len(missing_support)))
        if progress_callback is not None:
            progress_callback({
                "currentStage": "TOP_5_OPENING_RANGE_SECTOR_CONTEXT",
                "symbolsCompleted": len(request.symbols),
                "symbolsTotal": len(request.symbols),
                "supportSymbolsCompleted": 0,
                "supportSymbolsTotal": len(missing_support),
                "workersActive": support_workers,
            })
        support_rows = _execute_market_batches(
            [{**common, "symbol": symbol, "detectCandidates": False} for symbol in missing_support],
            support_workers,
            prepare_daily_watchlist_symbol_batch,
            cancel_event=cancel_event,
        )
        feature_paths.update({
            str(row["item"]["symbol"]): str(row["item"]["featurePath"])
            for row in support_rows if row.get("item") is not None
        })
        support_errors = [row["error"] for row in support_rows if row.get("error") is not None]
    if cancel_event is not None and cancel_event.is_set():
        raise BacktestCancelledError("Backtest cancellation requested")

    opening_minutes = (
        datetime_time.fromisoformat(config.selection_time).hour * 60
        + datetime_time.fromisoformat(config.selection_time).minute
    )
    ending_minutes = (
        datetime_time.fromisoformat(config.rescan_end_time).hour * 60
        + datetime_time.fromisoformat(config.rescan_end_time).minute
    )
    snapshot_columns = [
        "Open", "High", "Low", "Close", "Volume", "RSI", "EMAFast", "EMASlow",
        "ATR", "SessionVWAP", "AverageTradedValue", "ValidOHLCV", "RollingWindowVolume",
        "RollingTradedValue", "RollingReturnPct", "RollingWindowRvol", "PriceAccelerationPct",
        "CloseLocation", "UpperWickFraction", "DistanceFromVwapAtr", "CandleRangeAtr",
        "BullishEmaTrend", "EmaFastRising", "AtrPct", "MedianDailyTradedValue",
        "OpeningTradedValue", "DailyAtrPct", "OpeningGapPct", "SpreadPct",
    ]

    def read_rescan_rows(path_value: str) -> pd.DataFrame:
        frame = pd.read_parquet(path_value, columns=snapshot_columns)
        frame.index = pd.DatetimeIndex(frame.index)
        frame.index = frame.index.tz_localize(IST) if frame.index.tz is None else frame.index.tz_convert(IST)
        minutes = np.asarray(frame.index.hour * 60 + frame.index.minute)
        mask = (
            (minutes >= opening_minutes)
            & (minutes <= ending_minutes)
            & ((minutes - opening_minutes) % config.rescan_interval_minutes == 0)
            & (frame.index >= pd.Timestamp(analysis_start))
            & (frame.index <= pd.Timestamp(now))
        )
        return frame.loc[mask]

    candidate_frames = {
        symbol: read_rescan_rows(feature_paths[symbol])
        for symbol in request.symbols if symbol in feature_paths
    }
    context_frames = {
        symbol: (
            candidate_frames[symbol]
            if symbol in candidate_frames
            else read_rescan_rows(path)
        )
        for symbol, path in feature_paths.items()
    }
    nifty_candles = _local_top_5_opening_range_nifty(
        store, request.durationYears, analysis_start, now
    )
    nifty_features = (
        calculate_watchlist_features(nifty_candles, config)
        if not nifty_candles.empty else None
    )
    if nifty_features is not None:
        nifty_minutes = np.asarray(nifty_features.index.hour * 60 + nifty_features.index.minute)
        nifty_features = nifty_features.loc[
            (nifty_minutes >= opening_minutes)
            & (nifty_minutes <= ending_minutes)
            & ((nifty_minutes - opening_minutes) % config.rescan_interval_minutes == 0)
            & (nifty_features.index >= pd.Timestamp(analysis_start))
            & (nifty_features.index <= pd.Timestamp(now))
        ]
    selection_seed = daily_watchlist_fingerprint({
        "version": WATCHLIST_RULE_VERSION,
        "symbols": sorted(request.symbols),
        "analysisStart": pd.Timestamp(analysis_start).floor("5min").isoformat(),
        "analysisEnd": pd.Timestamp(now).floor("5min").isoformat(),
        "data": {
            symbol: daily_watchlist_file_fingerprint(raw_paths[symbol])
            for symbol in sorted(request.symbols)
        },
    })
    frozen_config = replace(config, mode="FROZEN_OPEN")
    rolling_config = replace(config, mode="ROLLING")
    top_two_config = replace(
        frozen_config,
        selected_symbols=min(2, len(request.symbols)),
        primary_symbols=min(2, len(request.symbols)),
        maximum_replacements_per_rescan=min(2, len(request.symbols)),
    )
    history_arguments = {
        "candidate_frames": candidate_frames,
        "context_frames": context_frames,
        "nifty_frame": nifty_features,
        "sector_by_symbol": sector_by_symbol,
        "minimum_average_traded_value": config.minimum_average_traded_value,
        "maximum_candle_range_atr": config.maximum_candle_range_atr,
        "maximum_spread_pct": config.live_maximum_spread_pct,
        "deterministic_seed": selection_seed,
    }
    frozen_eligibility_audit: dict[str, Any] = {}
    rolling_eligibility_audit: dict[str, Any] = {}
    frozen_history = build_watchlist_history(
        **history_arguments, config=frozen_config, selection_method="SCORE",
        eligibility_audit=frozen_eligibility_audit,
    )
    rolling_history = build_watchlist_history(
        **history_arguments, config=rolling_config, selection_method="SCORE",
        eligibility_audit=rolling_eligibility_audit,
    )
    top_two_history = build_watchlist_history(
        **history_arguments, config=top_two_config, selection_method="SCORE"
    )
    full_history = build_watchlist_history(
        **history_arguments, config=frozen_config, selection_method="FULL"
    )
    liquidity_history = build_watchlist_history(
        **history_arguments, config=frozen_config, selection_method="LIQUIDITY"
    )
    random_history = build_watchlist_history(
        **history_arguments, config=frozen_config, selection_method="RANDOM"
    )

    def candidates_for(
        history: Sequence[Mapping[str, Any]],
        mode: Literal["FROZEN_OPEN", "ROLLING"],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return select_candidates_for_history(
            opening_candidates,
            midday_candidates,
            history,
            mode=mode,
            opening_time=config.selection_time,
        )

    comparison_sources: dict[str, tuple[Sequence[Mapping[str, Any]], Literal["FROZEN_OPEN", "ROLLING"]]] = {
        "FROZEN_OPEN_TOP_FIVE": (frozen_history, "FROZEN_OPEN"),
        "ROLLING_TOP_FIVE": (rolling_history, "ROLLING"),
        "FROZEN_OPEN_TOP_TWO": (top_two_history, "FROZEN_OPEN"),
        "FULL_ELIGIBLE_UNIVERSE": (full_history, "FROZEN_OPEN"),
        "LIQUIDITY_ONLY_TOP_FIVE": (liquidity_history, "FROZEN_OPEN"),
        "CAUSALLY_MATCHED_RANDOM_FIVE": (random_history, "FROZEN_OPEN"),
    }
    comparison: dict[str, Any] = {}
    variant_payloads: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]] = {}
    for label, (history, mode) in comparison_sources.items():
        variant_candidates, excluded = candidates_for(history, mode)
        variant_trades, rejected = execute_daily_watchlist_portfolio(variant_candidates, config)
        variant_payloads[label] = (variant_candidates, variant_trades, rejected + excluded)
        comparison[label] = compare_watchlist_variant(
            history=history,
            candidates=variant_candidates,
            trades=variant_trades,
            analysis_start=analysis_start,
            analysis_end=now,
            duration_years=request.durationYears,
        )
    active_label = (
        "ROLLING_TOP_FIVE" if config.mode == "ROLLING" else "FROZEN_OPEN_TOP_FIVE"
    )
    active_history = rolling_history if config.mode == "ROLLING" else frozen_history
    active_candidates, trades, rejected = variant_payloads[active_label]
    accepted_signals = [item for item in active_candidates if not item.get("primaryReason")]
    decision = daily_watchlist_validation_decision(comparison)
    if progress_callback is not None:
        progress_callback({
            "currentStage": "TOP_5_OPENING_RANGE_COMPARISON_COMPLETE",
            "symbolsCompleted": len(request.symbols),
            "symbolsTotal": len(request.symbols),
            "candlesProcessed": sum(int(item["metrics"].get("candles", 0)) for item in prepared),
            "candidatesFound": len(opening_candidates) + len(midday_candidates),
            "acceptedSignals": sum(not item.get("primaryReason") for item in active_candidates),
            "workersActive": 1,
        })
    if any(int(trade.get("executedQuantity") or 0) != 50 for trade in trades):
        raise AssertionError("Daily Scalping Watchlist fixed-quantity invariant failed")
    if any(
        int(trade.get("entryBarIndex") or -1) != int(trade.get("signalBarIndex") or -2) + 1
        for trade in trades
    ):
        raise AssertionError("NEXT_BAR_OPEN must use entryBarIndex = signalBarIndex + 1")
    rejection_counts: dict[str, int] = {}
    for item in rejected:
        reason = str(item.get("primaryReason") or item.get("reason") or "UNEXPLAINED_REJECTION")
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
    daily_selection_rows = [
        {
            "sessionDate": snapshot["sessionDate"],
            "selectionTimestamp": snapshot["rescanTimestamp"],
            "symbols": [
                {
                    "symbol": entry["symbol"],
                    "rank": entry["rankAfter"],
                    "tier": entry["tier"],
                    "score": entry["score"],
                }
                for entry in snapshot.get("entries", [])
            ],
        }
        for snapshot in active_history
        if int(snapshot.get("rescanNumber", 0)) == 1
    ]
    rolling_replacement_rows = [
        snapshot
        for snapshot in rolling_history
        if int(snapshot.get("replacements", 0)) > 0
    ]
    midday_replacement_rows = rolling_replacement_rows if config.mode == "ROLLING" else []
    primary_selections = sum(
        item.get("tier") == "PRIMARY"
        for row in daily_selection_rows
        for item in row["symbols"]
    )
    reserve_selections = sum(
        item.get("tier") == "RESERVE"
        for row in daily_selection_rows
        for item in row["symbols"]
    )
    frozen_watchlist_summary = summarize_watchlist_history(
        frozen_history, variant_payloads["FROZEN_OPEN_TOP_FIVE"][0]
    )
    rolling_watchlist_summary = summarize_watchlist_history(
        rolling_history, variant_payloads["ROLLING_TOP_FIVE"][0]
    )

    def eligibility_report(audit: Mapping[str, Any]) -> dict[str, Any]:
        processed_symbols = {str(item["symbol"]) for item in prepared}
        eligible_symbols = {str(value) for value in audit.get("eligibleSymbols", set())}
        scored_symbols = {str(value) for value in audit.get("scoredSymbols", set())}
        reasons_by_symbol = {
            str(symbol): {str(reason) for reason in reasons}
            for symbol, reasons in audit.get("reasonsBySymbol", {}).items()
        }
        failed_symbols = {str(item.get("symbol")) for item in errors}
        rejected_symbols = set(request.symbols) - eligible_symbols
        reason_priority = (
            "CANDLE_DATA_UNAVAILABLE", "INVALID_OHLCV", "PRICE_UNAVAILABLE",
            "PRICE_BELOW_MINIMUM", "PRICE_ABOVE_MAXIMUM",
            "MEDIAN_DAILY_TRADED_VALUE_UNAVAILABLE", "MEDIAN_DAILY_TRADED_VALUE_BELOW_MINIMUM",
            "OPENING_TRADED_VALUE_UNAVAILABLE", "OPENING_TRADED_VALUE_BELOW_MINIMUM",
            "DAILY_ATR_UNAVAILABLE", "DAILY_ATR_BELOW_MINIMUM", "DAILY_ATR_ABOVE_MAXIMUM",
            "OPENING_GAP_UNAVAILABLE", "OPENING_GAP_ABOVE_MAXIMUM",
            "AVERAGE_TRADED_VALUE_UNAVAILABLE", "AVERAGE_TRADED_VALUE_BELOW_MINIMUM",
            "ROLLING_RVOL_UNAVAILABLE", "ROLLING_TRADED_VALUE_UNAVAILABLE",
            "CANDLE_RANGE_QUALITY_UNAVAILABLE", "CANDLE_RANGE_QUALITY_FAILED",
            "VOLUME_NOT_POSITIVE", "ATR_UNAVAILABLE", "EXCESSIVE_SPREAD",
        )
        rows: list[dict[str, Any]] = []
        symbol_reason_counts: dict[str, int] = {}
        for symbol in sorted(rejected_symbols):
            reasons = set(reasons_by_symbol.get(symbol, set()))
            if symbol in failed_symbols or symbol not in processed_symbols:
                reasons.add("CANDLE_DATA_UNAVAILABLE")
            ordered_reasons = [reason for reason in reason_priority if reason in reasons]
            ordered_reasons.extend(sorted(reasons - set(ordered_reasons)))
            if not ordered_reasons:
                ordered_reasons = ["NO_ELIGIBLE_SELECTION_OBSERVATION"]
            for reason in ordered_reasons:
                symbol_reason_counts[reason] = symbol_reason_counts.get(reason, 0) + 1
            rows.append({
                "symbol": symbol,
                "primaryReason": ordered_reasons[0],
                "reasons": ordered_reasons,
            })
        return {
            "symbolsRequested": len(request.symbols),
            "symbolsWithCandleData": len(processed_symbols),
            "symbolsEligibleAtLeastOnce": len(eligible_symbols),
            "symbolsRejectedForEntirePeriod": len(rejected_symbols),
            "symbolsActuallyScored": len(scored_symbols),
            "eligibilityEvaluations": int(audit.get("evaluations", 0)),
            "rejectionReasonEvaluationCounts": dict(sorted(
                (
                    (str(reason), int(count))
                    for reason, count in audit.get("evaluationReasonCounts", {}).items()
                ),
                key=lambda pair: (-pair[1], pair[0]),
            )),
            "rejectionReasonSymbolCounts": dict(sorted(
                symbol_reason_counts.items(), key=lambda pair: (-pair[1], pair[0])
            )),
            "rejectedSymbols": rows,
        }

    frozen_eligibility = eligibility_report(frozen_eligibility_audit)
    rolling_eligibility = eligibility_report(rolling_eligibility_audit)
    active_eligibility = rolling_eligibility if config.mode == "ROLLING" else frozen_eligibility
    summary = {
        **comparison[active_label]["overall"],
        **summarize_watchlist_history(active_history, active_candidates),
        "rawOpeningCandidates": len(opening_candidates),
        "rawMiddayCandidates": len(midday_candidates),
        "acceptedBuySignals": len(accepted_signals),
        "executedTrades": len(trades),
        "executedQuantity": 50,
        "universeEvaluated": len(prepared),
        "tradingDays": len(daily_selection_rows),
        "dailyWatchlists": len(daily_selection_rows),
        "primarySelections": int(primary_selections),
        "reserveSelections": int(reserve_selections),
        "watchlistReplacements": int(summarize_watchlist_history(active_history, active_candidates)["replacements"]),
        "frozenWatchlists": sum(int(row.get("rescanNumber", 0)) == 1 for row in frozen_history),
        "frozenReplacements": 0,
        "rollingWatchlists": sum(int(row.get("rescanNumber", 0)) == 1 for row in rolling_history),
        "rollingRescans": len(rolling_history),
        "rollingPromotions": int(rolling_watchlist_summary["newlyPromotedSymbols"]),
        "rollingRemovals": sum(len(row.get("removed", [])) for row in rolling_history),
        "openingBreakoutCandidates": len(opening_candidates),
        "rejectionCounts": dict(sorted(rejection_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
        "funnel": {
            "rawOpeningObservations": len(opening_candidates),
            "rawMiddayObservations": len(midday_candidates),
            "watchlistMatchedCandidates": len(active_candidates),
            "entryReadySignals": len(accepted_signals),
            "portfolioAccepted": len(trades),
            "executedTrades": len(trades),
        },
    }
    completed_at = datetime.now(IST)
    response = {
        "metadata": {
            "runId": run_id,
            "strategyMode": DAILY_WATCHLIST_STRATEGY_KEY,
            "strategyKey": DAILY_WATCHLIST_STRATEGY_KEY,
            "strategyName": DAILY_WATCHLIST_STRATEGY_NAME,
            "strategyDescription": DAILY_WATCHLIST_DESCRIPTION,
            "strategyVersion": DAILY_WATCHLIST_STRATEGY_VERSION,
            "watchlistMode": config.mode,
            "featureCodeVersion": DAILY_WATCHLIST_FEATURE_VERSION,
            "sessionRuleVersion": DAILY_WATCHLIST_SESSION_VERSION,
            "portfolioRuleVersion": DAILY_WATCHLIST_PORTFOLIO_VERSION,
            "watchlistRuleVersion": WATCHLIST_RULE_VERSION,
            "openingRangeRuleVersion": OPENING_RANGE_RULE_VERSION,
            "minimumUntouchedValidationTrades": MINIMUM_UNTOUCHED_VALIDATION_TRADES,
            "openingRangeAssumption": "09:15-09:30 completed-candle range; first completed close above its high; next-bar-open entry",
            "startedAt": started_at.isoformat(),
            "completedAt": completed_at.isoformat(),
            "generatedAt": completed_at.isoformat(),
            "analysisStart": analysis_start.isoformat(),
            "analysisEnd": now.isoformat(),
            "durationYears": request.durationYears,
            "timeframe": request.timeframe,
            "universeMode": request.universeMode,
            "symbolsRequested": len(request.symbols),
            "symbolsProcessed": len(prepared),
            "symbolsEligible": active_eligibility["symbolsEligibleAtLeastOnce"],
            "symbolsRejected": active_eligibility["symbolsRejectedForEntirePeriod"],
            "symbolsActuallyScored": active_eligibility["symbolsActuallyScored"],
            "universeEvaluated": len(prepared),
            "tradingDays": len(daily_selection_rows),
            "symbolsFailed": len(errors),
            "workerCount": workers,
            "runtimeSeconds": round(time.perf_counter() - started_clock, 4),
            "configuration": requested.public(),
            "submittedConfiguration": requested.public(),
            "effectiveConfiguration": requested.public(),
            "submittedMaximumHoldingBars": requested.maximumHoldingBars,
            "effectiveMaximumHoldingBars": config.maximum_holding_bars,
            "configurationHash": configuration_hash,
            "fingerprint": fingerprint,
            "dataSnapshot": fingerprint,
            "resultSource": "FRESH_CALCULATION",
            "cachedResult": False,
            "researchLabel": "Rejected/research-only until untouched validation passes",
            "liveOrdersEnabled": False,
            "historicalSpread": "UNAVAILABLE_ADVISORY",
            "supportingData": {
                "niftyAvailable": nifty_features is not None,
                "sectorMappingConfigured": sector_path is not None,
                "sectorSupportSymbols": len(support_symbols),
                "supportSymbolsUnavailable": len(support_errors),
            },
            "universeEligibility": active_eligibility,
        },
        "summary": summary,
        "watchlist": {
            "mode": config.mode,
            "summary": summarize_watchlist_history(active_history, active_candidates),
            "history": active_history,
        },
        "allWatchlistHistory": [
            *({**row, "benchmarkVariant": "FROZEN_OPEN_TOP_FIVE"} for row in frozen_history),
            *({**row, "benchmarkVariant": "ROLLING_TOP_FIVE"} for row in rolling_history),
        ],
        "dailySelections": daily_selection_rows,
        "middayReplacements": midday_replacement_rows,
        "watchlistModeSummaries": {
            "FROZEN_OPEN": {
                "watchlists": summary["frozenWatchlists"],
                "replacements": 0,
                **frozen_watchlist_summary,
                "eligibility": frozen_eligibility,
            },
            "ROLLING": {
                "watchlists": summary["rollingWatchlists"],
                "rescans": summary["rollingRescans"],
                "promotions": summary["rollingPromotions"],
                "removals": summary["rollingRemovals"],
                **rolling_watchlist_summary,
                "eligibility": rolling_eligibility,
            },
        },
        "candidates": sorted(
            [*opening_candidates, *midday_candidates],
            key=lambda row: (str(row.get("signalTimestamp")), str(row.get("symbol")), str(row.get("candidateId"))),
        ),
        "signals": accepted_signals,
        "openingSignals": [
            item for item in accepted_signals if item.get("signalType") == "OPENING_RANGE_BREAKOUT"
        ],
        "middaySignals": [
            item for item in accepted_signals if item.get("signalType") == "ROLLING_MOMENTUM_BREAKOUT"
        ],
        "trades": trades,
        "rejectedCandidates": rejected,
        "comparison": comparison,
        "validationDecision": decision,
        "results": [
            {"variant": label, **payload["overall"]}
            for label, payload in comparison.items()
        ],
        "errors": errors,
        "supportingDataErrors": support_errors,
        "warnings": [
            "Research and paper-signal only. This strategy has no broker-order integration.",
            "Opening-range rules are an explicit configurable research assumption because no prior ORB implementation existed in the repository.",
            "All ranking, signals, and entries use only completed candles available at the decision timestamp.",
            "Historical bid/ask spread is unavailable and is not fabricated.",
            "The current symbol universe introduces survivorship bias. Past performance does not guarantee future returns.",
        ],
    }
    response["metadata"]["resultCacheBytes"] = result_cache.save(fingerprint, response)
    return response


def run_backtest(request: BacktestRequest, store: HistoricalDataStore, now_ist: datetime | None = None) -> dict[str, Any]:
    if request.strategyMode == "rsi_recovery":
        return run_recovery_backtest(request, store, now_ist)
    if request.strategyMode == DAILY_WATCHLIST_STRATEGY_KEY:
        return run_top_5_opening_range_breakout_backtest(request, store, now_ist)

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
_backtest_job_service = BacktestJobService()
_store: HistoricalDataStore | None = None
_feature_snapshot_lock = threading.Lock()
_feature_snapshot_cache: tuple[int, pd.DataFrame] | None = None
_universe_service: UniverseService | None = None
_live_signal_engine: LiveSignalEngine | None = None
_market_data_refresh_service: MarketDataRefreshService | None = None
_oi_repository: OiRegimeRepository | None = None
_backtest_history_repository: BacktestHistoryRepository | None = None
_application_settings_repository: ApplicationSettingsRepository | None = None
_stock_scanner_service: StockScannerService | None = None


def get_store() -> HistoricalDataStore:
    global _store
    if _store is None:
        _store = create_store()
    return _store


def get_stock_scanner_service() -> StockScannerService:
    global _stock_scanner_service
    if _stock_scanner_service is None:
        _stock_scanner_service = StockScannerService(get_store())
    return _stock_scanner_service


def get_report_directory() -> Path:
    directory = Path(
        os.environ.get("BACKTEST_REPORT_DIR", "/var/lib/vento-nse/backtest/reports")
    ).expanduser()
    if not directory.is_absolute():
        raise RuntimeError("BACKTEST_REPORT_DIR must be an absolute path")
    return directory


def get_oi_repository() -> OiRegimeRepository:
    global _oi_repository
    if _oi_repository is None:
        default_root = Path(
            os.environ.get("BACKTEST_CACHE_DIR", "/var/lib/vento-nse/backtest")
        ).expanduser() / "nifty-oi"
        root = Path(os.environ.get("NIFTY_OI_DIR", str(default_root))).expanduser()
        if not root.is_absolute():
            raise RuntimeError("NIFTY_OI_DIR must be an absolute path")
        _oi_repository = OiRegimeRepository(root)
    return _oi_repository


def get_backtest_history_repository() -> BacktestHistoryRepository:
    global _backtest_history_repository
    if _backtest_history_repository is None:
        default_root = Path(
            os.environ.get("BACKTEST_CACHE_DIR", "/var/lib/vento-nse/backtest")
        ).expanduser() / "backtest-history"
        root = Path(os.environ.get("BACKTEST_HISTORY_DIR", str(default_root))).expanduser()
        if not root.is_absolute():
            raise RuntimeError("BACKTEST_HISTORY_DIR must be an absolute path")
        _backtest_history_repository = BacktestHistoryRepository(root, limit=HISTORY_LIMIT)
    return _backtest_history_repository


def get_application_settings_repository() -> ApplicationSettingsRepository:
    global _application_settings_repository
    if _application_settings_repository is None:
        default_root = Path(
            os.environ.get("BACKTEST_CACHE_DIR", "/var/lib/vento-nse/backtest")
        ).expanduser() / "application-settings"
        root = Path(os.environ.get("APPLICATION_SETTINGS_DIR", str(default_root))).expanduser()
        if not root.is_absolute():
            raise RuntimeError("APPLICATION_SETTINGS_DIR must be an absolute path")
        _application_settings_repository = ApplicationSettingsRepository(root)
    return _application_settings_repository


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
            oi_service=build_oi_service_from_environment(
                get_store().config,
                get_oi_repository().root,
            ),
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


@app.get("/backtest-history")
def list_backtest_history(
    owner_key: str = Header(alias="x-opendelta-history-owner"),
) -> dict[str, Any]:
    try:
        return {"runs": get_backtest_history_repository().list(owner_key), "limit": HISTORY_LIMIT}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (OSError, RuntimeError, sqlite3.Error) as error:
        raise HTTPException(status_code=503, detail="Backtest history is temporarily unavailable") from error


@app.get("/backtest-history/{run_id}")
def get_backtest_history(
    run_id: str,
    owner_key: str = Header(alias="x-opendelta-history-owner"),
) -> dict[str, Any]:
    try:
        return get_backtest_history_repository().get(owner_key, run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Backtest result was not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (OSError, RuntimeError, sqlite3.Error) as error:
        raise HTTPException(status_code=503, detail="Backtest history is temporarily unavailable") from error


@app.post("/backtest-history")
def save_backtest_history(
    request: BacktestHistorySaveRequest,
    owner_key: str = Header(alias="x-opendelta-history-owner"),
) -> dict[str, Any]:
    try:
        run = get_backtest_history_repository().save(owner_key, request.persisted())
        return {"run": run, "limit": HISTORY_LIMIT}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (OSError, RuntimeError, sqlite3.Error) as error:
        raise HTTPException(status_code=503, detail="Backtest history is temporarily unavailable") from error


@app.delete("/backtest-history/{run_id}")
def delete_backtest_history(
    run_id: str,
    owner_key: str = Header(alias="x-opendelta-history-owner"),
) -> dict[str, Any]:
    try:
        if not get_backtest_history_repository().delete(owner_key, run_id):
            raise HTTPException(status_code=404, detail="Backtest result was not found")
        return {"deleted": True, "id": run_id}
    except HTTPException:
        raise
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (OSError, RuntimeError, sqlite3.Error) as error:
        raise HTTPException(status_code=503, detail="Backtest history is temporarily unavailable") from error


@app.get("/nifty-oi/history/status")
def nifty_oi_history_status() -> dict[str, Any]:
    """Expose import coverage without returning credentials or raw contract payloads."""
    try:
        return get_oi_repository().history_status()
    except (OSError, RuntimeError, ValueError) as error:
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


@app.post("/market-data/symbols")
async def add_market_symbol(request: MarketSymbolRequest) -> dict[str, Any]:
    """Add a Dhan-validated NSE equity and refresh the shared market snapshot."""
    service = get_market_data_refresh_service()
    if service.status()["running"]:
        raise HTTPException(
            status_code=409,
            detail="Wait for the current market-data refresh to finish before adding a symbol",
        )

    try:
        config = DhanConfig.from_environment()
        instruments = await asyncio.to_thread(
            download_instrument_master,
            config.instrument_master_url,
        )
        registry = MarketSymbolRegistry(config.symbols_file, DEFAULT_SYMBOLS_FILE)
        addition = await asyncio.to_thread(registry.add, request.symbol, instruments)
        refresh = service.start()
        return {
            "symbol": addition.symbol,
            "companyName": addition.company_name,
            "symbolCount": addition.symbol_count,
            "refresh": refresh,
        }
    except SymbolAlreadyExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SymbolNotFoundError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (ConfigurationError, DhanAPIError, OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/application-settings")
def application_settings() -> dict[str, object]:
    try:
        return get_application_settings_repository().get().public()
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
        raise HTTPException(status_code=503, detail="Application settings are temporarily unavailable") from error


@app.put("/application-settings")
def update_application_settings(request: GlobalPriceSettingsRequest) -> dict[str, object]:
    try:
        return get_application_settings_repository().update(
            request.minimumPrice,
            request.maximumPrice,
        ).public()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (OSError, RuntimeError, sqlite3.Error) as error:
        raise HTTPException(status_code=503, detail="Application settings are temporarily unavailable") from error


@app.get("/market-data/symbols")
def list_market_symbols() -> dict[str, Any]:
    """Return the same managed symbol registry used by refreshes and backtests."""
    try:
        symbols_file = Path(os.environ.get("SYMBOLS_FILE", DEFAULT_SYMBOLS_FILE)).expanduser()
        symbols = load_symbols(symbols_file)
        settings = get_application_settings_repository().get()
        filter_applied = not (
            settings.minimum_price == 0
            and settings.maximum_price == GLOBAL_DEFAULT_MAXIMUM_PRICE
        )
        missing_price_count = 0
        filtered = list(symbols)
        if filter_applied:
            market_data = Path(
                os.environ.get(
                    "LIVE_MARKET_DATA_FILE",
                    "/var/lib/vento-nse/data/nse_symbols_rsi_volume.csv",
                )
            ).expanduser()
            filtered, missing_price_count = filter_symbols_by_price(
                symbols,
                prices_by_symbol(market_data),
                settings,
            )
        return {
            "symbols": filtered,
            "symbolCount": len(filtered),
            "totalSymbolCount": len(symbols),
            "priceRange": settings.public()["priceRange"],
            "priceFilterApplied": filter_applied,
            "missingPriceCount": missing_price_count,
        }
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _stock_scanner_company_names(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    frame = pd.read_csv(path, dtype=str).fillna("")
    normalized = {str(column).strip().casefold(): column for column in frame.columns}
    symbol_column = normalized.get("symbol")
    name_column = normalized.get("company_name") or normalized.get("company name")
    if symbol_column is None or name_column is None:
        return {}
    return {
        str(symbol).strip().upper().removesuffix(".NS"): str(name).strip()
        for symbol, name in zip(frame[symbol_column], frame[name_column], strict=False)
        if str(symbol).strip() and str(name).strip()
    }


@app.get("/stock-scanner")
def stock_scanner(refresh: bool = Query(default=False)) -> dict[str, Any]:
    """Return the causal, paper-only 15-minute Top-5 scanner snapshot."""
    try:
        market_symbols = list_market_symbols()
        settings = get_application_settings_repository().get()
        market_data_path = Path(
            os.environ.get(
                "LIVE_MARKET_DATA_FILE",
                "/var/lib/vento-nse/data/nse_symbols_rsi_volume.csv",
            )
        ).expanduser()
        sector_value = (
            os.environ.get("MARKET_CONTEXT_SECTOR_MAP_FILE")
            or os.environ.get("MARKET_ALIGNED_SECTOR_MAP_FILE")
        )
        sector_path = Path(sector_value).expanduser() if sector_value else None
        return get_stock_scanner_service().snapshot(
            market_symbols["symbols"],
            minimum_price=settings.minimum_price,
            maximum_price=settings.maximum_price,
            sector_by_symbol=load_vwap_sector_mapping(sector_path),
            company_names=_stock_scanner_company_names(market_data_path),
            force=refresh,
        )
    except HTTPException:
        raise
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
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


def _completed_job_progress(
    result: Mapping[str, Any], symbol_count: int,
) -> dict[str, Any]:
    summary = result.get("summary", {})
    metadata = result.get("metadata", {})
    funnel = summary.get("funnel") or summary.get("candidateFunnel", {})
    return {
        "currentStage": (
            "CACHED_RESULT" if bool(metadata.get("cachedResult")) else "FINALIZING"
        ),
        "symbolsCompleted": int(metadata.get("symbolsProcessed", symbol_count)),
        "symbolsTotal": symbol_count,
        "candlesProcessed": int(summary.get("candleRowsProcessed", 0)),
        "candidatesFound": int(
            summary.get(
                "rawCandidates",
                int(summary.get("rawOpeningCandidates", 0))
                + int(summary.get("rawMiddayCandidates", 0)),
            )
        ),
        "acceptedSignals": int(
            summary.get("acceptedBuySignals", funnel.get("executedTrades", 0))
        ),
        "workersActive": 0,
    }


def _job_history_record(
    result: dict[str, Any],
    request: BacktestRequest,
) -> dict[str, Any]:
    metadata = result.get("metadata", {})
    return BacktestHistorySaveRequest(
        id=str(metadata.get("runId") or request.runId or uuid.uuid4()),
        completedAt=metadata.get("completedAt") or datetime.now(IST),
        strategyMode=str(metadata.get("strategyMode") or request.strategyMode),
        strategyName=str(metadata.get("strategyName") or request.strategyMode),
        timeframe=str(metadata.get("timeframe") or request.timeframe),
        durationYears=int(metadata.get("durationYears") or request.durationYears),
        symbolCount=int(metadata.get("symbolsProcessed") or len(request.symbols)),
        response=result,
    ).persisted()


@app.post("/backtest/jobs")
def start_backtest_job(
    request: BacktestRequest,
    history_owner: str | None = Header(
        default=None,
        alias="x-opendelta-history-owner",
    ),
) -> dict[str, Any]:
    if request.strategyMode != DAILY_WATCHLIST_STRATEGY_KEY:
        raise HTTPException(
            status_code=422,
            detail="Asynchronous progress jobs apply to Top-5 Opening Range Breakout.",
        )
    store = get_store()

    def runner(
        progress: Callable[[dict[str, Any]], None],
        cancel_event: threading.Event,
    ) -> dict[str, Any]:
        result = run_top_5_opening_range_breakout_backtest(
            request,
            store,
            progress_callback=progress,
            cancel_event=cancel_event,
        )
        if history_owner:
            metadata = result.setdefault("metadata", {})
            metadata["historySaved"] = True
            try:
                get_backtest_history_repository().save(
                    history_owner,
                    _job_history_record(result, request),
                )
            except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
                metadata["historySaved"] = False
                metadata["historySaveError"] = str(error)
        progress(_completed_job_progress(result, len(request.symbols)))
        return result

    return _backtest_job_service.start(
        symbols_total=len(request.symbols),
        runner=runner,
    )


@app.get("/backtest/jobs/{job_id}")
def get_backtest_job(job_id: str) -> dict[str, Any]:
    try:
        return _backtest_job_service.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Backtest job was not found") from error


@app.delete("/backtest/jobs/{job_id}")
def cancel_backtest_job(job_id: str) -> dict[str, Any]:
    try:
        return _backtest_job_service.cancel(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Backtest job was not found") from error


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


@app.post("/backtest/compare-rsi-exits")
async def compare_rsi_exits(request: RsiExitComparisonRequest) -> dict[str, Any]:
    async with _run_lock:
        try:
            return await asyncio.to_thread(
                run_rsi_exit_comparison, request, get_store()
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
    _backtest_job_service.shutdown()
