"use client";

import { Download, Info, SortAsc, SortDesc } from "lucide-react";
import { useMemo, useState } from "react";
import { FeatureAnalysis } from "./feature-analysis";

export type RecoveryTrade = {
  tradeId: string;
  sequenceNumber: number;
  runId: string;
  strategyMode: "rsi_recovery";
  symbol: string;
  timeframe: string;
  signalTimestamp: string;
  entryTimestamp: string;
  entryBarIndex: number;
  executionModel: "SIGNAL_CLOSE" | "NEXT_BAR_OPEN";
  entryPrice: number;
  targetPct: number;
  targetPrice: number;
  status: "TARGET_HIT" | "OPEN";
  targetHitTimestamp: string | null;
  exitPrice: number | null;
  barsHeld: number;
  tradingSessionsHeld: number;
  sessionDistance: number;
  durationMinutes: number;
  durationHours: number;
  durationDays: number;
  targetSpeedBucket: string | null;
  sessionSpeedBucket: string | null;
  rsiArmTimestamp: string;
  rsiArmValue: number;
  rsiAtEntry: number;
  confirmationScore: number;
  requiredConfirmations: number;
  emaEnabled: boolean;
  vwapEnabled: boolean;
  volumeEnabled: boolean;
  emaConfirmation: boolean;
  vwapConfirmation: boolean;
  volumeConfirmation: boolean;
  emaFastAtEntry: number | null;
  emaSlowAtEntry: number | null;
  vwapAtEntry: number | null;
  volumeAtEntry: number | null;
  volumeEmaAtEntry: number | null;
  lowestPriceAfterEntry: number;
  maxAdversePct: number;
  highestPriceAfterEntry: number;
  maxFavorablePct: number;
  lastTimestamp: string;
  lastClose: number;
  currentPnlPct: number | null;
  grossReturnPct: number;
  estimatedCostPct: number;
  netReturnPct: number;
};

export type ProtectedPosition = {
  tradeId: string;
  sequenceNumber: number;
  signalTimestamp: string;
  entryTimestamp: string;
  entryBarIndex: number;
  entryPrice: number;
  quantity: number;
  capitalDeployed: number;
  exitModel?: "FIXED_TP_SL" | "ATR_DYNAMIC_TP_SL";
  atrLength?: number;
  atrTimeframe?: string;
  atrAtSignal?: number;
  atrPctAtEntry?: number;
  stopAtrMultiplier?: number;
  rewardRiskRatio?: number;
  minimumStopPct?: number;
  maximumStopPct?: number;
  dynamicStopPct?: number;
  dynamicTargetPct?: number;
  stopLossPrice?: number;
  rupeeRiskAtEntry?: number;
  targetPrice: number;
  exitTimestamp: string | null;
  exitPrice: number | null;
  exitReason: "TARGET_EXIT" | "TARGET_GAP" | "STOP_EXIT" | "STOP_GAP" | "TIME_EXIT" | null;
  exitFill: "GAP_OPEN" | "TARGET_PRICE" | "NEXT_TRADING_SESSION_OPEN" | null;
  status: "TARGET_EXIT" | "TARGET_GAP" | "STOP_EXIT" | "STOP_GAP" | "TIME_EXIT" | "OPEN";
  holdingSessions: number;
  tradingSessionsHeld: number;
  barsHeld: number;
  durationMinutes: number;
  durationHours: number;
  durationDays: number;
  lowestPriceAfterEntry: number;
  maxAdversePct: number;
  highestPriceAfterEntry: number;
  maxFavorablePct: number;
  grossPnl: number;
  buyCost: number;
  sellCost: number;
  slippageCost: number;
  estimatedOpenExitCost: number;
  totalCosts: number;
  tradingCosts?: number;
  netPnl?: number;
  realizedPnl: number | null;
  unrealizedPnl: number;
  lastTimestamp: string;
  lastClose: number;
  confirmationScore: number;
  requiredConfirmations: number;
  rsiAtEntry: number;
  executionModel: "SIGNAL_CLOSE" | "NEXT_BAR_OPEN";
};

export type SkippedRecoverySignal = {
  tradeId: string;
  sequenceNumber: number;
  signalTimestamp: string;
  entryTimestamp: string | null;
  entryPrice: number | null;
  status: "SKIPPED_MAX_OPEN_LOTS";
  reason: string;
};

export type ProtectedRecoverySummary = {
  totalValidBuySignals: number;
  totalBuySignals: number;
  buySignals: number;
  executedTrades: number;
  skippedMaxOpenLots: number;
  targetExits: number;
  targetGapExits?: number;
  stopExits?: number;
  stopGapExits?: number;
  targetsHit: number;
  timeExits: number;
  openPositions: number;
  openSignals: number;
  targetHitRate: number;
  winningTrades?: number;
  losingTrades?: number;
  winRate?: number;
  averageDynamicTargetPct?: number | null;
  averageDynamicStopPct?: number | null;
  averageRewardRisk?: number | null;
  grossProfit?: number;
  grossLoss?: number;
  tradingCosts?: number;
  estimatedOpenExitCosts?: number;
  expectancyPerTrade?: number | null;
  averageWinner?: number | null;
  averageLoser?: number | null;
  profitableClosedTrades: number;
  losingClosedTrades: number;
  realizedGrossProfit: number;
  realizedGrossLoss: number;
  netRealizedPnl: number;
  unrealizedPnl: number;
  combinedPnl: number;
  averageProfitPerTrade: number | null;
  averageLossPerTrade: number | null;
  profitFactor: number | null;
  maximumDrawdown: number;
  maximumDrawdownPct: number;
  maximumConcurrentPositions: number;
  maxConcurrentPositions: number;
  peakCapitalDeployed: number;
  averageHoldingMinutes: number | null;
  medianHoldingMinutes: number | null;
  averageHoldingSessions: number | null;
  medianHoldingSessions: number | null;
  candleRowsProcessed: number;
};

type SpeedBucket = { count: number; pct: number };

export type RecoverySymbolResult = {
  symbol: string;
  firstCandle: string;
  lastCandle: string;
  bars: number;
  totalBuySignals: number;
  buySignals: number;
  targetsHit: number;
  openSignals: number;
  targetHitRate: number;
  maximumConcurrentOpenSignals: number;
  averageConcurrentOpenSignals: number;
  maximumSignalsOpenSameDay: number;
  le30mPct: number;
  le2hPct: number;
  le24hPct: number;
  averageTargetMinutes: number | null;
  medianTargetMinutes: number | null;
  averageBarsToTarget: number | null;
  medianBarsToTarget: number | null;
  averageMaePct: number | null;
  medianMaePct: number | null;
  worstMaePct: number | null;
  averageMfePct: number | null;
  medianMfePct: number | null;
  openPositions: number;
  openPct: number;
  averageOpenAgeMinutes: number | null;
  medianOpenAgeMinutes: number | null;
  oldestOpenMinutes: number | null;
  averageOpenPnlPct: number | null;
  worstOpenPnlPct: number | null;
  averageOpenMaePct: number | null;
  worstOpenMaePct: number | null;
  speedBuckets: Record<string, SpeedBucket>;
  hitRateScore: number;
  speedScore: number;
  maeScore: number;
  openPenalty: number;
  qualityScore: number;
  trades: RecoveryTrade[];
  events: Array<Record<string, unknown>>;
  chart: Array<Record<string, unknown>>;
  positions?: ProtectedPosition[];
  skippedSignals?: SkippedRecoverySignal[];
  totalValidBuySignals?: number;
  executedTrades?: number;
  skippedMaxOpenLots?: number;
  targetExits?: number;
  timeExits?: number;
  maximumConcurrentPositions?: number;
  peakCapitalDeployed?: number;
  maximumDrawdown?: number;
};

export type RecoverySummary = {
  totalBuySignals: number;
  buySignals: number;
  totalTargetsHit: number;
  targetsHit: number;
  targetHitRate: number;
  totalOpenSignals: number;
  stillOpen: number;
  maximumConcurrentSignalsUniverse: number;
  maximumConcurrentSignalsSameSymbol: number;
  symbolsWithOpenSignals: number;
  averageOpenSignalsPerSymbol: number;
  symbolsWith2PlusOpenSignals: number;
  symbolsWith5PlusOpenSignals: number;
  maxConcurrentPositions: number;
  targetSpeedBuckets: Record<string, SpeedBucket>;
  sessionSpeedBuckets: Record<string, SpeedBucket>;
  averageTargetMinutes: number | null;
  medianTargetMinutes: number | null;
  averageBarsToTarget: number | null;
  medianBarsToTarget: number | null;
  averageCompletedMaePct: number | null;
  medianCompletedMaePct: number | null;
  worstCompletedMaePct: number | null;
  averageCompletedMfePct: number | null;
  medianCompletedMfePct: number | null;
  averageOpenAgeMinutes: number | null;
  medianOpenAgeMinutes: number | null;
  oldestOpenMinutes: number | null;
  oldestOpenSymbol: string | null;
  averageOpenPnlPct: number | null;
  worstOpenPnlPct: number | null;
  averageOpenMaePct: number | null;
  worstOpenMaePct: number | null;
  candleRowsProcessed: number;
};

export type RecoveryBacktestResponse = {
  metadata: {
    runId: string;
    strategyMode: "rsi_recovery";
    strategyVersion: string;
    startedAt: string;
    completedAt: string;
    generatedAt: string;
    analysisStart: string;
    dataFrom: string | null;
    dataTo: string | null;
    durationYears: number;
    timeframe: string;
    universeMode: "selected" | "all";
    symbolsRequested: number;
    symbolsProcessed: number;
    symbolsFailed: number;
    workerCount: number;
    runtimeSeconds: number;
    timezone: string;
    executionModel: "SIGNAL_CLOSE" | "NEXT_BAR_OPEN";
    exitModel?: "LEGACY_FIXED_TARGET" | "LEGACY_PROTECTED_TARGET" | "FIXED_TP_SL" | "ATR_DYNAMIC_TP_SL";
    backtestSemantics?: "SIGNAL_OBSERVATION" | "POSITION";
    exitProtection?: {
      enabled: boolean;
      exitModel?: "LEGACY_FIXED_TARGET" | "LEGACY_PROTECTED_TARGET" | "FIXED_TP_SL" | "ATR_DYNAMIC_TP_SL";
      quantityPerTrade: number;
      maxOpenLotsPerSymbol: number;
      maxHoldingTradingDays: number;
      timeExit: "NEXT_TRADING_SESSION_OPEN";
    };
    positionBacktestVersion?: string | null;
    strategyParameters: Record<string, string | number | boolean>;
    costModel: {
      buyCostBps: number;
      sellCostBps: number;
      slippageBpsPerSide: number;
      estimatedRoundTripCostPct: number;
    };
    qualityFormula: {
      weights: Record<string, number>;
      formula: string;
      speedScore: string;
      maeScore: string;
      openPenalty: string;
    };
    corporateActionAdjustment: string;
    gitCommitSha: string | null;
  };
  summary: RecoverySummary | ProtectedRecoverySummary;
  results: RecoverySymbolResult[];
  errors: Array<{ symbol: string; message: string }>;
  warnings: string[];
};

const speedOrder = [
  ["LE_30_MIN", "≤ 30 min"],
  ["GT_30_MIN_LE_2_HOURS", "30 min – 2 hrs"],
  ["GT_2_HOURS_LE_24_HOURS", "2 hrs – 24 hrs"],
  ["GT_24_HOURS", "> 24 hrs"],
] as const;

const sessionOrder = [
  ["SAME_SESSION", "Same session"],
  ["NEXT_SESSION", "Next session"],
  ["TWO_TO_FIVE_TRADING_DAYS", "2–5 trading days"],
  ["GT_FIVE_TRADING_DAYS", "> 5 trading days"],
] as const;

function finite(values: Array<number | null | undefined>) {
  return values.filter((value): value is number => value !== null && value !== undefined && Number.isFinite(value));
}

function mean(values: number[]) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function median(values: number[]) {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function rounded(value: number | null, digits = 4) {
  return value === null || !Number.isFinite(value) ? null : Number(value.toFixed(digits));
}

export function aggregateRecoveryResults(results: RecoverySymbolResult[]): RecoverySummary {
  const trades = results.flatMap((result) => result.trades);
  const completed = trades.filter((trade) => trade.status === "TARGET_HIT");
  const open = trades.filter((trade) => trade.status === "OPEN");
  const bucket = (key: "targetSpeedBucket" | "sessionSpeedBucket", names: readonly (readonly [string, string])[]) =>
    Object.fromEntries(names.map(([name]) => {
      const count = completed.filter((trade) => trade[key] === name).length;
      return [name, { count, pct: completed.length ? rounded(count / completed.length * 100, 2) ?? 0 : 0 }];
    }));
  const durations = completed.map((trade) => trade.durationMinutes);
  const bars = completed.map((trade) => trade.barsHeld);
  const completedMae = completed.map((trade) => trade.maxAdversePct);
  const completedMfe = completed.map((trade) => trade.maxFavorablePct);
  const openAges = open.map((trade) => trade.durationMinutes);
  const openPnl = finite(open.map((trade) => trade.currentPnlPct));
  const openMae = open.map((trade) => trade.maxAdversePct);
  const oldest = [...open].sort((left, right) => right.durationMinutes - left.durationMinutes)[0];

  const timeline = trades.flatMap((trade) => [
    { time: new Date(trade.entryTimestamp).getTime(), delta: 1 },
    { time: new Date(trade.targetHitTimestamp ?? trade.lastTimestamp).getTime(), delta: -1 },
  ]).sort((left, right) => left.time - right.time || right.delta - left.delta);
  let concurrent = 0;
  let maxConcurrent = 0;
  timeline.forEach((item) => {
    concurrent += item.delta;
    maxConcurrent = Math.max(maxConcurrent, concurrent);
  });
  const openCounts = results.map((result) => result.openSignals);
  const maxConcurrentSameSymbol = results.reduce(
    (maximum, result) => Math.max(maximum, result.maximumConcurrentOpenSignals),
    0,
  );

  return {
    totalBuySignals: trades.length,
    buySignals: trades.length,
    totalTargetsHit: completed.length,
    targetsHit: completed.length,
    targetHitRate: trades.length ? rounded(completed.length / trades.length * 100, 2) ?? 0 : 0,
    totalOpenSignals: open.length,
    stillOpen: open.length,
    maximumConcurrentSignalsUniverse: maxConcurrent,
    maximumConcurrentSignalsSameSymbol: maxConcurrentSameSymbol,
    symbolsWithOpenSignals: openCounts.filter((count) => count > 0).length,
    averageOpenSignalsPerSymbol: results.length ? rounded(open.length / results.length, 4) ?? 0 : 0,
    symbolsWith2PlusOpenSignals: openCounts.filter((count) => count >= 2).length,
    symbolsWith5PlusOpenSignals: openCounts.filter((count) => count >= 5).length,
    maxConcurrentPositions: maxConcurrent,
    targetSpeedBuckets: bucket("targetSpeedBucket", speedOrder),
    sessionSpeedBuckets: bucket("sessionSpeedBucket", sessionOrder),
    averageTargetMinutes: rounded(mean(durations)),
    medianTargetMinutes: rounded(median(durations)),
    averageBarsToTarget: rounded(mean(bars)),
    medianBarsToTarget: rounded(median(bars)),
    averageCompletedMaePct: rounded(mean(completedMae)),
    medianCompletedMaePct: rounded(median(completedMae)),
    worstCompletedMaePct: completedMae.length ? rounded(Math.min(...completedMae)) : null,
    averageCompletedMfePct: rounded(mean(completedMfe)),
    medianCompletedMfePct: rounded(median(completedMfe)),
    averageOpenAgeMinutes: rounded(mean(openAges)),
    medianOpenAgeMinutes: rounded(median(openAges)),
    oldestOpenMinutes: openAges.length ? rounded(Math.max(...openAges), 2) : null,
    oldestOpenSymbol: oldest?.symbol ?? null,
    averageOpenPnlPct: rounded(mean(openPnl)),
    worstOpenPnlPct: openPnl.length ? rounded(Math.min(...openPnl)) : null,
    averageOpenMaePct: rounded(mean(openMae)),
    worstOpenMaePct: openMae.length ? rounded(Math.min(...openMae)) : null,
    candleRowsProcessed: results.reduce((sum, result) => sum + result.bars, 0),
  };
}

export function aggregateProtectedResults(results: RecoverySymbolResult[]): ProtectedRecoverySummary {
  const positions = results.flatMap((result) => result.positions ?? []);
  const skipped = results.flatMap((result) => result.skippedSignals ?? []);
  const closed = positions.filter((position) => position.status !== "OPEN");
  const targetExits = positions.filter((position) => position.status === "TARGET_EXIT");
  const targetGaps = positions.filter((position) => position.status === "TARGET_GAP");
  const targets = [...targetExits, ...targetGaps];
  const stopExits = positions.filter((position) => position.status === "STOP_EXIT");
  const stopGaps = positions.filter((position) => position.status === "STOP_GAP");
  const timeExits = positions.filter((position) => position.status === "TIME_EXIT");
  const open = positions.filter((position) => position.status === "OPEN");
  const realized = finite(closed.map((position) => position.realizedPnl));
  const profits = realized.filter((value) => value > 0);
  const losses = realized.filter((value) => value < 0);
  const gross = closed.map((position) => position.grossPnl);
  const dynamicExitResults = positions.some((position) => position.exitModel === "FIXED_TP_SL" || position.exitModel === "ATR_DYNAMIC_TP_SL");
  const costs = finite((dynamicExitResults ? positions : closed).map((position) => position.tradingCosts ?? position.totalCosts));
  const estimatedOpenExitCosts = dynamicExitResults
    ? open.reduce((sum, position) => sum + (position.estimatedOpenExitCost ?? 0), 0)
    : 0;
  const dynamicTargets = finite(positions.map((position) => position.dynamicTargetPct));
  const dynamicStops = finite(positions.map((position) => position.dynamicStopPct));
  const rewardRisks = finite(positions.map((position) => position.rewardRiskRatio));
  const holdingMinutes = positions.map((position) => position.durationMinutes);
  const holdingSessions = positions.map((position) => position.holdingSessions);
  const timeline = positions.flatMap((position) => [
    { time: Date.parse(position.entryTimestamp), count: 1, capital: position.capitalDeployed },
    ...(position.exitTimestamp ? [{ time: Date.parse(position.exitTimestamp), count: -1, capital: -position.capitalDeployed }] : []),
  ]).sort((left, right) => left.time - right.time || left.count - right.count);
  let concurrent = 0;
  let maximumConcurrent = 0;
  let deployed = 0;
  let peakCapital = 0;
  timeline.forEach((event) => {
    concurrent += event.count;
    deployed += event.capital;
    maximumConcurrent = Math.max(maximumConcurrent, concurrent);
    peakCapital = Math.max(peakCapital, deployed);
  });
  const netRealizedPnl = realized.reduce((sum, value) => sum + value, 0);
  const unrealizedPnl = open.reduce((sum, position) => sum + position.unrealizedPnl, 0);
  const maximumDrawdown = results.reduce((sum, result) => sum + (result.maximumDrawdown ?? 0), 0);
  const totalValidBuySignals = positions.length + skipped.length;
  return {
    totalValidBuySignals,
    totalBuySignals: totalValidBuySignals,
    buySignals: totalValidBuySignals,
    executedTrades: positions.length,
    skippedMaxOpenLots: skipped.length,
    targetExits: targetExits.length,
    targetGapExits: targetGaps.length,
    stopExits: stopExits.length,
    stopGapExits: stopGaps.length,
    targetsHit: targets.length,
    timeExits: timeExits.length,
    openPositions: open.length,
    openSignals: open.length,
    targetHitRate: positions.length ? rounded(targets.length / positions.length * 100, 2) ?? 0 : 0,
    winningTrades: profits.length,
    losingTrades: losses.length,
    winRate: closed.length ? rounded(profits.length / closed.length * 100, 2) ?? 0 : 0,
    averageDynamicTargetPct: rounded(mean(dynamicTargets), 6),
    averageDynamicStopPct: rounded(mean(dynamicStops), 6),
    averageRewardRisk: rounded(mean(rewardRisks), 6),
    profitableClosedTrades: profits.length,
    losingClosedTrades: losses.length,
    realizedGrossProfit: rounded(gross.filter((value) => value > 0).reduce((sum, value) => sum + value, 0), 2) ?? 0,
    realizedGrossLoss: rounded(Math.abs(gross.filter((value) => value < 0).reduce((sum, value) => sum + value, 0)), 2) ?? 0,
    grossProfit: rounded(gross.filter((value) => value > 0).reduce((sum, value) => sum + value, 0), 2) ?? 0,
    grossLoss: rounded(Math.abs(gross.filter((value) => value < 0).reduce((sum, value) => sum + value, 0)), 2) ?? 0,
    tradingCosts: rounded(costs.reduce((sum, value) => sum + value, 0), 2) ?? 0,
    estimatedOpenExitCosts: rounded(estimatedOpenExitCosts, 2) ?? 0,
    netRealizedPnl: rounded(netRealizedPnl, 2) ?? 0,
    unrealizedPnl: rounded(unrealizedPnl, 2) ?? 0,
    combinedPnl: rounded(netRealizedPnl + unrealizedPnl, 2) ?? 0,
    averageProfitPerTrade: rounded(mean(profits), 2),
    averageLossPerTrade: rounded(mean(losses), 2),
    averageWinner: rounded(mean(profits), 2),
    averageLoser: rounded(mean(losses), 2),
    profitFactor: losses.length ? rounded(profits.reduce((sum, value) => sum + value, 0) / Math.abs(losses.reduce((sum, value) => sum + value, 0)), 4) : null,
    expectancyPerTrade: rounded(mean(realized), 2),
    maximumDrawdown: rounded(maximumDrawdown, 2) ?? 0,
    maximumDrawdownPct: peakCapital ? rounded(maximumDrawdown / peakCapital * 100, 4) ?? 0 : 0,
    maximumConcurrentPositions: maximumConcurrent,
    maxConcurrentPositions: maximumConcurrent,
    peakCapitalDeployed: rounded(peakCapital, 2) ?? 0,
    averageHoldingMinutes: rounded(mean(holdingMinutes), 2),
    medianHoldingMinutes: rounded(median(holdingMinutes), 2),
    averageHoldingSessions: rounded(mean(holdingSessions), 2),
    medianHoldingSessions: rounded(median(holdingSessions), 2),
    candleRowsProcessed: results.reduce((sum, result) => sum + result.bars, 0),
  };
}

export function mergeRecoveryResponses(
  current: RecoveryBacktestResponse | null,
  incoming: RecoveryBacktestResponse,
): RecoveryBacktestResponse {
  if (!current) return incoming;
  const results = [...current.results, ...incoming.results];
  return {
    metadata: {
      ...current.metadata,
      completedAt: incoming.metadata.completedAt,
      generatedAt: incoming.metadata.generatedAt,
      dataFrom: [current.metadata.dataFrom, incoming.metadata.dataFrom].filter(Boolean).sort()[0] ?? null,
      dataTo: [current.metadata.dataTo, incoming.metadata.dataTo].filter(Boolean).sort().at(-1) ?? null,
      symbolsRequested: current.metadata.symbolsRequested + incoming.metadata.symbolsRequested,
      symbolsProcessed: current.metadata.symbolsProcessed + incoming.metadata.symbolsProcessed,
      symbolsFailed: current.metadata.symbolsFailed + incoming.metadata.symbolsFailed,
      runtimeSeconds: current.metadata.runtimeSeconds + incoming.metadata.runtimeSeconds,
    },
    summary: incoming.metadata.exitProtection?.enabled
      ? aggregateProtectedResults(results)
      : aggregateRecoveryResults(results),
    results,
    errors: [...current.errors, ...incoming.errors],
    warnings: Array.from(new Set([...current.warnings, ...incoming.warnings])),
  };
}

function number(value: number | null, digits = 2) {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(value);
}

function percent(value: number | null) {
  if (value === null) return "—";
  return `${value > 0 ? "+" : ""}${number(value)}%`;
}

function money(value: number | null) {
  return value === null ? "—" : `₹${number(value)}`;
}

function duration(minutes: number | null) {
  if (minutes === null) return "—";
  if (minutes < 60) return `${number(minutes, 0)}m`;
  if (minutes <= 1_440) return `${number(minutes / 60, 1)}h`;
  return `${number(minutes / 1_440, 1)}d`;
}

function formatIst(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function tone(value: number | null) {
  if (value === null || value === 0) return "neutral-value";
  return value > 0 ? "positive-value" : "negative-value";
}

function csvCell(value: unknown) {
  const text = value === null || value === undefined ? "" : String(value);
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadCsv(filename: string, rows: Array<Record<string, unknown>>) {
  if (!rows.length) return;
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
  const body = [columns.map(csvCell).join(","), ...rows.map((row) => columns.map((column) => csvCell(row[column])).join(","))].join("\r\n");
  const url = URL.createObjectURL(new Blob([body], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

type SortKey = keyof Pick<RecoverySymbolResult,
  "symbol" | "buySignals" | "targetsHit" | "targetHitRate" | "openSignals" | "maximumConcurrentOpenSignals" | "medianTargetMinutes" | "averageMaePct" | "medianMaePct" | "worstMaePct" | "qualityScore"
>;

function ProtectedPositionTable({ positions }: { positions: ProtectedPosition[] }) {
  if (!positions.length) return <div className="empty-history">No executed positions are available in this view.</div>;
  return <div className="protected-position-table" role="region" aria-label="Protected position results">
    <div className="protected-position-grid protected-position-head"><span>Signal</span><span>Entry</span><span>ATR at entry</span><span>Entry price</span><span>Qty</span><span>Capital</span><span>TP</span><span>SL</span><span>Exit</span><span>Reason</span><span>Sessions</span><span>MAE / MFE</span><span>Net P&amp;L</span><span>Unrealized P&amp;L</span><span>Status</span></div>
    {positions.map((position) => <div className="protected-position-grid protected-position-row" key={position.tradeId}>
      <span data-label="Signal">{formatIst(position.signalTimestamp)}</span>
      <span data-label="Entry">{formatIst(position.entryTimestamp)}</span>
      <span data-label="ATR at entry">{position.atrAtSignal === undefined ? "—" : money(position.atrAtSignal)}<small>{position.atrPctAtEntry === undefined ? "" : percent(position.atrPctAtEntry)}</small></span>
      <span data-label="Entry price">{money(position.entryPrice)}</span>
      <span data-label="Quantity">{position.quantity}</span>
      <span data-label="Capital">{money(position.capitalDeployed)}</span>
      <span data-label="Take profit">{money(position.targetPrice)}<small>{position.dynamicTargetPct === undefined ? "" : percent(position.dynamicTargetPct)}</small></span>
      <span data-label="Stop loss">{position.stopLossPrice === undefined ? "—" : money(position.stopLossPrice)}<small>{position.dynamicStopPct === undefined ? "" : percent(-position.dynamicStopPct)}</small></span>
      <span data-label="Exit">{formatIst(position.exitTimestamp)}<small>{money(position.exitPrice)}</small></span>
      <span data-label="Exit reason">{position.exitReason?.replaceAll("_", " ") ?? "—"}</span>
      <span data-label="Holding sessions">{position.holdingSessions}<small>{duration(position.durationMinutes)} · {position.barsHeld} bars</small></span>
      <span data-label="MAE / MFE"><b className="negative-value">{percent(position.maxAdversePct)}</b><small className="positive-value">{percent(position.maxFavorablePct)}</small></span>
      <span data-label="Net P&L" className={tone(position.realizedPnl)}>{money(position.realizedPnl)}<small>{position.tradingCosts === undefined ? "" : `${money(position.tradingCosts)} costs`}</small></span>
      <span data-label="Unrealized P&L" className={tone(position.status === "OPEN" ? position.unrealizedPnl : null)}>{position.status === "OPEN" ? money(position.unrealizedPnl) : "—"}</span>
      <span data-label="Status"><b className={`trade-status ${position.status.toLowerCase()}`}>{position.status.replaceAll("_", " ")}</b></span>
    </div>)}
  </div>;
}

function ProtectedRecoveryResults({ response }: { response: RecoveryBacktestResponse }) {
  const [activeView, setActiveView] = useState<"overview" | "signals" | "open" | "features">("overview");
  const summary = response.summary as ProtectedRecoverySummary;
  const positions = useMemo(
    () => response.results.flatMap((result) => result.positions ?? []).sort((left, right) => Date.parse(right.entryTimestamp) - Date.parse(left.entryTimestamp)),
    [response.results],
  );
  const skipped = useMemo(
    () => response.results.flatMap((result) => result.skippedSignals ?? []).sort((left, right) => Date.parse(right.signalTimestamp) - Date.parse(left.signalTimestamp)),
    [response.results],
  );
  const open = positions.filter((position) => position.status === "OPEN");
  const tabs = [
    ["overview", "Overview", null],
    ["signals", "Signals", summary.totalValidBuySignals],
    ["open", "Open Signals", summary.openPositions],
    ["features", "Feature Analysis", null],
  ] as const;
  const protection = response.metadata.exitProtection;
  const dynamicExit = response.metadata.exitModel === "ATR_DYNAMIC_TP_SL" || response.metadata.exitModel === "FIXED_TP_SL";

  return <>
    <nav className="recovery-result-tabs" aria-label="Protected position result views">
      {tabs.map(([key, label, count]) => <button key={key} type="button" className={activeView === key ? "active" : ""} onClick={() => setActiveView(key)}>{label}{count === null ? "" : ` (${count.toLocaleString("en-IN")})`}</button>)}
    </nav>

    {activeView === "overview" && <>
      <section className="recovery-top-cards protected-top-cards" aria-label="Protected position summary">
        <div><span>Valid BUY signals</span><strong>{summary.totalValidBuySignals.toLocaleString("en-IN")}</strong></div>
        <div><span>Executed trades</span><strong>{summary.executedTrades.toLocaleString("en-IN")}</strong></div>
        <div><span>Skipped · max lots</span><strong>{summary.skippedMaxOpenLots.toLocaleString("en-IN")}</strong></div>
        <div><span>Target exits</span><strong>{(summary.targetExits + (summary.targetGapExits ?? 0)).toLocaleString("en-IN")}</strong></div>
        {dynamicExit && <div><span>Stop exits</span><strong>{((summary.stopExits ?? 0) + (summary.stopGapExits ?? 0)).toLocaleString("en-IN")}</strong></div>}
        <div><span>Time exits</span><strong>{summary.timeExits.toLocaleString("en-IN")}</strong></div>
        <div><span>Open positions</span><strong className={summary.openPositions ? "warning-value" : "positive-value"}>{summary.openPositions.toLocaleString("en-IN")}</strong></div>
      </section>
      <div className="research-semantics"><Info size={16} /><span><strong>Position backtest with {dynamicExit ? "explicit TP/SL exits" : "legacy exit protection"}.</strong> Win rate and net P&amp;L measure closed-trade profitability; target-hit rate is not used as a substitute. Skipped signals remain separate.</span></div>

      <section className="backtest-panel recovery-section">
        <div className="panel-title"><div><span className="section-kicker">Position outcomes</span><h2>Exit and profitability summary</h2></div><span className="date-window">{protection?.quantityPerTrade ?? 50} shares · max {protection?.maxOpenLotsPerSymbol ?? 1} lot · {protection?.maxHoldingTradingDays ?? 5} NSE sessions</span></div>
        <div className="metric-grid protected-metric-grid">
          <div><span>Target-hit rate</span><strong>{percent(summary.targetHitRate)}</strong></div>
          {dynamicExit && <div><span>Win rate</span><strong>{percent(summary.winRate ?? 0)}</strong></div>}
          {dynamicExit && <div><span>Target gaps</span><strong>{summary.targetGapExits ?? 0}</strong></div>}
          {dynamicExit && <div><span>Stop exits / gaps</span><strong>{summary.stopExits ?? 0} / {summary.stopGapExits ?? 0}</strong></div>}
          <div><span>Profitable closed</span><strong className="positive-value">{summary.profitableClosedTrades}</strong></div>
          <div><span>Losing closed</span><strong className="negative-value">{summary.losingClosedTrades}</strong></div>
          <div><span>Realized gross profit</span><strong className="positive-value">{money(summary.realizedGrossProfit)}</strong></div>
          <div><span>Realized gross loss</span><strong className="negative-value">{money(-summary.realizedGrossLoss)}</strong></div>
          <div><span>Net realized P&amp;L</span><strong className={tone(summary.netRealizedPnl)}>{money(summary.netRealizedPnl)}</strong></div>
          <div><span>Unrealized P&amp;L</span><strong className={tone(summary.unrealizedPnl)}>{money(summary.unrealizedPnl)}</strong></div>
          <div><span>Combined P&amp;L</span><strong className={tone(summary.combinedPnl)}>{money(summary.combinedPnl)}</strong></div>
          <div><span>Average profit / trade</span><strong className="positive-value">{money(summary.averageProfitPerTrade)}</strong></div>
          <div><span>Average loss / trade</span><strong className="negative-value">{money(summary.averageLossPerTrade)}</strong></div>
          <div><span>Profit factor</span><strong>{number(summary.profitFactor)}</strong></div>
          {dynamicExit && <div><span>Average TP / SL</span><strong>{percent(summary.averageDynamicTargetPct ?? null)} / {percent(-(summary.averageDynamicStopPct ?? 0))}</strong></div>}
          {dynamicExit && <div><span>Average reward:risk</span><strong>{number(summary.averageRewardRisk ?? null)}</strong></div>}
          {dynamicExit && <div><span>Trading costs</span><strong>{money(summary.tradingCosts ?? 0)}</strong></div>}
          {dynamicExit && <div><span>Expectancy / closed trade</span><strong className={tone(summary.expectancyPerTrade ?? null)}>{money(summary.expectancyPerTrade ?? null)}</strong></div>}
          <div><span>Maximum drawdown</span><strong className="negative-value">{money(-summary.maximumDrawdown)}<small>{percent(-summary.maximumDrawdownPct)}</small></strong></div>
          <div><span>Maximum concurrent</span><strong>{summary.maximumConcurrentPositions}</strong></div>
          <div><span>Peak capital deployed</span><strong>{money(summary.peakCapitalDeployed)}</strong></div>
          <div><span>Average holding time</span><strong>{duration(summary.averageHoldingMinutes)}</strong></div>
          <div><span>Median holding time</span><strong>{duration(summary.medianHoldingMinutes)}</strong></div>
        </div>
      </section>
    </>}

    {activeView === "signals" && <>
      <section className="backtest-panel recovery-detail-panel">
        <div className="panel-title"><div><span className="section-kicker">Executed positions only</span><h2>Position trade history</h2></div><button type="button" onClick={() => downloadCsv("protected_positions.csv", positions as unknown as Array<Record<string, unknown>>)}><Download size={15} />Positions CSV</button></div>
        <ProtectedPositionTable positions={positions} />
      </section>
      <section className="backtest-panel recovery-detail-panel">
        <div className="panel-title"><div><span className="section-kicker">Valid signals not executed</span><h2>Skipped · maximum open lots</h2></div><button type="button" onClick={() => downloadCsv("skipped_max_open_lots.csv", skipped as unknown as Array<Record<string, unknown>>)}><Download size={15} />Skipped CSV</button></div>
        {skipped.length ? <div className="skipped-signal-list">{skipped.map((signal) => <div key={signal.tradeId}><b>#{signal.sequenceNumber}</b><span>{formatIst(signal.signalTimestamp)}</span><strong>SKIPPED MAX OPEN LOTS</strong><small>{signal.reason}</small></div>)}</div> : <div className="empty-history">No valid signals were skipped by the open-lot limit.</div>}
      </section>
    </>}

    {activeView === "open" && <section className="backtest-panel recovery-detail-panel">
      <div className="panel-title"><div><span className="section-kicker">Marked at final available close</span><h2>Open protected positions</h2></div><span className="date-window">Unrealized P&amp;L remains separate from realized P&amp;L</span></div>
      <ProtectedPositionTable positions={open} />
    </section>}

    {activeView === "features" && <FeatureAnalysis />}

    <section className="backtest-notes recovery-run-metadata">
      <h3>Run metadata and cautions</h3>
      <p><strong>Mode:</strong> {response.metadata.exitModel?.replaceAll("_", " ") ?? "Legacy exit protection"} · {response.metadata.executionModel}{dynamicExit ? " · TP/SL frozen at entry" : ` · target ${number(Number(response.metadata.strategyParameters.targetPct))}% · no stop loss`}.</p>
      <p><strong>Costs:</strong> buy {number(response.metadata.costModel.buyCostBps)} bps, sell {number(response.metadata.costModel.sellCostBps)} bps, slippage {number(response.metadata.costModel.slippageBpsPerSide)} bps per side.</p>
      <p><strong>P&amp;L:</strong> Combined P&amp;L equals net realized P&amp;L plus estimated net unrealized P&amp;L at the final close. Multi-symbol drawdown conservatively sums independent symbol drawdowns.</p>
      {response.errors.map((item) => <p key={item.symbol}><strong>{item.symbol}:</strong> {item.message}</p>)}
      {response.warnings.map((warning) => <p key={warning}>{warning}</p>)}
    </section>
  </>;
}

export function RecoveryResults({ response }: { response: RecoveryBacktestResponse }) {
  return response.metadata.exitProtection?.enabled
    ? <ProtectedRecoveryResults response={response} />
    : <SignalRecoveryResults response={response} />;
}

function SignalRecoveryResults({ response }: { response: RecoveryBacktestResponse }) {
  const [activeView, setActiveView] = useState<"overview" | "signals" | "open" | "features">("overview");
  const [sortKey, setSortKey] = useState<SortKey>("qualityScore");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [detailSymbol, setDetailSymbol] = useState(response.results[0]?.symbol ?? null);
  const [visibleCount, setVisibleCount] = useState(100);
  const summary = response.summary as RecoverySummary;
  const sorted = useMemo(() => [...response.results].sort((left, right) => {
    const a = left[sortKey];
    const b = right[sortKey];
    const result = typeof a === "string" ? a.localeCompare(String(b)) : (a ?? -Infinity) - (Number(b) || 0);
    return sortDirection === "asc" ? result : -result;
  }), [response.results, sortDirection, sortKey]);
  const detail = response.results.find((result) => result.symbol === detailSymbol) ?? sorted[0] ?? null;
  const openTrades = useMemo(
    () => response.results.flatMap((result) => result.trades.filter((trade) => trade.status === "OPEN")),
    [response.results],
  );

  const sort = (key: SortKey) => {
    if (key === sortKey) setSortDirection((current) => current === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDirection(key === "symbol" ? "asc" : "desc");
    }
  };
  const sortLabel = (label: string, key: SortKey) => (
    <button type="button" onClick={() => sort(key)}>{label}{sortKey === key ? (sortDirection === "asc" ? <SortAsc size={11} /> : <SortDesc size={11} />) : null}</button>
  );

  const exportTrades = () => downloadCsv("trades.csv", response.results.flatMap((result) => result.trades));
  const exportSummary = () => downloadCsv("symbol_summary.csv", response.results.map((result) => {
    const row: Record<string, unknown> = { ...result, speedBuckets: JSON.stringify(result.speedBuckets) };
    delete row.trades;
    delete row.events;
    delete row.chart;
    return row;
  }));
  const exportOpen = () => downloadCsv("open_positions.csv", response.results.flatMap((result) => result.trades.filter((trade) => trade.status === "OPEN")));

  return (
    <>
      <nav className="recovery-result-tabs" aria-label="RSI Recovery result views">
        {([
          ["overview", "Overview"],
          ["signals", "Signals"],
          ["open", "Open Signals"],
          ["features", "Feature Analysis"],
        ] as const).map(([key, label]) => {
          const count = key === "signals" ? summary.buySignals : key === "open" ? summary.stillOpen : null;
          return <button key={key} type="button" className={activeView === key ? "active" : ""} onClick={() => setActiveView(key)}>{label}{count === null ? "" : ` (${count.toLocaleString("en-IN")})`}</button>;
        })}
      </nav>

      {activeView === "overview" && <>
      <section className="recovery-top-cards" aria-label="RSI Recovery summary">
        <div><span>BUY Signals</span><strong>{summary.buySignals.toLocaleString("en-IN")}</strong></div>
        <div><span>Targets Hit</span><strong>{summary.targetsHit.toLocaleString("en-IN")}</strong></div>
        <div><span>Hit Rate</span><strong>{percent(summary.targetHitRate)}</strong></div>
        <div><span>Open Signals</span><strong className={summary.stillOpen ? "warning-value" : "positive-value"}>{summary.stillOpen.toLocaleString("en-IN")}</strong></div>
        <div><span>Max Concurrent Signals</span><strong>{summary.maximumConcurrentSignalsUniverse.toLocaleString("en-IN")}</strong></div>
        <div><span>Max Concurrent Same Symbol</span><strong>{summary.maximumConcurrentSignalsSameSymbol.toLocaleString("en-IN")}</strong></div>
      </section>
      <div className="research-semantics"><Info size={14} /><span><strong>Signal backtest, not a portfolio backtest.</strong> Every fresh RSI arm/recovery cycle is an independent observation, even while earlier observations for the same symbol remain open.</span></div>

      <section className="backtest-panel recovery-section">
        <div className="panel-title recovery-panel-title"><div><span className="section-kicker">Target achievement</span><h2>Target speed</h2></div><span className="date-window">Percentages use completed targets only</span></div>
        <div className="speed-bucket-grid">{speedOrder.map(([key, label]) => {
          const item = summary.targetSpeedBuckets[key] ?? { count: 0, pct: 0 };
          return <div key={key}><span>{label}</span><strong>{item.count.toLocaleString("en-IN")}</strong><small>{number(item.pct)}% of completed</small></div>;
        })}</div>
        <div className="metric-grid recovery-metrics">
          <div><span>Average target time</span><strong>{duration(summary.averageTargetMinutes)}</strong></div>
          <div><span>Median target time</span><strong>{duration(summary.medianTargetMinutes)}</strong></div>
          <div><span>Average bars</span><strong>{number(summary.averageBarsToTarget, 1)}</strong></div>
          <div><span>Median bars</span><strong>{number(summary.medianBarsToTarget, 1)}</strong></div>
        </div>
        <div className="session-breakdown">{sessionOrder.map(([key, label]) => {
          const item = summary.sessionSpeedBuckets[key] ?? { count: 0, pct: 0 };
          return <span key={key}><b>{label}</b>{item.count} · {number(item.pct)}%</span>;
        })}</div>
      </section>

      <div className="recovery-two-column">
        <section className="backtest-panel recovery-section compact-section">
          <div className="panel-title"><div><span className="section-kicker">Risk while waiting</span><h2>Completed-signal excursions</h2></div></div>
          <div className="metric-grid recovery-risk-grid">
            <div><span>Average completed MAE</span><strong className="negative-value">{percent(summary.averageCompletedMaePct)}</strong></div>
            <div><span>Median completed MAE</span><strong className="negative-value">{percent(summary.medianCompletedMaePct)}</strong></div>
            <div><span>Worst completed MAE</span><strong className="negative-value">{percent(summary.worstCompletedMaePct)}</strong></div>
            <div><span>Average completed MFE</span><strong className="positive-value">{percent(summary.averageCompletedMfePct)}</strong></div>
            <div><span>Median completed MFE</span><strong className="positive-value">{percent(summary.medianCompletedMfePct)}</strong></div>
          </div>
        </section>
        <section className="backtest-panel recovery-section compact-section">
          <div className="panel-title"><div><span className="section-kicker">Open signal observations</span><h2>Unresolved observations</h2></div><span className="cost-note">{summary.symbolsWithOpenSignals} symbols · {summary.symbolsWith2PlusOpenSignals} with 2+ · {summary.symbolsWith5PlusOpenSignals} with 5+</span></div>
          <div className="metric-grid recovery-risk-grid">
            <div><span>Open signals</span><strong>{summary.stillOpen}</strong></div>
            <div><span>Average open / symbol</span><strong>{number(summary.averageOpenSignalsPerSymbol)}</strong></div>
            <div><span>Average open age</span><strong>{duration(summary.averageOpenAgeMinutes)}</strong></div>
            <div><span>Median open age</span><strong>{duration(summary.medianOpenAgeMinutes)}</strong></div>
            <div><span>Oldest open</span><strong>{duration(summary.oldestOpenMinutes)}{summary.oldestOpenSymbol ? ` · ${summary.oldestOpenSymbol}` : ""}</strong></div>
            <div><span>Average open P&amp;L</span><strong className={tone(summary.averageOpenPnlPct)}>{percent(summary.averageOpenPnlPct)}</strong></div>
            <div><span>Worst open P&amp;L</span><strong className="negative-value">{percent(summary.worstOpenPnlPct)}</strong></div>
            <div><span>Average open MAE</span><strong className="negative-value">{percent(summary.averageOpenMaePct)}</strong></div>
            <div><span>Worst open MAE</span><strong className="negative-value">{percent(summary.worstOpenMaePct)}</strong></div>
          </div>
        </section>
      </div>

      </>}

      {activeView === "signals" && <>
      <section className="backtest-panel recovery-symbol-panel">
        <div className="panel-title recovery-panel-title">
          <div><span className="section-kicker">All tested symbols</span><h2>Signal-quality ranking</h2></div>
          <div className="export-actions"><button type="button" onClick={exportTrades}><Download size={13} />Trades CSV</button><button type="button" onClick={exportSummary}><Download size={13} />Summary CSV</button><button type="button" onClick={exportOpen}><Download size={13} />Open CSV</button></div>
        </div>
        <div className="quality-formula"><Info size={14} /><span><strong>Transparent quality score:</strong> 40% target hit rate + 30% target speed + 20% MAE quality + 10% resolved-signal score. Open rate is open observations divided by all BUY observations. It is a research ranking, not profit probability or AI confidence.</span></div>
        <div className="recovery-summary-grid recovery-summary-head">
          <span>{sortLabel("Symbol", "symbol")}</span><span>{sortLabel("BUYs", "buySignals")}</span><span>{sortLabel("Targets hit", "targetsHit")}</span><span>{sortLabel("Hit %", "targetHitRate")}</span><span>{sortLabel("Open signals", "openSignals")}</span><span>{sortLabel("Max concurrent", "maximumConcurrentOpenSignals")}</span><span>{sortLabel("Median target", "medianTargetMinutes")}</span><span>{sortLabel("Avg MAE", "averageMaePct")}</span><span>{sortLabel("Median MAE", "medianMaePct")}</span><span>{sortLabel("Worst MAE", "worstMaePct")}</span><span>{sortLabel("Quality", "qualityScore")}</span>
        </div>
        {sorted.slice(0, visibleCount).map((result) => (
          <button key={result.symbol} type="button" className={`recovery-summary-grid recovery-summary-row ${detail?.symbol === result.symbol ? "selected" : ""}`} onClick={() => setDetailSymbol(result.symbol)}>
            <strong>{result.symbol}</strong><span data-label="BUYs">{result.buySignals}</span><span data-label="Targets hit">{result.targetsHit}</span><span data-label="Hit %">{number(result.targetHitRate)}%</span><span data-label="Open signals">{result.openSignals}</span><span data-label="Max concurrent">{result.maximumConcurrentOpenSignals}</span><span data-label="Median target">{duration(result.medianTargetMinutes)}</span><span data-label="Avg MAE" className="negative-value">{percent(result.averageMaePct)}</span><span data-label="Median MAE" className="negative-value">{percent(result.medianMaePct)}</span><span data-label="Worst MAE" className="negative-value">{percent(result.worstMaePct)}</span><span data-label="Quality"><b>{number(result.qualityScore, 1)}</b></span>
          </button>
        ))}
        {visibleCount < sorted.length && <button type="button" className="load-more-results" onClick={() => setVisibleCount((current) => current + 100)}>Show 100 more symbols</button>}
      </section>

      {detail && <section className="backtest-panel recovery-detail-panel">
        <div className="panel-title recovery-panel-title"><div><span className="section-kicker">Selected symbol</span><h2>{detail.symbol} recovery trades</h2></div><span className="date-window">{formatIst(detail.firstCandle)} – {formatIst(detail.lastCandle)}</span></div>
        <div className="quality-components">
          <span>Hit-rate score <b>{number(detail.hitRateScore, 1)}</b></span><span>Speed score <b>{number(detail.speedScore, 1)}</b></span><span>MAE score <b>{number(detail.maeScore, 1)}</b></span><span>Open penalty <b>{number(detail.openPenalty, 1)}</b></span><span>Quality <b>{number(detail.qualityScore, 1)}</b></span>
        </div>
        {detail.trades.length ? <div className="recovery-trade-list">
          <div className="recovery-trade-grid recovery-trade-head"><span>Entry</span><span>Entry price</span><span>Target</span><span>Target hit</span><span>Time / bars</span><span>Confirmations</span><span>EMA</span><span>VWAP</span><span>Volume</span><span>MAE</span><span>MFE</span><span>Status</span></div>
          {detail.trades.map((trade) => <div className="recovery-trade-grid recovery-trade-row" key={trade.tradeId} title={`Trade ${trade.tradeId}. RSI recovery mandatory; ${trade.confirmationScore}/${trade.requiredConfirmations} enabled confirmations passed. Independent signal observation; no stop loss or end-of-day exit. Target monitoring started after its own entry candle.`}>
            <span data-label="Entry"><b>#{trade.sequenceNumber}</b> {formatIst(trade.entryTimestamp)}<small>Signal {formatIst(trade.signalTimestamp)}</small></span><span data-label="Entry price">{money(trade.entryPrice)}</span><span data-label="Target">{money(trade.targetPrice)}</span><span data-label="Target hit">{formatIst(trade.targetHitTimestamp)}</span><span data-label="Time / bars">{duration(trade.durationMinutes)}<small>{trade.barsHeld} bars · {trade.tradingSessionsHeld} sessions</small></span><span data-label="Confirmations"><b>{trade.confirmationScore}/{trade.requiredConfirmations}</b><small>RSI {number(trade.rsiAtEntry)}</small></span><span data-label="EMA" className={trade.emaConfirmation ? "positive-value" : "neutral-value"}>{trade.emaEnabled ? (trade.emaConfirmation ? "Pass" : "Fail") : "Off"}</span><span data-label="VWAP" className={trade.vwapConfirmation ? "positive-value" : "neutral-value"}>{trade.vwapEnabled ? (trade.vwapConfirmation ? "Pass" : "Fail") : "Off"}</span><span data-label="Volume" className={trade.volumeConfirmation ? "positive-value" : "neutral-value"}>{trade.volumeEnabled ? (trade.volumeConfirmation ? "Pass" : "Fail") : "Off"}</span><span data-label="MAE" className="negative-value">{percent(trade.maxAdversePct)}</span><span data-label="MFE" className="positive-value">{percent(trade.maxFavorablePct)}</span><span data-label="Status"><b className={`trade-status ${trade.status === "OPEN" ? "open" : "hit"}`}>{trade.status === "OPEN" ? "OPEN" : "TARGET HIT"}</b>{trade.status === "OPEN" && <small>{percent(trade.currentPnlPct)} · {duration(trade.durationMinutes)}</small>}</span>
          </div>)}
        </div> : <div className="empty-history">No valid armed-RSI recovery BUY occurred for this symbol in the selected window.</div>}
      </section>}
      </>}

      {activeView === "open" && <>
        <section className="recovery-top-cards" aria-label="Open signal observations">
          <div><span>Open Signals</span><strong className="warning-value">{summary.stillOpen.toLocaleString("en-IN")}</strong></div>
          <div><span>Symbols with Open</span><strong>{summary.symbolsWithOpenSignals.toLocaleString("en-IN")}</strong></div>
          <div><span>Median Open Age</span><strong>{duration(summary.medianOpenAgeMinutes)}</strong></div>
          <div><span>Worst Open P&amp;L</span><strong className="negative-value">{percent(summary.worstOpenPnlPct)}</strong></div>
        </section>
        <section className="backtest-panel recovery-detail-panel">
          <div className="panel-title recovery-panel-title"><div><span className="section-kicker">Trapped-capital research outcomes</span><h2>All unresolved signal observations</h2></div><button type="button" onClick={exportOpen}><Download size={13} />Open CSV</button></div>
          <div className="research-semantics"><Info size={14} /><span>These are independent signal observations, not claims that capital was invested in each one. Multiple OPEN rows for a symbol are intentionally preserved.</span></div>
          <div className="recovery-trade-list">
            <div className="open-signal-grid recovery-trade-head"><span>Symbol / #</span><span>Entry</span><span>Entry price</span><span>Target</span><span>Age</span><span>Current P&amp;L</span><span>MAE</span><span>MFE</span></div>
            {openTrades.map((trade) => <div className="open-signal-grid recovery-trade-row" key={trade.tradeId}>
              <span data-label="Symbol"><b>{trade.symbol} #{trade.sequenceNumber}</b></span><span data-label="Entry">{formatIst(trade.entryTimestamp)}</span><span data-label="Entry price">{money(trade.entryPrice)}</span><span data-label="Target">{money(trade.targetPrice)}</span><span data-label="Age">{duration(trade.durationMinutes)}<small>{trade.barsHeld} bars · {trade.tradingSessionsHeld} sessions</small></span><span data-label="Current P&L" className={tone(trade.currentPnlPct)}>{percent(trade.currentPnlPct)}</span><span data-label="MAE" className="negative-value">{percent(trade.maxAdversePct)}</span><span data-label="MFE" className="positive-value">{percent(trade.maxFavorablePct)}</span>
            </div>)}
          </div>
        </section>
      </>}

      {activeView === "features" && <FeatureAnalysis />}

      <section className="backtest-notes recovery-run-metadata">
        <h3>Run metadata and cautions</h3>
        <p><strong>Run:</strong> {response.metadata.runId} · {response.metadata.strategyVersion} · {response.metadata.executionModel} · {response.metadata.timeframe} · {response.metadata.durationYears}Y</p>
        <p><strong>Processed:</strong> {response.metadata.symbolsProcessed}/{response.metadata.symbolsRequested} symbols, {response.metadata.symbolsFailed} failed, {summary.candleRowsProcessed.toLocaleString("en-IN")} candle rows, {number(response.metadata.runtimeSeconds)} seconds, {response.metadata.workerCount} workers.</p>
        <p><strong>Costs:</strong> buy {number(response.metadata.costModel.buyCostBps)} bps, sell {number(response.metadata.costModel.sellCostBps)} bps, slippage {number(response.metadata.costModel.slippageBpsPerSide)} bps per side. Estimated round trip {number(response.metadata.costModel.estimatedRoundTripCostPct)}%.</p>
        {response.errors.map((item) => <p key={item.symbol}><strong>{item.symbol}:</strong> {item.message}</p>)}
        {response.warnings.map((warning) => <p key={warning}>{warning}</p>)}
      </section>
    </>
  );
}
