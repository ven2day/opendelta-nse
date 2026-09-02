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
  const authenticatedRoutes = [
    "/", "/markets", "/signals", "/signals/crypto",
    "/backtest", "/backtest/crypto", "/settings", "/admin",
  ];
  const routesWithEmbeddedHeader = new Set(["/", "/signals/crypto", "/backtest", "/backtest/crypto", "/admin"]);
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
      if (route === "/signals") await expect(page.locator(".global-header")).toHaveCount(0);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${route} at ${viewport.width}px`).toBeLessThanOrEqual(1);
    }
  }
});

test("NSE Signals presents an expected market-close state without false degradation", async ({ page }) => {
  await page.goto("/signals");

  await expect(page.getByText("Market closed · automatic resume armed")).toBeVisible();
  await expect(page.getByText("RESUMES AT OPEN")).toBeVisible();
  await expect(page.getByText("Auto-refresh every 10 seconds")).toBeVisible();
  await expect(page.locator(".signals-runtime-banner")).toHaveClass(/ready/);
  await expect(page.getByText(/NSE is closed\. The engine will reconnect automatically/)).toBeVisible();
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

  const backtestsLink = page.getByRole("link", { name: "Backtests", exact: true });
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
  await expect(page.locator(".platform-route-context strong")).toHaveText("Backtests");
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

  await page.goto("/backtest");

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
  await expect.poll(() => page.locator(".platform-content").evaluate((element) => Number.parseFloat(getComputedStyle(element).marginLeft))).toBeGreaterThanOrEqual(247);
});

test("overview session values remain inside the table", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
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

  await page.goto("/markets");
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-theme", "light");
});

test("mobile navigation, logout, and authentication redirects work", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/markets");
  const menu = page.getByRole("button", { name: "Toggle navigation" });
  await menu.click();
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".platform-sidebar")).toBeVisible();
  await page.locator(".platform-signout").click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/backtest", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/login$/);
});
