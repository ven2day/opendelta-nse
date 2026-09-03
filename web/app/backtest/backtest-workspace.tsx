"use client";

import { ArrowDown, ArrowUp, ChevronsUpDown, FlaskConical, Gauge, LoaderCircle, Play, RefreshCw, SlidersHorizontal, Square, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { formatDateTime, formatInteger, formatMinutes, formatMoney, formatNumber, formatPercent, isoDate, marketLabel, shortId, tone } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { compactValues, pickValues, schemaDefaults, schemaFromValues, validateConfigValues, type ConfigSchema, type ConfigValues } from "../platform/schema-form";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Delete, v2Get, v2Post } from "../platform/v2-client";
import type { BacktestRun, BacktestRunsResponse, BacktestTradesResponse, StrategiesResponse, StrategyConfigResponse, UniversesResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, Message, PaperOnlyBadge, Panel, PnlValue, RequestErrorState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

const RUN_POLL_MS = 2_000;
const TRADES_POLL_MS = 6_000;
const RUNS_REFRESH_MS = 15_000;
const TRADES_PAGE_SIZE = 50;
const ACTIVE_STATUSES = new Set(["QUEUED", "RUNNING"]);
const DEFAULT_LOOKBACK_DAYS = 90;
const DEFAULT_TIMEFRAME = "5m";
type Notice = { kind: "success" | "error"; text: string } | null;
type TradeSort = "symbol" | "status" | "entryTimestamp" | "entryPrice" | "quantity" | "targetPrice" | "stopPrice" | "exitTimestamp" | "exitPrice" | "netPnl" | "maePct" | "holdingMinutes";
type SortDirection = "asc" | "desc";

function SortableHeading({ label, column, active, direction, numeric, onSort }: { label: string; column: TradeSort; active: boolean; direction: SortDirection; numeric?: boolean; onSort: (column: TradeSort) => void }) {
  const icon: ReactNode = active ? (direction === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />) : <ChevronsUpDown size={12} />;
  return <th className={numeric ? "numeric" : undefined} aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}><button type="button" className="quant-sort-button" onClick={() => onSort(column)}>{label}{icon}</button></th>;
}

/** The only keys the POST /v2/backtests `execution` body accepts, in display order. */
const EXECUTION_KEYS = ["targetPct", "stopLossPct", "maximumHoldingBars", "initialQuantity", "allowAdditionalBuys", "additionalQuantityPct", "additionalSizingMode", "minimumQuantity", "maximumEntriesPerCycle", "batchSize"] as const;

/** Fallback definitions for execution keys the platform `riskSchema` does not describe (or when it is absent). */
const EXECUTION_FALLBACK: ConfigSchema = {
  targetPct: { type: "number", label: "Target %", minimum: 0, description: "Optional; strategy target when empty" },
  stopLossPct: { type: "number", label: "Stop loss %", minimum: 0 },
  maximumHoldingBars: { type: "integer", label: "Maximum holding bars", minimum: 1 },
  initialQuantity: { type: "number", label: "Initial quantity", minimum: 0 },
  allowAdditionalBuys: { type: "boolean", label: "Allow additional buys" },
  additionalQuantityPct: { type: "number", label: "Additional quantity %", minimum: 0 },
  additionalSizingMode: { type: "string", label: "Additional sizing mode" },
  minimumQuantity: { type: "number", label: "Minimum quantity", minimum: 0 },
  maximumEntriesPerCycle: { type: "integer", label: "Maximum entries per cycle", minimum: 1 },
  batchSize: { type: "integer", label: "Batch size", minimum: 1, maximum: 200, default: 10, description: "Symbols processed per batch" },
};

/** Execution form schema: the published `riskSchema` (falling back to `riskDefaults`) filtered to the backtest contract. */
function executionSchemaFrom(response: StrategiesResponse | null): ConfigSchema {
  const published = response?.riskSchema ?? schemaFromValues(response?.riskDefaults ?? {});
  return Object.fromEntries(EXECUTION_KEYS.map((key) => [key, published[key] ?? EXECUTION_FALLBACK[key]]));
}

function parseSymbols(text: string): string[] {
  return Array.from(new Set(text.split(/[\s,;]+/).map((symbol) => symbol.trim().toUpperCase()).filter(Boolean)));
}

type BacktestConfiguration = { strategy: ConfigValues; execution: ConfigValues };

function isObject(value: unknown): value is ConfigValues {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatConfigurationJson(configuration: ConfigValues, execution: ConfigValues): string {
  return JSON.stringify({ strategy: compactValues(configuration), execution: pickValues(compactValues(execution), EXECUTION_KEYS) }, null, 2);
}

/** Accept one explicit JSON envelope; defaults are used when the editor has not been changed. */
function parseConfigurationJson(text: string, strategySchema: ConfigSchema, executionSchema: ConfigSchema): BacktestConfiguration {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("Strategy configuration is not valid JSON.");
  }
  if (!isObject(parsed)) throw new Error("Strategy configuration must be a JSON object.");
  const unknownKeys = Object.keys(parsed).filter((key) => key !== "strategy" && key !== "execution");
  if (unknownKeys.length) throw new Error(`Unknown configuration section: ${unknownKeys.join(", ")}. Use only strategy and execution.`);
  if (parsed.strategy !== undefined && !isObject(parsed.strategy)) throw new Error("strategy must be a JSON object.");
  if (parsed.execution !== undefined && !isObject(parsed.execution)) throw new Error("execution must be a JSON object.");
  const configuration = {
    strategy: (parsed.strategy as ConfigValues | undefined) ?? {},
    execution: (parsed.execution as ConfigValues | undefined) ?? {},
  };
  validateConfigValues(configuration.strategy, strategySchema, "strategy");
  validateConfigValues(configuration.execution, executionSchema, "execution");
  return configuration;
}

function isActive(status: string | undefined): boolean {
  return Boolean(status && ACTIVE_STATUSES.has(status.toUpperCase()));
}

/** Poll the selected run every two seconds only while it is queued or running. */
function pollWhileActive(run: BacktestRun | null): number | undefined {
  return isActive(run?.status) ? RUN_POLL_MS : undefined;
}

function progressPct(run: BacktestRun | null): number {
  if (!run || !run.symbolsTotal) return run?.status === "COMPLETE" ? 100 : 0;
  return Math.min(100, Math.round(((run.symbolsCompleted ?? 0) / run.symbolsTotal) * 100));
}

export function BacktestWorkspace({ market }: { market: PlatformMarket }) {
  const loadStrategies = useCallback(() => v2Get<StrategiesResponse>("strategies", { market }), [market]);
  const loadUniverses = useCallback(() => v2Get<UniversesResponse>("screener/universes", { market }), [market]);
  const loadRuns = useCallback(() => v2Get<BacktestRunsResponse>("backtests", { market, limit: 20 }), [market]);
  const strategies = useV2Resource(loadStrategies);
  const universes = useV2Resource(loadUniverses);
  const runs = useV2Resource(loadRuns, RUNS_REFRESH_MS);
  const { refresh: refreshRuns } = runs;

  const [strategyChoice, setStrategyChoice] = useState<string | null>(null);
  const [timeframeChoice, setTimeframeChoice] = useState<string | null>(null);
  const [symbolSource, setSymbolSource] = useState<"universe" | "custom">("universe");
  const [customSymbols, setCustomSymbols] = useState("");
  const [startDate, setStartDate] = useState(() => isoDate(new Date(Date.now() - DEFAULT_LOOKBACK_DAYS * 86_400_000)));
  const [endDate, setEndDate] = useState(() => isoDate(new Date()));
  const [configurationJsonEdits, setConfigurationJsonEdits] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [selectedRunChoice, setSelectedRunChoice] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [tradeSymbolInput, setTradeSymbolInput] = useState("");
  const [tradeSymbol, setTradeSymbol] = useState("");
  const [tradeStatus, setTradeStatus] = useState("");
  const [tradeSort, setTradeSort] = useState<TradeSort>("entryTimestamp");
  const [tradeDirection, setTradeDirection] = useState<SortDirection>("asc");
  const [tradeOffset, setTradeOffset] = useState(0);

  const marketStrategies = useMemo(() => (strategies.data?.strategies ?? []).filter((strategy) => !strategy.supportedMarkets?.length || strategy.supportedMarkets.includes(market)), [strategies.data, market]);
  const strategy = marketStrategies.find((item) => item.strategyId === strategyChoice) ?? marketStrategies[0] ?? null;
  const strategyId = strategy?.strategyId ?? null;
  const loadConfig = useCallback(() => (strategyId ? v2Get<StrategyConfigResponse>(`strategies/${strategyId}/config`, { market }) : Promise.resolve(null)), [strategyId, market]);
  const config = useV2Resource(loadConfig);

  const configKey = `${market}:${strategyId ?? ""}`;
  const configuration = strategy ? schemaDefaults(strategy.configSchema, config.data?.effectiveConfiguration, strategy.defaults) : {};
  const executionSchema = useMemo(() => executionSchemaFrom(strategies.data), [strategies.data]);
  const execution = schemaDefaults(executionSchema, config.data?.effectiveRiskSettings, strategies.data?.riskDefaults);
  const defaultConfigurationJson = formatConfigurationJson(configuration, execution);
  const configurationJson = configurationJsonEdits[configKey] ?? defaultConfigurationJson;
  const hasConfigurationOverride = configurationJsonEdits[configKey] !== undefined;
  const timeframes = strategy?.supportedTimeframes?.length ? strategy.supportedTimeframes : [DEFAULT_TIMEFRAME];
  const timeframe = timeframeChoice && timeframes.includes(timeframeChoice) ? timeframeChoice : timeframes.includes(DEFAULT_TIMEFRAME) ? DEFAULT_TIMEFRAME : timeframes[0];
  const activeUniverse = universes.data?.active?.[market] ?? universes.data?.universes.find((universe) => universe.active) ?? null;
  const symbols = symbolSource === "custom" ? parseSymbols(customSymbols) : activeUniverse?.symbols ?? [];

  const selectedRunId = selectedRunChoice ?? runs.data?.runs[0]?.runId ?? null;
  const listedRun = runs.data?.runs.find((run) => run.runId === selectedRunId) ?? null;
  const loadRun = useCallback((): Promise<BacktestRun | null> => (selectedRunId ? v2Get<BacktestRun>(`backtests/${selectedRunId}`) : Promise.resolve(null)), [selectedRunId]);
  const runDetail = useV2Resource<BacktestRun | null>(loadRun, pollWhileActive);
  const run = runDetail.data ?? listedRun;
  const runActive = isActive(run?.status);
  const { refresh: refreshRun } = runDetail;

  const tradesRunId = run && run.status !== "QUEUED" ? run.runId : null;
  const loadTrades = useCallback(() => (tradesRunId ? v2Get<BacktestTradesResponse>(`backtests/${tradesRunId}/trades`, { symbol: tradeSymbol || undefined, status: tradeStatus || undefined, sort: tradeSort, direction: tradeDirection, limit: TRADES_PAGE_SIZE, offset: tradeOffset }) : Promise.resolve(null)), [tradesRunId, tradeSymbol, tradeStatus, tradeSort, tradeDirection, tradeOffset]);
  // Polls while the run is active; the policy change on completion triggers one final re-fetch.
  const trades = useV2Resource(loadTrades, runActive ? TRADES_POLL_MS : undefined);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setTradeSymbol(tradeSymbolInput.trim().toUpperCase());
      setTradeOffset(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [tradeSymbolInput]);

  const selectRun = (runId: string) => {
    setSelectedRunChoice(runId);
    setTradeOffset(0);
    setTradeSymbol("");
    setTradeSymbolInput("");
    setTradeStatus("");
  };

  const sortTrades = (column: TradeSort) => {
    setTradeDirection((current) => column === tradeSort ? (current === "asc" ? "desc" : "asc") : "asc");
    setTradeSort(column);
    setTradeOffset(0);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!strategy) return;
    setSubmitting(true);
    setNotice(null);
    try {
      if (!symbols.length) throw new Error(symbolSource === "custom" ? "Enter at least one symbol." : "No active universe; save one in the Screener or enter symbols manually.");
      if (!startDate || !endDate || startDate > endDate) throw new Error("Choose a start date on or before the end date.");
      const defaults = { strategy: compactValues(configuration), execution: pickValues(compactValues(execution), EXECUTION_KEYS) };
      const overrides = hasConfigurationOverride ? parseConfigurationJson(configurationJson, strategy.configSchema, executionSchema) : { strategy: {}, execution: {} };
      const resolvedConfiguration = {
        strategy: { ...defaults.strategy, ...overrides.strategy },
        execution: { ...defaults.execution, ...overrides.execution },
      };
      const created = await v2Post<BacktestRun>("backtests", {
        market,
        strategyId: strategy.strategyId,
        symbols,
        timeframe,
        startDate,
        endDate,
        configuration: compactValues(resolvedConfiguration.strategy),
        execution: pickValues(compactValues(resolvedConfiguration.execution), EXECUTION_KEYS),
      });
      setNotice({ kind: "success", text: `Backtest ${shortId(created.runId)} queued for ${symbols.length} symbols.` });
      selectRun(created.runId);
      refreshRuns();
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "The backtest could not be started") });
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = async () => {
    if (!run || !window.confirm(`Cancel backtest ${shortId(run.runId)}?`)) return;
    setCancelling(true);
    try {
      await v2Delete(`backtests/${run.runId}`);
      refreshRun();
      refreshRuns();
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "The backtest could not be cancelled") });
    } finally {
      setCancelling(false);
    }
  };

  const metrics = run?.metrics ?? null;
  const total = trades.data?.total ?? 0;
  const pageStart = trades.data ? trades.data.offset + 1 : 0;
  const pageEnd = trades.data ? Math.min(trades.data.offset + trades.data.trades.length, total) : 0;

  return <main className="quant-workspace">
    <WorkspaceHeader
      eyebrow={`${marketLabel(market)} backtest`}
      title="Strategy backtest"
      actions={<div className="quant-header-actions"><PaperOnlyBadge /><button type="button" onClick={() => { refreshRuns(); refreshRun(); }}><RefreshCw size={15} />Refresh</button></div>}
    />

    <Panel icon={<FlaskConical size={17} />} title="Run backtest">
      {strategies.loading || universes.loading ? <LoadingState label="Loading strategies and universes" /> : strategies.error ? <RequestErrorState error={strategies.error} retry={strategies.reload} /> : !strategy ? <EmptyState title="No strategies for this market" description={`No registered strategy supports ${marketLabel(market)}.`} /> : <form onSubmit={submit} noValidate>
        <div className="quant-panel-body">
          <div className="quant-form-grid quant-backtest-run-grid">
            <label className="strategy"><span>Strategy</span><select value={strategy.strategyId} onChange={(event) => setStrategyChoice(event.target.value)}>{marketStrategies.map((item) => <option key={item.strategyId} value={item.strategyId}>{item.name} · v{item.version}</option>)}</select></label>
            <label><span>Timeframe</span><select value={timeframe} onChange={(event) => setTimeframeChoice(event.target.value)}>{timeframes.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
            <label><span>Start date</span><input type="date" value={startDate} max={endDate} onChange={(event) => setStartDate(event.target.value)} /></label>
            <label><span>End date</span><input type="date" value={endDate} min={startDate} onChange={(event) => setEndDate(event.target.value)} /></label>
            <label className="source"><span>Universe</span><select value={symbolSource} onChange={(event) => setSymbolSource(event.target.value as "universe" | "custom")}><option value="universe">{activeUniverse ? `${activeUniverse.name} · ${activeUniverse.symbols.length} symbols` : "No active universe"}</option><option value="custom">Custom symbol list</option></select></label>
            <div className="quant-backtest-run-action"><span>Action</span><button type="submit" className="primary" disabled={submitting || config.loading}>{submitting ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}{submitting ? "Starting…" : "Run backtest"}</button></div>
          </div>
          {universes.error && <p className="quant-inline-note">Universes unavailable: {universes.error.message}</p>}
          {symbolSource === "custom" && <label className="quant-backtest-custom-symbols"><span>Custom symbols</span><input value={customSymbols} placeholder="RELIANCE, TCS, INFY" onChange={(event) => setCustomSymbols(event.target.value)} /><small>{symbols.length} symbols selected</small></label>}
          <details className="quant-backtest-config">
            <summary><span><SlidersHorizontal size={14} />Strategy configuration</span><StatusBadge tone={hasConfigurationOverride ? "warn" : "neutral"}>{hasConfigurationOverride ? "Custom JSON" : "Defaults"}</StatusBadge></summary>
            <div className="quant-backtest-config-body">
              <div><strong>{config.data?.active ? `Active configuration: ${config.data.active.name}` : "Published defaults"}</strong><p>Edit only when this run needs different strategy or execution values. The exact JSON is stored with the result.</p></div>
              {config.loading ? <LoadingState label="Loading strategy defaults" /> : <label><span>JSON override</span><textarea aria-label="Backtest configuration JSON" spellCheck={false} value={configurationJson} disabled={submitting} onChange={(event) => setConfigurationJsonEdits((current) => ({ ...current, [configKey]: event.target.value }))} /></label>}
              <div className="quant-backtest-config-actions">
                <button type="button" onClick={() => { try { const parsed = parseConfigurationJson(configurationJson, strategy.configSchema, executionSchema); setConfigurationJsonEdits((current) => ({ ...current, [configKey]: JSON.stringify(parsed, null, 2) })); setNotice(null); } catch (reason) { setNotice({ kind: "error", text: errorMessage(reason, "Invalid strategy configuration") }); } }}>Format JSON</button>
                <button type="button" onClick={() => setConfigurationJsonEdits((current) => { const next = { ...current }; delete next[configKey]; return next; })}>Use defaults</button>
              </div>
            </div>
          </details>
          {notice && <Message kind={notice.kind}>{notice.text}</Message>}
        </div>
      </form>}
    </Panel>

    <Panel icon={<Gauge size={17} />} title={run ? `Run ${shortId(run.runId)}` : "Run progress"} description={run ? `${run.strategyId} ${run.strategyVersion ? `v${run.strategyVersion}` : ""} · ${run.timeframe} · ${run.startDate} → ${run.endDate}` : "Select a run from the list below or start a new one."} aside={run && <div className="quant-toolbar"><StatusBadge tone={tone(run.status)}>{run.status}</StatusBadge>{runActive && <button type="button" className="danger" disabled={cancelling || run.cancelRequested} onClick={() => void cancel()}><Square size={13} />{run.cancelRequested ? "Cancelling…" : cancelling ? "Cancelling…" : "Cancel"}</button>}</div>}>
      {!selectedRunId ? <EmptyState title="No backtests yet" description="Start a backtest above; progress and metrics appear here." /> : runDetail.error ? <RequestErrorState error={runDetail.error} retry={runDetail.reload} /> : !run ? <LoadingState label="Loading run" /> : <>
        <div className="quant-progress-row">
          <div className="quant-progress"><span style={{ width: `${progressPct(run)}%` }} /></div>
          <span>{formatInteger(run.symbolsCompleted ?? 0)} / {formatInteger(run.symbolsTotal ?? run.symbols.length)} symbols{run.currentSymbol ? ` · ${run.currentSymbol}` : ""}</span>
          <span>Created {formatDateTime(run.createdAt, market)}{run.completedAt ? ` · finished ${formatDateTime(run.completedAt, market)}` : ""}</span>
        </div>
        {run.error && <div className="quant-panel-body"><Message kind="error">{run.error}</Message></div>}
        {metrics ? <div className="quant-panel-body"><section className="quant-kpi-grid dense">
          <article><span>Realized PnL</span><strong><PnlValue value={metrics.realizedPnl} market={market} /></strong><small>Unrealized <PnlValue value={metrics.unrealizedPnl} market={market} /></small></article>
          <article><span>Win rate</span><strong>{metrics.winRate != null ? formatPercent(metrics.winRate, 1) : "—"}</strong><small>{formatInteger(metrics.completedTrades)} completed trades</small></article>
          <article><span>Signals</span><strong>{formatInteger(metrics.totalSignals)}</strong><small>{formatInteger(metrics.openTrades)} still open</small></article>
          <article><span>Targets / stops / expiries</span><strong>{formatInteger(metrics.targetHits)} / {formatInteger(metrics.stoppedTrades)} / {formatInteger(metrics.expiredTrades)}</strong></article>
          <article><span>Max drawdown</span><strong>{formatMoney(metrics.maximumDrawdown, market)}</strong></article>
          <article><span>Costs</span><strong>{formatMoney((metrics.fees ?? 0) + (metrics.slippage ?? 0), market)}</strong><small>Fees {formatMoney(metrics.fees, market)} · slippage {formatMoney(metrics.slippage, market)}</small></article>
          <article><span>MAE / MFE</span><strong>{formatPercent(metrics.averageMaePct)} / {formatPercent(metrics.averageMfePct)}</strong><small>Average adverse / favourable excursion</small></article>
          <article><span>Holding</span><strong>{formatMinutes(metrics.averageHoldingMinutes)}</strong><small>Median {formatMinutes(metrics.medianHoldingMinutes)}</small></article>
          <article><span>Symbols</span><strong>{formatInteger(metrics.symbolsProcessed)}</strong><small>{formatInteger(metrics.symbolsFailed)} failed</small></article>
        </section></div> : <div className="quant-panel-body"><p className="quant-inline-note">{runActive ? "Metrics are published when the run completes." : "No metrics were recorded for this run."}</p></div>}
        {run.failedSymbols && run.failedSymbols.length > 0 && <div className="quant-panel-body"><details className="quant-details"><summary>{formatInteger(run.failedSymbols.length)} failed symbols</summary><div className="quant-table-scroll"><table className="quant-table"><thead><tr><th>Symbol</th><th>Message</th></tr></thead><tbody>{run.failedSymbols.map((item) => <tr key={item.symbol}><td><strong>{item.symbol}</strong></td><td>{item.message}</td></tr>)}</tbody></table></div></details></div>}
      </>}
    </Panel>

    {tradesRunId && <Panel icon={<FlaskConical size={17} />} title="Trades" aside={(tradeSymbolInput || tradeStatus) && <button type="button" className="quant-icon-action" onClick={() => { setTradeSymbolInput(""); setTradeStatus(""); setTradeOffset(0); }}><X size={13} />Clear filters</button>}>
      {trades.loading ? <LoadingState label="Loading trades" /> : trades.error ? <RequestErrorState error={trades.error} retry={trades.reload} /> : !trades.data?.trades.length ? <EmptyState title="No trades yet" description={runActive ? "Trades appear as symbols complete." : "This run produced no trades for the selected filter."} /> : <>
        <div className="quant-table-scroll tall"><table className="quant-table">
          <thead>
            <tr className="quant-sort-row">
              <SortableHeading label="Symbol" column="symbol" active={tradeSort === "symbol"} direction={tradeDirection} onSort={sortTrades} />
              <SortableHeading label="Status" column="status" active={tradeSort === "status"} direction={tradeDirection} onSort={sortTrades} />
              <SortableHeading label="Entry" column="entryTimestamp" active={tradeSort === "entryTimestamp"} direction={tradeDirection} onSort={sortTrades} />
              <SortableHeading label="Entry price" column="entryPrice" active={tradeSort === "entryPrice"} direction={tradeDirection} numeric onSort={sortTrades} />
              <SortableHeading label="Qty" column="quantity" active={tradeSort === "quantity"} direction={tradeDirection} numeric onSort={sortTrades} />
              <SortableHeading label="Target" column="targetPrice" active={tradeSort === "targetPrice"} direction={tradeDirection} numeric onSort={sortTrades} />
              <SortableHeading label="Stop" column="stopPrice" active={tradeSort === "stopPrice"} direction={tradeDirection} numeric onSort={sortTrades} />
              <SortableHeading label="Exit" column="exitTimestamp" active={tradeSort === "exitTimestamp"} direction={tradeDirection} onSort={sortTrades} />
              <SortableHeading label="Exit price" column="exitPrice" active={tradeSort === "exitPrice"} direction={tradeDirection} numeric onSort={sortTrades} />
              <SortableHeading label="Net PnL" column="netPnl" active={tradeSort === "netPnl"} direction={tradeDirection} numeric onSort={sortTrades} />
              <SortableHeading label="MAE / MFE" column="maePct" active={tradeSort === "maePct"} direction={tradeDirection} numeric onSort={sortTrades} />
              <SortableHeading label="Holding" column="holdingMinutes" active={tradeSort === "holdingMinutes"} direction={tradeDirection} numeric onSort={sortTrades} />
            </tr>
            <tr className="quant-filter-row">
              <th><input aria-label="Filter trades by symbol" type="search" value={tradeSymbolInput} placeholder="Filter…" onChange={(event) => setTradeSymbolInput(event.target.value)} /></th>
              <th><select aria-label="Filter trades by status" value={tradeStatus} onChange={(event) => { setTradeStatus(event.target.value); setTradeOffset(0); }}><option value="">All</option><option value="OPEN">Open</option><option value="TARGET_HIT">Target hit</option><option value="STOPPED">Stopped</option><option value="EXPIRED">Expired</option></select></th>
              <th colSpan={10}></th>
            </tr>
          </thead>
          <tbody>{trades.data.trades.map((trade) => <tr key={trade.lotId}>
            <td><strong>{trade.symbol}</strong><small>Lot {trade.lotNumber ?? "—"} · {shortId(trade.cycleId)}</small></td>
            <td><StatusBadge tone={tone(trade.status)}>{trade.status}</StatusBadge></td>
            <td>{formatDateTime(trade.entryTimestamp, market)}</td>
            <td className="numeric">{formatNumber(trade.entryPrice)}</td>
            <td className="numeric">{formatNumber(trade.quantity, 4)}</td>
            <td className="numeric">{formatNumber(trade.targetPrice)}</td>
            <td className="numeric">{formatNumber(trade.stopPrice)}</td>
            <td>{formatDateTime(trade.exitTimestamp, market)}</td>
            <td className="numeric">{formatNumber(trade.exitPrice)}</td>
            <td className="numeric"><PnlValue value={trade.netPnl ?? trade.unrealizedPnl} market={market} /></td>
            <td className="numeric">{formatPercent(trade.maePct)} / {formatPercent(trade.mfePct)}</td>
            <td className="numeric">{formatMinutes(trade.holdingMinutes)}</td>
          </tr>)}</tbody>
        </table></div>
        <div className="quant-form-actions"><div className="quant-pager"><button type="button" disabled={tradeOffset === 0} onClick={() => setTradeOffset(Math.max(0, tradeOffset - TRADES_PAGE_SIZE))}>Previous</button><button type="button" disabled={pageEnd >= total} onClick={() => setTradeOffset(tradeOffset + TRADES_PAGE_SIZE)}>Next</button></div><span>{pageStart}–{pageEnd} of {formatInteger(total)} trades</span></div>
      </>}
    </Panel>}

    <Panel icon={<Gauge size={17} />} title="Recent runs" description={`Latest ${marketLabel(market)} backtests; select one to inspect its progress, metrics and trades.`}>
      {runs.loading ? <LoadingState label="Loading recent runs" /> : runs.error ? <RequestErrorState error={runs.error} retry={runs.reload} /> : !runs.data?.runs.length ? <EmptyState title="No runs recorded" description="Completed and in-flight backtests are listed here." /> : <div className="quant-table-scroll"><table className="quant-table">
        <thead><tr><th>Run</th><th>Strategy</th><th>Status</th><th>Progress</th><th>Range</th><th className="numeric">Realized PnL</th><th className="numeric">Win rate</th><th>Created</th><th></th></tr></thead>
        <tbody>{runs.data.runs.map((item) => <tr key={item.runId} className={item.runId === selectedRunId ? "active" : ""}>
          <td className="mono">{shortId(item.runId)}</td>
          <td><strong>{item.strategyId}</strong><small>{[item.strategyVersion, item.timeframe].filter(Boolean).join(" · ")}</small></td>
          <td><StatusBadge tone={tone(item.status)}>{item.status}</StatusBadge></td>
          <td>{formatInteger(item.symbolsCompleted ?? 0)} / {formatInteger(item.symbolsTotal ?? item.symbols.length)}</td>
          <td>{item.startDate} → {item.endDate}</td>
          <td className="numeric"><PnlValue value={item.metrics?.realizedPnl} market={market} /></td>
          <td className="numeric">{item.metrics?.winRate != null ? formatPercent(item.metrics.winRate, 1) : "—"}</td>
          <td>{formatDateTime(item.createdAt, market)}</td>
          <td><button type="button" disabled={item.runId === selectedRunId} onClick={() => selectRun(item.runId)}>{item.runId === selectedRunId ? "Selected" : "View"}</button></td>
        </tr>)}</tbody>
      </table></div>}
    </Panel>
  </main>;
}
