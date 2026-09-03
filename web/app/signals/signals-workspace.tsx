"use client";

import { Activity, Radio, RefreshCw } from "lucide-react";
import { useCallback, useState } from "react";
import { formatAge, formatDateTime, formatInteger, formatNumber, marketLabel, tone } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { useV2Resource } from "../platform/use-v2";
import { v2Get } from "../platform/v2-client";
import type { SignalsHealth, SignalsResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, MarketTabs, PaperOnlyBadge, Panel, RequestErrorState, StatusBadge, Tag, WorkspaceHeader } from "../platform/workspace-ui";

const SIGNAL_REFRESH_MS = 15_000;
const SIGNAL_LIMIT = 200;
const STATUS_OPTIONS = ["STRONG_BUY", "HOLDING", "TARGET_HIT", "EXITED", "EXPIRED"];
const FALLBACK_COLOURS: Record<string, string> = { STRONG_BUY: "blue", HOLDING: "orange", TARGET_HIT: "green", EXITED: "red", EXPIRED: "red" };

export function SignalsWorkspace({ market }: { market: PlatformMarket }) {
  const [status, setStatus] = useState("");
  const [symbolInput, setSymbolInput] = useState("");
  const [symbol, setSymbol] = useState("");
  const loadHealth = useCallback(() => v2Get<SignalsHealth>("signals/health", { market }), [market]);
  const loadSignals = useCallback(() => v2Get<SignalsResponse>("signals", { market, status: status || undefined, symbol: symbol || undefined, limit: SIGNAL_LIMIT }), [market, status, symbol]);
  const health = useV2Resource(loadHealth, SIGNAL_REFRESH_MS);
  const signals = useV2Resource(loadSignals, SIGNAL_REFRESH_MS);

  const worker = health.data?.workers?.[market] ?? null;
  const stored = health.data?.engines?.find((engine) => engine.market === market) ?? null;
  const engine = worker ?? stored;
  const colours = { ...FALLBACK_COLOURS, ...(signals.data?.colours ?? {}) };
  const rows = signals.data?.signals ?? [];
  const counts = rows.reduce<Record<string, number>>((accumulator, signal) => ({ ...accumulator, [signal.status]: (accumulator[signal.status] ?? 0) + 1 }), {});

  return <main className="quant-workspace">
    <WorkspaceHeader
      eyebrow={`${marketLabel(market)} signals`}
      title="Live signals"
      description="Completed-candle signals from the unified engine. Gold is a fresh strong buy, orange is holding, green hit its target, and red exited or expired. Refreshes every 15 seconds."
      actions={<div className="quant-header-actions"><PaperOnlyBadge /><button type="button" onClick={() => { health.refresh(); signals.refresh(); }}><RefreshCw size={15} />Refresh</button></div>}
    />
    <MarketTabs market={market} pathname="/signals" />

    {health.error ? <RequestErrorState error={health.error} retry={health.reload} /> : <div className="quant-health-bar" role="status" aria-label="Signal engine health">
      <div><span>Worker</span><strong>{health.loading ? "Checking" : (engine?.status ?? "Not running")}</strong><small>{worker ? "Live worker report" : stored ? "Last stored heartbeat" : "No engine report for this market"}</small></div>
      <div><span>Connection</span><strong>{engine?.connectionStatus ?? "—"}</strong></div>
      <div><span>Data age</span><strong>{formatAge(engine?.dataAgeSeconds)}</strong></div>
      <div><span>Last completed candle</span><strong>{formatDateTime(engine?.lastCompletedCandle, market)}</strong></div>
      <div><span>Symbols</span><strong>{worker?.symbols ? formatInteger(worker.symbols.length) : "—"}</strong></div>
      <div><span>Created / duplicates</span><strong>{formatInteger(worker?.signalsCreated)} / {formatInteger(worker?.duplicatesRejected)}</strong></div>
      <div><span>Message</span><strong title={engine?.message ?? undefined}>{engine?.message ?? "—"}</strong><small>{stored?.updatedAt ? `Stored ${formatDateTime(stored.updatedAt, market)}` : ""}</small></div>
    </div>}

    <section className="quant-kpi-grid dense">
      {STATUS_OPTIONS.map((option) => <article key={option}><span className="quant-signal-status" data-colour={colours[option] ?? "grey"}>{option.replace("_", " ")}</span><strong>{formatInteger(counts[option] ?? 0)}</strong></article>)}
    </section>

    <Panel icon={<Radio size={17} />} title="Signals" description={`Latest ${SIGNAL_LIMIT} ${marketLabel(market)} signals matching the filter.`} aside={<form className="quant-toolbar" onSubmit={(event) => { event.preventDefault(); setSymbol(symbolInput.trim().toUpperCase()); }}>
      <label><span>Status</span><select value={status} onChange={(event) => setStatus(event.target.value)}><option value="">All statuses</option>{STATUS_OPTIONS.map((option) => <option key={option} value={option}>{option.replace("_", " ")}</option>)}</select></label>
      <label><span>Symbol</span><input type="text" value={symbolInput} placeholder="Any symbol" onChange={(event) => setSymbolInput(event.target.value)} /></label>
      <button type="submit">Apply</button>
    </form>}>
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
          <td><div className="quant-tag-list">{(signal.reasons ?? []).map((reason) => <Tag key={reason}>{reason}</Tag>)}</div></td>
          <td><StatusBadge tone={tone(signal.status)}>{signal.strategyId}{signal.strategyVersion ? ` v${signal.strategyVersion}` : ""}</StatusBadge></td>
        </tr>)}</tbody>
      </table></div>}
    </Panel>
    <p className="quant-inline-note"><Activity size={12} /> Signals are informational and feed the paper account only. No broker order is ever placed from this page.</p>
  </main>;
}
