from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import backtest_api
from backtest_api import HistoricalDataStore
from main import IST
from stock_scanner import StockScannerService, build_stock_scanner_snapshot


def _frame(symbol_number: int, future_score: bool = False) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            "2026-08-28T09:30:00+05:30",
            "2026-08-28T09:45:00+05:30",
            "2026-08-28T10:00:00+05:30",
        ]
    )
    base = float(symbol_number)
    return pd.DataFrame(
        {
            "Open": [200.0, 201.0, 202.0],
            "High": [202.0, 203.0, 204.0],
            "Low": [199.0, 200.0, 201.0],
            "Close": [201.0, 202.0, 203.0],
            "Volume": [100_000.0, 110_000.0, 120_000.0],
            "ValidOHLCV": [True, True, True],
            "RSI": [52.0, 55.0, 58.0],
            "EMAFast": [200.0, 201.0, 202.0],
            "EMASlow": [198.0, 199.0, 200.0],
            "ATR": [2.0, 2.0, 2.0],
            "SessionVWAP": [199.0, 200.0, 201.0],
            "RVOL": [1.2, 1.3, 1.4],
            "AverageTradedValue": [5_000_000.0] * 3,
            "RollingWindowVolume": [250_000.0, 300_000.0, 350_000.0],
            "RollingTradedValue": [50_000_000.0 + base * 1_000_000.0] * 3,
            "RollingReturnPct": [base * 0.1, base * 0.12, 10.0 if future_score else base * 0.14],
            "SameTimeHistoricalMedianVolume": [100_000.0] * 3,
            "RollingWindowRvol": [1.0 + base * 0.1, 1.0 + base * 0.12, 20.0 if future_score else 1.0 + base * 0.14],
            "PriceAccelerationPct": [0.1, 0.1, 0.1],
            "CloseLocation": [0.7] * 3,
            "UpperWickFraction": [0.1] * 3,
            "DistanceFromVwapAtr": [0.5] * 3,
            "CandleRangeAtr": [1.0] * 3,
            "BullishEmaTrend": [True] * 3,
            "EmaFastRising": [True] * 3,
            "AtrPct": [1.0] * 3,
            "MedianDailyTradedValue": [200_000_000.0] * 3,
            "DailyAtrPct": [1.5] * 3,
            "OpeningGapPct": [0.5] * 3,
            "OpeningTradedValue": [5_000_000.0] * 3,
            "SpreadPct": [float("nan")] * 3,
        },
        index=index,
    )


def _nifty() -> pd.DataFrame:
    frame = _frame(1)
    frame["RollingReturnPct"] = [0.1, 0.2, 0.3]
    return frame


def _snapshot(now: str, frames: dict[str, pd.DataFrame] | None = None) -> dict:
    candidate_frames = frames or {f"S{number}": _frame(number) for number in range(1, 7)}
    return build_stock_scanner_snapshot(
        candidate_frames,
        nifty_frame=_nifty(),
        sector_by_symbol={symbol: symbol for symbol in candidate_frames},
        company_names={symbol: f"Company {symbol}" for symbol in candidate_frames},
        now_ist=datetime.fromisoformat(now),
        minimum_price=100,
        maximum_price=3_000,
    )


def test_scanner_uses_15_minute_rescans_and_top_two_primary() -> None:
    result = _snapshot("2026-08-28T09:47:00+05:30")
    assert result["metadata"]["lastRescanTimestamp"] == "2026-08-28T09:45:00+05:30"
    assert result["metadata"]["rescanIntervalMinutes"] == 15
    assert len(result["watchlist"]["topFive"]) == 5
    assert len(result["watchlist"]["primary"]) == 2
    assert len(result["watchlist"]["reserve"]) == 3
    assert len(result["opportunities"]) == 6


def test_future_completed_rows_cannot_change_an_earlier_rescan() -> None:
    baseline = _snapshot("2026-08-28T09:47:00+05:30")
    frames = {f"S{number}": _frame(number, future_score=number == 1) for number in range(1, 7)}
    changed_future = _snapshot("2026-08-28T09:47:00+05:30", frames)
    assert baseline["opportunities"] == changed_future["opportunities"]
    assert baseline["watchlist"]["topFive"] == changed_future["watchlist"]["topFive"]


def test_global_price_minimum_rejects_low_priced_idea() -> None:
    frames = {f"S{number}": _frame(number) for number in range(1, 6)}
    idea = _frame(10)
    idea["Close"] = 14.0
    frames["IDEA"] = idea
    result = _snapshot("2026-08-28T09:47:00+05:30", frames)
    assert "IDEA" not in {row["symbol"] for row in result["opportunities"]}
    assert result["eligibility"]["rejectionCounts"]["PRICE_BELOW_MINIMUM"] == 1


def test_minimum_residence_prevents_first_rescan_churn() -> None:
    frames = {f"S{number}": _frame(number) for number in range(1, 7)}
    frames["S1"].loc[pd.Timestamp("2026-08-28T09:45:00+05:30"), "RollingWindowRvol"] = 100.0
    result = _snapshot("2026-08-28T09:47:00+05:30", frames)
    assert result["watchlist"]["history"][1]["replacements"] == 0


def test_scanner_is_paper_only_and_keeps_signal_universe_frozen() -> None:
    result = _snapshot("2026-08-28T09:47:00+05:30")
    assert result["metadata"]["paperOnly"] is True
    assert result["metadata"]["liveOrdersEnabled"] is False
    assert result["metadata"]["signalUniversePolicy"] == "FROZEN_AT_09_30"


class _CachedStore:
    def __init__(self) -> None:
        self.cached_calls = 0

    def cached_candles(self, symbol, *args, benchmark=False, **kwargs):
        self.cached_calls += 1
        return _nifty() if benchmark else _frame(int(str(symbol).removeprefix("S")))

    def candles(self, *args, **kwargs):  # pragma: no cover - safety tripwire
        raise AssertionError("Stock Scanner must not fetch Dhan data")


class _PersistentCachedStore(_CachedStore):
    def __init__(self, cache_directory: Path) -> None:
        super().__init__()
        self.cache_directory = cache_directory
        for symbol in [*(f"S{number}" for number in range(1, 7)), "NIFTY50"]:
            self.cached_candle_path(symbol, "5m", 1).touch()

    def cached_candle_path(self, symbol, timeframe, duration_years, *, benchmark=False):
        cache_symbol = "NIFTY50" if benchmark else symbol
        return self.cache_directory / f"{cache_symbol}-5-{duration_years}y.csv.gz"


def test_service_reads_local_cache_only_and_caches_repeated_snapshot() -> None:
    store = _CachedStore()
    service = StockScannerService(store, cache_seconds=60)
    now = datetime(2026, 8, 28, 9, 47, tzinfo=IST)
    with patch("stock_scanner.calculate_watchlist_features", side_effect=lambda frame, config: frame):
        first = service.snapshot(
            [f"S{number}" for number in range(1, 7)],
            minimum_price=100,
            maximum_price=3_000,
            sector_by_symbol={f"S{number}": f"SECTOR-{number}" for number in range(1, 7)},
            company_names={},
            now_ist=now,
        )
        calls_after_first = store.cached_calls
        second = service.snapshot(
            [f"S{number}" for number in range(1, 7)],
            minimum_price=100,
            maximum_price=3_000,
            sector_by_symbol={f"S{number}": f"SECTOR-{number}" for number in range(1, 7)},
            company_names={},
            now_ist=now,
        )
    assert first["metadata"]["resultSource"] == "FRESH_CALCULATION"
    assert second["metadata"]["resultSource"] == "SCANNER_CACHE"
    assert store.cached_calls == calls_after_first


def test_service_reuses_source_fingerprinted_feature_cache_across_instances(tmp_path: Path) -> None:
    store = _PersistentCachedStore(tmp_path)
    now = datetime(2026, 8, 28, 9, 47, tzinfo=IST)
    arguments = {
        "minimum_price": 100,
        "maximum_price": 3_000,
        "sector_by_symbol": {f"S{number}": f"SECTOR-{number}" for number in range(1, 7)},
        "company_names": {},
        "now_ist": now,
    }
    with patch("stock_scanner.calculate_watchlist_features", side_effect=lambda frame, config: frame) as calculate:
        first = StockScannerService(store).snapshot(
            [f"S{number}" for number in range(1, 7)], **arguments
        )
        first_calculations = calculate.call_count
        second = StockScannerService(store).snapshot(
            [f"S{number}" for number in range(1, 7)], **arguments
        )

    assert first_calculations == 7
    assert calculate.call_count == first_calculations
    assert first["metadata"]["featureCacheMisses"] == 7
    assert second["metadata"]["featureCacheHits"] == 7
    assert second["metadata"]["featureCacheMisses"] == 0
    assert first["watchlist"] == second["watchlist"]


def test_backend_registers_stock_scanner_route() -> None:
    routes = {(getattr(route, "path", None), tuple(getattr(route, "methods", []))) for route in backtest_api.app.routes}
    assert any(path == "/stock-scanner" and "GET" in methods for path, methods in routes)


def test_local_scanner_reader_accepts_an_old_completed_cache(tmp_path: Path) -> None:
    store = object.__new__(HistoricalDataStore)
    store.cache_directory = tmp_path
    path = tmp_path / "TEST-5-1y.csv.gz"
    candles = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [10_000.0, 11_000.0],
        },
        index=pd.DatetimeIndex([
            "2026-08-28T09:15:00+05:30",
            "2026-08-28T09:20:00+05:30",
        ], name="Timestamp"),
    )
    candles.to_csv(path, compression="gzip")
    old_time = datetime(2026, 8, 28, 12, 0, tzinfo=IST).timestamp()
    path.touch()
    os.utime(path, (old_time, old_time))

    result = store.cached_candles(
        "TEST",
        "5m",
        1,
        datetime(2026, 8, 28, 9, 0, tzinfo=IST),
        datetime(2026, 8, 30, 12, 0, tzinfo=IST),
    )
    assert len(result) == 2
    assert result.index[-1] == pd.Timestamp("2026-08-28T09:25:00+05:30")
