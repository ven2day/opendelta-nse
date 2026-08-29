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

`Market-Aligned VWAP Pullback Scalper` uses the distinct
`market_aligned_vwap_pullback_scalper` key. It arms RSI pullbacks from 38–50
near session VWAP, EMA9 or EMA20 only in a rising completed-candle trend, then
requires a completed trigger candle and executes at the next bar open.
Session state and VWAP reset every NSE trading day. Stops and 1.5R targets are
frozen from entry-time ATR and pullback structure; the strategy never holds
overnight.

NIFTY supplies one safety rule. Sector, breadth, relative strength and optional
OI contribute to causal quality ranking instead of repeated mandatory gates.
Historical bid/ask spread is reported unavailable rather than fabricated.
Operations may provide absolute `MARKET_CONTEXT_SECTOR_MAP_FILE` and
`MARKET_CONTEXT_BREADTH_UNIVERSE_FILE` paths for supporting context.

This strategy is labelled `Research candidate — paper trading required` and
must not be represented as profitable without untouched chronological
validation.

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
