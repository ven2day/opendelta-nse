import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import test from "node:test";

process.env.APP_USERNAME = "test-admin";
process.env.APP_PASSWORD = "test-password-123";
process.env.AUTH_SECRET = "test-secret-that-is-at-least-32-characters-long";

const NAVIGATION = ["Dashboard", "Watchlist", "Backtest", "Research", "Indicators", "Signals", "Paper Trading", "Operations", "Strategies"];
const ROUTES = [
  { path: "/", title: "Dashboard" },
  { path: "/screener", title: "Watchlist" },
  { path: "/backtest", title: "Backtest" },
  { path: "/research", title: "Research Lab" },
  { path: "/indicators", title: "Indicators" },
  { path: "/signals", title: "Signals" },
  { path: "/paper-trading", title: "Paper Trading" },
  { path: "/operations", title: "Operations" },
  { path: "/settings", title: "Strategies" },
];

async function loadWorker() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-unified`);
  const { default: worker } = await import(workerUrl.href);
  return worker;
}

function fetchFromWorker(worker, path, init = {}) {
  const request = new Request(new URL(path, "http://localhost"), init);
  return worker.fetch(
    request,
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

async function login(worker) {
  const response = await fetchFromWorker(worker, "/api/login", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", "x-forwarded-proto": "https" },
    body: new URLSearchParams({ username: process.env.APP_USERNAME, password: process.env.APP_PASSWORD }),
    redirect: "manual",
  });
  assert.equal(response.status, 303);
  return (response.headers.get("set-cookie") ?? "").split(";", 1)[0];
}

function navigationLabels(html) {
  const nav = html.match(/<nav class="platform-topnav"[\s\S]*?<\/nav>/)?.[0] ?? "";
  return Array.from(nav.matchAll(/<a[^>]*aria-label="([^"]+)"/g), (match) => match[1]);
}

function marketSelectorLinks(html) {
  const selector = html.match(/<div class="platform-market-switch"[\s\S]*?<\/div>/)?.[0] ?? "";
  return Array.from(selector.matchAll(/href="([^"]+)"/g), (match) => match[1].replace(/&amp;/g, "&"));
}

test("every unified route requires login and renders one topbar market selector", async () => {
  const worker = await loadWorker();
  const cookie = await login(worker);

  for (const route of ROUTES) {
    const anonymous = await fetchFromWorker(worker, route.path, { headers: { accept: "text/html" }, redirect: "manual" });
    assert.ok([302, 303, 307, 308].includes(anonymous.status), `${route.path} must redirect anonymous visitors`);
    assert.match(anonymous.headers.get("location") ?? "", /\/login$/);

    const response = await fetchFromWorker(worker, route.path, { headers: { accept: "text/html", cookie } });
    assert.equal(response.status, 200, `${route.path} should render for a signed-in user`);
    const html = await response.text();
    assert.match(html, new RegExp(`<title>${route.title.replace(/ /g, "\\s")}`), `${route.path} title`);
    assert.deepEqual(navigationLabels(html), NAVIGATION, `${route.path} navigation`);
    assert.doesNotMatch(html, /platform-sidebar|platform-menu|platform-backdrop|data-navigation-open/, `${route.path} has no sidebar drawer`);
    assert.match(html, /data-ui-version="unified-v2"/);
    // The topbar clock is seeded after mount so server and client markup match (no hydration error #418).
    assert.match(html, /--:--:--/);
    assert.doesNotMatch(html, /Paper research only|Broker disabled/);
    assert.doesNotMatch(html, /class="global-header"/, `${route.path} must not embed the legacy header`);
    assert.deepEqual(marketSelectorLinks(html), [`${route.path}?market=NSE`, `${route.path}?market=CRYPTO`], `${route.path} topbar market selector`);
    assert.match(html, /aria-label="Active market"[\s\S]*?class="active"[^>]*href="[^"]*market=NSE"/, `${route.path} defaults to NSE`);
    assert.doesNotMatch(html, /<nav class="quant-market-tabs" aria-label="Market selector">/, `${route.path} must not duplicate the topbar market selector`);
  }

  const cryptoDashboard = await fetchFromWorker(worker, "/screener?market=CRYPTO", { headers: { accept: "text/html", cookie } });
  assert.equal(cryptoDashboard.status, 200);
  const cryptoHtml = await cryptoDashboard.text();
  assert.match(cryptoHtml, /class="active"[^>]*href="\/screener\?market=CRYPTO"/);
  assert.match(cryptoHtml, /href="\/signals\?market=CRYPTO"/, "navigation carries the selected market");

  const paper = await fetchFromWorker(worker, "/paper-trading", { headers: { accept: "text/html", cookie } });
  const paperHtml = await paper.text();
  assert.match(paperHtml, /Paper only/);
  assert.match(paperHtml, /broker execution disabled/i);
  assert.doesNotMatch(paperHtml, /placeOrder|marketOrder|place_order/);

  const settings = await fetchFromWorker(worker, "/settings", { headers: { accept: "text/html", cookie } });
  const settingsHtml = await settings.text();
  assert.match(settingsHtml, /Strategy control/);
  assert.match(settingsHtml, /Secure exchange connections/);
  assert.match(settingsHtml, /Live trading disabled/);
  assert.match(settingsHtml, /Live execution foundation/);
  assert.doesNotMatch(settingsHtml, /Global minimum price|Global maximum price/);
  assert.doesNotMatch(settingsHtml, /\/legacy\//, "settings no longer links to retired pages");

  // /admin was retired with the legacy shell and now redirects to the unified Settings workspace.
  const admin = await fetchFromWorker(worker, "/admin", { headers: { accept: "text/html", cookie }, redirect: "manual" });
  assert.ok([302, 303, 307, 308].includes(admin.status), "/admin must redirect");
  assert.match(admin.headers.get("location") ?? "", /\/settings$/);
});

test("the v2 proxy refuses anonymous and malformed requests before touching the service", async () => {
  const worker = await loadWorker();
  const anonymous = await fetchFromWorker(worker, "/api/v2/dashboard?market=NSE");
  assert.equal(anonymous.status, 401);
  const anonymousInstrument = await fetchFromWorker(worker, "/api/platform?action=add-instrument", { method: "POST" });
  assert.equal(anonymousInstrument.status, 401);

  const cookie = await login(worker);
  const traversal = await fetchFromWorker(worker, "/api/v2/..%2Fplatform", { headers: { cookie } });
  assert.equal(traversal.status, 404);

  const unconfigured = await fetchFromWorker(worker, "/api/v2/dashboard?market=NSE", { headers: { cookie } });
  assert.equal(unconfigured.status, 503);
  assert.match((await unconfigured.json()).detail, /not configured/);
  const unconfiguredInstrument = await fetchFromWorker(worker, "/api/platform?action=add-instrument", {
    method: "POST",
    headers: { "content-type": "application/json", cookie },
    body: JSON.stringify({ market: "CRYPTO", symbol: "BTC-USDT" }),
  });
  assert.equal(unconfiguredInstrument.status, 503);
});

test("TradingView has one narrow public JSON ingress", async () => {
  const worker = await loadWorker();
  const wrongType = await fetchFromWorker(worker, "/api/tradingview/webhook", {
    method: "POST",
    headers: { "content-type": "text/plain" },
    body: "not-json",
  });
  assert.equal(wrongType.status, 415);

  const unconfigured = await fetchFromWorker(worker, "/api/tradingview/webhook", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ schemaVersion: "1" }),
  });
  assert.equal(unconfigured.status, 503);
  assert.match((await unconfigured.json()).detail, /not configured/);
});

test("the unified navigation and proxy are wired exactly once", async () => {
  const [chrome, proxy, client] = await Promise.all([
    readFile(new URL("../app/platform/platform-chrome.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/v2/[...path]/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/platform/v2-client.ts", import.meta.url), "utf8"),
  ]);
  const labels = Array.from(chrome.matchAll(/label: "([^"]+)"/g), (match) => match[1]);
  assert.deepEqual(labels, NAVIGATION);
  assert.match(chrome, /OVERVIEW_REFRESH_INTERVAL_MS = 15_000/);
  assert.match(proxy, /getSessionUser/);
  assert.match(proxy, /BACKTEST_SERVICE_URL/);
  assert.match(proxy, /AbortSignal\.timeout\(UPSTREAM_TIMEOUT_MS\)/);
  assert.match(proxy, /UPSTREAM_TIMEOUT_MS = 30_000/);
  assert.match(proxy, /export async function (GET|POST|DELETE)/);
  assert.match(client, /export async function v2Get</);
  assert.match(client, /export async function v2Post</);
  assert.match(client, /export async function v2Delete</);
});

test("dashboard presents a compact live strategy lifecycle refreshed every ten seconds", async () => {
  const [dashboard, styles] = await Promise.all([
    readFile(new URL("../app/dashboard-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/platform/trading-terminal.css", import.meta.url), "utf8"),
  ]);
  assert.match(dashboard, /DASHBOARD_REFRESH_MS = 10_000/);
  assert.match(dashboard, /Live strategy lifecycle/);
  assert.match(dashboard, /Market data/);
  assert.match(dashboard, /Signal check/);
  assert.match(dashboard, /Paper trade/);
  assert.match(dashboard, /cycle\?\.lastSignalId \? lifecycle\.paper : stages\.paper/);
  assert.match(styles, /\.quant-lifecycle-rail\s*\{[^}]*grid-template-columns:\s*repeat\(3,/s);
});

test("production verification follows the Strategies navigation label", async () => {
  const script = new URL("../deploy/verify-container.sh", import.meta.url);
  const verification = await readFile(script, "utf8");
  assert.match(verification, /'Paper Trading' Operations Strategies/);
  assert.doesNotMatch(verification, /'Paper Trading' Settings/);
  if (process.platform !== "win32") {
    assert.notEqual((await stat(script)).mode & 0o111, 0, "deployment verification must remain executable");
  }
});

test("the backtest run ticket uses defaults with one collapsed JSON override", async () => {
  const source = await readFile(new URL("../app/backtest/backtest-workspace.tsx", import.meta.url), "utf8");
  assert.match(source, /<details className="quant-backtest-config">/);
  assert.doesNotMatch(source, /<details className="quant-backtest-config" open/);
  assert.match(source, />Copy JSON</);
  assert.match(source, /aria-label="Backtest configuration JSON"/);
  assert.match(source, /hasConfigurationOverride \? "Custom JSON" : "Defaults"/);
  assert.match(source, /validateConfigValues\(configuration\.strategy, strategySchema/);
  assert.match(source, /validateConfigValues\(configuration\.execution, executionSchema/);
  assert.doesNotMatch(source, /<SchemaForm/);
  assert.doesNotMatch(source, /Universe symbols/);
  assert.equal((source.match(/quant-backtest-run-grid/g) ?? []).length, 1, "desktop setup uses one compact control grid");
  assert.match(source, /aria-label="Filter trades by symbol"/);
  assert.match(source, /aria-label="Filter trades by status"/);
  assert.match(source, /sort: tradeSort, direction: tradeDirection/);
  assert.match(source, /<SortableHeading label="P&amp;L"/);
  assert.match(source, /quant-backtest-summary/);
  assert.doesNotMatch(source, /quant-kpi-grid/);
});

test("OPEN uses the warning colour while completed targets remain green", async () => {
  const source = await readFile(new URL("../app/platform/format.ts", import.meta.url), "utf8");
  const successStatuses = source.match(/if \(\[([^\]]+)\]\.includes\(value\)\) return "good"/)?.[1] ?? "";
  const warningStatuses = source.match(/if \(\[([^\]]+)\]\.includes\(value\)\) return "warn"/)?.[1] ?? "";
  assert.doesNotMatch(successStatuses, /"OPEN"/);
  assert.match(successStatuses, /"TARGET_HIT"/);
  assert.match(warningStatuses, /"OPEN"/);
});

test("the backtest trade ledger preserves readable outcome columns", async () => {
  const [workspace, styles] = await Promise.all([
    readFile(new URL("../app/backtest/backtest-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/platform/trading-terminal.css", import.meta.url), "utf8"),
  ]);
  assert.match(workspace, /quant-table quant-trades-table/);
  assert.equal((workspace.match(/<col className="quant-trade-/g) ?? []).length, 13);
  assert.match(styles, /\.quant-trades-table\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*1600px;[^}]*table-layout:\s*fixed;/s);
  assert.match(styles, /\.quant-trades-table th,[\s\S]*?white-space:\s*nowrap;/);
  assert.match(workspace, />Current close<\/th>/);
  assert.match(workspace, /trade\.status === "OPEN" \? formatNumber\(trade\.lastPrice\) : "—"/);
  assert.match(workspace, /trade\.status === "OPEN" \? trade\.unrealizedPnl : trade\.netPnl/);
  assert.match(workspace, /trade\.holdingBars != null/);
  assert.match(workspace, /"FIFO net target"/);
});

test("NSE paper positions identify the executable FIFO net target", async () => {
  const workspace = await readFile(new URL("../app/paper-trading/paper-workspace.tsx", import.meta.url), "utf8");
  assert.match(workspace, /"FIFO net target"/);
  assert.match(workspace, /latest completed-candle mark/);
  assert.match(workspace, /v2Post\(`paper\/lots\/\$\{lot\.lotId\}\/close`, \{\}/);
  assert.doesNotMatch(workspace, /Close price for/);
});

test("workspace headers stay compact and the watchlist declares both markets", async () => {
  const paths = [
    "../app/dashboard-workspace.tsx",
    "../app/screener/screener-workspace.tsx",
    "../app/backtest/backtest-workspace.tsx",
    "../app/signals/signals-workspace.tsx",
    "../app/paper-trading/paper-workspace.tsx",
    "../app/settings/settings-workspace.tsx",
  ];
  const sources = await Promise.all(paths.map((path) => readFile(new URL(path, import.meta.url), "utf8")));
  for (const [index, source] of sources.entries()) {
    const header = source.match(/<WorkspaceHeader[\s\S]*?\/>/)?.[0] ?? "";
    assert.ok(header, `${paths[index]} must use WorkspaceHeader`);
    assert.doesNotMatch(header, /\sdescription=/, `${paths[index]} must not add a page-header description`);
  }
  assert.match(sources[1], /NSE & Crypto watchlist/);
  assert.doesNotMatch(sources[1], /Crypto only/);
});

test("the screener presents eligible symbols as a watchlist, not trade calls", async () => {
  const source = await readFile(new URL("../app/screener/screener-workspace.tsx", import.meta.url), "utf8");
  assert.match(source, /className="quant-json-editor"/);
  assert.match(source, /<details className="quant-config-disclosure">/);
  assert.doesNotMatch(source, /<details className="quant-config-disclosure" open/);
  assert.match(source, />Copy JSON</);
  assert.match(source, /aria-label="Screener configuration JSON"/);
  assert.match(source, /validateConfigValues\(overrides, FILTER_SCHEMA/);
  assert.match(source, /Full \{marketLabel\(market\)\} market/);
  assert.doesNotMatch(source, /<SchemaForm/);
  assert.doesNotMatch(source, />Rank by</);
  assert.doesNotMatch(source, />Keep results</);
  assert.doesNotMatch(source, />Manual includes</);
  assert.match(source, /Save and use for signals/);
  assert.match(source, />Always include</);
  assert.match(source, />Always exclude</);
  assert.match(source, /not automatic BUY or SELL calls/);
  assert.match(source, /v2Get<WatchlistProfilesResponse>\("screener\/profiles"/);
  assert.match(source, /aria-label="Watchlist profile version"/);
  assert.match(source, /Save new version/);
  assert.match(source, /profileVersionId: selectedProfileVersionId/);
  assert.match(source, /Existing strategy watchlists were not changed/);
  assert.match(source, /Candidates \(/);
  assert.match(source, /Excluded \(/);
  assert.doesNotMatch(source, />Passed \(/);
  assert.doesNotMatch(source, /<SymbolTags symbols=\{universe\.symbols\}/);
  assert.match(source, /<th className="numeric">Action<\/th>/);
});

test("the six workspaces keep primary work visible and secondary detail collapsed", async () => {
  const [dashboard, screener, signals, paper, settings] = await Promise.all([
    readFile(new URL("../app/dashboard-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/screener/screener-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/signals/signals-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/paper-trading/paper-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/settings/settings-workspace.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(dashboard, /quant-primary-panel/);
  assert.match(dashboard, /section\.recent\.slice\(0, 3\)/);
  assert.match(dashboard, /<details className="quant-secondary-disclosure">/);
  assert.doesNotMatch(dashboard, /quant-kpi-grid/);
  assert.match(screener, /quant-overview-strip/);
  assert.match(signals, /title="Signals"[\s\S]*?<details className="quant-secondary-disclosure">/);
  assert.doesNotMatch(signals, /quant-kpi-grid/);
  assert.match(paper, /quant-portfolio-standalone/);
  assert.doesNotMatch(paper, /quant-kpi-grid/);
  assert.match(settings, /<details className="quant-config-disclosure">/);
  assert.match(settings, /<details className="quant-secondary-disclosure">/);
  assert.doesNotMatch(settings, /<details className="quant-config-disclosure" open/);
});

test("settings is JSON-first and does not duplicate global or market controls", async () => {
  const [source, connections, proxy] = await Promise.all([
    readFile(new URL("../app/settings/settings-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/settings/exchange-connections-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/platform/route.ts", import.meta.url), "utf8"),
  ]);
  assert.match(source, /aria-label="Strategy and paper execution JSON"/);
  assert.match(source, /validateConfigValues\(strategy, strategySchema/);
  assert.match(source, /validateConfigValues\(paperExecution, riskSchema/);
  assert.doesNotMatch(source, /<SchemaForm/);
  assert.doesNotMatch(source, /GlobalPriceRangeForm/);
  assert.doesNotMatch(source, /MARKETS\.map/);
  assert.doesNotMatch(source, /VALR/);
  assert.match(source, /<ExchangeConnectionsPanel \/>/);
  assert.match(connections, /publicMarketData/);
  assert.match(connections, /No private key required/);
  assert.match(source, /aria-label="Strategy mode"/);
  assert.match(source, /\["OFF", "SIGNALS", "PAPER"\]/);
  assert.match(source, /strategies\/\$\{selectedStrategy\.strategyId\}\/deployment/);
  assert.match(source, /strategy-deployments/);
  assert.match(source, /universeId: universeId \|\| null/);
  assert.match(source, />Watchlist</);
  assert.match(source, /Existing paper positions continue to be monitored/);
  assert.match(source, /<Plus size=\{15\} \/>Instrument setup/);
  assert.doesNotMatch(source, /Instrument setup<\/span>[\s\S]*?<details[^>]+open/);
  assert.match(source, /platformPost<InstrumentAddResponse>\("add-instrument", \{ market, symbol \}\)/);
  assert.match(proxy, /market === "NSE" \? "\/market-data\/symbols" : "\/crypto\/instruments"/);
  assert.match(proxy, /\{ provider: "OKX", providerSymbol: symbol \}/);
});

test("Strategy Studio V2 is collapsed and promotion remains backtest-gated", async () => {
  const source = await readFile(new URL("../app/settings/settings-workspace.tsx", import.meta.url), "utf8");
  const schema = await readFile(new URL("../app/platform/schema-form.tsx", import.meta.url), "utf8");
  assert.match(source, /<details className=\{`quant-secondary-disclosure \$\{styles\.studio\}`\}>/);
  assert.match(source, /Strategy Studio V2/);
  assert.match(source, /aria-label="Strategy V2 Python source"/);
  assert.match(source, /strategy-studio\/validate/);
  assert.match(source, /strategy-studio\/sources/);
  assert.match(source, /Backtest it, then approve that exact run for Signals or Paper/);
  assert.match(source, /Backtest-gated/);
  assert.match(schema, /Array\.isArray\(value\).*integer_array/);
  assert.doesNotMatch(source, /styles\.studio\}`\} open/);
});

test("Indicator Studio V2 is separate, immutable and preview-only", async () => {
  const [source, types] = await Promise.all([
    readFile(new URL("../app/indicators/indicator-studio-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/platform/v2-types.ts", import.meta.url), "utf8"),
  ]);
  assert.match(source, /title="Indicator Studio V2"/);
  assert.match(source, /aria-label="Indicator V2 Python source"/);
  assert.match(source, /indicator-studio\/validate/);
  assert.match(source, /Save new version/);
  assert.match(source, /Edit as new/);
  assert.match(source, /\/archive/);
  assert.match(source, /Stored-candle preview/);
  assert.match(source, /Preview cannot create signals or trades/);
  assert.doesNotMatch(source, /Approve for Signals|Approve for Paper|placeOrder|marketOrder/);
  assert.match(types, /export type IndicatorSourceManifest/);
  assert.match(types, /"LINE" \| "HISTOGRAM" \| "BAND" \| "POINTS"/);
});

test("completed V2 backtests expose the same ordered approval workflow", async () => {
  const [source, types] = await Promise.all([
    readFile(new URL("../app/backtest/backtest-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/platform/v2-types.ts", import.meta.url), "utf8"),
  ]);
  assert.match(source, /run\?\.strategySourceId \? "OPENDELTA"/);
  assert.match(source, /Approve for Signals/);
  assert.match(source, /Approve for Paper/);
  assert.match(source, /immutable V2 source/);
  assert.doesNotMatch(source, /Promotion to Signals and Paper remains locked/);
  assert.match(types, /strategySourceId\?: string \| null/);
});

test("completed backtests expose the immutable strategy chart workspace", async () => {
  const [backtest, chart, types] = await Promise.all([
    readFile(new URL("../app/backtest/backtest-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/backtest/strategy-chart-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/platform/v2-types.ts", import.meta.url), "utf8"),
  ]);
  assert.match(backtest, /title="Strategy chart"/);
  assert.match(backtest, /run\?\.status === "COMPLETE"/);
  assert.match(chart, /candlestick strategy chart/);
  assert.match(chart, /<rect x=\{x - model\.candleWidth/);
  assert.match(chart, />BUY<\/text>/);
  assert.match(chart, />ENTRY<\/text>/);
  assert.match(chart, />SELL<\/text>/);
  assert.match(chart, /onPointerMove/);
  assert.match(chart, /trade\.targetPrice/);
  assert.match(chart, /trade\.stopPrice/);
  assert.match(chart, /indicator-studio\/sources/);
  assert.match(types, /export type BacktestChartResponse/);
  assert.doesNotMatch(chart, /placeOrder|marketOrder|Approve for Paper/);
});

test("Research Lab creates immutable grouped backtest variants without deployment actions", async () => {
  const source = await readFile(new URL("../app/research/research-workspace.tsx", import.meta.url), "utf8");
  assert.match(source, /New controlled experiment/);
  assert.match(source, /research\/experiments\/preview/);
  assert.match(source, /research\/experiments\/from-preview/);
  assert.match(source, /Manual variants/);
  assert.match(source, /Grid sweep/);
  assert.match(source, /Add parameter/);
  assert.match(source, /Duplicate .* parameter/);
  assert.match(source, /Preview stale/);
  assert.match(source, /disabled=\{!previewFresh \|\| submitting\}/);
  assert.match(source, /Generated combinations|Deterministic name/);
  assert.match(source, /Full immutable JSON/);
  assert.doesNotMatch(source, /backtests\/\$\{.*\}\/approve|strategy-deployments|paper-trading|Approve for|Deploy/);
});

test("Research Lab completes strategy and walk-forward comparisons", async () => {
  const [research, walkComparison, backtestPage, backtest] = await Promise.all([
    readFile(new URL("../app/research/research-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/research/walk-forward-comparison.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/backtest/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/backtest/backtest-workspace.tsx", import.meta.url), "utf8"),
  ]);
  for (const metric of ["Net P&amp;L", "Drawdown", "Win rate", "Costs", "Completed trades", "Open trades", "Target hits", "Stops", "Expiries", "Average holding", "Exposure", "Failed symbols"]) assert.match(research, new RegExp(metric));
  assert.match(research, /Variant equity curves/);
  assert.match(research, /Return \/ drawdown score/);
  assert.match(research, /Exact immutable configuration/);
  assert.match(research, /MAX_VISIBLE_CURVES = 8/);
  assert.match(research, /Equity curve selection/);
  assert.match(research, /new URLSearchParams\(\{ market, runId: row\.variant\.run\.runId \}\)/);
  assert.match(research, /Rejected trades: unavailable/);
  assert.match(walkComparison, /Training versus unseen comparison/);
  assert.match(walkComparison, /TRAINING/);
  assert.match(walkComparison, /UNSEEN TEST/);
  assert.match(walkComparison, /Exact immutable inputs/);
  assert.match(walkComparison, /Rejected trades: unavailable/);
  assert.doesNotMatch(walkComparison, /Approve for|Deploy strategy|Enable live|Place order/);
  assert.match(backtestPage, /initialRunId=\{parameters\.runId\}/);
  assert.match(backtest, /useState<string \| null>\(initialRunId \?\? null\)/);
});

test("MCP proxy accepts only bounded bearer-authenticated JSON", async () => {
  const worker = await loadWorker();
  const request = JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} });
  const anonymous = await fetchFromWorker(worker, "/api/mcp", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: request,
  });
  assert.equal(anonymous.status, 401);
  const wrongType = await fetchFromWorker(worker, "/api/mcp", {
    method: "POST",
    headers: { authorization: "Bearer odt_example", "content-type": "text/plain" },
    body: request,
  });
  assert.equal(wrongType.status, 415);
  const unconfigured = await fetchFromWorker(worker, "/api/mcp", {
    method: "POST",
    headers: { authorization: "Bearer odt_example", "content-type": "application/json" },
    body: request,
  });
  assert.equal(unconfigured.status, 503);
});

test("Research Lab walk-forward validation separates training from unseen tests and stays research-only", async () => {
  const [workspace, walkForward, types, css] = await Promise.all([
    readFile(new URL("../app/research/research-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/research/walk-forward-section.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/platform/v2-types.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/platform/trading-terminal.css", import.meta.url), "utf8"),
  ]);
  assert.match(workspace, /<WalkForwardSection/);
  assert.match(walkForward, /Anchored/);
  assert.match(walkForward, /Rolling/);
  assert.match(walkForward, /TRAINING/);
  assert.match(walkForward, /UNSEEN TEST/);
  assert.match(walkForward, /research\/walk-forward\/preview/);
  assert.match(walkForward, /research\/walk-forward\/from-preview/);
  assert.match(walkForward, /disabled=\{!previewFresh \|\| submitting\}/);
  assert.match(walkForward, /Candle workload/);
  assert.match(walkForward, /Training chart/);
  assert.match(walkForward, /Unseen chart/);
  assert.match(types, /export type WalkForwardValidation/);
  assert.match(css, /\.research-walk-forward-grid/);
  assert.match(css, /@media \(max-width: 680px\)/);
  assert.doesNotMatch(walkForward, /Approve for|Deploy strategy|Enable live|Place order/);
});

test("AI Research Copilot is explicit, fail-closed, and draft-only in all three studios", async () => {
  const [copilot, strategies, indicators, research] = await Promise.all([
    readFile(new URL("../app/ai/ai-copilot-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/settings/settings-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/indicators/indicator-studio-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/research/research-workspace.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(strategies, /<AICopilotPanel surface="strategy"/);
  assert.match(indicators, /<AICopilotPanel surface="indicator"/);
  assert.match(research, /<AICopilotPanel surface="research"/);
  assert.match(copilot, /response\.label/);
  assert.match(copilot, /Do not send/);
  assert.match(copilot, /Selected trades · send none by default/);
  assert.match(copilot, /Save research draft/);
  assert.match(copilot, /Use in editor/);
  assert.match(copilot, /disabled=\{!status\.data\?\.configured/);
  assert.doesNotMatch(copilot, /Approve for|Deploy strategy|Enable live|Place order|API key/);
});

test("exchange credentials are write-only, encrypted, confirmation-gated, and never enable live trading", async () => {
  const [source, routes, repository, migration] = await Promise.all([
    readFile(new URL("../app/settings/exchange-connections-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../../backend/api/exchange_connection_routes.py", import.meta.url), "utf8"),
    readFile(new URL("../../backend/connections/repository.py", import.meta.url), "utf8"),
    readFile(new URL("../../backend/data/sql/020_secure_exchange_connections.sql", import.meta.url), "utf8"),
  ]);
  assert.match(source, /type="password"/);
  assert.match(source, /Live trading disabled/);
  assert.match(source, /Withdrawal-capable keys are automatically held disabled/);
  assert.match(source, /REPLACE \$\{connection\.provider\}/);
  assert.match(source, /DELETE \$\{connection\.provider\}/);
  assert.doesNotMatch(source, /enable live|approve|deploy strategy/i);
  assert.match(routes, /Exchange credential encryption is not configured/);
  assert.match(repository, /withdrawal permission cannot be enabled/i);
  assert.doesNotMatch(routes, /return .*apiSecret|return .*passphrase/);
  assert.match(migration, /credentials_ciphertext bytea/);
  assert.doesNotMatch(migration, /api_secret\s+(?:text|varchar)|api_key\s+(?:text|varchar)/i);
});

test("live execution is server-gated, idempotent, emergency-stopped, and has no casual order control", async () => {
  const [panel, routes, service, adapters, migration, env] = await Promise.all([
    readFile(new URL("../app/settings/live-execution-panel.tsx", import.meta.url), "utf8"),
    readFile(new URL("../../backend/api/live_execution_routes.py", import.meta.url), "utf8"),
    readFile(new URL("../../backend/live/service.py", import.meta.url), "utf8"),
    readFile(new URL("../../backend/live/adapters.py", import.meta.url), "utf8"),
    readFile(new URL("../../backend/data/sql/021_live_execution_foundation.sql", import.meta.url), "utf8"),
    readFile(new URL("../deploy/opendelta-dhan.env.example", import.meta.url), "utf8"),
  ]);
  assert.match(panel, /Live trading disabled/);
  assert.match(panel, /ACTIVATE EMERGENCY STOP/);
  assert.match(panel, /ENABLE LIVE \$\{item\.strategyId\}/);
  assert.doesNotMatch(panel, />Place order<|>Submit order</);
  assert.match(routes, /APIRouter\(prefix="\/v2\/live-execution"/);
  assert.match(routes, /@router\.post\("\/orders"/);
  assert.match(service, /create_intent/);
  assert.match(service, /idempotency_key/);
  assert.match(service, /ProviderResponseUncertain/);
  assert.match(service, /nse_session_is_open/);
  assert.match(adapters, /class DhanOrderAdapter/);
  assert.match(adapters, /class OkxOrderAdapter/);
  assert.match(adapters, /class ValrOrderAdapter/);
  assert.match(adapters, /ProviderMutationDisabled/);
  assert.match(migration, /CONSTRAINT live_order_intents_deployment_signal UNIQUE/);
  assert.match(migration, /'UNKNOWN'/);
  assert.match(env, /LIVE_TRADING_ENABLED=false/);
  assert.match(env, /LIVE_TRADING_DEPLOYMENT_ALLOWED=false/);
  assert.doesNotMatch(routes, /approve|credential.*decrypt/i);
});

test("production monitoring uses durable leases, append-only audits, and deduplicated alerts", async () => {
  const [workspace, routes, repository, leases, migration] = await Promise.all([
    readFile(new URL("../app/operations/operations-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../../backend/api/monitoring_routes.py", import.meta.url), "utf8"),
    readFile(new URL("../../backend/monitoring/repository.py", import.meta.url), "utf8"),
    readFile(new URL("../../backend/monitoring/leases.py", import.meta.url), "utf8"),
    readFile(new URL("../../backend/data/sql/022_production_monitoring.sql", import.meta.url), "utf8"),
  ]);
  assert.match(workspace, /Production observability/);
  assert.match(workspace, /Worker leases/);
  assert.match(workspace, /Append-only audit history/);
  assert.match(workspace, /Emergency-stop state/);
  assert.match(routes, /prefix="\/v2\/operations"/);
  assert.match(repository, /ON CONFLICT \(worker_type, task_key\) DO UPDATE/);
  assert.match(repository, /cooldown_until/);
  assert.match(leases, /worker-lease-heartbeat/);
  for (const worker of ["MARKET_DATA", "SIGNAL", "BACKTEST", "RESEARCH_EXPERIMENT", "WALK_FORWARD", "PAPER_EXECUTION", "LIVE_RECONCILIATION", "MONITORING"]) {
    assert.match(migration, new RegExp(`'${worker}'`));
  }
  assert.match(migration, /operational_audit_append_only/);
  assert.match(migration, /operational_alerts_active_fingerprint_uq/);
  assert.doesNotMatch(workspace, /apiSecret|passphrase|credentials_ciphertext/);
});

test("agent MCP access is scoped, hashed, bounded, audited, and research-only", async () => {
  const [proxy, routes, gateway, repository, migration] = await Promise.all([
    readFile(new URL("../app/api/mcp/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../../backend/api/agent_routes.py", import.meta.url), "utf8"),
    readFile(new URL("../../backend/agent/mcp.py", import.meta.url), "utf8"),
    readFile(new URL("../../backend/agent/repository.py", import.meta.url), "utf8"),
    readFile(new URL("../../backend/data/sql/023_agent_mcp_access.sql", import.meta.url), "utf8"),
  ]);
  assert.match(proxy, /authorization/);
  assert.match(proxy, /MAX_BODY_BYTES = 65_536/);
  assert.match(routes, /@router\.post\("\/mcp"\)/);
  assert.match(routes, /MCP_ALLOWED_ORIGINS/);
  assert.match(gateway, /MAX_MCP_OUTPUT_BYTES = 524_288/);
  assert.match(gateway, /SCOPE_DENIED/);
  assert.match(gateway, /MCP_AGENT_ACTION/);
  assert.match(repository, /hashlib\.sha256/);
  assert.match(migration, /token_hash char\(64\)/);
  assert.match(migration, /agent_rate_limit_windows/);
  for (const forbidden of ["place_order", "cancel_live_order", "approve_signals", "approve_paper", "read_credentials", "execute_shell"]) {
    assert.doesNotMatch(gateway.match(/TOOLS: tuple[\s\S]*?TOOL_BY_NAME/)?.[0] ?? "", new RegExp(`ToolSpec\\(\\"${forbidden}\\"`));
  }
});

test("signal filters stay collapsed and reason codes are humanized", async () => {
  const source = await readFile(new URL("../app/signals/signals-workspace.tsx", import.meta.url), "utf8");
  assert.match(source, /<details className="quant-filter-menu">/);
  assert.match(source, /<Tag key=\{reason\}>\{humanize\(reason\)\}<\/Tag>/);
});

test("screener and backtest use backend-owned ready-made NIFTY universes", async () => {
  const [screener, backtest, types] = await Promise.all([
    readFile(new URL("../app/screener/screener-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/backtest/backtest-workspace.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/platform/v2-types.ts", import.meta.url), "utf8"),
  ]);
  assert.match(screener, /v2Get<UniversePresetsResponse>\("screener\/presets"/);
  assert.match(screener, /presetId: selectedPreset\.presetId/);
  assert.match(backtest, /universePresetId: selectedPreset\.presetId/);
  assert.match(backtest, /official snapshot/);
  assert.match(types, /export type UniversePreset/);
  assert.doesNotMatch(screener, /const NIFTY_?50|const NIFTY_TOP_?20/);
  assert.doesNotMatch(backtest, /const NIFTY_?50|const NIFTY_TOP_?20/);
});
