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
  profileVersionId?: string | null;
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
export type WatchlistProfileVersion = {
  profileVersionId: string;
  profileId: string;
  market: PlatformMarket;
  name: string;
  version: number;
  filters: Record<string, unknown>;
  sourceKind: "MARKET" | "PRESET" | "CUSTOM";
  presetId?: string | null;
  symbols: string[];
  createdAt?: string | null;
};
export type WatchlistProfile = {
  profileId: string;
  market: PlatformMarket;
  name: string;
  versions: WatchlistProfileVersion[];
};
export type WatchlistProfilesResponse = { profiles: WatchlistProfile[] };

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
  /** Exact settings accepted by POST /v2/backtests `execution`. */
  executionSchema?: ConfigSchema | null;
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
export type StrategySignalSource = "OPENDELTA" | "TRADINGVIEW";
export type StrategyDeployment = {
  deploymentId?: string | null;
  strategySourceId?: string | null;
  market: PlatformMarket;
  strategyId: string;
  strategyVersion: string;
  configId?: string | null;
  universeId?: string | null;
  timeframe: string;
  mode: StrategyDeploymentMode;
  signalSource: StrategySignalSource;
  source: "DATABASE" | "ENVIRONMENT" | "DEFAULT";
  createdAt?: string | null;
  updatedAt?: string | null;
};
export type StrategyDeploymentsResponse = { deployments: StrategyDeployment[] };
export type TradingViewStatus = { configured: boolean; ready: boolean; paperOnly: true; deployment?: StrategyDeployment | null };
export type TradingViewEvent = {
  webhookEventId: string; eventId?: string | null; market: PlatformMarket; strategyId?: string | null;
  strategyVersion?: string | null; symbol?: string | null; timeframe?: string | null; action?: string | null;
  accepted: boolean; duplicate: boolean; mode?: string | null; signalId?: string | null; statusCode: number;
  reason?: string | null; durationMs: number; receivedAt: string;
};
export type TradingViewActivityResponse = { events: TradingViewEvent[] };
export type TradingViewTestResult = { safe: true; ready: boolean; checks: Record<string, boolean>; resolvedSymbol?: string | null; message: string };
export type BacktestApproval = { approvalId: string; runId: string; mode: "SIGNALS" | "PAPER"; configId: string; universeId: string; strategySourceId?: string | null; signalSource: StrategySignalSource; approvedAt: string };

export type StrategySourceManifest = {
  strategyId: string;
  name: string;
  version: string;
  description: string;
  supportedMarkets: PlatformMarket[];
  supportedTimeframes: string[];
  parameters: ConfigValues;
  requiredHistory?: number;
};
export type StrategySourceValidation = {
  valid: boolean;
  errors: string[];
  warnings: string[];
  manifest: StrategySourceManifest | null;
  codeHash: string;
};
export type StrategySource = {
  sourceId: string;
  strategyId: string;
  strategyVersion: string;
  name: string;
  description?: string;
  codeHash: string;
  manifest: StrategySourceManifest;
  validation: StrategySourceValidation;
  status: "VALIDATED" | "ARCHIVED";
  createdAt?: string | null;
  sourceCode?: string;
};
export type StrategySourcesResponse = { sources: StrategySource[] };
export type StrategySourceTemplate = { sourceCode: string };

export type IndicatorOutput = {
  name: string;
  label: string;
  display: "LINE" | "HISTOGRAM" | "BAND" | "POINTS";
  pane: "OVERLAY" | "PANEL";
};
export type IndicatorSourceManifest = {
  indicatorId: string;
  name: string;
  version: string;
  description: string;
  parameters: ConfigValues;
  requiredHistory: number;
  outputs: IndicatorOutput[];
};
export type IndicatorSourceValidation = {
  valid: boolean;
  errors: string[];
  warnings: string[];
  manifest: IndicatorSourceManifest | null;
  codeHash: string;
};
export type IndicatorSource = {
  sourceId: string;
  indicatorId: string;
  indicatorVersion: string;
  name: string;
  description?: string;
  codeHash: string;
  manifest: IndicatorSourceManifest;
  validation: IndicatorSourceValidation;
  status: "VALIDATED" | "ARCHIVED";
  createdAt?: string | null;
  archivedAt?: string | null;
  sourceCode?: string;
};
export type IndicatorSourcesResponse = { sources: IndicatorSource[] };
export type IndicatorSourceTemplate = { sourceCode: string };
export type IndicatorPreview = {
  outputs: IndicatorOutput[];
  rows: Array<Record<string, number | null>>;
  candles: { timestamp: string[]; open: number[]; high: number[]; low: number[]; close: number[]; volume: number[] };
};

export type BacktestMetrics = {
  totalSignals?: number | null;
  completedTrades?: number | null;
  targetHits?: number | null;
  stoppedTrades?: number | null;
  expiredTrades?: number | null;
  openTrades?: number | null;
  realizedPnl?: number | null;
  profitableTrades?: number | null;
  losingTrades?: number | null;
  breakevenTrades?: number | null;
  grossProfit?: number | null;
  grossLoss?: number | null;
  profitFactor?: number | null;
  unrealizedPnl?: number | null;
  fees?: number | null;
  slippage?: number | null;
  exposureMinutes?: number | null;
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
  strategySourceId?: string | null;
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
  signalPrice?: number | null;
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
export type BacktestChartResponse = {
  run: BacktestRun;
  symbol: string;
  candles: { timestamp: string[]; open: number[]; high: number[]; low: number[]; close: number[]; volume: number[] };
  trades: BacktestTrade[];
  indicator: null | { sourceId: string; name: string; version: string; outputs: IndicatorOutput[]; rows: Array<Record<string, number | null>> };
};

export type ResearchGenerationMode = "MANUAL" | "GRID";
export type ResearchParameterDefinition = {
  section: "strategy" | "execution";
  parameter: string;
  type: "number" | "integer" | "boolean" | "enum";
  method: "EXPLICIT_VALUES" | "NUMERIC_RANGE" | "FIXED";
  values?: unknown[];
  minimum?: number;
  maximum?: number;
  step?: number;
  value?: unknown;
};
export type ResearchPreviewVariant = { name: string; configuration: ConfigValues; execution: ConfigValues };
export type ResearchExperimentPreview = {
  previewHash: string; name: string; mode: ResearchGenerationMode; strategyId: string; strategyVersion: string;
  strategySourceId?: string | null; market: PlatformMarket; timeframe: string; universeId?: string | null;
  universePresetId?: string | null; universeName: string; symbols: string[]; startDate: string; endDate: string;
  sweepDefinitions: ResearchParameterDefinition[]; parameterCount: number; variantCount: number; symbolCount: number;
  estimatedSymbolRuns: number; variants: ResearchPreviewVariant[]; warnings: string[];
};
export type ResearchVariant = { variantId: string; position: number; name: string; configuration: ConfigValues; execution: ConfigValues; run: BacktestRun };
export type ResearchExperiment = {
  experimentId: string; name: string; mode: ResearchGenerationMode; market: PlatformMarket;
  strategyId: string; strategyVersion: string; strategySourceId?: string | null; timeframe: string;
  universeId?: string | null; universeName?: string | null; symbols: string[]; startDate: string; endDate: string;
  sweepDefinitions: ResearchParameterDefinition[]; previewHash: string; variantCount: number; symbolCount: number;
  estimatedSymbolRuns: number; status: string; variantStatusCounts: Record<string, number>;
  progress: { variantsTotal: number; symbolsTotal: number; symbolsCompleted: number };
  variants: ResearchVariant[]; createdAt: string;
};
export type ResearchExperimentsResponse = { experiments: ResearchExperiment[] };

export type WalkForwardMode = "ANCHORED" | "ROLLING";
export type WalkForwardRanking = "NET_PNL" | "RETURN_DRAWDOWN" | "LOWEST_DRAWDOWN" | "HIGHEST_WIN_RATE";
export type WalkForwardFoldPreview = {
  position: number; trainingStart: string; trainingEnd: string; testingStart: string; testingEnd: string;
  trainingSessions: number; testingSessions: number;
};
export type WalkForwardPreview = {
  previewHash: string; name: string; mode: WalkForwardMode; market: PlatformMarket;
  strategyId: string; strategyVersion: string; strategySourceId?: string | null; timeframe: string;
  universeId?: string | null; universeName: string; symbols: string[];
  overallStartDate: string; overallEndDate: string; trainingWindow: number; testingWindow: number;
  step: number; maximumFolds: number; candidateExperimentId: string; rankingObjective: WalkForwardRanking;
  minimumRequiredTrades: number; transactionCostBps: number; slippageBps: number;
  foldCount: number; candidateCount: number; childRunCount: number; symbolCount: number;
  estimatedSymbolRuns: number; estimatedCandleWorkload: number; folds: WalkForwardFoldPreview[];
  candidates: Array<{ variantId: string; position: number; name: string; configuration: ConfigValues; execution: ConfigValues }>;
  warnings: string[];
};
export type WalkForwardFold = WalkForwardFoldPreview & {
  foldId: string; status: string; error?: string | null; selectedVariantId?: string | null;
  selectedCandidateName?: string | null; selectedConfiguration?: ConfigValues | null;
  selectedExecution?: ConfigValues | null; trainingRank?: number | null;
  trainingCandidates: ResearchVariant[]; testRun?: BacktestRun | null; completedAt?: string | null;
};
export type WalkForwardValidation = Omit<WalkForwardPreview, "folds" | "candidates" | "warnings"> & {
  validationId: string; cancelRequested: boolean; status: string;
  foldStatusCounts: Record<string, number>; childRunStatusCounts: Record<string, number>;
  aggregateUnseenMetrics?: BacktestMetrics & { netPnl?: number; returnDrawdownScore?: number; failedSymbols?: number } | null;
  folds: WalkForwardFold[]; createdAt: string; completedAt?: string | null;
};
export type WalkForwardValidationsResponse = { validations: WalkForwardValidation[] };

export type AICopilotStatus = {
  configured: boolean; message: string; provider?: string | null; model?: string | null;
  safetyMode: "RESEARCH_DRAFT_ONLY";
};
export type AICopilotAction =
  | "EXPLAIN_STRATEGY" | "EXPLAIN_INDICATOR" | "EXPLAIN_BACKTEST" | "SUGGEST_IMPROVEMENTS"
  | "SUGGEST_EXPERIMENT" | "EXPLAIN_COMPARISON" | "EXPLAIN_WALK_FORWARD"
  | "DRAFT_STRATEGY" | "DRAFT_INDICATOR" | "DRAFT_CONFIGURATION";
export type AICopilotResponse = {
  requestId: string; label: string; action: AICopilotAction; content: string;
  provider: string; model: string; usage: Record<string, number>; contextCategories: string[];
  suggestedDraftType: "STRATEGY" | "INDICATOR" | "CONFIGURATION" | "NOTE";
};
export type AIResearchDraft = {
  draftId: string; requestId: string; draftType: AICopilotResponse["suggestedDraftType"];
  content: string; status: "DRAFT"; createdAt?: string;
};

export type ExchangePermissionReport = {
  authenticated?: boolean;
  read?: boolean;
  trade?: boolean;
  withdrawal?: boolean | null;
  ipAllowlisted?: boolean | null;
  environment?: "LIVE" | "DEMO";
  accountStatus?: string;
};

export type ExchangeConnection = {
  connectionId: string;
  provider: "OKX" | "VALR";
  label: string;
  environment: "LIVE" | "DEMO";
  configured: true;
  maskedKeyIdentifier: string;
  disabled: boolean;
  status: "NOT_TESTED" | "CONNECTED" | "FAILED" | "WITHDRAWAL_PERMISSION" | "DISABLED";
  permissions: ExchangePermissionReport;
  lastTestSuccess: boolean | null;
  lastTestMessage: string | null;
  lastTestedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type PlatformConnection = {
  provider: "DHAN";
  managedBy: "deployment";
  configured: boolean;
  status: "CONFIGURED" | "NOT_CONFIGURED";
  message: string;
};

export type ExchangeConnectionsResponse = {
  encryptionConfigured: boolean;
  connections: ExchangeConnection[];
  platformConnections: PlatformConnection[];
  publicMarketData: Record<string, { available: boolean; requiresPrivateConnection: boolean }>;
};

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
export type LifecycleStage = {
  status: string;
  message: string;
  timestamp?: string | null;
};
export type StrategyLifecycle = {
  strategyId: string;
  strategyVersion?: string | null;
  timeframe: string;
  mode: "SIGNALS" | "PAPER" | string;
  signalSource?: StrategySignalSource | null;
  workerStatus?: string | null;
  connectionStatus?: string | null;
  cycle?: {
    cycleId?: string | null;
    status: string;
    startedAt?: string | null;
    completedAt?: string | null;
    nextCheckAt?: string | null;
    symbolsRequested?: number | null;
    symbolsDownloaded?: number | null;
    symbolsEvaluated?: number | null;
    signalsCreated?: number | null;
    failures?: number | null;
    lastSignalId?: string | null;
    lastSignalSymbol?: string | null;
    stages?: Partial<Record<"data" | "signal" | "paper", LifecycleStage>>;
  } | null;
  lastSignal?: {
    signalId?: string | null;
    symbol?: string | null;
    signalType?: string | null;
    candleTimestamp?: string | null;
    createdAt?: string | null;
  } | null;
  paper?: LifecycleStage & { symbol?: string | null; orderId?: string | null };
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
  source?: StrategySignalSource | null;
  externalEventId?: string | null;
  receivedAt?: string | null;
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
  strategyId?: string | null;
  strategyVersion?: string | null;
  timeframe?: string | null;
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
  realizedPnl?: number | null;
  maePct?: number | null;
  mfePct?: number | null;
  fees?: number | null;
};
export type PaperOrder = {
  orderId: string;
  strategyId?: string | null;
  strategyVersion?: string | null;
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
  signalEngine: Section<{ stored: EngineStatus[]; workers: WorkerStatus[]; lifecycles?: StrategyLifecycle[] }>;
  paper: Section<{ account: PaperAccount; openPositions: PaperLot[] }>;
  paperOnly?: boolean;
  liveOrdersEnabled?: boolean;
};
