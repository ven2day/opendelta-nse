"""The one indicator library.

Every function here is causal: a value at bar *t* depends only on bars <= *t*.
Two smoothing conventions exist in the codebase and both are kept, explicitly
named, because switching one for the other changes historical results:

- ``rma``: plain exponential smoothing with alpha = 1/length (what the Strong
  Buy ADX/DMI and the crypto engine use).
- ``wilder_rma``: Pine ``ta.rma``-exact smoothing seeded with the first SMA
  (what the RSI recovery engine uses).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _numeric(values: pd.Series) -> pd.Series:
    return pd.to_numeric(values, errors="coerce").astype(float)


def _check_length(length: int, label: str) -> None:
    if length <= 0:
        raise ValueError(f"{label} length must be greater than 0")


def ema(values: pd.Series, length: int) -> pd.Series:
    """Causal EMA using the ``ta.ema`` recurrence (alpha = 2 / (length + 1))."""
    _check_length(length, "EMA")
    return _numeric(values).ewm(span=length, adjust=False, min_periods=length).mean()


def rma(values: pd.Series, length: int) -> pd.Series:
    """Exponential smoothing with alpha = 1 / length, unseeded."""
    _check_length(length, "RMA")
    return _numeric(values).ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def wilder_rma(values: pd.Series, length: int) -> pd.Series:
    """Wilder RMA seeded with the first length-value SMA, matching Pine ``ta.rma``."""
    _check_length(length, "RMA")
    numeric = _numeric(values)
    result = pd.Series(np.nan, index=numeric.index, dtype=float)
    valid_positions = np.flatnonzero(numeric.notna().to_numpy())
    if len(valid_positions) < length:
        return result
    seed_position = int(valid_positions[length - 1])
    seed = float(numeric.iloc[valid_positions[:length]].mean())
    seeded = pd.Series(np.nan, index=numeric.index, dtype=float)
    seeded.iloc[seed_position] = seed
    if seed_position + 1 < len(numeric):
        seeded.iloc[seed_position + 1 :] = numeric.iloc[seed_position + 1 :]
    return seeded.ewm(alpha=1.0 / length, adjust=False, ignore_na=True).mean()


def wilder_rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Pine-compatible ``ta.rsi``: close changes smoothed with the seeded Wilder RMA."""
    _check_length(length, "RSI")
    numeric = _numeric(close)
    change = numeric.diff()
    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)
    average_gain = wilder_rma(gains, length)
    average_loss = wilder_rma(losses, length)
    relative_strength = average_gain / average_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + relative_strength))
    rsi = rsi.mask(average_loss.eq(0), 100.0)
    rsi = rsi.mask(average_gain.eq(0) & average_loss.ne(0), 0.0)
    return rsi


def true_range(candles: pd.DataFrame) -> pd.Series:
    high = _numeric(candles["High"])
    low = _numeric(candles["Low"])
    close = _numeric(candles["Close"])
    previous_close = close.shift(1)
    return pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(candles: pd.DataFrame, length: int, *, seeded: bool) -> pd.Series:
    """Average true range; ``seeded=True`` is Wilder/Pine exact, ``False`` is plain RMA."""
    _check_length(length, "ATR")
    smoother = wilder_rma if seeded else rma
    return smoother(true_range(candles), length)


def session_vwap(candles: pd.DataFrame, timezone: str) -> pd.Series:
    """Volume-weighted average price, reset at every calendar day in ``timezone``."""
    if candles.empty:
        return pd.Series(index=candles.index, dtype=float)
    if not isinstance(candles.index, pd.DatetimeIndex):
        raise ValueError("VWAP requires a DatetimeIndex")
    index = candles.index.tz_localize(timezone) if candles.index.tz is None else candles.index.tz_convert(timezone)
    session = pd.Series(index.date, index=candles.index)
    typical = (candles["High"] + candles["Low"] + candles["Close"]) / 3
    cumulative_weighted = (typical * candles["Volume"]).groupby(session).cumsum()
    cumulative_volume = candles["Volume"].groupby(session).cumsum().replace(0, np.nan)
    return cumulative_weighted / cumulative_volume


def directional_movement(candles: pd.DataFrame, length: int, smoothing: int) -> pd.DataFrame:
    """Wilder DMI/ADX using unseeded RMA smoothing (``PlusDi``, ``MinusDi``, ``Adx``)."""
    _check_length(length, "DMI")
    _check_length(smoothing, "ADX smoothing")
    high = _numeric(candles["High"])
    low = _numeric(candles["Low"])
    up, down = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=candles.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=candles.index)
    average_range = rma(true_range(candles), length)
    plus_di = 100 * rma(plus_dm, length) / average_range.replace(0, np.nan)
    minus_di = 100 * rma(minus_dm, length) / average_range.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({"PlusDi": plus_di, "MinusDi": minus_di, "Adx": rma(dx, smoothing)}, index=candles.index)


def relative_volume(volume: pd.Series, length: int) -> pd.Series:
    """Volume divided by its trailing simple average (the current bar included)."""
    _check_length(length, "RVOL")
    numeric = _numeric(volume)
    return numeric / numeric.rolling(length, min_periods=length).mean().replace(0, np.nan)


def higher_timeframe_ema_alignment(close: pd.Series, minutes: int, fast: int, slow: int) -> pd.Series:
    """True where the last *completed* ``minutes``-bar fast EMA is above the slow EMA.

    Only higher-timeframe bars that closed strictly before the current bar are
    consulted, so a still-forming higher-timeframe bar can never influence the
    result.
    """
    _check_length(minutes, "Higher timeframe")
    completed = close.resample(f"{minutes}min", closed="right", label="right", origin="start_day").last().dropna()
    higher = pd.DataFrame(
        {"Fast": completed.ewm(span=fast, adjust=False, min_periods=fast).mean(), "Slow": completed.ewm(span=slow, adjust=False, min_periods=slow).mean()}
    ).dropna()
    if higher.empty:
        return pd.Series(False, index=close.index, dtype=bool)
    left = pd.DataFrame({"timestamp": close.index})
    right = higher.reset_index().rename(columns={higher.index.name or "index": "completedAt"})
    aligned = pd.merge_asof(left, right, left_on="timestamp", right_on="completedAt", direction="backward", allow_exact_matches=False)
    return pd.Series((aligned["Fast"] > aligned["Slow"]).fillna(False).to_numpy(), index=close.index, dtype=bool)
