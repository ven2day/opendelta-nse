"use client";

import { Coins, Save, Settings2, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { useCallback, useMemo, useState, type FormEvent } from "react";
import { GlobalPriceRangeForm } from "../admin/admin-settings";
import type { GlobalSettingsPayload } from "../global-settings-shared";
import { formatDateTime, marketLabel, MARKETS, shortId } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { compactValues, schemaDefaults, schemaFromValues, SchemaForm, type ConfigValues } from "../platform/schema-form";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type { StrategiesResponse, StrategyConfig, StrategyConfigResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, Message, Panel, RequestErrorState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

type Notice = { kind: "success" | "error"; text: string } | null;

const LEGACY_TOOLS: Array<{ href: string; title: string; description: string }> = [
  { href: "/legacy/screener", title: "Legacy RSI screener", description: "The original NSE RSI and volume dashboard with Dhan refresh controls." },
  { href: "/legacy/backtest", title: "Legacy NSE backtest", description: "Strong Buy, RSI recovery and ATR exit studies with saved history." },
  { href: "/legacy/backtest/crypto", title: "Legacy crypto backtest", description: "OKX and VALR completed-candle backtests for crypto and metals." },
  { href: "/legacy/signals", title: "Legacy NSE signals", description: "Completed-candle RSI recovery signals and manual paper decisions." },
  { href: "/legacy/signals?view=universe", title: "Legacy live universe", description: "Frozen live-signal universe versions and CSV exports." },
  { href: "/legacy/signals/crypto", title: "Legacy crypto signals", description: "Paper-only crypto and metals signal monitor." },
  { href: "/legacy/markets", title: "Legacy markets", description: "Instrument master and market context by provider." },
  { href: "/admin", title: "Admin price range", description: "Standalone page for the global price range form embedded below." },
];

export function SettingsWorkspace({ initialMarket, globalSettings }: { initialMarket: PlatformMarket; globalSettings: GlobalSettingsPayload }) {
  const [market, setMarket] = useState<PlatformMarket>(initialMarket);
  const [strategyChoice, setStrategyChoice] = useState<string | null>(null);
  const [configEdits, setConfigEdits] = useState<Record<string, ConfigValues>>({});
  const [riskEdits, setRiskEdits] = useState<Record<string, ConfigValues>>({});
  const [nameEdits, setNameEdits] = useState<Record<string, string>>({});
  const [activate, setActivate] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  const loadStrategies = useCallback(() => v2Get<StrategiesResponse>("strategies", { market }), [market]);
  const strategies = useV2Resource(loadStrategies);
  const marketStrategies = useMemo(() => (strategies.data?.strategies ?? []).filter((strategy) => !strategy.supportedMarkets?.length || strategy.supportedMarkets.includes(market)), [strategies.data, market]);
  const strategy = marketStrategies.find((item) => item.strategyId === strategyChoice) ?? marketStrategies[0] ?? null;
  const strategyId = strategy?.strategyId ?? null;
  const loadConfig = useCallback(() => (strategyId ? v2Get<StrategyConfigResponse>(`strategies/${strategyId}/config`, { market }) : Promise.resolve(null)), [strategyId, market]);
  const config = useV2Resource(loadConfig);
  const { refresh: refreshConfig } = config;

  const key = `${market}:${strategyId ?? ""}`;
  // Prefer the published risk schema (enum fields become selects); infer from defaults only when an older service omits it.
  const riskSchema = useMemo(() => strategies.data?.riskSchema ?? schemaFromValues({ ...(strategies.data?.riskDefaults ?? {}), ...(config.data?.effectiveRiskSettings ?? {}) }), [strategies.data, config.data]);
  const configuration = strategy ? (configEdits[key] ?? schemaDefaults(strategy.configSchema, config.data?.effectiveConfiguration, strategy.defaults)) : {};
  const riskSettings = riskEdits[key] ?? schemaDefaults(riskSchema, config.data?.effectiveRiskSettings, strategies.data?.riskDefaults);
  const name = nameEdits[key] ?? (config.data?.active?.name ?? (strategy ? `${strategy.name} · ${marketLabel(market)}` : ""));
  const active = config.data?.active ?? null;

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!strategy) return;
    setSaving(true);
    setNotice(null);
    try {
      const saved = await v2Post<StrategyConfig>(`strategies/${strategy.strategyId}/config`, {
        market,
        name: name.trim() || `${strategy.name} · ${marketLabel(market)}`,
        configuration: compactValues(configuration),
        riskSettings: compactValues(riskSettings),
        activate,
      }, { market });
      setNotice({ kind: "success", text: `Saved "${saved.name}" (${shortId(saved.configId)})${saved.active ? " and activated it" : ""} for ${strategy.name} on ${marketLabel(market)}.` });
      setConfigEdits((current) => ({ ...current, [key]: saved.configuration ?? configuration }));
      setRiskEdits((current) => ({ ...current, [key]: saved.riskSettings ?? riskSettings }));
      refreshConfig();
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "The configuration could not be saved") });
    } finally {
      setSaving(false);
    }
  };

  return <main className="quant-workspace">
    <WorkspaceHeader
      eyebrow="Configuration"
      title="Platform settings"
      description="Strategy parameters and risk settings per market are versioned on the platform database. Credentials, provider secrets and executable code stay outside the UI."
      actions={<div className="quant-header-actions"><StatusBadge tone="good">Orders disabled</StatusBadge><StatusBadge>Environment managed</StatusBadge></div>}
    />

    <Panel icon={<Settings2 size={17} />} title="Strategy configuration" description="Forms are generated from each strategy's published schema. Saving with activation makes this the configuration used by the signal engine and new backtests." aside={active ? <StatusBadge tone="good">Active: {active.name}{strategy ? ` · v${strategy.version}` : ""}</StatusBadge> : <StatusBadge tone="warn">No active config</StatusBadge>}>
      {strategies.loading ? <LoadingState label="Loading strategies" /> : strategies.error ? <RequestErrorState error={strategies.error} retry={strategies.reload} /> : !strategy ? <EmptyState title="No strategies registered" description={`No strategy supports ${marketLabel(market)}.`} /> : <form onSubmit={save} noValidate>
        <div className="quant-panel-body">
          <div className="quant-form-grid">
            <label><span>Market</span><select value={market} onChange={(event) => { setMarket(event.target.value as PlatformMarket); setStrategyChoice(null); }}>{MARKETS.map((item) => <option key={item} value={item}>{marketLabel(item)}</option>)}</select></label>
            <label><span>Strategy</span><select value={strategy.strategyId} onChange={(event) => setStrategyChoice(event.target.value)}>{marketStrategies.map((item) => <option key={item.strategyId} value={item.strategyId}>{item.name} · v{item.version}</option>)}</select><small>Timeframes {strategy.supportedTimeframes.join(", ") || "—"}</small></label>
            <label><span>Configuration name</span><input type="text" value={name} onChange={(event) => setNameEdits((current) => ({ ...current, [key]: event.target.value }))} /></label>
            <label className="checkbox"><input type="checkbox" checked={activate} onChange={(event) => setActivate(event.target.checked)} /><span>Activate on save</span></label>
          </div>
          {config.loading ? <LoadingState label="Loading active configuration" /> : config.error ? <RequestErrorState error={config.error} retry={config.reload} /> : <>
            <dl className="quant-facts">
              <div><dt>Active config</dt><dd>{active ? active.name : "None"}</dd></div>
              <div><dt>Config id</dt><dd className="mono">{active ? shortId(active.configId) : "—"}</dd></div>
              <div><dt>Strategy version</dt><dd>v{strategy.version}</dd></div>
              <div><dt>Updated</dt><dd>{formatDateTime(active?.updatedAt ?? active?.createdAt, market)}</dd></div>
              <div><dt>Saved versions</dt><dd>{config.data?.all.length ?? 0}</dd></div>
            </dl>
            <h3 className="quant-subheading"><SlidersHorizontal size={14} />Strategy parameters</h3>
            <SchemaForm schema={strategy.configSchema} values={configuration} disabled={saving} onChange={(next) => setConfigEdits((current) => ({ ...current, [key]: next }))} />
            <h3 className="quant-subheading"><ShieldCheck size={14} />Risk and execution settings</h3>
            <SchemaForm schema={riskSchema} values={riskSettings} disabled={saving} onChange={(next) => setRiskEdits((current) => ({ ...current, [key]: next }))} />
          </>}
          {notice && <Message kind={notice.kind}>{notice.text}</Message>}
        </div>
        <div className="quant-form-actions">
          <button type="submit" className="primary" disabled={saving || config.loading}><Save size={15} />{saving ? "Saving…" : activate ? "Save and activate" : "Save"}</button>
          <button type="button" disabled={saving} onClick={() => { setConfigEdits((current) => ({ ...current, [key]: schemaDefaults(strategy.configSchema, config.data?.effectiveConfiguration, strategy.defaults) })); setRiskEdits((current) => ({ ...current, [key]: schemaDefaults(riskSchema, config.data?.effectiveRiskSettings, strategies.data?.riskDefaults) })); }}>Reset to effective values</button>
          <span>{strategy.name} v{strategy.version} · {marketLabel(market)}</span>
        </div>
      </form>}
      {config.data && config.data.all.length > 0 && <div className="quant-table-scroll"><table className="quant-table">
        <thead><tr><th>Name</th><th>Config id</th><th>Status</th><th>Created</th><th>Updated</th></tr></thead>
        <tbody>{config.data.all.map((item) => <tr key={item.configId} className={item.active ? "active" : ""}><td><strong>{item.name}</strong></td><td className="mono">{shortId(item.configId)}</td><td><StatusBadge tone={item.active ? "good" : "neutral"}>{item.active ? "Active" : "Saved"}</StatusBadge></td><td>{formatDateTime(item.createdAt, market)}</td><td>{formatDateTime(item.updatedAt, market)}</td></tr>)}</tbody>
      </table></div>}
    </Panel>

    <Panel icon={<Coins size={17} />} title="Global price range" description="Applies to the legacy NSE screener, signals and backtest symbol universe. Stored backtest history is not changed.">
      <div className="site-shell quant-embedded-shell"><GlobalPriceRangeForm initialSettings={globalSettings} /></div>
    </Panel>

    <Panel icon={<ShieldCheck size={17} />} title="Credentials and broker execution" description="Secrets remain server-side environment values. The platform accepts no user-supplied executable strategies and installs no live order adapter." aside={<StatusBadge tone="good">Orders disabled</StatusBadge>}>
      <div className="quant-panel-body"><p className="quant-inline-note">Every workspace in this application is research or paper only. Broker execution is disabled at the service level and cannot be enabled from the UI.</p></div>
    </Panel>

    <Panel title="Legacy tools" description="The previous production pages stay reachable while the unified platform is rolled out.">
      <div className="quant-panel-body"><div className="quant-link-grid">{LEGACY_TOOLS.map((tool) => <a key={tool.href} href={tool.href}><strong>{tool.title}</strong><span>{tool.description}</span></a>)}</div></div>
    </Panel>
  </main>;
}
