"""Adapts the existing public-exchange CryptoMarketService to the CandleSource contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, Sequence

import pandas as pd

from backend.core.models import CANDLE_COLUMNS, COMPLETE_COLUMN


class _CryptoService(Protocol):
    def list_instruments(self) -> Sequence[Any]: ...

    def sync_candles(self, instrument: Any, timeframe: str, start: datetime, end: datetime) -> Sequence[Any]: ...


class CryptoCandleSource:
    def __init__(self, service: _CryptoService) -> None:
        self.service = service

    def resolve_instrument(self, symbol: str) -> Any:
        wanted = symbol.strip().upper()
        matches = [
            instrument
            for instrument in self.service.list_instruments()
            if wanted in {instrument.instrument_id.upper(), instrument.provider_symbol.upper(), instrument.display_symbol.upper()}
        ]
        if len(matches) != 1:
            raise ValueError(f"{symbol} is not an exactly configured crypto instrument")
        return matches[0]

    def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime, *, warmup_bars: int) -> pd.DataFrame:
        instrument = self.resolve_instrument(symbol)
        rows = self.service.sync_candles(instrument, timeframe, start, end)
        if not rows:
            return pd.DataFrame(columns=[*CANDLE_COLUMNS, COMPLETE_COLUMN])
        frame = pd.DataFrame(
            [
                {"Open": item.open, "High": item.high, "Low": item.low, "Close": item.close, "Volume": item.base_volume, COMPLETE_COLUMN: bool(item.complete)}
                for item in rows
            ],
            index=pd.DatetimeIndex([item.open_time for item in rows]),
        )
        frame.index = frame.index.tz_localize("UTC") if frame.index.tz is None else frame.index.tz_convert("UTC")
        return frame[~frame.index.duplicated(keep="last")].sort_index()
