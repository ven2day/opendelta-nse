"""Bounded memory on real data: a wide backtest over cached one-year NSE candles (skipped where the cache is absent)."""

from __future__ import annotations

import gzip
import threading
import tracemalloc
import unittest
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from backend.backtest import BacktestEngine, BacktestRequest, ExecutionSettings, MemoryResultWriter
from backend.markets.base import market_spec
from backend.strategies import STRATEGIES

CANDLE_CACHE = Path("/var/lib/opendelta/backtest")
FILES = sorted(CANDLE_CACHE.glob("*-5-1y.csv.gz")) if CANDLE_CACHE.exists() else []


class CachedCandleSource:
    """Reads the local gzip candle cache only; never touches a provider."""

    def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime, *, warmup_bars: int) -> pd.DataFrame:
        with gzip.open(CANDLE_CACHE / f"{symbol}-5-1y.csv.gz") as fh:
            return pd.read_csv(fh, parse_dates=["Timestamp"]).set_index("Timestamp")


@unittest.skipUnless(len(FILES) >= 100, "fewer than 100 cached one-year candle files available")
class RealCacheBacktestMemoryTests(unittest.TestCase):
    def _run(self, count: int) -> tuple[int, MemoryResultWriter]:
        symbols = [path.name.split("-5-1y")[0] for path in FILES[:count]]
        writer = MemoryResultWriter(keep_trades=False)
        engine = BacktestEngine(strategy=STRATEGIES.get("ema_vwap_strong_buy"), market=market_spec("NSE"), source=CachedCandleSource(), writer=writer, cancel_event=threading.Event())
        request = BacktestRequest(run_id="real-cache", market="NSE", strategy_id="ema_vwap_strong_buy", symbols=symbols, timeframe="5m", start_date=date(2025, 9, 1), end_date=date(2026, 9, 2), execution=ExecutionSettings(batch_size=500))
        tracemalloc.start()
        result = engine.run(request)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertEqual(result["status"], "COMPLETE", result)
        return peak, writer

    def test_one_year_backtest_over_one_hundred_real_symbols_stays_within_a_fixed_budget(self) -> None:
        peak_20, _ = self._run(20)
        peak_100, writer = self._run(100)
        self.assertGreater(writer.trade_count, 1_000)
        self.assertGreaterEqual(len(writer.batches), 100)  # written incrementally: at least one flush per symbol
        self.assertLess(peak_100, 256 * 1024 * 1024, f"peak {peak_100 / 1e6:.0f} MB")
        self.assertLess(peak_100, peak_20 * 1.5, f"peak grew from {peak_20 / 1e6:.0f} MB to {peak_100 / 1e6:.0f} MB across 5x more symbols")
