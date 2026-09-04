"""Dhan-backed historical candle store and candle preparation.

``HistoricalDataStore`` fetches symbols from Dhan, keeps a bounded local CSV
cache, and prepares completed OHLCV candles in the shape the shared engines
consume. It was extracted from the legacy ``backend/app.py`` god-file because
the unified platform's NSE ``CandleSource`` still reads through it.
"""

from __future__ import annotations

import io
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from backend.collector import (
    IST,
    RSI_PERIOD,
    DhanAPIError,
    DhanClient,
    DhanConfig,
    calculate_rsi,
    download_instrument_master,
    historical_payload_to_frame,
    load_symbols,
)
from backend.data.timescale import CanonicalCandleWriter, canonical_candles_from_dhan_frame

MARKET_OPEN = datetime_time(9, 15)
MARKET_CLOSE = datetime_time(15, 30)
CACHE_TTL_SECONDS = 60 * 60
INTRADAY_CHUNK_DAYS = 89


@dataclass(frozen=True)
class TimeframeSpec:
    source: Literal["daily", "intraday"]
    source_interval: str | None
    minutes: int | None
    resample_minutes: int | None = None


TIMEFRAMES: dict[str, TimeframeSpec] = {
    "1m": TimeframeSpec("intraday", "1", 1),
    "5m": TimeframeSpec("intraday", "5", 5),
    "15m": TimeframeSpec("intraday", "15", 15),
    "30m": TimeframeSpec("intraday", "15", 15, 30),
    "1h": TimeframeSpec("intraday", "60", 60),
    "2h": TimeframeSpec("intraday", "60", 60, 120),
    "4h": TimeframeSpec("intraday", "60", 60, 240),
    "1d": TimeframeSpec("daily", None, None),
}


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


class HistoricalDataStore:
    scanner_process_pool_enabled = True

    def __init__(
        self,
        config: DhanConfig,
        cache_directory: Path,
        canonical_writer: CanonicalCandleWriter | None = None,
    ) -> None:
        self.config = config
        self.client = DhanClient(config)
        self.cache_directory = cache_directory
        self.canonical_writer = canonical_writer
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

    def _cache_is_fresh(self, path: Path, now: datetime | None = None) -> bool:
        """Whether a cached candle file is fresh enough to skip a live Dhan fetch.

        During market hours this is the short CACHE_TTL_SECONDS wall-clock
        window, same as before. Outside market hours (evenings, nights,
        weekends) nothing new can exist from Dhan until the next session
        opens regardless of how many clock-hours have passed, so a cache
        written at or after the most recently completed session's close is
        already maximally fresh. Without this, every backtest run outside
        market hours forced a full live re-fetch of every symbol - hundreds
        of avoidable requests serialized behind Dhan's shared rate limit.
        """
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=IST)
        except OSError:
            return False
        now = now if now is not None else datetime.now(IST)
        if now.weekday() < 5 and MARKET_OPEN <= now.time() <= MARKET_CLOSE:
            return (now - mtime).total_seconds() <= CACHE_TTL_SECONDS
        session_date = now.date()
        while True:
            session_close = datetime.combine(session_date, MARKET_CLOSE, tzinfo=IST)
            if session_date.weekday() < 5 and session_close <= now:
                return mtime >= session_close
            session_date -= timedelta(days=1)

    def _read_cache(self, path: Path) -> pd.DataFrame | None:
        try:
            if not self._cache_is_fresh(path):
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
                security_id = self.security_id(symbol)
                raw = self._fetch_raw(security_id, spec, fetch_start, now_ist)
            if not raw.empty:
                self._write_cache(cache_path, raw)
                if self.canonical_writer is not None and spec.source == "intraday":
                    source_timeframe = {
                        "1": "1m",
                        "5": "5m",
                        "15": "15m",
                        "60": "1h",
                    }.get(str(spec.source_interval))
                    if source_timeframe is not None:
                        canonical_instrument = (
                            self._nifty_security_id if benchmark else security_id
                        )
                        assert canonical_instrument is not None
                        self.canonical_writer.write(
                            canonical_candles_from_dhan_frame(
                                raw,
                                instrument_id=canonical_instrument,
                                symbol=cache_symbol,
                                timeframe=source_timeframe,
                                completed_before=now_ist,
                            )
                        )
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

    def cached_candle_path(
        self,
        symbol: str,
        timeframe: str,
        duration_years: int,
        *,
        benchmark: bool = False,
    ) -> Path:
        """Return the canonical local candle-cache path without reading or fetching it."""
        spec = TIMEFRAMES[timeframe]
        source_key = spec.source_interval or "daily"
        cache_symbol = "NIFTY50" if benchmark else symbol
        return self._cache_path(cache_symbol, source_key, duration_years)