"use client";

import { AlertTriangle, Info } from "lucide-react";

export type VwapPullbackTrade = Record<string, unknown> & {
  tradeId: string;
  symbol: string;
  signalTimestamp: string;
  entryTimestamp: string;
  entryPrice: number;
  stopPrice: number;
  targetPrice: number;
  exitTimestamp: string;
  exitPrice: number;
  exitReason: string;
  quantity: number;
  grossPnl: number;
  totalCosts: number;
  netPnl: number;
  rMultiple: number | null;
  qualityScore: number;
};

export type VwapPullbackResponse = {
  metadata: {
    runId: string;
    strategyMode: "market_aligned_vwap_pullback_scalper";
    strategyKey: "market_aligned_vwap_pullback_scalper";
    strategyName: string;
    strategyVersion: string;
    completedAt: string;
    timeframe: "5m";
    durationYears: 1 | 3;
    configurationHash: string;
    fingerprint: string;
    resultSource: "FRESH_CALCULATION" | "RESULT_CACHE";
    cachedResult: boolean;
    effectiveConfiguration: Record<string, unknown>;
    researchLabel: string;
  };
  summary: Record<string, unknown> & {
    rawCandidates: number;
    acceptedBuySignals: number;
    executedTrades: number;
    rejectedCandidates: number;
    winningTrades: number;
    losingTrades: number;
    winRate: number;
    grossPnl: number;
    costs: number;
    netPnl: number;
    expectancy: number | null;
    profitFactor: number | null;
    maximumDrawdown: number;
    averageR: number | null;
    maximumConsecutiveLosses: number;
    tradesPerDay: number;
    noTradeDays: number;
    funnel: Record<string, number>;
    rejectionCounts: Record<string, number>;
  };
  trades: VwapPullbackTrade[];
  rejectedCandidates: Array<Record<string, unknown>>;
  results: Array<Record<string, unknown>>;
  walkForwardValidation: { method: string; folds: Array<Record<string, unknown>>; label: string };
  warnings: string[];
  errors: Array<{ symbol: string; message: string }>;
};

function number(value: unknown, digits = 2) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(numeric)
    : "—";
}

function money(value: unknown) { return `₹${number(value)}`; }

function time(value: unknown) {
  if (typeof value !== "string") return "—";
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" }).format(new Date(value));
}

const funnelLabels: Record<string, string> = {
  trendQualifiedBars: "Trend-qualified bars",
  pullbacksArmed: "Pullbacks armed",
  validTriggerCandles: "Valid trigger candles",
  marketSafetyPassed: "Market safety passed",
  liquidityPassed: "Liquidity passed",
  entriesAttempted: "Entries attempted",
  gapSkips: "Gap skips",
  riskWidthSkips: "Risk-width skips",
  dailyLimitSkips: "Daily-limit skips",
  executedTrades: "Executed trades",
};

export function VwapPullbackResults({ response }: { response: VwapPullbackResponse }) {
  const summary = response.summary;
  return <>
    <section className="backtest-overview">
      <div><span>Raw candidates</span><strong>{number(summary.rawCandidates, 0)}</strong></div>
      <div><span>Accepted BUY signals</span><strong>{number(summary.acceptedBuySignals, 0)}</strong></div>
      <div><span>Executed trades</span><strong>{number(summary.executedTrades, 0)}</strong></div>
      <div><span>Rejected candidates</span><strong>{number(summary.rejectedCandidates, 0)}</strong></div>
      <div><span>Net P&amp;L</span><strong>{money(summary.netPnl)}</strong></div>
      <div><span>Win rate</span><strong>{number(summary.winRate)}%</strong></div>
    </section>

    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">{response.metadata.researchLabel}</span><h2>VWAP pullback performance</h2></div><span className="cost-note">{response.metadata.resultSource.replaceAll("_", " ")} · {response.metadata.configurationHash.slice(0, 12)}</span></div>
      <div className="metric-grid">
        <div><span>Winning / losing</span><strong>{summary.winningTrades} / {summary.losingTrades}</strong></div>
        <div><span>Gross P&amp;L</span><strong>{money(summary.grossPnl)}</strong></div>
        <div><span>Costs</span><strong>{money(summary.costs)}</strong></div>
        <div><span>Expectancy</span><strong>{money(summary.expectancy)}</strong></div>
        <div><span>Profit factor</span><strong>{number(summary.profitFactor, 4)}</strong></div>
        <div><span>Maximum drawdown</span><strong>{money(summary.maximumDrawdown)}</strong></div>
        <div><span>Average R</span><strong>{number(summary.averageR, 3)}</strong></div>
        <div><span>Maximum consecutive losses</span><strong>{summary.maximumConsecutiveLosses}</strong></div>
        <div><span>Trades per day</span><strong>{number(summary.tradesPerDay, 3)}</strong></div>
        <div><span>No-trade days</span><strong>{summary.noTradeDays}</strong></div>
      </div>
      <details className="advanced-settings"><summary>Effective settings</summary><pre className="configuration-audit">{JSON.stringify(response.metadata.effectiveConfiguration, null, 2)}</pre></details>
    </section>

    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Chronological diagnostics</span><h2>Candidate funnel</h2></div></div>
      <div className="metric-grid">{Object.entries(funnelLabels).map(([key, label]) => <div key={key}><span>{label}</span><strong>{number(summary.funnel[key] ?? 0, 0)}</strong></div>)}</div>
      <details className="advanced-settings"><summary>Rejection diagnostics</summary><div className="metric-grid">{Object.entries(summary.rejectionCounts).map(([reason, count]) => <div key={reason}><span>{reason.replaceAll("_", " ")}</span><strong>{count}</strong></div>)}</div></details>
    </section>

    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Frozen at entry</span><h2>Executed trades</h2></div><span className="cost-note">Stop-first when one candle touches both exits</span></div>
      {response.trades.length ? <div className="trade-list">{response.trades.map((trade) => <article className="trade-card" key={trade.tradeId}>
        <div className="trade-card-top"><strong>{trade.symbol} · score {number(trade.qualityScore, 0)}</strong><span>{trade.exitReason.replaceAll("_", " ")} · {money(trade.netPnl)}</span></div>
        <div className="trade-foot"><span>Entry {money(trade.entryPrice)} · stop {money(trade.stopPrice)} · target {money(trade.targetPrice)}</span><span>{trade.quantity} shares · {number(trade.rMultiple, 3)}R</span></div>
        <div className="trade-foot"><span>{time(trade.entryTimestamp)}</span><span>{time(trade.exitTimestamp)}</span></div>
      </article>)}</div> : <div className="empty-history">No trade passed every chronological entry and portfolio rule.</div>}
    </section>

    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">No validation tuning</span><h2>Walk-forward validation</h2></div></div>
      <p>{response.walkForwardValidation.method}. {response.walkForwardValidation.folds.length} fold(s) reported separately.</p>
    </section>

    {(response.errors.length > 0 || response.warnings.length > 0) && <section className="backtest-notes"><h3>Backtest notes</h3>{response.errors.map((item) => <p key={`${item.symbol}-${item.message}`}><AlertTriangle size={14} /> <strong>{item.symbol}:</strong> {item.message}</p>)}{response.warnings.map((warning) => <p key={warning}><Info size={14} /> {warning}</p>)}</section>}
  </>;
}
