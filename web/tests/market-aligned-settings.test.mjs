import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function sources() {
  const [definitions, dashboard, controls, styles, results] = await Promise.all([
    readFile(new URL("../../strategy-parameters.json", import.meta.url), "utf8").then(JSON.parse),
    readFile(new URL("app/backtest/backtest-dashboard.tsx", root), "utf8"),
    readFile(new URL("app/backtest/strategy-parameters.tsx", root), "utf8"),
    readFile(new URL("app/globals.css", root), "utf8"),
    readFile(new URL("app/backtest/recovery-results.tsx", root), "utf8"),
  ]);
  return { definitions, dashboard, controls, styles, results };
}

function definition(items, key) {
  const item = items.find((candidate) => candidate.strategy === "market_aligned_rsi_scalper" && candidate.key === key);
  assert.ok(item, `missing ${key}`);
  return item;
}

function valid(item, value) {
  return Number.isFinite(value)
    && (item.type !== "integer" || Number.isInteger(value))
    && (item.minimum === null || value >= item.minimum)
    && (item.maximum === null || value <= item.maximum)
    && (item.step === null || Math.abs(value / item.step - Math.round(value / item.step)) < 1e-8);
}

test("normal decimals and counts follow aligned constraints", async () => {
  const { definitions } = await sources();
  assert.ok(valid(definition(definitions, "maximumIntrabarRangePct"), 5));
  assert.ok(valid(definition(definitions, "targetPct"), 0.5));
  assert.ok(valid(definition(definitions, "oiVolatilityPriceRisePct"), 0.25));
  assert.ok(valid(definition(definitions, "oiOptionsWeight"), 0.35));
  assert.ok(valid(definition(definitions, "oiMinimumCoverage"), 0.65));
  assert.ok(valid(definition(definitions, "minimumRvol"), 1.5));
  assert.ok(valid(definition(definitions, "oiBullishThreshold"), 20));
  assert.ok(valid(definition(definitions, "oiStronglyBullishThreshold"), 60));
  assert.ok(valid(definition(definitions, "minimumAlignmentScore"), 90));
  assert.ok(valid(definition(definitions, "oiElevatedQualityThreshold"), 95));
  assert.ok(valid(definition(definitions, "minimumAverageTradedValue"), 10000));
  assert.equal(valid(definition(definitions, "maximumTradesPerDay"), 2.5), false);
});

test("recommended, strict and custom preset behavior is explicit", async () => {
  const { dashboard } = await sources();
  assert.match(dashboard, /minimumAlignmentScore: 90, maximumTradesPerDay: 2, oiMode: "ADVISORY"/);
  assert.match(dashboard, /setMarketPreset\("Custom"\)/);
  assert.match(dashboard, /Restore recommended defaults/);
  assert.match(dashboard, /disabled=\{strategyMode === "market_aligned_rsi_scalper" && marketFormInvalid\}/);
});

test("expert settings start collapsed and normal controls stay understandable", async () => {
  const { dashboard } = await sources();
  assert.match(dashboard, /<details className="advanced-settings market-advanced-settings">/);
  assert.doesNotMatch(dashboard, /<details className="advanced-settings market-advanced-settings" open/);
  assert.match(dashboard, /<details className="expert-oi-settings">/);
  assert.doesNotMatch(dashboard, /<details className="expert-oi-settings" open/);
  for (const heading of ["Main settings", "Entry filters", "Risk and execution", "Open Interest", "Expert OI settings"]) {
    assert.match(dashboard, new RegExp(heading));
  }
  assert.match(dashboard, /<b>OFF<\/b> — OI is ignored/);
  assert.match(dashboard, /<b>ADVISORY<\/b> — OI is displayed but cannot block trades/);
});

test("Market-Aligned main settings can be collapsed after initial review", async () => {
  const dashboard = await readFile(new URL("app/backtest/backtest-dashboard.tsx", root), "utf8");
  assert.match(dashboard, /<details className="market-settings-card market-settings-section market-main-settings" open>/);
  assert.match(dashboard, /<summary><span><span className="section-kicker">Common controls<\/span><h2 id="market-main-settings-title">Main settings<\/h2>/);
  assert.match(dashboard, /<ChevronDown size=\{17\} \/><\/summary>/);
});

test("strategy configurations are saved separately and submitted separately", async () => {
  const { dashboard } = await sources();
  assert.match(dashboard, /vento-nse-backtest-preset:\$\{strategyMode\}/);
  assert.match(dashboard, /vento-nse-backtest-preset:\$\{next\}/);
  assert.match(dashboard, /currentStrategyValues\(strategyMode\)/);
  assert.match(dashboard, /marketAlignedConfiguration:/);
  assert.match(dashboard, /switchStrategy\("rsi_recovery"\)/);
  assert.match(dashboard, /switchStrategy\("market_aligned_rsi_scalper"\)/);
});

test("responsive styles prevent horizontal overflow and keep fields grouped", async () => {
  const { styles } = await sources();
  assert.match(styles, /\.backtest-shell \{ overflow-x: clip; \}/);
  assert.match(styles, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(styles, /\.market-settings-grid > \*[^{]*\{[^}]*min-width: 0/s);
  assert.match(styles, /\.parameter-input-row[^{]*\{[^}]*grid-template-columns: minmax\(0, 1fr\) auto/s);
  assert.match(styles, /@media \(max-width: 820px\)[\s\S]*\.market-settings-grid,[\s\S]*grid-template-columns: 1fr/);
  assert.match(styles, /\.json-configuration textarea[^{]*\{[^}]*width: 100%[^}]*max-height: 400px[^}]*overflow: auto/s);
  assert.match(styles, /\.json-configuration-toolbar[^{]*\{[^}]*flex-wrap: wrap/s);
  assert.doesNotMatch(styles, /\.market-aligned-settings[^}]*font-size:\s*(?:[0-9]|1[01])px/s);
});

test("numeric editing is application-validated and results hide diagnostics initially", async () => {
  const { controls, results } = await sources();
  assert.match(controls, /onBlur=/);
  assert.match(controls, /if \(!trimmed\) return "Enter a value\."/);
  assert.match(controls, /value=\{draft\}/);
  assert.match(results, /market-result-diagnostics/);
  assert.match(results, /Net P&amp;L/);
  assert.match(results, /Skipped signals/);
});

test("all native numeric controls use the shared constraints", async () => {
  const { dashboard } = await sources();
  const nativeNumberInputs = [...dashboard.matchAll(/<input[^>]*type="number"[^>]*>/g)].map((match) => match[0]);
  assert.ok(nativeNumberInputs.length > 0);
  for (const input of nativeNumberInputs) assert.match(input, /numericConstraints\(/);
});

test("Market-Aligned results expose a cumulative funnel and complete skipped-candidate audit", async () => {
  const { results, styles } = await sources();
  for (const label of [
    "RSI armed", "RSI recovery candidates", "Time-window passed", "NIFTY passed",
    "Sector passed", "Breadth passed", "Relative strength passed", "VWAP passed",
    "EMA passed", "RVOL passed", "Liquidity passed", "Room passed", "Score passed",
    "Executed trades",
  ]) assert.match(results, new RegExp(label));
  assert.match(results, /<h2>Skipped Candidates<\/h2>/);
  assert.match(results, /candidate\.rejectionReasonDetails|item\.rejectionReasonDetails/);
  assert.match(results, /REJECTED_GATE/);
  assert.match(results, /SKIPPED_DATA_UNAVAILABLE/);
  assert.match(results, /sectorMappingFound/);
  assert.match(results, /breadthSymbolCount/);
  assert.match(results, /oiResult/);
  assert.match(styles, /\.candidate-table-wrap[^{]*\{[^}]*max-width: 100%[^}]*overflow: auto/s);
  assert.match(styles, /@media \(max-width: 1100px\)[\s\S]*\.candidate-diagnostics-table/);
});
