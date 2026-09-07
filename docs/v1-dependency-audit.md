# Mandatory V1 dependency audit

Assessment date: 2026-09-07. Nothing classified `UNCERTAIN` may be removed
without an owner decision. No production table is dropped in the V2 cutover.

Names ending in `_v1` are not automatically obsolete. In this repository they
often identify a strategy or external provider API version and remain part of
the immutable V2 lifecycle.

## KEEP — OpenDelta foundation

| Candidate | Current consumers | V2 replacement | Coverage / migration impact | Risk and recommendation |
| --- | --- | --- | --- | --- |
| `backend/collector.py` and Dhan environment settings | NSE ingestion, quote/status and canonical worker | None; this is the Dhan foundation | collector, refresh, calendar and NSE E2E suites; no schema change | High removal risk. KEEP. |
| `backend/markets/crypto` public providers and `CryptoMarketService.sync_candles` | OKX/VALR catalogues, completed-candle recovery, Crypto candle source and Timescale dual writer | None; this is the Crypto data foundation | Crypto provider/dual-write tests; existing SQLite data remains | High removal risk. KEEP. |
| `backend/data/store.py` | Explicit legacy/fallback candle-read modes during Timescale rollout | Strict Timescale is the target default, but rollback still consumes this store | fallback tests; no destructive migration | KEEP as a rollback reader until an operational retention decision. |
| `backend/markets/timescale_source.py` fallback mode and `TimescaleDualWriter` | Safe market-data migration and missing-data recovery | Strict Timescale mode when healthy | Timescale source, deployment and market-data tests | KEEP; fallback is an availability control, not a duplicate V1 product. |
| `backend/strategies/*_v1.py` and strategy IDs ending `_v1` | Registry, backtests, signals, paper, research, TradingView parity | Immutable version fields already pin these implementations | extensive evaluator/parity/governance tests; rows reference IDs | KEEP. Renaming would break immutable history. |
| TimescaleDB, screener, watchlists/presets, signal lifecycle, paper broker/FIFO | All V2 workflows | None | migrations 001-023 and lifecycle suites | KEEP as platform foundations. |
| `/application-settings`, `/market-data/*`, `/platform/*`, `/nifty-oi/*` | Unified chrome/settings and operational data administration | V2 UI proxies already consume these operational endpoints | route and UI tests; no migration | KEEP; non-`/v2` does not mean obsolete. |
| `web/app/admin/page.tsx` redirect | Bookmarks to retired admin shell | `/settings` | navigation test | KEEP the safe redirect. |
| Existing database tables created before migration 012 | Historical signals, paper lots, runs and market data | V2 writes continue where applicable | migration suite; historical foreign keys | KEEP. Never drop historical data in this release. |

## REPLACED — V2 equivalent validated

| Candidate | Current consumers | V2 replacement | Coverage / migration impact | Risk and recommendation |
| --- | --- | --- | --- | --- |
| Retired Research/Failure engines and `/api/backtest`, `/research/run` style entry points | None mounted | `/v2/backtests` and `/v2/research/*` | ADR 0005, route contract and research suites; migrations 016-018 | Already removed. Do not recreate. |
| Retired `web/app/legacy/*` pages and embedded legacy header | None; directory absent | Unified OpenDelta workspaces and top navigation | rendered-page/navigation/browser tests | Already removed. Keep compatibility redirects only where present. |
| `strong_buy_compat.py` reference implementation | None; file absent | Registered `StrongBuyV1` using the shared evaluator | parity and strategy-engine tests | Already removed under ADR 0005. |
| One-strategy Research Lab variants | Existing endpoint forwards through the preview contract | Phase 7 manual/grid generation | migration 017 and parameter-experiment tests | Keep compatible API alias; it regenerates/validates server-side. |

## REMOVE — no remaining consumer

| Candidate | Current consumers | V2 replacement | Coverage / migration impact | Risk and recommendation |
| --- | --- | --- | --- | --- |
| `NSE_LIVE_STRATEGY`, `NSE_LIVE_TIMEFRAME`, `CRYPTO_LIVE_STRATEGY`, `CRYPTO_LIVE_TIMEFRAME` singular fallback | Only `live_strategy_bindings()` compatibility branch and one dedicated test | Plural JSON bindings plus pinned database deployments | plural/deployment tests cover replacement; no schema impact | Low risk after release-note warning. Remove code, test and docs in the final cleanup PR. |
| Startup use of `CRYPTO_SIGNAL_ENGINE_ENABLED` | Starts the older Crypto pullback scanner thread; the data service itself is also used on demand | V2 deployment-backed Crypto signal workers (`CRYPTO_SIGNAL_ENGINE_V2_ENABLED`) | V2 signal/paper suites and worker lease monitoring; no schema impact | Remove only the duplicate startup hook and deployment setting. Keep CryptoMarketService data methods. |
| Stale README claims that no live adapter/order path exists and all V2 features default off | Documentation only | Disabled-by-default live foundation and V2-default platform docs | documentation review; no migration | Remove/replace during cutover. |

## UNCERTAIN — requires owner decision

| Candidate | Current consumers | V2 replacement | Coverage / migration impact | Risk and recommendation |
| --- | --- | --- | --- | --- |
| `/crypto/backtest`, `/crypto/signals`, `/crypto/signals/scan` compatibility API | No current OpenDelta UI consumer found; external consumers cannot be proven absent | `/v2/backtests`, `/v2/signals` and deployment workers | legacy Crypto contract tests; SQLite result/history records | Do not delete. Deprecate, keep unlinked, and decide after access-log review. |
| Crypto SQLite catalogue/cache and historical result tables | Crypto catalogue/API and rollback candle source | Timescale is canonical for candles; no complete metadata/history migration exists | Crypto repository tests | Do not delete or stop reads. A later non-destructive migration plan is required. |
| `PLATFORM_CANDLE_READ_MODE=legacy` and `timescale-fallback` options | Operator rollback and unconfigured local development | strict `timescale` becomes the default | Timescale/fallback tests; production data readiness required | Keep options, change only the default. Remove after an owner-approved retention window. |
| Optional TradingView ingress and Pine parity assets | Explicit deployments may use it; not required by V2 | Native OpenDelta signals | signature/replay/parity tests; stored webhook events | Do not delete. It is optional, not a required platform dependency. |
| Old ADR wording and benchmarks labelled V1/legacy | Historical rationale and performance baselines | Current reports provide the final assessment | no runtime impact | Keep as historical documentation unless the owner requests archival. |

## Safe release policy

The cutover stops obsolete writes/workers before considering data removal. All
historical tables remain intact. Compatibility APIs in `UNCERTAIN` remain
available but are absent from V2 navigation. A future deletion requires access
logs, an owner decision, explicit export/retention steps and a separately
reviewed migration.
