export const TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY = "top_5_opening_range_breakout";
export const TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_NAME = "Top-5 Opening Range Breakout";

const WATCHLIST_MODES = new Set(["FROZEN_OPEN", "ROLLING"]);

export function createTop5OpeningRangeBreakoutRequest(base, configuration) {
  if (!configuration || !WATCHLIST_MODES.has(configuration.watchlistMode)) {
    throw new Error("watchlistMode must be FROZEN_OPEN or ROLLING");
  }
  return {
    ...base,
    strategyMode: TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY,
    strategyKey: TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY,
    top5OpeningRangeBreakoutConfiguration: { ...configuration },
  };
}

function metricRow(label, row = {}) {
  return `| ${label} | ${row.trades ?? 0} | ${row.winRate ?? 0} | ${row.grossPnl ?? 0} | ${row.costs ?? 0} | ${row.netPnlAfterCosts ?? 0} | ${row.expectancy ?? "—"} | ${row.profitFactor ?? "—"} | ${row.maximumDrawdown ?? 0} |`;
}

function comparisonSection(title, value) {
  return [
    `## ${title}`,
    "",
    "| Variant | Trades | Win rate | Gross P&L | Costs | Net P&L | Expectancy | Profit factor | Maximum drawdown |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    metricRow(title, value?.overall),
    "",
  ];
}

export function buildTop5OpeningRangeBreakoutMarkdown(response) {
  if (response?.metadata?.strategyKey !== TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY) {
    throw new Error("Cannot export a non-Top-5 result with the Top-5 exporter");
  }
  const summary = response.summary ?? {};
  const mode = response.metadata.watchlistMode ?? response.watchlist?.mode ?? "UNKNOWN";
  const comparisons = response.comparison ?? {};
  const folds = Object.fromEntries(
    Object.entries(comparisons).map(([key, value]) => [key, value?.chronologicalFolds ?? []]),
  );
  const primarySelections = summary.primarySelections
    ?? response.dailySelections.flatMap((row) => row.symbols).filter((row) => row.tier === "PRIMARY").length;
  const reserveSelections = summary.reserveSelections
    ?? response.dailySelections.flatMap((row) => row.symbols).filter((row) => row.tier === "RESERVE").length;
  const lines = [
    `# ${TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_NAME}`,
    "",
    `- Strategy: ${TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_NAME}`,
    `- Strategy key: ${response.metadata.strategyKey}`,
    `- Watchlist mode: ${mode}`,
    `- Universe evaluated: ${response.metadata.universeEvaluated ?? summary.universeEvaluated ?? 0}`,
    `- Trading days: ${response.metadata.tradingDays ?? summary.tradingDays ?? response.dailySelections.length}`,
    `- Daily watchlists: ${summary.dailyWatchlists ?? response.dailySelections.length}`,
    `- PRIMARY selections: ${primarySelections}`,
    `- RESERVE selections: ${reserveSelections}`,
    `- Watchlist replacements: ${summary.watchlistReplacements ?? summary.replacements ?? response.middayReplacements.length}`,
    `- Opening breakout candidates: ${summary.openingBreakoutCandidates ?? summary.rawOpeningCandidates ?? 0}`,
    `- Accepted BUY signals: ${summary.acceptedBuySignals ?? 0}`,
    `- Executed trades: ${summary.executedTrades ?? 0}`,
    `- Configuration hash: ${response.metadata.configurationHash}`,
    `- Result source: ${response.metadata.resultSource}`,
    `- Live orders enabled: ${response.metadata.liveOrdersEnabled}`,
    "",
    "## Daily watchlists",
    "",
    ...response.dailySelections.flatMap((selection) => [
      `### ${selection.sessionDate} — ${selection.selectionTimestamp}`,
      ...selection.symbols.map((item) => `- #${item.rank ?? item.rankAfter ?? "—"} ${item.symbol} — ${item.tier ?? "—"} — score ${item.score ?? "—"}`),
      "",
    ]),
    "## Watchlist replacements",
    "",
    "```json",
    JSON.stringify(response.middayReplacements, null, 2),
    "```",
    "",
    "## Opening breakout candidates and BUY signals",
    "",
    "```json",
    JSON.stringify({
      openingBreakoutCandidates: summary.openingBreakoutCandidates ?? summary.rawOpeningCandidates ?? 0,
      acceptedBuySignals: summary.acceptedBuySignals ?? 0,
      openingSignals: response.openingSignals,
      middaySignals: response.middaySignals,
      executedTrades: response.trades,
    }, null, 2),
    "```",
    "",
    ...comparisonSection("Top-5 results — FROZEN_OPEN", comparisons.FROZEN_OPEN_TOP_FIVE),
    ...comparisonSection("Top-5 results — ROLLING", comparisons.ROLLING_TOP_FIVE),
    ...comparisonSection("Top-2 results", comparisons.FROZEN_OPEN_TOP_TWO),
    ...comparisonSection("Full-universe baseline", comparisons.FULL_ELIGIBLE_UNIVERSE),
    ...comparisonSection("Liquidity-only baseline", comparisons.LIQUIDITY_ONLY_TOP_FIVE),
    ...comparisonSection("Random-five baseline", comparisons.CAUSALLY_MATCHED_RANDOM_FIVE),
    "## Development results",
    "",
    "```json",
    JSON.stringify(Object.fromEntries(Object.entries(folds).map(([key, values]) => [key, values.map((fold) => fold.development)])), null, 2),
    "```",
    "",
    "## Untouched validation results",
    "",
    "```json",
    JSON.stringify(Object.fromEntries(Object.entries(folds).map(([key, values]) => [key, values.map((fold) => fold.validation)])), null, 2),
    "```",
    "",
    "## Effective settings",
    "",
    "```json",
    JSON.stringify(response.metadata.effectiveConfiguration, null, 2),
    "```",
    "",
  ];
  return lines.join("\n");
}
