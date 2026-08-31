# OpenDelta Market Research

Production quant-platform architecture, contracts, data flows, lifecycles, APIs, tests, deployment, and limitations are documented in [web/docs/quant-platform-v1.md](web/docs/quant-platform-v1.md). Provider, factor, and strategy extension guides are in [web/docs/extensions.md](web/docs/extensions.md).

## Research safety status

New Research experiments are disabled by default with the server-side
`RESEARCH_ENGINE_V2_ENABLED=false` safety gate. Results created by the former
one-bar next-open-to-next-close observation model are retained but labelled
`LEGACY_INVALID_RESEARCH_MODEL`; their profitability metrics are hidden because
that model was not a strategy backtest. The factor education catalogue remains
available. Do not enable Research V2 until its deterministic trade-lifecycle,
multi-timeframe, worker, browser, and production acceptance checks pass.

The executable Research V1 code and request contract have been removed. Legacy
results remain read-only for audit purposes; only the versioned Research V2
request can create a new experiment.

## Canonical market-data rollout

TimescaleDB is the selected source of truth for completed NSE and crypto
candles. The rollout is additive: existing readers remain active until
dual-write reconciliation succeeds. Production can install the pinned,
private TimescaleDB service and apply the schema with:

```bash
sudo /opt/vento-nse/current/web/deploy/install-timescale-service.sh
```

For local or already-provisioned databases, apply the schema directly with:

```bash
python market_data_admin.py migrate
```

Build a versioned, holiday-aware calendar from an exact official NSE trading
day export, then load it:

```bash
python market_data_calendar.py \
  --trading-days /secure/path/official-nse-trading-days.csv \
  --output /secure/path/nse-sessions.csv \
  --start 2024-09-01 --end 2026-09-01 \
  --calendar-version NSE-2024-2026-v1 \
  --source-url https://nsearchives.nseindia.com/path/to/source.csv
python market_data_admin.py load-sessions --market NSE --file /secure/path/nse-sessions.csv
```

The CSV columns are `session_date,is_trading_day,open_time,close_time,calendar_version`.
Fresh Dhan cache fetches and OKX SQLite syncs then dual-write completed candles
to TimescaleDB. A failed canonical write never replaces or corrupts the legacy
copy; the failure is exposed by `/platform/data-health` for repair.

Queue resumable historical work with one command or a CSV manifest:

```bash
python market_data_admin.py enqueue-nse-universe \
  --timeframe 5m --start 2024-09-01T00:00:00Z --end 2026-09-01T00:00:00Z
python market_data_admin.py enqueue-okx-configured \
  --timeframe 5m --start 2024-09-01T00:00:00Z --end 2026-09-01T00:00:00Z
python market_data_worker.py --providers DHAN,OKX --maximum-chunks 100
python market_data_admin.py health
```

Each chunk is leased to one worker, checkpointed, retried with bounded backoff,
and reconciled by row count and SHA-256 before the checkpoint advances. The
completed range is gap-checked and repaired against the explicit NSE calendar
or continuous crypto UTC timeline. See [market-data operations](web/docs/market-data-operations.md)
and [TimescaleDB production bootstrap](web/docs/timescaledb-production-bootstrap.md),
plus [ADR 0004](web/docs/adr/0004-timescaledb-canonical-market-data.md). Redis is
not required for this phase, and existing readers are still unchanged.

Authenticated NSE market-research dashboard with RSI filters, signals,
point-in-time backtesting, saved account history and auditable strategy
diagnostics.

- Website: <https://nse.ventoday.com>
- Backtest: <https://nse.ventoday.com/backtest>
- NSE signal funnel: `/signals/funnel`
- Crypto/Metals backtest: `/backtest/crypto`
- Crypto/Metals signals: `/signals/crypto`

## Strategy status

| Strategy | Status | Notes |
| --- | --- | --- |
| EMA/VWAP Strong Buy | Active | The only strategy that can start a new backtest; broker orders are disabled. |
| RSI Range Strategy | Retired | Historical results remain read-only; new runs are blocked. |
| RSI Recovery Scalping | Retired | Historical results remain read-only; new runs are blocked. |
| NSE Signal Engine V2 | Research only | Long-only Trend Pullback Continuation and Breakout-Retest signals; all valid setups remain visible and broker orders are disabled. |
| Top-5 Opening Range Breakout | Retired | Historical results remain read-only; new runs are blocked. Supported `FROZEN_OPEN` and `ROLLING`. |
| Market-Aligned RSI Scalper | Retired | Historical results remain read-only; new runs are blocked. |
| Market-Aligned VWAP Pullback Scalper | Retired | Historical results remain read-only; new runs are blocked. |
| Crypto Trend Pullback Recovery | Research only | OKX/VALR public candles, completed-candle signals, next-bar backtest entry, no order path. |

No strategy in this repository is represented as guaranteed profitable.

## Stock Scanner

The authenticated `/scanner` page now leads with NSE Signal Engine V2; the same
view is available under Signals at `/signals/funnel`. It reads locally cached
completed five-minute candles for the saved global price-filtered NSE universe.
Every eligible symbol is evaluated for two independent long-only setups:
Trend Pullback Continuation and Breakout-Retest. Signal checks follow the latest
completed five-minute candle. Ranking changes display order only and never
hides a valid setup; activity Top-5 and Top-20 tables remain context, not entry
signals.

Every valid setup includes the passed rules, BUY rationale, stop-entry range,
structural stop, 1.5R target, timeout/invalidation/session SELL conditions and
historical-evidence record. V2 does not manufacture a probability: until its
walk-forward gate has at least 200 trades across 50 symbols, positive net and
stress expectancy, profit factor at least 1.20 and a positive confidence lower
bound, the evidence is `UNVALIDATED` and the setup is a `RESEARCH_SIGNAL`.
Paper limits decide only `PAPER_EXECUTED` versus
`PAPER_SKIPPED_RISK_LIMIT`; qualified signals remain visible. The original RSI
Recovery live workspace, keys, signals and history are unchanged.

The complete rule and evidence contract is documented in
[NSE Signal Engine V2](web/docs/nse-signal-engine-v2.md).

The scanner never fetches missing historical candles during an HTTP request,
never creates a broker order, and never enables live execution. The main NSE
Dashboard is unchanged.

## Crypto and metals research

OpenDelta now has separate NSE and Crypto/Metals workspaces. The main Dashboard
remains NSE-only. `/backtest/crypto` and `/signals/crypto` share one
provider-neutral strategy implementation so signal and backtest rules cannot
drift apart.

- `OKX` uses its public instruments and historical-candles APIs.
- `VALR` uses its public pairs and candle-buckets APIs.
- No API key is needed for the implemented public market-data endpoints.
- The Add Instrument button accepts only exact active symbols returned by the
  selected provider catalog.
- `XAUUSD.p` and `XAGUSD.p` are broker-specific CFD names. They are not
  hardcoded or silently mapped. Gold or silver can be added only when the
  selected provider actually publishes an XAU/XAG instrument.
- SQLite persists configured instruments, completed candles, deduplicated
  paper signals, and backtest summaries below `CRYPTO_MARKET_DIR`.
- Private account, balance, position, order, withdrawal, and live-execution
  APIs are intentionally absent.

The starter strategy is `crypto_trend_pullback_recovery` v1.0.0. It uses
EMA20/EMA50 direction, UTC-day VWAP, RSI arm/recovery, relative volume, an ATR
stop, a 1.5R target, and a six-bar time exit. It reads completed candles and
enters backtests only on the next candle open. Perpetual results include a
warning because funding is not yet modeled.

Production variables for the backtest service:

```dotenv
CRYPTO_SIGNAL_ENGINE_ENABLED=true
CRYPTO_SIGNAL_POLL_SECONDS=60
CRYPTO_MARKET_DIR=/var/lib/vento-nse/backtest/crypto-market
OKX_PUBLIC_API_URL=https://www.okx.com
VALR_PUBLIC_API_URL=https://api.valr.com
```

See [`web/docs/crypto-market-engine.md`](web/docs/crypto-market-engine.md) for
the component boundaries, current limitations, and safe removal candidates.

## Repository security

This is a public repository. Never commit `.env` files, Dhan credentials,
access tokens, TOTP secrets, private keys, production host details or database
credentials. Use the checked-in example files only as templates and keep real
values in the server environment with restrictive file permissions.

The root `.gitignore` excludes local environment files, common private-key
formats, SSH private-key filenames, runtime data, reports and deployment
archives. Review staged files and run a secret scan before every public push.

## Local development

1. Copy `.env.example` to `.env.local` and set a unique username, password,
   and a random `AUTH_SECRET` of at least 32 characters.
2. Set the Dhan variables documented in
   `deploy/vento-nse-dhan.env.example`, then generate the source CSV from the
   project root:

   ```powershell
   python main.py
   ```

3. Install and run the web app from the `web` directory:

   ```powershell
   cd web
   npm ci
   npm run dev
   ```

The dashboard is available at `http://localhost:3000`. The CSV synchronized at
build time is a fallback; the browser refreshes live market data every hour
and also supports a manual all-symbol refresh.

## Production build

```bash
cd web
npm ci
npm test
npm start
```

The production server listens on `0.0.0.0:3000` by default. Keep it behind
HTTPS so the login cookie is marked `Secure`.

## Ubuntu/Debian deployment

The included files assume releases are installed below `/opt/vento-nse` and
the dashboard runs in Docker.

1. Keep `/etc/vento-nse.env` limited to UI login settings:

   ```dotenv
   APP_USERNAME=admin
   APP_PASSWORD=replace-with-a-long-unique-password
   AUTH_SECRET=replace-with-at-least-32-random-characters
   ```

2. Create `/etc/vento-nse-dhan.env` from
   `deploy/vento-nse-dhan.env.example`, set mode `0600`, and never pass it to
   the frontend container.
3. Build both images with `deploy/install-release.sh`, install the collector
   units with `deploy/install-data-service.sh`, and run
   `systemctl start vento-nse-data.service` once before starting the UI.
4. Start the UI with `deploy/run-container.sh`. It mounts only
   `/var/lib/vento-nse/data` into the container as read-only market data.
5. Install the nginx site, validate with `nginx -t`, and reload nginx.
6. Install a TLS certificate for `nse.ventoday.com` and use Cloudflare Full
   (strict) SSL mode.

The Dhan collector runs after the NSE close at 16:15 IST on weekdays. It uses
TOTP authentication, validates the Dhan data subscription, retries stale
symbols, requires one common NSE session, and publishes atomically only when
the configured coverage threshold is satisfied.

The login uses a signed, 12-hour, HTTP-only cookie. Credentials and the signing
secret are read only from server environment variables.

## Historical NIFTY OI import

The shared point-in-time OI repository remains available at
`/var/lib/vento-nse/backtest/nifty-oi`. RSI Recovery Scalping and RSI Range
Strategy do not consume it. Market-Aligned VWAP Pullback Scalper keeps OI
`OFF` by default and may display it only as optional advisory context.
Production collection continues to use the configured Dhan integration; no
NSE website scraping or embedded contract identifiers are used.

## Backtest strategy separation

`EMA/VWAP Strong Buy` (`ema_vwap_strong_buy`) is the only strategy that can start
a new backtest. Every other strategy is retired from launching: `/backtest` and
`/backtest/jobs` reject them with HTTP 422, and the frontend offers no selector
for them.

`RSI Range Strategy`, `RSI Recovery Scalping` and `Top-5 Opening Range Breakout`
retain their existing keys, configuration, evaluators, exits, engines and result
views. Saved history entries still render read-only and display
`Retired strategy — cannot run again`.

`Market-Aligned RSI Scalper` is retired. It is absent from new-backtest
selection and cannot start a new API job. Existing saved results remain
read-only and display `Retired strategy — cannot run again`.

`Market-Aligned VWAP Pullback Scalper` is retired from new backtests. Historical
results remain readable under its original `market_aligned_vwap_pullback_scalper`
key, but the frontend selector and active API dispatcher cannot start it again.

NIFTY supplies one safety rule. Sector, breadth, relative strength and optional
OI contribute to causal quality ranking instead of repeated mandatory gates.
Historical bid/ask spread is reported unavailable rather than fabricated.
Operations may provide absolute `MARKET_CONTEXT_SECTOR_MAP_FILE` and
`MARKET_CONTEXT_BREADTH_UNIVERSE_FILE` paths for supporting context.

This strategy is labelled `Research candidate — paper trading required` and
must not be represented as profitable without untouched chronological
validation.

### Top-5 Opening Range Breakout research modes

`Top-5 Opening Range Breakout` is retired from new backtests. Its
`top_5_opening_range_breakout` key, engine and saved results are preserved. It
supported `FROZEN_OPEN` (the default) and `ROLLING` selection. Frozen mode ranks
at 09:30 and retains five symbols for the session. Rolling mode rescans completed candles every 30 minutes
through 14:00, applies score-advantage, residence-time, replacement-count and
sector caps, and makes a promoted symbol eligible only from the next completed
five-minute candle.

The opening research rule uses the completed 09:15-09:30 range and enters a
qualifying breakout only at the next candle open. Midday promotions use a
six-bar Rolling Momentum Breakout; the trigger candle is excluded from the
breakout level. Frozen top five, rolling top five, opening top two, full
eligible universe, liquidity-only top five and a causally matched random five
all share the same chronological portfolio, exactly 50 shares per executed
trade, stop/target, cost, slippage and square-off rules. Historical spread
remains advisory when bid/ask data is unavailable. The strategy produces
research and paper signals only and has no broker-order path. It remains
rejected unless every untouched validation fold has positive after-cost
expectancy and net P&L and outperforms the comparison baselines.

The recommended eligibility settings are a completed-candle price range of
₹100-₹5,000, at least ₹100,000,000 median daily traded value from prior
completed sessions, at least ₹2,500,000 causal opening traded value, prior-day
ATR between 0.8% and 4.0%, and a maximum absolute opening gap of 3.0%. These
values are part of the JSON/API configuration, configuration hash and result
cache key. The recommended maximum holding period is 12 five-minute bars;
an explicitly submitted JSON value remains authoritative. Results report
calendar-session and active-day trade frequencies separately, audit the exact
next-bar timestamps, and keep Markdown compact. Complete watchlists,
candidates, signals, trades and benchmark records are downloaded separately as
CSV or JSON.

#### Latest production research validation

The dated one-year run completed on 2026-08-30 used 649 requested symbols in
the configured ₹100-₹5,000 range. Frozen mode scored 619 symbols and executed
3 trades across 247 tested sessions; Rolling mode executed 159 trades. Frozen
ended at ₹-1,296.04 after costs and Rolling at ₹-19,533.23 after costs. In the
untouched final three months, Frozen executed 1 trade for ₹-17.29 and Rolling
executed 43 trades for ₹-5,788.08.

The result is `REJECTED_RESEARCH_ONLY`: after-cost expectancy was negative,
profit factor remained below 1, and the acceptance baselines were not beaten
reliably. This result must not be used to enable live broker orders. It is a
dated research observation, not a promise about future performance.

After deploying the dashboard and backtest images, run
`web/deploy/smoke-top-5-opening-range-breakout.sh`. It authenticates through the
normal web login, submits the unique Top-5 strategy key, requires an effective
FROZEN_OPEN configuration, and fails unless at least one historical daily
watchlist contains five ranked PRIMARY/RESERVE selections. The retired
`smoke-vwap-pullback.sh` fails closed and cannot create another VWAP run.

## Release rollback

Before a cutover, record the active release directory and both container image
tags. To roll back, stop only the dashboard and backtest units, restore the
previous release symlink/image tags with the existing deployment scripts, start
those two units, then run the authenticated Backtest and Signals smoke tests.
Do not restart the unrelated market-data collector. Production credentials and
`/etc/vento-nse-dhan.env` must never be copied into a release archive or logs.

## RSI Recovery position exits

The Backtest page keeps the original `Legacy fixed target` research mode and
adds position-based exit models without changing the underlying RSI Recovery
signal generator. `RSI profitable exit with risk control` uses a configurable
low-zone arm, recovery crossover, enabled EMA/VWAP/volume confirmations, and
the selected `SIGNAL_CLOSE` or `NEXT_BAR_OPEN` entry execution.

For the RSI profit-exit model, the hard stop is fixed from the executed entry
and begins monitoring on the following candle. A profitable RSI exit is
eligible only after a completed candle has RSI at or above the configured
profit-exit level and the executable price meets the configured minimum
profit. `NEXT_BAR_OPEN` exits recheck that minimum at the actual next open. If
neither rule closes the lot, the time exit occurs at the next available NSE
session open after the configured holding sessions; missing sessions and
weekends are not counted.

Configured buy/sell costs and per-side slippage are reported separately from
gross P&L. Closed-trade net P&L is gross P&L minus both sides' costs and
slippage. Open-trade unrealized P&L uses the last close and includes estimated
entry and exit costs under the same assumptions. The optional comparison tool
uses chronological development/validation splits and labels every result as a
research candidate, not live approval.
