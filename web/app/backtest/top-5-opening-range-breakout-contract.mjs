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
  return `| ${label} | ${row.trades ?? 0} | ${row.tradesPerCalendarSession ?? row.tradesPerDay ?? 0} | ${row.noTradeDays ?? 0} | ${row.winRate ?? 0} | ${row.targetExits ?? 0} | ${row.stopExits ?? 0} | ${row.timeExits ?? 0} | ${row.sessionExits ?? 0} | ${row.grossWinningProfit ?? 0} | ${row.grossLosingLoss ?? 0} | ${row.grossPnl ?? 0} | ${row.costs ?? 0} | ${row.netPnlAfterCosts ?? 0} | ${row.expectancy ?? "—"} | ${row.profitFactor ?? "—"} | ${row.maximumDrawdown ?? 0} |`;
}

const comparisonNames = {
  FROZEN_OPEN_TOP_FIVE: "Frozen Top-5",
  FROZEN_OPEN_TOP_TWO: "Frozen Top-2",
  ROLLING_TOP_FIVE: "Rolling Top-5",
  FULL_ELIGIBLE_UNIVERSE: "Full eligible universe",
  LIQUIDITY_ONLY_TOP_FIVE: "Liquidity-only Top-5",
  CAUSALLY_MATCHED_RANDOM_FIVE: "Causally matched random five",
};

const metricHeader = [
  "| Variant | Trades | Trades/tested session | No-trade sessions | Win rate | Target | Stop | Time | Session | Gross wins | Gross losses | Gross P&L | Costs | Net P&L | Net expectancy | Profit factor | Max drawdown |",
  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
];

export function buildTop5OpeningRangeBreakoutMarkdown(response) {
  if (response?.metadata?.strategyKey !== TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY) {
    throw new Error("Cannot export a non-Top-5 result with the Top-5 exporter");
  }
  const summary = response.summary ?? {};
  const mode = response.metadata.watchlistMode ?? response.watchlist?.mode ?? "UNKNOWN";
  const comparisons = response.comparison ?? {};
  const eligibility = response.metadata.universeEligibility ?? {};
  const dailySelections = response.dailySelections ?? [];
  const populatedSelectionSample = dailySelections
    .filter((selection) => (selection.symbols ?? []).length > 0)
    .slice(0, 10);
  const primarySelections = summary.primarySelections
    ?? dailySelections.flatMap((row) => row.symbols).filter((row) => row.tier === "PRIMARY").length;
  const reserveSelections = summary.reserveSelections
    ?? dailySelections.flatMap((row) => row.symbols).filter((row) => row.tier === "RESERVE").length;
  const rejectionRows = Object.entries({
    ...(eligibility.rejectionReasonSymbolCounts ?? {}),
    ...(summary.rejectionCounts ?? {}),
  }).sort((left, right) => Number(right[1]) - Number(left[1])).slice(0, 15);
  const lines = [
    `# ${TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_NAME}`,
    "",
    `- Strategy: ${TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_NAME}`,
    `- Strategy key: ${response.metadata.strategyKey}`,
    `- Watchlist mode: ${mode}`,
    `- Symbols requested: ${eligibility.symbolsRequested ?? response.metadata.symbolsRequested ?? 0}`,
    `- Symbols eligible at least once: ${eligibility.symbolsEligibleAtLeastOnce ?? 0}`,
    `- Symbols rejected for the entire period: ${eligibility.symbolsRejectedForEntirePeriod ?? 0}`,
    `- Symbols actually scored: ${eligibility.symbolsActuallyScored ?? response.metadata.universeEvaluated ?? 0}`,
    `- Trading days: ${response.metadata.tradingDays ?? summary.tradingDays ?? dailySelections.length}`,
    `- Daily watchlists: ${summary.dailyWatchlists ?? dailySelections.length}`,
    `- PRIMARY selections: ${primarySelections}`,
    `- RESERVE selections: ${reserveSelections}`,
    `- Frozen replacements: ${summary.frozenReplacements ?? 0}`,
    `- Rolling rescans: ${summary.rollingRescans ?? 0}`,
    `- Rolling promotions: ${summary.rollingPromotions ?? 0}`,
    `- Rolling removals: ${summary.rollingRemovals ?? 0}`,
    `- Opening breakout candidates: ${summary.openingBreakoutCandidates ?? summary.rawOpeningCandidates ?? 0}`,
    `- Accepted BUY signals: ${summary.acceptedBuySignals ?? 0}`,
    `- Executed trades: ${summary.executedTrades ?? 0}`,
    `- Configuration hash: ${response.metadata.configurationHash}`,
    `- Result source: ${response.metadata.resultSource}`,
    `- Live orders enabled: ${response.metadata.liveOrdersEnabled}`,
    "",
    "## Main metrics",
    "",
    ...metricHeader,
    metricRow(mode === "ROLLING" ? "Active Rolling Top-5" : "Active Frozen Top-5", summary),
    "",
    "## Daily-selection summary",
    "",
    `The full ${dailySelections.length}-session selection history is available as CSV/JSON. The first ten populated sessions are sampled below.`,
    "",
    ...populatedSelectionSample.flatMap((selection) => [
      `### ${selection.sessionDate} — ${selection.selectionTimestamp}`,
      ...selection.symbols.map((item) => `- #${item.rank ?? item.rankAfter ?? "—"} ${item.symbol} — ${item.tier ?? "—"} — score ${item.score ?? "—"}`),
      "",
    ]),
    "## Comparison results",
    "",
    ...metricHeader,
    ...Object.entries(comparisons).map(([key, value]) => metricRow(comparisonNames[key] ?? key, value?.overall)),
    "",
    "## Development results",
    "",
    ...metricHeader,
    ...Object.entries(comparisons).flatMap(([key, value]) => (value?.chronologicalFolds ?? []).map(
      (fold, index) => metricRow(`${comparisonNames[key] ?? key} fold ${index + 1}`, fold.development),
    )),
    "",
    "## Untouched validation results",
    "",
    ...metricHeader,
    ...Object.entries(comparisons).flatMap(([key, value]) => (value?.chronologicalFolds ?? []).map(
      (fold, index) => metricRow(`${comparisonNames[key] ?? key} fold ${index + 1}`, fold.validation),
    )),
    "",
    "## Top rejection reasons",
    "",
    ...(rejectionRows.length ? rejectionRows.map(([reason, count]) => `- ${reason}: ${count}`) : ["- None recorded"]),
    "",
    "## Sample trades",
    "",
    "Maximum 20 rows; download the complete trade dataset separately.",
    "",
    "| Symbol | Signal candle end | Decision | Entry candle start | Entry price | Exit reason | Gross P&L | Costs | Net P&L | Quantity |",
    "| --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: |",
    ...(response.trades ?? []).slice(0, 20).map((trade) => `| ${trade.symbol} | ${trade.signalCandleEnd ?? trade.signalTimestamp ?? "—"} | ${trade.decisionTimestamp ?? "—"} | ${trade.entryCandleStart ?? trade.entryTimestamp ?? "—"} | ${trade.entryPrice ?? "—"} | ${trade.exitReason ?? "—"} | ${trade.grossPnl ?? 0} | ${trade.totalCosts ?? 0} | ${trade.netPnl ?? 0} | ${trade.executedQuantity ?? 0} |`),
    "",
    "## Effective settings",
    "",
    "```json",
    JSON.stringify(response.metadata.effectiveConfiguration, null, 2),
    "```",
    "",
    "## Important warnings",
    "",
    `- Validation status: ${response.validationDecision?.status ?? "REJECTED_RESEARCH_ONLY"}`,
    `- ${response.validationDecision?.reason ?? "Untouched validation has not approved this selector."}`,
    ...(response.warnings ?? []).map((warning) => `- ${warning}`),
    "- Raw watchlists, candidates, signals, trades and benchmark records are intentionally excluded from Markdown; use their CSV/JSON downloads.",
    "",
  ];
  return lines.join("\n");
}
