"use client";

import { FlaskConical, Gauge, LoaderCircle, Play, RefreshCw, SlidersHorizontal, Square } from "lucide-react";
import { useCallback, useMemo, useState, type FormEvent } from "react";
import { formatDateTime, formatInteger, formatMinutes, formatMoney, formatNumber, formatPercent, isoDate, marketLabel, shortId, tone } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { compactValues, pickValues, schemaDefaults, schemaFromValues, SchemaForm, type ConfigSchema, type ConfigValues } from "../platform/schema-form";
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
  const [configEdits, setConfigEdits] = useState<Record<string, ConfigValues>>({});
  const [executionEdits, setExecutionEdits] = useState<Record<string, ConfigValues>>({});
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [selectedRunChoice, setSelectedRunChoice] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [tradeSymbolInput, setTradeSymbolInput] = useState("");
  const [tradeSymbol, setTradeSymbol] = useState("");
  const [tradeOffset, setTradeOffset] = useState(0);

  const marketStrategies = useMemo(() => (strategies.data?.strategies ?? []).filter((strategy) => !strategy.supportedMarkets?.length || strategy.supportedMarkets.includes(market)), [strategies.data, market]);
  const strategy = marketStrategies.find((item) => item.strategyId === strategyChoice) ?? marketStrategies[0] ?? null;
  const strategyId = strategy?.strategyId ?? null;
  const loadConfig = useCallback(() => (strategyId ? v2Get<StrategyConfigResponse>(`strategies/${strategyId}/config`, { market }) : Promise.resolve(null)), [strategyId, market]);
  const config = useV2Resource(loadConfig);

  const configKey = `${market}:${strategyId ?? ""}`;
  const configuration = strategy ? (configEdits[configKey] ?? schemaDefaults(strategy.configSchema, config.data?.effectiveConfiguration, strategy.defaults)) : {};
  const executionSchema = useMemo(() => executionSchemaFrom(strategies.data), [strategies.data]);
  const execution = executionEdits[configKey] ?? schemaDefaults(executionSchema, config.data?.effectiveRiskSettings, strategies.data?.riskDefaults);
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
  const loadTrades = useCallback(() => (tradesRunId ? v2Get<BacktestTradesResponse>(`backtests/${tradesRunId}/trades`, { symbol: tradeSymbol || undefined, limit: TRADES_PAGE_SIZE, offset: tradeOffset }) : Promise.resolve(null)), [tradesRunId, tradeSymbol, tradeOffset]);
  // Polls while the run is active; the policy change on completion triggers one final re-fetch.
  const trades = useV2Resource(loadTrades, runActive ? TRADES_POLL_MS : undefined);

  const selectRun = (runId: string) => {
    setSelectedRunChoice(runId);
    setTradeOffset(0);
    setTradeSymbol("");
    setTradeSymbolInput("");
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!strategy) return;
    setSubmitting(true);
    setNotice(null);
    try {
      if (!symbols.length) throw new Error(symbolSource === "custom" ? "Enter at least one symbol." : "No active universe; save one in the Screener or enter symbols manually.");
      if (!startDate || !endDate || startDate > endDate) throw new Error("Choose a start date on or before the end date.");
      const created = await v2Post<BacktestRun>("backtests", {
        market,
        strategyId: strategy.strategyId,
        symbols,
        timeframe,
        startDate,
        endDate,
        configuration: compactValues(configuration),
        execution: pickValues(compactValues(execution), EXECUTION_KEYS),
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
      description="Run any registered strategy against the active universe with a reproducible configuration snapshot. Runs are stored on the platform database and resume-safe."
      actions={<div className="quant-header-actions"><PaperOnlyBadge /><button type="button" onClick={() => { refreshRuns(); refreshRun(); }}><RefreshCw size={15} />Refresh</button></div>}
    />

    <Panel icon={<FlaskConical size={17} />} title="New backtest" description="Strategy parameters are generated from the strategy's published schema; execution settings start from the platform risk defaults.">
      {strategies.loading || universes.loading ? <LoadingState label="Loading strategies and universes" /> : strategies.error ? <RequestErrorState error={strategies.error} retry={strategies.reload} /> : !strategy ? <EmptyState title="No strategies for this market" description={`No registered strategy supports ${marketLabel(market)}.`} /> : <form onSubmit={submit} noValidate>
        <div className="quant-panel-body">
          <div className="quant-form-grid">
            <label><span>Strategy</span><select value={strategy.strategyId} onChange={(event) => setStrategyChoice(event.target.value)}>{marketStrategies.map((item) => <option key={item.strategyId} value={item.strategyId}>{item.name} · v{item.version}</option>)}</select><small>{strategy.supportedMarkets.join(", ")}</small></label>
            <label><span>Timeframe</span><select value={timeframe} onChange={(event) => setTimeframeChoice(event.target.value)}>{timeframes.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
            <label><span>Start date</span><input type="date" value={startDate} max={endDate} onChange={(event) => setStartDate(event.target.value)} /></label>
            <label><span>End date</span><input type="date" value={endDate} min={startDate} onChange={(event) => setEndDate(event.target.value)} /></label>
            <label><span>Symbol source</span><select value={symbolSource} onChange={(event) => setSymbolSource(event.target.value as "universe" | "custom")}><option value="universe">{activeUniverse ? `Active universe · ${activeUniverse.name} (${activeUniverse.symbols.length})` : "Active universe (none saved)"}</option><option value="custom">Custom symbol list</option></select>{universes.error && <small>Universes unavailable: {universes.error.message}</small>}</label>
            <label className="span-2"><span>{symbolSource === "custom" ? "Symbols" : "Universe symbols"}</span>{symbolSource === "custom" ? <textarea value={customSymbols} placeholder="SYMBOL, SYMBOL, …" onChange={(event) => setCustomSymbols(event.target.value)} /> : <textarea value={symbols.join(", ")} readOnly placeholder="Save an active universe in the Screener" />}<small>{symbols.length} symbols selected</small></label>
          </div>
          <h3 className="quant-subheading"><SlidersHorizontal size={14} />Strategy configuration{config.data?.active ? ` · active config "${config.data.active.name}"` : ""}</h3>
          {config.loading ? <LoadingState label="Loading active configuration" /> : <SchemaForm schema={strategy.configSchema} values={configuration} disabled={submitting} onChange={(next) => setConfigEdits((current) => ({ ...current, [configKey]: next }))} />}
          <h3 className="quant-subheading"><Gauge size={14} />Execution settings</h3>
          <SchemaForm schema={executionSchema} values={execution} disabled={submitting} onChange={(next) => setExecutionEdits((current) => ({ ...current, [configKey]: next }))} />
          {notice && <Message kind={notice.kind}>{notice.text}</Message>}
        </div>
        <div className="quant-form-actions">
          <button type="submit" className="primary" disabled={submitting}>{submitting ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}{submitting ? "Starting…" : "Run backtest"}</button>
          <button type="button" onClick={() => { setConfigEdits((current) => ({ ...current, [configKey]: schemaDefaults(strategy.configSchema, config.data?.effectiveConfiguration, strategy.defaults) })); setExecutionEdits((current) => ({ ...current, [configKey]: schemaDefaults(executionSchema, config.data?.effectiveRiskSettings, strategies.data?.riskDefaults) })); }}>Reset to defaults</button>
          <span>{strategy.name} v{strategy.version} · {timeframe} · {startDate} → {endDate}</span>
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

    {tradesRunId && <Panel icon={<FlaskConical size={17} />} title="Trades" description="Lot-level results for the selected run." aside={<form className="quant-toolbar" onSubmit={(event) => { event.preventDefault(); setTradeSymbol(tradeSymbolInput.trim().toUpperCase()); setTradeOffset(0); }}><label><span>Symbol</span><input type="text" value={tradeSymbolInput} placeholder="All symbols" onChange={(event) => setTradeSymbolInput(event.target.value)} /></label><button type="submit">Filter</button></form>}>
      {trades.loading ? <LoadingState label="Loading trades" /> : trades.error ? <RequestErrorState error={trades.error} retry={trades.reload} /> : !trades.data?.trades.length ? <EmptyState title="No trades yet" description={runActive ? "Trades appear as symbols complete." : "This run produced no trades for the selected filter."} /> : <>
        <div className="quant-table-scroll tall"><table className="quant-table">
          <thead><tr><th>Symbol</th><th>Status</th><th>Entry</th><th className="numeric">Entry price</th><th className="numeric">Qty</th><th className="numeric">Target</th><th className="numeric">Stop</th><th>Exit</th><th className="numeric">Exit price</th><th className="numeric">Net PnL</th><th className="numeric">MAE / MFE</th><th className="numeric">Holding</th></tr></thead>
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
