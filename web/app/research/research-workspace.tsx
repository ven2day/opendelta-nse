"use client";

import { Copy, FlaskConical, LoaderCircle, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useMemo, useState, type FormEvent } from "react";
import { formatDateTime, isoDate, marketLabel, shortId, tone } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type { ResearchExperiment, ResearchExperimentsResponse, StrategiesResponse, StrategySourcesResponse, UniversesResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, Message, Panel, RequestErrorState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

type DraftVariant = { id: string; name: string; json: string };
const EMPTY_VARIANT = JSON.stringify({ strategy: {}, execution: {} }, null, 2);

function newVariant(index: number): DraftVariant {
  return { id: crypto.randomUUID(), name: `Variant ${index}`, json: EMPTY_VARIANT };
}

function parseVariant(text: string): { strategy: Record<string, unknown>; execution: Record<string, unknown> } {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Each variant must be a JSON object.");
  const record = value as Record<string, unknown>;
  const strategy = record.strategy ?? {};
  const execution = record.execution ?? {};
  if (!strategy || typeof strategy !== "object" || Array.isArray(strategy)) throw new Error("variant.strategy must be an object.");
  if (!execution || typeof execution !== "object" || Array.isArray(execution)) throw new Error("variant.execution must be an object.");
  return { strategy: strategy as Record<string, unknown>, execution: execution as Record<string, unknown> };
}

export function ResearchWorkspace({ market }: { market: PlatformMarket }) {
  const strategies = useV2Resource(useCallback(() => v2Get<StrategiesResponse>("strategies", { market }), [market]));
  const sources = useV2Resource(useCallback(() => v2Get<StrategySourcesResponse>("strategy-studio/sources", { market, status: "VALIDATED" }), [market]));
  const universes = useV2Resource(useCallback(() => v2Get<UniversesResponse>("screener/universes", { market }), [market]));
  const experiments = useV2Resource(useCallback(() => v2Get<ResearchExperimentsResponse>("research/experiments", { market }), [market]), 5_000);
  const options = useMemo(() => [
    ...(strategies.data?.strategies ?? []).filter((item) => item.supportedMarkets.includes(market)).map((item) => ({ key: `builtin:${item.strategyId}`, id: item.strategyId, sourceId: null as string | null, name: item.name, version: item.version, timeframes: item.supportedTimeframes })),
    ...(sources.data?.sources ?? []).map((item) => ({ key: `v2:${item.sourceId}`, id: item.strategyId, sourceId: item.sourceId, name: `${item.name} (V2)`, version: item.strategyVersion, timeframes: item.manifest.supportedTimeframes })),
  ], [market, sources.data, strategies.data]);
  const [strategyKey, setStrategyKey] = useState("");
  const selected = options.find((item) => item.key === strategyKey) ?? options[0] ?? null;
  const [name, setName] = useState("Controlled strategy experiment");
  const [timeframe, setTimeframe] = useState("5m");
  const [startDate, setStartDate] = useState(() => isoDate(new Date(Date.now() - 60 * 86_400_000)));
  const [endDate, setEndDate] = useState(() => isoDate(new Date()));
  const [variants, setVariants] = useState<DraftVariant[]>(() => [newVariant(1), newVariant(2)]);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const activeUniverse = universes.data?.active?.[market] ?? universes.data?.universes.find((item) => item.active) ?? null;
  const allowedTimeframes = selected?.timeframes ?? ["5m"];
  const effectiveTimeframe = allowedTimeframes.includes(timeframe) ? timeframe : allowedTimeframes[0];

  const submit = async (event: FormEvent) => {
    event.preventDefault(); setSubmitting(true); setNotice(null);
    try {
      if (!selected) throw new Error("Select a strategy.");
      if (!activeUniverse?.symbols.length) throw new Error("Save or activate a watchlist before creating an experiment.");
      if (startDate > endDate) throw new Error("Start date must not be after end date.");
      const payloadVariants = variants.map((variant) => ({ name: variant.name.trim(), ...parseVariant(variant.json) }));
      if (payloadVariants.some((variant) => !variant.name)) throw new Error("Every variant needs a name.");
      const created = await v2Post<ResearchExperiment>("research/experiments", {
        name: name.trim(), market, strategyId: selected.id, strategySourceId: selected.sourceId ?? undefined,
        symbols: activeUniverse.symbols, timeframe: effectiveTimeframe, startDate, endDate,
        variants: payloadVariants.map((item) => ({ name: item.name, configuration: item.strategy, execution: item.execution })),
      });
      setNotice({ kind: "success", text: `Experiment ${shortId(created.experimentId)} queued with ${created.variants.length} immutable variants.` });
      experiments.refresh();
    } catch (reason) { setNotice({ kind: "error", text: errorMessage(reason, "The experiment could not be started") }); }
    finally { setSubmitting(false); }
  };

  return <main className="quant-workspace research-lab">
    <WorkspaceHeader eyebrow={`${marketLabel(market)} research`} title="Research Lab" actions={<button type="button" onClick={experiments.refresh}><RefreshCw size={15} />Refresh</button>} />
    <Panel icon={<FlaskConical size={17} />} title="New controlled experiment" description="Every variant pins the same strategy version, market, timeframe, watchlist and date range. Only JSON parameters change.">
      {strategies.loading || universes.loading || sources.loading ? <LoadingState label="Loading research inputs" /> : strategies.error ? <RequestErrorState error={strategies.error} retry={strategies.reload} /> : <form onSubmit={submit}>
        <div className="quant-panel-body">
          <div className="quant-form-grid research-experiment-grid">
            <label><span>Experiment name</span><input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} /></label>
            <label><span>Strategy version</span><select value={selected?.key ?? ""} onChange={(event) => setStrategyKey(event.target.value)}>{options.map((item) => <option key={item.key} value={item.key}>{item.name} · v{item.version}</option>)}</select></label>
            <label><span>Timeframe</span><select value={effectiveTimeframe} onChange={(event) => setTimeframe(event.target.value)}>{allowedTimeframes.map((item) => <option key={item}>{item}</option>)}</select></label>
            <label><span>Start date</span><input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
            <label><span>End date</span><input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
          </div>
          <p className="quant-inline-note">Watchlist: {activeUniverse ? `${activeUniverse.name} · ${activeUniverse.symbols.length} symbols` : "No active watchlist"}. Research experiments cannot approve or deploy strategies.</p>
          <div className="research-variant-list">{variants.map((variant, index) => <section className="research-variant" key={variant.id}>
            <header><input aria-label={`Variant ${index + 1} name`} value={variant.name} maxLength={80} onChange={(event) => setVariants((items) => items.map((item) => item.id === variant.id ? { ...item, name: event.target.value } : item))} /><div><button type="button" onClick={() => setVariants((items) => [...items, { ...newVariant(items.length + 1), json: variant.json }])} disabled={variants.length >= 12}><Copy size={13} />Duplicate</button><button type="button" onClick={() => setVariants((items) => items.filter((item) => item.id !== variant.id))} disabled={variants.length === 1}><Trash2 size={13} />Remove</button></div></header>
            <textarea aria-label={`${variant.name} JSON`} spellCheck={false} value={variant.json} onChange={(event) => setVariants((items) => items.map((item) => item.id === variant.id ? { ...item, json: event.target.value } : item))} />
          </section>)}</div>
          <div className="quant-form-actions"><button type="button" onClick={() => setVariants((items) => [...items, newVariant(items.length + 1)])} disabled={variants.length >= 12}><Plus size={14} />Add variant</button><button className="primary" type="submit" disabled={submitting || !selected}>{submitting ? <LoaderCircle className="spin" size={15} /> : <FlaskConical size={15} />}{submitting ? "Queuing…" : `Run ${variants.length} variants`}</button></div>
          {notice && <Message kind={notice.kind}>{notice.text}</Message>}
        </div>
      </form>}
    </Panel>
    <Panel icon={<FlaskConical size={17} />} title="Experiments" description="Status refreshes every five seconds while variants run.">
      {experiments.loading ? <LoadingState label="Loading experiments" /> : experiments.error ? <RequestErrorState error={experiments.error} retry={experiments.reload} /> : !experiments.data?.experiments.length ? <EmptyState title="No research experiments" description="Create controlled variants above." /> : <div className="research-experiment-list">{experiments.data.experiments.map((experiment) => <article key={experiment.experimentId} className="research-experiment-card"><header><div><strong>{experiment.name}</strong><small>{experiment.strategyId} v{experiment.strategyVersion} · {experiment.timeframe} · {experiment.symbols.length} symbols · {experiment.startDate} → {experiment.endDate}</small></div><StatusBadge tone={tone(experiment.status)}>{experiment.status}</StatusBadge></header><div className="research-run-grid">{experiment.variants.map((variant) => <div key={variant.variantId}><span>{variant.name}</span><StatusBadge tone={tone(variant.run.status)}>{variant.run.status}</StatusBadge><small>{shortId(variant.run.runId)} · created {formatDateTime(variant.run.createdAt, market)}</small></div>)}</div></article>)}</div>}
    </Panel>
  </main>;
}
