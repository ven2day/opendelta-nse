# OpenDelta dashboard

Private NSE dashboard with RSI filters, yesterday/current RSI,
yesterday/current close, and 24-hour volume.

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

`RSI Range Strategy` and `RSI Recovery Scalping` retain their existing keys,
configuration, evaluators, exits, results and URLs.

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

`Top-5 Opening Range Breakout` is a standalone backtest strategy with
`top_5_opening_range_breakout` as its internal key. It supports `FROZEN_OPEN` (the
default) and `ROLLING` selection. Frozen mode ranks at 09:30 and retains five
symbols for the session. Rolling mode rescans completed candles every 30 minutes
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
