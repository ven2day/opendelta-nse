"use client";

import { Braces, Save, Settings2, ShieldCheck } from "lucide-react";
import { useCallback, useMemo, useState, type FormEvent } from "react";
import { formatDateTime, marketLabel, shortId } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { compactValues, schemaDefaults, schemaFromValues, validateConfigValues, type ConfigSchema, type ConfigValues } from "../platform/schema-form";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type { StrategiesResponse, StrategyConfig, StrategyConfigResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, Message, Panel, RequestErrorState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

type Notice = { kind: "success" | "error"; text: string } | null;
type SettingsDocument = { strategy: ConfigValues; paperExecution: ConfigValues };

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

export function SettingsWorkspace({ initialMarket }: { initialMarket: PlatformMarket }) {
  const market = initialMarket;
  const [strategyChoice, setStrategyChoice] = useState<string | null>(null);
  const [jsonEdits, setJsonEdits] = useState<Record<string, string>>({});
  const [nameEdits, setNameEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  const loadStrategies = useCallback(() => v2Get<StrategiesResponse>("strategies", { market }), [market]);
  const strategies = useV2Resource(loadStrategies);
  const marketStrategies = useMemo(() => (strategies.data?.strategies ?? []).filter((item) => !item.supportedMarkets?.length || item.supportedMarkets.includes(market)), [strategies.data, market]);
  const selectedStrategy = marketStrategies.find((item) => item.strategyId === strategyChoice) ?? marketStrategies[0] ?? null;
  const strategyId = selectedStrategy?.strategyId ?? null;
  const loadConfig = useCallback(() => (strategyId ? v2Get<StrategyConfigResponse>(`strategies/${strategyId}/config`, { market }) : Promise.resolve(null)), [strategyId, market]);
  const config = useV2Resource(loadConfig);
  const { refresh: refreshConfig } = config;
  const key = `${market}:${strategyId ?? ""}`;
  const riskSchema = useMemo(() => strategies.data?.riskSchema ?? schemaFromValues({ ...(strategies.data?.riskDefaults ?? {}), ...(config.data?.effectiveRiskSettings ?? {}) }), [strategies.data, config.data]);
  const effectiveDocument = useMemo<SettingsDocument>(() => ({
    strategy: selectedStrategy ? compactValues(schemaDefaults(selectedStrategy.configSchema, config.data?.effectiveConfiguration, selectedStrategy.defaults)) : {},
    paperExecution: compactValues(schemaDefaults(riskSchema, config.data?.effectiveRiskSettings, strategies.data?.riskDefaults)),
  }), [selectedStrategy, config.data, riskSchema, strategies.data]);
  const configurationJson = jsonEdits[key] ?? JSON.stringify(effectiveDocument, null, 2);
  const name = nameEdits[key] ?? (config.data?.active?.name ?? (selectedStrategy ? `${selectedStrategy.name} · ${marketLabel(market)}` : ""));
  const active = config.data?.active ?? null;

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

  return <main className="quant-workspace">
    <WorkspaceHeader eyebrow={`${marketLabel(market)} configuration`} title="Settings" actions={<div className="quant-header-actions"><StatusBadge tone="good">Paper only</StatusBadge><StatusBadge>Server-managed keys</StatusBadge></div>} />

    <Panel icon={<Settings2 size={17} />} title="Strategy settings" description="One versioned JSON document controls strategy behaviour and paper execution for this market." aside={active ? <StatusBadge tone="good">Active: {active.name}</StatusBadge> : <StatusBadge tone="warn">No active config</StatusBadge>}>
      {strategies.loading ? <LoadingState label="Loading strategies" /> : strategies.error ? <RequestErrorState error={strategies.error} retry={strategies.reload} /> : !selectedStrategy ? <EmptyState title="No strategies registered" description={`No strategy supports ${marketLabel(market)}.`} /> : <form onSubmit={save} noValidate>
        <div className="quant-panel-body">
          <div className="quant-form-grid quant-settings-identity">
            <label><span>Strategy</span><select value={selectedStrategy.strategyId} disabled={saving} onChange={(event) => { setStrategyChoice(event.target.value); setNotice(null); }}>{marketStrategies.map((item) => <option key={item.strategyId} value={item.strategyId}>{item.name} · v{item.version}</option>)}</select><small>Timeframes {selectedStrategy.supportedTimeframes.join(", ") || "—"}</small></label>
            <label><span>Configuration name</span><input type="text" value={name} disabled={saving} onChange={(event) => setNameEdits((current) => ({ ...current, [key]: event.target.value }))} /></label>
          </div>
          {config.loading ? <LoadingState label="Loading active configuration" /> : config.error ? <RequestErrorState error={config.error} retry={config.reload} /> : <>
            <dl className="quant-facts">
              <div><dt>Market</dt><dd>{marketLabel(market)}</dd></div>
              <div><dt>Strategy version</dt><dd>v{selectedStrategy.version}</dd></div>
              <div><dt>Active config</dt><dd>{active ? shortId(active.configId) : "None"}</dd></div>
              <div><dt>Updated</dt><dd>{formatDateTime(active?.updatedAt ?? active?.createdAt, market)}</dd></div>
            </dl>
            <div className="quant-json-editor">
              <div className="quant-json-editor-heading"><span><Braces size={15} />Configuration JSON</span><small><code>strategy</code> drives signals/backtests; <code>paperExecution</code> controls simulated sizing and fills.</small></div>
              <textarea aria-label="Strategy and paper execution JSON" spellCheck={false} value={configurationJson} disabled={saving} onChange={(event) => setJsonEdits((current) => ({ ...current, [key]: event.target.value }))} />
              <div className="quant-backtest-config-actions"><button type="button" disabled={saving} onClick={formatJson}>Validate and format</button><button type="button" disabled={saving} onClick={() => { setJsonEdits((current) => ({ ...current, [key]: JSON.stringify(effectiveDocument, null, 2) })); setNotice(null); }}>Reset to active</button></div>
            </div>
          </>}
          {notice && <Message kind={notice.kind}>{notice.text}</Message>}
        </div>
        <div className="quant-form-actions"><button type="submit" className="primary" disabled={saving || config.loading}><Save size={15} />{saving ? "Saving…" : "Save and activate"}</button><span>Creates a version; previous versions remain in history.</span></div>
      </form>}
      {config.data && config.data.all.length > 0 && <div className="quant-table-scroll"><table className="quant-table"><thead><tr><th>Name</th><th>Config id</th><th>Status</th><th>Created</th></tr></thead><tbody>{config.data.all.map((item) => <tr key={item.configId} className={item.active ? "active" : ""}><td><strong>{item.name}</strong></td><td className="mono">{shortId(item.configId)}</td><td><StatusBadge tone={item.active ? "good" : "neutral"}>{item.active ? "Active" : "Saved"}</StatusBadge></td><td>{formatDateTime(item.createdAt, market)}</td></tr>)}</tbody></table></div>}
    </Panel>

    <Panel icon={<ShieldCheck size={17} />} title="Connections and safety" description="Connection details are informational; secrets cannot be entered in the browser." aside={<StatusBadge tone="good">Live orders disabled</StatusBadge>}>
      <div className="quant-panel-body"><dl className="quant-facts">
        <div><dt>Market data</dt><dd>{market === "CRYPTO" ? "OKX public feed" : "Dhan server connection"}</dd></div>
        <div><dt>Market-data key</dt><dd>{market === "CRYPTO" ? "Not required" : "Server managed"}</dd></div>
        <div><dt>Trading keys</dt><dd>Not accepted</dd></div>
        <div><dt>Execution</dt><dd>Paper simulation only</dd></div>
      </dl></div>
    </Panel>
  </main>;
}
