"use client";

import { Braces, Copy, Layers, ListChecks, LoaderCircle, Play, RefreshCw, Save, ScanSearch } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { formatDateTime, formatInteger, formatNumber, formatPercent, humanize, marketLabel, shortId, tone } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { compactValues, validateConfigValues, type ConfigSchema, type ConfigValues } from "../platform/schema-form";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type { ScreenerFiltersResponse, ScreenerResultsResponse, ScreenerRun, Universe, UniversePresetsResponse, UniversesResponse, WatchlistProfileVersion, WatchlistProfilesResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, Message, PaperOnlyBadge, Panel, RequestErrorState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

const RUN_POLL_MS = 2_000;
const ACTIVE_RUN_STATUSES = new Set(["QUEUED", "RUNNING", "PENDING"]);
type Notice = { kind: "success" | "error"; text: string } | null;

const FILTER_SCHEMA: ConfigSchema = {
  lookbackDays: { type: "integer", minimum: 1 },
  minimumPrice: { type: "number", minimum: 0 },
  maximumPrice: { type: "number", minimum: 0 },
  minimumAverageTradedValue: { type: "number", minimum: 0 },
  minimumAverageVolume: { type: "number", minimum: 0 },
  minimumVolatilityPct: { type: "number", minimum: 0 },
  maximumVolatilityPct: { type: "number", minimum: 0 },
  minimumCandleCoverage: { type: "number", minimum: 0, maximum: 1 },
  minimumSessions: { type: "integer", minimum: 1 },
  rankBy: { type: "string" },
  maximumSymbols: { type: "integer", minimum: 1 },
};

function isObject(value: unknown): value is ConfigValues {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseFilterOverrides(text: string): ConfigValues {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("Screener configuration is not valid JSON.");
  }
  if (!isObject(parsed)) throw new Error("Screener configuration must be a JSON object.");
  const overrides = compactValues(parsed);
  validateConfigValues(overrides, FILTER_SCHEMA, "filters");
  return overrides;
}

function parseSymbols(text: string): string[] {
  return Array.from(new Set(text.split(/[\s,;]+/).map((symbol) => symbol.trim().toUpperCase()).filter(Boolean)));
}

function isActive(status: string | undefined): boolean {
  return Boolean(status && ACTIVE_RUN_STATUSES.has(status.toUpperCase()));
}

export function ScreenerWorkspace({ market }: { market: PlatformMarket }) {
  const loadFilters = useCallback(() => v2Get<ScreenerFiltersResponse>("screener/filters"), []);
  const loadRuns = useCallback(() => v2Get<{ runs: ScreenerRun[] }>("screener/runs", { market, limit: 10 }), [market]);
  const loadUniverses = useCallback(() => v2Get<UniversesResponse>("screener/universes", { market }), [market]);
  const loadPresets = useCallback(() => v2Get<UniversePresetsResponse>("screener/presets", { market }), [market]);
  const loadProfiles = useCallback(() => v2Get<WatchlistProfilesResponse>("screener/profiles", { market }), [market]);
  const filters = useV2Resource(loadFilters);
  const runs = useV2Resource(loadRuns);
  const universes = useV2Resource(loadUniverses);
  const presets = useV2Resource(loadPresets);
  const profiles = useV2Resource(loadProfiles);
  const { refresh: refreshRuns } = runs;
  const { refresh: refreshUniverses } = universes;
  const { refresh: refreshProfiles } = profiles;

  const [symbolSource, setSymbolSource] = useState("market");
  const [symbolsText, setSymbolsText] = useState("");
  const [filterJsonEdits, setFilterJsonEdits] = useState<Record<PlatformMarket, string>>({ NSE: "", CRYPTO: "" });
  const [selectedProfileIds, setSelectedProfileIds] = useState<Record<PlatformMarket, string>>({ NSE: "", CRYPTO: "" });
  const [selectedProfileVersionIds, setSelectedProfileVersionIds] = useState<Record<PlatformMarket, string>>({ NSE: "", CRYPTO: "" });
  const [profileNames, setProfileNames] = useState<Record<PlatformMarket, string>>({ NSE: "", CRYPTO: "" });
  const [profileDirty, setProfileDirty] = useState<Record<PlatformMarket, boolean>>({ NSE: true, CRYPTO: true });
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileNotice, setProfileNotice] = useState<Notice>(null);
  const [submitting, setSubmitting] = useState(false);
  const [runNotice, setRunNotice] = useState<Notice>(null);
  const [pollingRunId, setPollingRunId] = useState<string | null>(null);
  const [pollingRun, setPollingRun] = useState<ScreenerRun | null>(null);
  const [selectedRunChoice, setSelectedRunChoice] = useState<string | null>(null);
  const [resultsTab, setResultsTab] = useState<"passed" | "rejected">("passed");
  const [universeName, setUniverseName] = useState("");
  const [manualIncludes, setManualIncludes] = useState("");
  const [manualExcludes, setManualExcludes] = useState("");
  const [savingUniverse, setSavingUniverse] = useState(false);
  const [universeNotice, setUniverseNotice] = useState<Notice>(null);
  const [activatingId, setActivatingId] = useState<string | null>(null);
  const [importName, setImportName] = useState("");
  const [importSymbols, setImportSymbols] = useState("");
  const [importing, setImporting] = useState(false);
  const [importNotice, setImportNotice] = useState<Notice>(null);

  const defaultFilters = filters.data?.defaults ?? {};
  const profileVersions = (profiles.data?.profiles ?? []).flatMap((profile) => profile.versions);
  const selectedProfileVersionId = selectedProfileVersionIds[market];
  const selectedProfileId = selectedProfileIds[market];
  const selectedProfileVersion = profileVersions.find((version) => version.profileVersionId === selectedProfileVersionId) ?? null;
  const filterJson = filterJsonEdits[market] || JSON.stringify(compactValues(defaultFilters), null, 2);
  const selectedPreset = presets.data?.presets.find((preset) => preset.presetId === symbolSource) ?? null;
  const effectiveSymbolSource = symbolSource === "market" || symbolSource === "custom" || selectedPreset ? symbolSource : "market";

  const selectedRunId = selectedRunChoice ?? runs.data?.runs[0]?.runId ?? null;

  const importTradingView = async (event: FormEvent) => {
    event.preventDefault();
    setImporting(true);
    setImportNotice(null);
    try {
      const result = await v2Post<{ universe: Universe; accepted: string[]; rejected: string[] }>("screener/universes/import-tradingview", { market, name: importName.trim(), symbols: importSymbols, activate: true });
      setImportNotice({ kind: result.rejected.length ? "error" : "success", text: `Imported ${result.accepted.length} symbols into “${result.universe.name}”${result.rejected.length ? `; ${result.rejected.length} could not be matched: ${result.rejected.slice(0, 5).join(", ")}` : "."}` });
      setImportName(""); setImportSymbols(""); refreshUniverses();
    } catch (reason) {
      setImportNotice({ kind: "error", text: errorMessage(reason, "TradingView watchlist could not be imported") });
    } finally { setImporting(false); }
  };
  const loadResults = useCallback(async () => {
    if (!selectedRunId) return null;
    const [passed, rejected] = await Promise.all([
      v2Get<ScreenerResultsResponse>(`screener/runs/${selectedRunId}/results`, { passed: true }),
      v2Get<ScreenerResultsResponse>(`screener/runs/${selectedRunId}/results`, { passed: false }),
    ]);
    return { passed, rejected };
  }, [selectedRunId]);
  const results = useV2Resource(loadResults);
  const { refresh: refreshResults } = results;

  useEffect(() => {
    if (!pollingRunId) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const run = await v2Get<ScreenerRun>(`screener/runs/${pollingRunId}`);
        if (cancelled) return;
        setPollingRun(run);
        if (!isActive(run.status)) {
          setPollingRunId(null);
          setRunNotice(run.status === "COMPLETE" || run.status === "COMPLETED" ? { kind: "success", text: `Watchlist ready: ${formatInteger(run.symbolsPassed)} of ${formatInteger(run.symbolsTotal)} symbols are eligible for monitoring.` } : { kind: "error", text: run.error || `Screen finished with status ${run.status}.` });
          refreshRuns();
          refreshResults();
        }
      } catch (reason) {
        if (cancelled) return;
        setPollingRunId(null);
        setRunNotice({ kind: "error", text: errorMessage(reason, "Lost track of the screener run") });
      }
    }, RUN_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pollingRunId, refreshRuns, refreshResults]);

  const selectedRun = pollingRun && pollingRun.runId === selectedRunId ? pollingRun : runs.data?.runs.find((run) => run.runId === selectedRunId) ?? results.data?.passed.run ?? null;
  const activeUniverse = universes.data?.active?.[market] ?? universes.data?.universes.find((universe) => universe.active) ?? null;
  const busy = submitting || Boolean(pollingRunId);

  const markProfileDirty = () => {
    setProfileDirty((current) => ({ ...current, [market]: true }));
    setProfileNotice(null);
  };

  const selectProfileVersion = (profileVersionId: string) => {
    if (!profileVersionId) {
      setSelectedProfileIds((current) => ({ ...current, [market]: "" }));
      setSelectedProfileVersionIds((current) => ({ ...current, [market]: "" }));
      setProfileNames((current) => ({ ...current, [market]: "" }));
      setProfileDirty((current) => ({ ...current, [market]: true }));
      setSymbolSource("market");
      setSymbolsText("");
      setFilterJsonEdits((current) => ({ ...current, [market]: JSON.stringify(compactValues(defaultFilters), null, 2) }));
      setProfileNotice(null);
      return;
    }
    const version = profileVersions.find((item) => item.profileVersionId === profileVersionId);
    if (!version) return;
    setSelectedProfileIds((current) => ({ ...current, [market]: version.profileId }));
    setSelectedProfileVersionIds((current) => ({ ...current, [market]: version.profileVersionId }));
    setProfileNames((current) => ({ ...current, [market]: version.name }));
    setProfileDirty((current) => ({ ...current, [market]: false }));
    setFilterJsonEdits((current) => ({ ...current, [market]: JSON.stringify(version.filters, null, 2) }));
    setSymbolSource(version.sourceKind === "MARKET" ? "market" : version.sourceKind === "PRESET" ? version.presetId ?? "market" : "custom");
    setSymbolsText(version.sourceKind === "CUSTOM" ? version.symbols.join(", ") : "");
    setProfileNotice(null);
  };

  const profilePayload = () => {
    const configuration = parseFilterOverrides(filterJson);
    const sourceKind = selectedPreset ? "PRESET" : effectiveSymbolSource === "custom" ? "CUSTOM" : "MARKET";
    return {
      filters: configuration,
      sourceKind,
      ...(selectedPreset ? { presetId: selectedPreset.presetId } : {}),
      ...(sourceKind === "CUSTOM" ? { symbols: parseSymbols(symbolsText) } : {}),
    };
  };

  const saveProfileVersion = async () => {
    setSavingProfile(true);
    setProfileNotice(null);
    try {
      const payload = profilePayload();
      if (payload.sourceKind === "CUSTOM" && !payload.symbols?.length) throw new Error("Enter at least one custom symbol before saving this profile.");
      let saved: WatchlistProfileVersion;
      if (selectedProfileId) {
        saved = await v2Post<WatchlistProfileVersion>(`screener/profiles/${selectedProfileId}/versions`, payload);
      } else {
        const name = profileNames[market].trim();
        if (!name) throw new Error("Enter a profile name before saving.");
        saved = await v2Post<WatchlistProfileVersion>("screener/profiles", { market, name, ...payload });
      }
      setSelectedProfileIds((current) => ({ ...current, [market]: saved.profileId }));
      setSelectedProfileVersionIds((current) => ({ ...current, [market]: saved.profileVersionId }));
      setProfileNames((current) => ({ ...current, [market]: saved.name }));
      setProfileDirty((current) => ({ ...current, [market]: false }));
      setProfileNotice({ kind: "success", text: `Saved ${saved.name} · v${saved.version}. Existing strategy watchlists were not changed.` });
      refreshProfiles();
    } catch (reason) {
      setProfileNotice({ kind: "error", text: errorMessage(reason, "The watchlist profile could not be saved") });
    } finally {
      setSavingProfile(false);
    }
  };

  const runScreener = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setRunNotice(null);
    try {
      const symbols = effectiveSymbolSource === "custom" ? parseSymbols(symbolsText) : [];
      if (effectiveSymbolSource === "custom" && !symbols.length) throw new Error("Enter at least one symbol or use a ready-made universe.");
      const configuration = parseFilterOverrides(filterJson);
      if (configuration.rankBy !== undefined && !(filters.data?.rankBy ?? []).includes(String(configuration.rankBy))) throw new Error(`filters.rankBy must be one of: ${(filters.data?.rankBy ?? []).join(", ")}.`);
      const body = selectedProfileVersionId && !profileDirty[market] ? {
        market,
        profileVersionId: selectedProfileVersionId,
      } : {
        market,
        filters: configuration,
        ...(selectedPreset ? { presetId: selectedPreset.presetId } : {}),
        ...(symbols.length ? { symbols } : {}),
      };
      const run = await v2Post<ScreenerRun>("screener/runs", body);
      setPollingRun(run);
      setPollingRunId(run.runId);
      setSelectedRunChoice(run.runId);
      setResultsTab("passed");
      refreshRuns();
    } catch (reason) {
      setRunNotice({ kind: "error", text: errorMessage(reason, "The screener run could not be started") });
    } finally {
      setSubmitting(false);
    }
  };

  const saveUniverse = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedRunId) return;
    setSavingUniverse(true);
    setUniverseNotice(null);
    try {
      const universe = await v2Post<Universe>("screener/universes", {
        runId: selectedRunId,
        name: universeName.trim(),
        manualIncludes: parseSymbols(manualIncludes),
        manualExcludes: parseSymbols(manualExcludes),
        activate: true,
      });
      setUniverseNotice({ kind: "success", text: `Saved "${universe.name}" with ${universe.symbols.length} symbols${universe.active ? " and selected it for strategies" : ""}.` });
      setUniverseName("");
      setManualIncludes("");
      setManualExcludes("");
      refreshUniverses();
    } catch (reason) {
      setUniverseNotice({ kind: "error", text: errorMessage(reason, "The watchlist could not be saved") });
    } finally {
      setSavingUniverse(false);
    }
  };

  const activate = async (universe: Universe) => {
    setActivatingId(universe.universeId);
    setUniverseNotice(null);
    try {
      await v2Post(`screener/universes/${universe.universeId}/activate`);
      setUniverseNotice({ kind: "success", text: `"${universe.name}" is now the active ${marketLabel(market)} watchlist.` });
      refreshUniverses();
    } catch (reason) {
      setUniverseNotice({ kind: "error", text: errorMessage(reason, "The watchlist could not be activated") });
    } finally {
      setActivatingId(null);
    }
  };

  const passedRows = results.data?.passed.results ?? [];
  const rejectedRows = results.data?.rejected.results ?? [];

  return <main className="quant-workspace">
    <WorkspaceHeader
      eyebrow={`NSE & Crypto watchlist · viewing ${marketLabel(market)}`}
      title="Trading watchlist"
      actions={<div className="quant-header-actions"><PaperOnlyBadge /><button type="button" onClick={() => { refreshRuns(); refreshUniverses(); refreshResults(); }}><RefreshCw size={15} />Refresh</button></div>}
    />

    <section className="quant-screener-purpose" aria-label="How the watchlist works">
      <strong>Find symbols worth monitoring—not automatic BUY or SELL calls.</strong>
      <span>The screen removes instruments that fail your price, liquidity, volatility or data-quality rules. Your selected strategies monitor the active watchlist and create actual decisions on the Signals page.</span>
      <a className="quant-action-link" href={market === "CRYPTO" ? "/signals?market=CRYPTO" : "/signals"}>View signals</a>
    </section>

    <section className="quant-overview-strip" aria-label="Watchlist overview">
      <div><span>Latest run</span><strong>{selectedRun?.status ? humanize(selectedRun.status) : "None"}</strong><small>{selectedRun ? formatDateTime(selectedRun.requestedAt, market) : "No runs yet"}</small></div>
      <div><span>Candidates</span><strong>{selectedRun ? `${formatInteger(selectedRun.symbolsPassed)} / ${formatInteger(selectedRun.symbolsTotal)}` : "—"}</strong><small>Eligible for monitoring</small></div>
      <div><span>Active watchlist</span><strong>{activeUniverse?.name ?? "None"}</strong><small>{activeUniverse ? `${activeUniverse.symbols.length} symbols` : "Create below"}</small></div>
      <div><span>Saved</span><strong>{universes.data ? formatInteger(universes.data.universes.length) : "—"}</strong><small>Watchlists</small></div>
    </section>

    <div className="quant-screener-layout">
    <div className="quant-screener-sidebar">
    <Panel icon={<ScanSearch size={17} />} title="Find candidates" description="Apply eligibility rules to a starting market list.">
      {filters.loading ? <LoadingState label="Loading filter defaults" /> : filters.error ? <RequestErrorState error={filters.error} retry={filters.reload} /> : <form onSubmit={runScreener} noValidate>
        <div className="quant-panel-body">
          <div className="quant-form-grid quant-screener-profile-grid">
            <label><span>Saved rule set</span><select aria-label="Watchlist profile version" value={selectedProfileVersionId} disabled={busy || profiles.loading} onChange={(event) => selectProfileVersion(event.target.value)}><option value="">New profile</option>{(profiles.data?.profiles ?? []).map((profile) => <optgroup key={profile.profileId} label={profile.name}>{profile.versions.map((version) => <option key={version.profileVersionId} value={version.profileVersionId}>v{version.version} · {formatDateTime(version.createdAt, market)}</option>)}</optgroup>)}</select>{profiles.error && <small>Saved profiles unavailable: {profiles.error.message}</small>}{selectedProfileVersion && <small>{profileDirty[market] ? `Modified from v${selectedProfileVersion.version}` : `Loaded v${selectedProfileVersion.version}`}</small>}</label>
            <label><span>Profile name</span><input aria-label="Watchlist profile name" value={profileNames[market]} disabled={busy || Boolean(selectedProfileId)} placeholder={market === "NSE" ? "NSE Intraday Quality" : "Crypto Liquid"} onChange={(event) => { setProfileNames((current) => ({ ...current, [market]: event.target.value })); markProfileDirty(); }} /><small>{selectedProfileId ? "Name is fixed; saving creates the next immutable version." : "Create a reusable named JSON rule set."}</small></label>
            <button type="button" className="quant-action-link" disabled={busy || savingProfile} onClick={() => void saveProfileVersion()}><Save size={15} />{savingProfile ? "Saving…" : selectedProfileId ? "Save new version" : "Save profile"}</button>
          </div>
          {profileNotice && <Message kind={profileNotice.kind}>{profileNotice.text}</Message>}
          <div className="quant-form-grid quant-screener-run-grid">
            <label><span>Starting universe</span><select value={effectiveSymbolSource} disabled={busy} onChange={(event) => { setSymbolSource(event.target.value); markProfileDirty(); }}><option value="market">Full {marketLabel(market)} market</option>{(presets.data?.presets ?? []).map((preset) => <option key={preset.presetId} value={preset.presetId}>{preset.name} ({preset.symbols.length})</option>)}<option value="custom">Custom list</option></select>{presets.error && <small>Ready-made universes unavailable: {presets.error.message}</small>}{selectedPreset && <small>Official snapshot · {selectedPreset.symbols.length} symbols · as of {selectedPreset.asOf}</small>}</label>
            {effectiveSymbolSource === "custom" && <label className="symbols"><span>Custom symbols</span><input value={symbolsText} disabled={busy} placeholder={market === "NSE" ? "RELIANCE, TCS, INFY" : "BTC-USDT, ETH-USDT"} onChange={(event) => { setSymbolsText(event.target.value); markProfileDirty(); }} /><small>{parseSymbols(symbolsText).length} symbols</small></label>}
          </div>
          <details className="quant-config-disclosure">
            <summary><span><Braces size={15} />Edit JSON</span><small>Filters and ranking</small></summary>
            <div className="quant-json-editor">
            <div className="quant-json-editor-heading"><span>Screener JSON</span><small>Price, liquidity, volatility, quality, ranking and result limit.</small></div>
              <textarea aria-label="Screener configuration JSON" spellCheck={false} value={filterJson} disabled={busy} onChange={(event) => { setFilterJsonEdits((current) => ({ ...current, [market]: event.target.value })); markProfileDirty(); }} />
              <div className="quant-backtest-config-actions">
                <button type="button" onClick={() => void navigator.clipboard.writeText(filterJson)}><Copy size={14} />Copy JSON</button>
                <button type="button" onClick={() => { try { const parsed = parseFilterOverrides(filterJson); setFilterJsonEdits((current) => ({ ...current, [market]: JSON.stringify(parsed, null, 2) })); setRunNotice(null); } catch (reason) { setRunNotice({ kind: "error", text: errorMessage(reason, "Invalid screener configuration") }); } }}>Validate and format</button>
                <button type="button" onClick={() => { setFilterJsonEdits((current) => ({ ...current, [market]: JSON.stringify(compactValues(defaultFilters), null, 2) })); markProfileDirty(); }}>Reset to defaults</button>
              </div>
            </div>
          </details>
          {runNotice && <Message kind={runNotice.kind}>{runNotice.text}</Message>}
        </div>
        <div className="quant-form-actions">
          <button type="submit" className="primary" disabled={busy}>{pollingRunId ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}{pollingRunId ? "Screening…" : "Find candidates"}</button>
          <span>{pollingRun && pollingRunId ? `Run ${shortId(pollingRun.runId)} · ${pollingRun.status} · ${formatInteger(pollingRun.symbolsTotal)} symbols` : selectedPreset?.name ?? (effectiveSymbolSource === "custom" ? `${parseSymbols(symbolsText).length} custom symbols` : `Full ${marketLabel(market)} market`)}</span>
        </div>
      </form>}
    </Panel>

    <Panel icon={<Save size={17} />} title="Use as watchlist" description="Freeze these candidates for backtests and strategy monitoring.">
      <form onSubmit={saveUniverse} noValidate>
        <div className="quant-panel-body">
          <div className="quant-form-grid quant-universe-save-grid">
            <label><span>Watchlist name</span><input type="text" required value={universeName} onChange={(event) => setUniverseName(event.target.value)} placeholder={`${marketLabel(market)} liquid candidates`} /></label>
            <label><span>Always include</span><input type="text" value={manualIncludes} onChange={(event) => setManualIncludes(event.target.value)} placeholder={market === "CRYPTO" ? "BTC-USDT, ETH-USDT" : "RELIANCE, TCS"} /><small>Optional symbols added even if they did not pass the screen</small></label>
            <label><span>Always exclude</span><input type="text" value={manualExcludes} onChange={(event) => setManualExcludes(event.target.value)} placeholder={market === "CRYPTO" ? "DOGE-USDT" : "ILLQUIDCO"} /><small>Optional symbols removed from this watchlist</small></label>
          </div>
          {universeNotice && <Message kind={universeNotice.kind}>{universeNotice.text}</Message>}
        </div>
        <div className="quant-form-actions">
          <button type="submit" className="primary" disabled={savingUniverse || !selectedRunId || !universeName.trim() || isActive(selectedRun?.status)}><Save size={15} />{savingUniverse ? "Saving…" : "Save and use for signals"}</button>
          <span>{selectedRun ? `${formatInteger(selectedRun.symbolsPassed)} candidates` : "Select a completed run"}</span>
        </div>
      </form>
    </Panel>
    </div>

    <Panel icon={<ListChecks size={17} />} title="Candidate watchlist" description="Candidates meet the eligibility rules; excluded symbols show the first failed rule." aside={<div className="quant-toolbar">
      {runs.data && runs.data.runs.length > 0 && <label><span>Run</span><select value={selectedRunId ?? ""} onChange={(event) => setSelectedRunChoice(event.target.value)}>{runs.data.runs.map((run) => <option key={run.runId} value={run.runId}>{shortId(run.runId)} · {run.status} · {formatDateTime(run.requestedAt, market)}</option>)}</select></label>}
      <div className="quant-section-tabs" role="tablist" aria-label="Result view">
        <button type="button" role="tab" aria-selected={resultsTab === "passed"} className={resultsTab === "passed" ? "active" : ""} onClick={() => setResultsTab("passed")}>Candidates ({formatInteger(passedRows.length)})</button>
        <button type="button" role="tab" aria-selected={resultsTab === "rejected"} className={resultsTab === "rejected" ? "active" : ""} onClick={() => setResultsTab("rejected")}>Excluded ({formatInteger(rejectedRows.length)})</button>
      </div>
    </div>}>
      {pollingRunId && pollingRun && <div className="quant-progress-row"><LoaderCircle className="spin" size={15} /><span>Screening {formatInteger(pollingRun.symbolsTotal)} symbols · {pollingRun.status}</span><StatusBadge tone={tone(pollingRun.status)}>{pollingRun.status}</StatusBadge></div>}
      {runs.loading || results.loading ? <LoadingState label="Loading watchlist candidates" /> : runs.error ? <RequestErrorState error={runs.error} retry={runs.reload} /> : results.error ? <RequestErrorState error={results.error} retry={results.reload} /> : !selectedRunId ? <EmptyState title="No watchlist runs yet" description="Find candidates to build the first strategy watchlist." /> : resultsTab === "passed" ? (passedRows.length ? <div className="quant-table-scroll tall"><table className="quant-table">
        <thead><tr><th className="numeric">Rank</th><th>Symbol</th><th>Watch status</th><th className="numeric">Price</th><th className="numeric">Traded value</th><th className="numeric">Volume</th><th className="numeric">Volatility</th><th className="numeric">Coverage</th><th className="numeric">Sessions</th><th className="numeric">Score</th></tr></thead>
        <tbody>{passedRows.map((row) => <tr key={row.symbol}>
          <td className="numeric">{row.rank ?? "—"}</td>
          <td><strong>{row.symbol}</strong></td><td><StatusBadge tone="neutral">Monitor</StatusBadge></td>
          <td className="numeric">{formatNumber(row.metrics?.lastPrice)}</td>
          <td className="numeric">{formatNumber(row.metrics?.averageTradedValue, 0)}</td>
          <td className="numeric">{formatNumber(row.metrics?.averageVolume, 0)}</td>
          <td className="numeric">{formatPercent(row.metrics?.volatilityPct)}</td>
          <td className="numeric">{row.metrics?.candleCoverage != null ? formatPercent(row.metrics.candleCoverage <= 1 ? row.metrics.candleCoverage * 100 : row.metrics.candleCoverage, 1) : "—"}</td>
          <td className="numeric">{formatInteger(row.metrics?.sessions)}</td>
          <td className="numeric">{formatNumber(row.score, 3)}</td>
        </tr>)}</tbody>
      </table></div> : <EmptyState title="No eligible candidates" description={selectedRun?.error || "Loosen the filters or widen the symbol list and run again."} />) : (rejectedRows.length ? <div className="quant-table-scroll tall"><table className="quant-table">
        <thead><tr><th>Symbol</th><th>Rejection reason</th><th>Details</th><th className="numeric">Price</th><th className="numeric">Traded value</th><th className="numeric">Volatility</th></tr></thead>
        <tbody>{rejectedRows.map((row) => <tr key={row.symbol}>
          <td><strong>{row.symbol}</strong></td>
          <td>{row.rejectionReason ? humanize(row.rejectionReason) : "—"}</td>
          <td>{typeof row.metrics?.error === "string" ? row.metrics.error : "—"}</td>
          <td className="numeric">{formatNumber(row.metrics?.lastPrice)}</td>
          <td className="numeric">{formatNumber(row.metrics?.averageTradedValue, 0)}</td>
          <td className="numeric">{formatPercent(row.metrics?.volatilityPct)}</td>
        </tr>)}</tbody>
      </table></div> : <EmptyState title="Nothing excluded" description="Every screened symbol met the eligibility rules." />)}
    </Panel>
    </div>

    <details className="quant-secondary-disclosure">
      <summary><span><Layers size={15} />Import TradingView watchlist</span><small>Exchange symbols are normalized to OpenDelta</small></summary>
      <form onSubmit={(event) => void importTradingView(event)}>
        <div className="quant-panel-body"><div className="quant-form-grid">
          <label><span>Watchlist name</span><input required value={importName} placeholder="TradingView shortlist" onChange={(event) => setImportName(event.target.value)} /></label>
          <label className="symbols"><span>TradingView symbols</span><textarea required value={importSymbols} placeholder={market === "CRYPTO" ? "OKX:BTCUSDT, OKX:ETHUSDT" : "NSE:RELIANCE, NSE:TCS"} onChange={(event) => setImportSymbols(event.target.value)} /><small>Paste comma, space or newline separated symbols.</small></label>
        </div>{importNotice && <Message kind={importNotice.kind}>{importNotice.text}</Message>}</div>
        <div className="quant-form-actions"><button className="primary" type="submit" disabled={importing || !importName.trim() || !importSymbols.trim()}>{importing ? "Importing…" : "Import and activate"}</button><span>No screen is run; symbols must exist in the configured market catalogue.</span></div>
      </form>
    </details>

    <Panel icon={<Layers size={17} />} title="Saved watchlists" description="The active watchlist supplies symbols to backtests and selected live strategies." aside={activeUniverse && <StatusBadge tone="good">Active: {activeUniverse.name}</StatusBadge>}>
      {universes.loading ? <LoadingState label="Loading watchlists" /> : universes.error ? <RequestErrorState error={universes.error} retry={universes.reload} /> : !universes.data?.universes.length ? <EmptyState title="No saved watchlists" description="Save candidate results above to create the first watchlist." /> : <div className="quant-table-scroll"><table className="quant-table">
        <thead><tr><th>Name</th><th className="numeric">Symbols</th><th>Includes / excludes</th><th>Created</th><th>Status</th><th className="numeric">Action</th></tr></thead>
        <tbody>{universes.data.universes.map((universe) => <tr key={universe.universeId} className={universe.active ? "active" : ""}>
          <td><strong>{universe.name}</strong><small className="mono">{shortId(universe.universeId)}</small></td>
          <td className="numeric">{formatInteger(universe.symbols.length)}</td>
          <td>{formatInteger(universe.manualIncludes?.length ?? 0)} / {formatInteger(universe.manualExcludes?.length ?? 0)}</td>
          <td>{formatDateTime(universe.createdAt, market)}</td>
          <td><StatusBadge tone={universe.active ? "good" : "neutral"}>{universe.active ? "Active" : "Saved"}</StatusBadge></td>
          <td className="numeric">{!universe.active && <button type="button" disabled={activatingId === universe.universeId} onClick={() => void activate(universe)}>{activatingId === universe.universeId ? "Activating…" : "Activate"}</button>}</td>
        </tr>)}</tbody>
      </table></div>}
    </Panel>
  </main>;
}
