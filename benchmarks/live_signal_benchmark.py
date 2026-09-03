from __future__ import annotations

import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.compat.live_signals import (
    IST,
    DhanQuoteTick,
    FiveMinuteCandleBuilder,
    evaluate_latest_recovery,
)
from backend.compat.recovery_backtest import RecoveryConfig

SYMBOLS = 300
BARS = 200


def fixture(seed: int) -> pd.DataFrame:
    random = np.random.default_rng(seed)
    sessions = []
    for day in pd.bdate_range("2026-08-12", periods=10):
        sessions.extend(pd.date_range(f"{day.date()} 09:20", periods=20, freq="5min", tz=IST))
    close = 1_000 + np.cumsum(random.normal(0, 1.2, BARS))
    open_price = np.r_[close[0], close[:-1]]
    high = np.maximum(open_price, close) + random.uniform(0.1, 1.0, BARS)
    low = np.minimum(open_price, close) - random.uniform(0.1, 1.0, BARS)
    return pd.DataFrame(
        {"Open": open_price, "High": high, "Low": low, "Close": close, "Volume": random.integers(1_000, 50_000, BARS)},
        index=pd.DatetimeIndex(sessions),
    )


def main() -> None:
    config = RecoveryConfig()
    frames = [fixture(seed) for seed in range(SYMBOLS)]
    tracemalloc.start()
    cpu_started = time.process_time()
    wall_started = time.perf_counter()
    latencies = []
    signals = 0
    for frame in frames:
        started = time.perf_counter()
        signals += evaluate_latest_recovery(frame, config) is not None
        latencies.append((time.perf_counter() - started) * 1_000)
    wall_seconds = time.perf_counter() - wall_started
    cpu_seconds = time.process_time() - cpu_started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    completed = []
    builder = FiveMinuteCandleBuilder(lambda symbol, candle: completed.append((symbol, candle)))
    builder.connection_started(pd.Timestamp("2026-08-26 09:15", tz=IST).to_pydatetime())
    tick_started = time.perf_counter()
    for index in range(SYMBOLS):
        builder.add_tick(
            f"SYM{index:03d}",
            DhanQuoteTick(1, str(index + 1), 1_000 + index / 10, 1_000 + index, pd.Timestamp("2026-08-26 10:01", tz=IST).to_pydatetime()),
        )
    tick_seconds = time.perf_counter() - tick_started

    result = {
        "symbols": SYMBOLS,
        "warmupBarsPerSymbol": BARS,
        "candleRowsHeld": SYMBOLS * BARS,
        "signalEvaluation": {
            "wallSeconds": round(wall_seconds, 4),
            "cpuSeconds": round(cpu_seconds, 4),
            "symbolsPerSecond": round(SYMBOLS / wall_seconds, 2),
            "meanMillisecondsPerSymbol": round(statistics.mean(latencies), 3),
            "p95MillisecondsPerSymbol": round(float(np.percentile(latencies, 95)), 3),
            "signalsOnLatestFixtureCandle": signals,
        },
        "tickToCandleUpdate": {
            "ticks": SYMBOLS,
            "wallMilliseconds": round(tick_seconds * 1_000, 3),
            "meanMicrosecondsPerTick": round(tick_seconds / SYMBOLS * 1_000_000, 3),
        },
        "peakTracedMemoryMiB": round(peak / 1024 / 1024, 3),
        "interpretation": "Deterministic synthetic benchmark; network reconnect/backfill latency is measured separately in production.",
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
