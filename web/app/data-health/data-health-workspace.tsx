"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { platformGet } from "../platform/platform-client";
import { ErrorState, LoadingState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

type Provider = { provider: string; markets: string[]; timeframes: string[]; data_types: string[]; timezone: string; public_only: boolean; status: string; privateTradingEndpoints: boolean };
type Health = { marketData: { status: string; modifiedAt?: string; ageSeconds?: number; thresholdSeconds?: number; reason?: string; dataVersion?: string; marketStatus?: string; expectedSessionDate?: string; dataSessionDate?: string }; featureCache: { status: string; entries: number }; providers: Provider[]; warnings: string[] };

function providerTone(status: string): "good" | "warn" | "bad" | "neutral" {
  if (["HEALTHY", "FRESH", "RUNNING", "AVAILABLE"].includes(status)) return "good";
  if (["FAILED", "UNAVAILABLE", "INVALID", "MISSING"].includes(status)) return "bad";
  if (["DEGRADED", "STALE", "STOPPED", "NOT_PROBED"].includes(status)) return "warn";
  return "neutral";
}

export function DataHealthWorkspace() {
  const [data, setData] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    try {
      setData(await platformGet<Health>("data-health"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Health status is unavailable");
    }
  }, []);
  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    const timer = window.setInterval(load, 30_000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [load]);

  return <main className="quant-workspace">
    <WorkspaceHeader eyebrow="Data quality" title="Data Health" description="Provider capability, normalized-data freshness and feature-cache state are visible without implying unsupported coverage." actions={<button type="button" onClick={() => void load()}><RefreshCw size={15} />Refresh</button>} />
    {error ? <ErrorState message={error} retry={() => void load()} /> : !data ? <LoadingState label="Checking providers and datasets" /> : <>
      <section className="quant-kpi-grid"><article><span>Market snapshot</span><strong>{data.marketData.reason === "MARKET_CLOSED_LAST_SESSION_CURRENT" ? "MARKET CLOSED" : data.marketData.status}</strong><small>{data.marketData.reason === "MARKET_CLOSED_LAST_SESSION_CURRENT" ? `Current through ${data.marketData.dataSessionDate ?? "last session"}` : data.marketData.ageSeconds == null ? data.marketData.reason || "No timestamp" : `${Math.round(data.marketData.ageSeconds / 60)} minutes old`}</small></article><article><span>Feature cache</span><strong>{data.featureCache.entries.toLocaleString()}</strong><small>{data.featureCache.status}</small></article><article><span>Providers</span><strong>{data.providers.length}</strong><small>Capability boundaries</small></article><article><span>Private endpoints</span><strong>None</strong><small>Market data only</small></article></section>
      <section className="quant-panel"><div className="quant-panel-heading"><div><h2>Provider capabilities</h2><p>Support is checked by provider, market, timeframe and data type.</p></div></div><div className="quant-table-scroll"><table className="quant-table data-health-table"><thead><tr><th>Provider</th><th>Markets</th><th>Timeframes</th><th>Data types</th><th>Timezone</th><th>Status</th></tr></thead><tbody>{data.providers.map((provider) => <tr key={provider.provider}><td><strong>{provider.provider}</strong><small>{provider.public_only ? "Public APIs" : "Configured authenticated data"}</small></td><td>{provider.markets.join(", ")}</td><td><div className="data-health-values mono">{provider.timeframes.map((timeframe) => <span key={timeframe}>{timeframe}</span>)}</div></td><td><div className="data-health-values">{provider.data_types.map((dataType) => <span key={dataType}>{dataType.replaceAll("_", " ")}</span>)}</div></td><td>{provider.timezone}</td><td><StatusBadge tone={providerTone(provider.status)}>{provider.status}</StatusBadge></td></tr>)}</tbody></table></div></section>
      <section className="quant-notes"><h2>Integrity rules</h2>{data.warnings.map((warning) => <p key={warning}>{warning}</p>)}</section>
    </>}
  </main>;
}
