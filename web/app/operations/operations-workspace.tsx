"use client";

import { Activity, AlertTriangle, BellRing, Database, RefreshCw, ScrollText, Server, ShieldAlert } from "lucide-react";
import { useCallback, useState } from "react";
import { formatDateTime, humanize, tone } from "../platform/format";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import { EmptyState, LoadingState, Message, Panel, RequestErrorState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

type Lease = {
  leaseId: string; workerType: string; taskKey: string; workerIdentity: string; hostIdentity: string;
  processIdentity: string; ownerFingerprint: string; heartbeatAt: string; expiresAt: string; status: string; lastError?: string | null;
};
type Alert = {
  alertId: string; alertType: string; severity: string; source: string; title: string; message: string;
  status: string; occurrenceCount: number; lastSeenAt: string; acknowledgedBy?: string | null; resolution?: string | null;
};
type Audit = {
  auditId: string; requestId: string; action: string; actorType: string; actorId: string; subjectType?: string | null;
  subjectId?: string | null; success: boolean; details: Record<string, unknown>; createdAt: string;
};
type OperationsHealth = {
  overall: string; generatedAt: string;
  marketData: Record<string, { dataFreshness?: { status?: string; reason?: string; ageSeconds?: number } }>;
  workerLeases: Lease[];
  queues: Record<string, { pending?: number; limit?: number; queued?: number; running?: number; active?: number }>;
  strategyRunner: { available?: boolean; networkless?: boolean; transport?: string };
  exchangeConnections: { connections?: Array<{ connectionId: string; provider: string; status: string; lastTestSuccess?: boolean | null }>; dhan?: { configured?: boolean; status?: string } };
  liveExecution: { defaultState?: string; intents?: Array<{ state: string; reconciliationStatus?: string }>; emergencyStops?: Array<{ stopId: string; scopeType: string; reason: string; active: boolean; activatedAt: string }> };
  activeAlerts: Alert[];
};

const REFRESH_MS = 15_000;
const displayTime = (value: string) => formatDateTime(value, "CRYPTO");
const badgeTone = (value: string): "good" | "warn" | "bad" | "neutral" => tone(value);

export function OperationsWorkspace() {
  const load = useCallback(async () => {
    const [health, audit] = await Promise.all([
      v2Get<OperationsHealth>("operations/health"),
      v2Get<{ items: Audit[] }>("operations/audit", { limit: 100 }),
    ]);
    return { health, audit: audit.items };
  }, []);
  const { data, error, loading, reload, refresh } = useV2Resource(load, REFRESH_MS);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);
  const [resolutions, setResolutions] = useState<Record<string, string>>({});

  const alertAction = async (alert: Alert, action: "acknowledge" | "resolve") => {
    setBusy(alert.alertId + action);
    setMessage(null);
    try {
      await v2Post(`operations/alerts/${alert.alertId}/${action}`, {
        actor: "platform-user",
        resolution: action === "resolve" ? resolutions[alert.alertId] : null,
      });
      setMessage({ kind: "success", text: action === "resolve" ? "Alert resolved." : "Alert acknowledged." });
      refresh();
    } catch (failure) {
      setMessage({ kind: "error", text: errorMessage(failure, "Alert update failed") });
    } finally {
      setBusy(null);
    }
  };

  return <main className="quant-workspace quant-operations-workspace">
    <WorkspaceHeader eyebrow="Production observability" title="Operations" description="Durable health, lease ownership, alerts, reconciliation and secret-free audit history." actions={<button type="button" onClick={refresh}><RefreshCw size={15} />Refresh</button>} />
    {loading ? <LoadingState label="Loading operational health" /> : error ? <RequestErrorState error={error} retry={reload} /> : data && <>
      <section className="quant-overview-strip" aria-label="Operational health summary">
        <div><span>Overall</span><strong><StatusBadge tone={badgeTone(data.health.overall)}>{humanize(data.health.overall)}</StatusBadge></strong><small>{displayTime(data.health.generatedAt)}</small></div>
        {Object.entries(data.health.marketData).map(([market, value]) => <div key={market}><span>{market} data</span><strong>{humanize(value.dataFreshness?.status ?? "unavailable")}</strong><small>{humanize(value.dataFreshness?.reason ?? "not reported")}</small></div>)}
        <div><span>Strategy runner</span><strong>{data.health.strategyRunner.available ? "Available" : "Unavailable"}</strong><small>{data.health.strategyRunner.networkless ? "Isolated · networkless" : "Status unavailable"}</small></div>
        <div><span>Live execution</span><strong>{data.health.liveExecution.defaultState ?? "Unavailable"}</strong><small>{data.health.liveExecution.intents?.filter((item) => item.reconciliationStatus === "REQUIRED").length ?? 0} need reconciliation</small></div>
      </section>

      {message && <Message kind={message.kind}>{message.text}</Message>}

      <div className="quant-operations-grid">
        <Panel icon={<Server size={18} />} title="Worker leases" description="Database CAS ownership and expiry; tokens are shown only as fingerprints.">
          {data.health.workerLeases.length ? <div className="quant-table-scroll tall"><table className="quant-table"><thead><tr><th>Worker</th><th>Task</th><th>Owner</th><th>Status</th><th>Heartbeat</th><th>Expiry</th></tr></thead><tbody>{data.health.workerLeases.map((lease) => <tr key={lease.leaseId}><td><strong>{humanize(lease.workerType)}</strong><small>{lease.workerIdentity}</small></td><td>{lease.taskKey}</td><td>{lease.hostIdentity}:{lease.processIdentity}<small>{lease.ownerFingerprint}</small></td><td><StatusBadge tone={badgeTone(lease.status)}>{humanize(lease.status)}</StatusBadge>{lease.lastError && <small>{lease.lastError}</small>}</td><td>{displayTime(lease.heartbeatAt)}</td><td>{displayTime(lease.expiresAt)}</td></tr>)}</tbody></table></div> : <EmptyState title="No lease history" description="Workers will appear after their first leased cycle." />}
        </Panel>

        <Panel icon={<Database size={18} />} title="Bounded queues" description="Backtest, research and walk-forward capacity remains shared and bounded.">
          <dl className="quant-facts">{Object.entries(data.health.queues).map(([name, queue]) => <div key={name}><dt>{humanize(name)}</dt><dd>{queue.pending ?? queue.active ?? ((queue.queued ?? 0) + (queue.running ?? 0))} active{queue.limit ? ` / ${queue.limit}` : ""}</dd></div>)}</dl>
        </Panel>

        <Panel icon={<Activity size={18} />} title="Exchange and reconciliation" description="Public data, private connectivity and live enablement remain separate.">
          <div className="quant-table-scroll"><table className="quant-table"><thead><tr><th>Provider</th><th>Private connection</th><th>Last test</th></tr></thead><tbody>
            <tr><td>Dhan</td><td>{data.health.exchangeConnections.dhan?.configured ? humanize(data.health.exchangeConnections.dhan.status ?? "configured") : "Not configured"}</td><td>Deployment managed</td></tr>
            {(data.health.exchangeConnections.connections ?? []).map((connection) => <tr key={connection.connectionId}><td>{connection.provider}</td><td><StatusBadge tone={badgeTone(connection.status)}>{humanize(connection.status)}</StatusBadge></td><td>{connection.lastTestSuccess === true ? "Passed" : connection.lastTestSuccess === false ? "Failed" : "Not tested"}</td></tr>)}
          </tbody></table></div>
        </Panel>

        <Panel icon={<ShieldAlert size={18} />} title="Emergency-stop state" description="Stops block new live intents; existing orders are not cancelled automatically.">
          {(data.health.liveExecution.emergencyStops ?? []).filter((item) => item.active).length ? <div className="quant-alert-list">{(data.health.liveExecution.emergencyStops ?? []).filter((item) => item.active).map((stop) => <article key={stop.stopId}><StatusBadge tone="bad">{humanize(stop.scopeType)}</StatusBadge><strong>{stop.reason}</strong><small>{displayTime(stop.activatedAt)}</small></article>)}</div> : <EmptyState title="No active emergency stop" description="All configured scopes are clear; all other live-execution gates still apply." />}
        </Panel>
      </div>

      <Panel icon={<BellRing size={18} />} title="Active alerts" description="Occurrences are deduplicated with cooldowns; acknowledgement does not resolve the condition.">
        {data.health.activeAlerts.length ? <div className="quant-table-scroll tall"><table className="quant-table"><thead><tr><th>Alert</th><th>Severity</th><th>Status</th><th>Last seen</th><th>Occurrences</th><th>Operator action</th></tr></thead><tbody>{data.health.activeAlerts.map((alert) => <tr key={alert.alertId}><td><strong>{alert.title}</strong><small>{alert.message} · {alert.source}</small></td><td><StatusBadge tone={alert.severity === "CRITICAL" ? "bad" : "warn"}>{humanize(alert.severity)}</StatusBadge></td><td>{humanize(alert.status)}</td><td>{displayTime(alert.lastSeenAt)}</td><td>{alert.occurrenceCount}</td><td><div className="quant-row-actions">{alert.status === "OPEN" && <button type="button" disabled={busy !== null} onClick={() => void alertAction(alert, "acknowledge")}>Acknowledge</button>}<input aria-label={`Resolution for ${alert.title}`} value={resolutions[alert.alertId] ?? ""} onChange={(event) => setResolutions((current) => ({ ...current, [alert.alertId]: event.target.value }))} placeholder="Resolution note" /><button type="button" disabled={busy !== null || !(resolutions[alert.alertId] ?? "").trim()} onClick={() => void alertAction(alert, "resolve")}>Resolve</button></div></td></tr>)}</tbody></table></div> : <EmptyState title="No active alerts" description="Health collection has not found an active operational condition." />}
      </Panel>

      <Panel icon={<ScrollText size={18} />} title="Append-only audit history" description="Important actions record identity and outcome without request bodies, credentials or secrets.">
        {data.audit.length ? <div className="quant-table-scroll tall"><table className="quant-table"><thead><tr><th>Time</th><th>Action</th><th>Actor</th><th>Subject</th><th>Outcome</th><th>Request</th></tr></thead><tbody>{data.audit.map((event) => <tr key={event.auditId}><td>{displayTime(event.createdAt)}</td><td>{humanize(event.action)}</td><td>{event.actorType} · {event.actorId}</td><td>{event.subjectType ?? "—"}<small>{event.subjectId ?? "—"}</small></td><td><StatusBadge tone={event.success ? "good" : "bad"}>{event.success ? "Succeeded" : "Failed"}</StatusBadge></td><td>{event.requestId}</td></tr>)}</tbody></table></div> : <EmptyState title="No audited actions" description="Important mutations will appear here after the first action." />}
      </Panel>

      <p className="quant-inline-note"><AlertTriangle size={14} />Exact timestamps use UTC with year. Monitoring never exposes credentials and cannot enable live trading.</p>
    </>}
  </main>;
}
