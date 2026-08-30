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
        { provider: "DHAN", markets: ["NSE"], timeframes: ["5m"], data_types: ["candles"], timezone: "Asia/Kolkata", public_only: false, status: "HEALTHY", privateTradingEndpoints: false },
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
  const platformRoutes = ["/markets", "/research", "/research/experiments", "/research/results", "/strategies", "/risk", "/data-health", "/jobs", "/settings"];
  const legacyRoutes = ["/", "/scanner", "/backtest", "/backtest/crypto", "/signals", "/signals/funnel", "/signals/crypto", "/admin"];
  const viewports = [
    { width: 1440, height: 900 },
    { width: 1024, height: 768 },
    { width: 768, height: 900 },
    { width: 390, height: 844 },
  ];

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const route of platformRoutes) {
      await page.goto(route);
      await expect(page.locator(".platform-topbar")).toHaveCount(1);
      await expect(page.locator(".platform-sidebar")).toHaveCount(1);
      await expect(page.locator('[aria-label="Toggle navigation"]')).toHaveCount(1);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${route} at ${viewport.width}px`).toBeLessThanOrEqual(1);
    }
    for (const route of legacyRoutes) {
      await page.goto(route);
      await expect(page.locator(".platform-topbar")).toHaveCount(0);
      await expect(page.locator(".platform-sidebar")).toHaveCount(0);
      await expect(page.locator('[aria-label="Toggle navigation"]')).toHaveCount(0);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${route} at ${viewport.width}px`).toBeLessThanOrEqual(1);
    }
  }
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
