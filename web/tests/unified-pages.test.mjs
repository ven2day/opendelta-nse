import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

process.env.APP_USERNAME = "test-admin";
process.env.APP_PASSWORD = "test-password-123";
process.env.AUTH_SECRET = "test-secret-that-is-at-least-32-characters-long";

const NAVIGATION = ["Dashboard", "Screener", "Backtest", "Signals", "Paper Trading", "Settings"];
const ROUTES = [
  { path: "/", title: "Dashboard" },
  { path: "/screener", title: "Screener" },
  { path: "/backtest", title: "Backtest" },
  { path: "/signals", title: "Signals" },
  { path: "/paper-trading", title: "Paper Trading" },
  { path: "/settings", title: "Settings" },
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
    assert.doesNotMatch(html, /Vento NSE/);
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
  assert.match(settingsHtml, /Global minimum price/);
  assert.match(settingsHtml, /Global maximum price/);
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

  const cookie = await login(worker);
  const traversal = await fetchFromWorker(worker, "/api/v2/..%2Fplatform", { headers: { cookie } });
  assert.equal(traversal.status, 404);

  const unconfigured = await fetchFromWorker(worker, "/api/v2/dashboard?market=NSE", { headers: { cookie } });
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

test("the backtest run ticket uses defaults with one collapsed JSON override", async () => {
  const source = await readFile(new URL("../app/backtest/backtest-workspace.tsx", import.meta.url), "utf8");
  assert.match(source, /<details className="quant-backtest-config">/);
  assert.doesNotMatch(source, /<details className="quant-backtest-config" open/);
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
});

test("workspace headers stay compact and the screener declares both markets", async () => {
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
  assert.match(sources[1], /NSE & Crypto screener/);
  assert.match(sources[1], /NSE & Crypto supported/);
  assert.doesNotMatch(sources[1], /Crypto only/);
});

test("the screener uses a compact run ticket with partial JSON overrides", async () => {
  const source = await readFile(new URL("../app/screener/screener-workspace.tsx", import.meta.url), "utf8");
  assert.match(source, /<details className="quant-backtest-config">/);
  assert.doesNotMatch(source, /<details className="quant-backtest-config" open/);
  assert.match(source, /aria-label="Screener configuration JSON"/);
  assert.match(source, /hasFilterOverride \? "Custom JSON" : "Defaults"/);
  assert.match(source, /validateConfigValues\(overrides, FILTER_SCHEMA/);
  assert.match(source, /Full \{marketLabel\(market\)\} market/);
  assert.doesNotMatch(source, /<SchemaForm/);
  assert.doesNotMatch(source, /Keep all passing symbols/);
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
