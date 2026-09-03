# Crypto and metals engine

## Implemented boundary

The existing NSE Dashboard, RSI Range strategy, RSI Recovery strategy, Top-5
research strategy, saved history, and Dhan signal runtime remain separate and
unchanged in behavior. The Crypto/Metals workspace has its own provider
adapters and durable records while sharing one crypto strategy evaluator
between backtest and paper-signal generation.

| Layer | Implementation | Responsibility |
| --- | --- | --- |
| Instrument domain | `backend/markets/common.py` | Normalized instruments, candles, timeframes, stable IDs |
| Public providers | `backend/markets/crypto/providers.py` | OKX instruments/candles and VALR pairs/buckets |
| Strategy | `backend/markets/crypto/strategy.py` | Completed-candle features, signals, next-bar backtest |
| Persistence/runtime | `backend/markets/crypto/engine.py` | SQLite, catalog validation, candle cache, scans, runs |
| Service API | `backend/markets/crypto/api.py` | Provider, instrument, signal, status, and backtest routes |
| Authenticated web proxy | `web/app/api/crypto/route.ts` | Session/proxy-token protected service access |
| Web UI | `web/app/crypto/crypto-workspace.tsx` | Add/remove, signals, and backtests |

## Data and execution rules

1. An instrument must be present and active in the provider's current public
   catalog before it can be added.
2. Candle records are normalized to UTC and incomplete candles are excluded.
3. Backtests and paper scans call the same `generate_signals` function.
4. Backtest fills occur at the next candle open with configured slippage and
   cost assumptions.
5. A same-candle stop/target collision is resolved stop-first.
6. Signals have deterministic IDs and are inserted once.
7. The engine contains no private exchange endpoint and no order method.

## Known gaps before any live-capital decision

- Add a provider-specific fee schedule instead of one default cost input.
- Add historical funding for perpetual instruments.
- Store bid/ask or order-book snapshots to measure spread and executable
  slippage; OHLCV cannot reconstruct either reliably.
- Add walk-forward/out-of-sample validation and portfolio-level exposure caps.
- Add exchange status/maintenance and stale-data gates.
- Add news/event and cross-instrument correlation gates only after their data
  sources are defined and tested.
- Keep at least four weeks of paper observations before evaluating whether any
  strategy deserves a separate live-execution project.

## Review: what should stay and what may be removed

Keep the existing active NSE strategy modules because their keys and historical
results are already persisted. Keep retired result renderers/read paths as long
as users need historical runs, but do not restore their run dispatchers.

The root and `web/public` CSV copies are deliberate build/runtime fallbacks and
should not be deleted until the dashboard is migrated to a single durable data
source. The example D1 files under `web/examples/d1` are not part of the running
market system and may be removed if they are no longer used for vinext platform
experiments. Old validation scripts and benchmark JSON should be archived only
after their referenced reports are retired; deleting them now would weaken
strategy auditability.

Do not combine NSE and Crypto provider code into one large module. Their market
sessions, symbol metadata, fees, candle sources, and validation requirements are
different; only the strategy interface and paper-only safety contract should be
shared.
