import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const key = "daily_scalping_watchlist";

async function sources() {
  const [definitions, dashboard, panel, results, history] = await Promise.all([
    readFile(new URL("../../strategy-parameters.json", import.meta.url), "utf8").then(JSON.parse),
    readFile(new URL("app/backtest/backtest-dashboard.tsx", root), "utf8"),
    readFile(new URL("app/backtest/daily-watchlist-settings.tsx", root), "utf8"),
    readFile(new URL("app/backtest/daily-watchlist-results.tsx", root), "utf8"),
    readFile(new URL("app/backtest/backtest-history.ts", root), "utf8"),
  ]);
  return { definitions, dashboard, panel, results, history };
}

test("Daily Watchlist is a standalone selectable strategy", async () => {
  const { dashboard, history } = await sources();
  assert.match(dashboard, /Daily Scalping Watchlist \+ Top-5 ORB<\/button>/);
  assert.match(dashboard, /dailyWatchlistConfiguration: dailyWatchlistSettings/);
  assert.match(dashboard, /isDailyWatchlistResponse/);
  assert.match(history, /daily_scalping_watchlist/);
  assert.doesNotMatch(dashboard, /vwapPullbackConfiguration: dailyWatchlistSettings/);
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
  assert.match(panel, /disabled=\{definition.key === "quantityPerTrade"\}/);
  assert.match(dashboard, /quantityPerTrade: 50/);
});

test("settings remain compact and research-only", async () => {
  const { panel } = await sources();
  for (const heading of ["Basic settings", "Watchlist selection", "Opening range breakout", "Midday breakout", "Exit and risk", "Advanced settings"]) {
    assert.match(panel, new RegExp(heading));
  }
  assert.doesNotMatch(panel, /<details[^>]* open/);
  assert.match(panel, /live broker orders are disabled/i);
});

test("results expose required audit and comparison fields", async () => {
  const { results } = await sources();
  for (const label of [
    "Daily selected symbols", "Complete intraday watchlist history", "Midday replacements",
    "Opening and midday BUY signals", "FROZEN_OPEN top five", "ROLLING top five",
    "Top two", "Full eligible universe", "Liquidity-only top five",
    "Causally matched random five", "Development", "Untouched validation",
    "Gross P&L", "Costs", "Net P&L", "50 shares", "Effective settings",
  ]) assert.match(results, new RegExp(label, "i"));
  assert.match(results, /Export Markdown/);
  assert.match(results, /JSON.stringify\(response.metadata.effectiveConfiguration/);
  assert.match(results, /Live orders remain disabled/);
});

test("generic JSON configuration is used for the standalone strategy", async () => {
  const { dashboard } = await sources();
  assert.equal((dashboard.match(/<JsonConfigurationEditor/g) ?? []).length, 1);
  assert.match(dashboard, /currentStrategyValues\(strategyMode\)/);
  assert.match(dashboard, /applyDailyWatchlistValues/);
});
