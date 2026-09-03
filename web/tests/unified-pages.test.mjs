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
  const sidebar = html.match(/<aside class="platform-sidebar"[\s\S]*?<\/aside>/)?.[0] ?? "";
  const nav = sidebar.match(/<nav[\s\S]*?<\/nav>/)?.[0] ?? "";
  return Array.from(nav.matchAll(/<span>([^<]+)<\/span>/g), (match) => match[1]);
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
    assert.match(html, /data-ui-version="unified-v2"/);
    // The topbar clock is seeded after mount so server and client markup match (no hydration error #418).
    assert.match(html, /--:--:--/);
    assert.match(html, /Paper research only/);
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
  assert.match(settingsHtml, /Legacy tools/);
  for (const href of ["/legacy/screener", "/legacy/backtest", "/legacy/backtest/crypto", "/legacy/signals", "/legacy/signals/crypto", "/legacy/markets", "/admin"]) {
    assert.match(settingsHtml, new RegExp(`href="${href.replace(/[/?]/g, (char) => `\\${char}`)}`), `settings links ${href}`);
  }
  assert.match(settingsHtml, /Global minimum price/);
  assert.match(settingsHtml, /Global maximum price/);
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
