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
      await expect(page.locator(".platform-sidebar")).toHaveCount(1);
      await expect(page.locator(".platform-menu")).toHaveCount(1);
      await expect(page.locator('.platform-frame[data-ui-version="unified-v2"]')).toHaveCount(1);
      if (routesWithEmbeddedHeader.has(route)) {
        await expect(page.locator(".global-header .brand")).not.toBeVisible();
        await expect(page.locator(".global-header .top-nav")).not.toBeVisible();
      }
      if (route === "/legacy/signals") await expect(page.locator(".global-header")).toHaveCount(0);
      await expect(page.locator(".platform-sidebar nav a span")).toHaveText(["Dashboard", "Screener", "Backtest", "Signals", "Paper Trading", "Settings"]);
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

test("sidebar links perform full document navigation in production", async ({ page }) => {
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
  await expect(page.locator('.quant-market-tabs a[aria-current="page"]')).toHaveText("Crypto");
  await expect(page.getByRole("link", { name: "Signals", exact: true })).toHaveAttribute("href", "/signals?market=CRYPTO");
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

test("desktop hamburger fully hides, restores, and remembers the sidebar", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  const menu = page.getByRole("button", { name: "Toggle navigation" });
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await menu.click();
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-navigation-open", "false");
  await expect(menu).toHaveAttribute("aria-expanded", "false");

  await expect.poll(() => page.locator(".platform-sidebar").evaluate((element) => element.getBoundingClientRect().right)).toBeLessThanOrEqual(1);
  await expect.poll(() => page.locator(".platform-content").evaluate((element) => Number.parseFloat(getComputedStyle(element).marginLeft))).toBeLessThanOrEqual(1);
  expect(await page.evaluate(() => window.localStorage.getItem("opendelta-sidebar-open"))).toBe("false");

  await page.reload();
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-navigation-open", "false");
  await page.getByRole("button", { name: "Toggle navigation" }).click();
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-navigation-open", "true");
  await expect.poll(() => page.locator(".platform-sidebar").evaluate((element) => element.getBoundingClientRect().left)).toBeGreaterThanOrEqual(0);
  const sidebarWidth = await page.locator(".platform-sidebar").evaluate((element) => element.getBoundingClientRect().width);
  await expect.poll(() => page.locator(".platform-content").evaluate((element) => Number.parseFloat(getComputedStyle(element).marginLeft))).toBeGreaterThanOrEqual(sidebarWidth - 1);
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
  const menu = page.getByRole("button", { name: "Toggle navigation" });
  await menu.click();
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".platform-sidebar")).toBeVisible();
  await page.locator(".platform-signout").click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/backtest", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/login$/);
});
