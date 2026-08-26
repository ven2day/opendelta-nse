"use client";
/* eslint-disable @next/next/no-html-link-for-pages -- Native navigation matches the existing production shell. */

import {
  Ban,
  Download,
  IndianRupee,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Pin,
  Radio,
  RefreshCw,
  TrendingUp,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

type RankingMode = "QUALITY" | "GOOD_RATE" | "TARGET_SPEED" | "LOW_MAE" | "TARGET_HIT_RATE";

type UniverseRow = {
  rank: number | null;
  qualityRank: number | null;
  symbol: string;
  referencePrice: number;
  priceAsOf: string;
  qualityScore: number;
  goodRate: number;
  badRate: number;
  historicalTargetHitRate: number;
  medianTargetMinutes: number;
  medianMaePct: number;
  worstMaePct: number;
  openRate: number;
  buyObservations: number;
  le30mPct: number;
  le2hPct: number;
  le24hPct: number;
  selectionReason: string;
  isPinned: boolean;
};

type ExcludedRow = {
  symbol: string | null;
  reason: string;
  detail?: string;
  referencePrice?: number | null;
  priceAsOf?: string | null;
  qualityScore?: number | null;
  qualityRank?: number | null;
  buyObservations?: number;
};

type Aggregate = {
  symbols: number;
  buyObservations: number;
  goodRate: number;
  badRate: number;
  neutralRate: number;
  openRate: number;
  targetHitRate: number;
  medianTargetMinutes: number;
  medianMaePct: number;
};

type UniverseRecord = {
  status: "PREVIEW" | "ACTIVE";
  frozen: boolean;
  universeVersion?: string;
  configurationHash: string;
  configuration: {
    topN: number;
    minimumPrice: number;
    maximumPrice: number;
    rankingMode: RankingMode;
    minimumBuyObservations: number;
    manualPins: string[];
    manualExclusions: string[];
    minimumGoodRate: number | null;
    maximumOpenRate: number | null;
    maximumMedianTargetMinutes: number | null;
    minimumTargetHitRate: number | null;
    minimumMedianMaePct: number | null;
    dynamicPriceFilter: boolean;
  };
  ranking: { mode: RankingMode; label: string; qualityFormula: string; qualitySortOrder: string[] };
  source: {
    strategyVersion: string;
    historicalRunId: string;
    backtestFrom: string;
    backtestTo: string;
    priceSource: string;
    priceAsOf: string;
    priceSourceGeneratedAt: string;
  };
  statistics: {
    totalNseSymbols: number;
    dataQualityEligible: number;
    dataQualityExcluded: number;
    priceEligible: number;
    priceBelowMinimum: number;
    priceAboveMaximum: number;
    referencePriceUnavailable: number;
    sampleEligible: number;
    rankingEligible: number;
    requestedTopN: number;
    calculatedSelected: number;
    pinned: number;
    manuallyExcluded: number;
    selected: number;
  };
  selectedSymbols: string[];
  selected: UniverseRow[];
  excluded: ExcludedRow[];
  dataQualityExcluded: ExcludedRow[];
  differences: {
    added: Array<{ symbol: string; reason: string }>;
    removed: Array<{ symbol: string; reason: string }>;
    unchanged: string[];
  };
  aggregates: { selected: Aggregate; fullValidatedUniverse: Aggregate; nextEligible100: Aggregate };
  distributions: {
    historicalBuyObservations: Record<string, number>;
    selectedReferencePrice: Record<string, number | null>;
    selectedBuyObservations: Record<string, number | null>;
  };
  nextTier: UniverseRow[];
  createdAt?: string;
};

type ConfigResponse = {
  defaults: UniverseRecord["configuration"];
  rankingModes: Array<{ value: RankingMode; label: string }>;
  historicalBuyObservationDistribution: Record<string, number>;
  minimumBuyObservationRationale: string;
  active: UniverseRecord | null;
};

type HistoryItem = {
  universeVersion: string;
  createdAt: string;
  selected: number;
  priceAsOf: string;
  configuration: UniverseRecord["configuration"];
};

type View = "selected" | "below" | "price" | "quality";
type SortKey = keyof Pick<UniverseRow, "rank" | "symbol" | "referencePrice" | "qualityScore" | "goodRate" | "badRate" | "historicalTargetHitRate" | "medianTargetMinutes" | "medianMaePct" | "openRate" | "buyObservations">;

const DEFAULTS = {
  topN: 300,
  minimumPrice: 500,
  maximumPrice: 2000,
  rankingMode: "QUALITY" as RankingMode,
  minimumBuyObservations: 50,
};

function number(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("en-IN", { maximumFractionDigits: digits });
}

function duration(minutes: number | null | undefined) {
  if (minutes == null || !Number.isFinite(minutes)) return "—";
  if (minutes < 60) return `${number(minutes, 0)}m`;
  if (minutes <= 1440) return `${number(minutes / 60, 1)}h`;
  return `${number(minutes / 1440, 1)}d`;
}

function formatIst(value: string | null | undefined) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  }).format(new Date(value));
}

function priceAge(value: string | null | undefined) {
  if (!value) return "unknown age";
  const minutes = Math.max(0, (Date.now() - new Date(value).getTime()) / 60_000);
  if (minutes < 60) return `${Math.round(minutes)} minutes old`;
  if (minutes < 1440) return `${number(minutes / 60, 1)} hours old`;
  return `${number(minutes / 1440, 1)} days old`;
}

function parseSymbols(value: string) {
  return [...new Set(value.split(/[\s,]+/).map((item) => item.trim().toUpperCase().replace(/\.NS$/, "")).filter(Boolean))];
}

async function responsePayload(response: Response) {
  const text = await response.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Live-universe service returned an unreadable response near: ${text.slice(0, 120)}`);
  }
}

export function LiveUniverse({ userName, signOutHref }: { userName: string; signOutHref: string }) {
  const [topN, setTopN] = useState(DEFAULTS.topN);
  const [minimumPrice, setMinimumPrice] = useState(DEFAULTS.minimumPrice);
  const [maximumPrice, setMaximumPrice] = useState(DEFAULTS.maximumPrice);
  const [rankingMode, setRankingMode] = useState<RankingMode>(DEFAULTS.rankingMode);
  const [minimumBuyObservations, setMinimumBuyObservations] = useState(DEFAULTS.minimumBuyObservations);
  const [manualPins, setManualPins] = useState("");
  const [manualExclusions, setManualExclusions] = useState("");
  const [minimumGoodRate, setMinimumGoodRate] = useState("");
  const [maximumOpenRate, setMaximumOpenRate] = useState("");
  const [maximumMedianTargetMinutes, setMaximumMedianTargetMinutes] = useState("");
  const [minimumTargetHitRate, setMinimumTargetHitRate] = useState("");
  const [minimumMedianMaePct, setMinimumMedianMaePct] = useState("");
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [active, setActive] = useState<UniverseRecord | null>(null);
  const [preview, setPreview] = useState<UniverseRecord | null>(null);
  const [previewRequest, setPreviewRequest] = useState<Record<string, unknown> | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [view, setView] = useState<View>("selected");
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  const applyConfiguration = useCallback((source: UniverseRecord["configuration"]) => {
    setTopN(source.topN);
    setMinimumPrice(source.minimumPrice);
    setMaximumPrice(source.maximumPrice);
    setRankingMode(source.rankingMode);
    setMinimumBuyObservations(source.minimumBuyObservations);
    setManualPins(source.manualPins.join(", "));
    setManualExclusions(source.manualExclusions.join(", "));
    setMinimumGoodRate(source.minimumGoodRate?.toString() ?? "");
    setMaximumOpenRate(source.maximumOpenRate?.toString() ?? "");
    setMaximumMedianTargetMinutes(source.maximumMedianTargetMinutes?.toString() ?? "");
    setMinimumTargetHitRate(source.minimumTargetHitRate?.toString() ?? "");
    setMinimumMedianMaePct(source.minimumMedianMaePct?.toString() ?? "");
  }, []);

  const loadConfiguration = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [configResponse, historyResponse] = await Promise.all([
        fetch("/api/live-universe?action=config", { cache: "no-store" }),
        fetch("/api/live-universe?action=history", { cache: "no-store" }),
      ]);
      const configBody = await responsePayload(configResponse);
      const historyBody = await responsePayload(historyResponse);
      if (!configResponse.ok) throw new Error(configBody.detail ?? "Unable to load live-universe configuration");
      if (!historyResponse.ok) throw new Error(historyBody.detail ?? "Unable to load universe history");
      setConfig(configBody);
      setHistory(historyBody.versions ?? []);
      setActive(configBody.active);
      if (configBody.active) {
        applyConfiguration(configBody.active.configuration);
        setPreview(configBody.active);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load live universe");
    } finally {
      setLoading(false);
    }
  }, [applyConfiguration]);

  // The async loader owns the initial remote-state synchronization for this client view.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void loadConfiguration(); }, [loadConfiguration]);

  const request = () => {
    if (!Number.isInteger(topN) || topN < 1 || topN > 750) throw new Error("Number of symbols must be between 1 and 750");
    if (minimumPrice < 0) throw new Error("Minimum share price cannot be negative");
    if (maximumPrice <= minimumPrice) throw new Error("Maximum share price must be greater than minimum share price");
    if (!Number.isInteger(minimumBuyObservations) || minimumBuyObservations < 1) throw new Error("Minimum historical BUY observations must be at least 1");
    const optional = (value: string) => value.trim() === "" ? null : Number(value);
    return {
      topN,
      minimumPrice,
      maximumPrice,
      rankingMode,
      minimumBuyObservations,
      manualPins: parseSymbols(manualPins),
      manualExclusions: parseSymbols(manualExclusions),
      minimumGoodRate: optional(minimumGoodRate),
      maximumOpenRate: optional(maximumOpenRate),
      maximumMedianTargetMinutes: optional(maximumMedianTargetMinutes),
      minimumTargetHitRate: optional(minimumTargetHitRate),
      minimumMedianMaePct: optional(minimumMedianMaePct),
      dynamicPriceFilter: false,
    };
  };

  const buildPreview = async () => {
    setWorking(true);
    setError("");
    try {
      const payload = request();
      const action = active ? "rebuild" : "preview";
      const response = await fetch(`/api/live-universe?action=${action}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await responsePayload(response);
      if (!response.ok) throw new Error(body.detail ?? "Unable to preview live universe");
      setPreview(body);
      setPreviewRequest(payload);
      setView("selected");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to preview live universe");
    } finally {
      setWorking(false);
    }
  };

  const saveAndFreeze = async () => {
    if (!preview || !previewRequest) return;
    setWorking(true);
    setError("");
    try {
      const response = await fetch("/api/live-universe?action=save", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...previewRequest, configurationHash: preview.configurationHash }),
      });
      const body = await responsePayload(response);
      if (!response.ok) throw new Error(body.detail ?? "Unable to freeze live universe");
      setActive(body);
      setPreview(body);
      setPreviewRequest(null);
      await loadConfiguration();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to freeze live universe");
    } finally {
      setWorking(false);
    }
  };

  const displayed = preview ?? active;
  const sortedSelected = useMemo(() => {
    if (!displayed) return [];
    return [...displayed.selected].sort((left, right) => {
      const a = left[sortKey];
      const b = right[sortKey];
      const difference = typeof a === "string" ? a.localeCompare(String(b)) : Number(a ?? Infinity) - Number(b ?? Infinity);
      return sortDirection === "asc" ? difference : -difference;
    });
  }, [displayed, sortDirection, sortKey]);

  const excludedRows = useMemo(() => {
    if (!displayed) return [];
    if (view === "below") return displayed.excluded.filter((item) => item.reason === "BELOW_RANK_CUTOFF");
    if (view === "price") return displayed.excluded.filter((item) => item.reason.startsWith("PRICE_") || item.reason === "REFERENCE_PRICE_UNAVAILABLE");
    if (view === "quality") return [...displayed.dataQualityExcluded, ...displayed.excluded.filter((item) => item.reason.includes("SAMPLE") || item.reason.includes("RATE") || item.reason.includes("MAE") || item.reason.includes("TARGET_TIME"))];
    return [];
  }, [displayed, view]);

  const sort = (key: SortKey) => {
    if (sortKey === key) setSortDirection((current) => current === "asc" ? "desc" : "asc");
    else { setSortKey(key); setSortDirection(key === "symbol" ? "asc" : key === "rank" || key === "medianTargetMinutes" ? "asc" : "desc"); }
  };

  const topPreset = (value: number) => { setTopN(value); setPreviewRequest(null); };
  const pricePreset = (minimum: number, maximum: number) => { setMinimumPrice(minimum); setMaximumPrice(maximum); setPreviewRequest(null); };
  const initials = userName.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();

  return <div className="site-shell backtest-shell live-universe-shell">
    <header className="global-header"><div className="header-inner">
      <a className="brand" href="/"><div className="brand-mark" aria-hidden="true">₹</div><div><strong>OpenDelta</strong><span>Market intelligence</span></div></a>
      <nav className="top-nav" aria-label="Main navigation">
        <a className="nav-item" href="/"><LayoutDashboard size={16} />Dashboard</a>
        <a className="nav-item" href="/backtest"><TrendingUp size={16} />Backtest</a>
        <a className="nav-item active" href="/signals" aria-current="page"><Radio size={16} />Signals</a>
      </nav>
      <div className="header-actions"><div className="user-chip"><div className="avatar">{initials}</div><span>{userName}</span></div><a href={signOutHref} className="icon-button" aria-label="Sign out"><LogOut size={17} /></a></div>
    </div></header>

    <main className="main-content live-universe-main">
      <section className="backtest-hero live-universe-hero"><div><span className="section-kicker">Signals · Universe</span><h1>Live Signal Universe</h1><p>Rank historically tested symbols, apply a completed-close price range, then freeze a reproducible list for paper trading.</p></div><div className="strategy-safety"><strong>No strategy change</strong><span>RSI Recovery 1.1.0 · no ATR gate · no live orders</span></div></section>

      {error && <div className="backtest-error" role="alert">{error}</div>}
      {loading ? <section className="backtest-panel universe-loading"><LoaderCircle className="spin" size={20} />Loading validated historical metrics…</section> : <>
        <section className="backtest-panel universe-config-panel">
          <div className="panel-title"><div><span className="section-kicker">Selection configuration</span><h2>Build from validated history</h2></div><span className="date-window">Quality is a research ranking, not probability of profit</span></div>
          <div className="universe-control-grid">
            <label><span>Number of symbols</span><input type="number" min="1" max="750" value={topN} onChange={(event) => setTopN(Number(event.target.value))} /><small>Up to the eligible universe</small></label>
            <label><span>Minimum share price</span><div className="rupee-input"><IndianRupee size={14} /><input type="number" min="0" step="0.05" value={minimumPrice} onChange={(event) => setMinimumPrice(Number(event.target.value))} /></div><small>Inclusive completed close</small></label>
            <label><span>Maximum share price</span><div className="rupee-input"><IndianRupee size={14} /><input type="number" min="0.01" step="0.05" value={maximumPrice} onChange={(event) => setMaximumPrice(Number(event.target.value))} /></div><small>Inclusive completed close</small></label>
            <label><span>Ranking</span><select value={rankingMode} onChange={(event) => setRankingMode(event.target.value as RankingMode)}>{(config?.rankingModes ?? [{ value: "QUALITY", label: "Quality Score" }]).map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}</select><small>Default remains Quality</small></label>
            <label><span>Minimum historical BUY observations</span><input type="number" min="1" value={minimumBuyObservations} onChange={(event) => setMinimumBuyObservations(Number(event.target.value))} /><small>Default 50 · historical P10 is {number(config?.historicalBuyObservationDistribution.p10, 0)}</small></label>
          </div>
          <div className="universe-presets"><span>Top</span>{[100, 200, 300, 500].map((value) => <button key={value} type="button" className={topN === value ? "active" : ""} onClick={() => topPreset(value)}>{value}</button>)}<span>Price</span>{[[300, 1000], [500, 2000], [1000, 3000]].map(([minimum, maximum]) => <button key={`${minimum}-${maximum}`} type="button" className={minimumPrice === minimum && maximumPrice === maximum ? "active" : ""} onClick={() => pricePreset(minimum, maximum)}>₹{minimum}–₹{maximum}</button>)}</div>
          <details className="universe-advanced"><summary>Advanced eligibility and manual overrides</summary><p>{config?.minimumBuyObservationRationale}</p><div className="universe-control-grid advanced-grid">
            <label><span><Pin size={13} /> Pin symbols</span><input value={manualPins} onChange={(event) => setManualPins(event.target.value)} placeholder="e.g. RELIANCE, SBIN" /><small>Must still pass price, data and sample validity</small></label>
            <label><span><Ban size={13} /> Exclude symbols</span><input value={manualExclusions} onChange={(event) => setManualExclusions(event.target.value)} placeholder="e.g. ABC, XYZ" /><small>Stored separately from calculated ranking</small></label>
            <label><span>Minimum GOOD rate %</span><input type="number" min="0" max="100" value={minimumGoodRate} onChange={(event) => setMinimumGoodRate(event.target.value)} placeholder="Disabled" /></label>
            <label><span>Maximum OPEN rate %</span><input type="number" min="0" max="100" value={maximumOpenRate} onChange={(event) => setMaximumOpenRate(event.target.value)} placeholder="Disabled" /></label>
            <label><span>Maximum median target minutes</span><input type="number" min="1" value={maximumMedianTargetMinutes} onChange={(event) => setMaximumMedianTargetMinutes(event.target.value)} placeholder="Disabled" /></label>
            <label><span>Minimum target achievement %</span><input type="number" min="0" max="100" value={minimumTargetHitRate} onChange={(event) => setMinimumTargetHitRate(event.target.value)} placeholder="Disabled" /></label>
            <label><span>Minimum acceptable median MAE %</span><input type="number" min="-100" max="0" value={minimumMedianMaePct} onChange={(event) => setMinimumMedianMaePct(event.target.value)} placeholder="Disabled" /></label>
          </div></details>
          <div className="universe-actions"><button type="button" className="run-backtest" disabled={working} onClick={() => void buildPreview()}>{working ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}{active ? "Rebuild universe preview" : "Preview universe"}</button>{previewRequest && preview?.status === "PREVIEW" && <button type="button" className="freeze-universe" disabled={working} onClick={() => void saveAndFreeze()}><LockKeyhole size={15} />{active ? "Confirm new frozen version" : "Save & Freeze"}</button>}</div>
        </section>

        {displayed && <>
          <section className="universe-summary-grid" aria-label="Universe preview summary">
            <div><span>Total NSE symbols</span><strong>{displayed.statistics.totalNseSymbols}</strong></div>
            <div><span>Data-quality eligible</span><strong>{displayed.statistics.dataQualityEligible}</strong></div>
            <div><span>Price eligible</span><strong>{displayed.statistics.priceEligible}</strong><small>₹{number(displayed.configuration.minimumPrice, 0)}–₹{number(displayed.configuration.maximumPrice, 0)}</small></div>
            <div><span>Requested Top N</span><strong>{displayed.statistics.requestedTopN}</strong></div>
            <div><span>Final live symbols</span><strong>{displayed.statistics.selected}</strong><small>{displayed.statistics.pinned} pinned · {displayed.statistics.manuallyExcluded} excluded</small></div>
            <div><span>Price as of</span><strong className="summary-time">{formatIst(displayed.source.priceAsOf)}</strong><small>{priceAge(displayed.source.priceAsOf)}</small></div>
          </section>

          <section className="backtest-panel universe-source-panel"><div><span>Historical ranking</span><strong>{displayed.source.strategyVersion}</strong><small>{formatIst(displayed.source.backtestFrom)} – {formatIst(displayed.source.backtestTo)}</small></div><div><span>Ranking source</span><strong>{displayed.ranking.mode}</strong><small>{displayed.ranking.qualityFormula}</small></div><div><span>Universe state</span><strong>{displayed.frozen ? `Frozen · ${displayed.universeVersion}` : "Preview only"}</strong><small>Price changes never alter a frozen list automatically</small></div>{active && <a className="download-button" href="/api/live-universe?action=export&version=active"><Download size={14} />CSV</a>}</section>

          {preview?.status === "PREVIEW" && active && <section className="backtest-panel universe-diff"><div><span>Added</span><strong>{preview.differences.added.length}</strong><small>{preview.differences.added.slice(0, 12).map((item) => item.symbol).join(", ") || "None"}</small></div><div><span>Removed</span><strong>{preview.differences.removed.length}</strong><small>{preview.differences.removed.slice(0, 12).map((item) => `${item.symbol} · ${item.reason}`).join(", ") || "None"}</small></div><div><span>Unchanged</span><strong>{preview.differences.unchanged.length}</strong><small>Active version is unchanged until confirmation</small></div></section>}

          <section className="backtest-panel universe-comparison"><div className="panel-title"><div><span className="section-kicker">Historical observation profile</span><h2>Selected versus validated universe</h2></div></div><div className="universe-comparison-grid"><span>Group</span><span>Signals</span><span>GOOD</span><span>BAD</span><span>OPEN</span><span>Target hit</span><span>Median target</span><span>Median MAE</span>{[["Selected", displayed.aggregates.selected], ["All 749", displayed.aggregates.fullValidatedUniverse], ["Next eligible", displayed.aggregates.nextEligible100]].map(([label, value]) => { const aggregate = value as Aggregate; return <div className="contents" key={label as string}><strong>{label as string}</strong><span>{aggregate.buyObservations.toLocaleString("en-IN")}</span><span>{number(aggregate.goodRate)}%</span><span>{number(aggregate.badRate)}%</span><span>{number(aggregate.openRate)}%</span><span>{number(aggregate.targetHitRate)}%</span><span>{duration(aggregate.medianTargetMinutes)}</span><span>{number(aggregate.medianMaePct, 3)}%</span></div>; })}</div></section>

          <section className="backtest-panel universe-table-panel">
            <div className="panel-title"><div><span className="section-kicker">Auditable selection</span><h2>{view === "selected" ? `${displayed.statistics.selected} selected symbols` : "Excluded symbols and reasons"}</h2></div></div>
            <nav className="recovery-result-tabs universe-tabs" aria-label="Universe eligibility views">
              <button type="button" className={view === "selected" ? "active" : ""} onClick={() => setView("selected")}>Selected · {displayed.statistics.selected}</button>
              <button type="button" className={view === "below" ? "active" : ""} onClick={() => setView("below")}>Below Top-N</button>
              <button type="button" className={view === "price" ? "active" : ""} onClick={() => setView("price")}>Price excluded · {displayed.statistics.priceBelowMinimum + displayed.statistics.priceAboveMaximum}</button>
              <button type="button" className={view === "quality" ? "active" : ""} onClick={() => setView("quality")}>Data / sample excluded</button>
            </nav>
            {view === "selected" ? <div className="universe-table-wrap"><table className="universe-table"><thead><tr>{[["Rank", "rank"], ["Symbol", "symbol"], ["Price", "referencePrice"], ["Quality", "qualityScore"], ["GOOD", "goodRate"], ["BAD", "badRate"], ["Target hit", "historicalTargetHitRate"], ["Median target", "medianTargetMinutes"], ["Median MAE", "medianMaePct"], ["OPEN", "openRate"], ["BUYs", "buyObservations"]].map(([label, key]) => <th key={key}><button type="button" onClick={() => sort(key as SortKey)}>{label}{sortKey === key ? (sortDirection === "asc" ? " ↑" : " ↓") : ""}</button></th>)}</tr></thead><tbody>{sortedSelected.map((row) => <tr key={row.symbol}><td>{row.rank}</td><td><strong>{row.symbol}</strong>{row.isPinned && <small className="pinned-label">PINNED</small>}</td><td>₹{number(row.referencePrice)}</td><td>{number(row.qualityScore, 1)}</td><td>{number(row.goodRate)}%</td><td>{number(row.badRate)}%</td><td>{number(row.historicalTargetHitRate)}%</td><td>{duration(row.medianTargetMinutes)}</td><td>{number(row.medianMaePct, 3)}%</td><td>{number(row.openRate)}%</td><td>{row.buyObservations}</td></tr>)}</tbody></table></div> : <div className="universe-excluded-list">{excludedRows.length ? excludedRows.map((row, index) => <div key={`${row.symbol}-${row.reason}-${index}`}><strong>{row.symbol ?? `${row.reason} (${row.buyObservations ?? 0})`}</strong><span>{row.reason.replaceAll("_", " ")}</span><span>{row.referencePrice != null ? `₹${number(row.referencePrice)}` : row.detail ?? "Not eligible"}</span><span>{row.qualityScore != null ? `Quality ${number(row.qualityScore, 1)}` : ""}</span></div>) : <p>No symbols in this category.</p>}</div>}
          </section>

          <section className="backtest-panel universe-distributions"><div><span>Selected price distribution</span><strong>₹{number(displayed.distributions.selectedReferencePrice.min)} – ₹{number(displayed.distributions.selectedReferencePrice.max)}</strong><small>P10 ₹{number(displayed.distributions.selectedReferencePrice.p10)} · P25 ₹{number(displayed.distributions.selectedReferencePrice.p25)} · median ₹{number(displayed.distributions.selectedReferencePrice.median)} · P75 ₹{number(displayed.distributions.selectedReferencePrice.p75)} · P90 ₹{number(displayed.distributions.selectedReferencePrice.p90)}</small></div><div><span>Selected BUY-count distribution</span><strong>{number(displayed.distributions.selectedBuyObservations.min, 0)} – {number(displayed.distributions.selectedBuyObservations.max, 0)}</strong><small>P10 {number(displayed.distributions.selectedBuyObservations.p10, 0)} · P25 {number(displayed.distributions.selectedBuyObservations.p25, 0)} · median {number(displayed.distributions.selectedBuyObservations.median, 0)} · P75 {number(displayed.distributions.selectedBuyObservations.p75, 0)} · P90 {number(displayed.distributions.selectedBuyObservations.p90, 0)}</small></div></section>

          {history.length > 0 && <section className="backtest-panel universe-history"><div className="panel-title"><div><span className="section-kicker">Audit history</span><h2>Frozen universe versions</h2></div></div>{history.map((item) => <div key={item.universeVersion}><strong>{item.universeVersion}</strong><span>{item.selected} symbols</span><span>₹{number(item.configuration.minimumPrice, 0)}–₹{number(item.configuration.maximumPrice, 0)}</span><span>{formatIst(item.priceAsOf)}</span><a href={`/api/live-universe?action=export&version=${encodeURIComponent(item.universeVersion)}`}><Download size={13} />CSV</a></div>)}</section>}
        </>}
      </>}
    </main>
  </div>;
}
