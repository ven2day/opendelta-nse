"use client";

import { Archive, Beaker, Code2, Copy, RotateCcw, Save } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { AICopilotPanel } from "../ai/ai-copilot-panel";
import { formatDateTime, marketLabel } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type { IndicatorPreview, IndicatorSource, IndicatorSourcesResponse, IndicatorSourceTemplate, IndicatorSourceValidation } from "../platform/v2-types";
import { EmptyState, LoadingState, Message, Panel, RequestErrorState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";
import styles from "./indicator-studio.module.css";

type Notice = { kind: "success" | "error"; text: string } | null;

function nextPatch(source: string): string {
  return source.replace(/("version"\s*:\s*")(\d+)\.(\d+)\.(\d+)(")/, (_match, before, major, minor, patch, after) => `${before}${major}.${minor}.${Number(patch) + 1}${after}`);
}

function parseParameters(text: string): Record<string, string | number | boolean> {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Parameters must be a JSON object.");
  for (const item of Object.values(value)) {
    if (!["string", "number", "boolean"].includes(typeof item)) throw new Error("Parameters support string, number and boolean values only.");
  }
  return value as Record<string, string | number | boolean>;
}

export function IndicatorStudioWorkspace({ initialMarket }: { initialMarket: PlatformMarket }) {
  const market = initialMarket;
  const [sourceCode, setSourceCode] = useState("");
  const [validation, setValidation] = useState<IndicatorSourceValidation | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [busy, setBusy] = useState<"validate" | "save" | "load" | "archive" | "preview" | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [symbol, setSymbol] = useState(market === "NSE" ? "RELIANCE" : "BTC-USDT");
  const [timeframe, setTimeframe] = useState("5m");
  const [parameters, setParameters] = useState("{}");
  const [preview, setPreview] = useState<IndicatorPreview | null>(null);

  const loadTemplate = useCallback(() => v2Get<IndicatorSourceTemplate>("indicator-studio/template"), []);
  const template = useV2Resource(loadTemplate);
  const loadSources = useCallback(() => v2Get<IndicatorSourcesResponse>("indicator-studio/sources"), []);
  const sources = useV2Resource(loadSources);
  const currentSource = sourceCode || template.data?.sourceCode || "";
  const selected = sources.data?.sources.find((item) => item.sourceId === selectedId) ?? null;
  const previewRows = useMemo(() => {
    if (!preview) return [];
    return preview.rows.map((values, index) => ({ values, index })).slice(-40).reverse();
  }, [preview]);

  const editSource = async (item: IndicatorSource) => {
    setBusy("load"); setNotice(null); setPreview(null);
    try {
      const loaded = await v2Get<IndicatorSource>(`indicator-studio/sources/${item.sourceId}`);
      setSourceCode(nextPatch(loaded.sourceCode ?? ""));
      setParameters(JSON.stringify(loaded.manifest.parameters ?? {}, null, 2));
      setSelectedId(item.sourceId);
      setValidation(null);
      setNotice({ kind: "success", text: `Loaded ${loaded.name} as a new draft. Its version was advanced; the saved source remains unchanged.` });
    } catch (reason) { setNotice({ kind: "error", text: errorMessage(reason, "The indicator source could not be loaded") }); }
    finally { setBusy(null); }
  };

  const validate = async () => {
    setBusy("validate"); setNotice(null);
    try {
      const result = await v2Post<IndicatorSourceValidation>("indicator-studio/validate", { sourceCode: currentSource });
      setValidation(result);
      setNotice({ kind: result.valid ? "success" : "error", text: result.valid ? `${result.manifest?.name} v${result.manifest?.version} is safe to save.` : result.errors.join(" ") });
    } catch (reason) { setNotice({ kind: "error", text: errorMessage(reason, "Validation failed") }); }
    finally { setBusy(null); }
  };

  const save = async () => {
    setBusy("save"); setNotice(null);
    try {
      const saved = await v2Post<IndicatorSource>("indicator-studio/sources", { sourceCode: currentSource });
      setSelectedId(saved.sourceId); setValidation(saved.validation);
      setParameters(JSON.stringify(saved.manifest.parameters ?? {}, null, 2));
      setNotice({ kind: "success", text: `Saved immutable ${saved.name} v${saved.indicatorVersion}. It is not attached to a strategy automatically.` });
      await sources.refresh();
    } catch (reason) { setNotice({ kind: "error", text: errorMessage(reason, "The indicator could not be saved") }); }
    finally { setBusy(null); }
  };

  const archive = async (item: IndicatorSource) => {
    if (!window.confirm(`Archive ${item.name} v${item.indicatorVersion}? Existing history will remain available.`)) return;
    setBusy("archive"); setNotice(null);
    try {
      await v2Post(`indicator-studio/sources/${item.sourceId}/archive`);
      if (selectedId === item.sourceId) setPreview(null);
      setNotice({ kind: "success", text: `${item.name} v${item.indicatorVersion} was archived.` });
      await sources.refresh();
    } catch (reason) { setNotice({ kind: "error", text: errorMessage(reason, "The indicator could not be archived") }); }
    finally { setBusy(null); }
  };

  const runPreview = async () => {
    if (!selected) return;
    setBusy("preview"); setNotice(null); setPreview(null);
    try {
      const result = await v2Post<IndicatorPreview>(`indicator-studio/sources/${selected.sourceId}/preview`, { market, symbol: symbol.trim().toUpperCase(), timeframe, params: parseParameters(parameters) });
      setPreview(result);
      setNotice({ kind: "success", text: `Calculated ${result.rows.length} completed candles using the isolated runner.` });
    } catch (reason) { setNotice({ kind: "error", text: errorMessage(reason, "Preview failed") }); }
    finally { setBusy(null); }
  };

  return <main className="quant-workspace">
    <WorkspaceHeader eyebrow={`${marketLabel(market)} research`} title="Indicator Studio V2" actions={<div className="quant-header-actions"><StatusBadge tone="good">Isolated Python</StatusBadge><StatusBadge>Immutable versions</StatusBadge></div>} />
    {notice && <Message kind={notice.kind}>{notice.text}</Message>}

    <Panel icon={<Code2 size={17} />} title="Python indicator editor" description="Validate and save a new immutable indicator version. Saving does not attach it to a strategy.">
      {template.loading ? <LoadingState label="Loading indicator template" /> : template.error ? <RequestErrorState error={template.error} retry={template.reload} /> : <div className="quant-panel-body">
        <textarea className={styles.editor} aria-label="Indicator V2 Python source" spellCheck={false} value={currentSource} disabled={busy !== null} onChange={(event) => { setSourceCode(event.target.value); setValidation(null); setNotice(null); }} />
        <div className={styles.actions}>
          <button type="button" onClick={() => navigator.clipboard.writeText(currentSource)}><Copy size={15} />Copy</button>
          <button type="button" disabled={busy !== null} onClick={() => { setSourceCode(template.data?.sourceCode ?? ""); setValidation(null); setSelectedId(null); setPreview(null); }}><RotateCcw size={15} />New template</button>
          <button type="button" disabled={busy !== null || !currentSource.trim()} onClick={() => void validate()}><Beaker size={15} />{busy === "validate" ? "Validating…" : "Validate"}</button>
          <button type="button" className="primary" disabled={busy !== null || !currentSource.trim() || validation?.valid === false} onClick={() => void save()}><Save size={15} />{busy === "save" ? "Saving…" : "Save new version"}</button>
        </div>
        {validation && <div className={styles.validation} data-valid={validation.valid}>{validation.errors.map((item) => <span key={item}>{item}</span>)}{validation.warnings.map((item) => <span key={item}>{item}</span>)}</div>}
      </div>}
    </Panel>

    <AICopilotPanel surface="indicator" market={market} onUseDraft={(content) => { setSourceCode(content); setValidation(null); setPreview(null); setNotice({ kind: "success", text: "AI draft loaded into the editor. Review it, then run normal V2 validation before saving." }); }} />

    <Panel icon={<Archive size={17} />} title="Version history" description="Saved versions never change. Load one to create its next draft, or archive it without deleting history.">
      {sources.loading ? <LoadingState label="Loading indicator versions" /> : sources.error ? <RequestErrorState error={sources.error} retry={sources.reload} /> : !sources.data?.sources.length ? <EmptyState title="No indicators saved" description="Validate the starter template and save version 1.0.0." /> : <div className={styles.history} role="table" aria-label="Indicator versions">
        <div className={styles.historyHeader} role="row"><span>Indicator</span><span>Version</span><span>Outputs</span><span>Created</span><span>Status</span><span>Actions</span></div>
        {sources.data.sources.map((item) => <div key={item.sourceId} className={`${styles.historyRow} ${selectedId === item.sourceId ? styles.selected : ""}`} role="row"><span><strong>{item.name}</strong><small>{item.indicatorId}</small></span><code>v{item.indicatorVersion}</code><span>{item.manifest.outputs.map((output) => output.label).join(", ")}</span><span>{formatDateTime(item.createdAt, market)}</span><StatusBadge tone={item.status === "VALIDATED" ? "good" : "warn"}>{item.status === "VALIDATED" ? "Validated" : "Archived"}</StatusBadge><span className={styles.rowActions}><button type="button" disabled={busy !== null} onClick={() => void editSource(item)}>Edit as new</button>{item.status === "VALIDATED" && <button type="button" disabled={busy !== null} onClick={() => void archive(item)}>Archive</button>}</span></div>)}
      </div>}
    </Panel>

    <Panel icon={<Beaker size={17} />} title="Stored-candle preview" description="Run a saved validated version against recent completed candles. Preview cannot create signals or trades.">
      {!selected ? <EmptyState title="Select a saved version" description="Use Edit as new in version history or save a version to select it." /> : <div className="quant-panel-body">
        <div className={styles.previewControls}><label><span>Indicator</span><input readOnly value={`${selected.name} · v${selected.indicatorVersion}`} /></label><label><span>Symbol</span><input value={symbol} onChange={(event) => setSymbol(event.target.value)} /></label><label><span>Timeframe</span><select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>{["5m", "15m", "30m", "1h", "4h", "1d"].map((item) => <option key={item}>{item}</option>)}</select></label><button type="button" className="primary" disabled={busy !== null || selected.status !== "VALIDATED" || !symbol.trim()} onClick={() => void runPreview()}>{busy === "preview" ? "Calculating…" : "Run preview"}</button></div>
        <details className="quant-config-disclosure"><summary><span>Parameter overrides</span><small>JSON · defaults loaded from this version</small></summary><textarea className="quant-json-editor" aria-label="Indicator parameter overrides JSON" value={parameters} onChange={(event) => setParameters(event.target.value)} /></details>
        {preview && <div className={styles.previewTable}><table><thead><tr><th>Completed candle</th><th>Close</th>{preview.outputs.map((output) => <th key={output.name}>{output.label}</th>)}</tr></thead><tbody>{previewRows.map(({ values, index }) => <tr key={`${preview.candles.timestamp[index]}-${index}`}><td>{formatDateTime(preview.candles.timestamp[index], market)}</td><td>{preview.candles.close[index]?.toFixed(4)}</td>{preview.outputs.map((output) => <td key={output.name}>{values[output.name] == null ? "—" : Number(values[output.name]).toFixed(4)}</td>)}</tr>)}</tbody></table></div>}
      </div>}
    </Panel>
  </main>;
}
