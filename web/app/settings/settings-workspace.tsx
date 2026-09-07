"use client";

import { Beaker, Braces, Code2, Copy, Pencil, Plus, RotateCcw, Save, Settings2 } from "lucide-react";
import { useCallback, useMemo, useState, type FormEvent } from "react";
import { AICopilotPanel } from "../ai/ai-copilot-panel";
import { formatDateTime, marketLabel, shortId } from "../platform/format";
import { platformGet, platformPost, type PlatformMarket } from "../platform/platform-client";
import { compactValues, schemaDefaults, schemaFromValues, validateConfigValues, type ConfigSchema, type ConfigValues } from "../platform/schema-form";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type { StrategiesResponse, StrategyConfig, StrategyConfigResponse, StrategyDeployment, StrategyDeploymentMode, StrategyDeploymentsResponse, StrategySignalSource, StrategySource, StrategySourcesResponse, StrategySourceTemplate, StrategySourceValidation, TradingViewStatus, TradingViewTestResult, UniversesResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, Message, Panel, RequestErrorState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";
import { ExchangeConnectionsPanel } from "./exchange-connections-panel";
import styles from "./settings-workspace.module.css";

type Notice = { kind: "success" | "error"; text: string } | null;
type SettingsDocument = { strategy: ConfigValues; paperExecution: ConfigValues };
type InstrumentListResponse = { count: number };
type InstrumentAddResponse = { symbol?: string; instrument?: { providerSymbol: string } };

function isObject(value: unknown): value is ConfigValues {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseSettingsDocument(text: string, strategySchema: ConfigSchema, riskSchema: ConfigSchema): SettingsDocument {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("Configuration is not valid JSON.");
  }
  if (!isObject(parsed)) throw new Error("Configuration must be a JSON object.");
  const unknown = Object.keys(parsed).filter((key) => key !== "strategy" && key !== "paperExecution");
  if (unknown.length) throw new Error(`Unknown top-level section${unknown.length === 1 ? "" : "s"}: ${unknown.join(", ")}.`);
  if (!isObject(parsed.strategy)) throw new Error('"strategy" must be a JSON object.');
  if (!isObject(parsed.paperExecution)) throw new Error('"paperExecution" must be a JSON object.');
  const strategy = compactValues(parsed.strategy);
  const paperExecution = compactValues(parsed.paperExecution);
  validateConfigValues(strategy, strategySchema, "strategy");
  validateConfigValues(paperExecution, riskSchema, "paperExecution");
  return { strategy, paperExecution };
}

function incrementPatchVersion(source: string): string {
  return source.replace(
    /(\[?['"]version['"]\]?\s*:\s*['"])(\d+)\.(\d+)\.(\d+)(['"])/,
    (_match, prefix: string, major: string, minor: string, patch: string, suffix: string) =>
      `${prefix}${major}.${minor}.${Number(patch) + 1}${suffix}`,
  );
}

export function SettingsWorkspace({ initialMarket }: { initialMarket: PlatformMarket }) {
  const market = initialMarket;
  const [strategyChoice, setStrategyChoice] = useState<string | null>(null);
  const [jsonEdits, setJsonEdits] = useState<Record<string, string>>({});
  const [nameEdits, setNameEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [changingMode, setChangingMode] = useState(false);
  const [timeframeEdits, setTimeframeEdits] = useState<Record<string, string>>({});
  const [universeEdits, setUniverseEdits] = useState<Record<string, string>>({});
  const [sourceEdits, setSourceEdits] = useState<Record<string, StrategySignalSource>>({});
  const [notice, setNotice] = useState<Notice>(null);
  const [instrumentSymbol, setInstrumentSymbol] = useState("");
  const [addingInstrument, setAddingInstrument] = useState(false);
  const [instrumentNotice, setInstrumentNotice] = useState<Notice>(null);
  const [testingTradingView, setTestingTradingView] = useState(false);
  const [strategySource, setStrategySource] = useState("");
  const [sourceBusy, setSourceBusy] = useState<"validate" | "save" | null>(null);
  const [sourceNotice, setSourceNotice] = useState<Notice>(null);
  const [sourceValidation, setSourceValidation] = useState<StrategySourceValidation | null>(null);

  const loadStrategies = useCallback(() => v2Get<StrategiesResponse>("strategies", { market }), [market]);
  const strategies = useV2Resource(loadStrategies);
  const loadDeployments = useCallback(() => v2Get<StrategyDeploymentsResponse>("strategy-deployments", { market }), [market]);
  const deployments = useV2Resource(loadDeployments);
  const loadUniverses = useCallback(() => v2Get<UniversesResponse>("screener/universes", { market }), [market]);
  const universes = useV2Resource(loadUniverses);
  const loadInstruments = useCallback(() => platformGet<InstrumentListResponse>("instruments", { market, limit: "1" }), [market]);
  const instruments = useV2Resource(loadInstruments);
  const loadSourceTemplate = useCallback(() => v2Get<StrategySourceTemplate>("strategy-studio/template"), []);
  const sourceTemplate = useV2Resource(loadSourceTemplate);
  const loadStrategySources = useCallback(() => v2Get<StrategySourcesResponse>("strategy-studio/sources", { market }), [market]);
  const strategySources = useV2Resource(loadStrategySources);
  const marketStrategies = useMemo(() => (strategies.data?.strategies ?? []).filter((item) => !item.supportedMarkets?.length || item.supportedMarkets.includes(market)), [strategies.data, market]);
  const selectedStrategy = marketStrategies.find((item) => item.strategyId === strategyChoice) ?? marketStrategies[0] ?? null;
  const strategyId = selectedStrategy?.strategyId ?? null;
  const loadConfig = useCallback(() => (strategyId ? v2Get<StrategyConfigResponse>(`strategies/${strategyId}/config`, { market }) : Promise.resolve(null)), [strategyId, market]);
  const config = useV2Resource(loadConfig);
  const { refresh: refreshConfig } = config;
  const key = `${market}:${strategyId ?? ""}`;
  const loadDeployment = useCallback(() => (strategyId ? v2Get<StrategyDeployment>(`strategies/${strategyId}/deployment`, { market }) : Promise.resolve(null)), [strategyId, market]);
  const deployment = useV2Resource(loadDeployment);
  const loadTradingViewStatus = useCallback(() => (strategyId ? v2Get<TradingViewStatus>("integrations/tradingview/status", { market, strategy: strategyId }) : Promise.resolve(null)), [strategyId, market]);
  const tradingView = useV2Resource(loadTradingViewStatus);
  const timeframe = timeframeEdits[key] ?? deployment.data?.timeframe ?? selectedStrategy?.supportedTimeframes[0] ?? "5m";
  const universeId = universeEdits[key] ?? deployment.data?.universeId ?? "";
  const signalSource = sourceEdits[key] ?? deployment.data?.signalSource ?? "OPENDELTA";
  const riskSchema = useMemo(() => strategies.data?.riskSchema ?? schemaFromValues({ ...(strategies.data?.riskDefaults ?? {}), ...(config.data?.effectiveRiskSettings ?? {}) }), [strategies.data, config.data]);
  const effectiveDocument = useMemo<SettingsDocument>(() => ({
    strategy: selectedStrategy ? compactValues(schemaDefaults(selectedStrategy.configSchema, config.data?.effectiveConfiguration, selectedStrategy.defaults)) : {},
    paperExecution: compactValues(schemaDefaults(riskSchema, config.data?.effectiveRiskSettings, strategies.data?.riskDefaults)),
  }), [selectedStrategy, config.data, riskSchema, strategies.data]);
  const configurationJson = jsonEdits[key] ?? JSON.stringify(effectiveDocument, null, 2);
  const name = nameEdits[key] ?? (config.data?.active?.name ?? (selectedStrategy ? `${selectedStrategy.name} · ${marketLabel(market)}` : ""));
  const active = config.data?.active ?? null;

  const currentSource = strategySource || sourceTemplate.data?.sourceCode || "";
  const sourceIsTemplate = currentSource === (sourceTemplate.data?.sourceCode ?? "");

  const editStrategySource = async (source: StrategySource) => {
    if (!source.sourceId || sourceBusy !== null) return;
    if (currentSource && !sourceIsTemplate && !window.confirm("Replace the current unsaved draft with a new version of this saved source?")) return;
    setSourceBusy("validate");
    setSourceNotice(null);
    try {
      const saved = await v2Get<StrategySource>(`strategy-studio/sources/${source.sourceId}`);
      const nextSource = incrementPatchVersion(saved.sourceCode ?? "");
      if (!nextSource) throw new Error("The saved source code is unavailable.");
      setStrategySource(nextSource);
      setSourceValidation(null);
      setSourceNotice({ kind: "success", text: `${saved.name} v${saved.strategyVersion} loaded as a new draft. Review the automatically incremented version, then validate and save it.` });
      document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Strategy Python source"]')?.focus();
    } catch (reason) {
      setSourceNotice({ kind: "error", text: errorMessage(reason, "The saved strategy source could not be opened") });
    } finally {
      setSourceBusy(null);
    }
  };

  const resetStrategySource = () => {
    if (!sourceIsTemplate && !window.confirm("Discard the current unsaved strategy draft and restore the starter template?")) return;
    setStrategySource(sourceTemplate.data?.sourceCode ?? "");
    setSourceValidation(null);
    setSourceNotice({ kind: "success", text: "Starter template restored. No saved strategy version was changed." });
  };

  const validateStrategySource = async () => {
    if (!currentSource.trim()) return;
    setSourceBusy("validate");
    setSourceNotice(null);
    try {
      const result = await v2Post<StrategySourceValidation>("strategy-studio/validate", { sourceCode: currentSource });
      setSourceValidation(result);
      setSourceNotice({ kind: result.valid ? "success" : "error", text: result.valid ? `${result.manifest?.name ?? "Strategy"} v${result.manifest?.version ?? ""} is valid and safe to version.` : result.errors.join(" ") });
    } catch (reason) {
      setSourceNotice({ kind: "error", text: errorMessage(reason, "The strategy source could not be validated") });
    } finally {
      setSourceBusy(null);
    }
  };

  const saveStrategySource = async () => {
    if (!currentSource.trim()) return;
    setSourceBusy("save");
    setSourceNotice(null);
    try {
      const saved = await v2Post<StrategySource>("strategy-studio/sources", { sourceCode: currentSource });
      setSourceValidation(saved.validation);
      setSourceNotice({ kind: "success", text: `Saved immutable ${saved.name} v${saved.strategyVersion}. Backtest it, then approve that exact run for Signals or Paper.` });
      strategySources.refresh();
    } catch (reason) {
      setSourceNotice({ kind: "error", text: errorMessage(reason, "The strategy source could not be saved") });
    } finally {
      setSourceBusy(null);
    }
  };

  const openConfiguration = () => {
    const details = document.querySelector<HTMLDetailsElement>("details.quant-config-disclosure");
    if (details) {
      details.open = true;
      details.scrollIntoView({ behavior: "smooth", block: "center" });
      window.setTimeout(() => details.querySelector("textarea")?.focus(), 350);
    }
  };

  const testTradingView = async () => {
    if (!selectedStrategy) return;
    setTestingTradingView(true);
    setNotice(null);
    try {
      const result = await v2Post<TradingViewTestResult>("integrations/tradingview/test", { market, strategyId: selectedStrategy.strategyId });
      setNotice({ kind: result.ready ? "success" : "error", text: result.message });
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "TradingView validation failed") });
    } finally {
      setTestingTradingView(false);
    }
  };

  const changeMode = async (mode: StrategyDeploymentMode) => {
    if (!selectedStrategy || (mode !== "OFF" && !active)) return;
    setChangingMode(true);
    setNotice(null);
    try {
      await v2Post<StrategyDeployment>(`strategies/${selectedStrategy.strategyId}/deployment`, { market, timeframe, mode, universeId: universeId || null, signalSource }, { market });
      setNotice({ kind: "success", text: mode === "OFF" ? "New signals and paper entries stopped." : mode === "SIGNALS" ? "Signals are now running without paper entries." : "Signals and paper trading are now running for this strategy." });
      deployment.refresh();
      deployments.refresh();
      tradingView.refresh();
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "The strategy mode could not be changed") });
    } finally {
      setChangingMode(false);
    }
  };

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedStrategy) return;
    setSaving(true);
    setNotice(null);
    try {
      const document = parseSettingsDocument(configurationJson, selectedStrategy.configSchema, riskSchema);
      const saved = await v2Post<StrategyConfig>(`strategies/${selectedStrategy.strategyId}/config`, {
        market,
        name: name.trim() || `${selectedStrategy.name} · ${marketLabel(market)}`,
        configuration: document.strategy,
        riskSettings: document.paperExecution,
        activate: true,
      }, { market });
      setJsonEdits((current) => ({ ...current, [key]: JSON.stringify({ strategy: saved.configuration ?? document.strategy, paperExecution: saved.riskSettings ?? document.paperExecution }, null, 2) }));
      setNotice({ kind: "success", text: `Saved and activated "${saved.name}" (${shortId(saved.configId)}).` });
      refreshConfig();
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "The configuration could not be saved") });
    } finally {
      setSaving(false);
    }
  };

  const formatJson = () => {
    if (!selectedStrategy) return;
    try {
      const document = parseSettingsDocument(configurationJson, selectedStrategy.configSchema, riskSchema);
      setJsonEdits((current) => ({ ...current, [key]: JSON.stringify(document, null, 2) }));
      setNotice(null);
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "Invalid configuration") });
    }
  };

  const addInstrument = async (event: FormEvent) => {
    event.preventDefault();
    const symbol = instrumentSymbol.trim().toUpperCase();
    if (!symbol) {
      setInstrumentNotice({ kind: "error", text: `Enter a ${marketLabel(market)} symbol.` });
      return;
    }
    setAddingInstrument(true);
    setInstrumentNotice(null);
    try {
      const added = await platformPost<InstrumentAddResponse>("add-instrument", { market, symbol });
      const canonical = added.symbol ?? added.instrument?.providerSymbol ?? symbol;
      setInstrumentSymbol("");
      setInstrumentNotice({ kind: "success", text: `${canonical} is now available to watchlists, backtests and strategies.` });
      instruments.refresh();
    } catch (reason) {
      setInstrumentNotice({ kind: "error", text: errorMessage(reason, "The instrument could not be added") });
    } finally {
      setAddingInstrument(false);
    }
  };

  return <main className="quant-workspace">
    <WorkspaceHeader eyebrow={`${marketLabel(market)} strategy control`} title="Strategies" actions={<div className="quant-header-actions"><StatusBadge tone="good">Paper only</StatusBadge><StatusBadge>Server-managed keys</StatusBadge></div>} />

    <ExchangeConnectionsPanel />

    <details className={`quant-secondary-disclosure ${styles.studio}`}>
      <summary><span><Code2 size={15} />Strategy Studio</span><small>Python editor · validation and immutable versions</small></summary>
      <div className="quant-panel-body">
        <div className={styles.studioIntro}><div><strong>Create a strategy</strong><small>Edit Python here. Saving never deploys code; a completed backtest must be approved before Signals or Paper.</small></div><StatusBadge tone="good">Backtest-gated</StatusBadge></div>
        {sourceTemplate.loading ? <LoadingState label="Loading strategy template" /> : sourceTemplate.error ? <Message kind="error">Strategy Studio is unavailable while the strategy service is offline. <button type="button" onClick={sourceTemplate.reload}>Retry</button></Message> : <>
          <textarea className={styles.codeEditor} aria-label="Strategy Python source" spellCheck={false} value={currentSource} disabled={sourceBusy !== null} onChange={(event) => { setStrategySource(event.target.value); setSourceValidation(null); setSourceNotice(null); }} />
          <div className={`${styles.studioActions} quant-editor-actions`}><button type="button" disabled={sourceBusy !== null || sourceIsTemplate} onClick={resetStrategySource}><RotateCcw size={15} />Restore template</button><button type="button" disabled={sourceBusy !== null || !currentSource.trim()} onClick={() => void validateStrategySource()}><Beaker size={15} />{sourceBusy === "validate" ? "Validating…" : "Validate draft"}</button><button type="button" className="primary" disabled={sourceBusy !== null || !currentSource.trim() || sourceValidation?.valid !== true} title={sourceValidation?.valid === true ? "Save this validated source as an immutable version" : "Validate this draft successfully before saving"} onClick={() => void saveStrategySource()}><Save size={15} />{sourceBusy === "save" ? "Saving…" : "Save new version"}</button></div>
        </>}
        {sourceNotice && <Message kind={sourceNotice.kind}>{sourceNotice.text}</Message>}
        {sourceValidation && (sourceValidation.errors.length > 0 || sourceValidation.warnings.length > 0) && <div className={styles.validationList}>{sourceValidation.errors.map((item) => <span key={item} className={styles.validationError}>{item}</span>)}{sourceValidation.warnings.map((item) => <span key={item}>{item}</span>)}</div>}
        <div className={styles.sourceHistory}><strong>Saved sources</strong>{strategySources.loading ? <small>Loading…</small> : strategySources.error ? <small>Unavailable until migration 012 is applied</small> : strategySources.data?.sources.length ? strategySources.data.sources.map((item) => <div key={item.sourceId} className={styles.sourceRow}><span><strong>{item.name}</strong><small>{item.strategyId}</small></span><code>v{item.strategyVersion}</code><span>{item.manifest.supportedMarkets.join(" + ")}</span><StatusBadge tone="good">Validated</StatusBadge><button type="button" disabled={sourceBusy !== null} onClick={() => void editStrategySource(item)}><Pencil size={14} />Edit as new version</button></div>) : <small>No source versions saved for {marketLabel(market)}.</small>}</div>
      </div>
    </details>

    <AICopilotPanel surface="strategy" market={market} onUseDraft={(content) => { setStrategySource(content); setSourceValidation(null); setSourceNotice({ kind: "success", text: "AI draft loaded into the editor. Review it, then run normal validation before saving." }); }} />

    <Panel icon={<Settings2 size={17} />} title="Strategy control" description="Select a strategy, assign its timeframe and watchlist, then run signals or paper trading." aside={active ? <StatusBadge tone="good">Active: {active.name}</StatusBadge> : <StatusBadge tone="warn">No active config</StatusBadge>}>
      {strategies.loading ? <LoadingState label="Loading strategies" /> : strategies.error ? <RequestErrorState error={strategies.error} retry={strategies.reload} /> : !selectedStrategy ? <EmptyState title="No strategies registered" description={`No strategy supports ${marketLabel(market)}.`} /> : <form onSubmit={save} noValidate>
        <div className="quant-panel-body">
          <div className={styles.strategyTable} role="table" aria-label="Strategies">
            <div className={styles.strategyHeader} role="row"><span>Strategy</span><span>Source</span><span>Timeframe</span><span>Watchlist</span><span>Mode</span><span>Status</span></div>
            {marketStrategies.map((item) => {
              const row = deployments.data?.deployments.find((candidate) => candidate.strategyId === item.strategyId);
              const watchlist = universes.data?.universes.find((candidate) => candidate.universeId === row?.universeId);
              const selected = item.strategyId === selectedStrategy.strategyId;
              return <div key={item.strategyId} role="row" className={`${styles.strategyRow} ${selected ? styles.selectedRow : ""}`}>
                <button type="button" className={styles.strategySelect} aria-current={selected ? "true" : undefined} onClick={() => { setStrategyChoice(item.strategyId); setNotice(null); }}><strong>{item.name}</strong><small>v{item.version}</small></button>
                <span>{row?.signalSource === "TRADINGVIEW" ? "TradingView" : "OpenDelta"}</span><span>{row?.timeframe ?? "—"}</span><span>{watchlist?.name ?? "Active market"}</span><span><StatusBadge tone={row?.mode === "PAPER" ? "good" : row?.mode === "SIGNALS" ? "neutral" : "warn"}>{row?.mode ?? "OFF"}</StatusBadge></span><span>{row?.configId ? "Configured" : row?.mode === "OFF" ? "Stopped" : "Needs config"}</span>
              </div>;
            })}
            {(strategySources.data?.sources ?? []).filter((source) => !marketStrategies.some((item) => item.strategyId === source.strategyId)).map((source) => {
              const row = deployments.data?.deployments.find((candidate) => candidate.strategySourceId === source.sourceId);
              const watchlist = universes.data?.universes.find((candidate) => candidate.universeId === row?.universeId);
              return <div key={source.sourceId} role="row" className={styles.strategyRow}>
                <button type="button" className={styles.strategySelect} onClick={() => void editStrategySource(source)}><strong>{source.name}</strong><small>v{source.strategyVersion} · Edit as new</small></button>
                <span>Strategy Studio</span><span>{row?.timeframe ?? source.manifest.supportedTimeframes.join(", ")}</span><span>{watchlist?.name ?? (row ? "Active market" : "—")}</span><span><StatusBadge tone={row?.mode === "PAPER" ? "good" : row?.mode === "SIGNALS" ? "neutral" : "warn"}>{row?.mode ?? "DRAFT"}</StatusBadge></span><span>{row ? "Backtest approved" : "Backtest required"}</span>
              </div>;
            })}
          </div>
          <div className="quant-form-grid quant-settings-identity">
            <label><span>Selected strategy</span><input type="text" readOnly value={`${selectedStrategy.name} · v${selectedStrategy.version}`} /><small>Timeframes {selectedStrategy.supportedTimeframes.join(", ") || "—"}</small></label>
            <label><span>Configuration name</span><input type="text" value={name} disabled={saving} onChange={(event) => setNameEdits((current) => ({ ...current, [key]: event.target.value }))} /><small>Saved with each configuration version</small></label>
          </div>
          {config.loading ? <LoadingState label="Loading active configuration" /> : config.error ? <RequestErrorState error={config.error} retry={config.reload} /> : <>
            <dl className="quant-facts">
              <div><dt>Market</dt><dd>{marketLabel(market)}</dd></div>
              <div><dt>Strategy version</dt><dd>v{selectedStrategy.version}</dd></div>
              <div><dt>Active config</dt><dd>{active ? shortId(active.configId) : "None"}</dd></div>
              <div><dt>Updated</dt><dd>{formatDateTime(active?.updatedAt ?? active?.createdAt, market)}</dd></div>
            </dl>
            {deployment.loading ? <LoadingState label="Loading strategy mode" /> : deployment.error ? <RequestErrorState error={deployment.error} retry={deployment.reload} /> : <section className={styles.deploymentControl} aria-label="Strategy deployment">
              <div className={styles.deploymentCopy}><strong>Automation</strong><small>Approve one source, watchlist and mode for this strategy.</small></div>
              <label><span>Signal source</span><select value={signalSource} disabled={changingMode} onChange={(event) => setSourceEdits((current) => ({ ...current, [key]: event.target.value as StrategySignalSource }))}><option value="OPENDELTA">OpenDelta</option><option value="TRADINGVIEW">TradingView</option></select></label>
              <label><span>Timeframe</span><select value={timeframe} disabled={changingMode} onChange={(event) => setTimeframeEdits((current) => ({ ...current, [key]: event.target.value }))}>{selectedStrategy.supportedTimeframes.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
              <label><span>Watchlist</span><select value={universeId} disabled={changingMode || universes.loading} onChange={(event) => setUniverseEdits((current) => ({ ...current, [key]: event.target.value }))}><option value="">Active market watchlist</option>{universes.data?.universes.map((item) => <option key={item.universeId} value={item.universeId}>{item.name} · {item.symbols.length} symbols</option>)}</select></label>
              <div className={styles.modeSwitch} role="group" aria-label="Strategy mode">{(["OFF", "SIGNALS", "PAPER"] as StrategyDeploymentMode[]).map((mode) => <button key={mode} type="button" className={deployment.data?.mode === mode && deployment.data?.signalSource === signalSource ? styles.active : ""} aria-pressed={deployment.data?.mode === mode && deployment.data?.signalSource === signalSource} disabled={changingMode || (mode !== "OFF" && (!active || (signalSource === "TRADINGVIEW" && !universeId)))} onClick={() => void changeMode(mode)}>{mode === "OFF" ? "Off" : mode === "SIGNALS" ? "Signals" : "Paper"}</button>)}</div>
              <div className={styles.deploymentState}>{!active ? <><span>Save a configuration to unlock Signals and Paper.</span><button type="button" className="quant-action-link" onClick={openConfiguration}>Configure now</button></> : signalSource === "TRADINGVIEW" && !universeId ? <span>Select an explicit watchlist to unlock TradingView modes.</span> : <span>{signalSource === "TRADINGVIEW" && tradingView.data && !tradingView.data.configured ? "Server webhook key is not configured yet; alerts will fail closed." : deployment.data?.mode === "PAPER" ? `${signalSource === "TRADINGVIEW" ? "Approved TradingView alerts" : "OpenDelta signals"} create simulated orders. Real broker orders remain disabled.` : deployment.data?.mode === "SIGNALS" ? "Approved signals are stored, but no new paper entries are opened." : "No new signals or paper entries. Existing paper positions continue to be monitored."}</span>}{signalSource === "TRADINGVIEW" && <button type="button" onClick={() => void testTradingView()} disabled={testingTradingView}>{testingTradingView ? "Testing…" : "Test alert pipeline"}</button>}</div>
            </section>}
            <details className="quant-config-disclosure">
              <summary><span><Braces size={15} />Edit JSON</span><small>Strategy and paper execution</small></summary>
              <div className="quant-json-editor">
                <div className="quant-json-editor-heading"><span>Configuration JSON</span><small><code>strategy</code> drives signals/backtests; <code>paperExecution</code> controls simulated sizing and fills.</small></div>
                <textarea aria-label="Strategy and paper execution JSON" spellCheck={false} value={configurationJson} disabled={saving} onChange={(event) => setJsonEdits((current) => ({ ...current, [key]: event.target.value }))} />
                <div className="quant-backtest-config-actions"><button type="button" disabled={saving} onClick={() => void navigator.clipboard.writeText(configurationJson)}><Copy size={14} />Copy JSON</button><button type="button" disabled={saving} onClick={formatJson}>Validate and format</button><button type="button" disabled={saving} onClick={() => { setJsonEdits((current) => ({ ...current, [key]: JSON.stringify(effectiveDocument, null, 2) })); setNotice(null); }}>Reset to active</button><button type="submit" className="primary" disabled={saving || config.loading}><Save size={15} />{saving ? "Saving…" : "Save and activate"}</button></div>
              </div>
            </details>
          </>}
          {notice && <Message kind={notice.kind}>{notice.text}</Message>}
        </div>
      </form>}
      {config.data && config.data.all.length > 0 && <details className="quant-secondary-disclosure"><summary><span>Configuration history</span><small>{config.data.all.length} saved version{config.data.all.length === 1 ? "" : "s"}</small></summary><div className="quant-table-scroll"><table className="quant-table"><thead><tr><th>Name</th><th>Config id</th><th>Status</th><th>Created</th></tr></thead><tbody>{config.data.all.map((item) => <tr key={item.configId} className={item.active ? "active" : ""}><td><strong>{item.name}</strong></td><td className="mono">{shortId(item.configId)}</td><td><StatusBadge tone={item.active ? "good" : "neutral"}>{item.active ? "Active" : "Saved"}</StatusBadge></td><td>{formatDateTime(item.createdAt, market)}</td></tr>)}</tbody></table></div></details>}
      <details className="quant-secondary-disclosure">
        <summary><span><Plus size={15} />Instrument setup</span><small>{instruments.loading ? "Loading…" : instruments.error ? "Unavailable" : `${instruments.data?.count ?? 0} configured for ${marketLabel(market)}`}</small></summary>
        <div className="quant-panel-body">
          <form className={`quant-toolbar ${styles.instrumentControl}`} onSubmit={(event) => void addInstrument(event)}>
            <div className={styles.instrumentCopy}><strong>Add a {marketLabel(market)} symbol</strong><small>{market === "CRYPTO" ? "Validated against the OKX public instrument catalogue." : "Validated against Dhan's active NSE equity instrument master."}</small></div>
            <label><span>Symbol</span><input value={instrumentSymbol} disabled={addingInstrument} placeholder={market === "CRYPTO" ? "BTC-USDT" : "RELIANCE"} onChange={(event) => { setInstrumentSymbol(event.target.value); setInstrumentNotice(null); }} /></label>
            <button type="submit" className="primary" disabled={addingInstrument || !instrumentSymbol.trim()}><Plus size={15} />{addingInstrument ? "Adding…" : "Add symbol"}</button>
          </form>
          <small className={styles.instrumentNote}>Adding an instrument makes it available to the market. Choose it in a watchlist before assigning that watchlist to a strategy.</small>
          {instrumentNotice && <Message kind={instrumentNotice.kind}>{instrumentNotice.text}</Message>}
        </div>
      </details>
    </Panel>

  </main>;
}
