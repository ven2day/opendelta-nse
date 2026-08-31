import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const source = (path) => readFile(new URL(path, root), "utf8");

test("Stock Scanner remains available in legacy and unified application navigation", async () => {
  const [legacyFiles, signals, platformChrome] = await Promise.all([
    Promise.all([
    source("app/dashboard.tsx"),
    source("app/backtest/backtest-dashboard.tsx"),
    source("app/signals/live-universe.tsx"),
    source("app/admin/admin-settings.tsx"),
    ]),
    source("app/signals/signals-workspace.tsx"),
    source("app/platform/platform-chrome.tsx"),
  ]);
  for (const file of legacyFiles) {
    assert.match(file, /Dashboard[\s\S]{0,60}<\/a>[\s\S]{0,180}href="\/scanner"/);
    assert.match(file, /Stock Scanner/);
  }
  assert.doesNotMatch(signals, /className="global-header"/);
  assert.match(platformChrome, /href: "\/scanner", label: "Scanner"/);
});

test("scanner page and API require the existing authenticated session", async () => {
  const [page, funnelPage, route] = await Promise.all([
    source("app/scanner/page.tsx"),
    source("app/signals/funnel/page.tsx"),
    source("app/api/stock-scanner/route.ts"),
  ]);
  assert.match(page, /requireSessionUser\(\)/);
  assert.match(funnelPage, /requireSessionUser\(\)/);
  assert.match(funnelPage, /focusSignals/);
  assert.match(route, /getSessionUser\(\)/);
  assert.match(route, /Authentication required/);
  assert.match(route, /\/stock-scanner\?refresh=/);
  assert.match(route, /cache: "no-store"/);
});

test("scanner UI leads with actual setups and retains activity rankings as context", async () => {
  const scanner = await source("app/scanner/stock-scanner.tsx");
  assert.match(scanner, /All valid setups/);
  assert.match(scanner, /ALL QUALIFYING TECHNICAL SETUPS/);
  assert.match(scanner, /NO VALID SETUP/);
  assert.match(scanner, /activity leaders/);
  assert.match(scanner, /Top 20 opportunities/);
  assert.match(scanner, /response\.signalFunnel\.allSignals/);
  assert.match(scanner, /Why BUY/);
  assert.match(scanner, /When to SELL/);
  assert.match(scanner, /historicalEvidence/);
  assert.match(scanner, /response\.watchlist\.topFive/);
  assert.match(scanner, /response\.opportunities/);
});

test("scanner shows 15-minute history and explicit 09:30-14:30 research cadence", async () => {
  const scanner = await source("app/scanner/stock-scanner.tsx");
  assert.match(scanner, /15-minute rescans/);
  assert.match(scanner, /09:30–14:30 IST/);
  assert.match(scanner, /Intraday watchlist history/);
  assert.match(scanner, /promoted/);
  assert.match(scanner, /removed/);
});

test("scanner consumes the application-wide price filter", async () => {
  const [scanner, backend] = await Promise.all([
    source("app/scanner/stock-scanner.tsx"),
    source("../backtest_api.py"),
  ]);
  assert.match(scanner, /Global price range/);
  assert.match(scanner, /metadata\.globalPriceRange/);
  assert.match(backend, /market_symbols = list_market_symbols\(\)/);
  assert.match(backend, /minimum_price=settings\.minimum_price/);
  assert.match(backend, /maximum_price=settings\.maximum_price/);
});

test("scanner stays paper-only and keeps the original RSI Recovery workspace isolated", async () => {
  const [scanner, backend, funnel, engine, signals] = await Promise.all([
    source("app/scanner/stock-scanner.tsx"),
    source("../stock_scanner.py"),
    source("../nse_signal_funnel.py"),
    source("../nse_signal_engine_v2.py"),
    source("../live_signals.py"),
  ]);
  assert.match(scanner, /Live orders disabled/);
  assert.match(backend, /"liveOrdersEnabled": False/);
  assert.match(backend, /"signalUniversePolicy": "SIGNAL_FIRST_FULL_ELIGIBLE_UNIVERSE"/);
  assert.match(funnel, /from nse_signal_engine_v2 import/);
  assert.match(engine, /TrendPullbackContinuationDetector/);
  assert.match(engine, /BreakoutRetestDetector/);
  assert.match(signals, /def evaluate_latest_recovery/);
  assert.doesNotMatch(`${backend}\n${funnel}`, /place_order|broker_order|paper-buy/);
});

test("company names are available on symbol hover", async () => {
  const scanner = await source("app/scanner/stock-scanner.tsx");
  assert.match(scanner, /title=\{entry\.companyName\}/);
  assert.match(scanner, /\{entry\.companyName\}/);
});

test("responsive scanner layout contains wide tables without page overflow", async () => {
  const styles = await source("app/globals.css");
  assert.match(styles, /\.scanner-shell[\s\S]*overflow-x: clip/);
  assert.match(styles, /\.scanner-table-wrap[\s\S]*overflow: clip/);
  assert.match(styles, /grid-template-columns: repeat\(5, minmax\(0, 1fr\)\)/);
  assert.match(styles, /\.scanner-main[\s\S]*font-size: 15px/);
  assert.match(styles, /\.scanner-hero h1[\s\S]*2\.6rem/);
  assert.match(styles, /\.scanner-signal-grid[\s\S]*repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(styles, /@media \(max-width: 760px\)[\s\S]*\.scanner-status-grid[\s\S]*grid-template-columns: 1fr/);
});

test("production backend image includes both scanner modules", async () => {
  const dockerfile = await source("deploy/backtest.Dockerfile");
  assert.match(dockerfile, /stock_scanner\.py/);
  assert.match(dockerfile, /nse_signal_funnel\.py/);
  assert.match(dockerfile, /nse_signal_engine_v2\.py/);
});

test("production scanner smoke validates the signal-first contract", async () => {
  const smoke = await source("deploy/smoke-stock-scanner.sh");
  assert.match(smoke, /SIGNAL_FIRST_FULL_ELIGIBLE_UNIVERSE/);
  assert.match(smoke, /maximumTradesPerDay == 5/);
  assert.match(smoke, /maximumConcurrent == 2/);
  assert.match(smoke, /nse_trend_pullback_continuation_v2/);
  assert.match(smoke, /nse_breakout_retest_v2/);
  assert.match(smoke, /DISPLAY_ORDER_ONLY_NEVER_HIDES_SIGNALS/);
});

test("candidate deployment supports an isolated validated host port", async () => {
  const [runner, promoter] = await Promise.all([
    source("deploy/run-container.sh"),
    source("deploy/promote-candidate.sh"),
  ]);
  assert.match(runner, /candidate_port="\$\{2:-3100\}"/);
  assert.match(runner, /candidate port must be an integer between 1024 and 65535/);
  assert.match(runner, /--publish "127\.0\.0\.1:\$\{candidate_port\}:3000"/);
  assert.match(runner, /http:\/\/127\.0\.0\.1:\$\{candidate_port\}\/login/);
  assert.match(promoter, /candidate_port="\$\{2:-3100\}"/);
  assert.match(promoter, /docker port vento-nse-candidate 3000\/tcp/);
  assert.match(promoter, /http:\/\/127\.0\.0\.1:\$\{candidate_port\}/);
  assert.match(promoter, /sed "s#http:\/\/127\.0\.0\.1:3100#http:\/\/127\.0\.0\.1:\$\{candidate_port\}#g"/);
  assert.match(promoter, /docker stop "\$previous_container"/);
  assert.doesNotMatch(promoter, /docker rm "\$previous_container"/);
});
