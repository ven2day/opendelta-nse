"use client";

import { FlaskConical, Play, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { platformGet, platformPost, type PlatformMarket } from "../platform/platform-client";
import { ErrorState, StatusBadge } from "../platform/workspace-ui";
import type { Factor } from "./factor-catalog";

type Mode = "EXACT" | "TOURNAMENT" | "FORWARD_SELECTION";
type Estimate = { possibleCombinations: number; plannedBacktests: number; bounded: boolean; symbols: number; candlesEstimate: number };
type Metrics = { status: string; tradeCount: number; netProfitCurrency: number; expectancy: number; profitFactor: number | null; maximumDrawdown: number; returnToDrawdown: number; winRate: number };
type Evaluation = { metrics: Metrics | null; unsupported?: { factorId: string; reason: string }[] };
type Experiment = { experimentId: string; selectedFactorIds: string[]; untouchedTestResult: Evaluation; warnings: string[]; paperOnly: boolean; liveOrdersEnabled: boolean };
type Job = { jobId: string; status: string; progress: number; result?: Experiment | null; error?: { code: string; message: string } | null };
type ResearchEngine = { enabled: boolean; status: string; message: string };
type Overview = { researchEngine?: ResearchEngine };

const initialSelection: string[] = [];

function isoDate(daysAgo: number): string {
  const value = new Date(); value.setUTCDate(value.getUTCDate() - daysAgo);
  return value.toISOString().slice(0, 10);
}

export function ExperimentBuilder({ factors }: { factors: Factor[] }) {
  const [market, setMarket] = useState<PlatformMarket>("NSE");
  const [provider, setProvider] = useState("DHAN");
  const [symbol, setSymbol] = useState("LUPIN");
  const [startDate, setStartDate] = useState(isoDate(365));
  const [endDate, setEndDate] = useState(isoDate(0));
  const [contextTimeframe, setContextTimeframe] = useState("15m");
  const [setupTimeframe, setSetupTimeframe] = useState("5m");
  const [executionTimeframe, setExecutionTimeframe] = useState("1m");
  const [mode, setMode] = useState<Mode>("EXACT");
  const [selected, setSelected] = useState<string[]>(initialSelection);
  const [minimumTrades, setMinimumTrades] = useState(30);
  const [beamWidth, setBeamWidth] = useState(2);
  const [targetPct, setTargetPct] = useState(0.51);
  const [stopLossPct, setStopLossPct] = useState(1);
  const [holdingBars, setHoldingBars] = useState(50);
  const [quantity, setQuantity] = useState(50);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [researchEngine, setResearchEngine] = useState<ResearchEngine | null>(null);
  const submissionKey = useRef("");

  useEffect(() => {
    let active = true;
    void platformGet<Overview>("overview")
      .then((overview) => { if (active) setResearchEngine(overview.researchEngine ?? null); })
      .catch(() => { if (active) setResearchEngine(null); });
    return () => { active = false; };
  }, []);

  const researchEnabled = researchEngine?.enabled === true;
  const available = useMemo(
    () => factors.filter((factor) => factor.supported_markets.includes(market)),
    [factors, market],
  );
  const payload = () => ({
    researchVersion: "2",
    mode,
    market,
    provider,
    baseStrategyId: market === "NSE" ? "rsi_recovery" : "crypto_trend_pullback_recovery",
    symbols: [symbol],
    startDate,
    endDate,
    contextTimeframe,
    setupTimeframe,
    executionTimeframe,
    direction: "LONG",
    factorSelections: selected,
    factorParameters: {},
    targetPct,
    stopLossPct,
    maximumHoldingBars: holdingBars,
    maximumTradesPerDay: 5,
    maximumOpenPositions: 2,
    oneOpenPositionPerSymbol: true,
    stopAfterFirstLoss: false,
    maximumDailyLossPct: 2,
    quantityPerTrade: quantity,
    capitalPerPosition: 100000,
    totalCapital: 1000000,
    buyCostBps: 5,
    sellCostBps: 5,
    slippageBpsPerSide: 2,
    minimumTrades,
    beamWidth,
    trainingFraction: 0.6,
    validationFraction: 0.2,
    testFraction: 0.2,
    rankingMetric: "EXPECTANCY",
  });

  const chooseMarket = (next: PlatformMarket) => {
    setMarket(next);
    setProvider(next === "NSE" ? "DHAN" : "OKX");
    setSymbol(next === "NSE" ? "LUPIN" : "BTC-USDT");
    setContextTimeframe(next === "NSE" ? "15m" : "1h");
    setSetupTimeframe(next === "NSE" ? "5m" : "15m");
    setExecutionTimeframe(next === "NSE" ? "1m" : "5m");
    setSelected([]);
    setEstimate(null);
    setJob(null);
  };

  const toggle = (factor: Factor) => {
    setEstimate(null);
    if (mode === "EXACT") { setSelected((current) => [...current.filter((id) => available.find((item) => item.factor_id === id)?.family !== factor.family), factor.factor_id]); return; }
    const sameFamily = mode !== "TOURNAMENT" || selected.length === 0 || available.find((item) => item.factor_id === selected[0])?.family === factor.family;
    if (!sameFamily) {
      setSelected([factor.factor_id]);
      return;
    }
    setSelected((current) => current.includes(factor.factor_id)
      ? current.filter((id) => id !== factor.factor_id)
      : [...current, factor.factor_id]);
  };

  const getEstimate = async () => {
    if (!researchEnabled) return;
    setBusy(true);
    setError("");
    try {
      setEstimate(await platformPost<Estimate>("estimate", payload()));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Estimate failed");
    } finally {
      setBusy(false);
    }
  };

  const poll = async (jobId: string) => {
    for (let count = 0; count < 240; count += 1) {
      const current = await platformGet<Job>("job", { jobId });
      setJob(current);
      if (["COMPLETE", "FAILED", "CANCELLED"].includes(current.status)) return;
      await new Promise((resolve) => window.setTimeout(resolve, 1_000));
    }
    throw new Error("The experiment is still running; continue monitoring it from Jobs.");
  };

  const run = async () => {
    if (!researchEnabled) return;
    setBusy(true);
    setError("");
    try {
      if (!submissionKey.current) submissionKey.current = crypto.randomUUID();
      const started = await platformPost<Job>("experiment", payload(), submissionKey.current);
      setJob(started);
      await poll(started.jobId);
      submissionKey.current = crypto.randomUUID();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Experiment failed");
    } finally {
      setBusy(false);
    }
  };

  return <div className="research-builder">
    <div className="quant-inline-warning" role="status">
      <strong>{researchEnabled ? "Research V2 enabled" : "Research execution disabled"}</strong><br />
      {researchEngine?.message ?? "The server-side Research V2 safety gate is being checked. New experiments remain disabled."}
    </div>
    <section className="quant-panel research-config">
      <div className="quant-panel-heading"><div><FlaskConical size={18} /><div><h2>Experiment configuration</h2><p>Bounded search with chronological training, validation and untouched test periods.</p></div></div><StatusBadge tone={researchEnabled ? "good" : "warn"}>{researchEnabled ? "Research V2" : "Fail closed"}</StatusBadge></div>
      <div className="research-form-grid">
        <label><span>Mode</span><select value={mode} onChange={(event) => { const next = event.target.value as Mode; setMode(next); setSelected(initialSelection); setEstimate(null); }}><option value="EXACT">Exact configuration</option><option value="TOURNAMENT">Single-family tournament</option><option value="FORWARD_SELECTION">Forward selection</option></select></label>
        <label><span>Market</span><select value={market} onChange={(event) => chooseMarket(event.target.value as PlatformMarket)}><option>NSE</option><option value="CRYPTO">Crypto</option></select></label>
        <label><span>Provider</span><select value={provider} onChange={(event) => setProvider(event.target.value)}>{market === "NSE" ? <option>DHAN</option> : <><option>OKX</option><option>VALR</option></>}</select></label>
        <label><span>Exact symbol</span><input value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} /></label>
        <label><span>Start date</span><input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
        <label><span>End date</span><input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
        <label><span>Context timeframe</span><select value={contextTimeframe} onChange={(event) => setContextTimeframe(event.target.value)}>{["15m", "30m", "1h", "6h", "1d"].map((item) => <option key={item}>{item}</option>)}</select></label>
        <label><span>Setup timeframe</span><select value={setupTimeframe} onChange={(event) => setSetupTimeframe(event.target.value)}>{["5m", "15m", "30m", "1h"].map((item) => <option key={item}>{item}</option>)}</select></label>
        <label><span>Execution timeframe</span><select value={executionTimeframe} onChange={(event) => setExecutionTimeframe(event.target.value)}>{["1m", "5m", "15m"].map((item) => <option key={item}>{item}</option>)}</select></label>
        <label><span>Minimum trades</span><input type="number" min="5" max="10000" value={minimumTrades} onChange={(event) => setMinimumTrades(Number(event.target.value))} /></label>
        {mode === "FORWARD_SELECTION" && <label><span>Beam width</span><input type="number" min="1" max="3" value={beamWidth} onChange={(event) => setBeamWidth(Number(event.target.value))} /></label>}
      </div>
      <details><summary>Execution and risk</summary><div className="research-form-grid"><label><span>Target %</span><input type="number" step="0.01" value={targetPct} onChange={(event) => setTargetPct(Number(event.target.value))} /></label><label><span>Stop-loss %</span><input type="number" step="0.01" value={stopLossPct} onChange={(event) => setStopLossPct(Number(event.target.value))} /></label><label><span>Maximum holding bars</span><input type="number" value={holdingBars} onChange={(event) => setHoldingBars(Number(event.target.value))} /></label><label><span>Quantity per trade</span><input type="number" value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} /></label><label><span>Total capital</span><input value="₹10,00,000" disabled /></label><label><span>Maximum trades daily</span><input value="5" disabled /></label></div></details>
      <details><summary>Factor selection · {selected.length} selected</summary><div className="research-factor-select">{available.map((factor) => <div key={factor.factor_id} className={selected.includes(factor.factor_id) ? "selected" : ""}><input id={`factor-${factor.factor_id}`} type="checkbox" checked={selected.includes(factor.factor_id)} onChange={() => toggle(factor)} /><label htmlFor={`factor-${factor.factor_id}`} aria-label={`Select ${factor.name}`}><span><strong>{factor.name}</strong><small>{factor.family.replaceAll("_", " ")}</small></span></label></div>)}</div></details>
      {error && <ErrorState message={error} />}
      <div className="research-actions"><button type="button" disabled={!researchEnabled || busy} onClick={() => void getEstimate()}><RefreshCw size={15} />Estimate search</button><button className="primary" type="button" disabled={!researchEnabled || busy || !estimate?.bounded} onClick={() => void run()}><Play size={15} />{busy ? "Working…" : "Run experiment"}</button><span>{estimate ? `${estimate.symbols} symbols · ${estimate.candlesEstimate.toLocaleString()} candles · ${estimate.plannedBacktests} backtests` : researchEnabled ? "Estimate workload before running" : "Research V2 acceptance is still in progress"}</span></div>
    </section>
    {job && <section className="quant-panel research-result"><div className="quant-panel-heading"><div><h2>Experiment job</h2><p className="mono">{job.jobId}</p></div><StatusBadge tone={job.status === "COMPLETE" ? "good" : job.status === "FAILED" ? "bad" : "warn"}>{job.status}</StatusBadge></div><div className="quant-progress"><span style={{ width: `${job.progress}%` }} /></div>{job.error && <div className="quant-inline-warning">{job.error.code}: {job.error.message}</div>}{job.result && <ExperimentResult result={job.result} />}</section>}
  </div>;
}

function metric(value: number, percentage = false): string {
  if (!Number.isFinite(value)) return "Undefined";
  return percentage ? `${(value * 100).toFixed(2)}%` : value.toFixed(4);
}

function ExperimentResult({ result }: { result: Experiment }) {
  const test = result.untouchedTestResult.metrics;
  if (!test) return <ErrorState message="The selected factors require unavailable data." />;
  return <div><div className="quant-kpi-grid"><article><span>Test status</span><strong>{test.status}</strong><small>{test.tradeCount} real trades</small></article><article><span>Net P&amp;L</span><strong>{test.netProfitCurrency.toFixed(2)}</strong><small>After configured costs</small></article><article><span>Expectancy</span><strong>{metric(test.expectancy, true)}</strong><small>Per executed trade</small></article><article><span>Max drawdown</span><strong>{metric(test.maximumDrawdown, true)}</strong><small>Real trade ledger</small></article></div><dl className="research-result-detail"><div><dt>Selected factors</dt><dd>{result.selectedFactorIds.join(", ") || "Baseline only"}</dd></div><div><dt>Profit factor</dt><dd>{test.profitFactor == null ? "Undefined" : metric(test.profitFactor)}</dd></div><div><dt>Return / drawdown</dt><dd>{metric(test.returnToDrawdown)}</dd></div><div><dt>Win rate</dt><dd>{metric(test.winRate, true)}</dd></div></dl>{result.warnings.map((warning) => <p className="research-warning" key={warning}>{warning}</p>)}</div>;
}
