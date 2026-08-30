"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { platformGet } from "../platform/platform-client";
import { ErrorState, LoadingState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

type Provider = { provider: string; markets: string[]; timeframes: string[]; data_types: string[]; timezone: string; public_only: boolean; status: string; privateTradingEndpoints: boolean };
type Health = { marketData: { status: string; modifiedAt?: string; ageSeconds?: number; thresholdSeconds?: number; reason?: string; dataVersion?: string }; featureCache: { status: string; entries: number }; providers: Provider[]; warnings: string[] };

export function DataHealthWorkspace() {
  const [data, setData] = useState<Health | null>(null); const [error, setError] = useState("");
  const load = useCallback(async () => { setError(""); try { setData(await platformGet<Health>("data-health")); } catch (reason) { setError(reason instanceof Error ? reason.message : "Health status is unavailable"); } }, []);
  useEffect(() => { const initial = window.setTimeout(() => void load(), 0); const timer = window.setInterval(load, 30_000); return () => { window.clearTimeout(initial); window.clearInterval(timer); }; }, [load]);
  return <main className="quant-workspace"><WorkspaceHeader eyebrow="Data quality" title="Data Health" description="Provider capability, normalized-data freshness and feature-cache state are visible without implying unsupported coverage." actions={<button type="button" onClick={() => void load()}><RefreshCw size={15} />Refresh</button>} />
    {error ? <ErrorState message={error} retry={() => void load()} /> : !data ? <LoadingState label="Checking providers and datasets" /> : <><section className="quant-kpi-grid"><article><span>Market snapshot</span><strong>{data.marketData.status}</strong><small>{data.marketData.ageSeconds == null ? data.marketData.reason || "No timestamp" : `${Math.round(data.marketData.ageSeconds / 60)} minutes old`}</small></article><article><span>Feature cache</span><strong>{data.featureCache.entries.toLocaleString()}</strong><small>{data.featureCache.status}</small></article><article><span>Providers</span><strong>{data.providers.length}</strong><small>Capability boundaries</small></article><article><span>Private endpoints</span><strong>None</strong><small>Market data only</small></article></section><section className="quant-panel"><div className="quant-panel-heading"><div><h2>Provider capabilities</h2><p>Support is checked by provider, market, timeframe and data type.</p></div></div><div className="quant-table-scroll"><table className="quant-table"><thead><tr><th>Provider</th><th>Markets</th><th>Timeframes</th><th>Data types</th><th>Timezone</th><th>Status</th></tr></thead><tbody>{data.providers.map((provider) => <tr key={provider.provider}><td><strong>{provider.provider}</strong><small>{provider.public_only ? "Public APIs" : "Configured authenticated data"}</small></td><td>{provider.markets.join(", ")}</td><td className="mono">{provider.timeframes.join(" · ")}</td><td>{provider.data_types.join(", ").replaceAll("_", " ")}</td><td>{provider.timezone}</td><td><StatusBadge tone="good">{provider.status}</StatusBadge></td></tr>)}</tbody></table></div></section><section className="quant-notes"><h2>Integrity rules</h2>{data.warnings.map((warning) => <p key={warning}>{warning}</p>)}</section></>}
  </main>;
}
