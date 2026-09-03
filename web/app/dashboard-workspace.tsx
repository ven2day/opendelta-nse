"use client";

import { Database, Gauge, Radio, RefreshCw, ScanSearch, Wallet } from "lucide-react";
import { useCallback, type ReactNode } from "react";
import { formatAge, formatDateTime, formatInteger, formatMoney, formatNumber, marketLabel, shortId, tone } from "./platform/format";
import type { PlatformMarket } from "./platform/platform-client";
import { useV2Resource } from "./platform/use-v2";
import { v2Get } from "./platform/v2-client";
import type { DashboardPayload, Section } from "./platform/v2-types";
import { EmptyState, LoadingState, Panel, PaperOnlyBadge, PnlValue, RequestErrorState, SectionError, StatusBadge, SymbolTags, WorkspaceHeader } from "./platform/workspace-ui";

const DASHBOARD_REFRESH_MS = 30_000;

function marketQuery(market: PlatformMarket): string {
  return market === "CRYPTO" ? "?market=CRYPTO" : "";
}

function SectionBody<T>({ section, children }: { section: Section<T>; children: (data: T) => ReactNode }) {
  if (!section.available || !section.data) return <SectionError message={section.error} />;
  return <>{children(section.data)}</>;
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
    <WorkspaceHeader
      eyebrow={`${marketLabel(market)} dashboard`}
      title={`${marketLabel(market)} trading dashboard`}
      actions={<div className="quant-header-actions"><PaperOnlyBadge /><button type="button" onClick={refresh}><RefreshCw size={15} />Refresh</button></div>}
    />
    {loading ? <LoadingState label="Loading dashboard" /> : error ? <RequestErrorState error={error} retry={reload} /> : data && <>
      <section className="quant-kpi-grid">
        <article><span>Market data</span><strong>{freshness?.status ?? "Unknown"}</strong><small>{freshness?.ageSeconds != null ? `Age ${formatAge(freshness.ageSeconds)}` : (freshness?.reason ?? "No freshness report")}</small></article>
        <article><span>Signal engine</span><strong>{worker?.status ?? "Unknown"}</strong><small>{worker?.connectionStatus ? `Connection ${String(worker.connectionStatus).toLowerCase()}` : "No worker report"}</small></article>
        <article><span>Paper equity</span><strong>{account ? formatMoney(account.equity, market, account.currency) : "—"}</strong><small>{account ? <>Today <PnlValue value={account.dailyPnl} market={market} currency={account.currency} /></> : "Paper account unavailable"}</small></article>
        <article><span>Active universe</span><strong>{universe?.name ?? "None"}</strong><small>{universe ? `${universe.symbols.length} symbols` : "Run the screener to build one"}</small></article>
      </section>

      <div className="quant-dashboard-grid">
      <Panel icon={<Database size={17} />} title="Market data" description="Freshness and ingestion job state for this market." aside={<StatusBadge tone={tone(freshness?.status)}>{freshness?.status ?? "unknown"}</StatusBadge>}>
        <SectionBody section={data.marketData}>{(section) => <dl className="quant-facts">
          <div><dt>Freshness</dt><dd>{section.dataFreshness?.status ?? "—"}</dd></div>
          <div><dt>Data age</dt><dd>{formatAge(section.dataFreshness?.ageSeconds)}</dd></div>
          <div><dt>Reason</dt><dd className="wrap">{section.dataFreshness?.reason ?? "—"}</dd></div>
          <div><dt>Job</dt><dd>{section.jobStatus?.status ?? "—"}</dd></div>
          <div><dt>Engine</dt><dd>{section.jobStatus?.engineStatus ?? "—"}</dd></div>
          <div><dt>Connection</dt><dd>{section.jobStatus?.connectionStatus ?? "—"}</dd></div>
          <div><dt>Environment</dt><dd>{section.environment ?? "—"}</dd></div>
        </dl>}</SectionBody>
      </Panel>

      <Panel icon={<ScanSearch size={17} />} title="Screener" description="Latest run and the active universe that feeds backtests and signals." aside={<a className="quant-action-link" href={`/screener${query}`}>Open screener</a>}>
        <SectionBody section={data.screener}>{(section) => <>
          <dl className="quant-facts">
            <div><dt>Latest run</dt><dd className="mono">{section.latestRun ? shortId(section.latestRun.runId) : "None"}</dd></div>
            <div><dt>Status</dt><dd>{section.latestRun?.status ?? "—"}</dd></div>
            <div><dt>Passed / total</dt><dd>{section.latestRun ? `${formatInteger(section.latestRun.symbolsPassed)} / ${formatInteger(section.latestRun.symbolsTotal)}` : "—"}</dd></div>
            <div><dt>Requested</dt><dd>{formatDateTime(section.latestRun?.requestedAt, market)}</dd></div>
            <div><dt>Completed</dt><dd>{formatDateTime(section.latestRun?.completedAt, market)}</dd></div>
            <div><dt>Active universe</dt><dd>{section.activeUniverse?.name ?? "None"}</dd></div>
          </dl>
          {section.activeUniverse && <div className="quant-panel-body"><SymbolTags symbols={section.activeUniverse.symbols} /></div>}
        </>}</SectionBody>
      </Panel>
      </div>

      <Panel icon={<Gauge size={17} />} title="Recent backtests" description="Database-backed incremental runs for this market." aside={<a className="quant-action-link" href={`/backtest${query}`}>Open backtest</a>}>
        <SectionBody section={data.backtests}>{(section) => section.recent.length ? <div className="quant-table-scroll"><table className="quant-table">
          <thead><tr><th>Run</th><th>Strategy</th><th>Status</th><th>Progress</th><th>Range</th><th className="numeric">Realized PnL</th><th className="numeric">Win rate</th><th>Created</th></tr></thead>
          <tbody>{section.recent.map((run) => <tr key={run.runId}>
            <td className="mono">{shortId(run.runId)}</td>
            <td><strong>{run.strategyId}</strong><small>{[run.strategyVersion, run.timeframe].filter(Boolean).join(" · ")}</small></td>
            <td><StatusBadge tone={tone(run.status)}>{run.status}</StatusBadge></td>
            <td>{formatInteger(run.symbolsCompleted ?? 0)} / {formatInteger(run.symbolsTotal ?? run.symbols.length)}</td>
            <td>{run.startDate} → {run.endDate}</td>
            <td className="numeric"><PnlValue value={run.metrics?.realizedPnl} market={market} /></td>
            <td className="numeric">{run.metrics?.winRate != null ? `${formatNumber(run.metrics.winRate, 1)}%` : "—"}</td>
            <td>{formatDateTime(run.createdAt, market)}</td>
          </tr>)}</tbody>
        </table></div> : <EmptyState title="No backtests yet" description="Run a strategy against the active universe from the Backtest page." />}</SectionBody>
      </Panel>

      <Panel icon={<Radio size={17} />} title="Signal engine" description="Live worker report and the last stored engine heartbeat." aside={<a className="quant-action-link" href={`/signals${query}`}>Open signals</a>}>
        <SectionBody section={data.signalEngine}>{(section) => <dl className="quant-facts">
          <div><dt>Workers</dt><dd>{formatInteger(section.workers.length)}</dd></div>
          <div><dt>Active strategies</dt><dd className="wrap">{section.workers.map((item) => `${item.strategyId} · ${item.timeframe}`).join(", ") || "Not running"}</dd></div>
          <div><dt>Connection</dt><dd>{section.workers.every((item) => item.connectionStatus === "CONNECTED") && section.workers.length ? "CONNECTED" : (section.workers[0]?.connectionStatus ?? section.stored[0]?.connectionStatus ?? "—")}</dd></div>
          <div><dt>Data age</dt><dd>{formatAge(section.workers[0]?.dataAgeSeconds ?? section.stored[0]?.dataAgeSeconds)}</dd></div>
          <div><dt>Last candle</dt><dd>{formatDateTime(section.workers[0]?.lastCompletedCandle ?? section.stored[0]?.lastCompletedCandle, market)}</dd></div>
          <div><dt>Signals created</dt><dd>{formatInteger(section.workers.reduce((total, item) => total + (item.signalsCreated ?? 0), 0))}</dd></div>
          <div><dt>Duplicates rejected</dt><dd>{formatInteger(section.workers.reduce((total, item) => total + (item.duplicatesRejected ?? 0), 0))}</dd></div>
          <div><dt>Message</dt><dd className="wrap">{section.workers[0]?.message ?? section.stored[0]?.message ?? "—"}</dd></div>
        </dl>}</SectionBody>
      </Panel>

      <Panel icon={<Wallet size={17} />} title="Paper account" description="Simulated fills only; no broker adapter is installed." aside={<a className="quant-action-link" href={`/paper-trading${query}`}>Open paper trading</a>}>
        <SectionBody section={data.paper}>{(section) => <>
          <div className="quant-panel-body"><section className="quant-kpi-grid dense">
            <article><span>Cash</span><strong>{formatMoney(section.account.cashBalance, market, section.account.currency)}</strong></article>
            <article><span>Market value</span><strong>{formatMoney(section.account.marketValue, market, section.account.currency)}</strong></article>
            <article><span>Realized PnL</span><strong><PnlValue value={section.account.realizedPnl} market={market} currency={section.account.currency} /></strong><small>Today <PnlValue value={section.account.realizedPnlToday} market={market} currency={section.account.currency} /></small></article>
            <article><span>Unrealized PnL</span><strong><PnlValue value={section.account.unrealizedPnl} market={market} currency={section.account.currency} /></strong></article>
            <article><span>Open positions</span><strong>{formatInteger(section.account.openPositions)}</strong><small>{formatInteger(section.account.closedLots)} closed lots</small></article>
          </section></div>
          {section.openPositions.length ? <div className="quant-table-scroll"><table className="quant-table">
            <thead><tr><th>Symbol</th><th className="numeric">Qty</th><th className="numeric">Entry</th><th className="numeric">Last</th><th className="numeric">Target</th><th className="numeric">Stop</th><th className="numeric">Unrealized</th><th>Status</th></tr></thead>
            <tbody>{section.openPositions.map((lot) => <tr key={lot.lotId}>
              <td><strong>{lot.symbol}</strong><small>Lot {lot.lotNumber ?? "—"} · {formatDateTime(lot.entryTimestamp, market)}</small></td>
              <td className="numeric">{formatNumber(lot.quantity, 4)}</td>
              <td className="numeric">{formatNumber(lot.entryPrice)}</td>
              <td className="numeric">{formatNumber(lot.lastPrice)}</td>
              <td className="numeric">{formatNumber(lot.targetPrice)}</td>
              <td className="numeric">{formatNumber(lot.stopPrice)}</td>
              <td className="numeric"><PnlValue value={lot.unrealizedPnl} market={market} currency={section.account.currency} /></td>
              <td><StatusBadge tone={tone(lot.status)}>{lot.status}</StatusBadge></td>
            </tr>)}</tbody>
          </table></div> : <EmptyState title="No open paper positions" description="Signals fill into the paper account automatically when the engine is running." />}
        </>}</SectionBody>
      </Panel>
    </>}
  </main>;
}
