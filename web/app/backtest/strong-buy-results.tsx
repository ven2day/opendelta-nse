"use client";

export type StrongBuyLot = {
  lotId: string; cycleId: string; lotNumber: number; signalTimestamp: string;
  entryTimestamp: string; entryPrice: number; quantity: number; targetPct: number;
  targetPrice: number; status: "HOLDING" | "TAKE_PROFIT_SOLD";
  exitTimestamp: string | null; exitPrice: number | null;
  realizedPnl: number | null; unrealizedPnl: number | null;
};

type FailureMetrics = {
  trades: number; netPnl: number; averagePnl: number; winRate: number;
  averageReturnPct: number; worstTradePct: number; maximumDrawdownCurrency: number;
  takeProfits: number; thesisFailedExits: number; timeHorizonFailures: number;
};

type FailureDecision = {
  decisionTimestamp: string; expectedValuePct: number; successProbability: number;
  expectedFailureLossPct: number; remainingTargetPct: number; failedGroups: string[];
  persistenceBars: number; stateObservations: number; stateLots?: number;
};

export type FailureEngineResearch = {
  mode: "RESEARCH_COMPARE"; status: "RESEARCH_CANDIDATE" | "REJECTED" | "INSUFFICIENT_DATA";
  liveAutoExitEnabled: false; lotsReceived?: number; lotsAvailable: number; rightCensoredLots?: number; foldsCompleted: number;
  foldsSkipped: Array<{ fold: number; trainingLots: number; testLots: number; reason: string }>;
  matchedTestComparison: {
    baseline: FailureMetrics; failureEngine: FailureMetrics; netPnlDifference: number;
    worstTradeImprovementPct: number; maximumDrawdownImprovementCurrency: number;
  };
  candidateRequirements?: {
    allConfiguredFoldsCompleted: boolean; strictNetPnlAndWorstTradeImprovementEveryFold: boolean;
    minimumStateLots: number; minimumThesisExits: number; minimumThesisExitsPerFold: number;
    actualThesisExits: number; met: boolean;
  };
  decisionAudit: Array<{
    lotId: string; symbol: string; exitTimestamp: string; exitPrice: number; pnl: number;
    returnPct: number; status: "THESIS_FAILED_EXIT"; decision: FailureDecision;
  }>;
  warnings: string[];
};

export type StrongBuyBacktestResponse = {
  metadata: { runId: string; strategyMode: "ema_vwap_strong_buy"; strategyName: string; strategyVersion: string; generatedAt: string; durationYears: number; timeframe: string; configuration: Record<string, unknown> };
  summary: { strongBuySignals: number; executedLots: number; takeProfitSold: number; holdingLots: number; targetHitRate: number; realizedPnl: number; unrealizedPnl: number };
  results: Array<{ symbol: string; lots: StrongBuyLot[] }>;
  errors: Array<{ symbol: string; message: string }>;
  warnings: string[];
  failureEngineResearch?: FailureEngineResearch;
};

function money(value: number | null | undefined) {
  return value == null ? "—" : "₹" + value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function time(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function StrongBuyResults({ response }: { response: StrongBuyBacktestResponse }) {
  const lots = response.results.flatMap((result) => result.lots.map((lot) => ({ ...lot, symbol: result.symbol })));
  const research = response.failureEngineResearch;
  const comparison = research?.matchedTestComparison;
  return <>
    <section className="backtest-overview">
      <div><span>Strong Buy signals</span><strong>{response.summary.strongBuySignals}</strong></div>
      <div><span>Executed lots</span><strong>{response.summary.executedLots}</strong></div>
      <div><span>TAKE PROFIT — SOLD</span><strong className="positive-value">{response.summary.takeProfitSold}</strong></div>
      <div><span>STRONG BUY — HOLDING</span><strong style={{ color: "var(--amber)" }}>{response.summary.holdingLots}</strong></div>
      <div><span>Target-hit rate</span><strong>{response.summary.targetHitRate.toFixed(2)}%</strong></div>
      <div><span>Realized / unrealized</span><strong>{money(response.summary.realizedPnl)} / {money(response.summary.unrealizedPnl)}</strong></div>
    </section>
    {research && comparison && <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Research only · live exits disabled</span><h2>Trade Failure Engine walk-forward comparison</h2></div><span className={"trade-status " + (research.status === "RESEARCH_CANDIDATE" ? "hit" : "open")}>{research.status.replaceAll("_", " ")}</span></div>
      <section className="backtest-overview">
        <div><span>Available lots</span><strong>{research.lotsAvailable}</strong></div>
        <div><span>Right-censored excluded</span><strong>{research.rightCensoredLots ?? 0}</strong></div>
        <div><span>Completed folds</span><strong>{research.foldsCompleted}</strong></div>
        <div><span>Matched test lots</span><strong>{comparison.baseline.trades}</strong></div>
        <div><span>Net P&amp;L difference</span><strong className={comparison.netPnlDifference >= 0 ? "positive-value" : "negative-value"}>{money(comparison.netPnlDifference)}</strong></div>
        <div><span>Worst-trade improvement</span><strong className={comparison.worstTradeImprovementPct >= 0 ? "positive-value" : "negative-value"}>{comparison.worstTradeImprovementPct.toFixed(2)} pp</strong></div>
        <div><span>Drawdown improvement</span><strong className={comparison.maximumDrawdownImprovementCurrency >= 0 ? "positive-value" : "negative-value"}>{money(comparison.maximumDrawdownImprovementCurrency)}</strong></div>
      </section>
      <div className="analysis-table-wrap"><table className="analysis-table"><thead><tr><th>Matched test result</th><th>Net P&amp;L</th><th>Win rate</th><th>Worst trade</th><th>Maximum drawdown</th><th>Target exits</th><th>Thesis exits</th><th>Horizon failures</th></tr></thead><tbody>
        {(["baseline", "failureEngine"] as const).map((key) => { const metrics = comparison[key]; return <tr key={key}><td><strong>{key === "baseline" ? "Baseline: hold to target/horizon" : "Failure Engine"}</strong></td><td>{money(metrics.netPnl)}</td><td>{metrics.winRate.toFixed(2)}%</td><td>{metrics.worstTradePct.toFixed(2)}%</td><td>{money(metrics.maximumDrawdownCurrency)}</td><td>{metrics.takeProfits}</td><td>{metrics.thesisFailedExits}</td><td>{metrics.timeHorizonFailures}</td></tr>; })}
      </tbody></table></div>
      {research.foldsSkipped.length > 0 && <p className="cost-note">{research.foldsSkipped.length} fold(s) were skipped because the configured training or test sample minimum was not reached.</p>}
      {research.candidateRequirements && <p className="cost-note">Candidate gate: {research.candidateRequirements.actualThesisExits}/{research.candidateRequirements.minimumThesisExits} required thesis exits; at least {research.candidateRequirements.minimumThesisExitsPerFold} per fold and {research.candidateRequirements.minimumStateLots} independent training lots per exact state.</p>}
      <div className="strategy-warning"><AlertText items={research.warnings} /></div>
      {research.decisionAudit.length > 0 && <details><summary>Audited thesis-failure exits ({research.decisionAudit.length})</summary><div className="analysis-table-wrap"><table className="analysis-table"><thead><tr><th>Symbol</th><th>Decision</th><th>Exit</th><th>Expected value</th><th>Success probability</th><th>Evidence groups</th><th>P&amp;L</th></tr></thead><tbody>
        {research.decisionAudit.slice(0, 100).map((trade) => <tr key={`${trade.lotId}-${trade.exitTimestamp}`}><td><strong>{trade.symbol}</strong></td><td>{time(trade.decision.decisionTimestamp)}</td><td>{money(trade.exitPrice)} · {time(trade.exitTimestamp)}</td><td>{trade.decision.expectedValuePct.toFixed(3)}%</td><td>{(trade.decision.successProbability * 100).toFixed(1)}%</td><td>{trade.decision.failedGroups.join(" + ")}</td><td className={trade.pnl >= 0 ? "positive-value" : "negative-value"}>{money(trade.pnl)}</td></tr>)}
      </tbody></table></div>{research.decisionAudit.length > 100 && <p className="cost-note">Showing the first 100 audited exits.</p>}</details>}
    </section>}
    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Independent lots</span><h2>Strong Buy entries and target outcomes</h2></div><span className="cost-note">Orange = holding · green = sold</span></div>
      <div className="analysis-table-wrap"><table className="analysis-table"><thead><tr><th>State</th><th>Symbol</th><th>Cycle / lot</th><th>Strong Buy</th><th>Entry</th><th>Qty</th><th>Target</th><th>Exit</th><th>P&amp;L</th></tr></thead><tbody>
        {lots.map((lot) => <tr key={lot.lotId}><td><span className={"trade-status " + (lot.status === "TAKE_PROFIT_SOLD" ? "hit" : "open")}>{lot.status === "TAKE_PROFIT_SOLD" ? "TAKE PROFIT — SOLD" : "STRONG BUY — HOLDING"}</span></td><td><strong>{lot.symbol}</strong></td><td>{lot.cycleId} · Lot {lot.lotNumber}</td><td>{time(lot.signalTimestamp)}</td><td>{money(lot.entryPrice)} · {time(lot.entryTimestamp)}</td><td>{lot.quantity}</td><td>{money(lot.targetPrice)} (+{lot.targetPct}%)</td><td>{lot.exitTimestamp ? money(lot.exitPrice) + " · " + time(lot.exitTimestamp) : "Not sold"}</td><td className={lot.status === "TAKE_PROFIT_SOLD" ? "positive-value" : ""}>{money(lot.realizedPnl ?? lot.unrealizedPnl)}</td></tr>)}
        {!lots.length && <tr><td colSpan={9}>No executed Strong Buy lots were found for this run.</td></tr>}
      </tbody></table></div>
    </section>
  </>;
}

function AlertText({ items }: { items: string[] }) {
  return <>{items.map((item) => <p key={item}>{item}</p>)}</>;
}
