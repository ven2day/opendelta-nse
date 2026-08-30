import assert from "node:assert/strict";
import test from "node:test";

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

function occurrences(value, pattern) {
  return value.match(pattern)?.length ?? 0;
}

test("quant routes authenticate and render exactly one platform shell", async () => {
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
    assert.equal(occurrences(html, /class="platform-topbar"/g), 1, `${path} should have one top bar`);
    assert.equal(occurrences(html, /class="platform-sidebar"/g), 1, `${path} should have one sidebar`);
    assert.equal(occurrences(html, /aria-label="Toggle navigation"/g), 1, `${path} should have one mobile menu`);
    assert.doesNotMatch(html, /Your site is taking shape|Internal Server Error/);
  }
});

test("legacy routes retain only their existing shell", async () => {
  const worker = await loadWorker();
  const cookie = await login(worker);
  const routes = [
    ["/", /Yesterday RSI/],
    ["/scanner", /Stock Scanner/],
    ["/backtest", /Historical backtest/],
    ["/backtest/crypto", /Crypto &amp; metals backtest/],
    ["/signals", /Completed-candle research monitor/],
    ["/signals/funnel", /NSE Signal Funnel/],
    ["/signals/crypto", /Crypto &amp; metals signals/],
    ["/admin", /Global price range/],
  ];
  for (const [path, marker] of routes) {
    const response = await fetchFromWorker(worker, path, { headers: { accept: "text/html", cookie } });
    assert.equal(response.status, 200, `${path} should render`);
    const html = await response.text();
    assert.match(html, marker);
    assert.equal(occurrences(html, /class="platform-topbar"/g), 0, `${path} must not render platform top bar`);
    assert.equal(occurrences(html, /class="platform-sidebar"/g), 0, `${path} must not render platform sidebar`);
    assert.equal(occurrences(html, /aria-label="Toggle navigation"/g), 0, `${path} must not render platform mobile menu`);
  }
});

test("platform API proxy rejects anonymous, unknown, and unsafe requests", async () => {
  const worker = await loadWorker();
  const anonymous = await fetchFromWorker(worker, "/api/platform?action=overview");
  assert.equal(anonymous.status, 401);
  const cookie = await login(worker);
  const unknown = await fetchFromWorker(worker, "/api/platform?action=place-order", { headers: { cookie } });
  assert.equal(unknown.status, 404);
  assert.match(await unknown.text(), /Unknown platform action/);
  const unsafeJob = await fetchFromWorker(worker, "/api/platform?action=job&jobId=..%2Fsecret", { headers: { cookie } });
  assert.equal(unsafeJob.status, 400);
  assert.match(await unsafeJob.text(), /valid jobId/);
});

test("login page never renders either authenticated shell", async () => {
  const worker = await loadWorker();
  const response = await fetchFromWorker(worker, "/login", { headers: { accept: "text/html" } });
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /Sign in/);
  assert.equal(occurrences(html, /class="platform-topbar"/g), 0);
  assert.equal(occurrences(html, /class="platform-sidebar"/g), 0);
});
