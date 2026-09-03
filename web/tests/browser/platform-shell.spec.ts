import { expect, test, type Page } from "@playwright/test";

const overview = {
  platform: "OpenDelta",
  environment: "test",
  dataFreshness: { status: "HEALTHY" },
  jobStatus: { status: "HEALTHY" },
};

async function mockPlatform(page: Page) {
  await page.route("**/api/platform?**", async (route) => {
    const action = new URL(route.request().url()).searchParams.get("action");
    if (action === "overview") return route.fulfill({ json: overview });
    return route.fulfill({ status: 404, json: { detail: "Not part of this browser smoke" } });
  });
  await page.route("**/api/v2/**", async (route) => {
    // The unified pages must render a clear "not configured" state, never crash, when the platform database is absent.
    return route.fulfill({ status: 503, json: { detail: "Platform database is not configured" } });
  });
  await page.route("**/api/live-signals?**", async (route) => {
    const action = new URL(route.request().url()).searchParams.get("action");
    const status = {
      connectionStatus: "DISCONNECTED",
      engineStatus: "MARKET_CLOSED",
      message: "NSE is closed; Dhan live subscriptions resume at the next market session",
      universeVersion: "LIVE-TEST-001",
      universeFrozen: true,
      monitoredSymbols: 300,
      subscribedSymbols: 300,
      timeframe: "5m",
      strategyVersion: "rsi-recovery-1.1.0",
      lastCompletedCandle: "2026-08-28T15:30:00+05:30",
      lastMarketDataTimestamp: null,
      dataAgeSeconds: null,
      marketSession: "CLOSED",
      paperOnly: true,
      liveOrdersEnabled: false,
      oiFilterMode: "OFF",
      oiRegime: null,
      oiHistory: null,
    };
    if (action === "signals") return route.fulfill({ json: { signals: [], status, study: { signalsGenerated: 0 } } });
    if (action === "status") return route.fulfill({ json: status });
    if (action === "settings") return route.fulfill({ json: { settings: {} } });
    if (action === "paper") return route.fulfill({ json: { paperTrades: [] } });
    return route.fulfill({ status: 404, json: { detail: "Unknown test action" } });
  });
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Username").fill("browser-admin");
  await page.getByLabel("Password").fill("browser-password-123");
  await Promise.all([
    page.waitForURL("**/"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);
}

test.beforeEach(async ({ page }) => {
  await mockPlatform(page);
  await login(page);
});

test("route-aware shell has no duplicate navigation or viewport overflow", async ({ page }) => {
  test.setTimeout(240_000);
  const authenticatedRoutes = [
    "/", "/screener", "/backtest", "/signals", "/paper-trading", "/settings",
    "/?market=CRYPTO", "/screener?market=CRYPTO", "/backtest?market=CRYPTO", "/signals?market=CRYPTO", "/paper-trading?market=CRYPTO",
    "/admin", "/legacy/screener", "/legacy/markets", "/legacy/signals", "/legacy/signals/crypto",
    "/legacy/backtest", "/legacy/backtest/crypto",
  ];
  const routesWithEmbeddedHeader = new Set(["/legacy/screener", "/legacy/signals/crypto", "/legacy/backtest", "/legacy/backtest/crypto", "/admin"]);
  const viewports = [
    { width: 1440, height: 900 },
    { width: 1024, height: 768 },
    { width: 768, height: 900 },
    { width: 390, height: 844 },
  ];

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const route of authenticatedRoutes) {
      await page.goto(route);
      await expect(page.locator(".platform-topbar")).toHaveCount(1);
      await expect(page.locator(".platform-topnav")).toHaveCount(1);
      await expect(page.locator(".platform-sidebar, .platform-menu, .platform-backdrop")).toHaveCount(0);
      await expect(page.locator('.platform-frame[data-ui-version="unified-v2"]')).toHaveCount(1);
      if (routesWithEmbeddedHeader.has(route)) {
        await expect(page.locator(".global-header .brand")).not.toBeVisible();
        await expect(page.locator(".global-header .top-nav")).not.toBeVisible();
      }
      if (route === "/legacy/signals") await expect(page.locator(".global-header")).toHaveCount(0);
      await expect(page.locator(".platform-topnav a span")).toHaveText(["Dashboard", "Screener", "Backtest", "Signals", "Paper Trading", "Settings"]);
      if (viewport.width === 1440 && !route.startsWith("/legacy") && route !== "/admin") {
        await expect(page.getByText("Unified platform database not configured").first()).toBeVisible({ timeout: 15_000 });
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${route} at ${viewport.width}px`).toBeLessThanOrEqual(1);
    }
  }
});

test("legacy NSE Signals presents an expected market-close state without false degradation", async ({ page }) => {
  await page.goto("/legacy/signals");

  await expect(page.locator(".platform-route-context span")).toHaveText("Legacy tool");
  await expect(page.getByText("Dhan market data")).toBeVisible();
  await expect(page.getByText("DISCONNECTED", { exact: true })).toBeVisible();
  await expect(page.getByText("MARKET CLOSED", { exact: true })).toBeVisible();
  await expect(page.getByText("No signals yet.")).toBeVisible();
  await expect(page.getByText("This page couldn’t load")).toHaveCount(0);
});

test("topbar links perform full document navigation in production", async ({ page }) => {
  await page.goto("/");

  const signalsLink = page.getByRole("link", { name: "Signals", exact: true });
  await expect(signalsLink).toHaveAttribute("href", "/signals");
  await page.evaluate(() => {
    (window as Window & { __openDeltaNavProbe?: boolean }).__openDeltaNavProbe = true;
  });
  await Promise.all([
    page.waitForURL("**/signals"),
    signalsLink.click(),
  ]);
  await expect(page).toHaveURL(/\/signals$/);
  expect(await page.evaluate(() => Boolean((window as Window & { __openDeltaNavProbe?: boolean }).__openDeltaNavProbe))).toBe(false);
  await expect(page.locator(".platform-route-context strong")).toHaveText("Signals");

  const backtestsLink = page.getByRole("link", { name: "Backtest", exact: true });
  await expect(backtestsLink).toHaveAttribute("href", "/backtest");
  await page.evaluate(() => {
    (window as Window & { __openDeltaNavProbe?: boolean }).__openDeltaNavProbe = true;
  });
  await Promise.all([
    page.waitForURL("**/backtest"),
    backtestsLink.click(),
  ]);
  await expect(page).toHaveURL(/\/backtest$/);
  expect(await page.evaluate(() => Boolean((window as Window & { __openDeltaNavProbe?: boolean }).__openDeltaNavProbe))).toBe(false);
  await expect(page.locator(".platform-route-context strong")).toHaveText("Backtest");
});

test("the market switcher keeps the current page and carries the market into navigation", async ({ page }) => {
  await page.goto("/screener");
  const cryptoSwitch = page.locator(".platform-market-switch a", { hasText: "Crypto" });
  await expect(cryptoSwitch).toHaveAttribute("href", "/screener?market=CRYPTO");
  await cryptoSwitch.click();
  await expect(page).toHaveURL(/\/screener\?market=CRYPTO$/);
  await expect(page.locator('.platform-market-switch a.active')).toHaveText("Crypto");
  await expect(page.locator('.quant-market-tabs[aria-label="Market selector"]')).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Signals", exact: true })).toHaveAttribute("href", "/signals?market=CRYPTO");
});

test("backtest ticket is compact and trade controls filter and sort the full result", async ({ page }) => {
  await page.unroute("**/api/v2/**");
  const tradeRequests: URL[] = [];
  const run = {
    runId: "11111111-1111-4111-8111-111111111111", market: "NSE", strategyId: "ema_vwap_strong_buy", strategyVersion: "1.0.0",
    timeframe: "5m", symbols: ["TCS", "RELIANCE"], startDate: "2026-06-01", endDate: "2026-09-01", status: "COMPLETE",
    symbolsTotal: 2, symbolsCompleted: 2, metrics: { totalSignals: 2, completedTrades: 1, targetHits: 1, openTrades: 1, symbolsProcessed: 2, symbolsFailed: 0 },
    createdAt: "2026-09-01T09:00:00Z", completedAt: "2026-09-01T09:01:00Z",
  };
  await page.route("**/api/v2/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/strategies/ema_vwap_strong_buy/config")) return route.fulfill({ json: { strategyId: "ema_vwap_strong_buy", market: "NSE", active: null, effectiveConfiguration: {}, effectiveRiskSettings: {}, all: [] } });
    if (url.pathname.endsWith("/strategies")) return route.fulfill({ json: { strategies: [{ strategyId: "ema_vwap_strong_buy", name: "Strong Buy", version: "1.0.0", supportedMarkets: ["NSE"], supportedTimeframes: ["5m"], configSchema: {}, defaults: {} }], markets: ["NSE", "CRYPTO"], riskDefaults: {} } });
    if (url.pathname.endsWith("/screener/universes")) return route.fulfill({ json: { active: { NSE: { universeId: "u1", market: "NSE", name: "Active NSE", symbols: ["TCS", "RELIANCE"], active: true } }, universes: [] } });
    if (url.pathname.endsWith(`/backtests/${run.runId}/trades`)) {
      tradeRequests.push(url);
      const rows = url.searchParams.has("symbol") || url.searchParams.has("status") ? [] : [
        { symbol: "TCS", lotId: "lot-open", status: "OPEN", entryTimestamp: "2026-09-01T09:30:00Z", entryPrice: 100, quantity: 10, targetPrice: 101, netPnl: 0, unrealizedPnl: -5, lastPrice: 99.5, holdingBars: 12 },
        { symbol: "RELIANCE", lotId: "lot-hit", status: "TARGET_HIT", entryTimestamp: "2026-09-01T09:35:00Z", entryPrice: 200, quantity: 5, targetPrice: 202, exitPrice: 202, netPnl: 10, holdingMinutes: 25 },
      ];
      return route.fulfill({ json: { runId: run.runId, total: rows.length, limit: 50, offset: 0, trades: rows } });
    }
    if (url.pathname.endsWith(`/backtests/${run.runId}`)) return route.fulfill({ json: run });
    if (url.pathname.endsWith("/backtests")) return route.fulfill({ json: { runs: [run] } });
    return route.fulfill({ status: 404, json: { detail: "Unexpected browser-test route" } });
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/backtest");
  await expect(page.getByRole("button", { name: "Run backtest" })).toBeVisible();
  const controls = page.locator(".quant-backtest-run-grid select, .quant-backtest-run-grid input");
  await expect(controls).toHaveCount(5);
  const tops = await controls.evaluateAll((elements) => elements.map((element) => Math.round(element.getBoundingClientRect().top)));
  expect(Math.max(...tops) - Math.min(...tops)).toBeLessThanOrEqual(2);

  const tradeScroller = page.locator(".quant-trades-scroll");
  const tradeTable = page.locator(".quant-trades-table");
  await expect(tradeTable).toBeVisible();
  const dimensions = await tradeScroller.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(dimensions.scrollWidth).toBeGreaterThan(dimensions.clientWidth);
  const headings = await tradeTable.locator("thead tr:first-child th").evaluateAll((elements) =>
    elements.map((element) => {
      const box = element.getBoundingClientRect();
      return { left: box.left, right: box.right, width: box.width };
    }),
  );
  for (let index = 0; index < headings.length - 1; index += 1) {
    expect(headings[index].width).toBeGreaterThanOrEqual(70);
    expect(headings[index].right).toBeLessThanOrEqual(headings[index + 1].left + 1);
  }
  expect(headings.at(-1)?.width).toBeGreaterThanOrEqual(100);
  await tradeScroller.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
  await expect(page.getByRole("columnheader", { name: /Holding/ })).toBeInViewport();
  await expect(page.getByText("Unrealized", { exact: true })).toBeVisible();
  await expect(page.getByText("99.5", { exact: true })).toBeVisible();
  await expect(page.getByText("12 bars", { exact: true })).toBeVisible();

  await expect(page.getByText("OPEN", { exact: true })).toHaveClass(/warn/);
  await page.getByRole("button", { name: /Symbol/ }).click();
  await expect.poll(() => tradeRequests.at(-1)?.searchParams.get("sort")).toBe("symbol");
  await expect.poll(() => tradeRequests.at(-1)?.searchParams.get("direction")).toBe("asc");
  await page.getByLabel("Filter trades by symbol").fill("TCS");
  await page.getByLabel("Filter trades by status").selectOption("OPEN");
  await expect.poll(() => tradeRequests.at(-1)?.searchParams.get("symbol")).toBe("TCS");
  await expect.poll(() => tradeRequests.at(-1)?.searchParams.get("status")).toBe("OPEN");
  await expect(page.getByLabel("Filter trades by symbol")).toHaveValue("TCS");
  await expect(page.getByLabel("Filter trades by status")).toHaveValue("OPEN");
  await expect(page.getByText("No trades yet")).toBeVisible();
});

test("a saved result from a removed strategy is ignored and cannot crash the Backtests page", async ({ page }) => {
  // Strategies removed from the platform (for example the old opening-range watchlist) may
  // still be returned by the history API; their records are filtered out client-side and
  // never rendered.
  const summary = {
    id: "legacy-removed-strategy",
    completedAt: "2026-08-29T15:30:00+05:30",
    strategyMode: "removed_legacy_strategy",
    strategyName: "Removed legacy strategy",
    timeframe: "5m",
    durationYears: 1,
    symbolCount: 649,
  };
  await page.route("**/api/backtest-history**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.has("id")) {
      return route.fulfill({ json: {
        ...summary,
        response: {
          metadata: {
            runId: summary.id,
            strategyMode: summary.strategyMode,
            strategyKey: summary.strategyMode,
          },
          results: [],
          errors: [],
          warnings: [],
        },
      } });
    }
    return route.fulfill({ json: { runs: [summary], limit: 10 } });
  });

  await page.goto("/legacy/backtest");

  await expect(page.locator(".platform-topbar")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent backtests" })).toBeVisible();
  await expect(page.getByText("Completed backtests will appear here automatically")).toBeVisible();
  await expect(page.getByText("Removed legacy strategy")).toHaveCount(0);
  await expect(page.getByText("Saved result could not be displayed.")).toHaveCount(0);
  await expect(page.getByText("This page couldn’t load")).toHaveCount(0);
});

test("desktop topbar navigation keeps the content full width", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.locator(".platform-topnav")).toBeVisible();
  await expect(page.locator(".platform-sidebar, .platform-menu, .platform-backdrop")).toHaveCount(0);
  await expect.poll(() => page.locator(".platform-content").evaluate((element) => Number.parseFloat(getComputedStyle(element).marginLeft))).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => window.localStorage.getItem("opendelta-sidebar-open"))).toBeNull();
});

test("legacy screener session values remain inside the table", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/legacy/screener");
  await expect(page.getByRole("columnheader", { name: "Session (IST)" })).toBeVisible();

  const sessions = page.locator(".session-cell");
  expect(await sessions.count()).toBeGreaterThan(0);
  const labels = await sessions.allTextContents();
  for (const label of labels) expect(label).not.toContain("· IST");

  const overflow = await sessions.evaluateAll((elements) =>
    elements.map((element) => element.scrollWidth - element.clientWidth),
  );
  for (const pixels of overflow) expect(pixels).toBeLessThanOrEqual(1);
});

test("theme preference persists while navigating between product areas", async ({ page }) => {
  await page.goto("/");
  const themeToggle = page.getByRole("button", { name: "Switch to light theme" });
  await themeToggle.click();
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-theme", "light");

  await page.goto("/backtest");
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-theme", "light");
  await expect(page.getByRole("button", { name: "Switch to dark theme" })).toBeVisible();

  await page.goto("/legacy/markets");
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-theme", "light");
});

test("mobile navigation, logout, and authentication redirects work", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/paper-trading");
  await expect(page.locator(".platform-topnav")).toBeVisible();
  await expect(page.locator(".platform-topnav a", { hasText: "Paper Trading" })).toHaveAttribute("aria-current", "page");
  await expect(page.locator(".platform-sidebar, .platform-menu, .platform-backdrop")).toHaveCount(0);
  await page.locator(".platform-signout").click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/backtest", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/login$/);
});
