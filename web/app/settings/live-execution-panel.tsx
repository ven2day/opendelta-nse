"use client";

import { AlertTriangle, LockKeyhole, ShieldAlert } from "lucide-react";
import { useCallback, useMemo, useState, type FormEvent } from "react";
import { formatDateTime, shortId } from "../platform/format";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type { ExchangeConnectionsResponse, LiveDeployment, LiveExecutionStatus, LiveRiskPolicy } from "../platform/v2-types";
import { EmptyState, LoadingState, Message, Panel, RequestErrorState, StatusBadge } from "../platform/workspace-ui";

type Notice = { kind: "success" | "error"; text: string } | null;

const DEFAULT_POLICY = {
  name: "Conservative live policy", maxOrderValue: "10000", maxPositionValue: "25000",
  maxTotalExposure: "50000", maxOpenPositions: "5", maxDailyTrades: "10", maxDailyLoss: "5000",
  maxPriceDeviationPct: "1", maxSignalAgeSeconds: "120", maxCandleAgeSeconds: "600",
  symbolAllowlist: "", marketAllowlist: "NSE", strategyAllowlist: "", timeframeAllowlist: "5m",
};

export function LiveExecutionPanel() {
  const live = useV2Resource(useCallback(() => v2Get<LiveExecutionStatus>("live-execution/status"), []));
  const connections = useV2Resource(useCallback(() => v2Get<ExchangeConnectionsResponse>("connections"), []));
  const [policy, setPolicy] = useState(DEFAULT_POLICY);
  const [approvalId, setApprovalId] = useState("");
  const [riskPolicyId, setRiskPolicyId] = useState("");
  const [provider, setProvider] = useState<"DHAN" | "OKX" | "VALR">("DHAN");
  const [connectionId, setConnectionId] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [confirmations, setConfirmations] = useState<Record<string, string>>({});
  const [stop, setStop] = useState({ scopeType: "GLOBAL", scopeKey: "*", reason: "Operator emergency stop" });
  const selectedApproval = live.data?.eligiblePaperApprovals.find((item) => item.approvalId === approvalId);
  const eligibleConnections = useMemo(
    () => (connections.data?.connections ?? []).filter((item) => item.provider === provider),
    [connections.data, provider],
  );

  const mutate = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true); setNotice(null);
    try {
      await operation();
      setNotice({ kind: "success", text: success });
      await live.reload();
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "Live execution action failed") });
    } finally { setBusy(false); }
  };

  const createPolicy = async (event: FormEvent) => {
    event.preventDefault();
    const csv = (value: string) => value.split(",").map((item) => item.trim()).filter(Boolean);
    await mutate(() => v2Post<LiveRiskPolicy>("live-execution/risk-policies", {
      name: policy.name, maxOrderValue: policy.maxOrderValue, maxPositionValue: policy.maxPositionValue,
      maxTotalExposure: policy.maxTotalExposure, maxOpenPositions: Number(policy.maxOpenPositions),
      maxDailyTrades: Number(policy.maxDailyTrades), maxDailyLoss: policy.maxDailyLoss,
      maxPriceDeviationPct: policy.maxPriceDeviationPct, maxSignalAgeSeconds: Number(policy.maxSignalAgeSeconds),
      maxCandleAgeSeconds: Number(policy.maxCandleAgeSeconds), symbolAllowlist: csv(policy.symbolAllowlist),
      marketAllowlist: csv(policy.marketAllowlist), strategyAllowlist: csv(policy.strategyAllowlist),
      timeframeAllowlist: csv(policy.timeframeAllowlist),
    }), "Risk policy saved. It grants no live authority on its own.");
  };

  const createDeployment = async (event: FormEvent) => {
    event.preventDefault();
    await mutate(() => v2Post<LiveDeployment>("live-execution/deployments", {
      approvalId, riskPolicyId, provider, connectionId: provider === "DHAN" ? null : connectionId,
    }), "Draft live deployment pinned. It remains inactive until every server-side gate passes.");
  };

  const gateTone = live.data?.liveTradingEnabled && live.data.deploymentPermission && live.data.environmentAllowed ? "warn" : "bad";
  return <Panel icon={<LockKeyhole size={17} />} title="Live execution foundation" description="Pinned approvals, risk gates, auditable intents, reconciliation and emergency stops. No order can bypass the backend gate chain." aside={<StatusBadge tone={gateTone}>{live.data?.defaultState ?? "Live trading disabled"}</StatusBadge>}>
    {live.loading ? <LoadingState label="Loading live execution safety state" />
      : live.error ? <RequestErrorState error={live.error} retry={live.reload} />
        : <div className="quant-panel-body live-execution-panel">
          <div className="live-safety-banner"><ShieldAlert size={20} /><div><strong>{live.data?.defaultState}</strong><span>Emergency stops block new orders only. Existing live orders are never cancelled without a separate explicit confirmation.</span></div></div>
          <dl className="quant-facts">
            <div><dt>Global flag</dt><dd>{live.data?.liveTradingEnabled ? "Enabled" : "Disabled"}</dd></div>
            <div><dt>Deployment permission</dt><dd>{live.data?.deploymentPermission ? "Present" : "Absent"}</dd></div>
            <div><dt>Environment</dt><dd>{live.data?.deploymentEnvironment} · {live.data?.environmentAllowed ? "allowed" : "blocked"}</dd></div>
            <div><dt>Emergency stops</dt><dd>{live.data?.emergencyStops.filter((item) => item.active).length ?? 0} active</dd></div>
          </dl>
          {notice && <Message kind={notice.kind}>{notice.text}</Message>}
          <details className="quant-secondary-disclosure"><summary><span>Risk policy</span><small>Required server-side limits and allowlists</small></summary>
            <form className="quant-form-grid live-policy-form" onSubmit={(event) => void createPolicy(event)}>
              {Object.entries(policy).map(([key, value]) => <label key={key}><span>{key.replaceAll(/([A-Z])/g, " $1")}</span><input value={value} onChange={(event) => setPolicy((current) => ({ ...current, [key]: event.target.value }))} /></label>)}
              <div className="quant-form-actions span-2"><button className="primary" disabled={busy || !policy.symbolAllowlist || !policy.strategyAllowlist} type="submit">Save risk policy</button></div>
            </form>
          </details>
          <details className="quant-secondary-disclosure"><summary><span>Draft deployment</span><small>Paper approval and immutable pins required</small></summary>
            <form className="quant-form-grid" onSubmit={(event) => void createDeployment(event)}>
              <label className="span-2"><span>Paper approval</span><select value={approvalId} onChange={(event) => { const id = event.target.value; setApprovalId(id); const item = live.data?.eligiblePaperApprovals.find((candidate) => candidate.approvalId === id); if (item) setProvider(item.market === "NSE" ? "DHAN" : "OKX"); }}><option value="">Select exact approved run</option>{live.data?.eligiblePaperApprovals.map((item) => <option key={item.approvalId} value={item.approvalId}>{item.strategyId} v{item.strategyVersion} · {item.market} · {shortId(item.runId)}</option>)}</select></label>
              <label><span>Risk policy</span><select value={riskPolicyId} onChange={(event) => setRiskPolicyId(event.target.value)}><option value="">Select policy</option>{live.data?.riskPolicies.map((item) => <option key={item.riskPolicyId} value={item.riskPolicyId}>{item.name}</option>)}</select></label>
              <label><span>Provider</span><select value={provider} onChange={(event) => { setProvider(event.target.value as typeof provider); setConnectionId(""); }}><option>DHAN</option><option>OKX</option><option>VALR</option></select></label>
              {provider !== "DHAN" && <label><span>Encrypted connection</span><select value={connectionId} onChange={(event) => setConnectionId(event.target.value)}><option value="">Select connection</option>{eligibleConnections.map((item) => <option key={item.connectionId} value={item.connectionId}>{item.label} · {item.status}</option>)}</select></label>}
              <div className="quant-form-actions span-2"><button className="primary" type="submit" disabled={busy || !selectedApproval || !riskPolicyId || (provider !== "DHAN" && !connectionId)}>Create inactive draft</button><span>Creating a draft never submits an order.</span></div>
            </form>
          </details>
          <section><h3 className="quant-subheading">Pinned deployments</h3>{!live.data?.deployments.length ? <EmptyState title="No live deployments" description="Paper trading remains the active execution path." /> : <div className="live-deployment-list">{live.data.deployments.map((item) => <article key={item.liveDeploymentId}><header><span><strong>{item.strategyId} · v{item.strategyVersion}</strong><small>{item.provider} · {item.market} · {item.timeframe} · {item.status}</small></span><StatusBadge tone={item.status === "ACTIVE" ? "bad" : "warn"}>{item.status}</StatusBadge></header><div className="quant-toolbar"><label><span>Confirmation</span><input value={confirmations[item.liveDeploymentId] ?? ""} placeholder={`ENABLE LIVE ${item.strategyId}`} onChange={(event) => setConfirmations((current) => ({ ...current, [item.liveDeploymentId]: event.target.value }))} /></label><button disabled={busy || item.status === "ACTIVE"} onClick={() => void mutate(() => v2Post(`live-execution/deployments/${item.liveDeploymentId}/activate`, { confirmation: confirmations[item.liveDeploymentId] ?? "" }), "Live deployment activated after all backend gates passed.")}>Activate</button><button className="danger" disabled={busy || item.status !== "ACTIVE"} onClick={() => void mutate(() => v2Post(`live-execution/deployments/${item.liveDeploymentId}/disable`, { confirmation: "DISABLE LIVE" }), "Live deployment disabled.")}>Disable</button></div></article>)}</div>}</section>
          <details className="quant-secondary-disclosure"><summary><span><AlertTriangle size={15} />Emergency stop</span><small>Global, provider, market or strategy scope</small></summary><form className="quant-form-grid" onSubmit={(event) => { event.preventDefault(); void mutate(() => v2Post("live-execution/emergency-stops", { ...stop, active: true, confirmation: "ACTIVATE EMERGENCY STOP" }), "Emergency stop activated. New live orders are blocked immediately."); }}><label><span>Scope</span><select value={stop.scopeType} onChange={(event) => setStop((current) => ({ ...current, scopeType: event.target.value, scopeKey: event.target.value === "GLOBAL" ? "*" : "" }))}><option>GLOBAL</option><option>PROVIDER</option><option>MARKET</option><option>STRATEGY</option></select></label><label><span>Scope key</span><input value={stop.scopeKey} onChange={(event) => setStop((current) => ({ ...current, scopeKey: event.target.value }))} /></label><label className="span-2"><span>Reason</span><input value={stop.reason} onChange={(event) => setStop((current) => ({ ...current, reason: event.target.value }))} /></label><div className="quant-form-actions span-2"><button type="submit" className="danger" disabled={busy || !stop.scopeKey || !stop.reason}>Activate emergency stop</button></div></form></details>
          <section><h3 className="quant-subheading">Live intent ledger</h3>{!live.data?.intents.length ? <EmptyState title="No live order intents" description="Blocked attempts and provider submissions will appear here; none are hidden." /> : <div className="quant-table-scroll"><table className="quant-table"><thead><tr><th>Created</th><th>Strategy</th><th>Symbol</th><th>Provider</th><th>State</th><th>Reconciliation</th></tr></thead><tbody>{live.data.intents.map((item) => <tr key={item.intentId}><td>{formatDateTime(item.createdAt, item.market)}</td><td>{item.strategyId} v{item.strategyVersion}</td><td>{item.symbol}</td><td>{item.provider}</td><td><StatusBadge tone={item.state === "FILLED" ? "good" : item.state === "BLOCKED" || item.state === "REJECTED" ? "bad" : "warn"}>{item.state}</StatusBadge></td><td>{item.reconciliationStatus}</td></tr>)}</tbody></table></div>}</section>
        </div>}
  </Panel>;
}
