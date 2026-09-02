"use client";
/* eslint-disable @next/next/no-html-link-for-pages -- Native navigation matches the production shell. */

import {
  Activity,
  BarChart3,
  Database,
  LayoutDashboard,
  LoaderCircle,
  LogOut,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Trash2,
  TrendingUp,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

type WorkspaceMode = "backtest" | "signals";
type Provider = "OKX" | "VALR";
type InstrumentType = "SPOT" | "PERPETUAL";

type Instrument = {
  instrumentId: string;
  provider: Provider;
  providerSymbol: string;
  displaySymbol: string;
  market: "CRYPTO" | "METAL";
  instrumentType: InstrumentType;
  baseCurrency: string;
  quoteCurrency: string;
  tickSize: string;
  quantityStep: string;
  minimumQuantity: string;
  minimumNotional: string;
  active: boolean;
  backtestEnabled: boolean;
  signalsEnabled: boolean;
};

type CryptoSignal = {
  signalId: string;
  provider: Provider;
  providerSymbol: string;
  displaySymbol: string;
  market: string;
  instrumentType: InstrumentType;
  strategyName: string;
  strategyVersion: string;
  timeframe: string;
  side: "BUY" | "SELL";
  signalTimestamp: string;
  signalPrice: number;
  stopPrice: number;
  targetPrice: number;
  rsi: number;
  rvol: number;
  atr: number;
};

type EngineStatus = {
  engineStatus: string;
  message: string;
  providers: string[];
  configuredInstruments: number;
  lastScan: string | null;
  lastError: string | null;
  pollingSeconds: number;
  strategyName: string;
  strategyVersion: string;
  paperOnly: boolean;
  liveOrdersEnabled: boolean;
};

type BacktestTrade = {
  signalId: string;
  side: "BUY" | "SELL";
  signalTimestamp: string;
  entryTimestamp: string;
  exitTimestamp: string;
  entryPrice: number;
  exitPrice: number;
  stopPrice: number;
  targetPrice: number;
  outcome: string;
  netPnlPerUnit: number;
  netR: number | null;
  barsHeld: number;
};

type BacktestResponse = {
  metadata: {
    runId: string;
    provider: Provider;
    providerSymbol: string;
    displaySymbol: string;
    market: string;
    strategyName: string;
    strategyVersion: string;
    timeframe: string;
    durationDays: number;
    dataSource: string;
    dataStart: string;
    dataEnd: string;
  };
  summary: {
    completedCandles: number;
    rawSignals: number;
    executedTrades: number;
    wins: number;
    losses: number;
    winRatePct: number;
    netPnlPerUnit: number;
    averageNetR: number;
    profitFactor: number | null;
  };
  trades: BacktestTrade[];
  warnings: string[];
};

const EMPTY_STATUS: EngineStatus = {
  engineStatus: "STOPPED", message: "Loading crypto signal engine", providers: ["OKX", "VALR"],
  configuredInstruments: 0, lastScan: null, lastError: null, pollingSeconds: 60,
  strategyName: "Crypto Trend Pullback Recovery", strategyVersion: "1.0.0", paperOnly: true, liveOrdersEnabled: false,
};

const TIMEFRAME_SECONDS: Record<string, number> = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "6h": 21600, "1d": 86400 };

function number(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function timestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" });
}

async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  const text = await response.text();
  let payload: unknown = {};
  try { payload = text ? JSON.parse(text) : {}; } catch { payload = {}; }
  if (!response.ok) {
    const detail = typeof payload === "object" && payload && "detail" in payload ? String((payload as { detail: unknown }).detail) : "Request failed";
    throw new Error(detail);
  }
  return payload as T;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="crypto-metric"><span>{label}</span><strong>{value}</strong></div>;
}

export function CryptoWorkspace({ mode, userName, signOutHref }: { mode: WorkspaceMode; userName: string; signOutHref: string }) {
  const [provider, setProvider] = useState<Provider>("OKX");
  const [query, setQuery] = useState("BTC");
  const [instrumentType, setInstrumentType] = useState<"ALL" | InstrumentType>("ALL");
  const [catalog, setCatalog] = useState<Instrument[]>([]);
  const [instruments, setInstruments] = useState<Instrument[]>([]);
  const [signals, setSignals] = useState<CryptoSignal[]>([]);
  const [status, setStatus] = useState<EngineStatus>(EMPTY_STATUS);
  const [selectedInstrument, setSelectedInstrument] = useState("");
  const [timeframe, setTimeframe] = useState("5m");
  const [durationDays, setDurationDays] = useState(30);
  const [side, setSide] = useState<"BOTH" | "BUY" | "SELL">("BOTH");
  const [result, setResult] = useState<BacktestResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const activeInstruments = useMemo(() => instruments.filter((item) => item.active), [instruments]);
  const maximumDurationDays = Math.min(730, Math.max(1, Math.floor(20_000 * TIMEFRAME_SECONDS[timeframe] / 86_400)));
  const initials = userName.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();

  const load = useCallback(async () => {
    setError("");
    try {
      const [instrumentPayload, statusPayload, signalPayload] = await Promise.all([
        jsonRequest<{ rows: Instrument[] }>("/api/crypto?action=instruments"),
        jsonRequest<EngineStatus>("/api/crypto?action=status"),
        jsonRequest<{ rows: CryptoSignal[] }>("/api/crypto?action=signals&limit=200"),
      ]);
      setInstruments(instrumentPayload.rows);
      setStatus(statusPayload);
      setSignals(signalPayload.rows);
      setSelectedInstrument((current) => current || instrumentPayload.rows.find((item) => item.active)?.instrumentId || "");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Crypto workspace could not load.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const searchCatalog = async (event?: FormEvent) => {
    event?.preventDefault();
    setWorking("catalog"); setError(""); setNotice("");
    const params = new URLSearchParams({ action: "catalog", provider, query, limit: "100" });
    if (instrumentType !== "ALL") params.set("instrumentType", instrumentType);
    try {
      const payload = await jsonRequest<{ rows: Instrument[] }>(`/api/crypto?${params.toString()}`);
      setCatalog(payload.rows);
      if (!payload.rows.length) setNotice(`No active ${provider} instruments matched “${query}”.`);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Catalog search failed."); }
    finally { setWorking(""); }
  };

  const addInstrument = async (item: Instrument) => {
    setWorking(item.instrumentId); setError(""); setNotice("");
    try {
      await jsonRequest("/api/crypto?action=add", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ provider: item.provider, providerSymbol: item.providerSymbol }) });
      setNotice(`${item.providerSymbol} was added to the paper research universe.`);
      await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Instrument could not be added."); }
    finally { setWorking(""); }
  };

  const removeInstrument = async (item: Instrument) => {
    setWorking(item.instrumentId); setError(""); setNotice("");
    try {
      await jsonRequest(`/api/crypto?instrumentId=${encodeURIComponent(item.instrumentId)}`, { method: "DELETE" });
      setNotice(`${item.providerSymbol} was removed from active monitoring. Historical research remains preserved.`);
      setSelectedInstrument((current) => current === item.instrumentId ? "" : current);
      await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Instrument could not be removed."); }
    finally { setWorking(""); }
  };

  const runBacktest = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedInstrument) { setError("Add and select an instrument first."); return; }
    setWorking("backtest"); setError(""); setNotice(""); setResult(null);
    try {
      const payload = await jsonRequest<BacktestResponse>("/api/crypto?action=backtest", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ instrumentId: selectedInstrument, timeframe, durationDays, configuration: { side } }),
      });
      setResult(payload);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Backtest failed."); }
    finally { setWorking(""); }
  };

  const scanNow = async () => {
    setWorking("scan"); setError(""); setNotice("");
    try {
      const payload = await jsonRequest<{ signalsCreated: number; instrumentsScanned: number }>(`/api/crypto?action=scan&timeframe=${encodeURIComponent(timeframe)}`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
      setNotice(`Scanned ${payload.instrumentsScanned} instruments and saved ${payload.signalsCreated} new completed-candle signals.`);
      await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Signal scan failed."); }
    finally { setWorking(""); }
  };

  return <div className="site-shell backtest-shell crypto-shell">
    <header className="global-header"><div className="header-inner backtest-header-inner">
      <a className="brand" href="/"><div className="brand-mark" aria-hidden="true">₹</div><div><strong>OpenDelta</strong><span>Market intelligence</span></div></a>
      <nav className="top-nav" aria-label="Main navigation"><a className="nav-item" href="/"><LayoutDashboard size={16} />Dashboard</a><a className={`nav-item ${mode === "backtest" ? "active" : ""}`} href="/backtest"><TrendingUp size={16} />Backtest</a><a className={`nav-item ${mode === "signals" ? "active" : ""}`} href="/signals"><Radio size={16} />Signals</a><a className="nav-item" href="/admin"><Settings2 size={16} />Admin</a></nav>
      <div className="header-actions"><div className="user-chip"><div className="avatar">{initials}</div><span>{userName}</span></div><a href={signOutHref} className="icon-button" aria-label="Sign out"><LogOut size={17} /></a></div>
    </div></header>

    <main className="backtest-main crypto-main">
      <nav className="market-workspace-tabs" aria-label="Market workspace"><a href={mode === "backtest" ? "/backtest" : "/signals"}>NSE</a><a className="active" href={mode === "backtest" ? "/backtest/crypto" : "/signals/crypto"}>Crypto & metals</a></nav>
      <section className="crypto-heading"><div><span className="section-kicker">24/7 public market data · paper only</span><h1>{mode === "backtest" ? "Crypto & metals backtest" : "Crypto & metals signals"}</h1><p>One provider-neutral strategy engine for OKX and VALR. Instruments are accepted only after live catalog validation.</p></div><span className="crypto-paper-badge"><ShieldCheck size={16} />LIVE ORDERS DISABLED</span></section>

      {error && <div className="backtest-error" role="alert">{error}</div>}
      {notice && <div className="signal-notice">{notice}</div>}

      <section className="crypto-status-grid">
        <Metric label="Engine" value={status.engineStatus} /><Metric label="Strategy" value={`${status.strategyName} v${status.strategyVersion}`} /><Metric label="Configured" value={`${status.configuredInstruments} instruments`} /><Metric label="Last scan" value={timestamp(status.lastScan)} />
      </section>

      <section className="backtest-panel crypto-instrument-panel">
        <div className="panel-title"><div><span className="section-kicker">Instrument registry</span><h2>Add from provider catalog</h2></div><span className="date-window">Public REST market data · no API key required</span></div>
        <div className="crypto-registry-grid">
          <form className="crypto-catalog-search" onSubmit={searchCatalog}>
            <label><span>Provider</span><select value={provider} onChange={(event) => setProvider(event.target.value as Provider)}><option>OKX</option><option>VALR</option></select></label>
            <label><span>Symbol search</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="BTC, ETH, XAU…" /></label>
            <label><span>Type</span><select value={instrumentType} onChange={(event) => setInstrumentType(event.target.value as "ALL" | InstrumentType)}><option value="ALL">All types</option><option value="SPOT">Spot</option><option value="PERPETUAL">Perpetual</option></select></label>
            <button className="secondary-action" disabled={working === "catalog"}>{working === "catalog" ? <LoaderCircle className="spin" size={16} /> : <Search size={16} />}Search catalog</button>
          </form>
          <div className="crypto-table-wrap"><table className="crypto-table"><thead><tr><th>Catalog symbol</th><th>Market</th><th>Type</th><th>Quote</th><th>Tick size</th><th></th></tr></thead><tbody>{catalog.length ? catalog.map((item) => <tr key={item.instrumentId}><td><strong>{item.providerSymbol}</strong><small>{item.provider}</small></td><td>{item.market}</td><td>{item.instrumentType}</td><td>{item.quoteCurrency}</td><td>{item.tickSize}</td><td><button disabled={working === item.instrumentId || activeInstruments.some((existing) => existing.instrumentId === item.instrumentId)} onClick={() => void addInstrument(item)}>{working === item.instrumentId ? <LoaderCircle className="spin" size={14} /> : <Plus size={14} />}{activeInstruments.some((existing) => existing.instrumentId === item.instrumentId) ? "Added" : "Add"}</button></td></tr>) : <tr><td colSpan={6} className="crypto-empty">Search OKX or VALR. BTC is prefilled for a quick start.</td></tr>}</tbody></table></div>
          <div className="crypto-table-wrap"><table className="crypto-table"><thead><tr><th>Configured</th><th>Provider</th><th>Market</th><th>Type</th><th>Minimum</th><th></th></tr></thead><tbody>{activeInstruments.length ? activeInstruments.map((item) => <tr key={item.instrumentId}><td><strong>{item.displaySymbol}</strong><small>{item.providerSymbol}</small></td><td>{item.provider}</td><td>{item.market}</td><td>{item.instrumentType}</td><td>{item.minimumQuantity || item.minimumNotional || "—"}</td><td><button className="danger" disabled={working === item.instrumentId} onClick={() => void removeInstrument(item)} aria-label={`Remove ${item.providerSymbol}`}><Trash2 size={14} />Remove</button></td></tr>) : <tr><td colSpan={6} className="crypto-empty">No instruments configured. Search the live provider catalog and add one.</td></tr>}</tbody></table></div>
        </div>
      </section>

      {mode === "backtest" ? <>
        <form className="backtest-panel crypto-run-panel" onSubmit={runBacktest}>
          <div className="panel-title"><div><span className="section-kicker">Shared strategy engine</span><h2>Trend Pullback Recovery</h2></div><span className="date-window">Completed candle → next-bar open</span></div>
          <div className="crypto-form-grid">
            <label><span>Instrument</span><select value={selectedInstrument} onChange={(event) => setSelectedInstrument(event.target.value)}><option value="">Select instrument</option>{activeInstruments.map((item) => <option key={item.instrumentId} value={item.instrumentId}>{item.displaySymbol} · {item.provider}</option>)}</select></label>
            <label><span>Timeframe</span><select value={timeframe} onChange={(event) => { const next = event.target.value; setTimeframe(next); setDurationDays((current) => Math.min(current, Math.min(730, Math.max(1, Math.floor(20_000 * TIMEFRAME_SECONDS[next] / 86_400))))); }}>{["1m", "5m", "15m", "30m", "1h", "6h", "1d"].map((item) => <option key={item}>{item}</option>)}</select></label>
            <label><span>Duration days (max {maximumDurationDays})</span><input type="number" min="1" max={maximumDurationDays} value={durationDays} onChange={(event) => setDurationDays(Number(event.target.value))} /></label>
            <label><span>Direction</span><select value={side} onChange={(event) => setSide(event.target.value as "BOTH" | "BUY" | "SELL")}><option value="BOTH">Buy & sell</option><option value="BUY">Buy only</option><option value="SELL">Sell only</option></select></label>
            <button className="secondary-action" disabled={working === "backtest" || !activeInstruments.length}>{working === "backtest" ? <LoaderCircle className="spin" size={16} /> : <BarChart3 size={16} />}Run backtest</button>
          </div>
          <p className="crypto-method-note">EMA20/50 trend + UTC-day VWAP + RSI recovery + RVOL confirmation; 1 ATR stop, 1.5R target, six-bar time exit. Default estimated costs: 8 bps per side plus 2 bps slippage.</p>
        </form>
        {result && <section className="backtest-panel crypto-results">
          <div className="panel-title"><div><span className="section-kicker">{result.metadata.provider} · {result.metadata.providerSymbol}</span><h2>{result.metadata.displaySymbol} result</h2></div><span className="date-window">{result.metadata.timeframe} · {result.metadata.durationDays} days</span></div>
          <div className="crypto-result-grid"><Metric label="Completed candles" value={number(result.summary.completedCandles, 0)} /><Metric label="Raw signals" value={number(result.summary.rawSignals, 0)} /><Metric label="Executed trades" value={number(result.summary.executedTrades, 0)} /><Metric label="Win rate" value={`${number(result.summary.winRatePct)}%`} /><Metric label="Average net R" value={number(result.summary.averageNetR, 3)} /><Metric label="Profit factor" value={number(result.summary.profitFactor, 3)} /></div>
          <div className="crypto-table-wrap"><table className="crypto-table"><thead><tr><th>Side</th><th>Signal</th><th>Entry</th><th>Exit</th><th>Outcome</th><th>Net / unit</th><th>Net R</th></tr></thead><tbody>{result.trades.length ? result.trades.map((trade) => <tr key={`${trade.signalId}-${trade.entryTimestamp}`}><td><span className={`crypto-side ${trade.side.toLowerCase()}`}>{trade.side}</span></td><td>{timestamp(trade.signalTimestamp)}</td><td>{number(trade.entryPrice, 8)}</td><td>{number(trade.exitPrice, 8)}</td><td>{trade.outcome}</td><td>{number(trade.netPnlPerUnit, 8)}</td><td>{number(trade.netR, 3)}</td></tr>) : <tr><td colSpan={7} className="crypto-empty">No trades passed the rules in this period.</td></tr>}</tbody></table></div>
          <div className="crypto-warnings">{result.warnings.map((warning) => <p key={warning}>{warning}</p>)}</div>
        </section>}
      </> : <section className="backtest-panel crypto-signal-panel">
        <div className="panel-title"><div><span className="section-kicker">Persisted observations</span><h2>Completed-candle paper signals</h2></div><div className="crypto-inline-actions"><select aria-label="Signal timeframe" value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>{["1m", "5m", "15m", "30m", "1h", "6h", "1d"].map((item) => <option key={item}>{item}</option>)}</select><button className="secondary-action" disabled={working === "scan" || !activeInstruments.length} onClick={() => void scanNow()}>{working === "scan" ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}Scan now</button></div></div>
        {loading ? <div className="crypto-empty"><LoaderCircle className="spin" size={18} />Loading signals…</div> : <div className="crypto-table-wrap"><table className="crypto-table"><thead><tr><th>Instrument</th><th>Side</th><th>Signal time</th><th>Price</th><th>Stop</th><th>Target</th><th>RSI</th><th>RVOL</th></tr></thead><tbody>{signals.length ? signals.map((signal) => <tr key={signal.signalId}><td><strong>{signal.displaySymbol}</strong><small>{signal.provider} · {signal.timeframe}</small></td><td><span className={`crypto-side ${signal.side.toLowerCase()}`}>{signal.side}</span></td><td>{timestamp(signal.signalTimestamp)}</td><td>{number(signal.signalPrice, 8)}</td><td>{number(signal.stopPrice, 8)}</td><td>{number(signal.targetPrice, 8)}</td><td>{number(signal.rsi)}</td><td>{number(signal.rvol)}</td></tr>) : <tr><td colSpan={8} className="crypto-empty">No persisted signals. The scanner records only fresh completed-candle setups.</td></tr>}</tbody></table></div>}
      </section>}

      <section className="crypto-boundary"><Database size={17} /><div><strong>Provider boundary</strong><span>OKX and VALR public market data are connected. No exchange credentials, balances, private account endpoints, order placement, or auto-trading paths are present.</span></div><Activity size={17} /></section>
    </main>
  </div>;
}
