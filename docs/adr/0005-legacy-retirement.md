# ADR 0005: Legacy research engine retirement

Status: accepted

The unified `/v2` platform (strategy registry, screener, backtests, live
signals, paper trading) fully supersedes the first-generation engines. The
legacy surface has been removed from the codebase:

- `backend/compat/` (RSI Recovery live-signal engine, recovery backtest and
  feature analysis, universe selection, ATR/RSI exit optimizers, job service)
- `backend/strategies/strong_buy_compat.py` (legacy Strong Buy reference
  implementation; `StrongBuyV1` is the native replacement)
- `backend/backtest/history.py` (file-based backtest history; superseded by the
  database-backed `/v2/backtests` run records)
- Legacy HTTP routes: `/backtest`, `/backtest/jobs*`, `/backtest/optimize-atr`,
  `/backtest/compare-rsi-exits`, `/backtest-history*`, `/live-universe/*`,
  `/live-signals*`, `/paper-trades*`, `/recovery-analysis*`,
  `/live-signals/settings`
- `web/app/legacy/*` pages and their web proxies (`/api/backtest`,
  `/api/backtest-history`, `/api/live-signals`, `/api/live-universe`,
  `/api/recovery-analysis`, `/api/market-data`, `/api/market-symbols`,
  `/api/crypto`, `/api/history-owner`)
- `/admin` now redirects to `/settings`; the global price range form lives in
  the unified Settings workspace.

Equivalence between the legacy and v2 Strong Buy implementations was proven
bar-for-bar before retirement (causality, next-open entry, indicator tables);
those guarantees now live in `tests/test_strategy_engine.py`,
`tests/test_backtest_engine.py`, and `tests/test_paper_trading.py` against the
native implementations only.

Saved historical results produced by retired engines are no longer served.
Historical candle caches, OI data, application settings, and the NIFTY OI
status route are unchanged.