"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { platformGet } from "../platform/platform-client";
import { EmptyState, ErrorState, LoadingState, StatusBadge } from "../platform/workspace-ui";

type MetricKey = "netProfit" | "expectancy" | "profitFactor" | "maximumDrawdown" | "returnToDrawdown" | "tradeCount" | "stability";
type Metrics = { status: string; tradeCount: number; netProfitCurrency: number; expectancy: number; profitFactor: number | null; maximumDrawdown: number; returnToDrawdown: number; monthlyStability?: { positiveRate?: number } };
type Result = { researchVersion?: string; researchValidity?: string; experimentId: string; configuration: { market: string; symbols?: string[]; symbol?: string; setupTimeframe?: string; timeframe?: string; mode: string; minimumTrades: number }; selectedFactorIds: string[]; untouchedTestResult: { metrics: Metrics | null } };
type Job = { jobId: string; jobType: string; status: string; researchValidity?: string; result?: Result | null };
type Jobs = { rows: Job[] };

const invalidLegacyModel = "LEGACY_INVALID_RESEARCH_MODEL";
const labels: Record<MetricKey, string> = { netProfit: "Net profit", expectancy: "Expectancy", profitFactor: "Profit factor", maximumDrawdown: "Drawdown", returnToDrawdown: "Return / drawdown", tradeCount: "Number of trades", stability: "Monthly stability" };

function value(result: Result, key: MetricKey): number {
  const metrics = result.untouchedTestResult.metrics;
  if (!metrics) return Number.NEGATIVE_INFINITY;
  if (key === "stability") return metrics.monthlyStability?.positiveRate ?? 0;
  if (key === "netProfit") return metrics.netProfitCurrency;
  return Number(metrics[key] ?? 0);
}

function percent(input: number): string { return `${(input * 100).toFixed(2)}%`; }

export function ResearchResults() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState("");
  const [sort, setSort] = useState<MetricKey>("expectancy");
  const load = useCallback(async () => {
    setError("");
    try {
      setJobs((await platformGet<Jobs>("jobs", { limit: "500" })).rows);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Experiment results are unavailable");
    }
  }, []);
  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(initial);
  }, [load]);

  const completed = useMemo(
    () => (jobs ?? []).filter((job) => job.jobType === "RESEARCH_EXPERIMENT" && job.status === "COMPLETE" && job.result),
    [jobs],
  );
  const legacy = useMemo(
    () => completed.filter((job) => job.researchValidity === invalidLegacyModel || job.result?.researchValidity === invalidLegacyModel),
    [completed],
  );
  const results = useMemo(() => completed
    .filter((job) => job.result?.researchVersion === "2" && job.result?.untouchedTestResult?.metrics && !job.researchValidity)
    .map((job) => ({ jobId: job.jobId, result: job.result as Result }))
    .sort((left, right) => {
      const direction = sort === "maximumDrawdown" ? 1 : -1;
      return (value(left.result, sort) - value(right.result, sort)) * direction || left.result.experimentId.localeCompare(right.result.experimentId);
    }), [completed, sort]);

  if (error) return <ErrorState message={error} retry={() => void load()} />;
  if (!jobs) return <LoadingState label="Loading completed experiments" />;
  return <div className="research-builder">
    {legacy.length > 0 && <section className="quant-panel" aria-label="Legacy invalid research results">
      <div className="quant-inline-warning"><strong>{invalidLegacyModel}</strong><br />These retained results used one-bar open-to-close observations, not strategy backtests with target, stop, holding, and portfolio rules. Their profitability metrics are hidden.</div>
      <div className="quant-table-scroll"><table className="quant-table"><thead><tr><th>Retained experiment</th><th>Configuration</th><th>Status</th></tr></thead><tbody>{legacy.map((job) => <tr key={job.jobId}><td className="mono">{job.result?.experimentId ?? job.jobId}</td><td>{job.result?.configuration ? `${job.result.configuration.market} · ${job.result.configuration.symbol} · ${job.result.configuration.timeframe}` : "Legacy configuration retained"}</td><td><StatusBadge tone="bad">{invalidLegacyModel}</StatusBadge></td></tr>)}</tbody></table></div>
    </section>}
    <section className="quant-panel">
      <div className="quant-panel-heading"><div><h2>Research V2 untouched-test leaderboard</h2><p>Only completed Research V2 strategy backtests may appear here. Win rate is not a ranking option.</p></div><label className="research-sort"><span>Sort by</span><select value={sort} onChange={(event) => setSort(event.target.value as MetricKey)}>{Object.entries(labels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label></div>
      {!results.length ? <EmptyState title="No completed Research V2 experiments" description="Run a real-trade Research V2 experiment after the server safety gate is enabled." /> : <div className="quant-table-scroll"><table className="quant-table"><thead><tr><th>Experiment</th><th>Factors</th><th>Sample</th><th>Net P&amp;L</th><th>Expectancy</th><th>Profit factor</th><th>Max drawdown</th><th>Return / DD</th><th>Stability</th></tr></thead><tbody>{results.map(({ jobId, result }) => { const metrics = result.untouchedTestResult.metrics as Metrics; const symbol = result.configuration.symbols?.join(", ") || result.configuration.symbol || "Universe"; const timeframe = result.configuration.setupTimeframe || result.configuration.timeframe || ""; return <tr key={jobId}><td><strong>{symbol} · {timeframe}</strong><small>{result.configuration.market} · {result.configuration.mode.replaceAll("_", " ")}</small><small className="mono">{result.experimentId}</small></td><td>{result.selectedFactorIds.join(", ") || "Baseline"}</td><td><StatusBadge tone={metrics.status === "CONCLUSIVE" ? "good" : "warn"}>{metrics.status}</StatusBadge><small>{metrics.tradeCount} / {result.configuration.minimumTrades} trades</small></td><td className="mono">{metrics.netProfitCurrency.toFixed(2)}</td><td className="mono">{percent(metrics.expectancy)}</td><td className="mono">{metrics.profitFactor == null ? "Undefined" : metrics.profitFactor.toFixed(2)}</td><td className="mono">{percent(metrics.maximumDrawdown)}</td><td className="mono">{metrics.returnToDrawdown.toFixed(2)}</td><td className="mono">{percent(metrics.monthlyStability?.positiveRate ?? 0)}</td></tr>; })}</tbody></table></div>}
    </section>
  </div>;
}
