"use client";

import { Activity, Radio, RefreshCw } from "lucide-react";
import { useCallback, useState } from "react";
import { formatAge, formatDateTime, formatInteger, formatNumber, humanize, marketLabel, tone } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { useV2Resource } from "../platform/use-v2";
import { v2Get } from "../platform/v2-client";
import type { SignalsHealth, SignalsResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, PaperOnlyBadge, Panel, RequestErrorState, StatusBadge, Tag, WorkspaceHeader } from "../platform/workspace-ui";

const SIGNAL_REFRESH_MS = 15_000;
const SIGNAL_LIMIT = 200;
const STATUS_OPTIONS = ["STRONG_BUY", "HOLDING", "TARGET_HIT", "EXITED", "EXPIRED"];
const FALLBACK_COLOURS: Record<string, string> = { STRONG_BUY: "blue", HOLDING: "orange", TARGET_HIT: "green", EXITED: "red", EXPIRED: "red" };

export function SignalsWorkspace({ market }: { market: PlatformMarket }) {
  const [status, setStatus] = useState("");
  const [strategy, setStrategy] = useState("");
  const [timeframe, setTimeframe] = useState("");
  const [symbolInput, setSymbolInput] = useState("");
  const [symbol, setSymbol] = useState("");
  const loadHealth = useCallback(() => v2Get<SignalsHealth>("signals/health", { market }), [market]);
  const loadSignals = useCallback(() => v2Get<SignalsResponse>("signals", { market, status: status || undefined, symbol: symbol || undefined, strategy: strategy || undefined, timeframe: timeframe || undefined, limit: SIGNAL_LIMIT }), [market, status, symbol, strategy, timeframe]);
  const health = useV2Resource(loadHealth, SIGNAL_REFRESH_MS);
  const signals = useV2Resource(loadSignals, SIGNAL_REFRESH_MS);

  const workers = health.data?.workers?.[market] ?? [];
  const stored = health.data?.engines?.filter((engine) => engine.market === market) ?? [];
  const engine = workers[0] ?? stored[0] ?? null;
  const colours = { ...FALLBACK_COLOURS, ...(signals.data?.colours ?? {}) };
  const rows = signals.data?.signals ?? [];
  const strategyOptions = Array.from(new Set([...workers.map((item) => item.strategyId), ...rows.map((item) => item.strategyId)].filter((item): item is string => Boolean(item))));
  const timeframeOptions = Array.from(new Set([...workers.map((item) => item.timeframe), ...rows.map((item) => item.timeframe)].filter((item): item is string => Boolean(item))));
  const readyWorkers = workers.filter((item) => item.status === "READY").length;
  const symbols = new Set(workers.flatMap((item) => item.symbols ?? []));
  const counts = rows.reduce<Record<string, number>>((accumulator, signal) => ({ ...accumulator, [signal.status]: (accumulator[signal.status] ?? 0) + 1 }), {});

  return <main className="quant-workspace">
    <WorkspaceHeader
      eyebrow={`${marketLabel(market)} signals`}
      title="Live signals"
      actions={<div className="quant-header-actions"><PaperOnlyBadge /><button type="button" onClick={() => { health.refresh(); signals.refresh(); }}><RefreshCw size={15} />Refresh</button></div>}
    />

    {health.error ? <RequestErrorState error={health.error} retry={health.reload} /> : <div className="quant-health-bar" role="status" aria-label="Signal engine health">
      <div><span>Workers</span><strong>{health.loading ? "Checking" : `${readyWorkers} / ${workers.length} ready`}</strong><small>{workers.length ? "Independent strategy/timeframe workers" : stored.length ? "Last stored heartbeats" : "No engine report for this market"}</small></div>
      <div><span>Connection</span><strong>{workers.length && workers.every((item) => item.connectionStatus === "CONNECTED") ? "CONNECTED" : (engine?.connectionStatus ?? "—")}</strong></div>
      <div><span>Data age</span><strong>{formatAge(engine?.dataAgeSeconds)}</strong></div>
      <div><span>Last completed candle</span><strong>{formatDateTime(engine?.lastCompletedCandle, market)}</strong></div>
      <div><span>Symbols</span><strong>{workers.length ? formatInteger(symbols.size) : "—"}</strong></div>
      <div><span>Created / duplicates</span><strong>{formatInteger(workers.reduce((total, item) => total + (item.signalsCreated ?? 0), 0))} / {formatInteger(workers.reduce((total, item) => total + (item.duplicatesRejected ?? 0), 0))}</strong></div>
      <div><span>Message</span><strong title={engine?.message ?? undefined}>{workers.length ? `${workers.length} active strategy worker${workers.length === 1 ? "" : "s"}` : (engine?.message ?? "—")}</strong><small>{stored[0]?.updatedAt ? `Stored ${formatDateTime(stored[0].updatedAt, market)}` : ""}</small></div>
    </div>}

    <Panel icon={<Activity size={17} />} title="Active signal strategies">
      {workers.length ? <div className="quant-table-scroll"><table className="quant-table">
        <thead><tr><th>Strategy</th><th>Timeframe</th><th>Status</th><th>Connection</th><th>Last completed candle</th><th className="numeric">Signals</th></tr></thead>
        <tbody>{workers.map((item) => <tr key={item.engine ?? `${item.strategyId}:${item.timeframe}`}>
          <td><strong>{item.strategyId}</strong><small>{item.strategyVersion ? `v${item.strategyVersion}` : ""}</small></td>
          <td>{item.timeframe}</td><td><StatusBadge tone={tone(item.status)}>{item.status ?? "unknown"}</StatusBadge></td>
          <td>{item.connectionStatus ?? "—"}</td><td>{formatDateTime(item.lastCompletedCandle, market)}</td><td className="numeric">{formatInteger(item.signalsCreated)}</td>
        </tr>)}</tbody>
      </table></div> : <EmptyState title="No active strategy workers" description="Enable at least one live strategy for this market." />}
    </Panel>

    <section className="quant-kpi-grid dense">
      {STATUS_OPTIONS.map((option) => <article key={option}><span className="quant-signal-status" data-colour={colours[option] ?? "grey"}>{option.replace("_", " ")}</span><strong>{formatInteger(counts[option] ?? 0)}</strong></article>)}
    </section>

    <Panel icon={<Radio size={17} />} title="Signals" description={`Latest ${SIGNAL_LIMIT} ${marketLabel(market)} signals.`} aside={<details className="quant-filter-menu"><summary>Filters{[status, strategy, timeframe, symbol].filter(Boolean).length ? ` (${[status, strategy, timeframe, symbol].filter(Boolean).length})` : ""}</summary><form className="quant-toolbar" onSubmit={(event) => { event.preventDefault(); setSymbol(symbolInput.trim().toUpperCase()); }}>
      <label><span>Status</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option>{STATUS_OPTIONS.map((option) => <option key={option} value={option}>{option.replace("_", " ")}</option>)}</select></label>
      <label><span>Strategy</span><select value={strategy} onChange={(event) => setStrategy(event.target.value)}><option value="">All strategies</option>{strategyOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select></label>
      <label><span>Timeframe</span><select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}><option value="">All timeframes</option>{timeframeOptions.map((option) => <option key={option} value={option}>{option}</option>)}</select></label>
      <label><span>Symbol</span><input type="text" value={symbolInput} placeholder="Any symbol" onChange={(event) => setSymbolInput(event.target.value)} /></label>
      <button type="submit">Apply</button>
    </form></details>}>
      {signals.loading ? <LoadingState label="Loading signals" /> : signals.error ? <RequestErrorState error={signals.error} retry={signals.reload} /> : !rows.length ? <EmptyState title="No signals" description={engine ? "No signals match the current filter." : "The signal engine has not reported for this market yet."} /> : <div className="quant-table-scroll tall"><table className="quant-table">
        <thead><tr><th>Symbol</th><th>Status</th><th>Candle</th><th className="numeric">Signal</th><th className="numeric">Target</th><th className="numeric">Stop</th><th className="numeric">Last</th><th>Expires</th><th>Exit</th><th>Reasons</th><th>Strategy</th></tr></thead>
        <tbody>{rows.map((signal) => <tr key={signal.signalId}>
          <td><strong>{signal.symbol}</strong><small>{signal.timeframe}{signal.signalType ? ` · ${signal.signalType}` : ""}</small></td>
          <td><span className="quant-signal-status" data-colour={signal.colour ?? colours[signal.status] ?? "grey"}>{String(signal.status).replace("_", " ")}</span></td>
          <td>{formatDateTime(signal.candleTimestamp, market)}</td>
          <td className="numeric">{formatNumber(signal.signalPrice)}</td>
          <td className="numeric">{formatNumber(signal.targetPrice)}</td>
          <td className="numeric">{formatNumber(signal.stopPrice)}</td>
          <td className="numeric">{formatNumber(signal.lastPrice)}</td>
          <td>{formatDateTime(signal.expiresAt, market)}</td>
          <td>{signal.exitTimestamp ? <><span>{formatDateTime(signal.exitTimestamp, market)}</span><small>@ {formatNumber(signal.exitPrice)}</small></> : "—"}</td>
          <td><div className="quant-tag-list">{(signal.reasons ?? []).map((reason) => <Tag key={reason}>{humanize(reason)}</Tag>)}</div></td>
          <td><StatusBadge tone={tone(signal.status)}>{signal.strategyId}{signal.strategyVersion ? ` v${signal.strategyVersion}` : ""}</StatusBadge></td>
        </tr>)}</tbody>
      </table></div>}
    </Panel>
    <p className="quant-inline-note"><Activity size={12} /> Signals are informational and feed the paper account only. No broker order is ever placed from this page.</p>
  </main>;
}
