# V1 performance measurement

Measured on the Windows development workspace on 2026-08-30 with Python 3.11, pandas, one process, one thread, deterministic synthetic input, and no provider/network or database I/O.

Command:

```powershell
$env:PYTHONPATH='.'
& '..\.venv\Scripts\python.exe' benchmarks\benchmark_quant_platform.py
```

Result:

```json
{
  "symbols": 750,
  "barsPerSymbol": 1000,
  "inputCandles": 750000,
  "factors": 4,
  "factorValues": 3000000,
  "chunkSize": 50,
  "chunks": 15,
  "elapsedSeconds": 55.08,
  "candlesPerSecond": 13617,
  "factorValuesPerSecond": 54466
}
```

This measures the factor calculation path for EMA alignment, RSI recovery, RVOL, and ATR percentile. It does not prove provider ingestion, database, network, or multi-user scalability. The production container retains its 2 GiB memory limit; application jobs use bounded workers/queue, research candidates are bounded before execution, UI results are paginated/contained, and the benchmark processes 50-symbol chunks rather than retaining all 750 frames.

The ATR percentile implementation dominates this mix because it performs a rolling percentile. Future optimization should begin with a measured vectorized/rank implementation and must preserve numerical regression fixtures.
