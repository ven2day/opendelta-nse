"use client";

import { useCallback, useEffect, useState } from "react";
import { platformGet, type PlatformMarket } from "../platform/platform-client";
import { EmptyState, ErrorState, LoadingState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

type Strategy = { key: string; version: string; name: string; market: PlatformMarket; status: "ACTIVE" | "RESEARCH_ONLY" | "RETIRED"; supports_long: boolean; supports_short: boolean; execution_model: string; description: string; compatibility: string[]; live_orders_enabled: boolean };
type Response = { rows: Strategy[]; count: number; paperOnly: boolean; liveOrdersEnabled: boolean };

export function StrategyCatalog() {
  const [market, setMarket] = useState<PlatformMarket>("NSE");
  const [data, setData] = useState<Response | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => { setError(""); setData(null); try { setData(await platformGet<Response>("strategies", { market })); } catch (reason) { setError(reason instanceof Error ? reason.message : "Strategy catalog is unavailable"); } }, [market]);
  useEffect(() => { const initial = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(initial); }, [load]);
  return <main className="quant-workspace"><WorkspaceHeader eyebrow="Strategy engine" title="Versioned strategy catalog" description="Compatibility, execution rules and lifecycle status are explicit. Existing strategy semantics remain available through legacy adapters." actions={<StatusBadge tone="good">Live orders disabled</StatusBadge>} />
    <div className="quant-market-tabs" role="tablist" aria-label="Strategy market">{(["NSE", "CRYPTO"] as PlatformMarket[]).map((item) => <button type="button" role="tab" aria-selected={market === item} className={market === item ? "active" : ""} key={item} onClick={() => setMarket(item)}>{item}</button>)}</div>
    <section className="quant-panel">{error ? <ErrorState message={error} retry={() => void load()} /> : !data ? <LoadingState label="Loading registered strategies" /> : !data.rows.length ? <EmptyState title="No strategies registered" description="This market has no compatible strategy definitions." /> : <div className="quant-card-list">{data.rows.map((strategy) => <article key={strategy.key}><div className="quant-card-title"><div><span className="mono">{strategy.key} · {strategy.version}</span><h2>{strategy.name}</h2></div><StatusBadge tone={strategy.status === "ACTIVE" ? "good" : strategy.status === "RETIRED" ? "bad" : "warn"}>{strategy.status.replace("_", " ")}</StatusBadge></div><p>{strategy.description}</p><dl><div><dt>Execution</dt><dd>{strategy.execution_model}</dd></div><div><dt>Directions</dt><dd>{strategy.supports_long ? "Long" : "—"}{strategy.supports_short ? " + Short" : ""}</dd></div><div><dt>Timeframes</dt><dd>{strategy.compatibility.join(", ")}</dd></div><div><dt>Broker orders</dt><dd>{strategy.live_orders_enabled ? "Enabled" : "Disabled"}</dd></div></dl>{strategy.status === "RETIRED" && <div className="quant-inline-warning">Historical results remain readable. New runs and trade-ready promotion are blocked.</div>}</article>)}</div>}</section>
  </main>;
}
