import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const key = "top_5_opening_range_breakout";

async function sources() {
  const [definitions, dashboard, panel, results, history, backendImage] = await Promise.all([
    readFile(new URL("../../strategy-parameters.json", import.meta.url), "utf8").then(JSON.parse),
    readFile(new URL("app/backtest/backtest-dashboard.tsx", root), "utf8"),
    readFile(new URL("app/backtest/daily-watchlist-settings.tsx", root), "utf8"),
    readFile(new URL("app/backtest/daily-watchlist-results.tsx", root), "utf8"),
    readFile(new URL("app/backtest/backtest-history.ts", root), "utf8"),
    readFile(new URL("deploy/backtest.Dockerfile", root), "utf8"),
  ]);
  return { definitions, dashboard, panel, results, history, backendImage };
}

test("Top-5 Opening Range Breakout is a standalone selectable strategy", async () => {
  const { dashboard, history, backendImage } = await sources();
  assert.match(dashboard, /Top-5 Opening Range Breakout<\/button>/);
  assert.match(dashboard, /createTop5OpeningRangeBreakoutRequest/);
  assert.match(dashboard, /isTop5OpeningRangeBreakoutResponse/);
  assert.match(history, /top_5_opening_range_breakout/);
  assert.doesNotMatch(dashboard, /Market-Aligned VWAP Pullback Scalper<\/button>/);
  assert.doesNotMatch(dashboard, /vwapPullbackConfiguration:/);
  assert.match(backendImage, /daily_scalping_watchlist\.py/);
});

test("recommended configuration keeps fixed quantity exactly 50", async () => {
  const { definitions, panel, dashboard } = await sources();
  const defaults = Object.fromEntries(definitions.filter((item) => item.strategy === key).map((item) => [item.key, item.default]));
  assert.equal(defaults.quantityPerTrade, 50);
  assert.equal(definitions.find((item) => item.strategy === key && item.key === "quantityPerTrade").minimum, 50);
  assert.equal(definitions.find((item) => item.strategy === key && item.key === "quantityPerTrade").maximum, 50);
  assert.equal(defaults.watchlistMode, "FROZEN_OPEN");
  assert.equal(defaults.watchlistSelectedSymbols, 5);
  assert.equal(defaults.watchlistPrimarySymbols, 2);
  assert.equal(defaults.watchlistRescanIntervalMinutes, 30);
  assert.equal(defaults.maximumHoldingBars, 12);
  assert.equal(defaults.minimumPrice, 100);
  assert.equal(defaults.maximumPrice, 5000);
  assert.equal(defaults.minimumMedianDailyTradedValue, 100000000);
  assert.equal(defaults.minimumOpeningTradedValue, 2500000);
  assert.equal(defaults.minimumDailyAtrPct, 0.8);
  assert.equal(defaults.maximumDailyAtrPct, 4.0);
  assert.equal(defaults.maximumOpeningGapPct, 3.0);
  assert.match(panel, /disabled=\{definition.key === "quantityPerTrade"\}/);
  assert.match(dashboard, /quantityPerTrade: 50/);
});

test("settings remain compact and research-only", async () => {
  const { panel } = await sources();
  for (const heading of ["Basic settings", "Watchlist selection", "Universe eligibility", "Opening range breakout", "Midday breakout", "Exit and risk", "Advanced settings"]) {
    assert.match(panel, new RegExp(heading));
  }
  assert.doesNotMatch(panel, /<details[^>]* open/);
  assert.match(panel, /live broker orders are disabled/i);
});

test("results expose required audit and comparison fields", async () => {
  const { results } = await sources();
  for (const label of [
    "Daily selected symbols", "Complete intraday watchlist history", "Midday replacements",
    "Opening and midday BUY signals", "Top-5 FROZEN_OPEN", "Top-5 ROLLING",
    "Top-2", "Full-universe baseline", "Liquidity-only baseline",
    "Random-five baseline", "Development", "Untouched validation",
    "Costs", "50 shares", "Effective settings",
    "Symbols requested", "Eligible at least once", "Actually scored", "Frozen replacements",
    "Rolling rescans", "Selector-value diagnostics", "CSV / JSON downloads",
    "Signal", "Decision", "Entry candle",
  ]) assert.match(results, new RegExp(label, "i"));
  assert.match(results, /Gross P&amp;L/i);
  assert.match(results, /Net P&amp;L/i);
  assert.match(results, /Export Markdown/);
  assert.match(results, /JSON.stringify\(response.metadata.effectiveConfiguration/);
  assert.match(results, /Live orders remain disabled/);
  for (const dataset of ["daily-watchlists", "candidates", "signals", "trades", "benchmark-results"]) {
    assert.match(results, new RegExp(dataset));
  }
});

test("generic JSON configuration is used for the standalone strategy", async () => {
  const { dashboard } = await sources();
  assert.equal((dashboard.match(/<JsonConfigurationEditor/g) ?? []).length, 1);
  assert.match(dashboard, /currentStrategyValues\(strategyMode\)/);
  assert.match(dashboard, /applyTop5OpeningRangeBreakoutValues/);
});
