import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const source = (path) => readFile(new URL(path, root), "utf8");

process.env.APP_USERNAME = "platform-admin";
process.env.APP_PASSWORD = "platform-password-123";
process.env.AUTH_SECRET = "platform-test-secret-that-is-at-least-32-characters";

async function loadWorker() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("platform-test", `${process.pid}-${Date.now()}`);
  return (await import(workerUrl.href)).default;
}

function fetchFromWorker(worker, path, init = {}) {
  return worker.fetch(new Request(new URL(path, "http://localhost"), init), {
    ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) },
  }, { waitUntil() {}, passThroughOnException() {} });
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

test("quant feature routes require authentication and server-render safely", async () => {
  const worker = await loadWorker();
  const cookie = await login(worker);
  const routes = [
    ["/markets", /NSE market research/],
    ["/research", /Learn the factor catalogue/],
    ["/research/experiments", /Design a bounded experiment/],
    ["/research/results", /Experiment results/],
    ["/strategies", /Versioned strategy catalog/],
    ["/risk", /Research risk controls/],
    ["/data-health", /Data Health/],
    ["/jobs", /Background jobs/],
    ["/settings", /Platform settings/],
  ];
  for (const [path, marker] of routes) {
    const anonymous = await fetchFromWorker(worker, path, { redirect: "manual", headers: { accept: "text/html" } });
    assert.ok([302, 303, 307, 308].includes(anonymous.status), `${path} should redirect anonymous users`);
    const authenticated = await fetchFromWorker(worker, path, { headers: { accept: "text/html", cookie } });
    assert.equal(authenticated.status, 200, `${path} should render`);
    const html = await authenticated.text();
    assert.match(html, marker);
    assert.match(html, /OpenDelta/);
    assert.doesNotMatch(html, /Your site is taking shape|Internal Server Error/);
  }
});

test("platform API proxy is authenticated and has a strict action allowlist", async () => {
  const worker = await loadWorker();
  const anonymous = await fetchFromWorker(worker, "/api/platform?action=overview");
  assert.equal(anonymous.status, 401);
  const route = await source("app/api/platform/route.ts");
  assert.match(route, /getSessionUser/);
  assert.match(route, /Unknown platform action/);
  assert.match(route, /safeJobId/);
  assert.match(route, /x-idempotency-key/);
  assert.doesNotMatch(route, /account|withdrawal|balance|placeOrder|marketOrder/);
});

test("common shell exposes one professional navigation and responsive safety state", async () => {
  const [layout, chrome, styles] = await Promise.all([
    source("app/layout.tsx"),
    source("app/platform/platform-chrome.tsx"),
    source("app/globals.css"),
  ]);
  assert.match(layout, /<PlatformChrome \/>/);
  for (const label of ["Overview", "Markets", "Screener", "Research Lab", "Strategies", "Backtests", "Signals", "Risk", "Data Health", "Jobs", "Settings"]) assert.match(chrome, new RegExp(`label: "${label}"`));
  assert.match(chrome, /Paper research only/);
  assert.match(chrome, /Broker execution disabled/);
  assert.match(styles, /\.platform-shell \+ \.platform-content/);
  assert.match(styles, /@media \(max-width: 820px\)/);
  assert.match(styles, /\.quant-table-scroll \{ overflow: auto/);
  assert.match(styles, /font-size: 14px/);
});

test("Research Lab teaches factors and bounds experiments before execution", async () => {
  const [catalog, builder, backend] = await Promise.all([
    source("app/research/factor-catalog.tsx"),
    source("app/research/experiment-builder.tsx"),
    source("../opendelta/research.py"),
  ]);
  assert.match(catalog, /Common misunderstanding/);
  assert.match(catalog, /Warm-up:/);
  assert.match(catalog, /missing_data_behavior/);
  assert.match(builder, /Estimate combinations before running/);
  assert.match(builder, /Single-family tournament/);
  assert.match(builder, /Forward selection/);
  assert.match(builder, /minimumTrades/);
  assert.match(backend, /untouchedTestResult/);
  assert.match(backend, /liveOrdersEnabled.*False/s);
});

test("production image and service include the modular runtime without enabling broker orders", async () => {
  const [dockerfile, service, platform, strategies] = await Promise.all([
    source("deploy/backtest.Dockerfile"),
    source("deploy/vento-nse-backtest.service"),
    source("../opendelta/platform.py"),
    source("../opendelta/strategies.py"),
  ]);
  assert.match(dockerfile, /COPY opendelta \.\/opendelta/);
  assert.match(service, /PLATFORM_DATA_DIR=\/var\/lib\/vento-nse\/backtest\/platform/);
  assert.match(platform, /"paperOnly": True/);
  assert.match(platform, /"liveOrdersEnabled": False/);
  assert.match(strategies, /market_aligned_vwap_pullback_scalper[\s\S]*"RETIRED"/);
  assert.doesNotMatch(`${platform}\n${strategies}`, /place_order|withdrawal|private trading/);
});
