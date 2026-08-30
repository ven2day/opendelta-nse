"use client";

import { useCallback, useEffect, useState } from "react";
import { Database, RefreshCw } from "lucide-react";
import { platformGet, type PlatformMarket } from "../platform/platform-client";
import { EmptyState, ErrorState, LoadingState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

type Instrument = { instrument_id: string; symbol: string; provider: string; provider_symbol: string; market_type: string; trading_status: string; company_name?: string | null; sector?: string | null };
type InstrumentResponse = { rows: Instrument[]; count: number; offset: number; limit: number };
type MarketContext = { session?: { status?: string; timezone?: string }; breadth?: { status?: string; advancing?: number; declining?: number; symbols?: number }; benchmarkDirection?: { status?: string }; sectorDirection?: { status?: string } };

export function MarketWorkspace({ initialMarket }: { initialMarket: PlatformMarket }) {
  const [market, setMarket] = useState(initialMarket);
  const [payload, setPayload] = useState<InstrumentResponse | null>(null);
  const [context, setContext] = useState<MarketContext | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true); setError(""); setContext(null);
    try { const [instruments, marketContext] = await Promise.all([platformGet<InstrumentResponse>("instruments", { market, limit: "100" }), platformGet<MarketContext>("market-context", { market })]); setPayload(instruments); setContext(marketContext); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Instrument master is unavailable"); }
    finally { setLoading(false); }
  }, [market]);

  useEffect(() => { const initial = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(initial); }, [load]);

  return <main className="quant-workspace">
    <WorkspaceHeader eyebrow={`${market} workspace`} title={market === "NSE" ? "NSE market research" : "Crypto market research"} description={market === "NSE" ? "India-session instruments, NIFTY context, Dhan data boundaries, signals and deterministic backtests." : "24/7 public OKX and VALR instruments, UTC sessions, long/short research and provider-specific costs."} actions={<div className="quant-header-actions"><StatusBadge tone="good">Paper only</StatusBadge><button type="button" onClick={() => void load()}><RefreshCw size={15} />Refresh</button></div>} />
    <div className="quant-market-tabs" role="tablist" aria-label="Market workspace">
      {(["NSE", "CRYPTO"] as PlatformMarket[]).map((item) => <button key={item} type="button" role="tab" aria-selected={market === item} className={market === item ? "active" : ""} onClick={() => setMarket(item)}>{item === "CRYPTO" ? "Crypto" : "NSE"}</button>)}
    </div>
    <section className="quant-kpi-grid">
      <article><span>Instruments</span><strong>{payload?.count ?? "—"}</strong><small>Provider-supported only</small></article>
      <article><span>Market status</span><strong>{context?.session?.status ?? (market === "NSE" ? "Schedule" : "24 / 7")}</strong><small>{context?.session?.timezone ?? (market === "NSE" ? "Asia/Kolkata" : "UTC")}</small></article>
      <article><span>Market breadth</span><strong>{context?.breadth?.status === "SUPPORTED" ? `${context.breadth.advancing ?? 0} / ${context.breadth.symbols ?? 0}` : "Unavailable"}</strong><small>{context?.breadth?.status === "SUPPORTED" ? "Advancing instruments" : "No assumptions manufactured"}</small></article>
      <article><span>Execution</span><strong>Disabled</strong><small>Research and paper signals</small></article>
    </section>
    <section className="quant-panel"><div className="quant-panel-heading"><div><Database size={17} /><div><h2>Instrument master</h2><p>Exact provider identifiers and activation state. Unavailable instruments are not offered.</p></div></div><StatusBadge>{payload ? `${payload.count} total` : "Loading"}</StatusBadge></div>
      {loading ? <LoadingState label="Loading instrument master" /> : error ? <ErrorState message={error} retry={() => void load()} /> : !payload?.rows.length ? <EmptyState title="No configured instruments" description={market === "CRYPTO" ? "Add an exact OKX or VALR catalog instrument in the Crypto workspace." : "The managed NSE symbol file is empty."} /> : <div className="quant-table-scroll"><table className="quant-table"><thead><tr><th>Symbol</th><th>Provider symbol</th><th>Provider</th><th>Type</th><th>Sector</th><th>Status</th></tr></thead><tbody>{payload.rows.map((row) => <tr key={row.instrument_id}><td><strong>{row.symbol}</strong><small>{row.company_name || row.instrument_id}</small></td><td className="mono">{row.provider_symbol}</td><td>{row.provider}</td><td>{row.market_type}</td><td>{row.sector || "Not mapped"}</td><td><StatusBadge tone={row.trading_status === "ACTIVE" ? "good" : "warn"}>{row.trading_status}</StatusBadge></td></tr>)}</tbody></table></div>}
    </section>
    <div className="quant-link-grid"><a href={market === "NSE" ? "/scanner" : "/signals/crypto"}><strong>{market === "NSE" ? "Open screener" : "Crypto signals"}</strong><span>Review current candidates and market context.</span></a><a href={market === "NSE" ? "/backtest" : "/backtest/crypto"}><strong>Open backtests</strong><span>Use market-specific assumptions and costs.</span></a><a href="/data-health"><strong>Review data health</strong><span>Freshness, providers, cache and quality state.</span></a></div>
  </main>;
}
