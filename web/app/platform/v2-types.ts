import type { PlatformMarket } from "./platform-client";
import type { ConfigSchema, ConfigValues } from "./schema-form";

/** One dashboard section; `available:false` carries the upstream error instead of data. */
export type Section<T> = { available: boolean; error?: string | null; data?: T | null };

export type DataFreshness = { status?: string | null; ageSeconds?: number | null; reason?: string | null };
export type MarketDataSummary = {
  dataFreshness?: DataFreshness | null;
  jobStatus?: { status?: string | null; engineStatus?: string | null; connectionStatus?: string | null } | null;
  environment?: string | null;
};

export type ScreenerFilterDefaults = Record<string, unknown>;
export type ScreenerFiltersResponse = { defaults: ScreenerFilterDefaults; rankBy: string[]; markets?: string[] };
export type ScreenerRun = {
  runId: string;
  market: PlatformMarket;
  status: string;
  filters?: Record<string, unknown> | null;
  symbolsTotal?: number | null;
  symbolsPassed?: number | null;
  error?: string | null;
  requestedAt?: string | null;
  completedAt?: string | null;
};
export type ScreenerMetrics = {
  error?: string | null;
  lastPrice?: number | null;
  averageTradedValue?: number | null;
  averageVolume?: number | null;
  volatilityPct?: number | null;
  candleCoverage?: number | null;
  sessions?: number | null;
  bars?: number | null;
};
export type ScreenerResult = { symbol: string; passed: boolean; rank?: number | null; score?: number | null; rejectionReason?: string | null; metrics?: ScreenerMetrics | null };
export type ScreenerResultsResponse = { run: ScreenerRun; results: ScreenerResult[] };
export type Universe = {
  universeId: string;
  market: PlatformMarket;
  name: string;
  symbols: string[];
  manualIncludes?: string[] | null;
  manualExcludes?: string[] | null;
  active: boolean;
  createdAt?: string | null;
};
export type UniversesResponse = { universes: Universe[]; active?: Partial<Record<PlatformMarket, Universe | null>> };
export type UniversePreset = {
  presetId: string;
  market: PlatformMarket;
  name: string;
  description: string;
  asOf: string;
  sourceUrl: string;
  symbols: string[];
};
export type UniversePresetsResponse = { presets: UniversePreset[] };

export type Strategy = {
  strategyId: string;
  name: string;
  version: string;
  supportedMarkets: string[];
  supportedTimeframes: string[];
  configSchema: ConfigSchema;
  defaults: ConfigValues;
};
export type StrategiesResponse = {
  strategies: Strategy[];
  markets: string[];
  riskDefaults: ConfigValues;
  /** Schema for the risk/execution settings (same shape as `configSchema`); older services omit it. */
  riskSchema?: ConfigSchema | null;
};
export type StrategyConfig = {
  configId: string;
  name: string;
  configuration: ConfigValues;
  riskSettings: ConfigValues;
  active: boolean;
  createdAt?: string | null;
  updatedAt?: string | null;
  strategyVersion?: string | null;
};
export type StrategyConfigResponse = {
  strategyId: string;
  market: PlatformMarket;
  active: StrategyConfig | null;
  effectiveConfiguration: ConfigValues;
  effectiveRiskSettings: ConfigValues;
  all: StrategyConfig[];
};
export type StrategyDeploymentMode = "OFF" | "SIGNALS" | "PAPER";
export type StrategyDeployment = {
  deploymentId?: string | null;
  market: PlatformMarket;
  strategyId: string;
  strategyVersion: string;
  configId?: string | null;
  timeframe: string;
  mode: StrategyDeploymentMode;
  source: "DATABASE" | "ENVIRONMENT" | "DEFAULT";
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type BacktestMetrics = {
  totalSignals?: number | null;
  completedTrades?: number | null;
  targetHits?: number | null;
  stoppedTrades?: number | null;
  expiredTrades?: number | null;
  openTrades?: number | null;
  realizedPnl?: number | null;
  unrealizedPnl?: number | null;
  fees?: number | null;
  slippage?: number | null;
  winRate?: number | null;
  averageMaePct?: number | null;
  averageMfePct?: number | null;
  averageHoldingMinutes?: number | null;
  medianHoldingMinutes?: number | null;
  maximumDrawdown?: number | null;
  symbolsProcessed?: number | null;
  symbolsFailed?: number | null;
};
export type BacktestStatus = "QUEUED" | "RUNNING" | "COMPLETE" | "FAILED" | "CANCELLED" | "INTERRUPTED";
export type BacktestRun = {
  runId: string;
  market: PlatformMarket;
  strategyId: string;
  strategyVersion?: string | null;
  configurationSnapshot?: ConfigValues | null;
  executionSettings?: ConfigValues | null;
  timeframe: string;
  symbols: string[];
  startDate: string;
  endDate: string;
  status: BacktestStatus | string;
  cancelRequested?: boolean;
  symbolsTotal?: number | null;
  symbolsCompleted?: number | null;
  currentSymbol?: string | null;
  failedSymbols?: Array<{ symbol: string; message: string }> | null;
  metrics?: BacktestMetrics | null;
  error?: string | null;
  createdAt?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
};
export type BacktestRunsResponse = { runs: BacktestRun[] };
export type BacktestTrade = {
  symbol: string;
  timeframe?: string | null;
  lotId: string;
  cycleId?: string | null;
  lotNumber?: number | null;
  signalTimestamp?: string | null;
  entryTimestamp?: string | null;
  entryPrice?: number | null;
  costBasisPrice?: number | null;
  fifoAllocations?: Array<{ lotId: string; quantity: number; entryPrice: number; fees: number }>;
  quantity?: number | null;
  targetPrice?: number | null;
  stopPrice?: number | null;
  exitTimestamp?: string | null;
  exitPrice?: number | null;
  status: string;
  grossPnl?: number | null;
  fees?: number | null;
  slippage?: number | null;
  netPnl?: number | null;
  unrealizedPnl?: number | null;
  lastPrice?: number | null;
  maePct?: number | null;
  mfePct?: number | null;
  holdingBars?: number | null;
  holdingMinutes?: number | null;
};
export type BacktestTradesResponse = { trades: BacktestTrade[]; total: number; limit: number; offset: number };

export type EngineStatus = {
  market?: PlatformMarket | string | null;
  status?: string | null;
  connectionStatus?: string | null;
  dataAgeSeconds?: number | null;
  lastCompletedCandle?: string | null;
  message?: string | null;
  updatedAt?: string | null;
};
export type WorkerStatus = EngineStatus & {
  engine?: string | null;
  strategyId?: string | null;
  strategyVersion?: string | null;
  timeframe?: string | null;
  symbols?: string[] | null;
  signalsCreated?: number | null;
  duplicatesRejected?: number | null;
};
export type SignalStatus = "STRONG_BUY" | "HOLDING" | "TARGET_HIT" | "EXITED" | "EXPIRED";
export type Signal = {
  signalId: string;
  market: PlatformMarket;
  strategyId: string;
  strategyVersion?: string | null;
  symbol: string;
  timeframe: string;
  candleTimestamp?: string | null;
  signalType?: string | null;
  status: SignalStatus | string;
  signalPrice?: number | null;
  targetPrice?: number | null;
  stopPrice?: number | null;
  expiresAt?: string | null;
  reasons?: string[] | null;
  indicators?: Record<string, unknown> | null;
  configurationSnapshot?: ConfigValues | null;
  lastPrice?: number | null;
  exitTimestamp?: string | null;
  exitPrice?: number | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  colour?: string | null;
};
export type SignalsResponse = { signals: Signal[]; colours: Record<string, string> };
export type SignalsHealth = { engines: EngineStatus[]; workers: Partial<Record<PlatformMarket, WorkerStatus[]>> };

export type PaperAccount = {
  market?: PlatformMarket;
  currency: string;
  startingBalance?: number | null;
  cashBalance?: number | null;
  marketValue?: number | null;
  equity?: number | null;
  openPositions?: number | null;
  closedLots?: number | null;
  realizedPnl?: number | null;
  realizedPnlToday?: number | null;
  unrealizedPnl?: number | null;
  dailyPnl?: number | null;
  asOf?: string | null;
  executionPolicy?: string | Record<string, unknown> | null;
  filled?: number | null;
  rejected?: number | null;
};
export type PaperLot = {
  lotId: string;
  symbol: string;
  cycleId?: string | null;
  lotNumber?: number | null;
  entryTimestamp?: string | null;
  entryPrice?: number | null;
  costBasisPrice?: number | null;
  fifoAllocations?: Array<{ lotId: string; quantity: number; entryPrice: number; fees: number }>;
  quantity?: number | null;
  targetPrice?: number | null;
  stopPrice?: number | null;
  expiresAt?: string | null;
  status: string;
  lastPrice?: number | null;
  unrealizedPnl?: number | null;
  maePct?: number | null;
  mfePct?: number | null;
  fees?: number | null;
};
export type PaperOrder = {
  orderId: string;
  symbol: string;
  side: string;
  quantity?: number | null;
  requestedPrice?: number | null;
  executedPrice?: number | null;
  fees?: number | null;
  slippage?: number | null;
  status: string;
  reason?: string | null;
  createdAt?: string | null;
};
export type PaperTrade = {
  symbol: string;
  side: string;
  quantity?: number | null;
  price?: number | null;
  fees?: number | null;
  slippage?: number | null;
  reason?: string | null;
  executedAt?: string | null;
};

export type DashboardPayload = {
  market: PlatformMarket;
  marketData: Section<MarketDataSummary>;
  screener: Section<{ latestRun: ScreenerRun | null; activeUniverse: Universe | null }>;
  backtests: Section<{ recent: BacktestRun[] }>;
  signalEngine: Section<{ stored: EngineStatus[]; workers: WorkerStatus[] }>;
  paper: Section<{ account: PaperAccount; openPositions: PaperLot[] }>;
  paperOnly?: boolean;
  liveOrdersEnabled?: boolean;
};
