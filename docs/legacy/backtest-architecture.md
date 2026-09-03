# OpenDelta Historical Backtest Architecture

## Baseline before RSI Recovery

1. **Frontend framework and structure** — React 19 rendered through the Next.js-compatible vinext runtime. The authenticated historical backtest route is `app/backtest/page.tsx`; its client UI, chart, request batching, and result rendering live in `app/backtest/backtest-dashboard.tsx`. Shared OpenDelta styling is custom CSS in `app/globals.css`.
2. **Backend framework** — FastAPI in `backend/app.py`, run by Uvicorn in the existing backtest container.
3. **Historical Backtest page** — one OpenDelta page with selected/all-symbol controls, 1/3-year duration, seven timeframes, RSI entry/exit ranges, an interactive SVG chart, symbol summary, and trade details.
4. **Existing RSI strategy** — `simulate_symbol` enters after RSI first enters the configured low range, executes at the following candle open, considers high-RSI exits at the following candle open, and holds until the existing minimum 1% estimated net profit condition is satisfied.
5. **API endpoints** — FastAPI exposes `GET /health` and `POST /backtest`. The authenticated frontend proxy is `app/api/backtest/route.ts`.
6. **Dhan history** — `DhanClient`, authentication, instrument mapping, payload parsing, and daily/intraday history retrieval are centralized in `backend/collector.py` and reused by `HistoricalDataStore`.
7. **NSE universe** — `symbols.csv`, loaded through the existing `load_symbols` helper. The synchronized frontend data supplies the same 750-symbol selector.
8. **Candle cache** — `HistoricalDataStore` uses atomic gzip CSV files per symbol/source interval/duration in `BACKTEST_CACHE_DIR`, with a one-hour TTL.
9. **Indicators** — only the existing RSI helper is present. It is retained for the RSI Range strategy to avoid changing established results.
10. **Backtest engine** — candle preparation/resampling and the compatibility simulator are in `backend/app.py` and `backend/compat/`.
11. **Result/report models** — a Pydantic request plus JSON-compatible dictionaries; the frontend defines matching TypeScript types. No persisted report model exists.
12. **Database** — a Cloudflare D1/Drizzle scaffold exists, but `db/schema.ts` is intentionally empty and the backtest does not use a database.
13. **Tests** — Python `unittest` tests in `../tests`; Node's built-in test runner validates the production frontend build in `tests/rendered-html.test.mjs`.
14. **Concurrency and batches** — the browser submits all-universe runs in deterministic batches of 10. FastAPI serializes requests with an async lock; each batch was processed sequentially.
15. **Theme/components** — OpenDelta CSS variables, dark/light palettes, Manrope and JetBrains Mono typography, native controls, and Lucide icons. There is no separate UI component framework.

## Extension boundary

RSI Recovery is implemented as a second strategy mode inside the same request route and page. It reuses authentication, Dhan history, IST candle normalization/resampling, the NSE universe, gzip cache, browser batching, API proxy, chart interaction, exports, and visual language. Its indicator calculation, stateful signal generation, target lifecycle, and research metrics are separated from the existing RSI Range simulator so that the original calculations remain regression-testable.

RSI Recovery is a signal-observation backtest, not a portfolio-capital simulation. Every fresh arm/recovery cycle creates an independent observation, even when earlier observations for the same symbol remain open. The engine keeps a per-symbol list of currently open observations, updates each observation's target and excursion state independently, and moves completed observations out of the active list. This preserves every qualifying signal without repeatedly scanning historical completed trades.
