import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  createJsonConfiguration,
  formatJsonConfiguration,
  parseAndValidateJsonConfiguration,
} from "../app/legacy/backtest/json-configuration.mjs";

const definitions = JSON.parse(await readFile(new URL("../../data/strategy-parameters.json", import.meta.url), "utf8"));
const strategyNames = {
  rsi_range: "RSI Range Strategy",
  rsi_recovery: "RSI Recovery Scalping",
};

function currentSettings(strategyKey, overrides = {}) {
  const settings = {
    symbols: ["LUPIN"],
    universeMode: "selected",
    durationYears: 1,
    timeframe: strategyKey === "rsi_range" ? "1d" : "5m",
  };
  for (const definition of definitions.filter((item) => item.strategy === strategyKey)) {
    settings[definition.key] = definition.default;
  }
  return { ...settings, ...overrides };
}

function validate(strategyKey, settings = currentSettings(strategyKey)) {
  const configuration = createJsonConfiguration(strategyKey, settings, definitions);
  return parseAndValidateJsonConfiguration(formatJsonConfiguration(configuration), {
    strategyKey,
    strategyNames,
    definitions,
    currentSettings: settings,
  });
}

test("all registered strategies produce complete valid JSON configurations", () => {
  for (const strategyKey of Object.keys(strategyNames)) {
    const settings = currentSettings(strategyKey);
    const configuration = createJsonConfiguration(strategyKey, settings, definitions);
    const strategyDefinitions = definitions.filter((item) => item.strategy === strategyKey);
    assert.equal(Object.keys(configuration.settings).length, strategyDefinitions.length + 4);
    assert.equal(validate(strategyKey).valid, true, strategyKey);
  }
});

test("valid JSON is summarized and can update form-controlled values", () => {
  const settings = currentSettings("rsi_recovery");
  const configuration = createJsonConfiguration("rsi_recovery", {
    ...settings,
    executionModel: "NEXT_BAR_OPEN",
    maxHoldingTradingDays: 3,
  }, definitions);
  const result = parseAndValidateJsonConfiguration(formatJsonConfiguration(configuration), {
    strategyKey: "rsi_recovery",
    strategyNames,
    definitions,
    currentSettings: settings,
  });
  assert.equal(result.valid, true);
  assert.equal(result.summary.changed, 2);
  assert.equal(result.configuration.settings.executionModel, "NEXT_BAR_OPEN");
  assert.equal(result.configuration.settings.maxHoldingTradingDays, 3);
});

test("unknown, missing, invalid and wrong-strategy settings are never partially accepted", () => {
  const settings = currentSettings("rsi_range");
  const configuration = createJsonConfiguration("rsi_range", settings, definitions);
  configuration.settings.rsiLenght = 14;
  delete configuration.settings.entryLow;
  const invalid = parseAndValidateJsonConfiguration(JSON.stringify(configuration), {
    strategyKey: "rsi_range",
    strategyNames,
    definitions,
    currentSettings: settings,
  });
  assert.equal(invalid.valid, false);
  assert.equal(invalid.configuration, null);
  assert.ok(invalid.errors.includes("Unknown setting: rsiLenght"));
  assert.ok(invalid.errors.includes("Missing setting: entryLow"));

  const other = createJsonConfiguration("rsi_recovery", currentSettings("rsi_recovery"), definitions);
  const mismatch = parseAndValidateJsonConfiguration(JSON.stringify(other), {
    strategyKey: "rsi_range",
    strategyNames,
    definitions,
    currentSettings: settings,
  });
  assert.equal(mismatch.valid, false);
  assert.equal(mismatch.belongsToStrategyKey, "rsi_recovery");
  assert.deepEqual(mismatch.errors, ["This JSON belongs to RSI Recovery Scalping."]);
});

test("schema, enum, type, range, step and related-field validation is strict", () => {
  const settings = currentSettings("rsi_recovery");
  const base = createJsonConfiguration("rsi_recovery", settings, definitions);
  for (const [key, value, expected] of [
    ["schemaVersion", 2, "Unsupported schemaVersion: 2"],
    ["exitModel", "UNKNOWN", "exitModel must be one of"],
    ["maxHoldingTradingDays", 2.5, "maxHoldingTradingDays must be a whole number"],
    ["rsiArmHigh", 101, "rsiArmHigh must be at most 100"],
    ["targetPct", 0.005, "targetPct must use increments of 0.01"],
  ]) {
    const candidate = structuredClone(base);
    if (key === "schemaVersion") candidate.schemaVersion = value;
    else candidate.settings[key] = value;
    const result = parseAndValidateJsonConfiguration(JSON.stringify(candidate), {
      strategyKey: "rsi_recovery",
      strategyNames,
      definitions,
      currentSettings: settings,
    });
    assert.equal(result.valid, false);
    assert.ok(result.errors.some((error) => error.includes(expected)), `${key}: ${result.errors.join("; ")}`);
  }

  const related = structuredClone(base);
  related.settings.rsiArmLow = related.settings.rsiArmHigh + 1;
  const relatedResult = parseAndValidateJsonConfiguration(JSON.stringify(related), {
    strategyKey: "rsi_recovery",
    strategyNames,
    definitions,
    currentSettings: settings,
  });
  assert.equal(relatedResult.valid, false);
  assert.ok(relatedResult.errors.some((error) => error.includes("rsiArmLow must be below rsiArmHigh")));
});

test("resolved output includes advanced values but excludes unregistered secrets", () => {
  const configuration = createJsonConfiguration("rsi_recovery", {
    ...currentSettings("rsi_recovery"),
    rupeeRiskBudget: 10,
    dhanToken: "never-copy-this",
    databasePassword: "never-copy-this",
  }, definitions);
  const source = formatJsonConfiguration(configuration);
  assert.match(source, /"rupeeRiskBudget": 10/);
  assert.doesNotMatch(source, /dhanToken|databasePassword|never-copy-this/);
});

test("a future registered strategy automatically uses the same serializer and validator", () => {
  const futureDefinitions = [
    ...definitions,
    { strategy: "future_strategy", key: "lookback", type: "integer", default: 12, minimum: 1, maximum: 100, step: 1 },
    { strategy: "future_strategy", key: "mode", type: "select", default: "SAFE", options: ["SAFE", "FAST"], minimum: null, maximum: null, step: null },
  ];
  const settings = {
    symbols: ["LUPIN"], universeMode: "selected", durationYears: 1, timeframe: "15m",
    lookback: 20, mode: "SAFE",
  };
  const configuration = createJsonConfiguration("future_strategy", settings, futureDefinitions);
  const result = parseAndValidateJsonConfiguration(JSON.stringify(configuration), {
    strategyKey: "future_strategy",
    strategyNames: { future_strategy: "Future Strategy" },
    definitions: futureDefinitions,
    currentSettings: settings,
  });
  assert.equal(result.valid, true);
  assert.equal(result.configuration.settings.lookback, 20);
});

test("the inline editor is one collapsed generic component with every required action", async () => {
  const editor = await readFile(new URL("../app/legacy/backtest/json-configuration-editor.tsx", import.meta.url), "utf8");
  const dashboard = await readFile(new URL("../app/legacy/backtest/backtest-dashboard.tsx", import.meta.url), "utf8");
  assert.match(editor, /<details className="json-configuration advanced-settings">/);
  assert.doesNotMatch(editor, /<details className="json-configuration advanced-settings" open/);
  for (const action of ["Copy JSON", "Paste from clipboard", "Format", "Validate", "Apply", "Reset"]) {
    assert.match(editor, new RegExp(`>${action}<`));
  }
  assert.match(editor, /if \(!result\.valid \|\| !result\.configuration/);
  assert.match(dashboard, /<JsonConfigurationEditor/);
  assert.equal((dashboard.match(/<JsonConfigurationEditor/g) ?? []).length, 1);
  assert.match(dashboard, /key=\{strategyMode\}/);
});
