import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

process.env.APP_USERNAME = "test-admin";
process.env.APP_PASSWORD = "test-password-123";
process.env.AUTH_SECRET = "test-secret-that-is-at-least-32-characters-long";

async function loadWorker() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker;
}

function fetchFromWorker(worker, path, init = {}) {
  const request = new Request(new URL(path, "http://localhost"), init);

  return worker.fetch(
    request,
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("requires login and server-renders the authenticated NSE dashboard", async () => {
  const worker = await loadWorker();

  const anonymousResponse = await fetchFromWorker(worker, "/", {
    headers: { accept: "text/html" },
    redirect: "manual",
  });
  assert.ok([302, 303, 307, 308].includes(anonymousResponse.status));
  assert.match(anonymousResponse.headers.get("location") ?? "", /\/login$/);

  const loginPageResponse = await fetchFromWorker(worker, "/login", {
    headers: { accept: "text/html" },
  });
  assert.equal(loginPageResponse.status, 200);
  const loginHtml = await loginPageResponse.text();
  assert.match(loginHtml, /Sign in/);
  assert.match(loginHtml, /OpenDelta/);
  assert.match(loginHtml, /₹/);
  assert.doesNotMatch(loginHtml, /Vento NSE/);
  assert.match(loginHtml, /Username/);
  assert.match(loginHtml, /Password/);

  const loginResponse = await fetchFromWorker(worker, "/api/login", {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      "x-forwarded-proto": "https",
    },
    body: new URLSearchParams({
      username: process.env.APP_USERNAME,
      password: process.env.APP_PASSWORD,
    }),
    redirect: "manual",
  });
  assert.equal(loginResponse.status, 303);
  assert.match(loginResponse.headers.get("location") ?? "", /\/$/);
  assert.match(loginResponse.headers.get("set-cookie") ?? "", /HttpOnly/);
  assert.match(loginResponse.headers.get("set-cookie") ?? "", /Secure/);

  const sessionCookie = (loginResponse.headers.get("set-cookie") ?? "").split(";", 1)[0];
  const response = await fetchFromWorker(worker, "/", {
    headers: { accept: "text/html", cookie: sessionCookie },
  });
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>NSE RSI Dashboard/);
  assert.match(html, /OpenDelta/);
  assert.match(html, /₹/);
  assert.doesNotMatch(html, /Vento NSE/);
  assert.doesNotMatch(html, /NSE equity monitor|Find momentum setups/);
  assert.match(html, /RSI 20–30/);
  assert.match(html, /RSI 30–40/);
  assert.match(html, /RSI 40–50/);
  assert.match(html, /RSI (?:>|&gt;) 50/);
  assert.match(html, /RSI slicer/);
  assert.match(html, /Minimum current RSI/);
  assert.match(html, /Maximum current RSI/);
  assert.match(html, /Price slicer/);
  assert.match(html, /Minimum current price/);
  assert.match(html, /Maximum current price/);
  assert.match(html, /Dhan market data/);
  assert.match(html, /Refresh all NSE data from Dhan/);
  assert.match(html, /\d{2} [A-Z][a-z]{2} \d{2}:\d{2} (?:AM|PM)/);
  assert.doesNotMatch(html, /symbols refreshed/);
  assert.match(html, /Add NSE symbol/);
  assert.doesNotMatch(html, /Export CSV/);
  assert.doesNotMatch(html, /NSE ready/);
  assert.doesNotMatch(html, /All prices|Extra large/);
  assert.match(html, /IST/);
  assert.doesNotMatch(html, /Symbol explorer|symbols match your current view/);
  assert.match(html, /Yesterday RSI/);
  assert.match(html, /Current RSI/);
  assert.match(html, /Yesterday price/);
  assert.match(html, /Current close/);
  assert.match(html, /Change \(₹\)/);
  assert.doesNotMatch(html, /[+−]₹\d/);
  assert.match(html, /Recent levels/);
  assert.match(html, /Confirmed 1-day pivots/);
  assert.doesNotMatch(html, /Awaiting confirmed daily pivots/);
  assert.match(html, /24h volume/);
  assert.doesNotMatch(html, />2h volume<|>4h volume</);
  assert.match(html, /LUPIN/);
  assert.match(html, /Backtest/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape/);

  const anonymousBacktest = await fetchFromWorker(worker, "/backtest", {
    headers: { accept: "text/html" },
    redirect: "manual",
  });
  assert.ok([302, 303, 307, 308].includes(anonymousBacktest.status));

  const backtestResponse = await fetchFromWorker(worker, "/backtest", {
    headers: { accept: "text/html", cookie: sessionCookie },
  });
  assert.equal(backtestResponse.status, 200);
  const backtestHtml = await backtestResponse.text();
  assert.match(backtestHtml, /Historical backtest/);
  assert.match(backtestHtml, /OpenDelta/);
  assert.match(backtestHtml, /₹/);
  assert.doesNotMatch(backtestHtml, /Vento NSE/);
  assert.match(backtestHtml, /Run backtest/);
  // EMA/VWAP Strong Buy is the only launchable strategy; the retired selectors are gone.
  assert.match(backtestHtml, /EMA\/VWAP Strong Buy/);
  assert.doesNotMatch(backtestHtml, />RSI Range Strategy</);
  assert.doesNotMatch(backtestHtml, />RSI Recovery Scalping</);
  assert.doesNotMatch(backtestHtml, />Top-5 Opening Range Breakout</);
  assert.doesNotMatch(backtestHtml, /Buy RSI range/);
  assert.doesNotMatch(backtestHtml, /Sell RSI range/);
  assert.match(backtestHtml, /Strong Buy entry/);
  assert.match(backtestHtml, /5m/);
  assert.match(backtestHtml, /4h/);
  assert.match(backtestHtml, /1d/);
  assert.match(backtestHtml, /LUPIN/);

  const anonymousSignals = await fetchFromWorker(worker, "/signals", {
    headers: { accept: "text/html" },
    redirect: "manual",
  });
  assert.ok([302, 303, 307, 308].includes(anonymousSignals.status));

  const signalsResponse = await fetchFromWorker(worker, "/signals", {
    headers: { accept: "text/html", cookie: sessionCookie },
  });
  assert.equal(signalsResponse.status, 200);
  const signalsHtml = await signalsResponse.text();
  assert.match(signalsHtml, /Signal time/);
  assert.match(signalsHtml, /Entry datetime/);
  assert.match(signalsHtml, /Take profit/);
  assert.match(signalsHtml, /Crypto &amp; metals/);
  assert.doesNotMatch(signalsHtml, /class="global-header"/);

  const anonymousCryptoBacktest = await fetchFromWorker(worker, "/backtest/crypto", {
    headers: { accept: "text/html" },
    redirect: "manual",
  });
  assert.ok([302, 303, 307, 308].includes(anonymousCryptoBacktest.status));

  const cryptoBacktestResponse = await fetchFromWorker(worker, "/backtest/crypto", {
    headers: { accept: "text/html", cookie: sessionCookie },
  });
  assert.equal(cryptoBacktestResponse.status, 200);
  const cryptoBacktestHtml = await cryptoBacktestResponse.text();
  assert.match(cryptoBacktestHtml, /Crypto &amp; metals backtest/);
  assert.match(cryptoBacktestHtml, /LIVE ORDERS DISABLED/);
  assert.match(cryptoBacktestHtml, /Search catalog/);
  assert.match(cryptoBacktestHtml, /Trend Pullback Recovery/);

  const cryptoSignalsResponse = await fetchFromWorker(worker, "/signals/crypto", {
    headers: { accept: "text/html", cookie: sessionCookie },
  });
  assert.equal(cryptoSignalsResponse.status, 200);
  const cryptoSignalsHtml = await cryptoSignalsResponse.text();
  assert.match(cryptoSignalsHtml, /Crypto &amp; metals signals/);
  assert.match(cryptoSignalsHtml, /Completed-candle paper signals/);

  const universeResponse = await fetchFromWorker(worker, "/signals?view=universe", {
    headers: { accept: "text/html", cookie: sessionCookie },
  });
  assert.equal(universeResponse.status, 200);
  assert.match(await universeResponse.text(), /Live Signal Universe/);

  const anonymousApi = await fetchFromWorker(worker, "/api/backtest", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ symbols: ["LUPIN"] }),
  });
  assert.equal(anonymousApi.status, 401);

  const anonymousCryptoApi = await fetchFromWorker(worker, "/api/crypto?action=instruments");
  assert.equal(anonymousCryptoApi.status, 401);
});

test("ships all synchronized NSE symbols without starter dependencies", async () => {
  const [dataText, packageText, dashboardText, backtestText, recoveryResultsText, featureAnalysisText, liveUniverseText, liveSignalsText, platformChromeText, backtestApiText, recoveryAnalysisApiText, liveUniverseApiText, liveSignalsApiText, marketRefreshApiText, marketDataText, stylesText, layoutText, liveCsv, syncDataText] = await Promise.all([
    readFile(new URL("../app/data/nse-data.json", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
    readFile(new URL("../app/dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/backtest/backtest-dashboard.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/backtest/recovery-results.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/backtest/feature-analysis.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/signals/live-universe.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/signals/signals-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/platform/platform-chrome.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/backtest/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/recovery-analysis/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/live-universe/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/live-signals/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/market-data/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/market-data.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../public/live/nse_symbols_rsi_volume.csv", import.meta.url), "utf8"),
    readFile(new URL("../scripts/sync-data.mjs", import.meta.url), "utf8"),
  ]);

  const data = JSON.parse(dataText);
  assert.equal(data.totalSymbols, 750);
  assert.equal(data.rows.length, 750);
  assert.equal(new Set(data.rows.map((row) => row.symbol)).size, 750);
  assert.match(data.generatedAt, /\+05:30$/);

  const lupin = data.rows.find((row) => row.symbol === "LUPIN");
  assert.ok(lupin);
  assert.equal(typeof lupin.previous_rsi_14, "number");
  assert.equal(typeof lupin.rsi_14, "number");
  assert.equal(typeof lupin.previous_close, "number");
  assert.equal(typeof lupin.entry_price, "number");
  assert.ok(!("volume_2h" in lupin));
  assert.ok(!("volume_4h" in lupin));
  assert.doesNotMatch(packageText, /react-loading-skeleton/);
  assert.match(packageText, /"lucide-react"/);
  assert.doesNotMatch(dashboardText, /className="sidebar"|symbol-avatar/);
  assert.doesNotMatch(
    dashboardText,
    /NSE equity monitor|RSI market dashboard|Find momentum setups|Symbol explorer|symbols match your current view/,
  );
  assert.match(dashboardText, /search-field header-search/);
  assert.doesNotMatch(dashboardText, /DashboardView|handleViewChange|BarChart3|Gauge/);
  assert.doesNotMatch(dashboardText, /volume_2h|volume_4h/);
  assert.match(dashboardText, /return value > 50/);
  assert.match(dashboardText, /timeZone: "Asia\/Kolkata"/);
  assert.match(dashboardText, /stock\.rsi_14 >= rsiSliderMin/);
  assert.match(dashboardText, /stock\.rsi_14 <= rsiSliderMax/);
  assert.doesNotMatch(dashboardText, /priceRanges|inPriceRange|All prices|Extra large/);
  assert.match(dashboardText, /priceFromSlider/);
  assert.match(dashboardText, /response\.headers\.get\("last-modified"\)/);
  assert.match(dashboardText, /isMarketDataStale/);
  assert.match(dashboardText, /Market data has missed its expected refresh/);
  assert.match(syncDataText, /stat\(sourceFile\)/);
  assert.match(syncDataText, /format\(sourceStats\.mtime\)/);
  assert.match(dashboardText, /className="global-header"/);
  assert.match(dashboardText, /\/api\/market-data\?format=csv/);
  assert.match(dashboardText, /cache: "no-store"/);
  assert.match(dashboardText, /&refresh=\$\{Date\.now\(\)\}/);
  assert.doesNotMatch(dashboardText, /&download=\$\{Date\.now\(\)\}/);
  assert.doesNotMatch(dashboardText, /Export CSV/);
  assert.match(dashboardText, /DATA_REFRESH_INTERVAL_MS = 60 \* 60 \* 1_000/);
  assert.match(dashboardText, /STATUS_REFRESH_INTERVAL_MS = 10_000/);
  assert.match(dashboardText, /Refresh all NSE data from Dhan/);
  assert.match(dashboardText, /dhan-refresh-control/);
  assert.match(dashboardText, /Refreshing \$\{status\.processedSymbols\}\/\$\{status\.totalSymbols\} symbols/);
  assert.match(dashboardText, /fetch\("\/api\/market-data"/);
  assert.match(dashboardText, /nextMarketSession === "CLOSED" \? "MARKET_CLOSED"/);
  assert.match(marketRefreshApiText, /getSessionUser/);
  assert.match(marketRefreshApiText, /\/market-data\/status/);
  assert.match(marketRefreshApiText, /\/market-data\/csv/);
  assert.match(marketRefreshApiText, /\/market-data\/refresh/);
  assert.match(dashboardText, /support_1_price/);
  assert.match(dashboardText, /resistance_2_time/);
  assert.match(dashboardText, /href="\/backtest"/);
  assert.doesNotMatch(dashboardText, /from "next\/link"/);
  assert.match(dashboardText, /<a className="nav-item" href="\/backtest">/);
  assert.match(dashboardText, /href="\/signals"/);
  assert.match(backtestText, /selectedSymbols\.length >= 10/);
  assert.doesNotMatch(backtestText, /from "next\/link"/);
  assert.match(backtestText, /<a className="nav-item" href="\/">/);
  assert.match(backtestText, /href="\/signals"/);
  assert.match(backtestText, /Signals execute at the next candle open/);
  assert.match(backtestText, /Performance summary/);
  assert.match(backtestText, /NIFTY 50/);
  assert.match(backtestText, /symbolsToRun = useAllSymbols \? availableSymbols : selectedSymbols/);
  assert.match(backtestText, /const batchSize = 10/);
  assert.match(backtestText, /const body = await result\.text\(\)/);
  assert.match(backtestText, /returned an unreadable response near/);
  assert.doesNotMatch(backtestText, /await result\.json\(\)/);
  assert.match(backtestText, /All \{availableSymbols\.length\} symbols/);
  // Retired strategies keep their names for saved-history labels and read-only views,
  // but EMA/VWAP Strong Buy is the only one that can be switched to and launched.
  assert.match(backtestText, /RSI Range Strategy/);
  assert.match(backtestText, /RSI Recovery Scalping/);
  assert.match(backtestText, /Top-5 Opening Range Breakout/);
  assert.match(backtestText, /const LAUNCHABLE_STRATEGY_MODE = "ema_vwap_strong_buy"/);
  assert.match(backtestText, /switchStrategy\(LAUNCHABLE_STRATEGY_MODE\)/);
  assert.doesNotMatch(backtestText, /switchStrategy\("rsi_range"\)/);
  assert.doesNotMatch(backtestText, /switchStrategy\("rsi_recovery"\)/);
  assert.doesNotMatch(backtestText, /switchStrategy\(TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY\)/);
  assert.match(backtestText, /top5OpeningRangeBreakoutConfiguration/);
  assert.doesNotMatch(backtestText, />Market-Aligned VWAP Pullback Scalper<\/button>/);
  assert.doesNotMatch(backtestText, /vwapPullbackConfiguration:/);
  assert.match(backtestText, /Retired strategy — cannot run again/);
  assert.match(backtestText, /strategyMode === "rsi_range"/);
  assert.match(backtestText, /Minimum confirmations must be between 0 and/);
  assert.match(backtestText, /minimumConfirmations > enabledConfirmations/);
  assert.match(backtestText, /rsiLength,/);
  assert.match(backtestText, /rsiArmLow,/);
  assert.match(backtestText, /rsiRecovery,/);
  assert.match(backtestText, /executionModel,/);
  assert.match(backtestText, /mergeRecoveryResponses/);
  assert.match(backtestText, /<RecoveryResults response=\{response\}/);
  assert.match(backtestText, /Every fresh RSI arm\/recovery cycle is recorded independently/);
  assert.doesNotMatch(backtestText, /one active position per symbol/);
  assert.match(backtestText, /Exit model/);
  assert.match(backtestText, /Legacy fixed target/);
  assert.match(backtestText, /Fixed TP and SL/);
  assert.match(backtestText, /ATR dynamic TP and SL/);
  assert.match(backtestText, /RSI profitable exit with risk control/);
  assert.match(backtestText, /Minimum profitable exit/);
  assert.match(backtestText, /Profit-exit RSI/);
  assert.match(backtestText, /Hard stop loss/);
  assert.match(backtestText, /RSI exit execution model/);
  assert.match(backtestText, /Compare RSI exit settings/);
  assert.match(backtestText, /setRsiArmLow\(20\)/);
  assert.match(backtestText, /minimumProfitPct,/);
  assert.match(backtestText, /hardStopLossPct,/);
  assert.match(backtestText, /Stop ATR multiplier/);
  assert.match(backtestText, /Reward:risk/);
  assert.match(backtestText, /Rupee risk budget/);
  assert.match(backtestText, /Optimize ATR exits/);
  assert.match(backtestText, /Quantity per trade/);
  assert.match(backtestText, /Maximum open lots/);
  assert.match(backtestText, /Maximum holding trading sessions/);
  assert.match(backtestText, /Advanced settings/);
  assert.match(backtestText, /exitProtectionEnabled,/);
  assert.match(backtestText, /quantityPerTrade,/);
  assert.match(backtestText, /maxOpenLotsPerSymbol,/);
  assert.match(backtestText, /maxHoldingTradingDays,/);
  assert.match(recoveryResultsText, /BUY Signals/);
  assert.match(recoveryResultsText, /Open Signals/);
  assert.match(recoveryResultsText, /Max Concurrent Signals/);
  assert.match(recoveryResultsText, /Max Concurrent Same Symbol/);
  assert.match(recoveryResultsText, /Signal backtest, not a portfolio backtest/);
  assert.match(recoveryResultsText, /Open signal observations/);
  assert.match(recoveryResultsText, /maximumConcurrentOpenSignals/);
  assert.match(recoveryResultsText, /trade\.tradeId/);
  assert.match(recoveryResultsText, /Signal-quality ranking/);
  assert.match(recoveryResultsText, /quality score:/i);
  assert.match(recoveryResultsText, /trades\.csv/);
  assert.match(recoveryResultsText, /symbol_summary\.csv/);
  assert.match(recoveryResultsText, /open_positions\.csv/);
  assert.match(recoveryResultsText, /slice\(0, visibleCount\)/);
  assert.match(recoveryResultsText, /Show 100 more symbols/);
  assert.match(recoveryResultsText, /Overview/);
  assert.match(recoveryResultsText, /Feature Analysis/);
  assert.match(recoveryResultsText, /SKIPPED_MAX_OPEN_LOTS/);
  assert.match(recoveryResultsText, /summary\.buySignals/);
  assert.match(recoveryResultsText, /summary\.stillOpen/);
  assert.match(recoveryResultsText, /summary\.executedTrades/);
  assert.match(recoveryResultsText, /summary\.openPositions/);
  assert.match(recoveryResultsText, /ATR at entry/);
  assert.match(recoveryResultsText, /Stop loss/);
  assert.match(recoveryResultsText, /summary\.winRate/);
  assert.match(recoveryResultsText, /RSI profit exits/);
  assert.match(recoveryResultsText, /RSI overbought exits/);
  assert.match(recoveryResultsText, /Maximum consecutive losses/);
  assert.match(recoveryResultsText, /of \{position\.confirmationsEnabled \?\? 3\} passed/);
  assert.match(featureAnalysisText, /Entry-time analysis only/);
  assert.match(featureAnalysisText, /Cliff&apos;s delta/);
  assert.match(featureAnalysisText, /Features are frozen on the closed BUY signal candle/);
  assert.match(featureAnalysisText, /confirmationCombination/);
  assert.match(featureAnalysisText, /timeOfDayBucket/);
  assert.doesNotMatch(featureAnalysisText, /calculateRecovery|calculateRSI|calculateEMA/);
  assert.match(backtestText, /onWheel=/);
  assert.match(backtestText, /onPointerMove=/);
  assert.match(backtestText, /Price \(INR\)/);
  assert.match(backtestText, /Date and time \(IST\)/);
  assert.match(backtestText, /Scroll through chart dates/);
  assert.match(stylesText, /\.chart-navigator/);
  assert.match(backtestApiText, /getSessionUser/);
  assert.match(backtestApiText, /BACKTEST_SERVICE_URL/);
  assert.match(backtestApiText, /action === "optimize-atr"/);
  assert.match(backtestApiText, /\/backtest\/optimize-atr/);
  assert.match(backtestApiText, /action === "compare-rsi-exits"/);
  assert.match(backtestApiText, /\/backtest\/compare-rsi-exits/);
  assert.match(recoveryAnalysisApiText, /getSessionUser/);
  assert.match(recoveryAnalysisApiText, /BACKTEST_SERVICE_URL/);
  assert.match(recoveryAnalysisApiText, /\[PROXY_TOKEN_HEADER\] = proxyToken/);
  assert.match(liveUniverseText, /Live Signal Universe/);
  assert.match(liveUniverseText, /Number of symbols/);
  assert.match(liveUniverseText, /Minimum share price/);
  assert.match(liveUniverseText, /Maximum share price/);
  assert.match(liveUniverseText, /Minimum historical BUY observations/);
  assert.match(liveUniverseText, /Rebuild universe preview/);
  assert.match(liveUniverseText, /Save & Freeze/);
  assert.match(liveUniverseText, /Below Top-N/);
  assert.match(liveUniverseText, /Price excluded/);
  assert.match(liveUniverseText, /manualPins/);
  assert.match(liveUniverseText, /manualExclusions/);
  assert.match(liveUniverseText, /\/api\/live-universe\?action=export/);
  assert.doesNotMatch(liveUniverseText, /feature_atr_pct|placeOrder|marketOrder/);
  assert.match(liveUniverseApiText, /getSessionUser/);
  assert.match(liveUniverseApiText, /BACKTEST_SERVICE_URL/);
  assert.match(liveUniverseApiText, /\[PROXY_TOKEN_HEADER\] = proxyToken/);
  assert.match(liveUniverseApiText, /\/live-universe\/preview/);
  assert.match(liveUniverseApiText, /\/live-universe\/save/);
  assert.match(liveSignalsText, /Signal time/);
  assert.match(liveSignalsText, /Take profit/);
  assert.match(liveSignalsText, /SIGNAL_REFRESH_INTERVAL_MS = 10_000/);
  assert.match(liveSignalsText, /document\.visibilityState === "visible"/);
  assert.doesNotMatch(liveSignalsText, /className="global-header"/);
  assert.match(platformChromeText, /OVERVIEW_REFRESH_INTERVAL_MS = 15_000/);
  assert.match(platformChromeText, /window\.addEventListener\("focus", refreshVisible\)/);
  assert.match(platformChromeText, /overviewUnavailable \? "UNAVAILABLE"/);
  assert.doesNotMatch(liveSignalsText, /className="snapshot-pill"/);
  assert.doesNotMatch(liveSignalsText, /placeOrder|marketOrder|place_order/);
  assert.doesNotMatch(liveUniverseText, /className="snapshot-pill"/);
  assert.match(liveSignalsApiText, /getSessionUser/);
  assert.match(liveSignalsApiText, /\/live-signals\/settings/);
  assert.match(liveSignalsApiText, /\/paper-trades/);
  assert.match(marketDataText, /parseMarketCsv/);
  assert.match(marketDataText, /support_1_price/);
  assert.equal(liveCsv.trim().split(/\r?\n/).length - 1, 750);
  assert.doesNotMatch(stylesText, /overflow-x:\s*auto|min-width:\s*1030px/);
  assert.match(layoutText, /Manrope/);
  assert.match(stylesText, /\.backtest-shell\s*\{[^}]*overflow-x:\s*clip/s);
  assert.match(stylesText, /\.backtest-shell\s*\{[^}]*font-size:\s*16px/s);
  assert.match(stylesText, /\.recovery-result-tabs button\s*\{[^}]*min-height:\s*44px/s);
  assert.match(stylesText, /\.recovery-result-tabs button:focus-visible/);
  assert.match(stylesText, /\.advanced-settings/);
  assert.match(stylesText, /\.site-shell\[data-theme="light"\]/);
  assert.match(stylesText, /\.backtest-shell \.feature-filter-actions button\s*\{[^}]*min-height:\s*44px/s);
  assert.match(stylesText, /\.site-shell\s*\{[^}]*font-size:\s*16px/s);
  assert.match(stylesText, /\.site-shell \.nav-item\s*\{[^}]*font-size:\s*15px/s);
  assert.match(stylesText, /\.signals-shell \.health-item span\s*\{[^}]*font-size:\s*11px/s);
  assert.match(stylesText, /\.signals-shell \.signal-metric > strong\s*\{[^}]*font-size:\s*13px/s);
  assert.match(stylesText, /\.site-shell table td\s*\{[^}]*font-size:\s*14px/s);
});
