import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
const key = "market_aligned_vwap_pullback_scalper";

async function sources() {
  const [definitions, dashboard, panel, styles, results] = await Promise.all([
    readFile(new URL("../../strategy-parameters.json", import.meta.url), "utf8").then(JSON.parse),
    readFile(new URL("app/backtest/backtest-dashboard.tsx", root), "utf8"),
    readFile(new URL("app/backtest/vwap-pullback-settings.tsx", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
    readFile(new URL("app/backtest/vwap-pullback-results.tsx", root), "utf8"),
  ]);
  return { definitions, dashboard, panel, styles, results };
}

function definition(items, field) {
  const item = items.find((candidate) => candidate.strategy === key && candidate.key === field);
  assert.ok(item, `missing ${field}`);
  return item;
}

function valid(item, value) {
  return Number.isFinite(value)
    && (item.type !== "integer" || Number.isInteger(value))
    && (item.minimum === null || value >= item.minimum)
    && (item.maximum === null || value <= item.maximum)
    && (item.step === null || Math.abs(value / item.step - Math.round(value / item.step)) < 1e-8);
}

test("new strategy numeric constraints are aligned", async () => {
  const { definitions } = await sources();
  for (const [field, value] of [
    ["buyCostBps", 5], ["maximumEntryGapAtr", 0.5], ["pullbackApproachAtr", 0.25],
    ["minimumStopPct", 0.35], ["volatilityStopAtr", 0.65], ["rewardRiskRatio", 1.5],
    ["maximumHoldingBars", 20], ["minimumQualityScore", 60], ["maximumTriggerRsi", 90],
    ["minimumAverageTradedValue", 10000],
  ]) assert.ok(valid(definition(definitions, field), value), `${field} rejected ${value}`);
  assert.equal(valid(definition(definitions, "maximumTradesPerDay"), 2.5), false);
});

test("selector exposes only the launchable EMA/VWAP Strong Buy strategy", async () => {
  const { dashboard } = await sources();
  assert.match(dashboard, />EMA\/VWAP Strong Buy<\/button>/);
  assert.doesNotMatch(dashboard, />RSI Range Strategy<\/button>/);
  assert.doesNotMatch(dashboard, />RSI Recovery Scalping<\/button>/);
  assert.doesNotMatch(dashboard, />Top-5 Opening Range Breakout<\/button>/);
  assert.doesNotMatch(dashboard, />Market-Aligned VWAP Pullback Scalper<\/button>/);
  assert.doesNotMatch(dashboard, />Market-Aligned RSI Scalper<\/button>/);
  assert.doesNotMatch(dashboard, /vwapPullbackConfiguration:/);
  assert.doesNotMatch(dashboard, /marketAlignedConfiguration:/);
});

test("compact settings sections are collapsed and JSON remains generic", async () => {
  const { panel, dashboard } = await sources();
  for (const heading of ["Basic settings", "Entry rules", "Exit and risk", "Market context", "Advanced settings"]) {
    assert.match(panel, new RegExp(heading));
  }
  assert.doesNotMatch(panel, /<details[^>]* open/);
  assert.match(dashboard, /<JsonConfigurationEditor/);
  assert.equal((dashboard.match(/<JsonConfigurationEditor/g) ?? []).length, 1);
});

test("defaults match the recommended research configuration", async () => {
  const { definitions } = await sources();
  const defaults = Object.fromEntries(definitions.filter((item) => item.strategy === key).map((item) => [item.key, item.default]));
  assert.equal(defaults.executionModel, "NEXT_BAR_OPEN");
  assert.equal(defaults.rsiPullbackMinimum, 38);
  assert.equal(defaults.rsiPullbackMaximum, 50);
  assert.equal(defaults.maximumTriggerRsi, 65);
  assert.equal(defaults.minimumTriggerRvol, 1.2);
  assert.equal(defaults.maximumTradesPerDay, 5);
  assert.equal(defaults.maximumConcurrentTrades, 2);
  assert.equal(defaults.oiMode, "OFF");
  assert.equal(defaults.enforceMinimumQualityScore, false);
});

test("results separate raw candidates, signals, executions, funnel and rejection reasons", async () => {
  const { results } = await sources();
  for (const label of ["Raw candidates", "Accepted BUY signals", "Executed trades", "Rejected candidates", "Candidate funnel", "Rejection diagnostics"]) {
    assert.match(results, new RegExp(label));
  }
  for (const label of ["Trend-qualified bars", "Pullbacks armed", "Valid trigger candles", "Gap skips", "Risk-width skips"]) {
    assert.match(results, new RegExp(label));
  }
});

test("responsive styles prevent horizontal overflow and group controls", async () => {
  const { styles } = await sources();
  assert.match(styles, /\.backtest-shell \{ overflow-x: clip; \}/);
  assert.match(styles, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(styles, /@media \(max-width: 820px\)[\s\S]*\.market-settings-grid,[\s\S]*grid-template-columns: 1fr/);
  assert.match(styles, /\.json-configuration textarea[^{]*\{[^}]*width: 100%[^}]*max-height: 400px[^}]*overflow: auto/s);
});

test("retired results remain readable but cannot be submitted", async () => {
  const { dashboard } = await sources();
  assert.match(dashboard, /Retired strategy — cannot run again/);
  assert.match(dashboard, /strategyMode === "market_aligned_rsi_scalper"/);
  assert.match(dashboard, /metadata\.strategyMode === "market_aligned_vwap_pullback_scalper"/);
  assert.doesNotMatch(dashboard, /switchStrategy\("market_aligned_rsi_scalper"\)/);
  assert.doesNotMatch(dashboard, /switchStrategy\("market_aligned_vwap_pullback_scalper"\)/);
});
