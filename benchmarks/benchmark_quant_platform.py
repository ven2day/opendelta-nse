from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from opendelta.factors import FactorEngine


SYMBOLS = 750
BARS_PER_SYMBOL = 1_000
FACTORS = ("ema_alignment", "rsi_recovery", "rvol", "atr_percentile")
CHUNK_SIZE = 50


def frame(seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0, 0.4, BARS_PER_SYMBOL).cumsum()
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2025-01-01", periods=BARS_PER_SYMBOL, freq="5min", tz="UTC"
            ),
            "open": close + rng.normal(0, 0.03, BARS_PER_SYMBOL),
            "high": close + rng.uniform(0.05, 0.6, BARS_PER_SYMBOL),
            "low": close - rng.uniform(0.05, 0.6, BARS_PER_SYMBOL),
            "close": close,
            "volume": rng.integers(1_000, 100_000, BARS_PER_SYMBOL),
        }
    )


def main() -> None:
    engine = FactorEngine()
    started = time.perf_counter()
    values = 0
    chunks = 0
    for chunk_start in range(0, SYMBOLS, CHUNK_SIZE):
        chunks += 1
        for symbol in range(chunk_start, min(chunk_start + CHUNK_SIZE, SYMBOLS)):
            candles = frame(symbol)
            for factor_id in FACTORS:
                output = engine.calculate(
                    factor_id, candles, market="NSE", timeframe="5m"
                )
                values += 0 if output.values is None else len(output.values)
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "symbols": SYMBOLS,
                "barsPerSymbol": BARS_PER_SYMBOL,
                "inputCandles": SYMBOLS * BARS_PER_SYMBOL,
                "factors": len(FACTORS),
                "factorValues": values,
                "chunkSize": CHUNK_SIZE,
                "chunks": chunks,
                "elapsedSeconds": round(elapsed, 3),
                "candlesPerSecond": round(SYMBOLS * BARS_PER_SYMBOL / elapsed),
                "factorValuesPerSecond": round(values / elapsed),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
