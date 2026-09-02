# RSI Recovery Engine Benchmark

Run on 25 August 2026 with `uv run python benchmarks/recovery_benchmark.py --workers 4 --sizes 1,100,300,750`.

The fixture contains 252 deterministic NSE-style sessions with 75 completed 5-minute candles per session: 18,900 rows per symbol. It measures indicator calculation, stateful signal generation, overlapping-observation lifecycle, metrics, and chart sampling. Dhan download, rate-limit, cache I/O, and API JSON transfer time are excluded.

| Symbols | Candle rows | Old one-active runtime | New overlap runtime | Runtime change | New symbols/sec |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 18,900 | 1.1495 s | 1.3563 s | +18.0% | 0.7373 |
| 100 | 1,890,000 | 7.4453 s | 11.9099 s | +60.0% | 8.3964 |
| 300 | 5,670,000 | 15.9616 s | 29.8397 s | +87.0% | 10.0537 |
| 750 | 14,175,000 | 35.9232 s | 70.7006 s | +96.8% | 10.6081 |

The deterministic overlap fixture generated 83 observations per symbol, all resolved by dataset end, with a maximum of six simultaneously open observations for the same symbol. The prior engine suppressed later signals while one observation was open, so its lower runtime did less lifecycle work.

The new engine scans only the currently open observation list. Completed observations are immediately moved to the result collection, and there is no arbitrary observation cap. The 750-symbol test processed 14.175 million candles and 62,250 observations in 70.70 seconds with four process workers.

The single-symbol result includes process-pool startup and is not comparable to a warmed in-process request. Peak parent/child process memory was unavailable from the Windows runner and is not estimated.

These are deterministic engine throughput figures, not Dhan universe-run promises. Production throughput also includes cache reads, API serialization, and the server's CPU limits.

The exact production Phase 1 replay processed 13,697,897 real candles for 749 usable symbols in 1,191.72 seconds cache-warm and 1,972.40 seconds cache-cold. See `recovery-overlap-report.md` and the two `benchmarks/opendelta-rsi-recovery-overlap-baseline*.json` artifacts for results and the old/new comparison.
