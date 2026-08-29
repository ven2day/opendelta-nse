from __future__ import annotations

import hashlib
import gzip
import json
import math
import os
import tempfile
import time
from dataclasses import asdict
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import numpy as np
import pandas as pd

from main import IST


STRATEGY_KEY = "top_5_opening_range_breakout"
STRATEGY_NAME = "Top-5 Opening Range Breakout"
STRATEGY_DESCRIPTION = (
    "Research-only top-five Opening Range Breakout with optional causal intraday rescans "
    "and Rolling Momentum Breakout entries for newly promoted symbols."
)
STRATEGY_VERSION = "top-5-opening-range-breakout-1.0.0"
FEATURE_CODE_VERSION = "top-5-opening-range-breakout-features-1"
SESSION_RULE_VERSION = "nse-completed-five-minute-session-1"
PORTFOLIO_RULE_VERSION = "top-5-opening-range-breakout-portfolio-1"
WATCHLIST_RULE_VERSION = "top-5-opening-range-breakout-selection-1"
OPENING_RANGE_RULE_VERSION = "top-five-orb-0915-0930-1"
MINIMUM_UNTOUCHED_VALIDATION_TRADES = 20
WatchlistMode = Literal["FROZEN_OPEN", "ROLLING"]
SelectionMethod = Literal["SCORE", "LIQUIDITY", "RANDOM", "FULL"]


@dataclass(frozen=True)
class DailyWatchlistConfig:
    execution_model: Literal["NEXT_BAR_OPEN"] = "NEXT_BAR_OPEN"
    opening_range_start_time: str = "09:15"
    opening_range_end_time: str = "09:30"
    last_entry_time: str = "14:45"
    square_off_time: str = "15:15"
    rsi_length: int = 14
    ema_fast: int = 9
    ema_slow: int = 20
    atr_length: int = 14
    rvol_period: int = 20
    mode: WatchlistMode = "FROZEN_OPEN"
    selection_time: str = "09:30"
    rescan_interval_minutes: int = 30
    rescan_end_time: str = "14:00"
    selected_symbols: int = 5
    primary_symbols: int = 2
    minimum_promotion_score: float = 60.0
    required_promotion_advantage: float = 10.0
    minimum_residence_minutes: int = 30
    maximum_replacements_per_rescan: int = 2
    maximum_symbols_per_sector: int = 2
    historical_sessions: int = 20
    rolling_window_minutes: int = 30
    breakout_lookback_bars: int = 6
    breakout_minimum_rvol: float = 1.50
    maximum_vwap_distance_atr: float = 1.0
    maximum_trades_per_symbol_per_day: int = 1
    opening_breakout_minimum_rvol: float = 1.50
    minimum_close_location: float = 0.60
    maximum_entry_gap_atr: float = 0.50
    structural_stop_buffer_atr: float = 0.05
    volatility_stop_atr: float = 0.60
    minimum_stop_pct: float = 0.35
    maximum_stop_pct: float = 1.00
    reward_risk_ratio: float = 1.50
    maximum_holding_bars: int = 6
    minimum_average_traded_value: float = 500_000.0
    maximum_candle_range_atr: float = 3.0
    live_maximum_spread_pct: float = 0.15
    quantity_per_trade: Literal[50] = 50
    configured_capital: float = 1_000_000.0
    maximum_capital_per_position: float = 1_000_000.0
    maximum_trades_per_day: int = 5
    maximum_concurrent_trades: int = 2
    stop_after_daily_losses: int = 2
    maximum_daily_loss_pct: float = 0.50
    buy_cost_bps: float = 5.0
    sell_cost_bps: float = 5.0
    slippage_bps: float = 2.0

    def validate(self) -> "DailyWatchlistConfig":
        if self.mode not in {"FROZEN_OPEN", "ROLLING"}:
            raise ValueError("Watchlist mode must be FROZEN_OPEN or ROLLING")
        integers = (
            self.rsi_length,
            self.ema_fast,
            self.ema_slow,
            self.atr_length,
            self.rvol_period,
            self.rescan_interval_minutes,
            self.selected_symbols,
            self.primary_symbols,
            self.minimum_residence_minutes,
            self.maximum_replacements_per_rescan,
            self.maximum_symbols_per_sector,
            self.historical_sessions,
            self.rolling_window_minutes,
            self.breakout_lookback_bars,
            self.maximum_trades_per_symbol_per_day,
            self.maximum_holding_bars,
            self.maximum_trades_per_day,
            self.maximum_concurrent_trades,
            self.stop_after_daily_losses,
        )
        if any(value <= 0 for value in integers):
            raise ValueError("Watchlist counts, windows, and intervals must be positive integers")
        if self.rescan_interval_minutes % 5 or self.rolling_window_minutes % 5:
            raise ValueError("Watchlist interval and rolling window must align to five-minute bars")
        if self.primary_symbols > self.selected_symbols:
            raise ValueError("Primary symbols cannot exceed selected symbols")
        if self.maximum_replacements_per_rescan > self.selected_symbols:
            raise ValueError("Maximum replacements cannot exceed selected symbols")
        if not 0 <= self.minimum_promotion_score <= 100:
            raise ValueError("Minimum promotion score must be between 0 and 100")
        if not 0 <= self.required_promotion_advantage <= 100:
            raise ValueError("Required promotion advantage must be between 0 and 100")
        positive = (
            self.breakout_minimum_rvol,
            self.opening_breakout_minimum_rvol,
            self.maximum_vwap_distance_atr,
            self.maximum_entry_gap_atr,
            self.volatility_stop_atr,
            self.minimum_stop_pct,
            self.maximum_stop_pct,
            self.reward_risk_ratio,
            self.minimum_average_traded_value,
            self.maximum_candle_range_atr,
            self.configured_capital,
            self.maximum_capital_per_position,
        )
        if any(value <= 0 for value in positive):
            raise ValueError("Watchlist signal, risk, liquidity, and capital limits must be positive")
        if self.ema_fast >= self.ema_slow:
            raise ValueError("EMA fast length must be below EMA slow length")
        if self.minimum_stop_pct > self.maximum_stop_pct:
            raise ValueError("Minimum stop distance cannot exceed maximum stop distance")
        if not 0 < self.minimum_close_location <= 1:
            raise ValueError("Minimum close location must be greater than zero and no more than one")
        if self.quantity_per_trade != 50:
            raise ValueError("Daily Scalping Watchlist uses exactly 50 shares per executed trade")
        if any(value < 0 for value in (self.buy_cost_bps, self.sell_cost_bps, self.slippage_bps)):
            raise ValueError("Costs and slippage cannot be negative")
        try:
            range_start = datetime_time.fromisoformat(self.opening_range_start_time)
            range_end = datetime_time.fromisoformat(self.opening_range_end_time)
            selection = datetime_time.fromisoformat(self.selection_time)
            end = datetime_time.fromisoformat(self.rescan_end_time)
            last_entry = datetime_time.fromisoformat(self.last_entry_time)
            square_off = datetime_time.fromisoformat(self.square_off_time)
        except ValueError as error:
            raise ValueError("Watchlist times must use HH:MM") from error
        if not range_start < range_end <= selection <= end < last_entry < square_off:
            raise ValueError(
                "Use opening range start < end <= selection <= final rescan < last entry < square-off"
            )
        if selection > end:
            raise ValueError("Watchlist selection time must not be after the final rescan")
        return self

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _as_ist(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(IST) if stamp.tzinfo is None else stamp.tz_convert(IST)


def _iso(value: Any) -> str:
    return _as_ist(value).isoformat()


def _finite(value: Any, digits: int = 6) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


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


class DailyWatchlistResultCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, fingerprint: str) -> Path:
        return self.root / fingerprint[:2] / f"{fingerprint}.json.gz"

    def load(self, fingerprint: str) -> dict[str, Any] | None:
        try:
            with gzip.open(self.path_for(fingerprint), "rt", encoding="utf-8") as handle:
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
                dir=path.parent,
                prefix=f".{fingerprint}.",
                suffix=".json.gz",
                delete=False,
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


def calculate_wilder_rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    average_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    average_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + relative_strength))
    return rsi.mask((average_loss == 0) & (average_gain > 0), 100.0).mask(
        (average_loss == 0) & (average_gain == 0), 50.0
    )


def calculate_ema(close: pd.Series, length: int) -> pd.Series:
    return close.ewm(span=length, adjust=False, min_periods=length).mean()


def calculate_wilder_atr(candles: pd.DataFrame, length: int) -> pd.Series:
    previous_close = candles["Close"].shift(1)
    true_range = pd.concat(
        [
            candles["High"] - candles["Low"],
            (candles["High"] - previous_close).abs(),
            (candles["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def calculate_session_vwap(candles: pd.DataFrame) -> pd.Series:
    session = pd.Series(candles.index.date, index=candles.index)
    typical = (candles["High"] + candles["Low"] + candles["Close"]) / 3.0
    cumulative_value = (typical * candles["Volume"]).groupby(session, sort=False).cumsum()
    cumulative_volume = candles["Volume"].groupby(session, sort=False).cumsum()
    return cumulative_value / cumulative_volume.replace(0, np.nan)


def calculate_watchlist_features(
    candles: pd.DataFrame,
    config: DailyWatchlistConfig,
) -> pd.DataFrame:
    config.validate()
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in candles.columns]
    if missing:
        raise ValueError("missing columns: " + ", ".join(missing))
    if not isinstance(candles.index, pd.DatetimeIndex):
        raise ValueError("timestamps are not a DatetimeIndex")
    if not candles.index.is_monotonic_increasing or candles.index.has_duplicates:
        raise ValueError("timestamps must be unique and ascending")
    if candles.index.tz is None:
        raise ValueError("timestamps are not timezone-aware")
    data = candles.copy().sort_index()
    data.index = pd.DatetimeIndex(data.index).tz_convert(IST)
    ohlcv = data[required].apply(pd.to_numeric, errors="coerce").astype(float)
    finite = pd.Series(np.isfinite(ohlcv.to_numpy(dtype=float)).all(axis=1), index=data.index)
    positive = ohlcv.gt(0).all(axis=1)
    ordered = (
        ohlcv["High"].ge(ohlcv[["Open", "Close", "Low"]].max(axis=1))
        & ohlcv["Low"].le(ohlcv[["Open", "Close", "High"]].min(axis=1))
    )
    valid = (finite & positive & ordered).fillna(False)
    data[required] = ohlcv
    data.loc[~valid, required] = np.nan
    data["ValidOHLCV"] = valid.astype(bool)
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
    return add_rolling_watchlist_features(data, config)


def add_rolling_watchlist_features(
    features: pd.DataFrame,
    config: DailyWatchlistConfig,
) -> pd.DataFrame:
    """Add causal rolling features, including same-clock historical RVOL.

    The denominator at timestamp T is the median rolling-window volume at T's
    clock time over prior valid sessions only. The current session is shifted
    out before the rolling median is calculated.
    """
    config.validate()
    data = features.copy().sort_index()
    index = pd.DatetimeIndex(data.index)
    index = index.tz_localize(IST) if index.tz is None else index.tz_convert(IST)
    data.index = index
    bars = max(1, config.rolling_window_minutes // 5)
    session = pd.Series(index.date, index=index)
    valid = data.get("ValidOHLCV", pd.Series(True, index=index)).fillna(False).astype(bool)
    volume = pd.to_numeric(data["Volume"], errors="coerce").where(valid)
    close = pd.to_numeric(data["Close"], errors="coerce").where(valid)
    open_price = pd.to_numeric(data["Open"], errors="coerce").where(valid)
    high = pd.to_numeric(data["High"], errors="coerce").where(valid)
    low = pd.to_numeric(data["Low"], errors="coerce").where(valid)
    traded_value = close * volume

    def session_rolling_sum(values: pd.Series) -> pd.Series:
        return values.groupby(session, sort=False).rolling(bars, min_periods=1).sum().reset_index(level=0, drop=True)

    rolling_volume = session_rolling_sum(volume)
    session_bar = data.groupby(session, sort=False).cumcount()
    rolling_count = volume.groupby(session, sort=False).rolling(bars, min_periods=1).count().reset_index(level=0, drop=True)
    expected_count = pd.Series(np.minimum(session_bar.to_numpy() + 1, bars), index=index)
    rolling_volume = rolling_volume.where(rolling_count == expected_count)
    data["RollingWindowVolume"] = rolling_volume
    data["RollingTradedValue"] = session_rolling_sum(traded_value).where(rolling_count == expected_count)
    shifted = close.groupby(session, sort=False).shift(bars)
    session_first_open = open_price.groupby(session, sort=False).transform("first")
    return_base = shifted.where(session_bar >= bars, session_first_open)
    data["RollingReturnPct"] = (close / return_base.replace(0, np.nan) - 1.0) * 100.0

    clock_key = pd.Series(index.strftime("%H:%M"), index=index)
    history = pd.Series(np.nan, index=index, dtype=float)
    for _, positions in rolling_volume.groupby(clock_key, sort=False).groups.items():
        values = rolling_volume.loc[positions]
        valid_values = values.dropna()
        historical_values = valid_values.shift(1).rolling(
            config.historical_sessions,
            min_periods=config.historical_sessions,
        ).median()
        history.loc[historical_values.index] = historical_values
    data["SameTimeHistoricalMedianVolume"] = history
    data["RollingWindowRvol"] = rolling_volume / history.replace(0, np.nan)

    three_bar = close / close.groupby(session, sort=False).shift(3) - 1.0
    prior_three = close.groupby(session, sort=False).shift(3) / close.groupby(session, sort=False).shift(6) - 1.0
    data["PriceAccelerationPct"] = (three_bar - prior_three) * 100.0
    candle_range = high - low
    data["CloseLocation"] = (close - low) / candle_range.replace(0, np.nan)
    data["UpperWickFraction"] = (high - pd.concat([open_price, close], axis=1).max(axis=1)) / candle_range.replace(0, np.nan)
    atr = pd.to_numeric(data.get("ATR"), errors="coerce")
    vwap = pd.to_numeric(data.get("SessionVWAP"), errors="coerce")
    data["DistanceFromVwapAtr"] = (close - vwap) / atr.replace(0, np.nan)
    data["CandleRangeAtr"] = candle_range / atr.replace(0, np.nan)
    ema_fast = pd.to_numeric(data.get("EMAFast"), errors="coerce")
    ema_slow = pd.to_numeric(data.get("EMASlow"), errors="coerce")
    data["BullishEmaTrend"] = (ema_fast > ema_slow) & (ema_fast > ema_fast.groupby(session, sort=False).shift(1))
    data["EmaFastRising"] = ema_fast > ema_fast.groupby(session, sort=False).shift(1)
    data["AtrPct"] = atr / close.replace(0, np.nan) * 100.0
    if "Bid" in data.columns and "Ask" in data.columns:
        bid = pd.to_numeric(data["Bid"], errors="coerce")
        ask = pd.to_numeric(data["Ask"], errors="coerce")
        midpoint = (bid + ask) / 2.0
        data["SpreadPct"] = ((ask - bid) / midpoint.replace(0, np.nan) * 100.0).where((bid > 0) & (ask >= bid))
    elif "SpreadPct" not in data.columns:
        data["SpreadPct"] = np.nan
    return data


def rescan_times_for_session(session_day: date, config: DailyWatchlistConfig) -> list[pd.Timestamp]:
    config.validate()
    start = datetime.combine(session_day, datetime_time.fromisoformat(config.selection_time), tzinfo=IST)
    end = datetime.combine(session_day, datetime_time.fromisoformat(config.rescan_end_time), tzinfo=IST)
    if config.mode == "FROZEN_OPEN":
        return [pd.Timestamp(start)]
    output: list[pd.Timestamp] = []
    cursor = start
    while cursor <= end:
        output.append(pd.Timestamp(cursor))
        cursor += timedelta(minutes=config.rescan_interval_minutes)
    return output


def _sample_completed(frame: pd.DataFrame, timestamp: pd.Timestamp) -> pd.Series | None:
    if frame.empty:
        return None
    position = int(frame.index.searchsorted(timestamp, side="right")) - 1
    if position < 0:
        return None
    source = frame.index[position]
    if source.date() != timestamp.date() or source > timestamp:
        return None
    return frame.iloc[position]


def _percentiles(values: Mapping[str, float | None]) -> dict[str, float]:
    finite = {symbol: float(value) for symbol, value in values.items() if value is not None and math.isfinite(float(value))}
    if not finite:
        return {}
    series = pd.Series(finite, dtype=float)
    denominator = max(len(series) - 1, 1)
    ranks = series.rank(method="average") - 1.0
    return {str(symbol): float(rank / denominator * 100.0) for symbol, rank in ranks.items()}


def score_rescan_rows(
    rows: Mapping[str, pd.Series],
    *,
    nifty_row: pd.Series | None,
    sector_by_symbol: Mapping[str, str],
    minimum_average_traded_value: float,
    maximum_candle_range_atr: float,
    maximum_spread_pct: float,
) -> list[dict[str, Any]]:
    stock_returns = {symbol: _finite(row.get("RollingReturnPct")) for symbol, row in rows.items()}
    nifty_return = _finite(nifty_row.get("RollingReturnPct")) if nifty_row is not None else None
    sector_groups: dict[str, list[float]] = {}
    for symbol, value in stock_returns.items():
        sector = sector_by_symbol.get(symbol)
        if sector and value is not None:
            sector_groups.setdefault(sector, []).append(value)
    sector_returns = {sector: float(np.mean(values)) for sector, values in sector_groups.items() if values}
    relative_nifty = {
        symbol: (value - nifty_return if value is not None and nifty_return is not None else None)
        for symbol, value in stock_returns.items()
    }
    relative_sector = {
        symbol: (
            value - sector_returns[sector_by_symbol[symbol]]
            if value is not None and sector_by_symbol.get(symbol) in sector_returns else None
        )
        for symbol, value in stock_returns.items()
    }
    rvol_pct = _percentiles({symbol: _finite(row.get("RollingWindowRvol")) for symbol, row in rows.items()})
    traded_pct = _percentiles({symbol: _finite(row.get("RollingTradedValue")) for symbol, row in rows.items()})
    nifty_pct = _percentiles(relative_nifty)
    sector_pct = _percentiles(relative_sector)

    output: list[dict[str, Any]] = []
    for symbol, row in rows.items():
        close = _finite(row.get("Close"))
        volume = _finite(row.get("Volume"))
        atr = _finite(row.get("ATR"))
        rvol = _finite(row.get("RollingWindowRvol"))
        traded_value = _finite(row.get("RollingTradedValue"), 2)
        average_value = _finite(row.get("AverageTradedValue"), 2)
        range_atr = _finite(row.get("CandleRangeAtr"))
        spread = _finite(row.get("SpreadPct"))
        mandatory = bool(
            bool(row.get("ValidOHLCV", True))
            and close is not None and close > 0
            and volume is not None and volume > 0
            and atr is not None and atr > 0
            and rvol is not None and rvol > 0
            and traded_value is not None and traded_value > 0
            and average_value is not None and average_value >= minimum_average_traded_value
            and range_atr is not None and range_atr <= maximum_candle_range_atr
            and (spread is None or spread <= maximum_spread_pct)
        )
        above_vwap_trend = bool(
            close is not None
            and _finite(row.get("SessionVWAP")) is not None
            and close > float(row["SessionVWAP"])
            and bool(row.get("BullishEmaTrend"))
        )
        acceleration = _finite(row.get("PriceAccelerationPct"))
        rolling_return = stock_returns.get(symbol)
        controlled_acceleration = bool(
            acceleration is not None and acceleration >= 0
            and rolling_return is not None and 0 <= rolling_return <= 3.0
            and range_atr is not None and range_atr <= 1.5
        )
        components = {
            "rollingRvolPercentile": 25.0 * rvol_pct.get(symbol, 0.0) / 100.0,
            "relativeToNiftyPercentile": 20.0 * nifty_pct.get(symbol, 0.0) / 100.0,
            "relativeToSectorPercentile": 15.0 * sector_pct.get(symbol, 0.0) / 100.0,
            "tradedValuePercentile": 15.0 * traded_pct.get(symbol, 0.0) / 100.0,
            "vwapAndEmaTrend": 15.0 if above_vwap_trend else 0.0,
            "controlledAcceleration": 10.0 if controlled_acceleration else 0.0,
        }
        penalties: dict[str, float] = {}
        distance = _finite(row.get("DistanceFromVwapAtr"))
        upper_wick = _finite(row.get("UpperWickFraction"))
        if distance is not None and distance > 1.0:
            penalties["MORE_THAN_ONE_ATR_ABOVE_VWAP"] = 15.0
        if rolling_return is not None and rolling_return > 3.0:
            penalties["THIRTY_MINUTE_GAIN_ABOVE_THREE_PCT"] = 15.0
        if upper_wick is not None and upper_wick > 0.60:
            penalties["LARGE_UPPER_WICK"] = 10.0
        if range_atr is not None and range_atr > 1.5:
            penalties["ABNORMAL_CANDLE_RANGE"] = 10.0
        if not bool(row.get("EmaFastRising")):
            penalties["FALLING_EMA_FAST"] = 10.0
        if spread is not None and spread > maximum_spread_pct:
            penalties["EXCESSIVE_SPREAD"] = 15.0
        score = max(0.0, min(100.0, sum(components.values()) - sum(penalties.values())))
        output.append({
            "symbol": symbol,
            "sector": sector_by_symbol.get(symbol),
            "sourceTimestamp": _iso(row.name),
            "eligible": mandatory,
            "score": round(score, 4),
            "components": {key: round(value, 4) for key, value in components.items()},
            "penalties": penalties,
            "rollingRvol": rvol,
            "rollingVolume": _finite(row.get("RollingWindowVolume"), 2),
            "rollingTradedValue": traded_value,
            "rollingReturnPct": rolling_return,
            "relativeToNiftyPct": _finite(relative_nifty.get(symbol)),
            "relativeToSectorPct": _finite(relative_sector.get(symbol)),
            "sessionVwap": _finite(row.get("SessionVWAP"), 4),
            "distanceFromVwapAtr": distance,
            "emaFast": _finite(row.get("EMAFast"), 4),
            "emaSlow": _finite(row.get("EMASlow"), 4),
            "emaFastRising": bool(row.get("EmaFastRising")),
            "rsi": _finite(row.get("RSI")),
            "atrPct": _finite(row.get("AtrPct")),
            "spreadPct": spread,
            "spreadStatus": "AVAILABLE" if spread is not None else "UNAVAILABLE_ADVISORY",
            "priceAccelerationPct": acceleration,
            "candleRangeAtr": range_atr,
            "upperWickFraction": upper_wick,
        })
    return sorted(output, key=lambda item: (-float(item["score"]), str(item["symbol"])))


def _candidate_base(
    symbol: str,
    data: pd.DataFrame,
    signal_index: int,
    *,
    signal_type: str,
    breakout_level: float,
    structural_low: float,
    config: DailyWatchlistConfig,
) -> dict[str, Any]:
    row = data.iloc[signal_index]
    signal = data.index[signal_index]
    candidate = {
        "candidateId": stable_fingerprint({
            "version": STRATEGY_VERSION,
            "symbol": symbol,
            "signalTimestamp": _iso(signal),
            "signalType": signal_type,
        })[:24],
        "symbol": symbol,
        "signalType": signal_type,
        "signalTimestamp": _iso(signal),
        "signalBarIndex": signal_index,
        "triggerClose": _finite(row["Close"], 4),
        "breakoutLevel": _finite(breakout_level, 4),
        "pullbackSwingLow": _finite(structural_low, 4),
        "sessionVwap": _finite(row.get("SessionVWAP"), 4),
        "emaFast": _finite(row.get("EMAFast"), 4),
        "emaSlow": _finite(row.get("EMASlow"), 4),
        "rsi": _finite(row.get("RSI")),
        "rollingRvol": _finite(row.get("RollingWindowRvol")),
        "atrAtTrigger": _finite(row.get("ATR"), 6),
        "closeLocation": _finite(row.get("CloseLocation")),
        "distanceFromVwapAtr": _finite(row.get("DistanceFromVwapAtr")),
        "primaryReason": None,
    }
    candidate.update(_plan_trade(data, candidate, config))
    return candidate


def _plan_trade(
    data: pd.DataFrame,
    candidate: Mapping[str, Any],
    config: DailyWatchlistConfig,
) -> dict[str, Any]:
    signal_index = int(candidate["signalBarIndex"])
    entry_index = signal_index + 1
    signal_timestamp = data.index[signal_index]
    if entry_index >= len(data) or data.index[entry_index].date() != signal_timestamp.date():
        return {"attemptStatus": "NO_NEXT_BAR", "primaryReason": "NO_NEXT_BAR"}
    if signal_timestamp.time().replace(tzinfo=None) > datetime_time.fromisoformat(config.last_entry_time):
        return {"attemptStatus": "AFTER_LAST_ENTRY", "primaryReason": "AFTER_LAST_ENTRY"}
    entry_row = data.iloc[entry_index]
    if not bool(entry_row.get("ValidOHLCV", True)):
        return {"attemptStatus": "INVALID_ENTRY_BAR", "primaryReason": "INVALID_ENTRY_BAR"}
    raw_entry = float(entry_row["Open"])
    trigger_close = float(candidate["triggerClose"])
    atr = float(candidate["atrAtTrigger"])
    gap_atr = abs(raw_entry - trigger_close) / atr
    if gap_atr > config.maximum_entry_gap_atr:
        return {
            "attemptStatus": "GAP_TOO_LARGE",
            "primaryReason": "GAP_TOO_LARGE",
            "rawEntryPrice": _finite(raw_entry, 4),
            "gapAtr": _finite(gap_atr, 6),
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
    quantity = config.quantity_per_trade
    capital_deployed = entry_price * quantity
    if capital_deployed > config.maximum_capital_per_position:
        return {"attemptStatus": "CAPITAL_LIMIT", "primaryReason": "CAPITAL_LIMIT"}
    target_price = entry_price + config.reward_risk_ratio * risk_per_share
    exit_index: int | None = None
    raw_exit: float | None = None
    exit_reason: str | None = None
    exit_timestamp: pd.Timestamp | None = None
    square_off = datetime_time.fromisoformat(config.square_off_time)
    five_minutes = pd.Timedelta(minutes=5)
    for index in range(entry_index + 1, len(data)):
        if data.index[index].date() != signal_timestamp.date():
            break
        row = data.iloc[index]
        if not bool(row.get("ValidOHLCV", True)):
            continue
        open_timestamp = data.index[index] - five_minutes
        open_price = float(row["Open"])
        if open_timestamp.time().replace(tzinfo=None) >= square_off:
            exit_index, raw_exit, exit_reason, exit_timestamp = index, open_price, "SESSION_EXIT", open_timestamp
            break
        if open_price <= stop_price:
            exit_index, raw_exit, exit_reason, exit_timestamp = index, open_price, "STOP_GAP", open_timestamp
            break
        if open_price >= target_price:
            exit_index, raw_exit, exit_reason, exit_timestamp = index, open_price, "TARGET_GAP", open_timestamp
            break
        if index > entry_index + config.maximum_holding_bars:
            exit_index, raw_exit, exit_reason, exit_timestamp = index, open_price, "TIME_EXIT", open_timestamp
            break
        if float(row["Low"]) <= stop_price:
            exit_index, raw_exit, exit_reason, exit_timestamp = index, stop_price, "STOP_EXIT", data.index[index]
            break
        if float(row["High"]) >= target_price:
            exit_index, raw_exit, exit_reason, exit_timestamp = index, target_price, "TARGET_EXIT", data.index[index]
            break
    if exit_index is None:
        same_session = np.flatnonzero(np.asarray(data.index.date) == signal_timestamp.date())
        if len(same_session) == 0:
            return {"attemptStatus": "NO_NEXT_BAR", "primaryReason": "NO_NEXT_BAR"}
        exit_index = int(same_session[-1])
        raw_exit = float(data.iloc[exit_index]["Close"])
        exit_reason = "SESSION_EXIT"
        exit_timestamp = data.index[exit_index]
    assert raw_exit is not None and exit_reason is not None and exit_timestamp is not None
    exit_price = raw_exit * (1.0 - slippage_rate)
    gross_pnl = (raw_exit - raw_entry) * quantity
    buy_cost = entry_price * quantity * config.buy_cost_bps / 10_000.0
    sell_cost = exit_price * quantity * config.sell_cost_bps / 10_000.0
    slippage_cost = ((entry_price - raw_entry) + (raw_exit - exit_price)) * quantity
    total_costs = buy_cost + sell_cost + slippage_cost
    net_pnl = gross_pnl - total_costs
    initial_risk = risk_per_share * quantity
    return {
        "attemptStatus": "READY",
        "primaryReason": None,
        "entryTimestamp": _iso(signal_timestamp),
        "entryDataTimestamp": _iso(data.index[entry_index]),
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
        "executedQuantity": quantity,
        "capitalDeployed": _finite(capital_deployed, 2),
        "initialRupeeRisk": _finite(initial_risk, 2),
        "exitTimestamp": _iso(exit_timestamp),
        "exitDataTimestamp": _iso(data.index[exit_index]),
        "exitBarIndex": exit_index,
        "rawExitPrice": _finite(raw_exit, 4),
        "exitPrice": _finite(exit_price, 4),
        "exitReason": exit_reason,
        "barsHeld": max(exit_index - entry_index, 0),
        "grossPnl": _finite(gross_pnl, 2),
        "buyCost": _finite(buy_cost, 2),
        "sellCost": _finite(sell_cost, 2),
        "slippageCost": _finite(slippage_cost, 2),
        "totalCosts": _finite(total_costs, 2),
        "netPnl": _finite(net_pnl, 2),
        "rMultiple": _finite(net_pnl / initial_risk, 6) if initial_risk > 0 else None,
    }


def _signal_row_passes(row: pd.Series, *, minimum_rvol: float, config: DailyWatchlistConfig) -> bool:
    close = _finite(row.get("Close"))
    vwap = _finite(row.get("SessionVWAP"))
    ema_fast = _finite(row.get("EMAFast"))
    ema_slow = _finite(row.get("EMASlow"))
    rsi = _finite(row.get("RSI"))
    rvol = _finite(row.get("RollingWindowRvol"))
    close_location = _finite(row.get("CloseLocation"))
    distance = _finite(row.get("DistanceFromVwapAtr"))
    average_value = _finite(row.get("AverageTradedValue"))
    range_atr = _finite(row.get("CandleRangeAtr"))
    return bool(
        bool(row.get("ValidOHLCV", True))
        and close is not None and vwap is not None and close > vwap
        and ema_fast is not None and ema_slow is not None and ema_fast > ema_slow
        and bool(row.get("EmaFastRising"))
        and rsi is not None and 50.0 <= rsi <= 70.0
        and rvol is not None and rvol >= minimum_rvol
        and close_location is not None and close_location >= config.minimum_close_location
        and distance is not None and distance <= config.maximum_vwap_distance_atr
        and average_value is not None and average_value >= config.minimum_average_traded_value
        and range_atr is not None and range_atr <= config.maximum_candle_range_atr
    )


def detect_opening_range_breakouts(
    symbol: str,
    features: pd.DataFrame,
    config: DailyWatchlistConfig,
    *,
    analysis_start: datetime | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    start_time = datetime_time.fromisoformat(config.opening_range_start_time)
    end_time = datetime_time.fromisoformat(config.opening_range_end_time)
    last_entry = datetime_time.fromisoformat(config.last_entry_time)
    expected_bars = int(
        (datetime.combine(date.min, end_time) - datetime.combine(date.min, start_time)).total_seconds()
        // 300
    )
    for _, session in features.groupby(features.index.date, sort=False):
        completed = session[
            (session.index.time > start_time) & (session.index.time <= end_time)
        ]
        if len(completed) != expected_bars or not completed["ValidOHLCV"].astype(bool).all():
            continue
        opening_high = float(completed["High"].max())
        opening_low = float(completed["Low"].min())
        for timestamp in session.index:
            if timestamp.time().replace(tzinfo=None) <= end_time or timestamp.time().replace(tzinfo=None) > last_entry:
                continue
            signal_index = int(features.index.get_loc(timestamp))
            if analysis_start is not None and timestamp < _as_ist(analysis_start):
                continue
            row = features.iloc[signal_index]
            previous = features.iloc[signal_index - 1] if signal_index > 0 else None
            if (
                previous is None
                or previous.name.date() != timestamp.date()
                or float(previous["Close"]) > opening_high
                or float(row["Close"]) <= opening_high
                or not _signal_row_passes(
                    row, minimum_rvol=config.opening_breakout_minimum_rvol, config=config
                )
            ):
                continue
            candidate = _candidate_base(
                symbol,
                features,
                signal_index,
                signal_type="OPENING_RANGE_BREAKOUT",
                breakout_level=opening_high,
                structural_low=opening_low,
                config=config,
            )
            candidate.update({
                "openingRangeStart": config.opening_range_start_time,
                "openingRangeEnd": config.opening_range_end_time,
                "openingRangeHigh": _finite(opening_high, 4),
                "openingRangeLow": _finite(opening_low, 4),
            })
            output.append(candidate)
            break
    return output


def detect_rolling_momentum_breakouts(
    symbol: str,
    features: pd.DataFrame,
    config: DailyWatchlistConfig,
    *,
    analysis_start: datetime | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    selection_time = datetime_time.fromisoformat(config.selection_time)
    last_entry = datetime_time.fromisoformat(config.last_entry_time)
    lookback = config.breakout_lookback_bars
    sessions = pd.Series(features.index.date, index=features.index)
    prior_high = features["High"].groupby(sessions, sort=False).rolling(
        lookback, min_periods=lookback
    ).max().reset_index(level=0, drop=True).groupby(sessions, sort=False).shift(1)
    prior_low = features["Low"].groupby(sessions, sort=False).rolling(
        lookback, min_periods=lookback
    ).min().reset_index(level=0, drop=True).groupby(sessions, sort=False).shift(1)
    for signal_index, timestamp in enumerate(features.index):
        clock = timestamp.time().replace(tzinfo=None)
        if clock <= selection_time or clock > last_entry:
            continue
        if analysis_start is not None and timestamp < _as_ist(analysis_start):
            continue
        level = _finite(prior_high.iloc[signal_index], 8)
        structural_low = _finite(prior_low.iloc[signal_index], 8)
        if level is None or structural_low is None:
            continue
        row = features.iloc[signal_index]
        if float(row["Close"]) <= level or not _signal_row_passes(
            row, minimum_rvol=config.breakout_minimum_rvol, config=config
        ):
            continue
        output.append(_candidate_base(
            symbol,
            features,
            signal_index,
            signal_type="ROLLING_MOMENTUM_BREAKOUT",
            breakout_level=level,
            structural_low=structural_low,
            config=config,
        ))
    return output


FEATURE_COLUMNS = [
    "Open", "High", "Low", "Close", "Volume", "ValidOHLCV", "RSI", "EMAFast",
    "EMASlow", "ATR", "SessionVWAP", "RVOL", "AverageTradedValue",
    "RollingWindowVolume", "RollingTradedValue", "RollingReturnPct",
    "SameTimeHistoricalMedianVolume", "RollingWindowRvol", "PriceAccelerationPct",
    "CloseLocation", "UpperWickFraction", "DistanceFromVwapAtr", "CandleRangeAtr",
    "BullishEmaTrend", "EmaFastRising", "AtrPct", "SpreadPct",
]


def _raw_cache_path(cache_directory: Path, symbol: str, duration_years: int) -> Path:
    safe_symbol = "".join(character for character in symbol if character.isalnum() or character in "-&")
    return cache_directory / f"{safe_symbol}-5-{duration_years}y.csv.gz"


def prepare_symbol_task(task: Mapping[str, Any]) -> dict[str, Any]:
    from backtest_api import prepare_candles

    symbol = str(task["symbol"])
    config: DailyWatchlistConfig = task["config"]
    source = _raw_cache_path(
        Path(str(task["cacheDirectory"])), symbol, int(task["durationYears"])
    )
    if not source.is_file():
        raise FileNotFoundError(f"Local 5-minute candle cache is unavailable for {symbol}")
    feature_parameters = {
        key: value for key, value in config.public().items()
        if key in {
            "rsi_length", "ema_fast", "ema_slow", "atr_length", "rvol_period",
            "historical_sessions", "rolling_window_minutes",
        }
    }
    feature_key = stable_fingerprint({
        "version": FEATURE_CODE_VERSION,
        "symbol": symbol,
        "source": file_stat_fingerprint(source),
        "analysisStart": _iso(task["analysisStart"]),
        "analysisEnd": _iso(task["now"]),
        "features": feature_parameters,
    })
    feature_path = Path(str(task["featureCacheDirectory"])) / symbol / f"{feature_key}.parquet"
    read_started = time.perf_counter()
    cache_hit = feature_path.is_file()
    if cache_hit:
        features = pd.read_parquet(feature_path)
        bytes_read = feature_path.stat().st_size
        indicator_seconds = 0.0
    else:
        raw = pd.read_csv(source, index_col="Timestamp", parse_dates=["Timestamp"])
        raw.index = pd.DatetimeIndex(raw.index)
        raw.index = raw.index.tz_localize(IST) if raw.index.tz is None else raw.index.tz_convert(IST)
        candles = prepare_candles(
            raw, "5m", task["analysisStart"], task["now"], warmup_bars=2_000
        )
        indicator_started = time.perf_counter()
        features = calculate_watchlist_features(candles, config)
        indicator_seconds = time.perf_counter() - indicator_started
        feature_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = feature_path.with_suffix(f".{os.getpid()}.tmp.parquet")
        features.loc[:, FEATURE_COLUMNS].to_parquet(temporary, index=True)
        os.replace(temporary, feature_path)
        bytes_read = source.stat().st_size
    read_seconds = time.perf_counter() - read_started
    detection_started = time.perf_counter()
    if bool(task.get("detectCandidates", True)):
        opening = detect_opening_range_breakouts(
            symbol, features, config, analysis_start=task["analysisStart"]
        )
        midday = detect_rolling_momentum_breakouts(
            symbol, features, config, analysis_start=task["analysisStart"]
        )
    else:
        opening, midday = [], []
    detection_seconds = time.perf_counter() - detection_started
    return {
        "symbol": symbol,
        "featurePath": str(feature_path),
        "featureCacheHit": cache_hit,
        "openingCandidates": opening,
        "middayCandidates": midday,
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


def execute_portfolio(
    candidates: Sequence[Mapping[str, Any]],
    config: DailyWatchlistConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    rejected: list[dict[str, Any]] = []
    for source in candidates:
        candidate = dict(source)
        if candidate.get("primaryReason"):
            candidate["status"] = "REJECTED"
            rejected.append(candidate)
            continue
        grouped.setdefault(_as_ist(candidate["entryTimestamp"]), []).append(candidate)
    active: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    daily_entries: dict[date, int] = {}
    daily_symbol_entries: set[tuple[date, str]] = set()
    daily_losses: dict[date, int] = {}
    daily_realized: dict[date, float] = {}
    loss_limit = config.configured_capital * config.maximum_daily_loss_pct / 100.0
    for entry_timestamp in sorted(grouped):
        remaining: list[dict[str, Any]] = []
        for trade in active:
            exit_timestamp = _as_ist(trade["exitTimestamp"])
            if exit_timestamp <= entry_timestamp:
                exit_day = exit_timestamp.date()
                pnl = float(trade["netPnl"])
                daily_realized[exit_day] = daily_realized.get(exit_day, 0.0) + pnl
                if pnl < 0:
                    daily_losses[exit_day] = daily_losses.get(exit_day, 0) + 1
            else:
                remaining.append(trade)
        active = remaining
        day = entry_timestamp.date()
        ordered = sorted(
            grouped[entry_timestamp],
            key=lambda row: (
                -float(row.get("rollingScore") or 0),
                int(row.get("rankAfterRescan") or 1_000_000),
                str(row["symbol"]),
                str(row["candidateId"]),
            ),
        )
        for candidate in ordered:
            reason: str | None = None
            symbol_day = (day, str(candidate["symbol"]))
            if daily_entries.get(day, 0) >= config.maximum_trades_per_day:
                reason = "MAX_TRADES_PER_DAY"
            elif symbol_day in daily_symbol_entries:
                reason = "SYMBOL_DAILY_TRADE_LIMIT"
            elif len(active) >= config.maximum_concurrent_trades:
                reason = "MAX_CONCURRENT_TRADES"
            elif daily_losses.get(day, 0) >= config.stop_after_daily_losses:
                reason = "DAILY_LOSS_COUNT"
            elif daily_realized.get(day, 0.0) <= -loss_limit:
                reason = "DAILY_LOSS_LIMIT"
            elif sum(float(item["capitalDeployed"]) for item in active) + float(
                candidate["capitalDeployed"]
            ) > config.configured_capital:
                reason = "CAPITAL_LIMIT"
            if reason is not None:
                candidate.update({"primaryReason": reason, "status": "REJECTED"})
                rejected.append(candidate)
                continue
            if int(candidate.get("executedQuantity") or 0) != 50:
                raise AssertionError("Daily Scalping Watchlist executed quantity must remain 50")
            sequence = len(trades) + 1
            trade = {
                **candidate,
                "tradeId": f"{STRATEGY_KEY}:{sequence}",
                "sequenceNumber": sequence,
                "strategyMode": STRATEGY_KEY,
                "status": str(candidate["exitReason"]),
                "primaryReason": "ACCEPTED",
            }
            trades.append(trade)
            active.append(trade)
            daily_entries[day] = daily_entries.get(day, 0) + 1
            daily_symbol_entries.add(symbol_day)
    return trades, rejected


def _choose_with_sector_cap(
    ranked: Sequence[Mapping[str, Any]],
    count: int,
    maximum_per_sector: int,
) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    sectors: dict[str, int] = {}
    for source in ranked:
        if not bool(source.get("eligible")):
            continue
        sector = str(source.get("sector") or f"__UNKNOWN__:{source['symbol']}")
        if sectors.get(sector, 0) >= maximum_per_sector:
            continue
        chosen.append(dict(source))
        sectors[sector] = sectors.get(sector, 0) + 1
        if len(chosen) >= count:
            break
    return chosen


def _deterministic_random_rank(rows: Sequence[Mapping[str, Any]], seed: str) -> list[dict[str, Any]]:
    return sorted(
        (dict(row) for row in rows if bool(row.get("eligible"))),
        key=lambda row: (
            hashlib.sha256(f"{seed}:{row['symbol']}".encode("utf-8")).hexdigest(),
            str(row["symbol"]),
        ),
    )


def build_watchlist_history(
    candidate_frames: Mapping[str, pd.DataFrame],
    *,
    context_frames: Mapping[str, pd.DataFrame] | None,
    nifty_frame: pd.DataFrame | None,
    sector_by_symbol: Mapping[str, str],
    config: DailyWatchlistConfig,
    minimum_average_traded_value: float,
    maximum_candle_range_atr: float,
    maximum_spread_pct: float,
    selection_method: SelectionMethod = "SCORE",
    deterministic_seed: str = "opendelta-watchlist",
) -> list[dict[str, Any]]:
    config.validate()
    if not candidate_frames:
        return []
    all_context = context_frames or candidate_frames
    session_days = sorted({stamp.date() for frame in candidate_frames.values() for stamp in frame.index})
    current: dict[str, dict[str, Any]] = {}
    history: list[dict[str, Any]] = []
    for session_day in session_days:
        current = {}
        for rescan_number, timestamp in enumerate(rescan_times_for_session(session_day, config)):
            rows = {
                symbol: row
                for symbol, frame in candidate_frames.items()
                if (row := _sample_completed(frame, timestamp)) is not None
            }
            context_rows = {
                symbol: row
                for symbol, frame in all_context.items()
                if (row := _sample_completed(frame, timestamp)) is not None
            }
            nifty_row = _sample_completed(nifty_frame, timestamp) if nifty_frame is not None else None
            ranked_context = score_rescan_rows(
                context_rows,
                nifty_row=nifty_row,
                sector_by_symbol=sector_by_symbol,
                minimum_average_traded_value=minimum_average_traded_value,
                maximum_candle_range_atr=maximum_candle_range_atr,
                maximum_spread_pct=maximum_spread_pct,
            )
            scores = {str(row["symbol"]): row for row in ranked_context if str(row["symbol"]) in rows}
            ranked = sorted(scores.values(), key=lambda row: (-float(row["score"]), str(row["symbol"])))
            before_order = sorted(current, key=lambda symbol: (-float(scores.get(symbol, current[symbol]).get("score", 0)), symbol))
            rank_before = {symbol: index + 1 for index, symbol in enumerate(before_order)}
            removed: list[dict[str, Any]] = []
            promoted: list[dict[str, Any]] = []

            if rescan_number == 0 or selection_method != "SCORE":
                if selection_method == "FULL":
                    selected = [dict(row) for row in ranked if bool(row.get("eligible"))]
                elif selection_method == "LIQUIDITY":
                    liquidity_ranked = sorted(
                        (row for row in ranked if bool(row.get("eligible"))),
                        key=lambda row: (-float(row.get("rollingTradedValue") or 0), str(row["symbol"])),
                    )
                    selected = _choose_with_sector_cap(
                        liquidity_ranked, config.selected_symbols, config.maximum_symbols_per_sector
                    )
                elif selection_method == "RANDOM":
                    selected = _choose_with_sector_cap(
                        _deterministic_random_rank(ranked, f"{deterministic_seed}:{session_day.isoformat()}"),
                        config.selected_symbols,
                        config.maximum_symbols_per_sector,
                    )
                else:
                    selected = _choose_with_sector_cap(
                        ranked, config.selected_symbols, config.maximum_symbols_per_sector
                    )
                current = {
                    str(row["symbol"]): {
                        **row,
                        "selectedAt": _iso(timestamp),
                        "promotionReason": "OPENING_SELECTION",
                    }
                    for row in selected
                }
                promoted = [dict(row) for row in current.values()]
            elif config.mode == "ROLLING":
                for symbol in list(current):
                    if symbol in scores:
                        current[symbol].update(scores[symbol])
                    else:
                        current[symbol].update({
                            "eligible": False,
                            "score": 0.0,
                            "sourceTimestamp": None,
                            "penalties": {"RESCAN_DATA_UNAVAILABLE": 100.0},
                        })
                candidates = [row for row in ranked if str(row["symbol"]) not in current and bool(row.get("eligible"))]
                replacements = 0
                while candidates and replacements < config.maximum_replacements_per_rescan:
                    current_order = sorted(current.values(), key=lambda row: (-float(row.get("score", 0)), str(row["symbol"])))
                    if len(current_order) < config.selected_symbols:
                        victim = None
                        threshold = config.minimum_promotion_score
                    else:
                        victim = current_order[-1]
                        residence = (timestamp - _as_ist(victim["selectedAt"])).total_seconds() / 60.0
                        if residence < config.minimum_residence_minutes:
                            break
                        threshold = max(
                            config.minimum_promotion_score,
                            float(victim.get("score", 0)) + config.required_promotion_advantage,
                        )
                    choice_index = None
                    for position, contender in enumerate(candidates):
                        if float(contender["score"]) < threshold:
                            continue
                        sector = contender.get("sector")
                        if sector:
                            sector_count = sum(
                                1 for row in current.values()
                                if row.get("sector") == sector and (victim is None or row["symbol"] != victim["symbol"])
                            )
                            if sector_count >= config.maximum_symbols_per_sector:
                                continue
                        choice_index = position
                        break
                    if choice_index is None:
                        break
                    contender = candidates.pop(choice_index)
                    if victim is not None:
                        old = current.pop(str(victim["symbol"]))
                        removed.append({
                            "symbol": old["symbol"],
                            "rankBefore": rank_before.get(str(old["symbol"])),
                            "score": old.get("score"),
                            "reason": "OUTRANKED_AFTER_MINIMUM_RESIDENCE",
                        })
                    contender = {
                        **contender,
                        "selectedAt": _iso(timestamp),
                        "promotionReason": (
                            "VACANT_WATCHLIST_SLOT" if victim is None
                            else f"SCORE_ADVANTAGE_{round(float(contender['score']) - float(victim.get('score', 0)), 4)}"
                        ),
                    }
                    current[str(contender["symbol"])] = contender
                    promoted.append(dict(contender))
                    replacements += 1

            after_order = sorted(current.values(), key=lambda row: (-float(row.get("score", 0)), str(row["symbol"])))
            entries: list[dict[str, Any]] = []
            promoted_symbols = {str(row["symbol"]) for row in promoted}
            for rank, row in enumerate(after_order, start=1):
                selected_at = _as_ist(row["selectedAt"])
                entries.append({
                    **row,
                    "selectionTimestamp": _iso(timestamp),
                    "rankBefore": rank_before.get(str(row["symbol"])),
                    "rankAfter": rank,
                    "tier": "PRIMARY" if rank <= config.primary_symbols else "RESERVE",
                    "action": "PROMOTED" if str(row["symbol"]) in promoted_symbols else "UNCHANGED",
                    "earliestEligibleSignalTimestamp": _iso(selected_at + pd.Timedelta(minutes=5)),
                })
            history.append({
                "sessionDate": session_day.isoformat(),
                "watchlistMode": config.mode,
                "selectionMethod": selection_method,
                "rescanTimestamp": _iso(timestamp),
                "rescanNumber": rescan_number + 1,
                "entries": entries,
                "selectedSymbols": [str(row["symbol"]) for row in entries],
                "promoted": promoted,
                "removed": removed,
                "replacements": len(removed),
                "eligibleSymbols": sum(bool(row.get("eligible")) for row in ranked),
                "evaluatedSymbols": len(ranked),
            })
            if config.mode == "FROZEN_OPEN" or selection_method != "SCORE":
                break
    return history


def select_candidates_for_history(
    opening_candidates: Sequence[Mapping[str, Any]],
    midday_candidates: Sequence[Mapping[str, Any]],
    history: Sequence[Mapping[str, Any]],
    *,
    mode: WatchlistMode,
    opening_time: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_day: dict[str, list[Mapping[str, Any]]] = {}
    for snapshot in history:
        by_day.setdefault(str(snapshot["sessionDate"]), []).append(snapshot)
    for snapshots in by_day.values():
        snapshots.sort(key=lambda item: str(item["rescanTimestamp"]))

    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    sources = [
        ("OPENING_RANGE_BREAKOUT", opening_candidates),
        ("ROLLING_MOMENTUM_BREAKOUT", midday_candidates),
    ]
    for signal_type, candidates in sources:
        for source in candidates:
            signal = _as_ist(source["signalTimestamp"])
            snapshots = by_day.get(signal.date().isoformat(), [])
            applicable = [item for item in snapshots if _as_ist(item["rescanTimestamp"]) <= signal]
            snapshot = applicable[-1] if applicable else None
            entry = next(
                (row for row in (snapshot or {}).get("entries", []) if row["symbol"] == source["symbol"]),
                None,
            )
            reason: str | None = None
            if entry is None:
                reason = "NOT_IN_WATCHLIST"
            else:
                selected_at = _as_ist(entry["selectedAt"])
                promoted_midday = selected_at.time().replace(tzinfo=None) > datetime_time.fromisoformat(opening_time)
                if signal < _as_ist(entry["earliestEligibleSignalTimestamp"]):
                    reason = "BEFORE_EARLIEST_ELIGIBLE_SIGNAL"
                elif promoted_midday and signal_type != "ROLLING_MOMENTUM_BREAKOUT":
                    reason = "MIDDAY_PROMOTION_REQUIRES_BREAKOUT"
                elif not promoted_midday and signal_type != "OPENING_RANGE_BREAKOUT":
                    reason = "OPENING_SELECTION_USES_ORB"
                elif mode == "FROZEN_OPEN" and signal_type != "OPENING_RANGE_BREAKOUT":
                    reason = "FROZEN_MODE_IGNORES_MIDDAY_BREAKOUT"
            if reason is not None:
                excluded.append({
                    "candidateId": source.get("candidateId"),
                    "symbol": source.get("symbol"),
                    "signalTimestamp": source.get("signalTimestamp"),
                    "signalType": signal_type,
                    "reason": reason,
                    "primaryReason": reason,
                    "status": "REJECTED",
                })
                continue
            assert entry is not None and snapshot is not None
            selected.append({
                **source,
                "watchlistMode": mode,
                "selectionTimestamp": entry["selectedAt"],
                "rescanTimestamp": snapshot["rescanTimestamp"],
                "rankBeforeRescan": entry.get("rankBefore"),
                "rankAfterRescan": entry.get("rankAfter"),
                "watchlistTier": entry.get("tier"),
                "promotionReason": entry.get("promotionReason"),
                "rollingScore": entry.get("score"),
                "earliestEligibleSignalTimestamp": entry.get("earliestEligibleSignalTimestamp"),
                "signalType": signal_type,
            })
    selected.sort(key=lambda row: (str(row.get("entryTimestamp") or row["signalTimestamp"]), str(row["symbol"]), str(row["candidateId"])))
    return selected, excluded


def summarize_watchlist_history(
    history: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    accepted = [item for item in candidates if not item.get("primaryReason")]
    midday = [item for item in accepted if item.get("signalType") == "ROLLING_MOMENTUM_BREAKOUT"]
    opening = [item for item in accepted if item.get("signalType") == "OPENING_RANGE_BREAKOUT"]
    return {
        "rescans": len(history),
        "replacements": sum(int(item.get("replacements", 0)) for item in history),
        "newlyPromotedSymbols": sum(
            len(item.get("promoted", [])) for item in history if int(item.get("rescanNumber", 0)) > 1
        ),
        "signalsFromOpeningSelection": len(opening),
        "signalsFromMiddayPromotions": len(midday),
    }


def trade_performance(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda row: (str(row.get("entryTimestamp")), str(row.get("symbol"))))
    pnl = [float(row.get("netPnl") or 0) for row in ordered]
    gross = sum(float(row.get("grossPnl") or 0) for row in ordered)
    costs = sum(float(row.get("totalCosts") or 0) for row in ordered)
    winners = [value for value in pnl if value > 0]
    losers = [value for value in pnl if value < 0]
    equity = np.concatenate(([0.0], np.cumsum(np.asarray(pnl, dtype=float))))
    peaks = np.maximum.accumulate(equity)
    drawdown = float(np.max(peaks - equity)) if len(equity) else 0.0
    days = {str(row.get("entryTimestamp", ""))[:10] for row in ordered}
    gross_profit = sum(winners)
    gross_loss = abs(sum(losers))
    return {
        "trades": len(ordered),
        "tradesPerDay": round(len(ordered) / len(days), 4) if days else 0.0,
        "winRate": round(len(winners) / len(ordered) * 100.0, 4) if ordered else 0.0,
        "averageWinner": round(float(np.mean(winners)), 4) if winners else None,
        "averageLoser": round(float(np.mean(losers)), 4) if losers else None,
        "grossPnl": round(gross, 2),
        "costs": round(costs, 2),
        "netPnlAfterCosts": round(sum(pnl), 2),
        "profitFactor": round(gross_profit / gross_loss, 6) if gross_loss else None,
        "expectancy": round(float(np.mean(pnl)), 4) if pnl else None,
        "maximumDrawdown": round(drawdown, 2),
        "executedQuantityInvariant": all(
            int(row.get("executedQuantity") or row.get("quantity") or 0) == 50
            for row in ordered
        ),
    }


def compare_watchlist_variant(
    *,
    history: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
    analysis_start: datetime,
    analysis_end: datetime,
    duration_years: int,
) -> dict[str, Any]:
    watchlist = summarize_watchlist_history(history, candidates)
    sessions = {str(item["sessionDate"]) for item in history}

    def in_range(row: Mapping[str, Any], start: pd.Timestamp, end: pd.Timestamp) -> bool:
        stamp = _as_ist(row["entryTimestamp"])
        return start <= stamp < end

    def period_performance(start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
        rows = [row for row in trades if in_range(row, start, end)]
        result = trade_performance(rows)
        period_sessions = {
            value for value in sessions
            if start.date() <= date.fromisoformat(value) < end.date()
        }
        traded_sessions = {str(row.get("entryTimestamp", ""))[:10] for row in rows}
        result["noTradeDays"] = max(len(period_sessions - traded_sessions), 0)
        return result

    start = _as_ist(analysis_start)
    end = _as_ist(analysis_end) + pd.Timedelta(microseconds=1)
    if duration_years <= 1:
        validation_start = end - pd.DateOffset(months=3)
        periods = [{
            "developmentFrom": _iso(start),
            "developmentTo": _iso(validation_start),
            "validationFrom": _iso(validation_start),
            "validationTo": _iso(end),
            "development": period_performance(start, validation_start),
            "validation": period_performance(validation_start, end),
        }]
    else:
        periods = []
        cursor = start
        while cursor + pd.DateOffset(months=15) <= end:
            development_end = cursor + pd.DateOffset(months=12)
            validation_end = development_end + pd.DateOffset(months=3)
            periods.append({
                "developmentFrom": _iso(cursor),
                "developmentTo": _iso(development_end),
                "validationFrom": _iso(development_end),
                "validationTo": _iso(validation_end),
                "development": period_performance(cursor, development_end),
                "validation": period_performance(development_end, validation_end),
            })
            cursor += pd.DateOffset(months=3)

    bucket_ranges = (
        ("10:00-11:00", datetime_time(10, 0), datetime_time(11, 0)),
        ("11:00-12:00", datetime_time(11, 0), datetime_time(12, 0)),
        ("12:00-13:00", datetime_time(12, 0), datetime_time(13, 0)),
        ("13:00-14:00", datetime_time(13, 0), datetime_time(14, 0)),
        ("14:00-14:45", datetime_time(14, 0), datetime_time(14, 46)),
    )
    midday = {}
    for label, bucket_start, bucket_end in bucket_ranges:
        rows = [
            row for row in trades
            if bucket_start <= _as_ist(row["entryTimestamp"]).time().replace(tzinfo=None) < bucket_end
        ]
        midday[label] = trade_performance(rows)
    overall = trade_performance(trades)
    overall["noTradeDays"] = max(len(sessions) - len({str(row.get("entryTimestamp", ""))[:10] for row in trades}), 0)
    return {
        **watchlist,
        "overall": overall,
        "chronologicalFolds": periods,
        "midday": midday,
    }


def validation_decision(comparisons: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    baseline_labels = (
        "FROZEN_OPEN_TOP_TWO",
        "FULL_ELIGIBLE_UNIVERSE",
        "LIQUIDITY_ONLY_TOP_FIVE",
        "CAUSALLY_MATCHED_RANDOM_FIVE",
    )

    def finite_metric(row: Mapping[str, Any], key: str, fallback: float) -> float:
        value = row.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return fallback
        return number if math.isfinite(number) else fallback

    def approved(label: str, additional_baselines: Sequence[str]) -> bool:
        candidate_folds = comparisons.get(label, {}).get("chronologicalFolds", [])
        baselines = tuple(dict.fromkeys((*baseline_labels, *additional_baselines)))
        if not candidate_folds:
            return False
        for index, fold in enumerate(candidate_folds):
            candidate = fold.get("validation", {})
            comparison_rows = []
            for baseline in baselines:
                folds = comparisons.get(baseline, {}).get("chronologicalFolds", [])
                if index < len(folds):
                    comparison_rows.append(folds[index].get("validation", {}))
            if (
                not comparison_rows
                or int(candidate.get("trades") or 0) < MINIMUM_UNTOUCHED_VALIDATION_TRADES
                or finite_metric(candidate, "netPnlAfterCosts", float("-inf")) <= 0
                or finite_metric(candidate, "expectancy", float("-inf")) <= 0
                or not all(
                    finite_metric(candidate, "netPnlAfterCosts", float("-inf"))
                    > finite_metric(row, "netPnlAfterCosts", float("-inf"))
                    and finite_metric(candidate, "expectancy", float("-inf"))
                    > finite_metric(row, "expectancy", float("-inf"))
                    for row in comparison_rows
                )
            ):
                return False
        return True

    frozen_approved = approved("FROZEN_OPEN_TOP_FIVE", ())
    rolling_approved = approved("ROLLING_TOP_FIVE", ("FROZEN_OPEN_TOP_FIVE",))
    return {
        "frozenApproved": frozen_approved,
        "rollingApproved": rolling_approved,
        "liveOrdersEnabled": False,
        "minimumTradesPerUntouchedFold": MINIMUM_UNTOUCHED_VALIDATION_TRADES,
        "status": "VALIDATED_RESEARCH_CANDIDATE" if frozen_approved or rolling_approved else "REJECTED_RESEARCH_ONLY",
        "reason": (
            "At least one selector passed every untouched fold with positive net P&L and expectancy after costs while outperforming its baselines; live orders remain disabled."
            if frozen_approved or rolling_approved
            else f"Neither selector had at least {MINIMUM_UNTOUCHED_VALIDATION_TRADES} trades in every untouched fold with positive after-cost expectancy and baseline outperformance."
        ),
    }
