from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, time as datetime_time
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import numpy as np
import pandas as pd

from main import IST
from recovery_backtest import (
    calculate_ema,
    calculate_session_vwap,
    calculate_wilder_rsi,
)
from recovery_dynamic_exit import calculate_wilder_atr


STRATEGY_KEY = "market_aligned_vwap_pullback_scalper"
STRATEGY_NAME = "Market-Aligned VWAP Pullback Scalper"
STRATEGY_VERSION = "market-aligned-vwap-pullback-scalper-1.0.0"
STRATEGY_DESCRIPTION = (
    "Controlled RSI pullbacks toward session VWAP or short EMAs inside an "
    "established intraday uptrend, with NIFTY safety and causal quality ranking."
)
FEATURE_CODE_VERSION = "vwap-pullback-features-3"
SESSION_RULE_VERSION = "nse-intraday-session-state-1"
PORTFOLIO_RULE_VERSION = "chronological-vwap-portfolio-1"


REASON_MESSAGES = {
    "TIME_WINDOW_FAILED": "The trigger candle was outside the configured entry window.",
    "MARKET_CONTEXT_UNAVAILABLE": "Completed NIFTY context was unavailable at the trigger time.",
    "NIFTY_STRONGLY_BEARISH": "NIFTY was below session VWAP with EMA9 below EMA20.",
    "LIQUIDITY_FAILED": "Historical traded-value or candle-range liquidity checks failed.",
    "QUALITY_SCORE_FAILED": "The optional enforced quality score was below its minimum.",
    "NO_NEXT_BAR": "No same-session next candle was available for entry.",
    "GAP_TOO_LARGE": "The next open was more than the allowed ATR gap above the trigger close.",
    "RISK_TOO_WIDE": "The frozen structural/ATR stop exceeded the maximum risk distance.",
    "INVALID_RISK": "A valid positive stop distance could not be calculated.",
    "QUANTITY_ZERO": "Risk and capital limits produced a zero-share position.",
    "INVALID_ENTRY_BAR": "The next entry bar contained invalid OHLCV data.",
    "MAX_TRADES_PER_DAY": "The configured daily trade count was already reached.",
    "MAX_CONCURRENT_TRADES": "The configured concurrent-position limit was already reached.",
    "DAILY_LOSS_COUNT": "The configured number of daily losses was already reached.",
    "DAILY_LOSS_LIMIT": "The configured daily rupee loss limit was already reached.",
    "CAPITAL_LIMIT": "The portfolio did not have enough configured capital for the position.",
    "ACCEPTED": "The candidate was accepted and executed.",
}


@dataclass(frozen=True)
class VwapPullbackConfig:
    execution_model: Literal["NEXT_BAR_OPEN"] = "NEXT_BAR_OPEN"
    entry_start_time: str = "09:30"
    last_entry_time: str = "14:45"
    square_off_time: str = "15:15"
    rsi_length: int = 14
    rsi_pullback_minimum: float = 38.0
    rsi_pullback_maximum: float = 50.0
    rsi_trigger_level: float = 50.0
    maximum_trigger_rsi: float = 65.0
    setup_expiry_bars: int = 6
    ema_fast: int = 9
    ema_slow: int = 20
    ema_slope_lookback_bars: int = 3
    atr_length: int = 14
    rvol_period: int = 20
    minimum_trigger_rvol: float = 1.20
    pullback_approach_atr: float = 0.25
    material_below_ema_atr: float = 0.25
    maximum_entry_gap_atr: float = 0.50
    structural_stop_buffer_atr: float = 0.05
    volatility_stop_atr: float = 0.60
    minimum_stop_pct: float = 0.35
    maximum_stop_pct: float = 1.00
    reward_risk_ratio: float = 1.50
    maximum_holding_bars: int = 6
    minimum_average_traded_value: float = 500_000.0
    maximum_candle_range_atr: float = 3.0
    historical_spread_mode: Literal["ADVISORY"] = "ADVISORY"
    live_maximum_spread_pct: float = 0.15
    market_context_fail_policy: Literal["ADVISORY", "REJECT"] = "ADVISORY"
    market_context_stale_seconds: int = 360
    minimum_breadth_pct: float = 45.0
    minimum_breadth_symbols: int = 10
    minimum_sector_members: int = 2
    minimum_sector_bullish_pct: float = 50.0
    relative_strength_lookback_bars: int = 3
    quality_rvol_threshold: float = 1.50
    enforce_minimum_quality_score: bool = False
    minimum_quality_score: float = 50.0
    oi_mode: Literal["OFF", "ADVISORY"] = "OFF"
    oi_stale_data_seconds: int = 360
    position_sizing: Literal["FIXED_QUANTITY", "RISK_BUDGET"] = "FIXED_QUANTITY"
    quantity_per_trade: int = 50
    rupee_risk_budget: float = 2_500.0
    maximum_quantity: int = 10_000
    configured_capital: float = 1_000_000.0
    maximum_capital_per_position: float = 1_000_000.0
    maximum_trades_per_day: int = 5
    maximum_concurrent_trades: int = 2
    stop_after_daily_losses: int = 2
    maximum_daily_loss_pct: float = 0.50
    buy_cost_bps: float = 5.0
    sell_cost_bps: float = 5.0
    slippage_bps: float = 2.0

    def validate(self) -> "VwapPullbackConfig":
        if self.execution_model != "NEXT_BAR_OPEN":
            raise ValueError("VWAP Pullback execution must be NEXT_BAR_OPEN")
        if not (
            0 <= self.rsi_pullback_minimum < self.rsi_pullback_maximum
            <= self.rsi_trigger_level < self.maximum_trigger_rsi <= 100
        ):
            raise ValueError(
                "RSI levels must satisfy pullback minimum < pullback maximum "
                "<= trigger < maximum trigger RSI"
            )
        if not 0 < self.ema_fast < self.ema_slow:
            raise ValueError("EMA fast length must be positive and below EMA slow length")
        positive_integers = (
            self.rsi_length,
            self.setup_expiry_bars,
            self.ema_slope_lookback_bars,
            self.atr_length,
            self.rvol_period,
            self.maximum_holding_bars,
            self.maximum_quantity,
            self.maximum_trades_per_day,
            self.maximum_concurrent_trades,
            self.stop_after_daily_losses,
            self.quantity_per_trade,
        )
        if any(value <= 0 for value in positive_integers):
            raise ValueError("VWAP Pullback bar lengths, quantities, and limits must be positive")
        if not 0 < self.minimum_stop_pct <= self.maximum_stop_pct < 100:
            raise ValueError("Stop distances must satisfy 0 < minimum <= maximum < 100")
        if any(value < 0 for value in (
            self.buy_cost_bps,
            self.sell_cost_bps,
            self.slippage_bps,
            self.maximum_daily_loss_pct,
        )):
            raise ValueError("Costs, slippage, and daily loss limits cannot be negative")
        if any(value <= 0 for value in (
            self.minimum_trigger_rvol,
            self.pullback_approach_atr,
            self.maximum_entry_gap_atr,
            self.volatility_stop_atr,
            self.reward_risk_ratio,
            self.minimum_average_traded_value,
            self.maximum_candle_range_atr,
            self.configured_capital,
            self.maximum_capital_per_position,
            self.rupee_risk_budget,
        )):
            raise ValueError("VWAP Pullback risk, liquidity, and capital values must be positive")
        try:
            entry_start = datetime_time.fromisoformat(self.entry_start_time)
            last_entry = datetime_time.fromisoformat(self.last_entry_time)
            square_off = datetime_time.fromisoformat(self.square_off_time)
        except ValueError as error:
            raise ValueError("VWAP Pullback session times must use HH:MM") from error
        if not entry_start < last_entry < square_off:
            raise ValueError("Session times must satisfy entry start < last entry < square-off")
        return self

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def _as_ist(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def _as_ist_index(values: Any) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(values)
    return index.tz_localize(IST) if index.tz is None else index.tz_convert(IST)


def _five_minute_bucket(value: Any) -> str:
    return _as_ist(value).floor("5min").isoformat()


def _iso(value: Any) -> str:
    return _as_ist(value).isoformat()


def stable_fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_stat_fingerprint(path: Path | None) -> str:
    if path is None or not path.is_file():
        return "MISSING"
    stat = path.stat()
    return f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"


class VwapPullbackResultCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, fingerprint: str) -> Path:
        return self.root / fingerprint[:2] / f"{fingerprint}.json.gz"

    def load(self, fingerprint: str) -> dict[str, Any] | None:
        path = self.path_for(fingerprint)
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, ValueError, TypeError):
            return None
        if value.get("metadata", {}).get("fingerprint") != fingerprint:
            return None
        metadata = value.setdefault("metadata", {})
        metadata["cachedResult"] = True
        metadata["resultSource"] = "RESULT_CACHE"
        metadata["originalRunTimestamp"] = metadata.get("completedAt")
        return value

    def save(self, fingerprint: str, value: Mapping[str, Any]) -> int:
        path = self.path_for(fingerprint)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=f".{fingerprint}.", suffix=".json.gz", delete=False
            ) as handle:
                temporary = Path(handle.name)
            with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=5) as handle:
                json.dump(value, handle, sort_keys=True, separators=(",", ":"), default=str)
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return path.stat().st_size


def load_sector_mapping(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Sector mapping JSON must be a symbol-to-sector object")
        rows = value.items()
    else:
        frame = pd.read_csv(path, dtype=str).fillna("")
        normalized = {str(column).strip().casefold(): column for column in frame.columns}
        symbol_column = normalized.get("symbol")
        sector_column = normalized.get("sector") or normalized.get("industry")
        if symbol_column is None or sector_column is None:
            raise ValueError("Sector mapping CSV requires symbol and sector/industry columns")
        rows = zip(frame[symbol_column], frame[sector_column], strict=False)
    return {
        str(symbol).strip().upper().removesuffix(".NS"): str(sector).strip()
        for symbol, sector in rows
        if str(symbol).strip() and str(sector).strip()
    }


def calculate_vwap_pullback_features(
    candles: pd.DataFrame, config: VwapPullbackConfig
) -> pd.DataFrame:
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in candles.columns]
    if missing:
        raise ValueError("missing columns: " + ", ".join(missing))
    if not isinstance(candles.index, pd.DatetimeIndex):
        raise ValueError("timestamps are not a DatetimeIndex")
    if not candles.index.is_monotonic_increasing:
        raise ValueError("timestamps are not ascending")
    if candles.index.has_duplicates:
        raise ValueError("duplicate candle timestamps")
    if candles.index.tz is None:
        raise ValueError("timestamps are not timezone-aware")
    data = candles.copy().sort_index()
    data.index = _as_ist_index(data.index)
    ohlcv = data[["Open", "High", "Low", "Close", "Volume"]].apply(
        pd.to_numeric, errors="coerce"
    ).astype(float)
    finite = pd.Series(
        np.isfinite(ohlcv.to_numpy(dtype=float)).all(axis=1), index=data.index
    )
    positive = ohlcv[["Open", "High", "Low", "Close", "Volume"]].gt(0).all(axis=1)
    ordered = (
        ohlcv["High"].ge(ohlcv[["Open", "Close", "Low"]].max(axis=1))
        & ohlcv["Low"].le(ohlcv[["Open", "Close", "High"]].min(axis=1))
    )
    valid_ohlcv = (finite & positive & ordered).fillna(False)
    data[ohlcv.columns] = ohlcv
    data.loc[~valid_ohlcv, ohlcv.columns] = np.nan
    data["ValidOHLCV"] = valid_ohlcv.astype(bool)
    data["RSI"] = calculate_wilder_rsi(data["Close"], config.rsi_length)
    data["EMAFast"] = calculate_ema(data["Close"], config.ema_fast)
    data["EMASlow"] = calculate_ema(data["Close"], config.ema_slow)
    data["ATR"] = calculate_wilder_atr(data, config.atr_length)
    data["SessionVWAP"] = calculate_session_vwap(data)
    previous_average_volume = data["Volume"].shift(1).rolling(
        config.rvol_period, min_periods=config.rvol_period
    ).mean()
    data["RVOL"] = data["Volume"] / previous_average_volume.replace(0, np.nan)
    traded_value = data["Close"] * data["Volume"]
    data["AverageTradedValue"] = traded_value.rolling(
        config.rvol_period, min_periods=config.rvol_period
    ).mean()
    data["ReturnPct"] = (
        data["Close"] / data["Close"].shift(config.relative_strength_lookback_bars) - 1.0
    ) * 100.0
    data["HighQualityTrigger"] = (
        data["Close"].gt(data["Open"])
        & (data["Close"] - data["Open"]).ge((data["High"] - data["Low"]) * 0.5)
        & (data["High"] - data["Low"]).le(data["ATR"] * 1.5)
    ).fillna(False)
    return data


def _liquidity_state(row: pd.Series, config: VwapPullbackConfig) -> tuple[bool, str | None]:
    values = [row.get(name) for name in ("Open", "High", "Low", "Close", "Volume")]
    if any(_finite(value) is None for value in values):
        return False, "INVALID_OHLCV"
    if min(float(row[name]) for name in ("Open", "High", "Low", "Close")) <= 0:
        return False, "INVALID_OHLCV"
    if float(row["Volume"]) <= 0:
        return False, "ZERO_VOLUME"
    average_value = _finite(row.get("AverageTradedValue"))
    atr = _finite(row.get("ATR"))
    if average_value is None or average_value < config.minimum_average_traded_value:
        return False, "MINIMUM_TRADED_VALUE"
    if atr is None or atr <= 0 or float(row["High"] - row["Low"]) > config.maximum_candle_range_atr * atr:
        return False, "CANDLE_RANGE_QUALITY"
    return True, None


def _plan_exit(
    data: pd.DataFrame,
    candidate: Mapping[str, Any],
    config: VwapPullbackConfig,
) -> dict[str, Any]:
    signal_index = int(candidate["signalBarIndex"])
    entry_index = signal_index + 1
    if entry_index >= len(data) or data.index[entry_index].date() != data.index[signal_index].date():
        return {"attemptStatus": "NO_NEXT_BAR", "primaryReason": "NO_NEXT_BAR"}
    if not bool(data.iloc[entry_index].get("ValidOHLCV", True)):
        return {
            "attemptStatus": "INVALID_ENTRY_BAR",
            "primaryReason": "INVALID_ENTRY_BAR",
        }
    raw_entry = float(data.iloc[entry_index]["Open"])
    trigger_close = float(candidate["triggerClose"])
    atr = float(candidate["atrAtTrigger"])
    if raw_entry - trigger_close > config.maximum_entry_gap_atr * atr:
        return {
            "attemptStatus": "GAP_TOO_LARGE",
            "primaryReason": "GAP_TOO_LARGE",
            "rawEntryPrice": _finite(raw_entry, 4),
            "gapAtr": _finite((raw_entry - trigger_close) / atr, 6),
        }
    slippage_rate = config.slippage_bps / 10_000.0
    entry_price = raw_entry * (1.0 + slippage_rate)
    structural_stop = float(candidate["pullbackSwingLow"]) - config.structural_stop_buffer_atr * atr
    volatility_stop = entry_price - config.volatility_stop_atr * atr
    stop_price = min(structural_stop, volatility_stop)
    risk_per_share = entry_price - stop_price
    if not math.isfinite(risk_per_share) or risk_per_share <= 0 or stop_price <= 0:
        return {"attemptStatus": "INVALID_RISK", "primaryReason": "INVALID_RISK"}
    risk_pct = risk_per_share / entry_price * 100.0
    if risk_pct < config.minimum_stop_pct:
        stop_price = entry_price * (1.0 - config.minimum_stop_pct / 100.0)
        risk_per_share = entry_price - stop_price
        risk_pct = config.minimum_stop_pct
    if risk_pct > config.maximum_stop_pct + 1e-12:
        return {
            "attemptStatus": "RISK_TOO_WIDE",
            "primaryReason": "RISK_TOO_WIDE",
            "rawEntryPrice": _finite(raw_entry, 4),
            "entryPrice": _finite(entry_price, 4),
            "structuralStop": _finite(structural_stop, 4),
            "volatilityStop": _finite(volatility_stop, 4),
            "riskPct": _finite(risk_pct, 6),
        }
    target_price = entry_price + config.reward_risk_ratio * risk_per_share
    if config.position_sizing == "RISK_BUDGET":
        quantity = math.floor(config.rupee_risk_budget / risk_per_share)
    else:
        quantity = config.quantity_per_trade
    capital_limit_quantity = math.floor(config.maximum_capital_per_position / entry_price)
    quantity = min(quantity, config.maximum_quantity, capital_limit_quantity)
    if quantity < 1:
        return {"attemptStatus": "QUANTITY_ZERO", "primaryReason": "QUANTITY_ZERO"}

    square_off = datetime_time.fromisoformat(config.square_off_time)
    exit_index: int | None = None
    raw_exit: float | None = None
    exit_reason: str | None = None
    for index in range(entry_index + 1, len(data)):
        if data.index[index].date() != data.index[entry_index].date():
            break
        row = data.iloc[index]
        if not bool(row.get("ValidOHLCV", True)):
            continue
        open_price = float(row["Open"])
        if open_price <= stop_price:
            exit_index, raw_exit, exit_reason = index, open_price, "STOP_GAP"
            break
        if open_price >= target_price:
            exit_index, raw_exit, exit_reason = index, open_price, "TARGET_GAP"
            break
        if data.index[index].time().replace(tzinfo=None) >= square_off:
            exit_index, raw_exit, exit_reason = index, open_price, "SESSION_EXIT"
            break
        if index > entry_index + config.maximum_holding_bars:
            exit_index, raw_exit, exit_reason = index, open_price, "TIME_EXIT"
            break
        if float(row["Low"]) <= stop_price:
            exit_index, raw_exit, exit_reason = index, stop_price, "STOP_EXIT"
            break
        if float(row["High"]) >= target_price:
            exit_index, raw_exit, exit_reason = index, target_price, "TARGET_EXIT"
            break
    if exit_index is None:
        same_session = np.flatnonzero(
            np.asarray(data.index.date) == data.index[entry_index].date()
        )
        if len(same_session) == 0:
            return {"attemptStatus": "NO_NEXT_BAR", "primaryReason": "NO_NEXT_BAR"}
        exit_index = int(same_session[-1])
        raw_exit = float(data.iloc[exit_index]["Close"])
        exit_reason = "SESSION_EXIT"

    assert raw_exit is not None and exit_reason is not None
    exit_price = raw_exit * (1.0 - slippage_rate)
    gross_pnl = (raw_exit - raw_entry) * quantity
    buy_cost = entry_price * quantity * config.buy_cost_bps / 10_000.0
    sell_cost = exit_price * quantity * config.sell_cost_bps / 10_000.0
    slippage_cost = ((entry_price - raw_entry) + (raw_exit - exit_price)) * quantity
    net_pnl = gross_pnl - buy_cost - sell_cost - slippage_cost
    initial_risk = risk_per_share * quantity
    return {
        "attemptStatus": "READY",
        "primaryReason": None,
        "entryTimestamp": _iso(data.index[entry_index]),
        "entryBarIndex": entry_index,
        "rawEntryPrice": _finite(raw_entry, 4),
        "entryPrice": _finite(entry_price, 4),
        "structuralStop": _finite(structural_stop, 4),
        "volatilityStop": _finite(volatility_stop, 4),
        "stopPrice": _finite(stop_price, 4),
        "riskPerShare": _finite(risk_per_share, 6),
        "riskPct": _finite(risk_pct, 6),
        "targetPrice": _finite(target_price, 4),
        "quantity": quantity,
        "capitalDeployed": _finite(entry_price * quantity, 2),
        "initialRupeeRisk": _finite(initial_risk, 2),
        "exitTimestamp": _iso(data.index[exit_index]),
        "exitBarIndex": exit_index,
        "rawExitPrice": _finite(raw_exit, 4),
        "exitPrice": _finite(exit_price, 4),
        "exitReason": exit_reason,
        "barsHeld": max(exit_index - entry_index, 0),
        "grossPnl": _finite(gross_pnl, 2),
        "buyCost": _finite(buy_cost, 2),
        "sellCost": _finite(sell_cost, 2),
        "slippageCost": _finite(slippage_cost, 2),
        "totalCosts": _finite(buy_cost + sell_cost + slippage_cost, 2),
        "netPnl": _finite(net_pnl, 2),
        "rMultiple": _finite(net_pnl / initial_risk, 6) if initial_risk > 0 else None,
    }


def detect_pullback_candidates(
    symbol: str,
    features: pd.DataFrame,
    config: VwapPullbackConfig,
    *,
    analysis_start: datetime | None = None,
) -> dict[str, Any]:
    config.validate()
    data = features.copy().sort_index()
    data.index = _as_ist_index(data.index)
    start = int(data.index.searchsorted(_as_ist(analysis_start), side="left")) if analysis_start else 0
    sessions = np.asarray(data.index.date)
    numeric = {
        name: pd.to_numeric(data[name], errors="coerce").to_numpy(dtype=float)
        for name in (
            "Open", "High", "Low", "Close", "Volume", "RSI", "EMAFast",
            "EMASlow", "ATR", "SessionVWAP", "RVOL", "AverageTradedValue",
            "ReturnPct",
        )
    }
    valid_ohlcv = (
        data["ValidOHLCV"].fillna(False).astype(bool).to_numpy()
        if "ValidOHLCV" in data.columns
        else np.ones(len(data), dtype=bool)
    )
    high_quality = (
        data["HighQualityTrigger"].fillna(False).astype(bool).to_numpy()
        if "HighQualityTrigger" in data.columns
        else np.zeros(len(data), dtype=bool)
    )
    prices_finite = np.column_stack([
        numeric["Open"], numeric["High"], numeric["Low"], numeric["Close"]
    ])
    liquidity_pass_values = (
        valid_ohlcv
        & np.isfinite(prices_finite).all(axis=1)
        & (prices_finite > 0).all(axis=1)
        & np.isfinite(numeric["Volume"])
        & (numeric["Volume"] > 0)
        & np.isfinite(numeric["AverageTradedValue"])
        & (numeric["AverageTradedValue"] >= config.minimum_average_traded_value)
        & np.isfinite(numeric["ATR"])
        & (numeric["ATR"] > 0)
        & ((numeric["High"] - numeric["Low"]) <= config.maximum_candle_range_atr * numeric["ATR"])
    )

    def liquidity_reason_at(position: int) -> str | None:
        if not valid_ohlcv[position] or not np.isfinite(prices_finite[position]).all():
            return "INVALID_OHLCV"
        if (prices_finite[position] <= 0).any():
            return "INVALID_OHLCV"
        if not math.isfinite(numeric["Volume"][position]):
            return "INVALID_OHLCV"
        if numeric["Volume"][position] <= 0:
            return "ZERO_VOLUME"
        average_value = numeric["AverageTradedValue"][position]
        if not math.isfinite(average_value) or average_value < config.minimum_average_traded_value:
            return "MINIMUM_TRADED_VALUE"
        atr_value = numeric["ATR"][position]
        if (
            not math.isfinite(atr_value)
            or atr_value <= 0
            or numeric["High"][position] - numeric["Low"][position]
            > config.maximum_candle_range_atr * atr_value
        ):
            return "CANDLE_RANGE_QUALITY"
        return None

    candidates: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    armed: dict[str, Any] | None = None
    current_session: Any = None
    trend_qualified = 0
    armed_count = 0
    entry_start = datetime_time.fromisoformat(config.entry_start_time)
    last_entry = datetime_time.fromisoformat(config.last_entry_time)

    for index in range(start, len(data)):
        session = sessions[index]
        if current_session != session:
            if armed is not None:
                events.append({"type": "SESSION_END_CANCEL", "timestamp": _iso(data.index[index])})
            armed = None
            current_session = session
        if not valid_ohlcv[index]:
            if armed is not None:
                events.append({
                    "type": "INVALID_OHLCV_CANCEL",
                    "timestamp": _iso(data.index[index]),
                    "armTimestamp": armed["armTimestamp"],
                })
            armed = None
            continue
        rsi = _finite(numeric["RSI"][index])
        atr = _finite(numeric["ATR"][index])
        ema_fast = _finite(numeric["EMAFast"][index])
        ema_slow = _finite(numeric["EMASlow"][index])
        vwap = _finite(numeric["SessionVWAP"][index])
        if None in (rsi, atr, ema_fast, ema_slow, vwap) or atr is None or atr <= 0:
            continue

        if armed is not None:
            bars_since_arm = index - int(armed["armBarIndex"])
            armed["pullbackSwingLow"] = min(
                float(armed["pullbackSwingLow"]), numeric["Low"][index]
            )
            cancel_reason: str | None = None
            if bars_since_arm > config.setup_expiry_bars:
                cancel_reason = "SETUP_EXPIRED"
            elif float(rsi) > config.maximum_trigger_rsi:
                cancel_reason = "RSI_EXCEEDED_MAXIMUM"
            elif float(ema_fast) <= float(ema_slow):
                cancel_reason = "EMA_TREND_CANCELLED"
            elif numeric["Close"][index] < float(ema_slow) - config.material_below_ema_atr * float(atr):
                cancel_reason = "MATERIAL_EMA_BREAK"

            valid_trigger = (
                cancel_reason is None
                and 0 < bars_since_arm <= config.setup_expiry_bars
                and index > 0
                and sessions[index - 1] == session
                and numeric["Close"][index] > numeric["High"][index - 1]
                and numeric["Close"][index] > float(vwap)
                and numeric["Close"][index] > float(ema_fast)
                and float(ema_fast) > float(ema_slow)
                and float(rsi) > config.rsi_trigger_level
                and float(rsi) <= config.maximum_trigger_rsi
                and _finite(numeric["RVOL"][index]) is not None
                and numeric["RVOL"][index] >= config.minimum_trigger_rvol
            )
            if valid_trigger:
                timestamp_time = data.index[index].time().replace(tzinfo=None)
                liquidity_pass = bool(liquidity_pass_values[index])
                liquidity_reason = liquidity_reason_at(index)
                candidate = {
                    "candidateId": f"{symbol}:{data.index[index].value}",
                    "symbol": symbol,
                    "signalTimestamp": _iso(data.index[index]),
                    "signalBarIndex": index,
                    "armTimestamp": armed["armTimestamp"],
                    "armBarIndex": armed["armBarIndex"],
                    "rsiAtArm": armed["rsiAtArm"],
                    "vwapAtArm": armed["vwapAtArm"],
                    "emaFastAtArm": armed["emaFastAtArm"],
                    "emaSlowAtArm": armed["emaSlowAtArm"],
                    "atrAtArm": armed["atrAtArm"],
                    "nearestPullbackReference": armed["nearestPullbackReference"],
                    "pullbackSwingLow": _finite(armed["pullbackSwingLow"], 4),
                    "previousHigh": _finite(numeric["High"][index - 1], 4),
                    "triggerClose": _finite(numeric["Close"][index], 4),
                    "triggerOpen": _finite(numeric["Open"][index], 4),
                    "triggerHigh": _finite(numeric["High"][index], 4),
                    "triggerLow": _finite(numeric["Low"][index], 4),
                    "triggerRsi": _finite(rsi),
                    "rvol": _finite(numeric["RVOL"][index]),
                    "atrAtTrigger": _finite(atr),
                    "sessionVwap": _finite(vwap),
                    "emaFast": _finite(ema_fast),
                    "emaSlow": _finite(ema_slow),
                    "averageTradedValue": _finite(numeric["AverageTradedValue"][index], 2),
                    "stockReturnPct": _finite(numeric["ReturnPct"][index]),
                    "highQualityTrigger": bool(high_quality[index]),
                    "timeWindowPassed": entry_start <= timestamp_time <= last_entry,
                    "liquidityPassed": liquidity_pass,
                    "liquidityReason": liquidity_reason,
                }
                candidate.update(_plan_exit(data, candidate, config))
                candidates.append(candidate)
                armed = None
                continue
            if cancel_reason is not None:
                events.append({
                    "type": cancel_reason,
                    "timestamp": _iso(data.index[index]),
                    "armTimestamp": armed["armTimestamp"],
                })
                armed = None

        if armed is not None:
            continue
        slope_index = index - config.ema_slope_lookback_bars
        prior_start = max(0, index - 3)
        prior_indices = range(prior_start, index)
        prior_same_session = index - prior_start == 3 and all(
            sessions[position] == session for position in prior_indices
        )
        slope_same_session = slope_index >= 0 and sessions[slope_index] == session
        trend_pass = (
            slope_same_session
            and float(ema_fast) > float(ema_slow)
            and pd.notna(numeric["EMASlow"][slope_index])
            and float(ema_slow) > numeric["EMASlow"][slope_index]
            and prior_same_session
            and any(
                pd.notna(numeric["SessionVWAP"][position])
                and numeric["Close"][position] > numeric["SessionVWAP"][position]
                for position in prior_indices
            )
        )
        liquidity_pass = bool(liquidity_pass_values[index])
        if not trend_pass or not liquidity_pass:
            continue
        trend_qualified += 1
        distances = {
            "SESSION_VWAP": abs(numeric["Close"][index] - float(vwap)),
            "EMA_FAST": abs(numeric["Close"][index] - float(ema_fast)),
            "EMA_SLOW": abs(numeric["Close"][index] - float(ema_slow)),
        }
        reference, distance = min(distances.items(), key=lambda item: (item[1], item[0]))
        arm_pass = (
            config.rsi_pullback_minimum <= float(rsi) <= config.rsi_pullback_maximum
            and distance <= config.pullback_approach_atr * float(atr)
            and numeric["Close"][index] >= float(ema_slow) - config.material_below_ema_atr * float(atr)
        )
        if arm_pass:
            armed_count += 1
            armed = {
                "armTimestamp": _iso(data.index[index]),
                "armBarIndex": index,
                "rsiAtArm": _finite(rsi),
                "vwapAtArm": _finite(vwap),
                "emaFastAtArm": _finite(ema_fast),
                "emaSlowAtArm": _finite(ema_slow),
                "atrAtArm": _finite(atr),
                "nearestPullbackReference": reference,
                "pullbackSwingLow": numeric["Low"][index],
            }
            events.append({"type": "PULLBACK_ARMED", **armed})

    return {
        "symbol": symbol,
        "bars": max(len(data) - start, 0),
        "firstCandle": _iso(data.index[start]) if start < len(data) else None,
        "lastCandle": _iso(data.index[-1]) if len(data) else None,
        "trendQualifiedBars": trend_qualified,
        "pullbacksArmed": armed_count,
        "candidates": candidates,
        "events": events,
    }


def _sample_frame(frame: pd.DataFrame, timestamp: pd.Timestamp, stale_seconds: int) -> pd.Series | None:
    if frame.empty:
        return None
    position = int(frame.index.searchsorted(timestamp, side="right")) - 1
    if position < 0:
        return None
    source = frame.index[position]
    if source > timestamp or (timestamp - source).total_seconds() > stale_seconds:
        return None
    return frame.iloc[position]


def build_nifty_candidate_context(
    nifty_candles: pd.DataFrame,
    candidates: Sequence[Mapping[str, Any]],
    config: VwapPullbackConfig,
) -> dict[str, dict[str, Any]]:
    if nifty_candles.empty:
        return {}
    features = calculate_vwap_pullback_features(nifty_candles, config)
    contexts: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        timestamp = _as_ist(candidate["signalTimestamp"])
        row = _sample_frame(features, timestamp, config.market_context_stale_seconds)
        if row is None:
            continue
        close = _finite(row.get("Close"))
        vwap = _finite(row.get("SessionVWAP"))
        ema_fast = _finite(row.get("EMAFast"))
        ema_slow = _finite(row.get("EMASlow"))
        if None in (close, vwap, ema_fast, ema_slow):
            continue
        supportive = bool(float(close) > float(vwap) or float(ema_fast) >= float(ema_slow))
        contexts[str(candidate["candidateId"])] = {
            "available": True,
            "supportive": supportive,
            "close": close,
            "sessionVwap": vwap,
            "emaFast": ema_fast,
            "emaSlow": ema_slow,
            "returnPct": _finite(row.get("ReturnPct")),
            "sourceTimestamp": _iso(row.name),
        }
    return contexts


def build_supporting_context(
    candidates: Sequence[Mapping[str, Any]],
    *,
    feature_paths_by_symbol: Mapping[str, str],
    breadth_symbols: Sequence[str],
    sector_by_symbol: Mapping[str, str],
    config: VwapPullbackConfig,
) -> dict[str, dict[str, Any]]:
    if not candidates:
        return {}
    timestamps = pd.DatetimeIndex(sorted({_as_ist(item["signalTimestamp"]) for item in candidates}))
    timestamp_ns = timestamps.as_unit("ns").asi8
    stale_ns = config.market_context_stale_seconds * 1_000_000_000
    count = len(timestamps)
    breadth_observed = np.zeros(count, dtype=np.int32)
    breadth_bullish = np.zeros(count, dtype=np.int32)
    sectors = sorted(set(sector_by_symbol.values()))
    sector_state = {
        sector: {
            "observed": np.zeros(count, dtype=np.int32),
            "bullish": np.zeros(count, dtype=np.int32),
            "sum": np.zeros(count, dtype=float),
        }
        for sector in sectors
    }
    breadth_set = set(breadth_symbols)
    for symbol in sorted(set(feature_paths_by_symbol) & (breadth_set | set(sector_by_symbol))):
        path = Path(feature_paths_by_symbol[symbol])
        if not path.is_file():
            continue
        frame = pd.read_parquet(path, columns=["Close", "EMASlow", "ReturnPct"])
        frame.index = _as_ist_index(frame.index)
        positions = np.asarray(frame.index.searchsorted(timestamps, side="right"), dtype=np.int64) - 1
        valid_position = positions >= 0
        safe = np.maximum(positions, 0)
        source_ns = frame.index.as_unit("ns").asi8[safe]
        fresh = valid_position & (timestamp_ns >= source_ns) & ((timestamp_ns - source_ns) <= stale_ns)
        if symbol in breadth_set:
            close = pd.to_numeric(frame["Close"], errors="coerce").to_numpy(dtype=float)[safe]
            ema = pd.to_numeric(frame["EMASlow"], errors="coerce").to_numpy(dtype=float)[safe]
            valid = fresh & np.isfinite(close) & np.isfinite(ema)
            breadth_observed += valid.astype(np.int32)
            breadth_bullish += (valid & (close >= ema)).astype(np.int32)
        sector = sector_by_symbol.get(symbol)
        if sector in sector_state:
            returns = pd.to_numeric(frame["ReturnPct"], errors="coerce").to_numpy(dtype=float)[safe]
            valid = fresh & np.isfinite(returns)
            state = sector_state[sector]
            state["observed"] += valid.astype(np.int32)
            state["bullish"] += (valid & (returns > 0)).astype(np.int32)
            state["sum"] += np.where(valid, returns, 0.0)

    timestamp_position = {timestamp.isoformat(): index for index, timestamp in enumerate(timestamps)}
    output: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        position = timestamp_position[_as_ist(candidate["signalTimestamp"]).isoformat()]
        observed = int(breadth_observed[position])
        breadth_pct = (int(breadth_bullish[position]) / observed * 100.0) if observed else None
        sector = sector_by_symbol.get(str(candidate["symbol"]))
        state = sector_state.get(sector or "")
        sector_observed = int(state["observed"][position]) if state is not None else 0
        sector_return = (
            float(state["sum"][position] / sector_observed)
            if state is not None and sector_observed else None
        )
        sector_bullish_pct = (
            int(state["bullish"][position]) / sector_observed * 100.0
            if state is not None and sector_observed else None
        )
        output[str(candidate["candidateId"])] = {
            "breadthAvailable": observed >= config.minimum_breadth_symbols,
            "breadthSymbolCount": observed,
            "breadthPct": _finite(breadth_pct),
            "breadthSupportive": bool(
                observed >= config.minimum_breadth_symbols
                and breadth_pct is not None
                and breadth_pct >= config.minimum_breadth_pct
            ),
            "sectorMappingFound": sector is not None,
            "sectorName": sector,
            "sectorAvailable": sector_observed >= config.minimum_sector_members,
            "sectorMemberCount": sector_observed,
            "sectorReturnPct": _finite(sector_return),
            "sectorBullishPct": _finite(sector_bullish_pct),
            "sectorSupportive": bool(
                sector_observed >= config.minimum_sector_members
                and sector_return is not None
                and sector_return > 0
                and sector_bullish_pct is not None
                and sector_bullish_pct >= config.minimum_sector_bullish_pct
            ),
        }
    return output


def score_candidate_quality(
    candidate: Mapping[str, Any],
    nifty: Mapping[str, Any] | None,
    support: Mapping[str, Any] | None,
    config: VwapPullbackConfig,
) -> dict[str, Any]:
    nifty = nifty or {"available": False, "supportive": False}
    support = support or {}
    stock_return = _finite(candidate.get("stockReturnPct"))
    nifty_return = _finite(nifty.get("returnPct"))
    sector_return = _finite(support.get("sectorReturnPct"))
    relative_strength = (
        stock_return is not None
        and nifty_return is not None
        and sector_return is not None
        and stock_return > nifty_return
        and stock_return > sector_return
    )
    components = {
        "niftySupportive": 20 if bool(nifty.get("available")) and bool(nifty.get("supportive")) else 0,
        "sectorSupportive": 15 if bool(support.get("sectorSupportive")) else 0,
        "breadthSupportive": 15 if bool(support.get("breadthSupportive")) else 0,
        "relativeStrength": 20 if relative_strength else 0,
        "highRvol": 15 if float(candidate.get("rvol") or 0) >= config.quality_rvol_threshold else 0,
        "highQualityTrigger": 15 if bool(candidate.get("highQualityTrigger")) else 0,
    }
    return {
        "qualityScore": float(sum(components.values())),
        "qualityComponents": components,
        "relativeStrengthPassed": relative_strength,
    }


def enrich_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    nifty_by_candidate: Mapping[str, Mapping[str, Any]],
    support_by_candidate: Mapping[str, Mapping[str, Any]],
    config: VwapPullbackConfig,
    oi_repository: Any | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in candidates:
        candidate = dict(item)
        candidate_id = str(candidate["candidateId"])
        nifty = dict(nifty_by_candidate.get(candidate_id, {}))
        support = dict(support_by_candidate.get(candidate_id, {}))
        quality = score_candidate_quality(candidate, nifty, support, config)
        candidate.update(quality)
        candidate.update({
            "niftyDataAvailable": bool(nifty.get("available")),
            "niftySupportive": bool(nifty.get("supportive")),
            "niftySourceTimestamp": nifty.get("sourceTimestamp"),
            "niftyReturnPct": nifty.get("returnPct"),
            **support,
        })
        if not candidate["timeWindowPassed"]:
            reason = "TIME_WINDOW_FAILED"
        elif not candidate["niftyDataAvailable"] and config.market_context_fail_policy == "REJECT":
            reason = "MARKET_CONTEXT_UNAVAILABLE"
        elif candidate["niftyDataAvailable"] and not candidate["niftySupportive"]:
            reason = "NIFTY_STRONGLY_BEARISH"
        elif not candidate["liquidityPassed"]:
            reason = "LIQUIDITY_FAILED"
        elif config.enforce_minimum_quality_score and candidate["qualityScore"] < config.minimum_quality_score:
            reason = "QUALITY_SCORE_FAILED"
        elif candidate.get("attemptStatus") != "READY":
            reason = str(candidate.get("primaryReason") or candidate.get("attemptStatus") or "INVALID_RISK")
        else:
            reason = None
        candidate["marketSafetyPassed"] = bool(
            (candidate["niftyDataAvailable"] and candidate["niftySupportive"])
            or (not candidate["niftyDataAvailable"] and config.market_context_fail_policy == "ADVISORY")
        )
        candidate["entriesAttempted"] = reason not in {
            "TIME_WINDOW_FAILED", "MARKET_CONTEXT_UNAVAILABLE", "NIFTY_STRONGLY_BEARISH",
            "LIQUIDITY_FAILED", "QUALITY_SCORE_FAILED",
        }
        candidate["primaryReason"] = reason
        candidate["status"] = "REJECTED" if reason else "PORTFOLIO_PENDING"
        candidate["reason"] = REASON_MESSAGES.get(reason or "ACCEPTED")
        if config.oi_mode == "OFF":
            candidate.update({"oiMode": "OFF", "oiResult": "NOT_EVALUATED", "oiSourceTimestamp": None})
        else:
            snapshot = (
                oi_repository.regime_at_or_before(
                    candidate["signalTimestamp"], stale_seconds=config.oi_stale_data_seconds
                )
                if oi_repository is not None else None
            )
            candidate.update({
                "oiMode": "ADVISORY",
                "oiResult": (snapshot or {}).get("regime", "UNAVAILABLE"),
                "oiScore": (snapshot or {}).get("combinedScore"),
                "oiSourceTimestamp": (snapshot or {}).get("sourceTimestamp"),
            })
        output.append(candidate)
    return output


def execute_portfolio(
    candidates: Sequence[Mapping[str, Any]], config: VwapPullbackConfig
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    rejected: list[dict[str, Any]] = []
    for item in candidates:
        candidate = dict(item)
        if candidate.get("primaryReason"):
            rejected.append(candidate)
            continue
        entry = _as_ist(candidate["entryTimestamp"])
        grouped.setdefault(entry, []).append(candidate)
    active: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    daily_entries: dict[Any, int] = {}
    daily_losses: dict[Any, int] = {}
    daily_realized: dict[Any, float] = {}
    loss_limit = config.configured_capital * config.maximum_daily_loss_pct / 100.0

    for entry_timestamp in sorted(grouped):
        still_active: list[dict[str, Any]] = []
        for trade in active:
            exit_timestamp = _as_ist(trade["exitTimestamp"])
            if exit_timestamp <= entry_timestamp:
                exit_day = exit_timestamp.date()
                pnl = float(trade["netPnl"])
                daily_realized[exit_day] = daily_realized.get(exit_day, 0.0) + pnl
                if pnl < 0:
                    daily_losses[exit_day] = daily_losses.get(exit_day, 0) + 1
            else:
                still_active.append(trade)
        active = still_active
        day = entry_timestamp.date()
        ordered = sorted(
            grouped[entry_timestamp],
            key=lambda row: (-float(row.get("qualityScore") or 0), str(row["symbol"]), str(row["candidateId"])),
        )
        for candidate in ordered:
            reason: str | None = None
            if daily_entries.get(day, 0) >= config.maximum_trades_per_day:
                reason = "MAX_TRADES_PER_DAY"
            elif len(active) >= config.maximum_concurrent_trades:
                reason = "MAX_CONCURRENT_TRADES"
            elif daily_losses.get(day, 0) >= config.stop_after_daily_losses:
                reason = "DAILY_LOSS_COUNT"
            elif daily_realized.get(day, 0.0) <= -loss_limit:
                reason = "DAILY_LOSS_LIMIT"
            elif sum(float(trade["capitalDeployed"]) for trade in active) + float(candidate["capitalDeployed"]) > config.configured_capital:
                reason = "CAPITAL_LIMIT"
            if reason is not None:
                candidate.update({
                    "primaryReason": reason,
                    "status": "REJECTED",
                    "reason": REASON_MESSAGES[reason],
                })
                rejected.append(candidate)
                continue
            sequence = len(trades) + 1
            trade = {
                **candidate,
                "tradeId": f"{STRATEGY_KEY}:{sequence}",
                "sequenceNumber": sequence,
                "strategyMode": STRATEGY_KEY,
                "status": str(candidate["exitReason"]),
                "primaryReason": "ACCEPTED",
                "reason": REASON_MESSAGES["ACCEPTED"],
            }
            trades.append(trade)
            active.append(trade)
            daily_entries[day] = daily_entries.get(day, 0) + 1
    return trades, rejected


def summarize_results(
    symbol_results: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
    config: VwapPullbackConfig,
) -> dict[str, Any]:
    net = [float(item["netPnl"]) for item in trades]
    winners = [value for value in net if value > 0]
    losers = [value for value in net if value < 0]
    cumulative = np.cumsum(np.asarray(net, dtype=float)) if net else np.asarray([], dtype=float)
    peaks = np.maximum.accumulate(np.concatenate(([0.0], cumulative))) if net else np.asarray([0.0])
    equity = np.concatenate(([0.0], cumulative))
    drawdown = float(np.max(peaks - equity)) if len(equity) else 0.0
    consecutive = 0
    maximum_consecutive = 0
    for value in net:
        consecutive = consecutive + 1 if value < 0 else 0
        maximum_consecutive = max(maximum_consecutive, consecutive)
    traded_days = {pd.Timestamp(item["entryTimestamp"]).date() for item in trades}
    sessions = {
        pd.Timestamp(event["armTimestamp"]).date()
        for result in symbol_results
        for event in result.get("events", [])
        if event.get("armTimestamp")
    }
    reason_counts: dict[str, int] = {}
    for item in rejected:
        reason = str(item.get("primaryReason") or "UNKNOWN")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    funnel = {
        "trendQualifiedBars": sum(int(item.get("trendQualifiedBars", 0)) for item in symbol_results),
        "pullbacksArmed": sum(int(item.get("pullbacksArmed", 0)) for item in symbol_results),
        "validTriggerCandles": len(candidates),
        "marketSafetyPassed": sum(bool(item.get("marketSafetyPassed")) for item in candidates),
        "liquidityPassed": sum(bool(item.get("marketSafetyPassed")) and bool(item.get("liquidityPassed")) for item in candidates),
        "entriesAttempted": sum(bool(item.get("entriesAttempted")) for item in candidates),
        "gapSkips": reason_counts.get("GAP_TOO_LARGE", 0),
        "riskWidthSkips": reason_counts.get("RISK_TOO_WIDE", 0),
        "dailyLimitSkips": sum(reason_counts.get(code, 0) for code in ("MAX_TRADES_PER_DAY", "DAILY_LOSS_COUNT", "DAILY_LOSS_LIMIT")),
        "executedTrades": len(trades),
    }
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    return {
        "rawCandidates": len(candidates),
        "acceptedBuySignals": sum(bool(item.get("entriesAttempted")) for item in candidates),
        "executedTrades": len(trades),
        "rejectedCandidates": len(rejected),
        "winningTrades": len(winners),
        "losingTrades": len(losers),
        "winRate": _finite(len(winners) / len(trades) * 100.0, 2) if trades else 0.0,
        "averageWinner": _finite(np.mean(winners), 2) if winners else None,
        "averageLoser": _finite(np.mean(losers), 2) if losers else None,
        "grossPnl": _finite(sum(float(item["grossPnl"]) for item in trades), 2),
        "costs": _finite(sum(float(item["totalCosts"]) for item in trades), 2),
        "netPnl": _finite(sum(net), 2),
        "expectancy": _finite(np.mean(net), 2) if net else None,
        "profitFactor": _finite(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "maximumDrawdown": _finite(drawdown, 2),
        "maximumDrawdownPct": _finite(drawdown / config.configured_capital * 100.0, 6),
        "averageR": _finite(np.mean([float(item["rMultiple"]) for item in trades]), 6) if trades else None,
        "maximumConsecutiveLosses": maximum_consecutive,
        "tradesPerDay": _finite(len(trades) / len(traded_days), 4) if traded_days else 0.0,
        "noTradeDays": max(len(sessions - traded_days), 0),
        "targetExits": sum(str(item["exitReason"]).startswith("TARGET") for item in trades),
        "stopExits": sum(str(item["exitReason"]).startswith("STOP") for item in trades),
        "timeExits": sum(item["exitReason"] == "TIME_EXIT" for item in trades),
        "sessionExits": sum(item["exitReason"] == "SESSION_EXIT" for item in trades),
        "rejectionCounts": dict(sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))),
        "funnel": funnel,
        "candleRowsProcessed": sum(int(item.get("bars", 0)) for item in symbol_results),
    }


FEATURE_COLUMNS = [
    "Open", "High", "Low", "Close", "Volume", "RSI", "EMAFast", "EMASlow",
    "ATR", "SessionVWAP", "RVOL", "AverageTradedValue", "ReturnPct", "HighQualityTrigger",
    "ValidOHLCV",
]


def _raw_cache_path(cache_directory: Path, symbol: str, duration_years: int, benchmark: bool = False) -> Path:
    safe = "NIFTY50" if benchmark else "".join(character for character in symbol if character.isalnum() or character in "-&")
    return cache_directory / f"{safe}-5-{duration_years}y.csv.gz"


def prepare_symbol_task(task: Mapping[str, Any]) -> dict[str, Any]:
    from backtest_api import prepare_candles

    symbol = str(task["symbol"])
    config: VwapPullbackConfig = task["config"]
    source = _raw_cache_path(Path(str(task["cacheDirectory"])), symbol, int(task["durationYears"]))
    if not source.is_file():
        raise FileNotFoundError(f"Local 5-minute candle cache is unavailable for {symbol}")
    feature_key = stable_fingerprint({
        "version": FEATURE_CODE_VERSION,
        "symbol": symbol,
        "source": file_stat_fingerprint(source),
        "analysisStartCompletedBucket": _five_minute_bucket(task["analysisStart"]),
        "analysisEndCompletedBucket": _five_minute_bucket(task["now"]),
        "features": {
            key: value for key, value in config.public().items()
            if key in {
                "rsi_length", "ema_fast", "ema_slow", "atr_length", "rvol_period",
                "relative_strength_lookback_bars", "minimum_average_traded_value",
                "maximum_candle_range_atr",
            }
        },
    })
    feature_path = Path(str(task["featureCacheDirectory"])) / symbol / f"{feature_key}.parquet"
    read_started = time.perf_counter()
    hit = feature_path.is_file()
    if hit:
        features = pd.read_parquet(feature_path)
        bytes_read = feature_path.stat().st_size
        indicator_seconds = 0.0
    else:
        raw = pd.read_csv(source, index_col="Timestamp", parse_dates=["Timestamp"])
        raw.index = pd.DatetimeIndex(raw.index)
        raw.index = raw.index.tz_localize(IST) if raw.index.tz is None else raw.index.tz_convert(IST)
        candles = prepare_candles(raw, "5m", task["analysisStart"], task["now"], warmup_bars=100)
        indicator_started = time.perf_counter()
        features = calculate_vwap_pullback_features(candles, config)
        indicator_seconds = time.perf_counter() - indicator_started
        feature_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = feature_path.with_suffix(f".{os.getpid()}.tmp.parquet")
        features.loc[:, FEATURE_COLUMNS].to_parquet(temporary, index=True)
        os.replace(temporary, feature_path)
        bytes_read = source.stat().st_size
    read_seconds = time.perf_counter() - read_started
    if bool(task.get("detectCandidates", True)):
        detection_started = time.perf_counter()
        result = detect_pullback_candidates(
            symbol, features, config, analysis_start=task["analysisStart"]
        )
        detection_seconds = time.perf_counter() - detection_started
    else:
        result = None
        detection_seconds = 0.0
    return {
        "symbol": symbol,
        "result": result,
        "featurePath": str(feature_path),
        "featureCacheHit": hit,
        "metrics": {
            "bytesRead": bytes_read,
            "readSeconds": read_seconds,
            "indicatorSeconds": indicator_seconds,
            "candidateSeconds": detection_seconds,
            "candles": len(features),
            "invalidCandleRows": int((~features["ValidOHLCV"].astype(bool)).sum()),
        },
    }


def prepare_symbol_batch(tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in tasks:
        try:
            rows.append({"item": prepare_symbol_task(task), "error": None})
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            rows.append({
                "item": None,
                "error": {"symbol": str(task.get("symbol") or ""), "message": str(error)},
            })
    return rows
