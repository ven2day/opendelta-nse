from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from time import perf_counter
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.collector import IST  # noqa: E402
from backend.compat.recovery_backtest import STRATEGY_VERSION, RecoveryConfig, simulate_recovery_symbol  # noqa: E402

_WORKER_FRAME: pd.DataFrame | None = None
_WORKER_CONFIG = RecoveryConfig()


def make_one_year_five_minute_fixture() -> pd.DataFrame:
    sessions = [
        pd.date_range(
            pd.Timestamp(day.date(), tz=IST) + pd.Timedelta(hours=9, minutes=20),
            periods=75,
            freq="5min",
        )
        for day in pd.bdate_range("2025-08-25", periods=252)
    ]
    index = sessions[0].append(sessions[1:])
    random = np.random.default_rng(42)
    returns = random.normal(0, 0.0015, len(index))
    close = 100.0 * np.exp(np.cumsum(returns))
    open_values = np.concatenate(([close[0]], close[:-1]))
    spread = np.maximum(np.abs(close - open_values), 0.05) + random.random(len(index)) * 0.12
    return pd.DataFrame(
        {
            "Open": open_values,
            "High": np.maximum(open_values, close) + spread,
            "Low": np.minimum(open_values, close) - spread,
            "Close": close,
            "Volume": random.integers(1_000, 50_000, len(index)),
        },
        index=index,
    )


def _initialize_worker(frame: pd.DataFrame, config: RecoveryConfig) -> None:
    global _WORKER_FRAME, _WORKER_CONFIG
    _WORKER_FRAME = frame
    _WORKER_CONFIG = config


def _run_symbol(position: int) -> tuple[int, int, int, int]:
    assert _WORKER_FRAME is not None
    result = simulate_recovery_symbol(
        f"SYNTH{position:04d}",
        _WORKER_FRAME,
        timeframe="5m",
        config=_WORKER_CONFIG,
        run_id="performance-benchmark",
    )
    return (
        result["buySignals"],
        result["targetsHit"],
        result["openSignals"],
        result["maximumConcurrentOpenSignals"],
    )


def peak_memory_mb() -> float | None:
    if sys.platform != "win32":
        try:
            import resource

            value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            return value / (1024.0 if sys.platform != "darwin" else 1024.0 * 1024.0)
        except (ImportError, AttributeError):
            pass
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(counters),
            counters.cb,
        )
        peak = counters.PeakWorkingSetSize / (1024.0 * 1024.0)
        return peak if peak > 0 else None
    except (AttributeError, OSError):
        return None


def run_size(frame: pd.DataFrame, symbols: int, workers: int) -> dict[str, Any]:
    started = perf_counter()
    if workers == 1:
        _initialize_worker(frame, RecoveryConfig())
        outcomes = [_run_symbol(position) for position in range(symbols)]
    else:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_initialize_worker,
            initargs=(frame, RecoveryConfig()),
        ) as executor:
            outcomes = list(executor.map(_run_symbol, range(symbols), chunksize=max(1, symbols // (workers * 8))))
    runtime = perf_counter() - started
    return {
        "symbols": symbols,
        "timeframe": "5m",
        "duration": "1y / 252 sessions",
        "candleRowsPerSymbol": len(frame),
        "candleRowsProcessed": len(frame) * symbols,
        "runtimeSeconds": round(runtime, 4),
        "symbolsPerSecond": round(symbols / runtime, 4),
        "workerCount": workers,
        "strategyVersion": STRATEGY_VERSION,
        "observationSemantics": "Every fresh RSI arm/recovery cycle is independent; only currently open observations are scanned.",
        "peakParentMemoryMb": round(peak_memory_mb(), 2) if peak_memory_mb() is not None else None,
        "buySignals": sum(item[0] for item in outcomes),
        "targetsHit": sum(item[1] for item in outcomes),
        "openSignals": sum(item[2] for item in outcomes),
        "maximumConcurrentSameSymbol": max((item[3] for item in outcomes), default=0),
        "dataSource": "deterministic synthetic OHLCV; Dhan network/cache time excluded",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the OpenDelta RSI Recovery engine.")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--sizes", default="1,100,300,750")
    arguments = parser.parse_args()
    sizes = [int(value) for value in arguments.sizes.split(",") if value.strip()]
    workers = max(1, min(arguments.workers, 16))
    frame = make_one_year_five_minute_fixture()
    print(json.dumps({"benchmarks": [run_size(frame, size, workers) for size in sizes]}, indent=2))


if __name__ == "__main__":
    main()
