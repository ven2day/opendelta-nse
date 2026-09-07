"use client";

import { KeyRound, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { useCallback, useState, type FormEvent } from "react";
import { formatDateTime } from "../platform/format";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type { ExchangeConnection, ExchangeConnectionsResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, Message, Panel, RequestErrorState, StatusBadge } from "../platform/workspace-ui";

type Provider = "OKX" | "VALR";
type Notice = { kind: "success" | "error"; text: string } | null;

const EMPTY_SECRET = { apiKey: "", apiSecret: "", passphrase: "" };

export function ExchangeConnectionsPanel() {
  const connections = useV2Resource(useCallback(
    () => v2Get<ExchangeConnectionsResponse>("connections"),
    [],
  ));
  const [provider, setProvider] = useState<Provider>("OKX");
  const [label, setLabel] = useState("OpenDelta account");
  const [environment, setEnvironment] = useState<"LIVE" | "DEMO">("LIVE");
  const [secret, setSecret] = useState(EMPTY_SECRET);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);

  const mutate = async (key: string, operation: () => Promise<unknown>, success: string) => {
    setBusy(key); setNotice(null);
    try {
      await operation();
      setNotice({ kind: "success", text: success });
      await connections.reload();
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "Connection action failed") });
    } finally { setBusy(null); }
  };

  const add = async (event: FormEvent) => {
    event.preventDefault();
    await mutate("add", () => v2Post("connections", {
      provider, label, environment: provider === "VALR" ? "LIVE" : environment,
      apiKey: secret.apiKey, apiSecret: secret.apiSecret,
      passphrase: provider === "OKX" ? secret.passphrase : undefined,
    }), `${provider} credentials encrypted. Test the read-only connection before relying on it.`);
    setSecret(EMPTY_SECRET);
  };

  return <Panel icon={<KeyRound size={17} />} title="API connections" description="Add, test, replace or remove OKX and VALR API keys. Credentials are encrypted on the backend and are used only for read-only account access; OpenDelta executes paper trades only." aside={<StatusBadge tone="good">Paper only</StatusBadge>}>
    {connections.loading ? <LoadingState label="Loading exchange connection status" />
      : connections.error ? <RequestErrorState error={connections.error} retry={connections.reload} />
        : <div className="quant-panel-body exchange-connections-panel">
          <div className="connection-safety-strip">
            <StatusBadge tone={connections.data?.encryptionConfigured ? "good" : "warn"}>{connections.data?.encryptionConfigured ? "Encryption ready" : "Master key required"}</StatusBadge>
            <span>Use read-only keys. Keys with trade or withdrawal permission are rejected and held disabled.</span>
          </div>
          <div className="connection-platform-grid">
            {connections.data?.platformConnections.map((item) => <article key={item.provider}><strong>{item.provider}</strong><StatusBadge tone={item.configured ? "good" : "warn"}>{item.status.replace("_", " ")}</StatusBadge><small>{item.message}</small></article>)}
            {Object.entries(connections.data?.publicMarketData ?? {}).map(([name, item]) => <article key={name}><strong>{name} data</strong><StatusBadge tone={item.available ? "good" : "warn"}>{item.available ? "Available" : "Unavailable"}</StatusBadge><small>{item.requiresPrivateConnection ? "Managed by the Dhan collector" : "Public data needs no private key"}</small></article>)}
          </div>
          <details className="quant-secondary-disclosure connection-add"><summary><span><KeyRound size={15} />Add encrypted connection</span><small>Credentials are write-only and cleared after submission</small></summary>
            <form className="quant-form-grid" onSubmit={(event) => void add(event)}>
              <label><span>Provider</span><select value={provider} onChange={(event) => { const next = event.target.value as Provider; setProvider(next); if (next === "VALR") setEnvironment("LIVE"); }}><option>OKX</option><option>VALR</option></select></label>
              <label><span>Label</span><input maxLength={120} value={label} onChange={(event) => setLabel(event.target.value)} /></label>
              <label><span>Environment</span><select value={provider === "VALR" ? "LIVE" : environment} disabled={provider === "VALR"} onChange={(event) => setEnvironment(event.target.value as "LIVE" | "DEMO")}><option>LIVE</option>{provider === "OKX" && <option>DEMO</option>}</select></label>
              <label><span>API key</span><input aria-label="Exchange API key" type="password" autoComplete="off" value={secret.apiKey} onChange={(event) => setSecret((current) => ({ ...current, apiKey: event.target.value }))} /></label>
              <label><span>API secret</span><input aria-label="Exchange API secret" type="password" autoComplete="new-password" value={secret.apiSecret} onChange={(event) => setSecret((current) => ({ ...current, apiSecret: event.target.value }))} /></label>
              {provider === "OKX" && <label><span>Passphrase</span><input aria-label="OKX passphrase" type="password" autoComplete="new-password" value={secret.passphrase} onChange={(event) => setSecret((current) => ({ ...current, passphrase: event.target.value }))} /></label>}
              <div className="quant-form-actions"><button className="primary" type="submit" disabled={busy !== null || !connections.data?.encryptionConfigured || !label.trim() || !secret.apiKey || !secret.apiSecret || (provider === "OKX" && !secret.passphrase)}>Encrypt and save</button></div>
            </form>
          </details>
          {notice && <Message kind={notice.kind}>{notice.text}</Message>}
          {!connections.data?.connections.length ? <EmptyState title="No private exchange connections" description="OKX and VALR public data continue working without private credentials." />
            : <div className="connection-list">{connections.data.connections.map((item) => <ConnectionRow key={item.connectionId} connection={item} busy={busy} mutate={mutate} />)}</div>}
        </div>}
  </Panel>;
}

function ConnectionRow({ connection, busy, mutate }: {
  connection: ExchangeConnection;
  busy: string | null;
  mutate: (key: string, operation: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [replace, setReplace] = useState(EMPTY_SECRET);
  const [replaceLabel, setReplaceLabel] = useState(connection.label);
  const [replaceConfirmation, setReplaceConfirmation] = useState("");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const key = connection.connectionId;
  const tone = connection.status === "CONNECTED" ? "good" : "warn";
  const permission = connection.permissions;
  return <article className="connection-card">
    <header><span><strong>{connection.provider} · {connection.label}</strong><small>{connection.maskedKeyIdentifier} · {connection.environment}</small></span><StatusBadge tone={tone}>{connection.status.replaceAll("_", " ")}</StatusBadge></header>
    <dl className="quant-facts">
      <div><dt>Read</dt><dd>{permission.read === undefined ? "Not tested" : permission.read ? "Allowed" : "Missing"}</dd></div>
      <div><dt>Trade permission</dt><dd>{permission.trade === undefined ? "Not tested" : permission.trade ? "Rejected — remove it" : "None (safe)"}</dd></div>
      <div><dt>Withdrawal</dt><dd>{permission.withdrawal == null ? "Unknown" : permission.withdrawal ? "Detected — disabled" : "Not detected"}</dd></div>
      <div><dt>IP restriction</dt><dd>{permission.ipAllowlisted == null ? "Unavailable" : permission.ipAllowlisted ? "Detected" : "Not detected"}</dd></div>
      <div><dt>Last test</dt><dd>{connection.lastTestedAt ? formatDateTime(connection.lastTestedAt, "CRYPTO") : "Never"}</dd></div>
      <div><dt>Updated</dt><dd>{formatDateTime(connection.updatedAt, "CRYPTO")}</dd></div>
    </dl>
    {connection.lastTestMessage && <small>{connection.lastTestMessage}</small>}
    <div className="quant-form-actions">
      <button type="button" disabled={busy !== null} onClick={() => void mutate(`${key}:test`, () => v2Post(`connections/${key}/test`, {}), `${connection.provider} read-only connection test completed.`)}><ShieldCheck size={14} />Test connection</button>
      <button type="button" disabled={busy !== null || (!connection.disabled && (connection.permissions.withdrawal === true || connection.permissions.trade === true))} onClick={() => void mutate(`${key}:disable`, () => v2Post(`connections/${key}/disable`, { disabled: !connection.disabled }), connection.disabled ? "Read-only connection enabled for account access." : "Connection disabled.")}>{connection.disabled ? "Enable read access" : "Disable"}</button>
      <button type="button" disabled={busy !== null} onClick={() => void mutate(`${key}:rotate`, () => v2Post(`connections/${key}/rotate`, { confirmation: "ROTATE" }), "Encrypted material rotated with a fresh data key and nonces.")}><RefreshCw size={14} />Rotate encryption</button>
    </div>
    <details className="quant-secondary-disclosure"><summary><span>Replace credentials</span><small>Type REPLACE {connection.provider}</small></summary><div className="quant-form-grid">
      <label><span>Label</span><input value={replaceLabel} onChange={(event) => setReplaceLabel(event.target.value)} /></label>
      <label><span>New API key</span><input type="password" autoComplete="off" value={replace.apiKey} onChange={(event) => setReplace((current) => ({ ...current, apiKey: event.target.value }))} /></label>
      <label><span>New API secret</span><input type="password" autoComplete="new-password" value={replace.apiSecret} onChange={(event) => setReplace((current) => ({ ...current, apiSecret: event.target.value }))} /></label>
      {connection.provider === "OKX" && <label><span>New passphrase</span><input type="password" autoComplete="new-password" value={replace.passphrase} onChange={(event) => setReplace((current) => ({ ...current, passphrase: event.target.value }))} /></label>}
      <label><span>Confirmation</span><input value={replaceConfirmation} placeholder={`REPLACE ${connection.provider}`} onChange={(event) => setReplaceConfirmation(event.target.value)} /></label>
      <div className="quant-form-actions"><button type="button" disabled={busy !== null || replaceConfirmation !== `REPLACE ${connection.provider}` || !replace.apiKey || !replace.apiSecret || (connection.provider === "OKX" && !replace.passphrase)} onClick={() => void mutate(`${key}:replace`, () => v2Post(`connections/${key}/replace`, { provider: connection.provider, label: replaceLabel, environment: connection.environment, apiKey: replace.apiKey, apiSecret: replace.apiSecret, passphrase: connection.provider === "OKX" ? replace.passphrase : undefined, confirmation: replaceConfirmation }), "Credentials replaced and held untested until a new connection test succeeds.")}><RefreshCw size={14} />Replace credentials</button></div>
    </div></details>
    <details className="quant-secondary-disclosure danger-disclosure"><summary><span><Trash2 size={14} />Delete encrypted connection</span><small>Does not affect public market data</small></summary><div className="quant-form-grid"><label><span>Type DELETE {connection.provider}</span><input value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} /></label><div className="quant-form-actions"><button type="button" disabled={busy !== null || deleteConfirmation !== `DELETE ${connection.provider}`} onClick={() => void mutate(`${key}:delete`, () => v2Post(`connections/${key}/delete`, { confirmation: deleteConfirmation }), "Encrypted connection deleted; its secret-free audit event remains.")}><Trash2 size={14} />Delete connection</button></div></div></details>
  </article>;
}
