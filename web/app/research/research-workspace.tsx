"use client";

import {
  ArrowDown, ArrowUp, ChevronsUpDown, Copy, FlaskConical, LoaderCircle,
  Plus, RefreshCw, RotateCcw, Square, Trash2,
} from "lucide-react";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import {
  formatDateTime, formatInteger, formatMinutes, formatMoney, formatPercent,
  isoDate, marketLabel, shortId, tone,
} from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { schemaFromValues, type ConfigSchema, type ConfigValues } from "../platform/schema-form";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Delete, v2Get, v2Post } from "../platform/v2-client";
import type {
  BacktestTrade, BacktestTradesResponse, ResearchExperiment, ResearchExperimentPreview,
  ResearchExperimentsResponse, ResearchGenerationMode, ResearchParameterDefinition,
  ResearchVariant, StrategiesResponse, StrategySourcesResponse, UniversePresetsResponse,
  UniversesResponse,
} from "../platform/v2-types";
import {
  EmptyState, LoadingState, Message, Panel, RequestErrorState, StatusBadge, WorkspaceHeader,
} from "../platform/workspace-ui";
import { WalkForwardSection } from "./walk-forward-section";

const PREVIEW_PAGE_SIZE = 10;
const MAX_VISIBLE_CURVES = 8;
const MAX_MANUAL_VARIANTS = 20;
const MAX_SWEEP_PARAMETERS = 8;
const ACTIVE_STATUSES = new Set(["QUEUED", "RUNNING"]);

const EXECUTION_FALLBACK: ConfigSchema = {
  targetPct: { type: "number", default: null, minimum: 0.00000001, label: "Target %" },
  stopLossPct: { type: "number", default: null, minimum: 0.00000001, maximum: 99.99999999, label: "Stop loss %" },
  maximumHoldingBars: { type: "integer", default: null, minimum: 1, label: "Maximum holding bars" },
  initialQuantity: { type: "number", default: 100, minimum: 0.00000001, label: "Initial quantity" },
  allowAdditionalBuys: { type: "boolean", default: true, label: "Allow additional buys" },
  additionalQuantityPct: { type: "number", default: 50, minimum: 0.00000001, maximum: 100, label: "Additional quantity %" },
  additionalSizingMode: { type: "string", default: "REDUCE_EVERY_NEW_LOT", enum: ["REDUCE_EVERY_NEW_LOT", "FIXED_PERCENTAGE_OF_FIRST_LOT"], label: "Additional sizing mode" },
  minimumQuantity: { type: "number", default: 1, minimum: 0.00000001, label: "Minimum quantity" },
  maximumEntriesPerCycle: { type: "integer", default: 10, minimum: 1, maximum: 100, label: "Maximum entries per cycle" },
  batchSize: { type: "integer", default: 500, minimum: 1, label: "Candle batch size" },
};

type DraftVariant = { id: string; name: string; json: string };
type DraftParameter = {
  id: string;
  section: "strategy" | "execution";
  parameter: string;
  type: ResearchParameterDefinition["type"];
  method: ResearchParameterDefinition["method"];
  valuesText: string;
  minimum: string;
  maximum: string;
  step: string;
  fixedText: string;
};
type StrategyOption = {
  key: string; id: string; sourceId: string | null; name: string; version: string;
  timeframes: string[]; schema: ConfigSchema;
};
type VariantTrades = { variant: ResearchVariant; trades: BacktestTrade[]; total: number };
type ComparisonSort = "netPnl" | "drawdown" | "winRate" | "costs" | "trades" | "exposure" | "name";
type SortDirection = "asc" | "desc";
type Ranking = "NET_PNL" | "RETURN_DRAWDOWN" | "LOWEST_DRAWDOWN" | "HIGHEST_WIN_RATE" | "NONE";

function newVariant(index: number): DraftVariant {
  return {
    id: crypto.randomUUID(),
    name: `Variant ${index}`,
    json: JSON.stringify({ strategy: {}, execution: {} }, null, 2),
  };
}

function parseVariant(text: string): { strategy: ConfigValues; execution: ConfigValues } {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Each variant must be a JSON object.");
  const record = value as Record<string, unknown>;
  const unknown = Object.keys(record).filter((key) => !["strategy", "execution"].includes(key));
  if (unknown.length) throw new Error(`Unknown variant section: ${unknown.join(", ")}.`);
  const strategy = record.strategy ?? {};
  const execution = record.execution ?? {};
  if (!strategy || typeof strategy !== "object" || Array.isArray(strategy)) throw new Error("variant.strategy must be an object.");
  if (!execution || typeof execution !== "object" || Array.isArray(execution)) throw new Error("variant.execution must be an object.");
  return { strategy: strategy as ConfigValues, execution: execution as ConfigValues };
}

function supportedType(definition: ConfigSchema[string]): ResearchParameterDefinition["type"] | null {
  if (definition.enum?.length) return "enum";
  return ["number", "integer", "boolean"].includes(definition.type)
    ? definition.type as ResearchParameterDefinition["type"]
    : null;
}

function newParameter(section: DraftParameter["section"], parameter: string, definition: ConfigSchema[string]): DraftParameter {
  const type = supportedType(definition) ?? "number";
  const defaultValue = definition.default ?? (type === "boolean" ? true : definition.enum?.[0] ?? definition.minimum ?? 1);
  const minimum = definition.minimum ?? (typeof defaultValue === "number" ? defaultValue : 0);
  const maximum = definition.maximum ?? (typeof minimum === "number" ? minimum + 2 : 2);
  return {
    id: crypto.randomUUID(), section, parameter, type, method: "EXPLICIT_VALUES",
    valuesText: JSON.stringify(definition.enum ?? (type === "boolean" ? [true, false] : [defaultValue])),
    minimum: String(minimum), maximum: String(maximum), step: type === "integer" ? "1" : "0.1",
    fixedText: JSON.stringify(defaultValue),
  };
}

function parseDraftParameter(row: DraftParameter): ResearchParameterDefinition {
  const common = { section: row.section, parameter: row.parameter, type: row.type, method: row.method };
  if (row.method === "EXPLICIT_VALUES") {
    const values: unknown = JSON.parse(row.valuesText);
    if (!Array.isArray(values)) throw new Error(`${row.parameter} values must be a JSON array.`);
    return { ...common, values };
  }
  if (row.method === "NUMERIC_RANGE") {
    return { ...common, minimum: Number(row.minimum), maximum: Number(row.maximum), step: Number(row.step) };
  }
  return { ...common, value: JSON.parse(row.fixedText) };
}

function generatedValueCount(row: DraftParameter): string {
  try {
    if (row.method === "FIXED") return "1";
    if (row.method === "EXPLICIT_VALUES") {
      const values: unknown = JSON.parse(row.valuesText);
      return Array.isArray(values) ? String(values.length) : "—";
    }
    const minimum = Number(row.minimum), maximum = Number(row.maximum), step = Number(row.step);
    if (![minimum, maximum, step].every(Number.isFinite) || step <= 0 || minimum > maximum) return "—";
    return String(Math.floor((maximum - minimum) / step + 1e-10) + 1);
  } catch { return "—"; }
}

function cumulativeCurve(trades: BacktestTrade[]): number[] {
  let equity = 0;
  return trades
    .filter((trade) => trade.exitTimestamp)
    .sort((a, b) => String(a.exitTimestamp).localeCompare(String(b.exitTimestamp)))
    .map((trade) => { equity += trade.netPnl ?? 0; return equity; });
}

function stableColour(id: string): string {
  const colours = ["#4ea1ff", "#ff9f43", "#47c98b", "#c084fc", "#f87171", "#22d3ee", "#facc15", "#fb7185"];
  const hash = Array.from(id).reduce((value, character) => ((value * 31) + character.charCodeAt(0)) >>> 0, 7);
  return colours[hash % colours.length];
}

function differingKeys(values: ConfigValues[]): string[] {
  const keys = [...new Set(values.flatMap((value) => Object.keys(value)))].sort();
  return keys.filter((key) => new Set(values.map((value) => JSON.stringify(value[key]))).size > 1);
}

function EquityComparison({ rows, selectedIds, setSelectedIds }: {
  rows: VariantTrades[];
  selectedIds: string[];
  setSelectedIds: (ids: string[]) => void;
}) {
  const availableIds = new Set(rows.map((row) => row.variant.variantId));
  const retainedIds = selectedIds.filter((id) => availableIds.has(id)).slice(0, MAX_VISIBLE_CURVES);
  const effectiveIds = retainedIds.length
    ? retainedIds
    : rows.filter((row) => row.variant.run.status === "COMPLETE").slice(0, MAX_VISIBLE_CURVES).map((row) => row.variant.variantId);
  const selected = rows.filter((row) => effectiveIds.includes(row.variant.variantId));
  const curves = selected.map((row) => ({ id: row.variant.variantId, name: row.variant.name, values: cumulativeCurve(row.trades) }));
  const values = curves.flatMap((curve) => curve.values);
  const toggle = (id: string) => {
    if (effectiveIds.includes(id)) setSelectedIds(effectiveIds.filter((item) => item !== id));
    else if (effectiveIds.length < MAX_VISIBLE_CURVES) setSelectedIds([...effectiveIds, id]);
  };
  return <div className="research-equity-workspace">
    <div className="research-curve-picker" role="group" aria-label="Equity curve selection">
      {rows.map((row) => <label key={row.variant.variantId}>
        <input type="checkbox" checked={effectiveIds.includes(row.variant.variantId)} onChange={() => toggle(row.variant.variantId)} disabled={!effectiveIds.includes(row.variant.variantId) && effectiveIds.length >= MAX_VISIBLE_CURVES} />
        <i style={{ background: stableColour(row.variant.variantId) }} />{row.variant.name}
      </label>)}
      <small>Choose up to {MAX_VISIBLE_CURVES} curves.</small>
    </div>
    {!values.length ? <EmptyState title="Equity curves pending" description="Curves use actual recorded closed trades and appear as selected variants progress." /> : <EquityChart curves={curves} values={values} />}
  </div>;
}

function EquityChart({ curves, values }: { curves: Array<{ id: string; name: string; values: number[] }>; values: number[] }) {
  const width = 960, height = 220, pad = 24;
  const low = Math.min(0, ...values), high = Math.max(0, ...values), span = Math.max(1, high - low);
  return <div className="research-equity-chart">
    <svg role="img" aria-label="Variant equity curves" viewBox={`0 0 ${width} ${height}`}>
      <line x1={pad} x2={width - pad} y1={height - pad - ((0 - low) / span) * (height - pad * 2)} y2={height - pad - ((0 - low) / span) * (height - pad * 2)} />
      {curves.map((curve) => {
        const points = curve.values.map((value, point) => `${pad + (point / Math.max(1, curve.values.length - 1)) * (width - pad * 2)},${height - pad - ((value - low) / span) * (height - pad * 2)}`).join(" ");
        return <polyline key={curve.id} points={points} style={{ stroke: stableColour(curve.id) }} />;
      })}
    </svg>
    <div>{curves.map((curve) => <span key={curve.id}><i style={{ background: stableColour(curve.id) }} />{curve.name}</span>)}</div>
  </div>;
}

function ComparisonHeading({ label, column, active, direction, onSort, numeric }: {
  label: string; column: ComparisonSort; active: boolean; direction: SortDirection;
  onSort: (column: ComparisonSort) => void; numeric?: boolean;
}) {
  const icon: ReactNode = active ? (direction === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} />) : <ChevronsUpDown size={12} />;
  return <th className={numeric ? "numeric" : undefined} aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}>
    <button type="button" className="quant-sort-button" onClick={() => onSort(column)}>{label}{icon}</button>
  </th>;
}

function rowMetrics(row: VariantTrades) {
  const metrics = row.variant.run.metrics ?? {};
  const exposure = row.trades.reduce((sum, trade) => sum + (trade.holdingMinutes ?? 0), 0);
  const netPnl = (metrics.realizedPnl ?? 0) + (metrics.unrealizedPnl ?? 0);
  const drawdown = metrics.maximumDrawdown ?? 0;
  return {
    netPnl, drawdown, winRate: metrics.winRate ?? -1,
    costs: (metrics.fees ?? 0) + (metrics.slippage ?? 0),
    trades: metrics.completedTrades ?? row.trades.filter((trade) => Boolean(trade.exitTimestamp)).length,
    exposure,
    returnDrawdown: netPnl / Math.max(Math.abs(drawdown), 1),
  };
}

export function ResearchWorkspace({ market }: { market: PlatformMarket }) {
  const strategies = useV2Resource(useCallback(() => v2Get<StrategiesResponse>("strategies", { market }), [market]));
  const sources = useV2Resource(useCallback(() => v2Get<StrategySourcesResponse>("strategy-studio/sources", { market, status: "VALIDATED" }), [market]));
  const universes = useV2Resource(useCallback(() => v2Get<UniversesResponse>("screener/universes", { market }), [market]));
  const presets = useV2Resource(useCallback(() => v2Get<UniversePresetsResponse>("screener/presets", { market }), [market]));
  const experiments = useV2Resource(useCallback(() => v2Get<ResearchExperimentsResponse>("research/experiments", { market }), [market]), 5_000);

  const options = useMemo<StrategyOption[]>(() => [
    ...(strategies.data?.strategies ?? [])
      .filter((item) => item.supportedMarkets.includes(market))
      .map((item) => ({ key: `builtin:${item.strategyId}:${item.version}`, id: item.strategyId, sourceId: null, name: item.name, version: item.version, timeframes: item.supportedTimeframes, schema: item.configSchema })),
    ...(sources.data?.sources ?? [])
      .filter((item) => item.status === "VALIDATED")
      .map((item) => ({ key: `v2:${item.sourceId}`, id: item.strategyId, sourceId: item.sourceId, name: `${item.name} (V2)`, version: item.strategyVersion, timeframes: item.manifest.supportedTimeframes, schema: schemaFromValues(item.manifest.parameters) })),
  ], [market, sources.data, strategies.data]);
  const [strategyKey, setStrategyKey] = useState("");
  const selected = options.find((item) => item.key === strategyKey) ?? options[0] ?? null;
  const executionSchema = strategies.data?.executionSchema ?? EXECUTION_FALLBACK;

  const activeUniverse = universes.data?.active?.[market] ?? universes.data?.universes.find((item) => item.active) ?? null;
  const universeOptions = useMemo(() => [
    ...(activeUniverse ? [{ key: `saved:${activeUniverse.universeId}`, name: `${activeUniverse.name} · active watchlist`, count: activeUniverse.symbols.length }] : []),
    ...(presets.data?.presets ?? []).map((item) => ({ key: `preset:${item.presetId}`, name: `${item.name} · ${item.asOf}`, count: item.symbols.length })),
  ], [activeUniverse, presets.data]);
  const [universeChoice, setUniverseChoice] = useState("");
  const effectiveUniverseChoice = universeOptions.some((item) => item.key === universeChoice) ? universeChoice : universeOptions[0]?.key ?? "";

  const [name, setName] = useState("Controlled strategy experiment");
  const [mode, setMode] = useState<ResearchGenerationMode>("MANUAL");
  const [timeframe, setTimeframe] = useState("5m");
  const [startDate, setStartDate] = useState(() => isoDate(new Date(Date.now() - 60 * 86_400_000)));
  const [endDate, setEndDate] = useState(() => isoDate(new Date()));
  const [variants, setVariants] = useState<DraftVariant[]>(() => [newVariant(1)]);
  const [parameters, setParameters] = useState<DraftParameter[]>([]);
  const [draftRevision, setDraftRevision] = useState(0);
  const [previewRecord, setPreviewRecord] = useState<{ data: ResearchExperimentPreview; fingerprint: string; revision: number; idempotencyKey: string } | null>(null);
  const [previewPage, setPreviewPage] = useState(0);
  const [selectedPreviewVariant, setSelectedPreviewVariant] = useState(0);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  const allowedTimeframes = selected?.timeframes ?? ["5m"];
  const effectiveTimeframe = allowedTimeframes.includes(timeframe) ? timeframe : allowedTimeframes[0];
  const draftFingerprint = JSON.stringify({ name, mode, strategyKey: selected?.key, universe: effectiveUniverseChoice, timeframe: effectiveTimeframe, startDate, endDate, variants, parameters });
  const previewFresh = Boolean(previewRecord && previewRecord.revision === draftRevision && previewRecord.fingerprint === draftFingerprint);
  const touchDraft = () => setDraftRevision((value) => value + 1);

  const schemaFor = (section: DraftParameter["section"]): ConfigSchema => section === "strategy" ? selected?.schema ?? {} : executionSchema;
  const parameterOptions = (section: DraftParameter["section"]) => Object.entries(schemaFor(section)).filter(([, definition]) => supportedType(definition));
  const addParameter = () => {
    const candidates: Array<[DraftParameter["section"], string, ConfigSchema[string]]> = [
      ...parameterOptions("strategy").map(([key, definition]): ["strategy", string, ConfigSchema[string]] => ["strategy", key, definition]),
      ...parameterOptions("execution").map(([key, definition]): ["execution", string, ConfigSchema[string]] => ["execution", key, definition]),
    ];
    const unused = candidates.find(([section, key]) => !parameters.some((item) => item.section === section && item.parameter === key));
    if (unused) {
      touchDraft();
      setParameters((items) => [...items, newParameter(...unused)]);
    }
  };
  const updateParameter = (id: string, update: Partial<DraftParameter>) => {
    touchDraft();
    setParameters((items) => items.map((item) => item.id === id ? { ...item, ...update } : item));
  };

  const buildPayload = () => {
    if (!selected) throw new Error("Select an exact strategy version.");
    if (!effectiveUniverseChoice) throw new Error("Select an active watchlist or supported universe.");
    if (startDate > endDate) throw new Error("Start date must not be after end date.");
    const universe = effectiveUniverseChoice.startsWith("saved:")
      ? { universeId: effectiveUniverseChoice.slice("saved:".length) }
      : { universePresetId: effectiveUniverseChoice.slice("preset:".length) };
    const payload = {
      name: name.trim(), mode, market, strategyId: selected.id, strategyVersion: selected.version,
      strategySourceId: selected.sourceId ?? undefined, ...universe,
      timeframe: effectiveTimeframe, startDate, endDate, configuration: {}, execution: {},
      variants: mode === "MANUAL" ? variants.map((variant) => {
        const parsed = parseVariant(variant.json);
        return { name: variant.name.trim(), configuration: parsed.strategy, execution: parsed.execution };
      }) : [],
      parameters: mode === "GRID" ? parameters.map(parseDraftParameter) : [],
    };
    if (!payload.name) throw new Error("Experiment name is required.");
    if (mode === "MANUAL" && payload.variants.some((variant) => !variant.name)) throw new Error("Every variant needs a name.");
    return payload;
  };

  const previewExperiment = async () => {
    setPreviewing(true); setNotice(null);
    try {
      const data = await v2Post<ResearchExperimentPreview>("research/experiments/preview", buildPayload());
      setPreviewRecord({ data, fingerprint: draftFingerprint, revision: draftRevision, idempotencyKey: crypto.randomUUID() });
      setPreviewPage(0); setSelectedPreviewVariant(0);
    } catch (reason) { setNotice({ kind: "error", text: errorMessage(reason, "The experiment preview is invalid") }); }
    finally { setPreviewing(false); }
  };

  const runExperiment = async () => {
    if (!previewRecord || !previewFresh) return;
    setSubmitting(true); setNotice(null);
    try {
      const created = await v2Post<ResearchExperiment>("research/experiments/from-preview", {
        ...buildPayload(), previewHash: previewRecord.data.previewHash, idempotencyKey: previewRecord.idempotencyKey,
      });
      setNotice({ kind: "success", text: `Experiment ${shortId(created.experimentId)} queued with ${created.variantCount} immutable variants.` });
      experiments.refresh();
    } catch (reason) { setNotice({ kind: "error", text: errorMessage(reason, "The experiment could not be started") }); }
    finally { setSubmitting(false); }
  };

  const reset = () => {
    setMode("MANUAL"); setVariants([newVariant(1)]); setParameters([]);
    setPreviewRecord(null); setNotice(null); setName("Controlled strategy experiment"); touchDraft();
  };

  const [comparisonId, setComparisonId] = useState<string | null>(null);
  const comparison = experiments.data?.experiments.find((item) => item.experimentId === comparisonId) ?? experiments.data?.experiments[0] ?? null;
  const loadComparison = useCallback(async (): Promise<VariantTrades[]> => {
    if (!comparison) return [];
    const results = new Array<VariantTrades>(comparison.variants.length);
    let cursor = 0;
    const worker = async () => {
      while (cursor < comparison.variants.length) {
        const index = cursor++;
        const variant = comparison.variants[index];
        const page = await v2Get<BacktestTradesResponse>(`backtests/${variant.run.runId}/trades`, { sort: "exitTimestamp", direction: "asc", limit: 5000 });
        results[index] = { variant, trades: page.trades, total: page.total };
      }
    };
    await Promise.all(Array.from({ length: Math.min(4, comparison.variants.length) }, worker));
    return results;
  }, [comparison]);
  const comparisonTrades = useV2Resource(loadComparison, comparison?.variants.some((variant) => ACTIVE_STATUSES.has(variant.run.status)) ? 5_000 : undefined);
  const [comparisonSort, setComparisonSort] = useState<ComparisonSort>("netPnl");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [ranking, setRanking] = useState<Ranking>("NET_PNL");
  const [selectedCurveIds, setSelectedCurveIds] = useState<string[]>([]);

  const orderedRows = useMemo(() => {
    const rows = [...(comparisonTrades.data ?? [])];
    const value = (row: VariantTrades, column: ComparisonSort): number | string => {
      const metrics = rowMetrics(row);
      return column === "name" ? row.variant.name.toLocaleLowerCase() : metrics[column];
    };
    const compare = (a: VariantTrades, b: VariantTrades, column: ComparisonSort, direction: SortDirection) => {
      const left = value(a, column), right = value(b, column);
      const result = typeof left === "string" ? left.localeCompare(String(right)) : left - Number(right);
      return direction === "asc" ? result : -result;
    };
    if (ranking === "NONE") return rows.sort((a, b) => compare(a, b, comparisonSort, sortDirection));
    const complete = rows.filter((row) => row.variant.run.status === "COMPLETE");
    const incomplete = rows.filter((row) => row.variant.run.status !== "COMPLETE");
    const rankingRule: Record<Exclude<Ranking, "NONE">, [ComparisonSort | "returnDrawdown", SortDirection]> = {
      NET_PNL: ["netPnl", "desc"], RETURN_DRAWDOWN: ["returnDrawdown", "desc"],
      LOWEST_DRAWDOWN: ["drawdown", "asc"], HIGHEST_WIN_RATE: ["winRate", "desc"],
    };
    const [column, direction] = rankingRule[ranking];
    complete.sort((a, b) => {
      const left = column === "returnDrawdown" ? rowMetrics(a).returnDrawdown : value(a, column);
      const right = column === "returnDrawdown" ? rowMetrics(b).returnDrawdown : value(b, column);
      return direction === "asc" ? Number(left) - Number(right) : Number(right) - Number(left);
    });
    return [...complete, ...incomplete];
  }, [comparisonSort, comparisonTrades.data, ranking, sortDirection]);
  const leaderId = orderedRows.find((row) => row.variant.run.status === "COMPLETE")?.variant.variantId;
  const onComparisonSort = (column: ComparisonSort) => {
    setRanking("NONE");
    if (comparisonSort === column) setSortDirection((value) => value === "asc" ? "desc" : "asc");
    else { setComparisonSort(column); setSortDirection(column === "name" ? "asc" : "desc"); }
  };

  const cancelExperiment = async (experimentId: string) => {
    setNotice(null);
    try {
      await v2Delete(`research/experiments/${experimentId}`);
      experiments.refresh();
    } catch (reason) { setNotice({ kind: "error", text: errorMessage(reason, "Cancellation could not be requested") }); }
  };

  const preview = previewRecord?.data ?? null;
  const previewRows = preview?.variants.slice(previewPage * PREVIEW_PAGE_SIZE, (previewPage + 1) * PREVIEW_PAGE_SIZE) ?? [];
  const selectedPreview = preview?.variants[selectedPreviewVariant] ?? null;
  const strategyDifferenceKeys = preview?.mode === "GRID"
    ? preview.sweepDefinitions.filter((item) => item.section === "strategy").map((item) => item.parameter)
    : differingKeys(preview?.variants.map((item) => item.configuration) ?? []);
  const executionDifferenceKeys = preview?.mode === "GRID"
    ? preview.sweepDefinitions.filter((item) => item.section === "execution").map((item) => item.parameter)
    : differingKeys(preview?.variants.map((item) => item.execution) ?? []);

  return <main className="quant-workspace research-lab">
    <WorkspaceHeader eyebrow={`${marketLabel(market)} research`} title="Research Lab" actions={<button type="button" onClick={experiments.refresh}><RefreshCw size={15} />Refresh</button>} />
    <Panel icon={<FlaskConical size={17} />} title="New controlled experiment" description="Preview every immutable backtest input before queueing. Research cannot approve or deploy strategies.">
      {strategies.loading || universes.loading || sources.loading || presets.loading ? <LoadingState label="Loading research inputs" /> : strategies.error ? <RequestErrorState error={strategies.error} retry={strategies.reload} /> : <div className="quant-panel-body">
        <div className="quant-form-grid research-experiment-grid">
          <label><span>Experiment name</span><input value={name} maxLength={120} onChange={(event) => { touchDraft(); setName(event.target.value); }} /></label>
          <label><span>Strategy and exact version</span><select value={selected?.key ?? ""} onChange={(event) => { touchDraft(); setStrategyKey(event.target.value); }}>{options.map((item) => <option key={item.key} value={item.key}>{item.name} · v{item.version}</option>)}</select></label>
          <label><span>Timeframe</span><select value={effectiveTimeframe} onChange={(event) => { touchDraft(); setTimeframe(event.target.value); }}>{allowedTimeframes.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label><span>Watchlist or universe</span><select aria-label="Watchlist or universe" value={effectiveUniverseChoice} onChange={(event) => { touchDraft(); setUniverseChoice(event.target.value); }}>{universeOptions.map((item) => <option key={item.key} value={item.key}>{item.name} · {item.count} symbols</option>)}</select></label>
          <label><span>Start date</span><input type="date" value={startDate} onChange={(event) => { touchDraft(); setStartDate(event.target.value); }} /></label>
          <label><span>End date</span><input type="date" value={endDate} onChange={(event) => { touchDraft(); setEndDate(event.target.value); }} /></label>
        </div>

        <div className="research-mode-control" role="group" aria-label="Experiment generation mode">
          <button type="button" className={mode === "MANUAL" ? "active" : ""} onClick={() => { if (mode !== "MANUAL") { touchDraft(); setMode("MANUAL"); } }}>Manual variants</button>
          <button type="button" className={mode === "GRID" ? "active" : ""} onClick={() => { if (mode !== "GRID") { touchDraft(); setMode("GRID"); } }}>Grid sweep</button>
        </div>

        {mode === "MANUAL" ? <div className="research-variant-list">{variants.map((variant, index) => <section className="research-variant" key={variant.id}>
          <header><input aria-label={`Variant ${index + 1} name`} value={variant.name} maxLength={80} onChange={(event) => { touchDraft(); setVariants((items) => items.map((item) => item.id === variant.id ? { ...item, name: event.target.value } : item)); }} /><div>
            <button type="button" aria-label={`Duplicate ${variant.name}`} onClick={() => { touchDraft(); setVariants((items) => [...items, { ...newVariant(items.length + 1), json: variant.json }]); }} disabled={variants.length >= MAX_MANUAL_VARIANTS}><Copy size={13} />Duplicate</button>
            <button type="button" aria-label={`Remove ${variant.name}`} onClick={() => { touchDraft(); setVariants((items) => items.filter((item) => item.id !== variant.id)); }} disabled={variants.length === 1}><Trash2 size={13} />Remove</button>
          </div></header>
          <textarea aria-label={`${variant.name} JSON`} spellCheck={false} value={variant.json} onChange={(event) => { touchDraft(); setVariants((items) => items.map((item) => item.id === variant.id ? { ...item, json: event.target.value } : item)); }} />
        </section>)}</div> : <ParameterBuilder rows={parameters} schemaFor={schemaFor} update={updateParameter} remove={(id) => { touchDraft(); setParameters((items) => items.filter((item) => item.id !== id)); }} duplicate={(row) => { touchDraft(); setParameters((items) => [...items, { ...row, id: crypto.randomUUID() }]); }} />}

        <div className="quant-form-actions research-actions">
          {mode === "MANUAL" ? <button type="button" onClick={() => { touchDraft(); setVariants((items) => [...items, newVariant(items.length + 1)]); }} disabled={variants.length >= MAX_MANUAL_VARIANTS}><Plus size={14} />Add variant</button> : <button type="button" onClick={addParameter} disabled={parameters.length >= MAX_SWEEP_PARAMETERS}><Plus size={14} />Add parameter</button>}
          <button type="button" onClick={reset}><RotateCcw size={14} />Reset</button>
          <button type="button" onClick={previewExperiment} disabled={previewing || !selected || !effectiveUniverseChoice}>{previewing ? <LoaderCircle className="spin" size={14} /> : <FlaskConical size={14} />}{previewing ? "Previewing…" : "Preview experiment"}</button>
          <button className="primary" type="button" onClick={runExperiment} disabled={!previewFresh || submitting}>{submitting ? <LoaderCircle className="spin" size={14} /> : <FlaskConical size={14} />}{submitting ? "Queuing…" : "Run experiment"}</button>
          {previewRecord && !previewFresh && <span className="research-preview-stale">Preview stale · preview again before running.</span>}
        </div>
        {notice && <Message kind={notice.kind}>{notice.text}</Message>}
      </div>}
    </Panel>

    {preview && <Panel icon={<FlaskConical size={17} />} title="Experiment preview" description="No database rows or backtests are created by preview.">
      <div className="quant-panel-body research-preview">
        <dl className="quant-facts research-preview-summary">
          <div><dt>Strategy</dt><dd>{preview.strategyId} v{preview.strategyVersion}</dd></div><div><dt>Market</dt><dd>{preview.market}</dd></div>
          <div><dt>Timeframe</dt><dd>{preview.timeframe}</dd></div><div><dt>Watchlist</dt><dd>{preview.universeName}</dd></div>
          <div><dt>Symbols</dt><dd>{preview.symbolCount}</dd></div><div><dt>Parameters</dt><dd>{preview.parameterCount}</dd></div>
          <div><dt>Variants</dt><dd>{preview.variantCount}</dd></div><div><dt>Symbol-runs</dt><dd>{preview.estimatedSymbolRuns}</dd></div>
        </dl>
        {preview.warnings.map((warning) => <Message kind="error" key={warning}>{warning}</Message>)}
        <div className="quant-table-scroll"><table className="quant-table research-combination-table"><thead><tr><th>#</th><th>Deterministic name</th><th>Strategy differences</th><th>Execution differences</th><th>JSON</th></tr></thead><tbody>{previewRows.map((variant, index) => {
          const absoluteIndex = previewPage * PREVIEW_PAGE_SIZE + index;
          const strategyDifferences = strategyDifferenceKeys.map((key) => `${key}=${JSON.stringify(variant.configuration[key])}`).join(" · ") || "—";
          const executionDifferences = executionDifferenceKeys.map((key) => `${key}=${JSON.stringify(variant.execution[key])}`).join(" · ") || "—";
          return <tr key={`${absoluteIndex}-${variant.name}`}><td>{absoluteIndex + 1}</td><td>{variant.name}</td><td>{strategyDifferences}</td><td>{executionDifferences}</td><td><button type="button" onClick={() => setSelectedPreviewVariant(absoluteIndex)}>Inspect</button></td></tr>;
        })}</tbody></table></div>
        <div className="quant-pager"><button type="button" onClick={() => setPreviewPage((value) => Math.max(0, value - 1))} disabled={previewPage === 0}>Previous</button><span>Page {previewPage + 1} of {Math.max(1, Math.ceil(preview.variantCount / PREVIEW_PAGE_SIZE))}</span><button type="button" onClick={() => setPreviewPage((value) => value + 1)} disabled={(previewPage + 1) * PREVIEW_PAGE_SIZE >= preview.variantCount}>Next</button></div>
        {selectedPreview && <details className="quant-details research-preview-json"><summary>Full immutable JSON · variant {selectedPreviewVariant + 1}</summary><pre>{JSON.stringify(selectedPreview, null, 2)}</pre></details>}
      </div>
    </Panel>}

    <Panel icon={<FlaskConical size={17} />} title="Experiments" description="Queued and running variants use the shared bounded backtest worker pool.">
      {experiments.loading ? <LoadingState label="Loading experiments" /> : experiments.error ? <RequestErrorState error={experiments.error} retry={experiments.reload} /> : !experiments.data?.experiments.length ? <EmptyState title="No research experiments" description="Create controlled variants above." /> : <div className="research-experiment-list">{experiments.data.experiments.map((experiment) => <article key={experiment.experimentId} className="research-experiment-card"><header><div><strong>{experiment.name}</strong><small>{experiment.mode} · {experiment.strategyId} v{experiment.strategyVersion} · {experiment.timeframe} · {experiment.symbolCount} symbols · {experiment.variantCount} variants</small></div><div className="research-card-actions"><StatusBadge tone={tone(experiment.status)}>{experiment.status}</StatusBadge>{experiment.variants.some((variant) => ACTIVE_STATUSES.has(variant.run.status)) && <button type="button" onClick={() => cancelExperiment(experiment.experimentId)}><Square size={12} />Cancel</button>}</div></header><div className="research-status-counts">{Object.entries(experiment.variantStatusCounts).map(([status, count]) => <span key={status}>{status.toLowerCase()} {count}</span>)}</div><div className="research-run-grid">{experiment.variants.map((variant) => <div key={variant.variantId}><span>{variant.name}</span><StatusBadge tone={tone(variant.run.status)}>{variant.run.status}</StatusBadge><small>{shortId(variant.run.runId)} · created {formatDateTime(variant.run.createdAt, market)}</small></div>)}</div></article>)}</div>}
    </Panel>

    <WalkForwardSection
      market={market}
      strategies={options.map(({ key, id, sourceId, name: optionName, version, timeframes }) => ({ key, id, sourceId, name: optionName, version, timeframes }))}
      universes={universeOptions}
      experiments={experiments.data?.experiments ?? []}
    />

    <Panel icon={<FlaskConical size={17} />} title="Strategy comparison" description="Completed variants are ranked; incomplete, failed and cancelled variants remain visible without a rank.">
      {!experiments.data?.experiments.length ? <EmptyState title="Nothing to compare" description="Create an experiment with two or more variants." /> : <div className="quant-panel-body research-comparison">
        <div className="research-comparison-controls"><label><span>Experiment</span><select value={comparison?.experimentId ?? ""} onChange={(event) => setComparisonId(event.target.value)}>{experiments.data.experiments.map((item) => <option key={item.experimentId} value={item.experimentId}>{item.name} · {item.variantCount} variants</option>)}</select></label><label><span>Ranking</span><select aria-label="Ranking" value={ranking} onChange={(event) => setRanking(event.target.value as Ranking)}><option value="NET_PNL">Net P&amp;L</option><option value="RETURN_DRAWDOWN">Return / drawdown score</option><option value="LOWEST_DRAWDOWN">Lowest drawdown</option><option value="HIGHEST_WIN_RATE">Highest win rate</option><option value="NONE">Table sort</option></select></label></div>
        {comparisonTrades.loading ? <LoadingState label="Loading variant trades" /> : comparisonTrades.error ? <RequestErrorState error={comparisonTrades.error} retry={comparisonTrades.reload} /> : <>
          <div className="quant-table-scroll"><table className="quant-table research-comparison-table"><thead><tr>
            <ComparisonHeading label="Variant" column="name" active={ranking === "NONE" && comparisonSort === "name"} direction={sortDirection} onSort={onComparisonSort} />
            <th>Status</th><ComparisonHeading label="Net P&L" column="netPnl" active={ranking === "NONE" && comparisonSort === "netPnl"} direction={sortDirection} onSort={onComparisonSort} numeric />
            <ComparisonHeading label="Maximum drawdown" column="drawdown" active={ranking === "NONE" && comparisonSort === "drawdown"} direction={sortDirection} onSort={onComparisonSort} numeric />
            <ComparisonHeading label="Win rate" column="winRate" active={ranking === "NONE" && comparisonSort === "winRate"} direction={sortDirection} onSort={onComparisonSort} numeric />
            <ComparisonHeading label="Costs" column="costs" active={ranking === "NONE" && comparisonSort === "costs"} direction={sortDirection} onSort={onComparisonSort} numeric />
            <ComparisonHeading label="Completed trades" column="trades" active={ranking === "NONE" && comparisonSort === "trades"} direction={sortDirection} onSort={onComparisonSort} numeric />
            <ComparisonHeading label="Exposure" column="exposure" active={ranking === "NONE" && comparisonSort === "exposure"} direction={sortDirection} onSort={onComparisonSort} numeric />
            <th className="numeric">Failed symbols</th><th>Inspect</th>
          </tr></thead><tbody>{orderedRows.map((row) => {
            const metrics = rowMetrics(row), complete = row.variant.run.status === "COMPLETE";
            return <tr key={row.variant.variantId} className={leaderId === row.variant.variantId ? "research-leading-variant" : undefined}><td><strong>{row.variant.name}</strong>{leaderId === row.variant.variantId && <small>Leader</small>}<small>{shortId(row.variant.run.runId)}{ranking === "RETURN_DRAWDOWN" && complete ? ` · score ${metrics.returnDrawdown.toFixed(3)}` : ""}</small></td><td><StatusBadge tone={tone(row.variant.run.status)}>{row.variant.run.status}</StatusBadge></td><td className="numeric">{formatMoney(metrics.netPnl, market)}</td><td className="numeric">{formatMoney(metrics.drawdown, market)}</td><td className="numeric">{metrics.winRate < 0 ? "—" : formatPercent(metrics.winRate, 1)}</td><td className="numeric">{formatMoney(metrics.costs, market)}</td><td className="numeric">{formatInteger(metrics.trades)}</td><td className="numeric">{formatMinutes(metrics.exposure)}</td><td className="numeric">{formatInteger(row.variant.run.failedSymbols?.length ?? 0)}</td><td><a className="quant-inline-link" href={`/backtest?${new URLSearchParams({ market, runId: row.variant.run.runId })}`}>Chart</a></td></tr>;
          })}</tbody></table></div>
          <p className="quant-inline-note">Return / drawdown score = net P&amp;L ÷ max(|maximum drawdown|, 1). It is not a Sharpe ratio. Exposure is based on recorded trade holding minutes. Failed symbols are operational failures. Rejected trades: unavailable — true rejection analytics require a future decision-event audit model; a strategy emitting no BUY is not a rejection.</p>
          <EquityComparison rows={comparisonTrades.data ?? []} selectedIds={selectedCurveIds} setSelectedIds={setSelectedCurveIds} />
        </>}
      </div>}
    </Panel>
  </main>;
}

function ParameterBuilder({ rows, schemaFor, update, remove, duplicate }: {
  rows: DraftParameter[];
  schemaFor: (section: DraftParameter["section"]) => ConfigSchema;
  update: (id: string, update: Partial<DraftParameter>) => void;
  remove: (id: string) => void;
  duplicate: (row: DraftParameter) => void;
}) {
  if (!rows.length) return <EmptyState title="No sweep parameters" description="Add one or more schema-backed strategy or execution parameters." />;
  return <div className="quant-table-scroll research-parameter-scroll"><table className="quant-table research-parameter-table"><thead><tr><th>Section</th><th>Parameter</th><th>Type</th><th>Method</th><th>Values or range</th><th className="numeric">Count</th><th>Actions</th></tr></thead><tbody>{rows.map((row) => {
    const schema = schemaFor(row.section);
    const definition = schema[row.parameter] ?? {};
    const options = Object.entries(schema).filter(([, item]) => supportedType(item));
    const changeSection = (section: DraftParameter["section"]) => {
      const [parameter, nextDefinition] = Object.entries(schemaFor(section)).find(([, item]) => supportedType(item)) ?? ["", { type: "number", default: 1 }];
      update(row.id, { ...newParameter(section, parameter, nextDefinition), id: row.id });
    };
    const changeParameter = (parameter: string) => update(row.id, { ...newParameter(row.section, parameter, schema[parameter]), id: row.id });
    return <tr key={row.id}><td><select aria-label="Parameter section" value={row.section} onChange={(event) => changeSection(event.target.value as DraftParameter["section"])}><option value="strategy">strategy</option><option value="execution">execution</option></select></td><td><select aria-label="Parameter name" value={row.parameter} onChange={(event) => changeParameter(event.target.value)}>{options.map(([key, item]) => <option key={key} value={key}>{item.label ?? key}</option>)}</select></td><td><select aria-label="Parameter type" value={row.type} disabled><option value={row.type}>{row.type}</option></select></td><td><select aria-label="Generation method" value={row.method} onChange={(event) => update(row.id, { method: event.target.value as DraftParameter["method"] })}><option value="EXPLICIT_VALUES">Explicit values</option>{["number", "integer"].includes(row.type) && <option value="NUMERIC_RANGE">Numeric range</option>}<option value="FIXED">Fixed value</option></select></td><td>{row.method === "EXPLICIT_VALUES" ? <textarea aria-label={`${row.parameter} values`} value={row.valuesText} onChange={(event) => update(row.id, { valuesText: event.target.value })} /> : row.method === "NUMERIC_RANGE" ? <div className="research-range-inputs"><input aria-label={`${row.parameter} minimum`} type="number" step="any" value={row.minimum} onChange={(event) => update(row.id, { minimum: event.target.value })} /><input aria-label={`${row.parameter} maximum`} type="number" step="any" value={row.maximum} onChange={(event) => update(row.id, { maximum: event.target.value })} /><input aria-label={`${row.parameter} step`} type="number" step="any" value={row.step} onChange={(event) => update(row.id, { step: event.target.value })} /></div> : definition.enum?.length ? <select aria-label={`${row.parameter} fixed value`} value={row.fixedText} onChange={(event) => update(row.id, { fixedText: event.target.value })}>{definition.enum.map((item) => <option key={JSON.stringify(item)} value={JSON.stringify(item)}>{String(item)}</option>)}</select> : row.type === "boolean" ? <select aria-label={`${row.parameter} fixed value`} value={row.fixedText} onChange={(event) => update(row.id, { fixedText: event.target.value })}><option value="true">true</option><option value="false">false</option></select> : <input aria-label={`${row.parameter} fixed value`} type="number" step="any" value={row.fixedText} onChange={(event) => update(row.id, { fixedText: event.target.value })} />}</td><td className="numeric">{generatedValueCount(row)}</td><td><div className="quant-row-actions"><button type="button" aria-label={`Duplicate ${row.parameter} parameter`} onClick={() => duplicate(row)}><Copy size={12} /></button><button type="button" aria-label={`Remove ${row.parameter} parameter`} onClick={() => remove(row.id)}><Trash2 size={12} /></button></div></td></tr>;
  })}</tbody></table></div>;
}
