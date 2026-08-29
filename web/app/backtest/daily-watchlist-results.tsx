"use client";

import { AlertTriangle, Download, Info } from "lucide-react";
import {
  buildTop5OpeningRangeBreakoutMarkdown,
  TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY,
} from "./top-5-opening-range-breakout-contract.mjs";

type MetricSet = {
  trades: number;
  tradesPerDay: number;
  noTradeDays: number;
  winRate: number;
  averageWinner: number | null;
  averageLoser: number | null;
  grossPnl: number;
  costs: number;
  netPnlAfterCosts: number;
  profitFactor: number | null;
  expectancy: number | null;
  maximumDrawdown: number;
  executedQuantityInvariant: boolean;
};

type Comparison = {
  rescans: number;
  replacements: number;
  newlyPromotedSymbols: number;
  signalsFromOpeningSelection: number;
  signalsFromMiddayPromotions: number;
  overall: MetricSet;
  chronologicalFolds: Array<{
    developmentFrom: string;
    developmentTo: string;
    validationFrom: string;
    validationTo: string;
    development: MetricSet;
    validation: MetricSet;
  }>;
  midday: Record<string, MetricSet>;
};

type WatchlistEntry = {
  symbol: string;
  rank?: number | null;
  rankAfter?: number | null;
  tier?: "PRIMARY" | "RESERVE";
  score?: number;
};

export type Top5OpeningRangeBreakoutResponse = {
  metadata: {
    runId: string;
    strategyMode: "top_5_opening_range_breakout";
    strategyKey: "top_5_opening_range_breakout";
    strategyName: "Top-5 Opening Range Breakout";
    strategyVersion: string;
    completedAt: string;
    timeframe: "5m";
    durationYears: 1 | 3;
    configurationHash: string;
    fingerprint: string;
    resultSource: "FRESH_CALCULATION" | "RESULT_CACHE";
    effectiveConfiguration: Record<string, unknown>;
    openingRangeAssumption: string;
    researchLabel: string;
    liveOrdersEnabled: false;
    watchlistMode: "FROZEN_OPEN" | "ROLLING";
    universeEvaluated: number;
    tradingDays: number;
  };
  summary: MetricSet & {
    rawOpeningCandidates: number;
    rawMiddayCandidates: number;
    openingBreakoutCandidates: number;
    acceptedBuySignals: number;
    executedTrades: number;
    executedQuantity: 50;
    universeEvaluated: number;
    tradingDays: number;
    dailyWatchlists: number;
    primarySelections: number;
    reserveSelections: number;
    watchlistReplacements: number;
    rejectionCounts: Record<string, number>;
    funnel: Record<string, number>;
  };
  watchlist: { mode: "FROZEN_OPEN" | "ROLLING"; history: Array<Record<string, unknown>> };
  dailySelections: Array<{ sessionDate: string; selectionTimestamp: string; symbols: WatchlistEntry[] }>;
  middayReplacements: Array<Record<string, unknown>>;
  openingSignals: Array<Record<string, unknown>>;
  middaySignals: Array<Record<string, unknown>>;
  signals: Array<Record<string, unknown>>;
  trades: Array<Record<string, unknown>>;
  rejectedCandidates: Array<Record<string, unknown>>;
  comparison: Record<string, Comparison>;
  validationDecision: { frozenApproved: boolean; rollingApproved: boolean; status: string; reason: string; liveOrdersEnabled: false };
  warnings: string[];
  errors: Array<{ symbol: string; message: string }>;
  results: Array<Record<string, unknown>>;
};

const comparisonNames: Record<string, string> = {
  FROZEN_OPEN_TOP_FIVE: "Top-5 FROZEN_OPEN",
  ROLLING_TOP_FIVE: "Top-5 ROLLING",
  FROZEN_OPEN_TOP_TWO: "Top-2",
  FULL_ELIGIBLE_UNIVERSE: "Full-universe baseline",
  LIQUIDITY_ONLY_TOP_FIVE: "Liquidity-only baseline",
  CAUSALLY_MATCHED_RANDOM_FIVE: "Random-five baseline",
};

function number(value: unknown, digits = 2) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(numeric)
    : "—";
}

function money(value: unknown) { return `₹${number(value)}`; }
function timestamp(value: unknown) {
  if (typeof value !== "string") return "—";
  return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" }).format(new Date(value));
}

function exportMarkdown(response: Top5OpeningRangeBreakoutResponse) {
  const markdown = buildTop5OpeningRangeBreakoutMarkdown(response);
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `top-5-opening-range-breakout-${response.metadata.runId}.md`;
  anchor.click();
  URL.revokeObjectURL(url);
}

function Metrics({ row }: { row: MetricSet }) {
  return <div className="metric-grid">
    <div><span>Trades</span><strong>{row.trades}</strong></div>
    <div><span>Win rate</span><strong>{number(row.winRate)}%</strong></div>
    <div><span>Gross P&amp;L</span><strong>{money(row.grossPnl)}</strong></div>
    <div><span>Costs</span><strong>{money(row.costs)}</strong></div>
    <div><span>Net P&amp;L</span><strong>{money(row.netPnlAfterCosts)}</strong></div>
    <div><span>Expectancy</span><strong>{money(row.expectancy)}</strong></div>
    <div><span>Profit factor</span><strong>{number(row.profitFactor, 4)}</strong></div>
    <div><span>Maximum drawdown</span><strong>{money(row.maximumDrawdown)}</strong></div>
    <div><span>Quantity invariant</span><strong>{row.executedQuantityInvariant ? "50 shares ✓" : "FAILED"}</strong></div>
  </div>;
}

function SignalList({ title, signals }: { title: string; signals: Array<Record<string, unknown>> }) {
  return <details className="advanced-settings"><summary>{title} ({signals.length})</summary>
    {signals.length ? <div className="trade-list">{signals.map((signal, index) => <article className="trade-card" key={String(signal.candidateId ?? `${title}-${index}`)}>
      <div className="trade-card-top"><strong>{String(signal.symbol)} · {String(signal.watchlistTier ?? "—")}</strong><span>score {number(signal.rollingScore)}</span></div>
      <div className="trade-foot"><span>Selected {timestamp(signal.selectionTimestamp)}</span><span>Signal {timestamp(signal.signalTimestamp)}</span></div>
      <div className="trade-foot"><span>Entry {timestamp(signal.entryTimestamp)} · {money(signal.entryPrice)}</span><span>Breakout {money(signal.breakoutLevel)}</span></div>
      <div className="trade-foot"><span>VWAP {money(signal.sessionVwap)} · EMA {number(signal.emaFast)}/{number(signal.emaSlow)}</span><span>RSI {number(signal.rsi)} · RVOL {number(signal.rollingRvol)}x</span></div>
      <div className="trade-foot"><span>Stop {money(signal.stopPrice)} · target {money(signal.targetPrice)}</span><span>{String(signal.exitReason ?? "—").replaceAll("_", " ")} · net {money(signal.netPnl)}</span></div>
    </article>)}</div> : <div className="empty-history">No BUY signal passed the causal rules.</div>}
  </details>;
}

export function Top5OpeningRangeBreakoutResults({ response }: { response: Top5OpeningRangeBreakoutResponse }) {
  if (response.metadata.strategyKey !== TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY) {
    return <section className="backtest-panel"><div className="backtest-message error"><AlertTriangle size={17} /><span>Top-5 renderer rejected a mismatched strategy response.</span></div></section>;
  }
  return <>
    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">{response.metadata.researchLabel}</span><h2>Top-5 Opening Range Breakout</h2></div><button type="button" className="secondary-action" onClick={() => exportMarkdown(response)}><Download size={15} /> Export Markdown</button></div>
      <div className={`backtest-message ${response.validationDecision.status === "REJECTED_RESEARCH_ONLY" ? "error" : "open-position"}`}><AlertTriangle size={17} /><span><strong>{response.validationDecision.status.replaceAll("_", " ")}</strong> — {response.validationDecision.reason} Live orders remain disabled.</span></div>
      <div className="metric-grid"><div><span>Watchlist mode</span><strong>{response.metadata.watchlistMode}</strong></div><div><span>Universe evaluated</span><strong>{response.metadata.universeEvaluated}</strong></div><div><span>Trading days</span><strong>{response.metadata.tradingDays}</strong></div></div>
      <details className="advanced-settings" open><summary>Effective settings · {response.metadata.configurationHash.slice(0, 12)}</summary><pre className="configuration-audit">{JSON.stringify(response.metadata.effectiveConfiguration, null, 2)}</pre></details>
    </section>

    <section className="backtest-overview">
      <div><span>Daily watchlists</span><strong>{response.summary.dailyWatchlists}</strong></div>
      <div><span>PRIMARY selections</span><strong>{response.summary.primarySelections}</strong></div>
      <div><span>RESERVE selections</span><strong>{response.summary.reserveSelections}</strong></div>
      <div><span>Watchlist replacements</span><strong>{response.summary.watchlistReplacements}</strong></div>
      <div><span>Opening breakout candidates</span><strong>{response.summary.openingBreakoutCandidates}</strong></div>
      <div><span>Accepted BUY signals</span><strong>{response.summary.acceptedBuySignals}</strong></div>
      <div><span>Executed trades</span><strong>{response.summary.executedTrades}</strong></div>
      <div><span>Net P&amp;L</span><strong>{money(response.summary.netPnlAfterCosts)}</strong></div>
    </section>

    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Opening selection audit</span><h2>Daily selected symbols</h2></div><span className="cost-note">Rank · PRIMARY/RESERVE · selection timestamp</span></div>
      {response.dailySelections.length ? <div className="trade-list">{response.dailySelections.map((selection) => <article className="trade-card" key={`${selection.sessionDate}-${selection.selectionTimestamp}`}>
        <div className="trade-card-top"><strong>{selection.sessionDate}</strong><span>{timestamp(selection.selectionTimestamp)}</span></div>
        <div className="trade-foot"><span>{selection.symbols.map((item) => `#${item.rank ?? item.rankAfter} ${item.symbol} ${item.tier}`).join(" · ")}</span></div>
      </article>)}</div> : <div className="empty-history">No opening watchlist could be formed.</div>}
      <details className="advanced-settings"><summary>Complete intraday watchlist history ({response.watchlist.history.length})</summary><pre className="configuration-audit">{JSON.stringify(response.watchlist.history, null, 2)}</pre></details>
      <details className="advanced-settings"><summary>Midday replacements ({response.middayReplacements.length})</summary><pre className="configuration-audit">{JSON.stringify(response.middayReplacements, null, 2)}</pre></details>
    </section>

    <section className="backtest-panel"><div className="panel-title"><div><span className="section-kicker">Causal entries</span><h2>Opening and midday BUY signals</h2></div></div><SignalList title="Opening range BUY signals" signals={response.openingSignals} /><SignalList title="Midday promotion BUY signals" signals={response.middaySignals} /></section>

    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Identical costs and risk rules</span><h2>Top-5, Top-2 and baseline results</h2></div></div>
      {Object.entries(response.comparison).map(([key, value]) => <details className="advanced-settings" key={key} open={key === "FROZEN_OPEN_TOP_FIVE" || key === "ROLLING_TOP_FIVE"}>
        <summary>{comparisonNames[key] ?? key}</summary><Metrics row={value.overall} />
        {value.chronologicalFolds.map((fold, index) => <div key={`${key}-${index}`}><h3>Fold {index + 1}</h3><p>Development {fold.developmentFrom}–{fold.developmentTo}</p><Metrics row={fold.development} /><p>Untouched validation {fold.validationFrom}–{fold.validationTo}</p><Metrics row={fold.validation} /></div>)}
      </details>)}
    </section>

    <section className="backtest-panel"><div className="panel-title"><div><span className="section-kicker">Execution audit</span><h2>Executed trades</h2></div><span className="cost-note">Every row must show 50 shares</span></div>{response.trades.length ? <div className="trade-list">{response.trades.map((trade, index) => <article className="trade-card" key={String(trade.tradeId ?? index)}><div className="trade-card-top"><strong>{String(trade.symbol)} · {String(trade.signalType).replaceAll("_", " ")}</strong><span>{String(trade.exitReason).replaceAll("_", " ")} · {money(trade.netPnl)}</span></div><div className="trade-foot"><span>Entry {money(trade.entryPrice)} · stop {money(trade.stopPrice)} · target {money(trade.targetPrice)}</span><span>{String(trade.executedQuantity)} shares</span></div><div className="trade-foot"><span>Gross {money(trade.grossPnl)}</span><span>Costs {money(trade.totalCosts)}</span></div></article>)}</div> : <div className="empty-history">No trade passed every causal signal and portfolio rule.</div>}</section>

    {(response.errors.length > 0 || response.warnings.length > 0) && <section className="backtest-notes"><h3>Backtest notes</h3>{response.errors.map((item) => <p key={`${item.symbol}-${item.message}`}><AlertTriangle size={14} /> <strong>{item.symbol}:</strong> {item.message}</p>)}{response.warnings.map((warning) => <p key={warning}><Info size={14} /> {warning}</p>)}</section>}
  </>;
}
