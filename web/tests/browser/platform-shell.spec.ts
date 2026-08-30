import { expect, test, type Page } from "@playwright/test";

const overview = {
  platform: "OpenDelta",
  environment: "test",
  dataFreshness: { status: "HEALTHY" },
  jobStatus: { status: "HEALTHY" },
  researchEngine: {
    enabled: false,
    status: "DISABLED_FAIL_CLOSED",
    message: "New Research experiments are disabled while Research V2 correctness is validated.",
  },
};

async function mockPlatform(page: Page) {
  await page.route("**/api/platform?**", async (route) => {
    const action = new URL(route.request().url()).searchParams.get("action");
    if (action === "overview") return route.fulfill({ json: overview });
    if (action === "factors") return route.fulfill({ json: { rows: [], count: 0 } });
    if (action === "jobs") return route.fulfill({ json: { rows: [], count: 0, worker: { status: "HEALTHY" } } });
    if (action === "data-health") return route.fulfill({ json: {
      marketData: { status: "HEALTHY", ageSeconds: 60 },
      featureCache: { status: "HEALTHY", entries: 2 },
      providers: [
        { provider: "DHAN", markets: ["NSE"], timeframes: ["1m", "5m", "15m", "30m", "1h", "6h", "1d"], data_types: ["historical_candles", "live_quotes"], timezone: "Asia/Kolkata", public_only: false, status: "HEALTHY", privateTradingEndpoints: false },
        { provider: "OKX", markets: ["CRYPTO"], timeframes: ["5m"], data_types: ["candles"], timezone: "UTC", public_only: true, status: "DEGRADED", privateTradingEndpoints: false },
        { provider: "VALR", markets: ["CRYPTO"], timeframes: ["5m"], data_types: ["candles"], timezone: "UTC", public_only: true, status: "UNAVAILABLE", privateTradingEndpoints: false },
      ],
      warnings: ["Fixture status"],
    } });
    return route.fulfill({ status: 404, json: { detail: "Not part of this browser smoke" } });
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
    "/", "/markets", "/scanner", "/signals", "/signals/funnel", "/signals/crypto",
    "/strategies", "/backtest", "/backtest/crypto", "/research", "/research/experiments",
    "/research/results", "/risk", "/data-health", "/jobs", "/settings", "/admin",
  ];
  const routesWithEmbeddedHeader = new Set(["/", "/scanner", "/signals", "/signals/funnel", "/signals/crypto", "/backtest", "/backtest/crypto", "/admin"]);
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
      await expect(page.locator('[aria-label="Toggle navigation"]')).toHaveCount(1);
      await expect(page.locator('.platform-frame[data-ui-version="unified-v2"]')).toHaveCount(1);
      if (routesWithEmbeddedHeader.has(route)) {
        await expect(page.locator(".global-header .brand")).not.toBeVisible();
        await expect(page.locator(".global-header .top-nav")).not.toBeVisible();
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${route} at ${viewport.width}px`).toBeLessThanOrEqual(1);
    }
  }
});

test("sidebar links perform full document navigation in production", async ({ page }) => {
  await page.goto("/");

  const scannerLink = page.getByRole("link", { name: "Scanner", exact: true });
  await expect(scannerLink).toHaveAttribute("href", "/scanner");
  await page.evaluate(() => {
    (window as Window & { __openDeltaNavProbe?: boolean }).__openDeltaNavProbe = true;
  });
  await Promise.all([
    page.waitForURL("**/scanner"),
    scannerLink.click(),
  ]);
  await expect(page).toHaveURL(/\/scanner$/);
  expect(await page.evaluate(() => Boolean((window as Window & { __openDeltaNavProbe?: boolean }).__openDeltaNavProbe))).toBe(false);
  await expect(page.locator(".platform-route-context strong")).toHaveText("Scanner");

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

test("desktop sidebar collapses, expands, and remembers its state", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  const collapse = page.getByRole("button", { name: "Collapse navigation" });
  await collapse.click();
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-sidebar-collapsed", "true");
  await expect(page.getByRole("button", { name: "Expand navigation" })).toBeVisible();

  await expect.poll(() => page.locator(".platform-sidebar").evaluate((element) => element.getBoundingClientRect().width)).toBeLessThanOrEqual(80);
  await expect.poll(() => page.locator(".platform-content").evaluate((element) => Number.parseFloat(getComputedStyle(element).marginLeft))).toBeLessThanOrEqual(80);
  expect(await page.evaluate(() => window.localStorage.getItem("opendelta-sidebar-collapsed"))).toBe("true");

  await page.reload();
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-sidebar-collapsed", "true");
  await page.getByRole("button", { name: "Expand navigation" }).click();
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-sidebar-collapsed", "false");
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

test("research remains disabled and provider badges reflect status", async ({ page }) => {
  await page.goto("/research/experiments");
  await expect(page.getByText("Research execution disabled")).toBeVisible();
  await expect(page.getByRole("button", { name: "Estimate search" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Run experiment" })).toBeDisabled();

  await page.goto("/data-health");
  await expect(page.getByRole("cell", { name: "HEALTHY" }).locator(".quant-badge")).toHaveClass(/good/);
  await expect(page.getByRole("cell", { name: "DEGRADED" }).locator(".quant-badge")).toHaveClass(/warn/);
  await expect(page.getByRole("cell", { name: "UNAVAILABLE" }).locator(".quant-badge")).toHaveClass(/bad/);
});

test("data-health provider values wrap inside their columns", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/data-health");
  await expect(page.locator(".data-health-table")).toBeVisible();

  const overflow = await page.locator(".data-health-values").evaluateAll((elements) =>
    elements.map((element) => element.scrollWidth - element.clientWidth),
  );
  expect(overflow.length).toBeGreaterThan(0);
  for (const pixels of overflow) expect(pixels).toBeLessThanOrEqual(1);
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
  await page.goto("/research", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/login$/);
});
