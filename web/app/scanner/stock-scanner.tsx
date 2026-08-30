"use client";
/* eslint-disable @next/next/no-html-link-for-pages -- Native navigation avoids stalled production transitions. */

import {
  Activity,
  Clock3,
  LayoutDashboard,
  LoaderCircle,
  LogOut,
  Radio,
  RefreshCw,
  ScanSearch,
  Settings2,
  TrendingUp,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

type ScannerEntry = {
  symbol: string;
  companyName: string;
  rank?: number;
  rankAfter?: number;
  rankBefore?: number | null;
  tier?: "PRIMARY" | "RESERVE";
  score: number;
  selectedAt?: string;
  selectionTimestamp?: string;
  promotionReason?: string;
  action?: string;
  rollingRvol?: number | null;
  rollingTradedValue?: number | null;
  rollingReturnPct?: number | null;
  relativeToNiftyPct?: number | null;
  relativeToSectorPct?: number | null;
  sessionVwap?: number | null;
  distanceFromVwapAtr?: number | null;
  emaFast?: number | null;
  emaSlow?: number | null;
  rsi?: number | null;
  atrPct?: number | null;
  priceAccelerationPct?: number | null;
  spreadStatus?: string;
  penalties?: Record<string, number>;
};

type ScannerHistory = {
  rescanTimestamp: string;
  entries: ScannerEntry[];
  promoted: ScannerEntry[];
  removed: ScannerEntry[];
  replacements: number;
  eligibleSymbols: number;
  evaluatedSymbols: number;
};

type FunnelSignal = {
  eventId: string;
  signalId?: string;
  symbol: string;
  strategyKey: string;
  strategyName: string;
  strategyStatus?: string;
  signalTimestamp: string;
  status: "TRADE_READY" | "WATCH" | "REJECTED";
  rank?: number;
  signalScore?: number;
  activityScore?: number;
  signalClose?: number;
  estimatedEntry?: number;
  estimatedStop?: number;
  estimatedTarget?: number;
  riskPerShare?: number;
  rewardRisk?: number;
  quantity?: number;
  executionModel?: string;
  reasons?: string[];
};

type SignalFunnel = {
  metadata: {
    funnelVersion: string;
    generatedAt: string;
    paperOnly: boolean;
    liveOrdersEnabled: boolean;
    configuration: { maximumTradesPerDay: number; maximumConcurrent: number; quantityPerTrade: number };
    strategies: Array<{ key: string; name: string; version: string; tradeReadyAllowed?: boolean }>;
  };
  counts: { tradeable: number; strategyEvaluations: number; validSetups: number; tradeReady: number; watch: number; rejected: number };
  tradeReady: FunnelSignal[];
  watch: FunnelSignal[];
  rejected: FunnelSignal[];
  rejectionCounts: Record<string, number>;
};

type ScannerResponse = {
  metadata: {
    status: string;
    generatedAt: string;
    sessionDate?: string;
    lastRescanTimestamp?: string;
    nextRescanTimestamp?: string | null;
    latestSourceTimestamp?: string;
    dataFreshnessMinutes?: number;
    symbolsRequested: number;
    symbolsLoaded: number;
    symbolsScored?: number;
    symbolsFailed: number;
    rescanIntervalMinutes: number;
    globalPriceRange: { minimumPrice: number; maximumPrice: number };
    resultSource?: string;
    paperOnly: boolean;
    liveOrdersEnabled: boolean;
    signalUniversePolicy: string;
  };
  watchlist: {
    topFive: ScannerEntry[];
    primary: ScannerEntry[];
    reserve: ScannerEntry[];
    promoted: ScannerEntry[];
    removed: ScannerEntry[];
    history: ScannerHistory[];
  };
  opportunities: ScannerEntry[];
  eligibility: { eligible: number; rejected: number; rejectionCounts: Record<string, number> };
  signalFunnel: SignalFunnel;
  errors: Array<{ symbol: string; reason: string }>;
  warnings: string[];
  detail?: string;
};

function number(value: number | null | undefined, digits = 2): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("en-IN", { maximumFractionDigits: digits });
}

function money(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function price(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `₹${value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatIst(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  }).format(parsed);
}

function initials(name: string): string {
  return name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
}

export function StockScanner({ userName, signOutHref, focusSignals = false }: { userName: string; signOutHref: string; focusSignals?: boolean }) {
  const [response, setResponse] = useState<ScannerResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (force = false) => {
    if (force) setRefreshing(true);
    try {
      const result = await fetch(`/api/stock-scanner?refresh=${force}`, { cache: "no-store" });
      const body = await result.json() as ScannerResponse;
      if (!result.ok) throw new Error(body.detail ?? "Unable to load Stock Scanner");
      setResponse(body);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load Stock Scanner");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial authenticated API synchronization
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const rejectionRows = useMemo(
    () => Object.entries(response?.eligibility.rejectionCounts ?? {}).sort((left, right) => right[1] - left[1]),
    [response],
  );

  return <div className="site-shell scanner-shell">
    <header className="global-header"><div className="header-inner">
      <a className="brand" href="/"><div className="brand-mark" aria-hidden="true">₹</div><div><strong>OpenDelta</strong><span>Market intelligence</span></div></a>
      <nav className="top-nav" aria-label="Main navigation">
        <a className="nav-item" href="/"><LayoutDashboard size={16} />Dashboard</a>
        <a className={`nav-item ${focusSignals ? "" : "active"}`} href="/scanner" aria-current={focusSignals ? undefined : "page"}><ScanSearch size={16} />Stock Scanner</a>
        <a className="nav-item" href="/backtest"><TrendingUp size={16} />Backtest</a>
        <a className={`nav-item ${focusSignals ? "active" : ""}`} href="/signals" aria-current={focusSignals ? "page" : undefined}><Radio size={16} />Signals</a>
        <a className="nav-item" href="/admin"><Settings2 size={16} />Admin</a>
      </nav>
      <div className="header-actions"><div className="user-chip"><div className="avatar">{initials(userName)}</div><span>{userName}</span></div><a href={signOutHref} className="icon-button" aria-label="Sign out"><LogOut size={17} /></a></div>
    </div></header>

    <main className="main-content scanner-main">
      <section className="scanner-hero backtest-panel">
        <div><span className="section-kicker">Completed five-minute candles · paper research</span><h1>{focusSignals ? "NSE Signal Funnel" : "Stock Scanner & Signal Funnel"}</h1><p>Evaluates active RSI Recovery across every eligible stock and shows retired VWAP Pullback candidates as WATCH-only research. The activity Top-5 remains context and never forces a trade.</p></div>
        <button className="secondary-action" disabled={refreshing} onClick={() => void load(true)}>{refreshing ? <><LoaderCircle className="spin" size={15} />Refreshing…</> : <><RefreshCw size={15} />Refresh scanner</>}</button>
      </section>

      {error && <div className="backtest-message error" role="alert">{error}</div>}
      {loading && !response ? <section className="backtest-panel scanner-loading"><LoaderCircle className="spin" size={22} />Reading cached candles and ranking the filtered universe…</section> : response && <>
        <section className="scanner-status-grid">
          <div><Activity size={17} /><span>Status</span><strong>{response.metadata.status.replaceAll("_", " ")}</strong></div>
          <div><Clock3 size={17} /><span>Last rescan</span><strong>{formatIst(response.metadata.lastRescanTimestamp)}</strong></div>
          <div><Clock3 size={17} /><span>Next rescan</span><strong>{formatIst(response.metadata.nextRescanTimestamp)}</strong></div>
          <div><ScanSearch size={17} /><span>Universe loaded</span><strong>{response.metadata.symbolsLoaded} / {response.metadata.symbolsRequested}</strong></div>
          <div><TrendingUp size={17} /><span>Eligible / scored</span><strong>{response.eligibility.eligible} / {response.metadata.symbolsScored ?? 0}</strong></div>
          <div><RefreshCw size={17} /><span>Data freshness</span><strong>{number(response.metadata.dataFreshnessMinutes, 0)} min</strong></div>
        </section>

        <section className="scanner-safety backtest-panel"><div><strong>Research only</strong><span>Live orders disabled · {response.metadata.signalUniversePolicy.replaceAll("_", " ")}</span></div><div><strong>Global price range</strong><span>{money(response.metadata.globalPriceRange.minimumPrice)} to {money(response.metadata.globalPriceRange.maximumPrice)}</span></div><div><strong>Source</strong><span>{formatIst(response.metadata.latestSourceTimestamp)} · {response.metadata.resultSource?.replaceAll("_", " ")}</span></div></section>

        <section className="backtest-panel scanner-funnel" id="trade-ready">
          <div className="panel-title"><div><span className="section-kicker">Signal-first selection</span><h2>Actual strategy setups</h2></div><span className="date-window">Maximum {response.signalFunnel.metadata.configuration.maximumConcurrent} concurrent · {response.signalFunnel.metadata.configuration.maximumTradesPerDay} trades/day</span></div>
          <div className="scanner-funnel-counts"><div><span>Universe requested</span><strong>{response.metadata.symbolsRequested}</strong></div><div><span>Tradeable</span><strong>{response.signalFunnel.counts.tradeable}</strong></div><div><span>Strategy checks</span><strong>{response.signalFunnel.counts.strategyEvaluations}</strong></div><div><span>Valid setups</span><strong>{response.signalFunnel.counts.validSetups}</strong></div><div className="ready"><span>Trade ready</span><strong>{response.signalFunnel.counts.tradeReady}</strong></div><div><span>Watch</span><strong>{response.signalFunnel.counts.watch}</strong></div></div>
          <div className="scanner-signal-heading"><div><h3>TRADE_READY</h3><p>Only completed-candle setups that passed a registered strategy and the signal score threshold.</p></div><a href="/signals">Open RSI Recovery history</a></div>
          {response.signalFunnel.tradeReady.length ? <div className="scanner-signal-grid">{response.signalFunnel.tradeReady.map((signal) => <article className="trade-ready" key={signal.eventId}><div className="scanner-signal-title"><span>#{signal.rank}</span><div><strong>{signal.symbol}</strong><small>{signal.strategyName}</small></div><b>BUY</b></div><div className="scanner-signal-score"><span>Signal score</span><strong>{number(signal.signalScore)}</strong></div><div className="scanner-signal-prices"><div><span>Estimated entry</span><strong>{price(signal.estimatedEntry)}</strong></div><div><span>Stop</span><strong>{price(signal.estimatedStop)}</strong></div><div><span>Target</span><strong>{price(signal.estimatedTarget)}</strong></div><div><span>Risk / share</span><strong>{price(signal.riskPerShare)}</strong></div></div><footer><span>{signal.executionModel?.replaceAll("_", " ")}</span><strong>{number(signal.rewardRisk)}R · {signal.quantity} shares</strong></footer></article>)}</div> : <div className="scanner-no-trade"><strong>NO TRADE</strong><span>No strategy setup passed at {formatIst(response.signalFunnel.metadata.generatedAt)}. The system will not manufacture two entries.</span></div>}
          {response.signalFunnel.watch.length > 0 && <><div className="scanner-signal-heading"><div><h3>WATCH</h3><p>Research-only or lower-ranked setups. Retired strategies can never become trade-ready.</p></div></div><div className="scanner-watch-signal-grid">{response.signalFunnel.watch.map((signal) => <article key={signal.eventId}><div><strong>{signal.symbol}</strong><span>{signal.strategyName}</span></div><b>{number(signal.signalScore)}</b><small>{signal.strategyStatus?.replaceAll("_", " ")} · {price(signal.estimatedEntry)} → {price(signal.estimatedTarget)}</small></article>)}</div></>}
          <details className="scanner-funnel-rejections"><summary>Why setups were rejected ({response.signalFunnel.counts.rejected})</summary><div>{Object.entries(response.signalFunnel.rejectionCounts).slice(0, 12).map(([reason, count]) => <p key={reason}><strong>{count}</strong><span>{reason.replaceAll("_", " ")}</span></p>)}</div></details>
        </section>

        <section className="backtest-panel scanner-watchlist">
          <div className="panel-title"><div><span className="section-kicker">Activity context—not entry signals</span><h2>Top 2 activity leaders · ranks 3–5 watchlist</h2></div><span className="date-window">15-minute rescans · 09:30–14:30 IST</span></div>
          {response.watchlist.topFive.length ? <div className="scanner-watchlist-grid">{response.watchlist.topFive.map((entry) => <article key={entry.symbol} className={entry.tier === "PRIMARY" ? "primary" : "reserve"}>
            <span className="scanner-rank">#{entry.rankAfter}</span><div><strong title={entry.companyName}>{entry.symbol}</strong><small>{entry.companyName}</small></div><b>{entry.tier}</b><div><span>Score</span><strong>{number(entry.score)}</strong></div><div><span>Selected</span><strong>{formatIst(entry.selectedAt)}</strong></div><p>{entry.promotionReason?.replaceAll("_", " ")}</p>
          </article>)}</div> : <p className="scanner-empty">No five-symbol watchlist is available for this rescan. Eligibility thresholds remain unchanged.</p>}
        </section>

        <section className="backtest-panel scanner-opportunities">
          <div className="panel-title"><div><span className="section-kicker">Ranked at {formatIst(response.metadata.lastRescanTimestamp)}</span><h2>Top 20 opportunities</h2></div><span className="date-window">{response.metadata.rescanIntervalMinutes}-minute cadence</span></div>
          <div className="scanner-table-wrap"><table><thead><tr><th>Rank</th><th>Symbol</th><th>Score</th><th>30m RVOL</th><th>30m value</th><th>30m return</th><th>vs NIFTY</th><th>vs sector</th><th>RSI</th><th>VWAP distance</th><th>EMA9 / EMA20</th><th>Penalties</th></tr></thead><tbody>{response.opportunities.map((entry) => <tr key={entry.symbol}><td>#{entry.rank}</td><td><strong title={entry.companyName}>{entry.symbol}</strong><small>{entry.companyName}</small></td><td>{number(entry.score)}</td><td>{number(entry.rollingRvol)}×</td><td>{money(entry.rollingTradedValue)}</td><td>{number(entry.rollingReturnPct)}%</td><td>{number(entry.relativeToNiftyPct)}%</td><td>{number(entry.relativeToSectorPct)}%</td><td>{number(entry.rsi)}</td><td>{number(entry.distanceFromVwapAtr)} ATR</td><td>{number(entry.emaFast)} / {number(entry.emaSlow)}</td><td>{Object.keys(entry.penalties ?? {}).join(", ").replaceAll("_", " ") || "None"}</td></tr>)}</tbody></table></div>
        </section>

        <details className="backtest-panel scanner-history"><summary>Intraday watchlist history ({response.watchlist.history.length} rescans)</summary><div>{response.watchlist.history.map((snapshot) => <article key={snapshot.rescanTimestamp}><strong>{formatIst(snapshot.rescanTimestamp)}</strong><span>{snapshot.entries.map((entry) => `${entry.rankAfter}. ${entry.symbol} (${entry.tier})`).join(" · ") || "No eligible selections"}</span><small>{snapshot.promoted.length} promoted · {snapshot.removed.length} removed · {snapshot.eligibleSymbols}/{snapshot.evaluatedSymbols} eligible</small></article>)}</div></details>

        <details className="backtest-panel scanner-diagnostics"><summary>Eligibility diagnostics</summary><div className="scanner-diagnostic-grid"><div><strong>{response.eligibility.rejected}</strong><span>Rejected at latest rescan</span></div><div><strong>{response.metadata.symbolsFailed}</strong><span>Local candle files unavailable</span></div>{rejectionRows.slice(0, 10).map(([reason, count]) => <div key={reason}><strong>{count}</strong><span>{reason.replaceAll("_", " ")}</span></div>)}</div>{response.errors.length > 0 && <p>{response.errors.length} symbol-level cache errors are retained in the API response for audit.</p>}</details>

        <section className="scanner-warnings">{response.warnings.map((warning) => <p key={warning}>{warning}</p>)}</section>
      </>}
    </main>
  </div>;
}
