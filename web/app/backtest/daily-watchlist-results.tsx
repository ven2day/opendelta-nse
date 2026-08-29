"use client";

import { AlertTriangle, Download, Info } from "lucide-react";

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
  rankBefore?: number | null;
  tier?: "PRIMARY" | "RESERVE";
  score?: number;
  promotionReason?: string;
};

export type DailyWatchlistResponse = {
  metadata: {
    runId: string;
    strategyMode: "daily_scalping_watchlist";
    strategyKey: "daily_scalping_watchlist";
    strategyName: string;
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
  };
  summary: MetricSet & {
    rawOpeningCandidates: number;
    rawMiddayCandidates: number;
    acceptedBuySignals: number;
    executedTrades: number;
    executedQuantity: 50;
    rejectionCounts: Record<string, number>;
    funnel: Record<string, number>;
  };
  watchlist: { mode: "FROZEN_OPEN" | "ROLLING"; history: Array<Record<string, unknown>> };
  dailySelections: Array<{ sessionDate: string; selectionTimestamp: string; symbols: WatchlistEntry[] }>;
  middayReplacements: Array<Record<string, unknown>>;
  signals: Array<Record<string, unknown>>;
  openingSignals: Array<Record<string, unknown>>;
  middaySignals: Array<Record<string, unknown>>;
  trades: Array<Record<string, unknown>>;
  rejectedCandidates: Array<Record<string, unknown>>;
  comparison: Record<string, Comparison>;
  validationDecision: { frozenApproved: boolean; rollingApproved: boolean; status: string; reason: string; liveOrdersEnabled: false };
  warnings: string[];
  errors: Array<{ symbol: string; message: string }>;
  results: Array<Record<string, unknown>>;
};

const comparisonNames: Record<string, string> = {
  FROZEN_OPEN_TOP_FIVE: "FROZEN_OPEN top five",
  ROLLING_TOP_FIVE: "ROLLING top five",
  FROZEN_OPEN_TOP_TWO: "Top two",
  FULL_ELIGIBLE_UNIVERSE: "Full eligible universe",
  LIQUIDITY_ONLY_TOP_FIVE: "Liquidity-only top five",
  CAUSALLY_MATCHED_RANDOM_FIVE: "Causally matched random five",
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

function metricMarkdown(label: string, row: MetricSet) {
  return `| ${label} | ${row.trades} | ${row.winRate}% | ${row.grossPnl} | ${row.costs} | ${row.netPnlAfterCosts} | ${row.expectancy ?? "—"} | ${row.profitFactor ?? "—"} | ${row.maximumDrawdown} |`;
}

function exportMarkdown(response: DailyWatchlistResponse) {
  const lines = [
    `# ${response.metadata.strategyName}`,
    "",
    `- Strategy version: ${response.metadata.strategyVersion}`,
    `- Configuration hash: ${response.metadata.configurationHash}`,
    `- Result source: ${response.metadata.resultSource}`,
    `- Research status: ${response.validationDecision.status}`,
    `- Live orders enabled: ${response.metadata.liveOrdersEnabled}`,
    `- Quantity invariant: exactly ${response.summary.executedQuantity} shares`,
    `- Opening-range assumption: ${response.metadata.openingRangeAssumption}`,
    "",
    "## Effective settings",
    "",
    "```json",
    JSON.stringify(response.metadata.effectiveConfiguration, null, 2),
    "```",
    "",
    "## Comparison",
    "",
    "| Variant | Trades | Win rate | Gross P&L | Costs | Net P&L | Expectancy | Profit factor | Max drawdown |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ...Object.entries(response.comparison).map(([key, value]) => metricMarkdown(comparisonNames[key] ?? key, value.overall)),
    "",
    "## Daily selections",
    "",
    ...response.dailySelections.flatMap((selection) => [
      `### ${selection.sessionDate} — ${selection.selectionTimestamp}`,
      ...selection.symbols.map((item) => `- #${item.rank ?? item.rankAfter ?? "—"} ${item.symbol} — ${item.tier ?? "—"} — score ${item.score ?? "—"}`),
      "",
    ]),
    "## Midday replacements",
    "",
    "```json",
    JSON.stringify(response.middayReplacements, null, 2),
    "```",
    "",
    "## Opening and midday BUY signals",
    "",
    "```json",
    JSON.stringify({ opening: response.openingSignals, midday: response.middaySignals }, null, 2),
    "```",
    "",
    "## Development and untouched validation",
    "",
    "```json",
    JSON.stringify(Object.fromEntries(Object.entries(response.comparison).map(([key, value]) => [key, value.chronologicalFolds])), null, 2),
    "```",
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `daily-watchlist-${response.metadata.runId}.md`;
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
      <div className="trade-foot"><span>Rank {String(signal.rankBeforeRescan ?? "—")} → {String(signal.rankAfterRescan ?? "—")}</span><span>{String(signal.promotionReason ?? "OPENING_SELECTION")}</span></div>
    </article>)}</div> : <div className="empty-history">No BUY signal passed the causal rules.</div>}
  </details>;
}

export function DailyWatchlistResults({ response }: { response: DailyWatchlistResponse }) {
  return <>
    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">{response.metadata.researchLabel}</span><h2>Daily Scalping Watchlist validation</h2></div><button type="button" className="secondary-action" onClick={() => exportMarkdown(response)}><Download size={15} /> Export Markdown</button></div>
      <div className={`backtest-message ${response.validationDecision.status === "REJECTED_RESEARCH_ONLY" ? "error" : "open-position"}`}><AlertTriangle size={17} /><span><strong>{response.validationDecision.status.replaceAll("_", " ")}</strong> — {response.validationDecision.reason} Live orders remain disabled.</span></div>
      <details className="advanced-settings" open><summary>Effective settings · {response.metadata.configurationHash.slice(0, 12)}</summary><pre className="configuration-audit">{JSON.stringify(response.metadata.effectiveConfiguration, null, 2)}</pre></details>
    </section>

    <section className="backtest-overview">
      <div><span>Opening candidates</span><strong>{response.summary.rawOpeningCandidates}</strong></div>
      <div><span>Midday candidates</span><strong>{response.summary.rawMiddayCandidates}</strong></div>
      <div><span>Accepted BUY signals</span><strong>{response.summary.acceptedBuySignals}</strong></div>
      <div><span>Executed trades</span><strong>{response.summary.executedTrades}</strong></div>
      <div><span>Fixed quantity</span><strong>{response.summary.executedQuantity} shares</strong></div>
      <div><span>Net P&amp;L</span><strong>{money(response.summary.netPnlAfterCosts)}</strong></div>
    </section>

    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Explainable chronology</span><h2>Signal funnel and rejections</h2></div></div>
      <div className="metric-grid">{Object.entries(response.summary.funnel).map(([label, value]) => <div key={label}><span>{label.replace(/([a-z])([A-Z])/g, "$1 $2")}</span><strong>{value}</strong></div>)}</div>
      <details className="advanced-settings"><summary>Primary rejection reasons</summary><div className="metric-grid">{Object.entries(response.summary.rejectionCounts).map(([label, value]) => <div key={label}><span>{label.replaceAll("_", " ")}</span><strong>{value}</strong></div>)}</div></details>
    </section>

    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Opening selection audit</span><h2>Daily selected symbols</h2></div><span className="cost-note">Rank · PRIMARY/RESERVE · timestamp</span></div>
      {response.dailySelections.length ? <div className="trade-list">{response.dailySelections.map((selection) => <article className="trade-card" key={`${selection.sessionDate}-${selection.selectionTimestamp}`}>
        <div className="trade-card-top"><strong>{selection.sessionDate}</strong><span>{timestamp(selection.selectionTimestamp)}</span></div>
        <div className="trade-foot"><span>{selection.symbols.map((item) => `#${item.rank ?? item.rankAfter} ${item.symbol} ${item.tier}`).join(" · ")}</span></div>
      </article>)}</div> : <div className="empty-history">No opening selection could be formed from available completed candles.</div>}
      <details className="advanced-settings"><summary>Complete intraday watchlist history ({response.watchlist.history.length})</summary><pre className="configuration-audit">{JSON.stringify(response.watchlist.history, null, 2)}</pre></details>
      <details className="advanced-settings"><summary>Midday replacements ({response.middayReplacements.length})</summary><pre className="configuration-audit">{JSON.stringify(response.middayReplacements, null, 2)}</pre></details>
    </section>

    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Causal entries</span><h2>Opening and midday BUY signals</h2></div></div>
      <SignalList title="Opening range BUY signals" signals={response.openingSignals} />
      <SignalList title="Midday promotion BUY signals" signals={response.middaySignals} />
    </section>

    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Identical costs and risk rules</span><h2>Selector and baseline comparison</h2></div></div>
      {Object.entries(response.comparison).map(([key, value]) => <details className="advanced-settings" key={key} open={key === "FROZEN_OPEN_TOP_FIVE" || key === "ROLLING_TOP_FIVE"}>
        <summary>{comparisonNames[key] ?? key}</summary>
        <Metrics row={value.overall} />
        <div className="metric-grid"><div><span>Rescans</span><strong>{value.rescans}</strong></div><div><span>Replacements</span><strong>{value.replacements}</strong></div><div><span>Opening signals</span><strong>{value.signalsFromOpeningSelection}</strong></div><div><span>Midday signals</span><strong>{value.signalsFromMiddayPromotions}</strong></div></div>
        {value.chronologicalFolds.map((fold, index) => <div key={`${key}-${index}`}><h3>Fold {index + 1}</h3><p>Development {fold.developmentFrom}–{fold.developmentTo}</p><Metrics row={fold.development} /><p>Untouched validation {fold.validationFrom}–{fold.validationTo}</p><Metrics row={fold.validation} /></div>)}
        <details className="advanced-settings"><summary>Midday time buckets</summary>{Object.entries(value.midday).map(([label, metrics]) => <div key={label}><h3>{label}</h3><Metrics row={metrics} /></div>)}</details>
      </details>)}
    </section>

    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Execution audit</span><h2>Executed trades</h2></div><span className="cost-note">Every row must show 50 shares</span></div>
      {response.trades.length ? <div className="trade-list">{response.trades.map((trade, index) => <article className="trade-card" key={String(trade.tradeId ?? index)}>
        <div className="trade-card-top"><strong>{String(trade.symbol)} · {String(trade.signalType).replaceAll("_", " ")}</strong><span>{String(trade.exitReason).replaceAll("_", " ")} · {money(trade.netPnl)}</span></div>
        <div className="trade-foot"><span>Entry {money(trade.entryPrice)} · stop {money(trade.stopPrice)} · target {money(trade.targetPrice)}</span><span>{String(trade.executedQuantity)} shares</span></div>
        <div className="trade-foot"><span>Gross {money(trade.grossPnl)}</span><span>Costs {money(trade.totalCosts)}</span></div>
      </article>)}</div> : <div className="empty-history">No trade passed every causal signal and portfolio rule.</div>}
    </section>

    {(response.errors.length > 0 || response.warnings.length > 0) && <section className="backtest-notes"><h3>Backtest notes</h3>{response.errors.map((item) => <p key={`${item.symbol}-${item.message}`}><AlertTriangle size={14} /> <strong>{item.symbol}:</strong> {item.message}</p>)}{response.warnings.map((warning) => <p key={warning}><Info size={14} /> {warning}</p>)}</section>}
  </>;
}
