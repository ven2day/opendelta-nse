"use client";

import { useCallback, useEffect, useState } from "react";
import { platformGet } from "../platform/platform-client";
import { ErrorState, LoadingState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

type Risk = { policy: Record<string, string | number | boolean>; state: string; warnings: string[]; paperOnly: boolean; liveOrdersEnabled: boolean };

function label(key: string): string { return key.replaceAll("_", " ").replace(/\b\w/g, (value) => value.toUpperCase()); }

export function RiskWorkspace() {
  const [data, setData] = useState<Risk | null>(null); const [error, setError] = useState("");
  const load = useCallback(async () => { setError(""); try { setData(await platformGet<Risk>("risk")); } catch (reason) { setError(reason instanceof Error ? reason.message : "Risk service is unavailable"); } }, []);
  useEffect(() => { const initial = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(initial); }, [load]);
  return <main className="quant-workspace"><WorkspaceHeader eyebrow="Portfolio & risk" title="Research risk controls" description="Capital, concentration, daily-loss and consecutive-loss constraints are centralized for paper-trading preparation." actions={<StatusBadge tone="good">Paper controls active</StatusBadge>} />
    {error ? <ErrorState message={error} retry={() => void load()} /> : !data ? <LoadingState /> : <><section className="quant-kpi-grid">{Object.entries(data.policy).filter(([key]) => !["paper_only", "live_orders_enabled"].includes(key)).map(([key, value]) => <article key={key}><span>{label(key)}</span><strong>{typeof value === "number" ? value.toLocaleString() : String(value)}</strong><small>Central risk policy</small></article>)}</section><section className="quant-panel quant-safety-panel"><div><h2>{data.state.replaceAll("_", " ")}</h2><p>The V1 risk module produces warnings and rejection reasons; it has no broker execution adapter.</p></div><dl><div><dt>Paper only</dt><dd>{data.paperOnly ? "Yes" : "No"}</dd></div><div><dt>Live orders</dt><dd>{data.liveOrdersEnabled ? "Enabled" : "Disabled"}</dd></div></dl>{data.warnings.map((warning) => <p className="quant-inline-warning" key={warning}>{warning}</p>)}</section></>}
  </main>;
}
