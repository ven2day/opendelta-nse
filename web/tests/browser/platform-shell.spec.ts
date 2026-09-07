import { expect, test, type Page } from "@playwright/test";

const overview = {
  platform: "OpenDelta",
  environment: "test",
  dataFreshness: { status: "HEALTHY" },
  jobStatus: { status: "HEALTHY" },
};

async function mockPlatform(page: Page) {
  await page.route("**/api/platform?**", async (route) => {
    const parameters = new URL(route.request().url()).searchParams;
    const action = parameters.get("action");
    if (action === "overview") return route.fulfill({ json: { ...overview, environment: parameters.get("market") ?? "NSE" } });
    return route.fulfill({ status: 404, json: { detail: "Not part of this browser smoke" } });
  });
  await page.route("**/api/v2/**", async (route) => {
    // The unified pages must render a clear "not configured" state, never crash, when the platform database is absent.
    return route.fulfill({ status: 503, json: { detail: "Platform database is not configured" } });
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
    "/", "/screener", "/backtest", "/research", "/indicators", "/signals", "/paper-trading", "/settings",
    "/?market=CRYPTO", "/screener?market=CRYPTO", "/backtest?market=CRYPTO", "/research?market=CRYPTO", "/indicators?market=CRYPTO", "/signals?market=CRYPTO", "/paper-trading?market=CRYPTO",
    "/admin",
  ];
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
      await expect(page.locator(".platform-topnav a")).toHaveCount(8);
      expect(await page.locator(".platform-topnav a").evaluateAll((links) => links.map((link) => link.getAttribute("aria-label")))).toEqual(["Dashboard", "Watchlist", "Backtest", "Research", "Indicators", "Signals", "Paper Trading", "Strategies"]);
      await expect(page.locator(".platform-safety-chip")).toHaveCount(0);
      if (viewport.width === 1440) {
        await expect(page.getByText("Unified platform database not configured").first()).toBeVisible({ timeout: 15_000 });
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${route} at ${viewport.width}px`).toBeLessThanOrEqual(1);
    }
  }
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
  await expect(page.getByRole("link", { name: "Signals", exact: true })).toHaveAttribute("aria-current", "page");

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
  await expect(page.getByRole("link", { name: "Backtest", exact: true })).toHaveAttribute("aria-current", "page");
});

test("the market switcher keeps the current page and carries the market into navigation", async ({ page }) => {
  await page.goto("/screener");
  const cryptoSwitch = page.locator(".platform-market-switch a", { hasText: "Crypto" });
  await expect(cryptoSwitch).toHaveAttribute("href", "/screener?market=CRYPTO");
  await cryptoSwitch.click();
  await expect(page).toHaveURL(/\/screener\?market=CRYPTO$/);
  await expect(page.locator('.platform-market-switch a.active')).toHaveText("Crypto");
  await expect(page.locator(".platform-environment")).toHaveText("CRYPTO");
  await expect(page.locator('.quant-market-tabs[aria-label="Market selector"]')).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Signals", exact: true })).toHaveAttribute("href", "/signals?market=CRYPTO");
});

test("research parameter sweeps preview safely and reuse comparison and chart workspaces", async ({ page }) => {
  await page.unroute("**/api/v2/**");
  let previewAttempts = 0;
  const runs = [
    "71111111-1111-4111-8111-111111111111",
    "72222222-2222-4222-8222-222222222222",
  ];
  const variants = runs.map((runId, index) => ({
    variantId: `70000000-0000-4000-8000-00000000000${index + 1}`,
    position: index + 1,
    name: `rsi_low=${25 + index * 5}`,
    configuration: { rsi_low: 25 + index * 5 },
    execution: { targetPct: 0.5, executionTimeframe: "5m" },
    run: {
      runId,
      market: "NSE",
      strategyId: "rsi_dip_ladder_v1",
      strategyVersion: "1.0.0",
      timeframe: "5m",
      symbols: ["INFY", "TCS"],
      startDate: "2026-08-01",
      endDate: "2026-08-31",
      status: "COMPLETE",
      symbolsTotal: 2,
      symbolsCompleted: 2,
      failedSymbols: index ? [{ symbol: "INFY", message: "missing candle" }] : [],
      metrics: {
        realizedPnl: index ? 80 : 120,
        unrealizedPnl: 0,
        maximumDrawdown: index ? 15 : 20,
        winRate: index ? 0.5 : 0.75,
        fees: 4,
        slippage: 2,
        completedTrades: 2,
        openTrades: index,
        targetHits: 1,
        stoppedTrades: index,
        expiredTrades: 0,
        averageHoldingMinutes: 25,
        exposureMinutes: 50,
      },
      createdAt: "2026-09-05T09:00:00Z",
    },
  }));
  const experiment = {
    experimentId: "70000000-0000-4000-8000-000000000099",
    name: "Completed RSI sweep",
    mode: "GRID",
    market: "NSE",
    strategyId: "rsi_dip_ladder_v1",
    strategyVersion: "1.0.0",
    strategySourceId: null,
    timeframe: "5m",
    universeId: "60000000-0000-4000-8000-000000000001",
    universeName: "Active NSE",
    symbols: ["INFY", "TCS"],
    startDate: "2026-08-01",
    endDate: "2026-08-31",
    sweepDefinitions: [{ section: "strategy", parameter: "rsi_low", type: "number", method: "EXPLICIT_VALUES", values: [25, 30] }],
    previewHash: `sha256:${"a".repeat(64)}`,
    variantCount: 2,
    symbolCount: 2,
    estimatedSymbolRuns: 4,
    status: "COMPLETE",
    variantStatusCounts: { QUEUED: 0, RUNNING: 0, COMPLETE: 2, FAILED: 0, CANCELLED: 0, INTERRUPTED: 0 },
    progress: { variantsTotal: 2, symbolsTotal: 4, symbolsCompleted: 4 },
    variants,
    createdAt: "2026-09-05T09:00:00Z",
  };

  await page.route("**/api/v2/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/strategies")) return route.fulfill({ json: {
      strategies: [{
        strategyId: "rsi_dip_ladder_v1", name: "RSI Dip Ladder", version: "1.0.0",
        supportedMarkets: ["NSE", "CRYPTO"], supportedTimeframes: ["5m", "1d"],
        configSchema: { rsi_low: { type: "number", default: 30, minimum: 1, maximum: 90, label: "Low RSI" } }, defaults: { rsi_low: 30 },
      }],
      markets: ["NSE", "CRYPTO"], riskDefaults: {}, riskSchema: {},
      executionSchema: { targetPct: { type: "number", default: null, minimum: 0.00000001, label: "Target %" } },
    } });
    if (path.endsWith("/strategy-studio/sources")) return route.fulfill({ json: { sources: [] } });
    if (path.endsWith("/screener/universes")) return route.fulfill({ json: {
      active: { NSE: { universeId: "60000000-0000-4000-8000-000000000001", market: "NSE", name: "Active NSE", symbols: ["INFY", "TCS"], active: true } },
      universes: [],
    } });
    if (path.endsWith("/screener/presets")) return route.fulfill({ json: { presets: [] } });
    if (path.endsWith("/research/experiments/preview") && request.method() === "POST") {
      previewAttempts += 1;
      if (previewAttempts > 1) return route.fulfill({ status: 422, json: { detail: "The experiment requires 20,100 symbol-runs; the limit is 20,000" } });
      return route.fulfill({ json: {
        previewHash: `sha256:${"b".repeat(64)}`,
        name: "Controlled strategy experiment", mode: "GRID", strategyId: "rsi_dip_ladder_v1", strategyVersion: "1.0.0",
        strategySourceId: null, market: "NSE", timeframe: "5m", universeId: "60000000-0000-4000-8000-000000000001",
        universeName: "Active NSE", symbols: ["INFY", "TCS"], startDate: "2026-07-08", endDate: "2026-09-06",
        sweepDefinitions: [{ section: "strategy", parameter: "rsi_low", type: "number", method: "EXPLICIT_VALUES", values: [25, 30] }],
        parameterCount: 1, variantCount: 2, symbolCount: 2, estimatedSymbolRuns: 4,
        variants: variants.map(({ name, configuration, execution }) => ({ name, configuration, execution })), warnings: [],
      } });
    }
    if (path.endsWith("/research/walk-forward/preview") && request.method() === "POST") {
      return route.fulfill({ json: {
        previewHash: `sha256:${"c".repeat(64)}`, name: "Walk-forward validation", mode: "ROLLING",
        market: "NSE", strategyId: "rsi_dip_ladder_v1", strategyVersion: "1.0.0", strategySourceId: null,
        timeframe: "5m", universeId: "60000000-0000-4000-8000-000000000001", universeName: "Active NSE",
        symbols: ["INFY", "TCS"], overallStartDate: "2026-03-01", overallEndDate: "2026-09-01",
        trainingWindow: 20, testingWindow: 5, step: 5, maximumFolds: 6,
        candidateExperimentId: experiment.experimentId, rankingObjective: "RETURN_DRAWDOWN",
        minimumRequiredTrades: 3, transactionCostBps: 11.1, slippageBps: 5,
        foldCount: 2, candidateCount: 2, childRunCount: 6, symbolCount: 2,
        estimatedSymbolRuns: 12, estimatedCandleWorkload: 30000,
        folds: [
          { position: 1, trainingStart: "2026-03-02", trainingEnd: "2026-03-27", testingStart: "2026-03-30", testingEnd: "2026-04-03", trainingSessions: 20, testingSessions: 5 },
          { position: 2, trainingStart: "2026-03-09", trainingEnd: "2026-04-03", testingStart: "2026-04-06", testingEnd: "2026-04-10", trainingSessions: 20, testingSessions: 5 },
        ],
        candidates: variants.map(({ variantId, position, name, configuration, execution }) => ({ variantId, position, name, configuration, execution })),
        warnings: [],
      } });
    }
    if (path.endsWith("/research/walk-forward")) return route.fulfill({ json: { validations: [{
      validationId: "73333333-3333-4333-8333-333333333333", previewHash: `sha256:${"c".repeat(64)}`,
      name: "Completed walk-forward", mode: "ROLLING", market: "NSE", strategyId: "rsi_dip_ladder_v1",
      strategyVersion: "1.0.0", strategySourceId: null, timeframe: "5m", universeName: "Active NSE",
      symbols: ["INFY", "TCS"], overallStartDate: "2026-03-01", overallEndDate: "2026-09-01",
      trainingWindow: 20, testingWindow: 5, step: 5, maximumFolds: 6,
      candidateExperimentId: experiment.experimentId, rankingObjective: "RETURN_DRAWDOWN",
      minimumRequiredTrades: 3, transactionCostBps: 11.1, slippageBps: 5,
      foldCount: 1, candidateCount: 2, childRunCount: 3, symbolCount: 2,
      estimatedSymbolRuns: 6, estimatedCandleWorkload: 15000, cancelRequested: false, status: "COMPLETE",
      foldStatusCounts: { COMPLETE: 1 }, childRunStatusCounts: { COMPLETE: 3 }, createdAt: "2026-09-05T09:00:00Z",
      aggregateUnseenMetrics: { realizedPnl: 35, maximumDrawdown: 8, winRate: 60, completedTrades: 2, openTrades: 0, targetHits: 1, stoppedTrades: 1, expiredTrades: 0, averageHoldingMinutes: 20, exposureMinutes: 40 },
      folds: [{
        foldId: "74444444-4444-4444-8444-444444444444", position: 1, status: "COMPLETE",
        trainingStart: "2026-03-02", trainingEnd: "2026-03-27", testingStart: "2026-03-30", testingEnd: "2026-04-03",
        trainingSessions: 20, testingSessions: 5, selectedVariantId: variants[0].variantId,
        selectedCandidateName: variants[0].name, selectedConfiguration: variants[0].configuration,
        selectedExecution: variants[0].execution, trainingRank: 1, trainingCandidates: variants,
        testRun: { ...variants[0].run, runId: "75555555-5555-4555-8555-555555555555", startDate: "2026-03-30", endDate: "2026-04-03", metrics: { ...variants[0].run.metrics, realizedPnl: 35, maximumDrawdown: 8 } },
      }],
    }] } });
    if (path.endsWith("/research/experiments")) return route.fulfill({ json: { experiments: [experiment] } });
    const runIndex = runs.findIndex((runId) => path.endsWith(`/backtests/${runId}/trades`));
    if (runIndex >= 0) return route.fulfill({ json: {
      runId: runs[runIndex], total: 2, limit: 5000, offset: 0,
      trades: [
        { symbol: "TCS", lotId: `lot-${runIndex}-1`, status: "TARGET_HIT", exitTimestamp: "2026-08-02T10:00:00Z", netPnl: 40, holdingMinutes: 20 },
        { symbol: "TCS", lotId: `lot-${runIndex}-2`, status: "TARGET_HIT", exitTimestamp: "2026-08-03T10:00:00Z", netPnl: runIndex ? 40 : 80, holdingMinutes: 30 },
      ],
    } });
    return route.fulfill({ status: 404, json: { detail: `Unexpected research route: ${path}` } });
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/research");
  const runButton = page.getByRole("button", { name: "Run experiment" });
  await expect(runButton).toBeDisabled();
  await page.getByRole("button", { name: "Grid sweep" }).click();
  await page.getByRole("button", { name: "Add parameter" }).click();
  await page.getByLabel("rsi_low values").fill("[25, 30]");
  await page.getByRole("button", { name: "Preview experiment" }).click();
  await expect(page.getByRole("heading", { name: "Experiment preview" })).toBeVisible();
  await expect(page.getByText("rsi_low=25", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Symbol-runs").first()).toBeVisible();
  await expect(runButton).toBeEnabled();

  const chart = page.getByRole("link", { name: "Chart" }).first();
  await expect(chart).toHaveAttribute("href", `/backtest?market=NSE&runId=${runs[0]}`);
  await page.getByLabel("Ranking", { exact: true }).selectOption("RETURN_DRAWDOWN");
  await expect(page.getByText("Leader", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Net P&L" }).click();
  await expect(page.getByRole("columnheader", { name: "Net P&L" }).first()).toHaveAttribute("aria-sort", "ascending");
  const strategyComparison = page.getByRole("heading", { name: "Strategy comparison" }).locator("xpath=ancestor::section[1]");
  await strategyComparison.getByRole("button", { name: "JSON" }).first().click();
  await expect(strategyComparison.getByText(/Exact immutable configuration/)).toBeVisible();
  await expect(page.getByRole("group", { name: "Equity curve selection" }).getByRole("checkbox")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "Training versus unseen comparison" })).toBeVisible();
  const foldComparison = page.getByRole("heading", { name: "Training versus unseen comparison" }).locator("xpath=ancestor::section[1]");
  await expect(foldComparison.getByText("TRAINING", { exact: true })).toBeVisible();
  await expect(foldComparison.getByText("UNSEEN TEST", { exact: true })).toBeVisible();
  await expect(foldComparison.getByRole("link", { name: "Chart" }).last()).toHaveAttribute("href", "/backtest?market=NSE&runId=75555555-5555-4555-8555-555555555555");

  await page.getByLabel("rsi_low values").fill("[20, 25, 30]");
  await expect(page.getByText(/Preview stale/)).toBeVisible();
  await expect(runButton).toBeDisabled();
  await page.getByLabel("rsi_low values").fill("[25, 30]");
  await expect(runButton).toBeDisabled();
  await page.getByLabel("rsi_low values").fill("[20, 25, 30]");
  await page.getByRole("button", { name: "Preview experiment" }).click();
  await expect(page.getByText(/20,100 symbol-runs/)).toBeVisible();
  await expect(runButton).toBeDisabled();

  const walkRun = page.getByRole("button", { name: "Run validation" });
  await expect(walkRun).toBeDisabled();
  await page.getByRole("button", { name: "Preview walk-forward" }).click();
  await expect(page.getByRole("heading", { name: "Walk-forward workload preview" })).toBeVisible();
  await expect(page.getByText("TRAINING").first()).toBeVisible();
  await expect(page.getByText("UNSEEN TEST").first()).toBeVisible();
  await expect(walkRun).toBeEnabled();
  await page.getByLabel("Training sessions").fill("25");
  await expect(page.getByText(/Preview stale/).last()).toBeVisible();
  await expect(walkRun).toBeDisabled();
  await expect(page.getByText(/Approve for|Deploy/)).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test("strategies adds a configured instrument with one compact control", async ({ page }) => {
  await page.unroute("**/api/platform?**");
  await page.unroute("**/api/v2/**");
  let added: unknown;
  await page.route("**/api/platform?**", async (route) => {
    const request = route.request();
    const parameters = new URL(request.url()).searchParams;
    if (parameters.get("action") === "overview") return route.fulfill({ json: { ...overview, environment: "CRYPTO" } });
    if (parameters.get("action") === "instruments" && request.method() === "GET") return route.fulfill({ json: { rows: [], count: 0, offset: 0, limit: 1 } });
    if (parameters.get("action") === "add-instrument" && request.method() === "POST") {
      added = request.postDataJSON();
      return route.fulfill({ json: { instrument: { providerSymbol: "BTC-USDT" } } });
    }
    return route.fulfill({ status: 404, json: { detail: "Unexpected platform route" } });
  });
  await page.route("**/api/v2/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const strategy = { strategyId: "rsi_dip_ladder", name: "RSI Dip Ladder", version: "1.0.0", supportedMarkets: ["CRYPTO"], supportedTimeframes: ["5m"], configSchema: {}, defaults: {} };
    if (path.endsWith("/strategies")) return route.fulfill({ json: { strategies: [strategy], markets: ["NSE", "CRYPTO"], riskDefaults: {}, riskSchema: {} } });
    if (path.endsWith("/strategy-deployments")) return route.fulfill({ json: { deployments: [] } });
    if (path.endsWith("/screener/universes")) return route.fulfill({ json: { active: {}, universes: [] } });
    if (path.endsWith("/strategies/rsi_dip_ladder/config")) return route.fulfill({ json: { strategyId: strategy.strategyId, market: "CRYPTO", active: null, effectiveConfiguration: {}, effectiveRiskSettings: {}, all: [] } });
    if (path.endsWith("/strategies/rsi_dip_ladder/deployment")) return route.fulfill({ json: { market: "CRYPTO", strategyId: strategy.strategyId, timeframe: "5m", mode: "OFF" } });
    return route.fulfill({ status: 404, json: { detail: "Unexpected v2 route" } });
  });

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/settings?market=CRYPTO");
  const disclosure = page.getByText("Instrument setup", { exact: true });
  await expect(page.getByPlaceholder("BTC-USDT")).toBeHidden();
  await disclosure.click();
  const input = page.getByPlaceholder("BTC-USDT");
  const button = page.getByRole("button", { name: "Add symbol" });
  await expect(input).toBeVisible();
  const tops = await Promise.all([input, button].map((control) => control.evaluate((element) => Math.round(element.getBoundingClientRect().top))));
  expect(Math.max(...tops) - Math.min(...tops)).toBeLessThanOrEqual(2);
  await input.fill("btc-usdt");
  await button.click();
  await expect(page.getByText("BTC-USDT is now available to watchlists, backtests and strategies.")).toBeVisible();
  expect(added).toEqual({ market: "CRYPTO", symbol: "BTC-USDT" });
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
        { symbol: "RELIANCE", lotId: "lot-hit", status: "TARGET_HIT", entryTimestamp: "2026-09-01T09:35:00Z", entryPrice: 200, quantity: 5, targetPrice: 202, exitTimestamp: "2026-09-01T10:00:00Z", exitPrice: 202, netPnl: 10, holdingMinutes: 25 },
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

  const tradeScroller = page.locator(".quant-trades-scroll").first();
  const tradeTable = page.locator(".quant-trades-table").first();
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
  const holdingBounds = await tradeTable.getByRole("columnheader", { name: /Holding/ }).evaluate((header) => {
    const headerBox = header.getBoundingClientRect();
    const scrollerBox = header.closest(".quant-trades-scroll")?.getBoundingClientRect();
    return scrollerBox ? {
      headerLeft: headerBox.left,
      headerRight: headerBox.right,
      scrollerLeft: scrollerBox.left,
      scrollerRight: scrollerBox.right,
    } : null;
  });
  expect(holdingBounds).not.toBeNull();
  expect(holdingBounds?.headerLeft ?? 0).toBeGreaterThanOrEqual((holdingBounds?.scrollerLeft ?? 0) - 1);
  expect(holdingBounds?.headerRight ?? 0).toBeLessThanOrEqual((holdingBounds?.scrollerRight ?? 0) + 1);
  await expect(page.getByText("Unrealized", { exact: true })).toBeVisible();
  await expect(page.getByText("99.5", { exact: true })).toBeVisible();
  await expect(page.getByText("12 bars", { exact: true })).toBeVisible();
  await expect(page.getByText("01 Sept 2026, 15:00 IST", { exact: true })).toBeVisible();
  await expect(page.getByText("01 Sept 2026, 15:30 IST", { exact: true })).toBeVisible();

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

test("desktop navigation stays on one row and the workspace uses the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/screener");
  await expect(page.locator(".platform-topnav")).toBeVisible();
  await expect(page.locator(".platform-sidebar, .platform-menu, .platform-backdrop")).toHaveCount(0);
  await expect.poll(() => page.locator(".platform-content").evaluate((element) => Number.parseFloat(getComputedStyle(element).marginLeft))).toBeLessThanOrEqual(1);
  const shell = await page.locator(".platform-topbar").evaluate((topbar) => {
    const bar = topbar.getBoundingClientRect();
    const children = Array.from(topbar.children).map((element) => element.getBoundingClientRect());
    return {
      height: bar.height,
      childrenFitOneRow: children.every((box) => box.top >= bar.top - 1 && box.bottom <= bar.bottom + 1),
    };
  });
  expect(shell.height).toBeLessThanOrEqual(60);
  expect(shell.childrenFitOneRow).toBe(true);
  const navFits = await page.locator(".platform-topnav").evaluate((nav) => nav.scrollWidth <= nav.clientWidth + 1);
  expect(navFits).toBe(true);
  const compactNavigation = await page.locator(".platform-topnav").evaluate((nav) => ({
    width: nav.getBoundingClientRect().width,
    linkWidths: Array.from(nav.querySelectorAll("a"), (link) => link.getBoundingClientRect().width),
    flexGrow: getComputedStyle(nav).flexGrow,
  }));
  expect(compactNavigation.width).toBeLessThanOrEqual(350);
  expect(compactNavigation.flexGrow).toBe("0");
  expect(compactNavigation.linkWidths).toEqual([40, 40, 40, 40, 40, 40, 40, 40]);
  const settingsLink = page.getByRole("link", { name: "Strategies", exact: true });
  const collapsedWidth = await settingsLink.evaluate((link) => link.getBoundingClientRect().width);
  await settingsLink.hover();
  await expect(settingsLink.locator(".platform-nav-label")).toBeVisible();
  await expect.poll(() => settingsLink.evaluate((link) => link.getBoundingClientRect().width)).toBeGreaterThan(collapsedWidth + 35);
  const workspaceWidth = await page.locator(".quant-workspace").evaluate((element) => element.getBoundingClientRect().width);
  expect(workspaceWidth).toBeGreaterThanOrEqual(1400);
  const screenerColumns = await page.locator(".quant-screener-layout").evaluate((layout) => {
    const sidebar = layout.querySelector(".quant-screener-sidebar")?.getBoundingClientRect();
    const results = Array.from(layout.children).at(1)?.getBoundingClientRect();
    return sidebar && results ? {
      aligned: Math.abs(sidebar.top - results.top) <= 1,
      sidebarWidth: sidebar.width,
      resultsWidth: results.width,
    } : null;
  });
  expect(screenerColumns).not.toBeNull();
  expect(screenerColumns?.aligned).toBe(true);
  expect(screenerColumns?.resultsWidth ?? 0).toBeGreaterThan((screenerColumns?.sidebarWidth ?? 0) * 2);
  expect(await page.evaluate(() => window.localStorage.getItem("opendelta-sidebar-open"))).toBeNull();
});

test("theme preference persists while navigating between product areas", async ({ page }) => {
  await page.goto("/");
  const themeToggle = page.getByRole("button", { name: "Switch to light theme" });
  await themeToggle.click();
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-theme", "light");

  await page.goto("/backtest");
  await expect(page.locator(".platform-frame")).toHaveAttribute("data-theme", "light");
  await expect(page.getByRole("button", { name: "Switch to dark theme" })).toBeVisible();

  await page.goto("/screener");
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
