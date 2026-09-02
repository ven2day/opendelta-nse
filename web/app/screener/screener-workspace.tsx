"use client";

import { Layers, ListChecks, LoaderCircle, Play, RefreshCw, Save, ScanSearch } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { formatDateTime, formatInteger, formatNumber, formatPercent, humanize, marketLabel, shortId, tone } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { compactValues, schemaDefaults, schemaFromValues, SchemaForm, type ConfigValues } from "../platform/schema-form";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type { ScreenerFiltersResponse, ScreenerResultsResponse, ScreenerRun, Universe, UniversesResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, MarketTabs, Message, PaperOnlyBadge, Panel, RequestErrorState, StatusBadge, SymbolTags, WorkspaceHeader } from "../platform/workspace-ui";

const RUN_POLL_MS = 2_000;
const ACTIVE_RUN_STATUSES = new Set(["QUEUED", "RUNNING", "PENDING"]);
type Notice = { kind: "success" | "error"; text: string } | null;

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
  const filters = useV2Resource(loadFilters);
  const runs = useV2Resource(loadRuns);
  const universes = useV2Resource(loadUniverses);
  const { refresh: refreshRuns } = runs;
  const { refresh: refreshUniverses } = universes;

  const [filterEdits, setFilterEdits] = useState<ConfigValues>({});
  const [allPassingChoice, setAllPassingChoice] = useState<boolean | null>(null);
  const [maximumSymbolsEdit, setMaximumSymbolsEdit] = useState<string | null>(null);
  const [symbolsText, setSymbolsText] = useState("");
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

  const filterSchema = useMemo(() => {
    const schema = schemaFromValues(filters.data?.defaults ?? {});
    if (schema.rankBy && filters.data?.rankBy?.length) schema.rankBy = { ...schema.rankBy, enum: filters.data.rankBy };
    delete schema.maximumSymbols;
    return schema;
  }, [filters.data]);
  const filterValues = useMemo(() => ({ ...schemaDefaults(filterSchema, filters.data?.defaults), ...filterEdits }), [filterSchema, filters.data, filterEdits]);
  const defaultMaximum = filters.data?.defaults.maximumSymbols;
  const allPassing = allPassingChoice ?? (defaultMaximum === null || defaultMaximum === undefined);
  const maximumSymbols = maximumSymbolsEdit ?? (typeof defaultMaximum === "number" ? String(defaultMaximum) : "");

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
      const symbols = parseSymbols(symbolsText);
      const body = {
        market,
        filters: { ...compactValues(filterValues), maximumSymbols: allPassing ? null : Number(maximumSymbols) },
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
      eyebrow={`${marketLabel(market)} screener`}
      title="Universe screener"
      description="Rank instruments by liquidity, price, volatility and data coverage, review what passed or was rejected, then freeze the list as the universe used by backtests and the signal engine."
      actions={<div className="quant-header-actions"><PaperOnlyBadge /><button type="button" onClick={() => { refreshRuns(); refreshUniverses(); refreshResults(); }}><RefreshCw size={15} />Refresh</button></div>}
    />
    <MarketTabs market={market} pathname="/screener" />

    <section className="quant-kpi-grid">
      <article><span>Latest run</span><strong>{selectedRun?.status ?? "None"}</strong><small>{selectedRun ? `${shortId(selectedRun.runId)} · ${formatDateTime(selectedRun.requestedAt, market)}` : "No screener runs yet"}</small></article>
      <article><span>Passed / total</span><strong>{selectedRun ? `${formatInteger(selectedRun.symbolsPassed)} / ${formatInteger(selectedRun.symbolsTotal)}` : "—"}</strong><small>Symbols in the selected run</small></article>
      <article><span>Active universe</span><strong>{activeUniverse?.name ?? "None"}</strong><small>{activeUniverse ? `${activeUniverse.symbols.length} symbols` : "Save a run to create one"}</small></article>
      <article><span>Saved universes</span><strong>{universes.data ? formatInteger(universes.data.universes.length) : "—"}</strong><small>{marketLabel(market)} only</small></article>
    </section>

    <Panel icon={<ScanSearch size={17} />} title="Screen filters" description="Defaults come from the platform; choose a maximum symbol count or keep every passing symbol.">
      {filters.loading ? <LoadingState label="Loading filter defaults" /> : filters.error ? <RequestErrorState error={filters.error} retry={filters.reload} /> : <form onSubmit={runScreener} noValidate>
        <div className="quant-panel-body">
          <SchemaForm schema={filterSchema} values={filterValues} onChange={setFilterEdits} disabled={busy} />
          <div className="quant-form-grid">
            <label className="checkbox"><input type="checkbox" checked={allPassing} disabled={busy} onChange={(event) => setAllPassingChoice(event.target.checked)} /><span>Keep all passing symbols</span></label>
            <label><span>Maximum symbols</span><input type="number" min={1} step={1} inputMode="numeric" value={allPassing ? "" : maximumSymbols} disabled={busy || allPassing} placeholder={allPassing ? "All passing" : ""} onChange={(event) => setMaximumSymbolsEdit(event.target.value)} /><small>Top-ranked symbols kept after filtering</small></label>
            <label className="span-2"><span>Symbols override (optional)</span><textarea value={symbolsText} disabled={busy} placeholder="Leave empty to screen the full instrument master, or paste symbols separated by commas or new lines." onChange={(event) => setSymbolsText(event.target.value)} /></label>
          </div>
          {runNotice && <Message kind={runNotice.kind}>{runNotice.text}</Message>}
        </div>
        <div className="quant-form-actions">
          <button type="submit" className="primary" disabled={busy}>{pollingRunId ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}{pollingRunId ? "Screening…" : "Run screener"}</button>
          <span>{pollingRun && pollingRunId ? `Run ${shortId(pollingRun.runId)} · ${pollingRun.status} · ${formatInteger(pollingRun.symbolsTotal)} symbols` : `Rank by ${String(filterValues.rankBy ?? "score")}`}</span>
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
