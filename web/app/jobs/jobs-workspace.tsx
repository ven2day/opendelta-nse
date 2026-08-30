"use client";

import { useCallback, useEffect, useState } from "react";
import { Ban, RefreshCw } from "lucide-react";
import { cancelPlatformJob, platformGet } from "../platform/platform-client";
import { EmptyState, ErrorState, LoadingState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

type Job = { jobId: string; jobType: string; status: string; progress: number; attempt: number; maximumAttempts: number; createdAt: string; updatedAt: string; error?: { code: string; message: string } | null };
type Jobs = { rows: Job[]; count: number; worker: { status: string; maximumWorkers: number; queueDepth: number; running: number } };
const terminal = new Set(["COMPLETE", "FAILED", "CANCELLED"]);

function tone(status: string): "good" | "warn" | "bad" | "neutral" { return status === "COMPLETE" ? "good" : status === "FAILED" || status === "CANCELLED" ? "bad" : status === "RUNNING" ? "warn" : "neutral"; }

export function JobsWorkspace() {
  const [data, setData] = useState<Jobs | null>(null); const [error, setError] = useState(""); const [busy, setBusy] = useState("");
  const load = useCallback(async () => { try { setData(await platformGet<Jobs>("jobs", { limit: "100" })); setError(""); } catch (reason) { setError(reason instanceof Error ? reason.message : "Worker state is unavailable"); } }, []);
  useEffect(() => { const initial = window.setTimeout(() => void load(), 0); const timer = window.setInterval(load, 2_500); return () => { window.clearTimeout(initial); window.clearInterval(timer); }; }, [load]);
  const cancel = async (jobId: string) => { setBusy(jobId); try { await cancelPlatformJob(jobId); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Cancellation failed"); } finally { setBusy(""); } };
  return <main className="quant-workspace"><WorkspaceHeader eyebrow="Job & worker module" title="Background jobs" description="Long research, backtest, data and signal evaluations run outside HTTP requests with bounded concurrency, progress, retry and cancellation." actions={<button type="button" onClick={() => void load()}><RefreshCw size={15} />Refresh</button>} />
    {error && <ErrorState message={error} retry={() => void load()} />}{!data ? !error && <LoadingState label="Loading worker queue" /> : <><section className="quant-kpi-grid"><article><span>Worker</span><strong>{data.worker.status}</strong><small>{data.worker.maximumWorkers} controlled workers</small></article><article><span>Running</span><strong>{data.worker.running}</strong><small>Currently executing</small></article><article><span>Queued</span><strong>{data.worker.queueDepth}</strong><small>Bounded pending work</small></article><article><span>Recorded jobs</span><strong>{data.count}</strong><small>Recent operational history</small></article></section><section className="quant-panel">{!data.rows.length ? <EmptyState title="No jobs yet" description="Start a bounded Research Lab experiment to populate the queue." /> : <div className="quant-table-scroll"><table className="quant-table"><thead><tr><th>Job</th><th>Status</th><th>Progress</th><th>Attempts</th><th>Updated</th><th>Action</th></tr></thead><tbody>{data.rows.map((job) => <tr key={job.jobId}><td><strong>{job.jobType.replaceAll("_", " ")}</strong><small className="mono">{job.jobId}</small></td><td><StatusBadge tone={tone(job.status)}>{job.status}</StatusBadge>{job.error && <small>{job.error.code}: {job.error.message}</small>}</td><td><div className="quant-progress"><span style={{ width: `${job.progress}%` }} /></div><small>{Math.round(job.progress)}%</small></td><td>{job.attempt} / {job.maximumAttempts}</td><td>{new Date(job.updatedAt).toLocaleString()}</td><td><button className="quant-icon-action" type="button" disabled={terminal.has(job.status) || busy === job.jobId} onClick={() => void cancel(job.jobId)}><Ban size={14} />Cancel</button></td></tr>)}</tbody></table></div>}</section></>}
  </main>;
}
