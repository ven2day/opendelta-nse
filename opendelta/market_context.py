from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .core import UNSUPPORTED_DATA_REQUIREMENT
from .market_data import file_data_version, freshness


class MarketContextService:
    def __init__(self, market_data_file: Path, stale_seconds: int = 86_400) -> None:
        self.market_data_file = market_data_file
        self.stale_seconds = stale_seconds

    @staticmethod
    def session(market: str, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(UTC)
        if market == "CRYPTO":
            return {
                "status": "OPEN_24_7",
                "timezone": "UTC",
                "session": "WEEKEND" if current.weekday() >= 5 else "WEEKDAY",
            }
        local = current.astimezone(ZoneInfo("Asia/Kolkata"))
        market_open = time(9, 15) <= local.time() <= time(15, 30)
        return {
            "status": "OPEN" if local.weekday() < 5 and market_open else "CLOSED",
            "timezone": "Asia/Kolkata",
            "session": "REGULAR",
            "scheduleBasis": "WEEKDAY_CLOCK_ONLY",
        }

    def snapshot(self, market: str) -> dict[str, Any]:
        if market == "CRYPTO":
            return {
                "market": market,
                "session": self.session(market),
                "benchmarkDirection": {"status": UNSUPPORTED_DATA_REQUIREMENT},
                "sectorDirection": {"status": UNSUPPORTED_DATA_REQUIREMENT},
                "breadth": {"status": UNSUPPORTED_DATA_REQUIREMENT},
                "relativeStrength": {"status": UNSUPPORTED_DATA_REQUIREMENT},
                "regime": {"status": "PROVIDER_INSTRUMENT_REQUIRED"},
            }
        quality = freshness(self.market_data_file, self.stale_seconds, market="NSE")
        if not self.market_data_file.exists():
            return {
                "market": market,
                "session": self.session(market),
                "data": quality,
                "breadth": {"status": "UNAVAILABLE"},
                "benchmarkDirection": {"status": UNSUPPORTED_DATA_REQUIREMENT},
                "sectorDirection": {"status": UNSUPPORTED_DATA_REQUIREMENT},
            }
        frame = pd.read_csv(self.market_data_file)
        normalized = {str(column).strip().casefold(): column for column in frame.columns}
        previous_name = normalized.get("previous_close") or normalized.get("yesterday price")
        current_name = normalized.get("entry_price") or normalized.get("current close") or normalized.get("close")
        breadth: dict[str, Any] = {"status": UNSUPPORTED_DATA_REQUIREMENT}
        if previous_name and current_name:
            previous = pd.to_numeric(frame[previous_name], errors="coerce")
            current = pd.to_numeric(frame[current_name], errors="coerce")
            valid = previous.notna() & current.notna() & previous.ne(0)
            advancing = int((current.loc[valid] > previous.loc[valid]).sum())
            declining = int((current.loc[valid] < previous.loc[valid]).sum())
            breadth = {
                "status": "SUPPORTED",
                "symbols": int(valid.sum()),
                "advancing": advancing,
                "declining": declining,
                "unchanged": int(valid.sum()) - advancing - declining,
                "advancePct": advancing / int(valid.sum()) * 100 if valid.any() else 0.0,
            }
        return {
            "market": market,
            "session": self.session(market),
            "data": {**quality, "dataVersion": file_data_version(self.market_data_file)},
            "breadth": breadth,
            "benchmarkDirection": {"status": UNSUPPORTED_DATA_REQUIREMENT, "reason": "POINT_IN_TIME_NIFTY_CONTEXT_NOT_IN_SNAPSHOT"},
            "sectorDirection": {"status": UNSUPPORTED_DATA_REQUIREMENT, "reason": "AUDITED_SECTOR_CONTEXT_NOT_IN_SNAPSHOT"},
            "relativeStrength": {"status": UNSUPPORTED_DATA_REQUIREMENT, "reason": "ALIGNED_BENCHMARK_REQUIRED"},
            "regime": {"status": "INSTRUMENT_TIMEFRAME_REQUIRED"},
        }

    @staticmethod
    def relative_return(close: pd.Series, benchmark_close: pd.Series | None, periods: int) -> dict[str, Any]:
        if benchmark_close is None:
            return {"status": UNSUPPORTED_DATA_REQUIREMENT, "reason": "BENCHMARK_MISSING"}
        if len(close) != len(benchmark_close):
            return {"status": UNSUPPORTED_DATA_REQUIREMENT, "reason": "BENCHMARK_ALIGNMENT_INVALID"}
        values = close.astype(float).pct_change(periods) - benchmark_close.astype(float).pct_change(periods)
        return {"status": "SUPPORTED", "values": values}
