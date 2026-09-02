# Contributing

## Ground rules

- Paper trading only. Never add a broker or exchange order client; tests
  assert their absence and a PR that needs one is out of scope.
- Strategies decide BUY / SELL / NONE and nothing else. Database access,
  provider calls, WebSockets, backtest loops and accounting live in the
  engines.
- No strategy-name `if/elif` chains — use the registry.
- Every backtest run, signal and paper lot must keep `strategy_id`,
  `strategy_version` and its configuration snapshot.
- Do not push to `main`, deploy, or touch the production database from a
  branch without approval.

## Setup

```bash
uv sync                          # Python 3.12 environment
cd web && npm ci && cd ..        # Node 22
```

## Adding a strategy

1. `backend/strategies/<name>_v1.py` implementing `backend.strategies.base.Strategy`
   (`strategy_id`, `name`, `version`, `supported_markets`, `supported_timeframes`,
   `config_schema`, `required_history`, `validate_config`, `evaluate`; optional
   vectorised `decision_frame`).
2. Register it in `backend/strategies/__init__.py`: `STRATEGIES.register(YourStrategy())`.
3. Add `tests/test_<name>.py`. `tests/test_strategy_plugins.py` shows the
   minimum a plug-in must satisfy.

The screener, backtest, signals, paper trading, API and UI pick it up
automatically.

## Tests

```bash
PYTHONPATH=. pytest -q                                   # backend
TEST_DATABASE_URL=postgresql://… PYTHONPATH=. pytest -q  # + PostgreSQL suites
cd web && npm run lint && npx tsc --noEmit && npm test   # web (builds first)
cd web && npm run test:browser                           # Playwright
python scripts/security_scan.py                          # tracked-file secret scan
```

CI runs all of the above with a TimescaleDB service container.

## Pull requests

Small, focused commits with an explanatory message. Verified numbers belong
in the description (test counts, memory figures) — never claims that a
strategy is profitable.
