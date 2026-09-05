"use client";

import { Activity, Database, Gauge, RefreshCw, ScanSearch, Wallet } from "lucide-react";
import { useCallback, type ReactNode } from "react";
import { formatAge, formatDateTime, formatInteger, formatMoney, formatNumber, humanize, marketLabel, shortId, tone } from "./platform/format";
import type { PlatformMarket } from "./platform/platform-client";
import { useV2Resource } from "./platform/use-v2";
import { v2Get } from "./platform/v2-client";
import type { DashboardPayload, Section } from "./platform/v2-types";
import { EmptyState, LoadingState, PaperOnlyBadge, Panel, PnlValue, RequestErrorState, SectionError, StatusBadge, WorkspaceHeader } from "./platform/workspace-ui";

const DASHBOARD_REFRESH_MS = 30_000;

function marketQuery(market: PlatformMarket): string {
  return market === "CRYPTO" ? "?market=CRYPTO" : "";
}

function SectionBody<T>({ section, children }: { section: Section<T>; children: (data: T) => ReactNode }) {
  if (!section.available || !section.data) return <SectionError message={section.error} />;
  return <>{children(section.data)}</>;
}

function readable(value: unknown): string {
  return value ? humanize(String(value)) : "Unavailable";
}

export function DashboardWorkspace({ market }: { market: PlatformMarket }) {
  const load = useCallback(() => v2Get<DashboardPayload>("dashboard", { market }), [market]);
  const { data, error, loading, reload, refresh } = useV2Resource(load, DASHBOARD_REFRESH_MS);
  const query = marketQuery(market);
  const account = data?.paper.available ? data.paper.data?.account ?? null : null;
  const signalWorkers = data?.signalEngine.data?.workers ?? [];
  const storedWorkers = data?.signalEngine.data?.stored ?? [];
  const worker = signalWorkers[0] ?? storedWorkers[0] ?? null;
  const freshness = data?.marketData.data?.dataFreshness ?? null;
  const universe = data?.screener.data?.activeUniverse ?? null;

  return <main className="quant-workspace quant-dashboard-workspace">
    <WorkspaceHeader eyebrow={marketLabel(market) + " overview"} title="Dashboard" actions={<div className="quant-header-actions"><PaperOnlyBadge /><button type="button" onClick={refresh}><RefreshCw size={15} />Refresh</button></div>} />
    {loading ? <LoadingState label="Loading dashboard" /> : error ? <RequestErrorState error={error} retry={reload} /> : data && <>
      <section className="quant-overview-strip" aria-label="Market overview">
        <div><span>Market data</span><strong><i data-tone={tone(freshness?.status)} />{readable(freshness?.status)}</strong><small>{freshness?.ageSeconds != null ? formatAge(freshness.ageSeconds) : readable(freshness?.reason)}</small></div>
        <div><span>Strategy automation</span><strong><i data-tone={worker ? tone(worker.status) : "warn"} />{worker ? readable(worker.status) : "Not configured"}</strong><small>{worker?.connectionStatus ? readable(worker.connectionStatus) : "Choose Signals or Paper in Settings"}</small></div>
        <div><span>Active watchlist</span><strong>{universe?.name ?? "None"}</strong><small>{universe ? formatInteger(universe.symbols.length) + " symbols" : "Create in Watchlist"}</small></div>
        <div><span>Open positions</span><strong>{formatInteger(account?.openPositions)}</strong><small>{account ? formatInteger(account.closedLots) + " closed lots" : "Paper account unavailable"}</small></div>
      </section>

      {!worker && <section className="quant-dashboard-next-step" aria-label="Strategy setup required"><div><strong>No strategy automation is running</strong><span>Save a strategy configuration, then choose Signals or Paper. Until then the dashboard has no signals or simulated trades to display.</span></div><a className="quant-action-link" href={"/settings" + query}>Configure strategy</a></section>}

      <Panel className="quant-primary-panel" icon={<Wallet size={18} />} title="Paper portfolio" description="Current simulated account performance." aside={<a className="quant-action-link" href={"/paper-trading" + query}>View account</a>}>
        <SectionBody section={data.paper}>{(section) => <div className="quant-portfolio-hero">
          <div className="quant-portfolio-value"><span>Equity</span><strong>{formatMoney(section.account.equity, market, section.account.currency)}</strong><small>Starting balance {formatMoney(section.account.startingBalance, market, section.account.currency)}</small></div>
          <dl>
            <div><dt>Today</dt><dd><PnlValue value={section.account.dailyPnl} market={market} currency={section.account.currency} /></dd></div>
            <div><dt>Realized</dt><dd><PnlValue value={section.account.realizedPnl} market={market} currency={section.account.currency} /></dd></div>
            <div><dt>Unrealized</dt><dd><PnlValue value={section.account.unrealizedPnl} market={market} currency={section.account.currency} /></dd></div>
            <div><dt>Cash</dt><dd>{formatMoney(section.account.cashBalance, market, section.account.currency)}</dd></div>
          </dl>
          {section.openPositions.length ? <div className="quant-table-scroll"><table className="quant-table">
            <thead><tr><th>Open position</th><th className="numeric">Qty</th><th className="numeric">Entry</th><th className="numeric">Last</th><th className="numeric">Unrealized</th><th>Status</th></tr></thead>
            <tbody>{section.openPositions.slice(0, 5).map((lot) => <tr key={lot.lotId}><td><strong>{lot.symbol}</strong><small>{formatDateTime(lot.entryTimestamp, market)}</small></td><td className="numeric">{formatNumber(lot.quantity, 4)}</td><td className="numeric">{formatNumber(lot.entryPrice)}</td><td className="numeric">{formatNumber(lot.lastPrice)}</td><td className="numeric"><PnlValue value={lot.unrealizedPnl} market={market} currency={section.account.currency} /></td><td><StatusBadge tone={tone(lot.status)}>{readable(lot.status)}</StatusBadge></td></tr>)}</tbody>
          </table></div> : <div className="quant-compact-empty"><EmptyState title="No open paper positions" description={worker ? "Qualified signals will appear here after simulated execution." : "Start a strategy in Paper mode from Settings to create simulated positions."} /></div>}
        </div>}</SectionBody>
      </Panel>

      <Panel icon={<Gauge size={17} />} title="Recent backtests" aside={<a className="quant-action-link" href={"/backtest" + query}>View all</a>}>
        <SectionBody section={data.backtests}>{(section) => section.recent.length ? <div className="quant-table-scroll"><table className="quant-table">
          <thead><tr><th>Strategy</th><th>Status</th><th>Progress</th><th className="numeric">Realized PnL</th><th className="numeric">Win rate</th><th>Created</th></tr></thead>
          <tbody>{section.recent.slice(0, 3).map((run) => <tr key={run.runId}><td><strong>{humanize(run.strategyId)}</strong><small>{[run.strategyVersion, run.timeframe, shortId(run.runId)].filter(Boolean).join(" · ")}</small></td><td><StatusBadge tone={tone(run.status)}>{readable(run.status)}</StatusBadge></td><td>{formatInteger(run.symbolsCompleted ?? 0)} / {formatInteger(run.symbolsTotal ?? run.symbols.length)}</td><td className="numeric"><PnlValue value={run.metrics?.realizedPnl} market={market} /></td><td className="numeric">{run.metrics?.winRate != null ? formatNumber(run.metrics.winRate, 1) + "%" : "—"}</td><td>{formatDateTime(run.createdAt, market)}</td></tr>)}</tbody>
        </table></div> : <EmptyState title="No backtests yet" description="Run a strategy from the Backtest page." />}</SectionBody>
      </Panel>

      <details className="quant-secondary-disclosure">
        <summary><span><Activity size={16} />System details</span><small>Market data, strategy automation and watchlist diagnostics</small></summary>
        <div className="quant-secondary-grid">
          <Panel icon={<Database size={16} />} title="Market data">
            <SectionBody section={data.marketData}>{(section) => <dl className="quant-facts"><div><dt>Freshness</dt><dd>{readable(section.dataFreshness?.status)}</dd></div><div><dt>Data age</dt><dd>{formatAge(section.dataFreshness?.ageSeconds)}</dd></div><div><dt>Reason</dt><dd className="wrap">{readable(section.dataFreshness?.reason)}</dd></div><div><dt>Job</dt><dd>{readable(section.jobStatus?.status)}</dd></div><div><dt>Connection</dt><dd>{readable(section.jobStatus?.connectionStatus)}</dd></div></dl>}</SectionBody>
          </Panel>
          <Panel icon={<ScanSearch size={16} />} title="Watchlist" aside={<a className="quant-action-link" href={"/screener" + query}>Open</a>}>
            <SectionBody section={data.screener}>{(section) => <dl className="quant-facts"><div><dt>Latest run</dt><dd>{section.latestRun ? readable(section.latestRun.status) : "None"}</dd></div><div><dt>Candidates</dt><dd>{section.latestRun ? formatInteger(section.latestRun.symbolsPassed) + " / " + formatInteger(section.latestRun.symbolsTotal) : "—"}</dd></div><div><dt>Active watchlist</dt><dd>{section.activeUniverse?.name ?? "None"}</dd></div><div><dt>Completed</dt><dd>{formatDateTime(section.latestRun?.completedAt, market)}</dd></div></dl>}</SectionBody>
          </Panel>
          <Panel icon={<Activity size={16} />} title="Signal engine" aside={<a className="quant-action-link" href={"/signals" + query}>Open</a>}>
            <SectionBody section={data.signalEngine}>{(section) => <dl className="quant-facts"><div><dt>Workers</dt><dd>{formatInteger(section.workers.length)}</dd></div><div><dt>Strategies</dt><dd className="wrap">{section.workers.map((item) => humanize(item.strategyId ?? "unknown") + " · " + item.timeframe).join(", ") || "Not running"}</dd></div><div><dt>Data age</dt><dd>{formatAge(section.workers[0]?.dataAgeSeconds ?? section.stored[0]?.dataAgeSeconds)}</dd></div><div><dt>Last candle</dt><dd>{formatDateTime(section.workers[0]?.lastCompletedCandle ?? section.stored[0]?.lastCompletedCandle, market)}</dd></div></dl>}</SectionBody>
          </Panel>
        </div>
      </details>
    </>}
  </main>;
}
