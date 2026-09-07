"use client";

import { Bot, Copy, LoaderCircle, Save } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import type { PlatformMarket } from "../platform/platform-client";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type {
  AICopilotAction, AICopilotResponse, AICopilotStatus, AIResearchDraft,
  BacktestRunsResponse, BacktestTradesResponse, ResearchExperimentsResponse,
  StrategySourcesResponse, IndicatorSourcesResponse, WalkForwardValidationsResponse,
} from "../platform/v2-types";
import { LoadingState, Message, Panel, RequestErrorState, StatusBadge } from "../platform/workspace-ui";

type Surface = "strategy" | "indicator" | "research";
type ContextResources = {
  strategies: StrategySourcesResponse["sources"];
  indicators: IndicatorSourcesResponse["sources"];
  runs: BacktestRunsResponse["runs"];
  experiments: ResearchExperimentsResponse["experiments"];
  validations: WalkForwardValidationsResponse["validations"];
};

const ACTIONS: Record<Surface, Array<{ value: AICopilotAction; label: string }>> = {
  strategy: [
    { value: "EXPLAIN_STRATEGY", label: "Explain strategy" },
    { value: "SUGGEST_IMPROVEMENTS", label: "Suggest improvements" },
    { value: "DRAFT_STRATEGY", label: "Generate draft strategy" },
    { value: "DRAFT_CONFIGURATION", label: "Draft JSON configuration" },
    { value: "SUGGEST_EXPERIMENT", label: "Suggest experiment" },
  ],
  indicator: [
    { value: "EXPLAIN_INDICATOR", label: "Explain indicator" },
    { value: "SUGGEST_IMPROVEMENTS", label: "Suggest improvements" },
    { value: "DRAFT_INDICATOR", label: "Generate draft indicator" },
  ],
  research: [
    { value: "EXPLAIN_BACKTEST", label: "Explain backtest" },
    { value: "EXPLAIN_COMPARISON", label: "Explain comparison" },
    { value: "EXPLAIN_WALK_FORWARD", label: "Explain walk-forward validation" },
    { value: "SUGGEST_EXPERIMENT", label: "Suggest experiment" },
    { value: "DRAFT_CONFIGURATION", label: "Draft JSON configuration" },
  ],
};

export function AICopilotPanel({ surface, market, onUseDraft }: {
  surface: Surface; market: PlatformMarket; onUseDraft?: (content: string) => void;
}) {
  const status = useV2Resource(useCallback(() => v2Get<AICopilotStatus>("ai/copilot/status"), []));
  const resources = useV2Resource(useCallback(async (): Promise<ContextResources> => {
    const empty: ContextResources = { strategies: [], indicators: [], runs: [], experiments: [], validations: [] };
    if (surface === "strategy") {
      const strategies = await v2Get<StrategySourcesResponse>("strategy-studio/sources", { market });
      return { ...empty, strategies: strategies.sources };
    }
    if (surface === "indicator") {
      const indicators = await v2Get<IndicatorSourcesResponse>("indicator-studio/sources");
      return { ...empty, indicators: indicators.sources };
    }
    const [runs, experiments, validations] = await Promise.all([
      v2Get<BacktestRunsResponse>("backtests", { market, limit: 50 }),
      v2Get<ResearchExperimentsResponse>("research/experiments", { market, limit: 50 }),
      v2Get<WalkForwardValidationsResponse>("research/walk-forward", { market, limit: 50 }),
    ]);
    return { ...empty, runs: runs.runs, experiments: experiments.experiments, validations: validations.validations };
  }, [market, surface]));
  const [action, setAction] = useState<AICopilotAction>(ACTIONS[surface][0].value);
  const effectiveAction = ACTIONS[surface].some((item) => item.value === action) ? action : ACTIONS[surface][0].value;
  const [instruction, setInstruction] = useState("Explain the important assumptions, risks, and next research checks.");
  const [strategySourceId, setStrategySourceId] = useState("");
  const [indicatorSourceId, setIndicatorSourceId] = useState("");
  const [backtestRunId, setBacktestRunId] = useState("");
  const [experimentId, setExperimentId] = useState("");
  const [walkForwardValidationId, setWalkForwardValidationId] = useState("");
  const [tradeLotIds, setTradeLotIds] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [response, setResponse] = useState<AICopilotResponse | null>(null);
  const [saved, setSaved] = useState<AIResearchDraft | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const trades = useV2Resource(useCallback(
    () => backtestRunId
      ? v2Get<BacktestTradesResponse>(`backtests/${backtestRunId}/trades`, { limit: 20, sort: "exitTimestamp", direction: "desc" })
      : Promise.resolve(null),
    [backtestRunId],
  ));
  const contextCategories = useMemo(() => [
    strategySourceId && "Strategy source",
    indicatorSourceId && "Indicator source",
    backtestRunId && "Backtest summary",
    tradeLotIds.length && `${tradeLotIds.length} selected trade${tradeLotIds.length === 1 ? "" : "s"}`,
    experimentId && "Experiment comparison",
    walkForwardValidationId && "Walk-forward result",
  ].filter(Boolean), [backtestRunId, experimentId, indicatorSourceId, strategySourceId, tradeLotIds.length, walkForwardValidationId]);

  const request = async () => {
    setSubmitting(true); setNotice(null); setResponse(null); setSaved(null);
    try {
      const result = await v2Post<AICopilotResponse>("ai/copilot/requests", {
        action: effectiveAction, instruction,
        context: {
          strategySourceId: strategySourceId || undefined,
          indicatorSourceId: indicatorSourceId || undefined,
          backtestRunId: backtestRunId || undefined,
          selectedTradeLotIds: tradeLotIds,
          experimentId: experimentId || undefined,
          walkForwardValidationId: walkForwardValidationId || undefined,
        },
      });
      setResponse(result);
    } catch (reason) { setNotice(errorMessage(reason, "AI research request failed")); }
    finally { setSubmitting(false); }
  };
  const saveDraft = async () => {
    if (!response) return;
    setSaving(true); setNotice(null);
    try {
      setSaved(await v2Post<AIResearchDraft>("ai/copilot/drafts", {
        requestId: response.requestId, draftType: response.suggestedDraftType, content: response.content,
      }));
    } catch (reason) { setNotice(errorMessage(reason, "AI draft could not be saved")); }
    finally { setSaving(false); }
  };
  const toggleTrade = (lotId: string) => setTradeLotIds((current) => (
    current.includes(lotId) ? current.filter((item) => item !== lotId) : [...current, lotId].slice(0, 20)
  ));

  return <Panel icon={<Bot size={17} />} title="AI Research Copilot" description="Provider calls happen on the backend. Only context selected below is sent; every output remains an unvalidated research draft.">
    {status.loading || resources.loading ? <LoadingState label="Loading AI research controls" />
      : status.error ? <RequestErrorState error={status.error} retry={status.reload} />
        : resources.error ? <RequestErrorState error={resources.error} retry={resources.reload} />
          : <div className="quant-panel-body ai-copilot-panel">
            <div className="ai-copilot-status"><StatusBadge tone={status.data?.configured ? "good" : "warn"}>{status.data?.configured ? "Configured" : "Unavailable"}</StatusBadge><span>{status.data?.message}{status.data?.provider ? ` · ${status.data.provider} · ${status.data.model}` : ""}</span></div>
            <div className="quant-form-grid ai-copilot-grid">
              <label><span>Research action</span><select aria-label="AI research action" value={effectiveAction} onChange={(event) => setAction(event.target.value as AICopilotAction)}>{ACTIONS[surface].map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
              {surface === "strategy" && <ContextSelect label="Strategy source context" value={strategySourceId} setValue={setStrategySourceId} options={(resources.data?.strategies ?? []).map((item) => ({ id: item.sourceId, label: `${item.name} · v${item.strategyVersion} · ${item.status}` }))} />}
              {surface === "indicator" && <ContextSelect label="Indicator source context" value={indicatorSourceId} setValue={setIndicatorSourceId} options={(resources.data?.indicators ?? []).map((item) => ({ id: item.sourceId, label: `${item.name} · v${item.indicatorVersion} · ${item.status}` }))} />}
              {surface === "research" && <>
                <ContextSelect label="Backtest summary context" value={backtestRunId} setValue={(value) => { setBacktestRunId(value); setTradeLotIds([]); }} options={(resources.data?.runs ?? []).map((item) => ({ id: item.runId, label: `${item.strategyId} · ${item.timeframe} · ${item.status}` }))} />
                <ContextSelect label="Experiment comparison context" value={experimentId} setValue={setExperimentId} options={(resources.data?.experiments ?? []).map((item) => ({ id: item.experimentId, label: `${item.name} · ${item.variantCount} variants` }))} />
                <ContextSelect label="Walk-forward result context" value={walkForwardValidationId} setValue={setWalkForwardValidationId} options={(resources.data?.validations ?? []).map((item) => ({ id: item.validationId, label: `${item.name} · ${item.foldCount} folds · ${item.status}` }))} />
              </>}
              <label className="ai-copilot-instruction"><span>Research instruction</span><textarea maxLength={4000} value={instruction} onChange={(event) => setInstruction(event.target.value)} /></label>
            </div>
            {surface === "research" && backtestRunId && <details className="quant-details ai-trade-context"><summary>Selected trades · send none by default</summary>{trades.loading ? <LoadingState label="Loading bounded trades" /> : <div>{(trades.data?.trades ?? []).map((trade) => <label key={trade.lotId}><input type="checkbox" checked={tradeLotIds.includes(trade.lotId)} onChange={() => toggleTrade(trade.lotId)} />{trade.symbol} · {trade.status} · {trade.lotId}</label>)}</div>}</details>}
            <p className="quant-inline-note">Context to send: {contextCategories.length ? contextCategories.join(" · ") : "instruction only"}. Credentials, environment files, unrelated records, and provider keys are never selectable.</p>
            <div className="quant-form-actions"><button className="primary" type="button" disabled={!status.data?.configured || submitting || !instruction.trim()} onClick={request}>{submitting ? <LoaderCircle className="spin" size={14} /> : <Bot size={14} />}{submitting ? "Generating…" : "Generate research draft"}</button></div>
            {notice && <Message kind="error">{notice}</Message>}
            {response && <section className="ai-copilot-response"><strong>{response.label}</strong><small>{response.provider} · {response.model} · request {response.requestId.slice(0, 8)}</small><pre>{response.content}</pre><div className="quant-form-actions"><button type="button" onClick={() => void navigator.clipboard.writeText(response.content)}><Copy size={14} />Copy</button><button type="button" disabled={saving || Boolean(saved)} onClick={saveDraft}><Save size={14} />{saved ? "Draft saved" : saving ? "Saving…" : "Save research draft"}</button>{onUseDraft && ["STRATEGY", "INDICATOR"].includes(response.suggestedDraftType) && <button type="button" onClick={() => onUseDraft(response.content)}>Use in editor</button>}</div>{saved && <Message kind="success">Saved as DRAFT {saved.draftId.slice(0, 8)}. Review it, then use the normal validation and immutable-save workflow.</Message>}</section>}
          </div>}
  </Panel>;
}

function ContextSelect({ label, value, setValue, options }: {
  label: string; value: string; setValue: (value: string) => void; options: Array<{ id: string; label: string }>;
}) {
  return <label><span>{label}</span><select aria-label={label} value={value} onChange={(event) => setValue(event.target.value)}><option value="">Do not send</option>{options.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>;
}
