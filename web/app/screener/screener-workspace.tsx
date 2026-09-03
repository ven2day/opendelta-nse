"use client";

import { Layers, ListChecks, LoaderCircle, Play, RefreshCw, Save, ScanSearch, SlidersHorizontal } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { formatDateTime, formatInteger, formatNumber, formatPercent, humanize, marketLabel, shortId, tone } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { compactValues, validateConfigValues, type ConfigSchema, type ConfigValues } from "../platform/schema-form";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type { ScreenerFiltersResponse, ScreenerResultsResponse, ScreenerRun, Universe, UniversePresetsResponse, UniversesResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, Message, PaperOnlyBadge, Panel, RequestErrorState, StatusBadge, SymbolTags, WorkspaceHeader } from "../platform/workspace-ui";

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
  const filters = useV2Resource(loadFilters);
  const runs = useV2Resource(loadRuns);
  const universes = useV2Resource(loadUniverses);
  const presets = useV2Resource(loadPresets);
  const { refresh: refreshRuns } = runs;
  const { refresh: refreshUniverses } = universes;

  const [rankChoice, setRankChoice] = useState<string | null>(null);
  const [resultLimitChoice, setResultLimitChoice] = useState<string | null>(null);
  const [symbolSource, setSymbolSource] = useState("market");
  const [symbolsText, setSymbolsText] = useState("");
  const [filterJsonEdits, setFilterJsonEdits] = useState<Record<PlatformMarket, string>>({ NSE: "", CRYPTO: "" });
  const [submitting, setSubmitting] = useState(false);
  const [runNotice, setRunNotice] = useState<Notice>(null);
  const [pollingRunId, setPollingRunId] = useState<string | null>(null);
  const [pollingRun, setPollingRun] = useState<ScreenerRun | null>(null);
  const [selectedRunChoice, setSelectedRunChoice] = useState<string | null>(null);
  const [resultsTab, setResultsTab] = useState<"passed" | "rejected">("passed");
  const [universeName, setUniverseName] = useState("");
  const [universeLimit, setUniverseLimit] = useState("");
  const [includesText, setIncludesText] = useState("");
  const [excludesText, setExcludesText] = useState("");
  const [activateUniverse, setActivateUniverse] = useState(true);
  const [savingUniverse, setSavingUniverse] = useState(false);
  const [universeNotice, setUniverseNotice] = useState<Notice>(null);
  const [activatingId, setActivatingId] = useState<string | null>(null);

  const defaultFilters = filters.data?.defaults ?? {};
  const defaultMaximum = filters.data?.defaults.maximumSymbols;
  const rankBy = rankChoice ?? String(defaultFilters.rankBy ?? filters.data?.rankBy?.[0] ?? "liquidity");
  const resultLimit = resultLimitChoice ?? (typeof defaultMaximum === "number" ? String(defaultMaximum) : "all");
  const filterJson = filterJsonEdits[market];
  const hasFilterOverride = filterJson.trim() !== "" && filterJson.trim() !== "{}";
  const selectedPreset = presets.data?.presets.find((preset) => preset.presetId === symbolSource) ?? null;
  const effectiveSymbolSource = symbolSource === "market" || symbolSource === "custom" || selectedPreset ? symbolSource : "market";

  const selectedRunId = selectedRunChoice ?? runs.data?.runs[0]?.runId ?? null;
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
          setRunNotice(run.status === "COMPLETE" || run.status === "COMPLETED" ? { kind: "success", text: `Screen complete: ${formatInteger(run.symbolsPassed)} of ${formatInteger(run.symbolsTotal)} symbols passed.` } : { kind: "error", text: run.error || `Screen finished with status ${run.status}.` });
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

  const runScreener = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setRunNotice(null);
    try {
      const symbols = effectiveSymbolSource === "custom" ? parseSymbols(symbolsText) : [];
      if (effectiveSymbolSource === "custom" && !symbols.length) throw new Error("Enter at least one symbol or use a ready-made universe.");
      const overrides = hasFilterOverride ? parseFilterOverrides(filterJson) : {};
      if (overrides.rankBy !== undefined && !(filters.data?.rankBy ?? []).includes(String(overrides.rankBy))) throw new Error(`filters.rankBy must be one of: ${(filters.data?.rankBy ?? []).join(", ")}.`);
      const body = {
        market,
        filters: {
          ...compactValues(defaultFilters),
          rankBy,
          maximumSymbols: resultLimit === "all" ? null : Number(resultLimit),
          ...overrides,
        },
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
        ...(universeLimit.trim() ? { maximumSymbols: Number(universeLimit) } : {}),
        manualIncludes: parseSymbols(includesText),
        manualExcludes: parseSymbols(excludesText),
        activate: activateUniverse,
      });
      setUniverseNotice({ kind: "success", text: `Saved "${universe.name}" with ${universe.symbols.length} symbols${universe.active ? " and activated it" : ""}.` });
      setUniverseName("");
      refreshUniverses();
    } catch (reason) {
      setUniverseNotice({ kind: "error", text: errorMessage(reason, "The universe could not be saved") });
    } finally {
      setSavingUniverse(false);
    }
  };

  const activate = async (universe: Universe) => {
    setActivatingId(universe.universeId);
    setUniverseNotice(null);
    try {
      await v2Post(`screener/universes/${universe.universeId}/activate`);
      setUniverseNotice({ kind: "success", text: `"${universe.name}" is now the active ${marketLabel(market)} universe.` });
      refreshUniverses();
    } catch (reason) {
      setUniverseNotice({ kind: "error", text: errorMessage(reason, "The universe could not be activated") });
    } finally {
      setActivatingId(null);
    }
  };

  const passedRows = results.data?.passed.results ?? [];
  const rejectedRows = results.data?.rejected.results ?? [];

  return <main className="quant-workspace">
    <WorkspaceHeader
      eyebrow={`NSE & Crypto screener · viewing ${marketLabel(market)}`}
      title="Universe screener"
      actions={<div className="quant-header-actions"><PaperOnlyBadge /><button type="button" onClick={() => { refreshRuns(); refreshUniverses(); refreshResults(); }}><RefreshCw size={15} />Refresh</button></div>}
    />

    <section className="quant-kpi-grid">
      <article><span>Latest run</span><strong>{selectedRun?.status ?? "None"}</strong><small>{selectedRun ? `${shortId(selectedRun.runId)} · ${formatDateTime(selectedRun.requestedAt, market)}` : "No screener runs yet"}</small></article>
      <article><span>Passed / total</span><strong>{selectedRun ? `${formatInteger(selectedRun.symbolsPassed)} / ${formatInteger(selectedRun.symbolsTotal)}` : "—"}</strong><small>Symbols in the selected run</small></article>
      <article><span>Active universe</span><strong>{activeUniverse?.name ?? "None"}</strong><small>{activeUniverse ? `${activeUniverse.symbols.length} symbols` : "Save a run to create one"}</small></article>
      <article><span>Saved universes</span><strong>{universes.data ? formatInteger(universes.data.universes.length) : "—"}</strong><small>NSE & Crypto supported · viewing {marketLabel(market)}</small></article>
    </section>

    <div className="quant-screener-layout">
    <Panel icon={<ScanSearch size={17} />} title="Run screener">
      {filters.loading ? <LoadingState label="Loading filter defaults" /> : filters.error ? <RequestErrorState error={filters.error} retry={filters.reload} /> : <form onSubmit={runScreener} noValidate>
        <div className="quant-panel-body">
          <div className="quant-form-grid quant-screener-run-grid">
            <label><span>Rank by</span><select value={rankBy} disabled={busy} onChange={(event) => setRankChoice(event.target.value)}>{(filters.data?.rankBy ?? []).map((option) => <option key={option} value={option}>{humanize(option)}</option>)}</select></label>
            <label><span>Keep results</span><select value={resultLimit} disabled={busy} onChange={(event) => setResultLimitChoice(event.target.value)}><option value="all">All passing</option><option value="50">Top 50</option><option value="100">Top 100</option><option value="300">Top 300</option>{typeof defaultMaximum === "number" && ![50, 100, 300].includes(defaultMaximum) && <option value={String(defaultMaximum)}>Default ({defaultMaximum})</option>}</select></label>
            <label><span>Starting universe</span><select value={effectiveSymbolSource} disabled={busy} onChange={(event) => setSymbolSource(event.target.value)}><option value="market">Full {marketLabel(market)} market</option>{(presets.data?.presets ?? []).map((preset) => <option key={preset.presetId} value={preset.presetId}>{preset.name} ({preset.symbols.length})</option>)}<option value="custom">Custom list</option></select>{presets.error && <small>Ready-made universes unavailable: {presets.error.message}</small>}{selectedPreset && <small>Official snapshot · {selectedPreset.symbols.length} symbols · as of {selectedPreset.asOf}</small>}</label>
            {effectiveSymbolSource === "custom" && <label className="symbols"><span>Custom symbols</span><input value={symbolsText} disabled={busy} placeholder={market === "NSE" ? "RELIANCE, TCS, INFY" : "BTC-USDT, ETH-USDT"} onChange={(event) => setSymbolsText(event.target.value)} /><small>{parseSymbols(symbolsText).length} symbols</small></label>}
          </div>
          <details className="quant-backtest-config">
            <summary><span><SlidersHorizontal size={14} />Screener configuration</span><StatusBadge tone={hasFilterOverride ? "warn" : "neutral"}>{hasFilterOverride ? "Custom JSON" : "Defaults"}</StatusBadge></summary>
            <div className="quant-backtest-config-body">
              <div><strong>Platform defaults</strong><p>Add only the filters you want to override. The options above are applied automatically.</p></div>
              <label><span>JSON override</span><textarea aria-label="Screener configuration JSON" spellCheck={false} value={filterJson} disabled={busy} placeholder={'{\n  "minimumPrice": 500,\n  "maximumPrice": 2000\n}'} onChange={(event) => setFilterJsonEdits((current) => ({ ...current, [market]: event.target.value }))} /></label>
              <div className="quant-backtest-config-actions">
                <button type="button" onClick={() => { try { const parsed = parseFilterOverrides(filterJson || "{}"); setFilterJsonEdits((current) => ({ ...current, [market]: JSON.stringify(parsed, null, 2) })); setRunNotice(null); } catch (reason) { setRunNotice({ kind: "error", text: errorMessage(reason, "Invalid screener configuration") }); } }}>Format JSON</button>
                <button type="button" onClick={() => setFilterJsonEdits((current) => ({ ...current, [market]: "" }))}>Use defaults</button>
              </div>
            </div>
          </details>
          {runNotice && <Message kind={runNotice.kind}>{runNotice.text}</Message>}
        </div>
        <div className="quant-form-actions">
          <button type="submit" className="primary" disabled={busy}>{pollingRunId ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}{pollingRunId ? "Screening…" : "Run screener"}</button>
          <span>{pollingRun && pollingRunId ? `Run ${shortId(pollingRun.runId)} · ${pollingRun.status} · ${formatInteger(pollingRun.symbolsTotal)} symbols` : `${selectedPreset?.name ?? (effectiveSymbolSource === "custom" ? `${parseSymbols(symbolsText).length} custom symbols` : `Full ${marketLabel(market)} market`)} · ${humanize(rankBy)} · ${resultLimit === "all" ? "all passing" : `top ${resultLimit}`}`}</span>
        </div>
      </form>}
    </Panel>

    <Panel icon={<ListChecks size={17} />} title="Results" description="Passed symbols carry their rank and metrics; rejected symbols show the first failing rule." aside={<div className="quant-toolbar">
      {runs.data && runs.data.runs.length > 0 && <label><span>Run</span><select value={selectedRunId ?? ""} onChange={(event) => setSelectedRunChoice(event.target.value)}>{runs.data.runs.map((run) => <option key={run.runId} value={run.runId}>{shortId(run.runId)} · {run.status} · {formatDateTime(run.requestedAt, market)}</option>)}</select></label>}
      <div className="quant-section-tabs" role="tablist" aria-label="Result view">
        <button type="button" role="tab" aria-selected={resultsTab === "passed"} className={resultsTab === "passed" ? "active" : ""} onClick={() => setResultsTab("passed")}>Passed ({formatInteger(passedRows.length)})</button>
        <button type="button" role="tab" aria-selected={resultsTab === "rejected"} className={resultsTab === "rejected" ? "active" : ""} onClick={() => setResultsTab("rejected")}>Rejected ({formatInteger(rejectedRows.length)})</button>
      </div>
    </div>}>
      {pollingRunId && pollingRun && <div className="quant-progress-row"><LoaderCircle className="spin" size={15} /><span>Screening {formatInteger(pollingRun.symbolsTotal)} symbols · {pollingRun.status}</span><StatusBadge tone={tone(pollingRun.status)}>{pollingRun.status}</StatusBadge></div>}
      {runs.loading || results.loading ? <LoadingState label="Loading screener results" /> : runs.error ? <RequestErrorState error={runs.error} retry={runs.reload} /> : results.error ? <RequestErrorState error={results.error} retry={results.reload} /> : !selectedRunId ? <EmptyState title="No screener runs yet" description="Run the screener to rank the instrument universe." /> : resultsTab === "passed" ? (passedRows.length ? <div className="quant-table-scroll tall"><table className="quant-table">
        <thead><tr><th className="numeric">Rank</th><th>Symbol</th><th className="numeric">Price</th><th className="numeric">Traded value</th><th className="numeric">Volume</th><th className="numeric">Volatility</th><th className="numeric">Coverage</th><th className="numeric">Sessions</th><th className="numeric">Score</th></tr></thead>
        <tbody>{passedRows.map((row) => <tr key={row.symbol}>
          <td className="numeric">{row.rank ?? "—"}</td>
          <td><strong>{row.symbol}</strong></td>
          <td className="numeric">{formatNumber(row.metrics?.lastPrice)}</td>
          <td className="numeric">{formatNumber(row.metrics?.averageTradedValue, 0)}</td>
          <td className="numeric">{formatNumber(row.metrics?.averageVolume, 0)}</td>
          <td className="numeric">{formatPercent(row.metrics?.volatilityPct)}</td>
          <td className="numeric">{row.metrics?.candleCoverage != null ? formatPercent(row.metrics.candleCoverage <= 1 ? row.metrics.candleCoverage * 100 : row.metrics.candleCoverage, 1) : "—"}</td>
          <td className="numeric">{formatInteger(row.metrics?.sessions)}</td>
          <td className="numeric">{formatNumber(row.score, 3)}</td>
        </tr>)}</tbody>
      </table></div> : <EmptyState title="No symbols passed" description={selectedRun?.error || "Loosen the filters or widen the symbol list and run again."} />) : (rejectedRows.length ? <div className="quant-table-scroll tall"><table className="quant-table">
        <thead><tr><th>Symbol</th><th>Rejection reason</th><th className="numeric">Price</th><th className="numeric">Traded value</th><th className="numeric">Volatility</th></tr></thead>
        <tbody>{rejectedRows.map((row) => <tr key={row.symbol}>
          <td><strong>{row.symbol}</strong></td>
          <td>{row.rejectionReason ? humanize(row.rejectionReason) : "—"}</td>
          <td className="numeric">{formatNumber(row.metrics?.lastPrice)}</td>
          <td className="numeric">{formatNumber(row.metrics?.averageTradedValue, 0)}</td>
          <td className="numeric">{formatPercent(row.metrics?.volatilityPct)}</td>
        </tr>)}</tbody>
      </table></div> : <EmptyState title="Nothing rejected" description="Every screened symbol passed the filters." />)}
    </Panel>
    </div>

    <Panel icon={<Save size={17} />} title="Save as universe" description="Freeze the selected run as a named universe. Manual includes and excludes are applied on top of the ranked list.">
      <form onSubmit={saveUniverse} noValidate>
        <div className="quant-panel-body">
          <div className="quant-form-grid">
            <label><span>Universe name</span><input type="text" required value={universeName} onChange={(event) => setUniverseName(event.target.value)} placeholder={`${marketLabel(market)} liquid universe`} /></label>
            <label><span>Maximum symbols</span><input type="number" min={1} step={1} inputMode="numeric" value={universeLimit} placeholder="All passing" onChange={(event) => setUniverseLimit(event.target.value)} /><small>Leave empty to keep every passing symbol</small></label>
            <label><span>Manual includes</span><input type="text" value={includesText} onChange={(event) => setIncludesText(event.target.value)} placeholder="SYMBOL, SYMBOL" /></label>
            <label><span>Manual excludes</span><input type="text" value={excludesText} onChange={(event) => setExcludesText(event.target.value)} placeholder="SYMBOL, SYMBOL" /></label>
            <label className="checkbox"><input type="checkbox" checked={activateUniverse} onChange={(event) => setActivateUniverse(event.target.checked)} /><span>Activate immediately for {marketLabel(market)}</span></label>
          </div>
          {universeNotice && <Message kind={universeNotice.kind}>{universeNotice.text}</Message>}
        </div>
        <div className="quant-form-actions">
          <button type="submit" className="primary" disabled={savingUniverse || !selectedRunId || !universeName.trim() || isActive(selectedRun?.status)}><Save size={15} />{savingUniverse ? "Saving…" : "Save universe"}</button>
          <span>{selectedRun ? `From run ${shortId(selectedRun.runId)} (${formatInteger(selectedRun.symbolsPassed)} passed)` : "Select a completed run first"}</span>
        </div>
      </form>
    </Panel>

    <Panel icon={<Layers size={17} />} title="Saved universes" description="The active universe is the default symbol source for backtests and the live signal engine." aside={activeUniverse && <StatusBadge tone="good">Active: {activeUniverse.name}</StatusBadge>}>
      {universes.loading ? <LoadingState label="Loading universes" /> : universes.error ? <RequestErrorState error={universes.error} retry={universes.reload} /> : !universes.data?.universes.length ? <EmptyState title="No saved universes" description="Save a screener run above to create the first universe." /> : <div className="quant-table-scroll"><table className="quant-table">
        <thead><tr><th>Name</th><th className="numeric">Symbols</th><th>Includes / excludes</th><th>Created</th><th>Status</th><th>Symbols</th><th></th></tr></thead>
        <tbody>{universes.data.universes.map((universe) => <tr key={universe.universeId} className={universe.active ? "active" : ""}>
          <td><strong>{universe.name}</strong><small className="mono">{shortId(universe.universeId)}</small></td>
          <td className="numeric">{formatInteger(universe.symbols.length)}</td>
          <td>{formatInteger(universe.manualIncludes?.length ?? 0)} / {formatInteger(universe.manualExcludes?.length ?? 0)}</td>
          <td>{formatDateTime(universe.createdAt, market)}</td>
          <td><StatusBadge tone={universe.active ? "good" : "neutral"}>{universe.active ? "Active" : "Saved"}</StatusBadge></td>
          <td><SymbolTags symbols={universe.symbols} limit={12} /></td>
          <td>{!universe.active && <button type="button" disabled={activatingId === universe.universeId} onClick={() => void activate(universe)}>{activatingId === universe.universeId ? "Activating…" : "Activate"}</button>}</td>
        </tr>)}</tbody>
      </table></div>}
    </Panel>
  </main>;
}
