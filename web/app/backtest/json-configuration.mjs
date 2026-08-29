export const JSON_CONFIGURATION_SCHEMA_VERSION = 1;

export const RUN_SETTING_DEFINITIONS = [
  { key: "symbols", type: "string-array", default: [], minimum: null, maximum: null, step: null },
  { key: "universeMode", type: "select", default: "selected", options: ["selected", "all"], minimum: null, maximum: null, step: null },
  { key: "durationYears", type: "integer", default: 1, options: [1, 3], minimum: 1, maximum: 3, step: 1 },
  { key: "timeframe", type: "select", default: "1d", options: ["5m", "15m", "30m", "1h", "2h", "4h", "1d"], minimum: null, maximum: null, step: null },
];

const envelopeKeys = new Set(["schemaVersion", "strategyKey", "settings"]);

function definitionsFor(strategyKey, definitions) {
  return [
    ...RUN_SETTING_DEFINITIONS,
    ...definitions.filter((definition) => definition.strategy === strategyKey),
  ];
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function syntaxError(error, source) {
  const message = error instanceof Error ? error.message : "Invalid JSON syntax";
  const position = /position\s+(\d+)/i.exec(message)?.[1];
  if (position === undefined) return `Line 1: ${message}`;
  const line = source.slice(0, Number(position)).split("\n").length;
  return `Line ${line}: ${message}`;
}

function settingError(source, key, message) {
  const keyPosition = source.indexOf(`"${key}"`);
  if (keyPosition < 0) return message;
  const line = source.slice(0, keyPosition).split("\n").length;
  return `Line ${line}: ${message}`;
}

function numericError(definition, value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return `${definition.key} must be a number`;
  }
  if (definition.type === "integer" && !Number.isInteger(value)) {
    return `${definition.key} must be a whole number`;
  }
  if (definition.minimum !== null && value < definition.minimum) {
    return `${definition.key} must be at least ${definition.minimum}`;
  }
  if (definition.maximum !== null && value > definition.maximum) {
    return `${definition.key} must be at most ${definition.maximum}`;
  }
  if (Array.isArray(definition.options) && !definition.options.includes(value)) {
    return `${definition.key} must be one of: ${definition.options.join(", ")}`;
  }
  if (definition.step !== null) {
    const quotient = value / definition.step;
    if (Math.abs(quotient - Math.round(quotient)) > 1e-8) {
      return `${definition.key} must use increments of ${definition.step}`;
    }
  }
  return null;
}

function valueError(definition, value) {
  if (definition.type === "number" || definition.type === "integer") {
    return numericError(definition, value);
  }
  if (definition.type === "boolean") {
    return typeof value === "boolean" ? null : `${definition.key} must be true or false`;
  }
  if (definition.type === "select") {
    if (typeof value !== "string" && typeof value !== "number") {
      return `${definition.key} has the wrong value type`;
    }
    return Array.isArray(definition.options) && !definition.options.includes(value)
      ? `${definition.key} must be one of: ${definition.options.join(", ")}`
      : null;
  }
  if (definition.type === "time") {
    if (typeof value !== "string" || !/^([01]\d|2[0-3]):[0-5]\d$/.test(value)) {
      return `${definition.key} must use 24-hour HH:MM format`;
    }
    return null;
  }
  if (definition.type === "string-array") {
    if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
      return `${definition.key} must be an array of symbol strings`;
    }
    const normalized = value.map((item) => item.trim().toUpperCase());
    if (normalized.some((item) => !item || !item.replaceAll("&", "").replaceAll("-", "").match(/^[A-Z0-9]+$/))) {
      return `${definition.key} contains an invalid NSE symbol`;
    }
    if (new Set(normalized).size !== normalized.length) return `${definition.key} contains duplicate symbols`;
    return null;
  }
  return `${definition.key} uses an unsupported parameter type`;
}

function relationshipErrors(strategyKey, settings) {
  const errors = [];
  if (settings.universeMode === "selected" && settings.symbols.length === 0) {
    errors.push("symbols must contain at least one symbol when universeMode is selected");
  }
  if (settings.universeMode === "selected" && settings.symbols.length > 10) {
    errors.push("symbols may contain at most 10 selected symbols");
  }

  if (strategyKey === "rsi_range") {
    if (!(settings.entryLow < settings.entryHigh && settings.entryHigh < settings.exitLow && settings.exitLow < settings.exitHigh)) {
      errors.push("RSI ranges must satisfy entryLow < entryHigh < exitLow < exitHigh");
    }
  }

  if (strategyKey === "rsi_recovery") {
    if (!(settings.rsiArmLow < settings.rsiArmHigh)) errors.push("rsiArmLow must be below rsiArmHigh");
    if (settings.minimumStopPct > settings.maximumStopPct) errors.push("minimumStopPct cannot exceed maximumStopPct");
    if (settings.upperRsiLevel < settings.profitExitRsi) errors.push("upperRsiLevel cannot be below profitExitRsi");
    const enabled = [settings.emaEnabled, settings.vwapEnabled, settings.volumeEnabled].filter(Boolean).length;
    if (settings.minimumConfirmations > enabled) errors.push(`minimumConfirmations cannot exceed ${enabled} enabled filters`);
    const protectedExit = settings.exitModel !== "LEGACY_FIXED_TARGET";
    if (settings.exitProtectionEnabled !== protectedExit) {
      errors.push("exitProtectionEnabled must match the selected exitModel");
    }
    for (const key of ["targetPct", "fixedStopLossPct", "stopAtrMultiplier", "rewardRiskRatio", "minimumStopPct", "maximumStopPct", "rupeeRiskBudget", "maximumCapitalPerPosition", "minimumProfitPct", "hardStopLossPct"]) {
      if (!(settings[key] > 0)) errors.push(`${key} must be greater than 0`);
    }
    if (settings.hardStopLossPct >= 100) errors.push("hardStopLossPct must be below 100");
  }

  if (strategyKey === "market_aligned_vwap_pullback_scalper") {
    if (settings.timeframe !== "5m") errors.push("Market-Aligned VWAP Pullback Scalper requires timeframe 5m");
    if (!(settings.rsiPullbackMinimum < settings.rsiPullbackMaximum
      && settings.rsiPullbackMaximum <= settings.rsiTriggerLevel
      && settings.rsiTriggerLevel < settings.maximumTriggerRsi)) {
      errors.push("RSI levels must satisfy rsiPullbackMinimum < rsiPullbackMaximum <= rsiTriggerLevel < maximumTriggerRsi");
    }
    if (!(settings.emaFast < settings.emaSlow)) errors.push("emaFast must be below emaSlow");
    if (!(settings.entryStartTime < settings.lastEntryTime && settings.lastEntryTime < settings.squareOffTime)) {
      errors.push("Session times must satisfy entryStartTime < lastEntryTime < squareOffTime");
    }
    if (!(settings.minimumStopPct <= settings.maximumStopPct)) errors.push("minimumStopPct cannot exceed maximumStopPct");
    if (settings.executionModel !== "NEXT_BAR_OPEN") errors.push("executionModel must be NEXT_BAR_OPEN");
  }

  if (strategyKey === "top_5_opening_range_breakout") {
    if (settings.timeframe !== "5m") errors.push("Top-5 Opening Range Breakout requires timeframe 5m");
    if (!(settings.emaFast < settings.emaSlow)) errors.push("emaFast must be below emaSlow");
    if (!(settings.minimumStopPct <= settings.maximumStopPct)) errors.push("minimumStopPct cannot exceed maximumStopPct");
    if (settings.watchlistPrimarySymbols > settings.watchlistSelectedSymbols) {
      errors.push("watchlistPrimarySymbols cannot exceed watchlistSelectedSymbols");
    }
    if (settings.watchlistMaximumReplacementsPerRescan > settings.watchlistSelectedSymbols) {
      errors.push("watchlistMaximumReplacementsPerRescan cannot exceed watchlistSelectedSymbols");
    }
    if (settings.watchlistRescanIntervalMinutes % 5 !== 0 || settings.watchlistRollingWindowMinutes % 5 !== 0) {
      errors.push("watchlistRescanIntervalMinutes and watchlistRollingWindowMinutes must be multiples of 5");
    }
    if (!(settings.openingRangeStartTime < settings.openingRangeEndTime
      && settings.openingRangeEndTime <= settings.watchlistSelectionTime
      && settings.watchlistSelectionTime <= settings.watchlistRescanEndTime
      && settings.watchlistRescanEndTime < settings.lastEntryTime
      && settings.lastEntryTime < settings.squareOffTime)) {
      errors.push("Session times must satisfy opening start < opening end <= selection <= final rescan < last entry < square-off");
    }
    if (settings.executionModel !== "NEXT_BAR_OPEN") errors.push("executionModel must be NEXT_BAR_OPEN");
    if (settings.quantityPerTrade !== 50) errors.push("quantityPerTrade must be exactly 50");
  }
  return errors;
}

export function createJsonConfiguration(strategyKey, settings, definitions) {
  const resolved = {};
  for (const definition of definitionsFor(strategyKey, definitions)) {
    resolved[definition.key] = settings[definition.key] === undefined
      ? definition.default
      : settings[definition.key];
  }
  return {
    schemaVersion: JSON_CONFIGURATION_SCHEMA_VERSION,
    strategyKey,
    settings: resolved,
  };
}

export function formatJsonConfiguration(configuration) {
  return JSON.stringify(configuration, null, 2);
}

export function parseAndValidateJsonConfiguration(source, options) {
  let parsed;
  try {
    parsed = JSON.parse(source);
  } catch (error) {
    return { valid: false, errors: [syntaxError(error, source)], configuration: null, summary: null };
  }

  if (!isObject(parsed)) {
    return { valid: false, errors: ["Configuration must be a JSON object"], configuration: null, summary: null };
  }
  const unknownEnvelope = Object.keys(parsed).filter((key) => !envelopeKeys.has(key));
  if (unknownEnvelope.length) {
    return { valid: false, errors: unknownEnvelope.map((key) => `Unknown top-level field: ${key}`), configuration: null, summary: null };
  }
  if (parsed.schemaVersion !== JSON_CONFIGURATION_SCHEMA_VERSION) {
    return { valid: false, errors: [`Unsupported schemaVersion: ${String(parsed.schemaVersion)}`], configuration: null, summary: null };
  }
  if (typeof parsed.strategyKey !== "string") {
    return { valid: false, errors: ["strategyKey must be a string"], configuration: null, summary: null };
  }
  if (parsed.strategyKey !== options.strategyKey) {
    const strategyName = options.strategyNames?.[parsed.strategyKey] ?? parsed.strategyKey;
    return {
      valid: false,
      errors: [`This JSON belongs to ${strategyName}.`],
      configuration: null,
      summary: null,
      belongsToStrategyKey: parsed.strategyKey,
      belongsToStrategyName: strategyName,
    };
  }
  if (!isObject(parsed.settings)) {
    return { valid: false, errors: ["settings must be a JSON object"], configuration: null, summary: null };
  }

  const definitions = definitionsFor(options.strategyKey, options.definitions);
  const knownKeys = new Set(definitions.map((definition) => definition.key));
  const unknownSettings = Object.keys(parsed.settings).filter((key) => !knownKeys.has(key));
  const missingSettings = definitions.filter((definition) => !(definition.key in parsed.settings));
  const errors = [
    ...unknownSettings.map((key) => `Unknown setting: ${key}`),
    ...missingSettings.map((definition) => `Missing setting: ${definition.key}`),
  ];
  for (const definition of definitions) {
    if (!(definition.key in parsed.settings)) continue;
    const error = valueError(definition, parsed.settings[definition.key]);
    if (error) errors.push(settingError(source, definition.key, error));
  }
  if (!errors.length) errors.push(...relationshipErrors(options.strategyKey, parsed.settings));
  if (errors.length) return { valid: false, errors, configuration: null, summary: null };

  const currentSettings = options.currentSettings ?? {};
  const changed = definitions.filter((definition) => !sameValue(currentSettings[definition.key], parsed.settings[definition.key])).length;
  const unchanged = definitions.length - changed;
  return {
    valid: true,
    errors: [],
    configuration: parsed,
    summary: { changed, unchanged, errors: 0 },
  };
}
