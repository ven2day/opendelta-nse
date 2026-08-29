"use client";
/* eslint-disable @next/next/no-html-link-for-pages -- Native navigation matches the existing production shell. */

import {
  Activity,
  BellRing,
  CheckCircle2,
  Clock3,
  Eye,
  IndianRupee,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Radio,
  RefreshCw,
  ScanSearch,
  Settings2,
  TrendingUp,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { formatGlobalPriceRange, isPriceInGlobalRange, parseGlobalSettings, type GlobalPriceRange } from "../global-settings-shared";

type Tab = "live" | "watch" | "paper" | "history";
type SortKey = "signalTimestamp" | "rank" | "qualityScore" | "goodRate" | "medianTargetMinutes" | "atrPct" | "volumeRatio" | "distanceToResistancePct";

type EngineStatus = {
  connectionStatus: string;
  engineStatus: string;
  message: string;
  universeVersion: string | null;
  universeFrozen: boolean;
  monitoredSymbols: number;
  subscribedSymbols: number;
  timeframe: string;
  strategyVersion: string;
  lastCompletedCandle: string | null;
  lastMarketDataTimestamp: string | null;
  dataAgeSeconds: number | null;
  marketSession: string;
  paperOnly: boolean;
  liveOrdersEnabled: boolean;
  oiFilterMode: "OFF" | "ADVISORY" | "ENFORCED";
  oiRegime: OiRegime | null;
  oiHistory: OiHistoryStatus | null;
};

type OiHistoryStatus = {
  state: string;
  request?: { fromDate?: string; toDate?: string };
  optionRowsImported?: number;
  enforcementReady?: boolean;
  reason?: string;
};

type OiRegime = {
  regime: string;
  combinedScore: number | null;
  confidence: string;
  sourceTimestamp: string | null;
  dataAgeSeconds: number | null;
  reason: string;
  options?: { score?: number | null };
  futures?: { score?: number | null };
  spot?: { score?: number | null };
};

type Settings = {
  entryRangeMethod: "FIXED_PERCENT" | "ATR_BASED";
  fixedLowerPct: number;
  fixedUpperPct: number;
  atrLowerMultiplier: number;
  atrUpperMultiplier: number;
  paperAllocation: number;
  staleDataSeconds: number;
  freshMinutes: number;
  recentMinutes: number;
  supportLookbackShort: number;
  supportLookbackLong: number;
  oiFilterMode: "OFF" | "ADVISORY" | "ENFORCED";
  oiLookbackBars: number;
  oiStrikesEachSide: number;
  oiMinimumPriceChangePct: number;
  oiMinimumChangePct: number;
  oiMaximumSpreadPct: number;
  oiStaleDataSeconds: number;
  oiMinimumValidContractFraction: number;
  oiMinimumFuturesVolume: number;
  oiVolatilityPriceRisePct: number;
  oiVolatilityIvRise: number;
  oiMinimumCoverage: number;
  oiOptionsWeight: number;
  oiFuturesWeight: number;
  oiSpotWeight: number;
  oiStronglyBearishThreshold: number;
  oiBearishThreshold: number;
  oiBullishThreshold: number;
  oiStronglyBullishThreshold: number;
  oiElevatedQualityThreshold: number;
  oiFailPolicy: "SKIP" | "ALLOW";
};

type Signal = {
  signalId: string;
  symbol: string;
  universeVersion: string;
  strategyVersion: string;
  timeframe: string;
  signalTimestamp: string;
  marketDataTimestamp: string;
  dataAgeSeconds: number;
  signalClose: number;
  currentPrice: number | null;
  currentPriceTimestamp: string | null;
  buyRangeStatus: "IN_RANGE" | "ABOVE_RANGE" | "BELOW_RANGE" | "UNAVAILABLE";
  signalAgeMinutes: number;
  freshness: "FRESH" | "RECENT" | "OLD";
  rsi: number;
  rsiMinimumSinceArm: number;
  rsiArmTimestamp: string;
  barsArmToRecovery: number;
  ema9: number;
  ema20: number;
  emaSpreadPct: number;
  vwap: number;
  vwapDistancePct: number;
  volumeRatio: number;
  confirmationScore: number;
  emaConfirmation: boolean;
  vwapConfirmation: boolean;
  volumeConfirmation: boolean;
  atr14: number;
  atrPct: number;
  momentum15m: number;
  momentum30m: number;
  manualAction: "NO_ACTION" | "WATCH" | "IGNORE" | "PAPER_BUY";
  ignoreReason?: string | null;
  notes?: string | null;
  buyRange: { low: number; midpoint: number; high: number; method: string; formula: string };
  quantitySuggestion: {
    allocation: number;
    recommendedQuantity: number;
    quantityAtLower: number;
    quantityAtMidpoint: number;
    quantityAtUpper: number;
    referenceEntryMidpoint: number;
  };
  indicativeTargets: { atLower: number; atMidpoint: number; atUpper: number };
  historicalContext: {
    rank: number | null;
    qualityScore: number | null;
    goodRate: number | null;
    targetHitRate: number | null;
    medianTargetMinutes: number | null;
    medianMaePct: number | null;
    openRate: number | null;
    buyObservations: number | null;
  };
  supportResistance: {
    support: number | null;
    resistance: number | null;
    distanceToSupportPct: number | null;
    distanceToResistancePct: number | null;
    targetRoom: "TIGHT" | "CLEAR" | "UNAVAILABLE";
    resistanceBeforeTarget: boolean;
    supportSource?: string | null;
    resistanceSource?: string | null;
    previousSessionHigh?: number | null;
    previousSessionLow?: number | null;
    previousSessionClose?: number | null;
  };
  hypotheticalOutcome: {
    status: "OPEN" | "TARGET_HIT";
    targetHitTimestamp: string | null;
    durationMinutes: number | null;
    maePct: number | null;
    mfePct: number | null;
    lastClose: number;
  };
  oiFilterMode: "OFF" | "ADVISORY" | "ENFORCED";
  oiRegimeAtSignal: string | null;
  oiScoreAtSignal: number | null;
  oiConfidence: string | null;
  oiDecision: string;
  oiDecisionReason: string;
  oiSourceTimestamp: string | null;
  executionEligible: boolean;
};

type PaperTrade = {
  paperTradeId: string;
  signalId: string;
  symbol: string;
  entryTimestamp: string;
  entryPrice: number;
  quantity: number;
  paperAmount: number;
  targetPrice: number;
  status: "OPEN" | "TARGET_HIT" | "MANUALLY_CLOSED";
  currentPrice: number;
  currentPnl: number;
  currentPnlPct: number;
  targetProgressPct: number;
  ageMinutes: number;
  maePct: number | null;
  mfePct: number | null;
  exitTimestamp?: string | null;
  exitPrice?: number | null;
};

type Study = {
  signalsGenerated: number;
  paperBought: number;
  watched: number;
  ignored: number;
  noAction: number;
  paperTargetsHit: number;
  paperPositionsOpen: number;
  systemSignal2h?: { rate: number | null };
  paperSelected2h?: { rate: number | null };
  ignored2h?: { rate: number | null };
};

const EMPTY_STATUS: EngineStatus = {
  connectionStatus: "DISCONNECTED", engineStatus: "STARTING", message: "Loading live-signal runtime", universeVersion: null,
  universeFrozen: false, monitoredSymbols: 0, subscribedSymbols: 0, timeframe: "5m", strategyVersion: "rsi-recovery-1.1.0",
  lastCompletedCandle: null, lastMarketDataTimestamp: null, dataAgeSeconds: null, marketSession: "CLOSED", paperOnly: true, liveOrdersEnabled: false,
  oiFilterMode: "OFF", oiRegime: null, oiHistory: null,
};

function money(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : `₹${value.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function number(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function percent(value: number | null | undefined, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : `${value >= 0 ? "+" : ""}${number(value, digits)}%`;
}

function formatIst(value: string | null | undefined) {
  if (!value) return "No completed candle yet";
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true }).format(new Date(value)) + " IST";
}

function duration(minutes: number | null | undefined) {
  if (minutes == null || !Number.isFinite(minutes)) return "—";
  if (minutes < 60) return `${Math.round(minutes)}m`;
  if (minutes < 1440) return `${number(minutes / 60, 1)}h`;
  return `${number(minutes / 1440, 1)}d`;
}

async function payload(response: Response) {
  const text = await response.text();
  try { return JSON.parse(text); } catch { return { detail: text || "The service returned an unreadable response" }; }
}

async function mutation(action: string, body: unknown, id?: { signalId?: string; paperTradeId?: string }) {
  const query = new URLSearchParams({ action, ...Object.fromEntries(Object.entries(id ?? {}).filter(([, value]) => value)) as Record<string, string> });
  const response = await fetch(`/api/live-signals?${query}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const result = await payload(response);
  if (!response.ok) throw new Error(result.detail ?? "Unable to save change");
  return result;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return <div className={`signal-metric ${tone ?? ""}`}><span>{label}</span><strong>{value}</strong></div>;
}

function SignalCard({ signal, readOnly, onPaper, onWatch, onIgnore }: {
  signal: Signal; readOnly?: boolean; onPaper: (signal: Signal) => void; onWatch: (signal: Signal) => void; onIgnore: (signal: Signal) => void;
}) {
  const historical = signal.historicalContext;
  const levels = signal.supportResistance;
  const statusCopy = signal.buyRangeStatus === "ABOVE_RANGE" ? "ABOVE BUY RANGE · DO NOT CHASE" : signal.buyRangeStatus === "BELOW_RANGE" ? "BELOW ORIGINAL BUY RANGE · RE-EVALUATE" : signal.buyRangeStatus.replaceAll("_", " ");
  return <article className={`live-signal-card ${signal.buyRangeStatus.toLowerCase()} ${signal.freshness.toLowerCase()}`}>
    <div className="signal-card-head">
      <div><span className="buy-badge">BUY</span><div><h2>{signal.symbol}</h2><span>{formatIst(signal.signalTimestamp)} · {duration(signal.signalAgeMinutes)} ago · {signal.freshness}</span></div></div>
      <div className="signal-rank"><span>Historical rank</span><strong>#{historical.rank ?? "—"} / 300</strong></div>
    </div>
    <div className="signal-price-strip">
      <Metric label="Live price" value={money(signal.currentPrice)} />
      <Metric label="Signal price" value={money(signal.signalClose)} />
      <Metric label="Suggested buy range" value={`${money(signal.buyRange.low)} – ${money(signal.buyRange.high)}`} />
      <Metric label="Status" value={statusCopy} tone={signal.buyRangeStatus.toLowerCase()} />
      <Metric label="Suggested quantity" value={`${signal.quantitySuggestion.recommendedQuantity} shares`} />
      <Metric label="Paper allocation" value={money(signal.quantitySuggestion.allocation, 0)} />
      <Metric label="Indicative target" value={`${money(signal.indicativeTargets.atLower)} – ${money(signal.indicativeTargets.atUpper)}`} />
    </div>
    <div className="signal-context-grid">
      <section><h3>Historical quality</h3><div className="signal-detail-grid">
        <Metric label="Quality score" value={number(historical.qualityScore)} />
        <Metric label="GOOD ≤2h" value={`${number(historical.goodRate)}%`} />
        <Metric label="Target achieved" value={`${number(historical.targetHitRate)}%`} />
        <Metric label="Median target" value={duration(historical.medianTargetMinutes)} />
        <Metric label="Median MAE" value={percent(historical.medianMaePct)} />
        <Metric label="Historical OPEN" value={`${number(historical.openRate)}%`} />
        <Metric label="Historical BUYs" value={number(historical.buyObservations, 0)} />
      </div></section>
      <section><h3>Signal candle context</h3><div className="signal-detail-grid">
        <Metric label="RSI" value={number(signal.rsi)} />
        <Metric label="Min RSI since arm" value={number(signal.rsiMinimumSinceArm)} />
        <Metric label="Arm → recovery" value={`${signal.barsArmToRecovery} bars`} />
        <Metric label="Confirmations" value={`${signal.confirmationScore}/3`} />
        <Metric label="EMA spread" value={percent(signal.emaSpreadPct, 3)} />
        <Metric label="VWAP distance" value={percent(signal.vwapDistancePct, 3)} />
        <Metric label="Volume ratio" value={`${number(signal.volumeRatio)}×`} />
        <Metric label="ATR" value={percent(signal.atrPct, 3)} />
        <Metric label="15m momentum" value={percent(signal.momentum15m, 3)} />
        <Metric label="30m momentum" value={percent(signal.momentum30m, 3)} />
      </div><div className="confirmation-pills"><span className={signal.emaConfirmation ? "pass" : "fail"}>EMA</span><span className={signal.vwapConfirmation ? "pass" : "fail"}>VWAP</span><span className={signal.volumeConfirmation ? "pass" : "fail"}>VOLUME</span></div></section>
      <section><h3>Strategy isolation</h3><div className="signal-detail-grid"><Metric label="Strategy" value="RSI Recovery Scalping" /><Metric label="OI execution gate" value="NOT APPLIED" /></div><small>Market-Aligned VWAP Pullback Scalper is a separate backtest strategy. This signal preserves RSI Recovery behavior.</small></section>
      <section><h3>Support and target room</h3><div className="signal-detail-grid">
        <Metric label="Recent support" value={money(levels.support)} />
        <Metric label="Distance to support" value={percent(levels.distanceToSupportPct)} />
        <Metric label="Recent resistance" value={money(levels.resistance)} />
        <Metric label="Room to resistance" value={percent(levels.distanceToResistancePct)} />
        <Metric label="Target required" value="+0.50%" />
        <Metric label="Target room" value={levels.targetRoom} tone={levels.targetRoom.toLowerCase()} />
      </div>{levels.resistanceBeforeTarget && <div className="resistance-warning">Resistance lies before the indicative target</div>}<small>{levels.supportSource ?? "Causal recent lows"} · {levels.resistanceSource ?? "Causal recent highs"}</small></section>
    </div>
    <div className="signal-card-foot">
      <div><span>Entry range method: {signal.buyRange.method.replaceAll("_", " ")} heuristic</span><small>{signal.buyRange.formula}</small></div>
      {!readOnly && signal.manualAction !== "PAPER_BUY" && <div className="signal-actions"><button className="paper-buy-button" title="Record a paper BUY" onClick={() => onPaper(signal)}><IndianRupee size={14} />Paper buy</button>{signal.manualAction !== "WATCH" && <button onClick={() => onWatch(signal)}><Eye size={14} />Watch</button>}<button onClick={() => onIgnore(signal)}><X size={14} />Ignore</button></div>}
      {signal.manualAction !== "NO_ACTION" && <span className={`manual-action ${signal.manualAction.toLowerCase()}`}>{signal.manualAction.replace("_", " ")}</span>}
    </div>
  </article>;
}

export function SignalsWorkspace({ userName, signOutHref, initialGlobalPriceRange }: { userName: string; signOutHref: string; initialGlobalPriceRange: GlobalPriceRange }) {
  const [tab, setTab] = useState<Tab>("live");
  const [signals, setSignals] = useState<Signal[]>([]);
  const [paperTrades, setPaperTrades] = useState<PaperTrade[]>([]);
  const [status, setStatus] = useState<EngineStatus>(EMPTY_STATUS);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [draftSettings, setDraftSettings] = useState<Settings | null>(null);
  const [study, setStudy] = useState<Study | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [paperSignal, setPaperSignal] = useState<Signal | null>(null);
  const [paperEntry, setPaperEntry] = useState(0);
  const [paperQuantity, setPaperQuantity] = useState(0);
  const [closingTrade, setClosingTrade] = useState<PaperTrade | null>(null);
  const [closePrice, setClosePrice] = useState(0);
  const [ignoreSignal, setIgnoreSignal] = useState<Signal | null>(null);
  const [ignoreReason, setIgnoreReason] = useState("Manual chart rejection");
  const [rangeFilter, setRangeFilter] = useState("ALL");
  const [confirmationFilter, setConfirmationFilter] = useState("ALL");
  const [rankFilter, setRankFilter] = useState("ALL");
  const [sortKey, setSortKey] = useState<SortKey>("signalTimestamp");
  const [globalPriceRange, setGlobalPriceRange] = useState(initialGlobalPriceRange);
  const seenSignals = useRef<Set<string>>(new Set());

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [signalResponse, settingsResponse, paperResponse, globalSettingsResponse] = await Promise.all([
        fetch("/api/live-signals?action=signals", { cache: "no-store" }),
        fetch("/api/live-signals?action=settings", { cache: "no-store" }),
        fetch("/api/live-signals?action=paper", { cache: "no-store" }),
        fetch("/api/global-settings", { cache: "no-store" }),
      ]);
      const [signalBody, settingsBody, paperBody, globalSettingsBody] = await Promise.all([payload(signalResponse), payload(settingsResponse), payload(paperResponse), payload(globalSettingsResponse)]);
      if (!signalResponse.ok) throw new Error(signalBody.detail ?? "Unable to load live signals");
      if (!settingsResponse.ok) throw new Error(settingsBody.detail ?? "Unable to load signal settings");
      if (!paperResponse.ok) throw new Error(paperBody.detail ?? "Unable to load paper positions");
      if (globalSettingsResponse.ok) setGlobalPriceRange(parseGlobalSettings(globalSettingsBody).priceRange);
      const incoming: Signal[] = signalBody.signals ?? [];
      if (seenSignals.current.size) {
        const fresh = incoming.filter((item) => !seenSignals.current.has(item.signalId));
        if (fresh.length) setNotice(`${fresh.length} new completed-candle BUY signal${fresh.length === 1 ? "" : "s"}`);
      }
      seenSignals.current = new Set(incoming.map((item) => item.signalId));
      setSignals(incoming);
      setStatus(signalBody.status ?? EMPTY_STATUS);
      setStudy(signalBody.study ?? null);
      setSettings(settingsBody.settings);
      setDraftSettings((current) => current ?? settingsBody.settings);
      setPaperTrades(paperBody.paperTrades ?? []);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load live signals");
    } finally { if (!quiet) setLoading(false); }
  }, []);

  // Polling is the presentation transport; market ingestion remains server-side streaming.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); const timer = window.setInterval(() => void load(true), 10_000); return () => window.clearInterval(timer); }, [load]);

  const decide = async (signal: Signal, action: "WATCH" | "IGNORE", reason?: string) => {
    setWorking(true); setError("");
    try { await mutation("decision", { action, reason }, { signalId: signal.signalId }); setIgnoreSignal(null); await load(true); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to save decision"); }
    finally { setWorking(false); }
  };

  const openPaper = (signal: Signal) => {
    setPaperSignal(signal);
    setPaperEntry(signal.currentPrice ?? signal.buyRange.midpoint);
    setPaperQuantity(signal.quantitySuggestion.recommendedQuantity);
  };

  const confirmPaper = async () => {
    if (!paperSignal) return;
    setWorking(true); setError("");
    try { await mutation("paper-buy", { actualEntryPrice: paperEntry, actualQuantity: paperQuantity }, { signalId: paperSignal.signalId }); setPaperSignal(null); await load(true); setTab("paper"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to create paper trade"); }
    finally { setWorking(false); }
  };

  const closePaper = async (trade: PaperTrade) => {
    setWorking(true); setError("");
    try { await mutation("close", { actualExitPrice: closePrice, notes: "Manual close from Signals" }, { paperTradeId: trade.paperTradeId }); setClosingTrade(null); await load(true); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to close paper trade"); }
    finally { setWorking(false); }
  };

  const saveSettings = async () => {
    if (!draftSettings) return;
    setWorking(true); setError("");
    try { await mutation("settings", draftSettings); setSettings(draftSettings); setDraftSettings(draftSettings); setSettingsOpen(false); await load(true); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to save settings"); }
    finally { setWorking(false); }
  };

  const visibleSignals = useMemo(() => {
    const base = tab === "watch" ? signals.filter((item) => item.manualAction === "WATCH") : tab === "live" ? signals.filter((item) => item.manualAction === "NO_ACTION") : signals;
    return base.filter((item) => isPriceInGlobalRange(item.currentPrice ?? item.signalClose, globalPriceRange))
      .filter((item) => rangeFilter === "ALL" || item.buyRangeStatus === rangeFilter)
      .filter((item) => confirmationFilter === "ALL" || item.confirmationScore === Number(confirmationFilter))
      .filter((item) => {
        const rank = item.historicalContext.rank ?? 9999;
        if (rankFilter === "1-50") return rank <= 50;
        if (rankFilter === "51-100") return rank >= 51 && rank <= 100;
        if (rankFilter === "101-200") return rank >= 101 && rank <= 200;
        if (rankFilter === "201-300") return rank >= 201 && rank <= 300;
        return true;
      }).sort((left, right) => {
        if (sortKey === "signalTimestamp") return Date.parse(right.signalTimestamp) - Date.parse(left.signalTimestamp);
        const a = sortKey === "distanceToResistancePct" ? left.supportResistance.distanceToResistancePct : left.historicalContext[sortKey as keyof Signal["historicalContext"]] ?? left[sortKey as keyof Signal];
        const b = sortKey === "distanceToResistancePct" ? right.supportResistance.distanceToResistancePct : right.historicalContext[sortKey as keyof Signal["historicalContext"]] ?? right[sortKey as keyof Signal];
        if (sortKey === "rank" || sortKey === "medianTargetMinutes") return Number(a ?? Infinity) - Number(b ?? Infinity);
        return Number(b ?? -Infinity) - Number(a ?? -Infinity);
      });
  }, [confirmationFilter, globalPriceRange, rangeFilter, rankFilter, signals, sortKey, tab]);

  const initials = userName.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
  const connected = status.connectionStatus === "CONNECTED";

  return <div className="site-shell backtest-shell signals-shell">
    <header className="global-header"><div className="header-inner">
      <a className="brand" href="/"><div className="brand-mark" aria-hidden="true">₹</div><div><strong>OpenDelta</strong><span>Market intelligence</span></div></a>
      <nav className="top-nav" aria-label="Main navigation"><a className="nav-item" href="/"><LayoutDashboard size={16} />Dashboard</a><a className="nav-item" href="/scanner"><ScanSearch size={16} />Stock Scanner</a><a className="nav-item" href="/backtest"><TrendingUp size={16} />Backtest</a><a className="nav-item active" href="/signals" aria-current="page"><Radio size={16} />Signals</a><a className="nav-item" href="/admin"><Settings2 size={16} />Admin</a></nav>
      <div className="header-actions"><div className="user-chip"><div className="avatar">{initials}</div><span>{userName}</span></div><a href={signOutHref} className="icon-button" aria-label="Sign out"><LogOut size={17} /></a></div>
    </div></header>

    <main className="main-content signals-main">
      <nav className="market-workspace-tabs" aria-label="Market workspace"><a className="active" href="/signals">NSE</a><a href="/signals/crypto">Crypto &amp; metals</a></nav>
      <section className="signals-healthbar">
        <div className="signals-health-title"><span className="section-kicker">Completed-candle research monitor</span><h1>Signals</h1></div>
        <div className={`health-item ${connected ? "healthy" : "warning"}`}>{connected ? <Wifi size={16} /> : <WifiOff size={16} />}<div><span>Dhan market data</span><strong>{status.connectionStatus}</strong></div></div>
        <div className={`health-item ${status.engineStatus === "READY" ? "healthy" : "warning"}`}><Activity size={16} /><div><span>Signal engine</span><strong>{status.engineStatus.replaceAll("_", " ")}</strong></div></div>
        <div className={`health-item ${status.universeFrozen ? "healthy" : "warning"}`}><LockKeyhole size={16} /><div><span>Universe</span><strong>{status.universeVersion ?? "Unavailable"} · {status.universeFrozen ? "Frozen" : "Not frozen"}</strong></div></div>
        <div className="health-item"><Clock3 size={16} /><div><span>Last completed candle</span><strong>{formatIst(status.lastCompletedCandle)}</strong></div></div>
        <div className="health-item"><Radio size={16} /><div><span>Monitored</span><strong>{status.monitoredSymbols} symbols · {status.timeframe}</strong></div></div>
        <button className="icon-button" onClick={() => void load()} aria-label="Refresh signals"><RefreshCw size={16} /></button><button className="icon-button" onClick={() => { setDraftSettings(settings); setSettingsOpen(true); }} aria-label="Signals settings"><Settings2 size={16} /></button>
      </section>

      <section className="backtest-panel oi-regime-card" aria-label="RSI Recovery strategy isolation">
        <div className="panel-title"><div><span className="section-kicker">RSI Recovery Scalping</span><h2>Existing live behavior preserved</h2></div><span className="date-window">OI gate: OFF</span></div>
        <small>Optional market context belongs to the separate Market-Aligned VWAP Pullback Scalper backtest. It does not block, create, or resize RSI Recovery Signals trades.</small>
      </section>

      {notice && <div className="signal-notice"><BellRing size={15} />{notice}<button onClick={() => setNotice("")} aria-label="Dismiss"><X size={14} /></button></div>}
      {error && <div className="backtest-error" role="alert">{error}</div>}
      <section className="signals-study-grid">
        <Metric label="Signals generated" value={number(study?.signalsGenerated, 0)} />
        <Metric label="Paper bought" value={number(study?.paperBought, 0)} />
        <Metric label="Watched" value={number(study?.watched, 0)} />
        <Metric label="Ignored" value={number(study?.ignored, 0)} />
        <Metric label="No action" value={number(study?.noAction, 0)} />
        <Metric label="Paper targets hit" value={number(study?.paperTargetsHit, 0)} />
        <Metric label="Paper positions open" value={number(study?.paperPositionsOpen, 0)} />
      </section>

      <nav className="signals-tabs" aria-label="Signals views"><button className={tab === "live" ? "active" : ""} onClick={() => setTab("live")}>Live signals</button><button className={tab === "watch" ? "active" : ""} onClick={() => setTab("watch")}>Watch</button><button className={tab === "paper" ? "active" : ""} onClick={() => setTab("paper")}>Paper positions</button><button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>History</button><a href="/signals?view=universe">Universe</a></nav>
      {tab !== "paper" && <a className="global-range-badge signals-global-range" href="/admin">Global price: {formatGlobalPriceRange(globalPriceRange)}</a>}

      {tab !== "paper" && <section className="signals-filterbar"><select aria-label="Buy range status" value={rangeFilter} onChange={(event) => setRangeFilter(event.target.value)}><option value="ALL">All range states</option><option value="IN_RANGE">In range</option><option value="ABOVE_RANGE">Above range</option><option value="BELOW_RANGE">Below range</option></select><select aria-label="Confirmation score" value={confirmationFilter} onChange={(event) => setConfirmationFilter(event.target.value)}><option value="ALL">All confirmations</option><option value="2">2/3 confirmations</option><option value="3">3/3 confirmations</option></select><select aria-label="Historical rank" value={rankFilter} onChange={(event) => setRankFilter(event.target.value)}><option value="ALL">All historical ranks</option><option value="1-50">Rank 1–50</option><option value="51-100">Rank 51–100</option><option value="101-200">Rank 101–200</option><option value="201-300">Rank 201–300</option></select><select aria-label="Sort signals" value={sortKey} onChange={(event) => setSortKey(event.target.value as SortKey)}><option value="signalTimestamp">Newest first</option><option value="rank">Historical rank</option><option value="qualityScore">Quality</option><option value="goodRate">GOOD rate</option><option value="medianTargetMinutes">Median target time</option><option value="atrPct">ATR</option><option value="volumeRatio">Volume ratio</option><option value="distanceToResistancePct">Distance to resistance</option></select><span>{visibleSignals.length} observations</span></section>}

      {loading ? <section className="backtest-panel signals-empty"><LoaderCircle className="spin" size={20} />Loading persisted signals and engine health…</section> : tab === "paper" ? <section className="paper-position-list">{paperTrades.length ? paperTrades.map((trade) => <article className="paper-position-card" key={trade.paperTradeId}><div><span className={`trade-status ${trade.status === "OPEN" ? "open" : "hit"}`}>{trade.status.replaceAll("_", " ")}</span><h2>{trade.symbol}</h2><small>{formatIst(trade.entryTimestamp)} · {duration(trade.ageMinutes)} old</small></div><Metric label="Actual paper entry" value={money(trade.entryPrice)} /><Metric label="Quantity" value={`${trade.quantity} shares`} /><Metric label="Paper amount" value={money(trade.paperAmount)} /><Metric label="Paper target" value={money(trade.targetPrice)} /><Metric label="Current" value={money(trade.currentPrice)} /><Metric label="Current P&L" value={`${money(trade.currentPnl)} · ${percent(trade.currentPnlPct)}`} tone={trade.currentPnl >= 0 ? "clear" : "tight"} /><Metric label="Target progress" value={`${number(trade.targetProgressPct)}%`} /><Metric label="MAE / MFE" value={`${percent(trade.maePct)} / ${percent(trade.mfePct)}`} />{trade.status === "OPEN" && <button disabled={working} onClick={() => { setClosingTrade(trade); setClosePrice(trade.currentPrice); }}>Close paper trade</button>}</article>) : <div className="backtest-panel signals-empty">No paper positions yet. A PAPER BUY records research data only and never sends a Dhan order.</div>}</section> : <section className="live-signal-list">{visibleSignals.length ? visibleSignals.map((signal) => <SignalCard key={signal.signalId} signal={signal} readOnly={tab === "history"} onPaper={openPaper} onWatch={(item) => void decide(item, "WATCH")} onIgnore={setIgnoreSignal} />) : <div className="backtest-panel signals-empty"><CheckCircle2 size={20} />No signals match this view. The engine waits for a completed 5-minute RSI arm → recovery candle with at least 2 of 3 confirmations.</div>}</section>}
    </main>

    {settingsOpen && draftSettings && <div className="signal-modal-backdrop" role="presentation"><section className="signal-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <button className="modal-close" onClick={() => setSettingsOpen(false)} aria-label="Close"><X /></button><span className="section-kicker">Paper decision support only</span><h2 id="settings-title">Signals settings</h2><p>These controls change entry suggestions and paper sizing. They do not change RSI Recovery v1.1.0.</p>
      <div className="signal-settings-grid"><label><span>Entry range method</span><select value={draftSettings.entryRangeMethod} onChange={(event) => setDraftSettings({ ...draftSettings, entryRangeMethod: event.target.value as Settings["entryRangeMethod"] })}><option value="FIXED_PERCENT">Fixed percent</option><option value="ATR_BASED">ATR based</option></select></label>
        {draftSettings.entryRangeMethod === "FIXED_PERCENT" ? <><label><span>Lower tolerance %</span><input type="number" min="0" step="0.01" value={draftSettings.fixedLowerPct} onChange={(event) => setDraftSettings({ ...draftSettings, fixedLowerPct: Number(event.target.value) })} /></label><label><span>Upper chase tolerance %</span><input type="number" min="0" step="0.01" value={draftSettings.fixedUpperPct} onChange={(event) => setDraftSettings({ ...draftSettings, fixedUpperPct: Number(event.target.value) })} /></label></> : <><label><span>Lower ATR multiplier</span><input type="number" min="0" step="0.05" value={draftSettings.atrLowerMultiplier} onChange={(event) => setDraftSettings({ ...draftSettings, atrLowerMultiplier: Number(event.target.value) })} /></label><label><span>Upper ATR multiplier</span><input type="number" min="0" step="0.05" value={draftSettings.atrUpperMultiplier} onChange={(event) => setDraftSettings({ ...draftSettings, atrUpperMultiplier: Number(event.target.value) })} /></label></>}
        <label><span>Default paper allocation</span><input type="number" min="1" step="1000" value={draftSettings.paperAllocation} onChange={(event) => setDraftSettings({ ...draftSettings, paperAllocation: Number(event.target.value) })} /></label><label><span>Stale-data threshold seconds</span><input type="number" min="10" value={draftSettings.staleDataSeconds} onChange={(event) => setDraftSettings({ ...draftSettings, staleDataSeconds: Number(event.target.value) })} /></label></div>
      {status.strategyVersion === "market-aligned-rsi-scalper-1.0.0" && <details className="advanced-settings"><summary>Advanced OI settings</summary><div className="signal-settings-grid">
        <label><span>Completed 5m lookback</span><input type="number" min="1" max="100" value={draftSettings.oiLookbackBars} onChange={(event) => setDraftSettings({ ...draftSettings, oiLookbackBars: Number(event.target.value) })} /></label>
        <label><span>Strikes each side of ATM</span><input type="number" min="0" max="20" value={draftSettings.oiStrikesEachSide} onChange={(event) => setDraftSettings({ ...draftSettings, oiStrikesEachSide: Number(event.target.value) })} /></label>
        <label><span>Minimum premium change %</span><input type="number" min="0" step="0.01" value={draftSettings.oiMinimumPriceChangePct} onChange={(event) => setDraftSettings({ ...draftSettings, oiMinimumPriceChangePct: Number(event.target.value) })} /></label>
        <label><span>Minimum OI change %</span><input type="number" min="0" step="0.1" value={draftSettings.oiMinimumChangePct} onChange={(event) => setDraftSettings({ ...draftSettings, oiMinimumChangePct: Number(event.target.value) })} /></label>
        <label><span>Maximum spread %</span><input type="number" min="0.01" step="0.5" value={draftSettings.oiMaximumSpreadPct} onChange={(event) => setDraftSettings({ ...draftSettings, oiMaximumSpreadPct: Number(event.target.value) })} /></label>
        <label><span>OI stale after seconds</span><input type="number" min="1" step="30" value={draftSettings.oiStaleDataSeconds} onChange={(event) => setDraftSettings({ ...draftSettings, oiStaleDataSeconds: Number(event.target.value) })} /></label>
        <label><span>Minimum valid contracts</span><input type="number" min="0.01" max="1" step="0.05" value={draftSettings.oiMinimumValidContractFraction} onChange={(event) => setDraftSettings({ ...draftSettings, oiMinimumValidContractFraction: Number(event.target.value) })} /></label>
        <label><span>Minimum futures volume</span><input type="number" min="0" step="1" value={draftSettings.oiMinimumFuturesVolume} onChange={(event) => setDraftSettings({ ...draftSettings, oiMinimumFuturesVolume: Number(event.target.value) })} /></label>
        <label><span>IV expansion premium rise %</span><input type="number" min="0" step="0.05" value={draftSettings.oiVolatilityPriceRisePct} onChange={(event) => setDraftSettings({ ...draftSettings, oiVolatilityPriceRisePct: Number(event.target.value) })} /></label>
        <label><span>IV expansion IV rise</span><input type="number" min="0" step="0.1" value={draftSettings.oiVolatilityIvRise} onChange={(event) => setDraftSettings({ ...draftSettings, oiVolatilityIvRise: Number(event.target.value) })} /></label>
        <label><span>Minimum coverage</span><input type="number" min="0.01" max="1" step="0.05" value={draftSettings.oiMinimumCoverage} onChange={(event) => setDraftSettings({ ...draftSettings, oiMinimumCoverage: Number(event.target.value) })} /></label>
        <label><span>Options weight</span><input type="number" min="0" max="1" step="0.05" value={draftSettings.oiOptionsWeight} onChange={(event) => setDraftSettings({ ...draftSettings, oiOptionsWeight: Number(event.target.value) })} /></label>
        <label><span>Futures weight</span><input type="number" min="0" max="1" step="0.05" value={draftSettings.oiFuturesWeight} onChange={(event) => setDraftSettings({ ...draftSettings, oiFuturesWeight: Number(event.target.value) })} /></label>
        <label><span>Spot trend weight</span><input type="number" min="0" max="1" step="0.05" value={draftSettings.oiSpotWeight} onChange={(event) => setDraftSettings({ ...draftSettings, oiSpotWeight: Number(event.target.value) })} /></label>
        <label><span>Strong bearish threshold</span><input type="number" min="-100" max="100" value={draftSettings.oiStronglyBearishThreshold} onChange={(event) => setDraftSettings({ ...draftSettings, oiStronglyBearishThreshold: Number(event.target.value) })} /></label>
        <label><span>Bearish threshold</span><input type="number" min="-100" max="100" value={draftSettings.oiBearishThreshold} onChange={(event) => setDraftSettings({ ...draftSettings, oiBearishThreshold: Number(event.target.value) })} /></label>
        <label><span>Bullish threshold</span><input type="number" min="-100" max="100" value={draftSettings.oiBullishThreshold} onChange={(event) => setDraftSettings({ ...draftSettings, oiBullishThreshold: Number(event.target.value) })} /></label>
        <label><span>Strong bullish threshold</span><input type="number" min="-100" max="100" value={draftSettings.oiStronglyBullishThreshold} onChange={(event) => setDraftSettings({ ...draftSettings, oiStronglyBullishThreshold: Number(event.target.value) })} /></label>
        <label><span>Elevated stock quality</span><input type="number" min="0" max="100" value={draftSettings.oiElevatedQualityThreshold} onChange={(event) => setDraftSettings({ ...draftSettings, oiElevatedQualityThreshold: Number(event.target.value) })} /></label>
        <label><span>Missing-data policy</span><select value={draftSettings.oiFailPolicy} onChange={(event) => setDraftSettings({ ...draftSettings, oiFailPolicy: event.target.value as Settings["oiFailPolicy"] })}><option value="SKIP">Skip and record</option><option value="ALLOW">Allow (explicit override)</option></select></label>
      </div></details>}
      <div className="allocation-presets">{[10000, 25000, 50000, 100000].map((value) => <button key={value} className={draftSettings.paperAllocation === value ? "active" : ""} onClick={() => setDraftSettings({ ...draftSettings, paperAllocation: value })}>{money(value, 0)}</button>)}</div><div className="modal-actions"><button onClick={() => setSettingsOpen(false)}>Cancel</button><button className="paper-buy-button" disabled={working} onClick={() => void saveSettings()}>Save paper settings</button></div>
    </section></div>}

    {paperSignal && <div className="signal-modal-backdrop" role="presentation"><section className="signal-modal paper-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="paper-title"><button className="modal-close" onClick={() => setPaperSignal(null)} aria-label="Close"><X /></button><span className="section-kicker">No broker order</span><h2 id="paper-title">Confirm PAPER BUY · {paperSignal.symbol}</h2><div className="paper-confirm-summary"><Metric label="Signal time" value={formatIst(paperSignal.signalTimestamp)} /><Metric label="Signal close" value={money(paperSignal.signalClose)} /><Metric label="Current price" value={money(paperSignal.currentPrice)} /><Metric label="Suggested range" value={`${money(paperSignal.buyRange.low)} – ${money(paperSignal.buyRange.high)}`} /></div><div className="signal-settings-grid"><label><span>Actual paper entry</span><input type="number" min="0.01" step="0.05" value={paperEntry} onChange={(event) => setPaperEntry(Number(event.target.value))} /></label><label><span>Actual quantity</span><input type="number" min="1" step="1" value={paperQuantity} onChange={(event) => setPaperQuantity(Math.floor(Number(event.target.value)))} /></label></div><div className="paper-confirm-total"><Metric label="Paper amount" value={money(paperEntry * paperQuantity)} /><Metric label="Actual paper target +0.5%" value={money(paperEntry * 1.005)} /><Metric label="Historical quality" value={`${number(paperSignal.historicalContext.qualityScore)} · GOOD ${number(paperSignal.historicalContext.goodRate)}%`} /></div><p>Paper performance will use the actual entry above. The system signal continues to track its separate signal-close target.</p><div className="modal-actions"><button onClick={() => setPaperSignal(null)}>Cancel</button><button className="paper-buy-button" disabled={working || paperEntry <= 0 || paperQuantity < 1} onClick={() => void confirmPaper()}><IndianRupee size={14} />Confirm paper buy</button></div></section></div>}

    {ignoreSignal && <div className="signal-modal-backdrop" role="presentation"><section className="signal-modal" role="dialog" aria-modal="true" aria-labelledby="ignore-title"><button className="modal-close" onClick={() => setIgnoreSignal(null)} aria-label="Close"><X /></button><span className="section-kicker">Manual decision study</span><h2 id="ignore-title">Ignore {ignoreSignal.symbol}</h2><p>The signal will remain stored and its hypothetical target, MAE and MFE will continue to be measured.</p><label><span>Reason</span><select value={ignoreReason} onChange={(event) => setIgnoreReason(event.target.value)}>{["Near resistance", "Weak support", "Price ran away", "Market weak", "Already holding similar stock", "Manual chart rejection", "Other"].map((reason) => <option key={reason}>{reason}</option>)}</select></label><div className="modal-actions"><button onClick={() => setIgnoreSignal(null)}>Cancel</button><button disabled={working} onClick={() => void decide(ignoreSignal, "IGNORE", ignoreReason)}>Save ignore decision</button></div></section></div>}

    {closingTrade && <div className="signal-modal-backdrop" role="presentation"><section className="signal-modal" role="dialog" aria-modal="true" aria-labelledby="close-paper-title"><button className="modal-close" onClick={() => setClosingTrade(null)} aria-label="Close"><X /></button><span className="section-kicker">Manual paper exit</span><h2 id="close-paper-title">Close {closingTrade.symbol} paper trade</h2><p>This records a manual paper exit only. It does not send a broker order.</p><label><span>Actual paper exit price</span><input type="number" min="0.01" step="0.05" value={closePrice} onChange={(event) => setClosePrice(Number(event.target.value))} /></label><div className="paper-confirm-total"><Metric label="Entry" value={money(closingTrade.entryPrice)} /><Metric label="Exit" value={money(closePrice)} /><Metric label="Estimated P&L" value={`${money((closePrice - closingTrade.entryPrice) * closingTrade.quantity)} · ${percent((closePrice / closingTrade.entryPrice - 1) * 100)}`} /></div><div className="modal-actions"><button onClick={() => setClosingTrade(null)}>Cancel</button><button disabled={working || closePrice <= 0} onClick={() => void closePaper(closingTrade)}>Confirm manual close</button></div></section></div>}
  </div>;
}
