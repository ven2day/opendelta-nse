"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { platformGet } from "../platform/platform-client";
import { EmptyState, ErrorState, LoadingState, StatusBadge } from "../platform/workspace-ui";

type MetricKey = "netProfit" | "expectancy" | "profitFactor" | "maximumDrawdown" | "returnToDrawdown" | "tradeCount" | "stability";
type Metrics = { status: string; tradeCount: number; netProfit: number; expectancy: number; profitFactor: number; maximumDrawdown: number; returnToDrawdown: number; monthlyStability?: { positiveMonthRate?: number } };
type Result = { experimentId: string; generatedAt: string; configuration: { market: string; symbol: string; timeframe: string; mode: string; minimumTrades: number }; selectedFactorIds: string[]; untouchedTestResult: Metrics };
type Job = { jobId: string; jobType: string; status: string; result?: Result | null };
type Jobs = { rows: Job[] };

const labels: Record<MetricKey, string> = { netProfit: "Net profit", expectancy: "Expectancy", profitFactor: "Profit factor", maximumDrawdown: "Drawdown", returnToDrawdown: "Return / drawdown", tradeCount: "Number of trades", stability: "Monthly stability" };

function value(result: Result, key: MetricKey): number {
  if (key === "stability") return result.untouchedTestResult.monthlyStability?.positiveMonthRate ?? 0;
  return Number(result.untouchedTestResult[key]);
}

function percent(input: number): string { return `${(input * 100).toFixed(2)}%`; }

export function ResearchResults() {
  const [jobs, setJobs] = useState<Job[] | null>(null); const [error, setError] = useState(""); const [sort, setSort] = useState<MetricKey>("expectancy");
  const load = useCallback(async () => { setError(""); try { setJobs((await platformGet<Jobs>("jobs", { limit: "500" })).rows); } catch (reason) { setError(reason instanceof Error ? reason.message : "Experiment results are unavailable"); } }, []);
  useEffect(() => { const initial = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(initial); }, [load]);
  const results = useMemo(() => (jobs ?? []).filter((job) => job.jobType === "RESEARCH_EXPERIMENT" && job.status === "COMPLETE" && job.result).map((job) => ({ jobId: job.jobId, result: job.result as Result })).sort((left, right) => {
    const direction = sort === "maximumDrawdown" ? 1 : -1;
    return (value(left.result, sort) - value(right.result, sort)) * direction || left.result.experimentId.localeCompare(right.result.experimentId);
  }), [jobs, sort]);
  if (error) return <ErrorState message={error} retry={() => void load()} />;
  if (!jobs) return <LoadingState label="Loading completed experiments" />;
  return <section className="quant-panel"><div className="quant-panel-heading"><div><h2>Untouched-test leaderboard</h2><p>Results below their configured minimum sample remain inconclusive. Win rate is not a ranking option.</p></div><label className="research-sort"><span>Sort by</span><select value={sort} onChange={(event) => setSort(event.target.value as MetricKey)}>{Object.entries(labels).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label></div>{!results.length ? <EmptyState title="No completed research experiments" description="Run an exact, tournament, or forward-selection experiment. Completed versioned results will appear here." /> : <div className="quant-table-scroll"><table className="quant-table"><thead><tr><th>Experiment</th><th>Factors</th><th>Sample</th><th>Net profit</th><th>Expectancy</th><th>Profit factor</th><th>Max drawdown</th><th>Return / DD</th><th>Stability</th></tr></thead><tbody>{results.map(({ jobId, result }) => { const metrics = result.untouchedTestResult; return <tr key={jobId}><td><strong>{result.configuration.symbol} · {result.configuration.timeframe}</strong><small>{result.configuration.market} · {result.configuration.mode.replaceAll("_", " ")}</small><small className="mono">{result.experimentId}</small></td><td>{result.selectedFactorIds.join(", ") || "Baseline"}</td><td><StatusBadge tone={metrics.status === "CONCLUSIVE" ? "good" : "warn"}>{metrics.status}</StatusBadge><small>{metrics.tradeCount} / {result.configuration.minimumTrades} trades</small></td><td className="mono">{percent(metrics.netProfit)}</td><td className="mono">{percent(metrics.expectancy)}</td><td className="mono">{Number.isFinite(metrics.profitFactor) ? metrics.profitFactor.toFixed(2) : "∞"}</td><td className="mono">{percent(metrics.maximumDrawdown)}</td><td className="mono">{metrics.returnToDrawdown.toFixed(2)}</td><td className="mono">{percent(metrics.monthlyStability?.positiveMonthRate ?? 0)}</td></tr>; })}</tbody></table></div>}</section>;
}
