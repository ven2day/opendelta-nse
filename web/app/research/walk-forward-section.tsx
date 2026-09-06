"use client";

import { FlaskConical, LoaderCircle, RotateCcw, Square } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import {
  formatDateTime, formatInteger, formatMinutes, formatMoney, formatPercent, isoDate, shortId, tone,
} from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Delete, v2Get, v2Post } from "../platform/v2-client";
import type {
  ResearchExperiment, WalkForwardMode, WalkForwardPreview, WalkForwardRanking,
  WalkForwardValidation, WalkForwardValidationsResponse,
} from "../platform/v2-types";
import { EmptyState, LoadingState, Message, Panel, RequestErrorState, StatusBadge } from "../platform/workspace-ui";

export type WalkForwardStrategyOption = {
  key: string; id: string; sourceId: string | null; name: string; version: string; timeframes: string[];
};
export type WalkForwardUniverseOption = { key: string; name: string; count: number };

type PreviewRecord = { data: WalkForwardPreview; fingerprint: string; idempotencyKey: string };

export function WalkForwardSection({ market, strategies, universes, experiments }: {
  market: PlatformMarket;
  strategies: WalkForwardStrategyOption[];
  universes: WalkForwardUniverseOption[];
  experiments: ResearchExperiment[];
}) {
  const validations = useV2Resource(
    useCallback(() => v2Get<WalkForwardValidationsResponse>("research/walk-forward", { market }), [market]),
    5_000,
  );
  const [name, setName] = useState("Walk-forward validation");
  const [mode, setMode] = useState<WalkForwardMode>("ROLLING");
  const [strategyKey, setStrategyKey] = useState("");
  const selected = strategies.find((item) => item.key === strategyKey) ?? strategies[0] ?? null;
  const [timeframe, setTimeframe] = useState("5m");
  const effectiveTimeframe = selected?.timeframes.includes(timeframe) ? timeframe : selected?.timeframes[0] ?? "5m";
  const [universeKey, setUniverseKey] = useState("");
  const effectiveUniverse = universes.some((item) => item.key === universeKey) ? universeKey : universes[0]?.key ?? "";
  const candidates = useMemo(() => experiments.filter((item) => (
    item.market === market && item.strategyId === selected?.id && item.strategyVersion === selected?.version
    && (item.strategySourceId ?? null) === (selected?.sourceId ?? null) && item.timeframe === effectiveTimeframe
    && item.variantCount <= 20
  )), [effectiveTimeframe, experiments, market, selected]);
  const [candidateId, setCandidateId] = useState("");
  const effectiveCandidateId = candidates.some((item) => item.experimentId === candidateId)
    ? candidateId : candidates[0]?.experimentId ?? "";
  const [startDate, setStartDate] = useState(() => isoDate(new Date(Date.now() - 180 * 86_400_000)));
  const [endDate, setEndDate] = useState(() => isoDate(new Date()));
  const [trainingWindow, setTrainingWindow] = useState(20);
  const [testingWindow, setTestingWindow] = useState(5);
  const [step, setStep] = useState(5);
  const [maximumFolds, setMaximumFolds] = useState(6);
  const [ranking, setRanking] = useState<WalkForwardRanking>("RETURN_DRAWDOWN");
  const [minimumTrades, setMinimumTrades] = useState(3);
  const [transactionCostBps, setTransactionCostBps] = useState(market === "NSE" ? 11.1 : 8);
  const [slippageBps, setSlippageBps] = useState(market === "NSE" ? 5 : 2);
  const [preview, setPreview] = useState<PreviewRecord | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  const fingerprint = JSON.stringify({
    name, mode, strategy: selected?.key, timeframe: effectiveTimeframe, universe: effectiveUniverse,
    candidate: effectiveCandidateId, startDate, endDate, trainingWindow, testingWindow, step, maximumFolds,
    ranking, minimumTrades, transactionCostBps, slippageBps,
  });
  const previewFresh = preview?.fingerprint === fingerprint;
  const mutate = (action: () => void) => { action(); setNotice(null); };
  const buildPayload = () => {
    if (!selected) throw new Error("Select an exact strategy version.");
    if (!effectiveUniverse) throw new Error("Select an active watchlist or supported universe.");
    if (!effectiveCandidateId) throw new Error("Create or select a compatible parameter experiment first.");
    if (!name.trim()) throw new Error("Validation name is required.");
    if (startDate > endDate) throw new Error("Start date must not be after end date.");
    const universe = effectiveUniverse.startsWith("saved:")
      ? { universeId: effectiveUniverse.slice(6) }
      : { universePresetId: effectiveUniverse.slice(7) };
    return {
      name: name.trim(), mode, market, strategyId: selected.id, strategyVersion: selected.version,
      strategySourceId: selected.sourceId ?? undefined, ...universe, timeframe: effectiveTimeframe,
      startDate, endDate, trainingWindow, testingWindow, step, maximumFolds,
      candidateExperimentId: effectiveCandidateId, rankingObjective: ranking,
      minimumRequiredTrades: minimumTrades, transactionCostBps, slippageBps,
    };
  };
  const previewValidation = async () => {
    setPreviewing(true); setNotice(null);
    try {
      const data = await v2Post<WalkForwardPreview>("research/walk-forward/preview", buildPayload());
      setPreview({ data, fingerprint, idempotencyKey: crypto.randomUUID() });
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "Walk-forward preview is invalid") });
    } finally { setPreviewing(false); }
  };
  const runValidation = async () => {
    if (!preview || !previewFresh) return;
    setSubmitting(true); setNotice(null);
    try {
      const created = await v2Post<WalkForwardValidation>("research/walk-forward/from-preview", {
        ...buildPayload(), previewHash: preview.data.previewHash, idempotencyKey: preview.idempotencyKey,
      });
      setNotice({ kind: "success", text: `Walk-forward validation ${shortId(created.validationId)} queued.` });
      validations.refresh();
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "Walk-forward validation could not be started") });
    } finally { setSubmitting(false); }
  };
  const reset = () => {
    setMode("ROLLING"); setTrainingWindow(20); setTestingWindow(5); setStep(5); setMaximumFolds(6);
    setRanking("RETURN_DRAWDOWN"); setMinimumTrades(3); setPreview(null); setNotice(null);
  };
  const cancel = async (validationId: string) => {
    try { await v2Delete(`research/walk-forward/${validationId}`); validations.refresh(); }
    catch (reason) { setNotice({ kind: "error", text: errorMessage(reason, "Cancellation could not be requested") }); }
  };

  return <>
    <Panel icon={<FlaskConical size={17} />} title="Walk-forward validation" description="Select on TRAINING data, then freeze and evaluate the winner on each UNSEEN TEST period.">
      <div className="quant-panel-body">
        <div className="research-mode-control" role="group" aria-label="Walk-forward mode">
          <button type="button" className={mode === "ANCHORED" ? "active" : ""} onClick={() => mutate(() => setMode("ANCHORED"))}>Anchored</button>
          <button type="button" className={mode === "ROLLING" ? "active" : ""} onClick={() => mutate(() => setMode("ROLLING"))}>Rolling</button>
        </div>
        <div className="quant-form-grid research-walk-forward-grid">
          <label><span>Validation name</span><input value={name} maxLength={120} onChange={(event) => mutate(() => setName(event.target.value))} /></label>
          <label><span>Strategy and exact version</span><select value={selected?.key ?? ""} onChange={(event) => mutate(() => setStrategyKey(event.target.value))}>{strategies.map((item) => <option key={item.key} value={item.key}>{item.name} · v{item.version}</option>)}</select></label>
          <label><span>Timeframe</span><select value={effectiveTimeframe} onChange={(event) => mutate(() => setTimeframe(event.target.value))}>{selected?.timeframes.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label><span>Watchlist or universe</span><select value={effectiveUniverse} onChange={(event) => mutate(() => setUniverseKey(event.target.value))}>{universes.map((item) => <option key={item.key} value={item.key}>{item.name} · {item.count} symbols</option>)}</select></label>
          <label><span>Candidate experiment</span><select aria-label="Candidate parameter experiment" value={effectiveCandidateId} onChange={(event) => mutate(() => setCandidateId(event.target.value))}><option value="">Select experiment</option>{candidates.map((item) => <option key={item.experimentId} value={item.experimentId}>{item.name} · {item.variantCount} candidates</option>)}</select></label>
          <label><span>Ranking objective</span><select value={ranking} onChange={(event) => mutate(() => setRanking(event.target.value as WalkForwardRanking))}><option value="NET_PNL">Net P&amp;L</option><option value="RETURN_DRAWDOWN">Return / drawdown score</option><option value="LOWEST_DRAWDOWN">Lowest drawdown</option><option value="HIGHEST_WIN_RATE">Highest win rate</option></select></label>
          <label><span>Overall start</span><input type="date" value={startDate} onChange={(event) => mutate(() => setStartDate(event.target.value))} /></label>
          <label><span>Overall end</span><input type="date" value={endDate} onChange={(event) => mutate(() => setEndDate(event.target.value))} /></label>
          <NumberInput label="Training sessions" value={trainingWindow} setValue={(value) => mutate(() => setTrainingWindow(value))} min={1} />
          <NumberInput label="Testing sessions" value={testingWindow} setValue={(value) => mutate(() => setTestingWindow(value))} min={1} />
          <NumberInput label="Step sessions" value={step} setValue={(value) => mutate(() => setStep(value))} min={1} />
          <NumberInput label="Maximum folds" value={maximumFolds} setValue={(value) => mutate(() => setMaximumFolds(value))} min={1} max={12} />
          <NumberInput label="Minimum completed trades" value={minimumTrades} setValue={(value) => mutate(() => setMinimumTrades(value))} min={0} />
          <NumberInput label="Transaction cost · bps/side" value={transactionCostBps} setValue={(value) => mutate(() => setTransactionCostBps(value))} min={0} step="0.1" />
          <NumberInput label="Slippage · bps/side" value={slippageBps} setValue={(value) => mutate(() => setSlippageBps(value))} min={0} step="0.1" />
        </div>
        <div className="quant-form-actions research-actions">
          <button type="button" onClick={reset}><RotateCcw size={14} />Reset</button>
          <button type="button" onClick={previewValidation} disabled={previewing || !effectiveCandidateId || !effectiveUniverse}>{previewing ? <LoaderCircle className="spin" size={14} /> : <FlaskConical size={14} />}{previewing ? "Previewing…" : "Preview walk-forward"}</button>
          <button className="primary" type="button" onClick={runValidation} disabled={!previewFresh || submitting}>{submitting ? <LoaderCircle className="spin" size={14} /> : <FlaskConical size={14} />}{submitting ? "Queuing…" : "Run validation"}</button>
          {preview && !previewFresh && <span className="research-preview-stale">Preview stale · preview again before running.</span>}
        </div>
        {notice && <Message kind={notice.kind}>{notice.text}</Message>}
      </div>
    </Panel>

    {preview && <Panel icon={<FlaskConical size={17} />} title="Walk-forward workload preview" description="Preview is read-only; no fold or backtest row exists yet.">
      <div className="quant-panel-body research-walk-preview">
        <dl className="quant-facts research-preview-summary">
          <div><dt>Strategy</dt><dd>{preview.data.strategyId} v{preview.data.strategyVersion}</dd></div>
          <div><dt>Mode</dt><dd>{preview.data.mode}</dd></div><div><dt>Watchlist</dt><dd>{preview.data.universeName}</dd></div>
          <div><dt>Folds</dt><dd>{preview.data.foldCount}</dd></div><div><dt>Candidates/fold</dt><dd>{preview.data.candidateCount}</dd></div>
          <div><dt>Child runs</dt><dd>{preview.data.childRunCount}</dd></div><div><dt>Symbol-runs</dt><dd>{preview.data.estimatedSymbolRuns}</dd></div>
          <div><dt>Candle workload</dt><dd>{formatInteger(preview.data.estimatedCandleWorkload)}</dd></div>
        </dl>
        {preview.data.warnings.map((warning) => <Message kind="error" key={warning}>{warning}</Message>)}
        <FoldTimeline folds={preview.data.folds} />
      </div>
    </Panel>}

    <Panel icon={<FlaskConical size={17} />} title="Walk-forward results" description="Training rankings and unseen results remain separate; failed and incomplete folds stay visible.">
      {validations.loading ? <LoadingState label="Loading walk-forward validations" /> : validations.error ? <RequestErrorState error={validations.error} retry={validations.reload} /> : !validations.data?.validations.length ? <EmptyState title="No walk-forward validations" description="Preview a candidate experiment above." /> : <div className="research-walk-results">{validations.data.validations.map((validation) => <ValidationCard key={validation.validationId} validation={validation} market={market} cancel={cancel} />)}</div>}
    </Panel>
  </>;
}

function NumberInput({ label, value, setValue, min, max, step = "1" }: {
  label: string; value: number; setValue: (value: number) => void; min: number; max?: number; step?: string;
}) {
  return <label><span>{label}</span><input aria-label={label} type="number" min={min} max={max} step={step} value={value} onChange={(event) => setValue(Number(event.target.value))} /></label>;
}

function FoldTimeline({ folds }: { folds: Array<{ position: number; trainingStart: string; trainingEnd: string; testingStart: string; testingEnd: string }> }) {
  return <div className="research-fold-timeline">{folds.map((fold) => <div key={fold.position}>
    <strong>Fold {fold.position}</strong><span className="research-training-label">TRAINING</span>
    <span>{fold.trainingStart} → {fold.trainingEnd}</span><span className="research-unseen-label">UNSEEN TEST</span>
    <span>{fold.testingStart} → {fold.testingEnd}</span>
  </div>)}</div>;
}

function ValidationCard({ validation, market, cancel }: {
  validation: WalkForwardValidation; market: PlatformMarket; cancel: (id: string) => void;
}) {
  const active = ["QUEUED", "RUNNING", "CANCELLING"].includes(validation.status);
  const aggregate = validation.aggregateUnseenMetrics;
  return <article className="research-experiment-card research-walk-card">
    <header><div><strong>{validation.name}</strong><small>{validation.mode} · {validation.strategyId} v{validation.strategyVersion} · {validation.foldCount} folds · created {formatDateTime(validation.createdAt, market)}</small></div><div className="research-card-actions"><StatusBadge tone={tone(validation.status)}>{validation.status}</StatusBadge>{active && <button type="button" onClick={() => cancel(validation.validationId)}><Square size={12} />Cancel</button>}</div></header>
    {aggregate && <dl className="quant-facts research-unseen-summary">
      <div><dt>UNSEEN net P&amp;L</dt><dd>{formatMoney(aggregate.netPnl ?? 0, market)}</dd></div>
      <div><dt>Maximum drawdown</dt><dd>{formatMoney(aggregate.maximumDrawdown ?? 0, market)}</dd></div>
      <div><dt>Win rate</dt><dd>{aggregate.winRate == null ? "—" : formatPercent(aggregate.winRate, 1)}</dd></div>
      <div><dt>Completed trades</dt><dd>{formatInteger(aggregate.completedTrades ?? 0)}</dd></div>
      <div><dt>Average holding</dt><dd>{formatMinutes(aggregate.averageHoldingMinutes ?? 0)}</dd></div>
      <div><dt>Return / drawdown</dt><dd>{(aggregate.returnDrawdownScore ?? 0).toFixed(3)}</dd></div>
    </dl>}
    <div className="research-fold-details">{validation.folds.map((fold) => {
      const selectedTraining = fold.trainingCandidates.find((item) => item.variantId === fold.selectedVariantId);
      return <details key={fold.foldId}><summary><strong>Fold {fold.position}</strong><span className="research-training-label">TRAINING</span>{fold.trainingStart} → {fold.trainingEnd}<span className="research-unseen-label">UNSEEN TEST</span>{fold.testingStart} → {fold.testingEnd}<StatusBadge tone={tone(fold.status)}>{fold.status}</StatusBadge></summary>
        {fold.error && <Message kind="error">{fold.error}</Message>}
        <div className="research-fold-selection"><div><strong>Selected candidate</strong><span>{fold.selectedCandidateName ?? "Pending training completion"}</span>{selectedTraining && <a className="quant-inline-link" href={`/backtest?${new URLSearchParams({ market, runId: selectedTraining.run.runId })}`}>Training chart</a>}</div><div><strong>Unseen result</strong><span>{fold.testRun?.status ?? "Not started"}</span>{fold.testRun && <a className="quant-inline-link" href={`/backtest?${new URLSearchParams({ market, runId: fold.testRun.runId })}`}>Unseen chart</a>}</div></div>
        {fold.selectedConfiguration && <details className="quant-details"><summary>Frozen immutable configuration and parameter changes</summary><pre>{JSON.stringify({ strategy: fold.selectedConfiguration, execution: fold.selectedExecution }, null, 2)}</pre></details>}
      </details>;
    })}</div>
    <p className="quant-inline-note">Training selects candidates only from completed runs meeting the minimum-trades rule. Aggregate metrics use unseen test runs only. Return / drawdown score is not a Sharpe ratio.</p>
  </article>;
}
