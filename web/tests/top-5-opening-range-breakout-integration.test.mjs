import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY,
  TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_NAME,
  buildTop5OpeningRangeBreakoutMarkdown,
  createTop5OpeningRangeBreakoutRequest,
} from "../app/backtest/top-5-opening-range-breakout-contract.mjs";

const root = new URL("../", import.meta.url);

function backendResponse(request) {
  assert.equal(request.strategyKey, TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY);
  assert.equal(request.strategyMode, TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY);
  return {
    metadata: {
      runId: "top5-integration",
      strategyMode: request.strategyMode,
      strategyKey: request.strategyKey,
      strategyName: TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_NAME,
      strategyVersion: "top-5-opening-range-breakout-1.0.0",
      researchLabel: "Research candidate — paper trading required",
      resultSource: "FRESH_CALCULATION",
      configurationHash: "config-hash",
      calculationHash: "calculation-hash",
      dataSnapshot: "data-snapshot",
      effectiveConfiguration: request.top5OpeningRangeBreakoutConfiguration,
      watchlistMode: request.top5OpeningRangeBreakoutConfiguration.watchlistMode,
      universeEvaluated: 5,
      tradingDays: 1,
      liveOrdersEnabled: false,
    },
    watchlist: { mode: request.top5OpeningRangeBreakoutConfiguration.watchlistMode },
    summary: {
      universeEvaluated: 5,
      tradingDays: 1,
      dailyWatchlists: 1,
      primarySelections: 2,
      reserveSelections: 3,
      watchlistReplacements: 0,
      openingBreakoutCandidates: 1,
      acceptedBuySignals: 1,
      executedTrades: 1,
    },
    dailySelections: [{
      sessionDate: "2026-08-28",
      selectionTimestamp: "2026-08-28T09:30:00+05:30",
      symbols: [
        { rank: 1, symbol: "AAA", tier: "PRIMARY", score: 91 },
        { rank: 2, symbol: "BBB", tier: "PRIMARY", score: 88 },
        { rank: 3, symbol: "CCC", tier: "RESERVE", score: 84 },
        { rank: 4, symbol: "DDD", tier: "RESERVE", score: 81 },
        { rank: 5, symbol: "EEE", tier: "RESERVE", score: 79 },
      ],
    }],
    middayReplacements: [],
    openingSignals: [{ symbol: "AAA", signalTimestamp: "2026-08-28T09:50:00+05:30" }],
    middaySignals: [],
    trades: [{ symbol: "AAA", executedQuantity: 50 }],
    comparison: {},
  };
}

test("frontend selection submits the Top-5 key and exports only the Top-5 contract", async () => {
  const dashboard = await readFile(new URL("app/backtest/backtest-dashboard.tsx", root), "utf8");
  assert.match(dashboard, /createTop5OpeningRangeBreakoutRequest\(commonRequest, top5OpeningRangeBreakoutSettings\)/);
  assert.doesNotMatch(dashboard, />Market-Aligned VWAP Pullback Scalper<\/button>/);

  let capturedRequest;
  const request = createTop5OpeningRangeBreakoutRequest(
    { symbols: ["AAA", "BBB", "CCC", "DDD", "EEE"], timeframe: "5m" },
    { watchlistMode: "FROZEN_OPEN", quantityPerTrade: 50, maximumTradesPerDay: 5 },
  );
  const submitToApi = async (body) => {
    capturedRequest = structuredClone(body);
    return backendResponse(body);
  };
  const response = await submitToApi(request);

  assert.equal(capturedRequest.strategyKey, "top_5_opening_range_breakout");
  assert.equal(capturedRequest.top5OpeningRangeBreakoutConfiguration.watchlistMode, "FROZEN_OPEN");
  assert.equal(response.metadata.strategyKey, capturedRequest.strategyKey);
  assert.equal(response.metadata.strategyName, "Top-5 Opening Range Breakout");
  assert.deepEqual(response.metadata.effectiveConfiguration, capturedRequest.top5OpeningRangeBreakoutConfiguration);

  const markdown = buildTop5OpeningRangeBreakoutMarkdown(response);
  assert.match(markdown, /^# Top-5 Opening Range Breakout/m);
  assert.match(markdown, /Watchlist mode: FROZEN_OPEN/);
  assert.match(markdown, /09:30:00\+05:30/);
  for (const symbol of ["AAA", "BBB", "CCC", "DDD", "EEE"]) assert.match(markdown, new RegExp(symbol));
  assert.match(markdown, /## Effective settings/);
  assert.match(markdown, /"quantityPerTrade": 50/);
  assert.doesNotMatch(markdown, /VWAP pullback performance/i);
});

test("request builder rejects invalid watchlist modes before submission", () => {
  assert.throws(
    () => createTop5OpeningRangeBreakoutRequest({}, { watchlistMode: "VWAP_PULLBACK" }),
    /watchlistMode must be FROZEN_OPEN or ROLLING/,
  );
});
