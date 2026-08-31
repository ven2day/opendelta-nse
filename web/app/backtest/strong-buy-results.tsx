"use client";

export type StrongBuyLot = {
  lotId: string; cycleId: string; lotNumber: number; signalTimestamp: string;
  entryTimestamp: string; entryPrice: number; quantity: number; targetPct: number;
  targetPrice: number; status: "HOLDING" | "TAKE_PROFIT_SOLD";
  exitTimestamp: string | null; exitPrice: number | null;
  realizedPnl: number | null; unrealizedPnl: number | null;
};

export type StrongBuyBacktestResponse = {
  metadata: { runId: string; strategyMode: "ema_vwap_strong_buy"; strategyName: string; strategyVersion: string; generatedAt: string; durationYears: number; timeframe: string; configuration: Record<string, unknown> };
  summary: { strongBuySignals: number; executedLots: number; takeProfitSold: number; holdingLots: number; targetHitRate: number; realizedPnl: number; unrealizedPnl: number };
  results: Array<{ symbol: string; lots: StrongBuyLot[] }>;
  errors: Array<{ symbol: string; message: string }>;
  warnings: string[];
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
  return <>
    <section className="backtest-overview">
      <div><span>Strong Buy signals</span><strong>{response.summary.strongBuySignals}</strong></div>
      <div><span>Executed lots</span><strong>{response.summary.executedLots}</strong></div>
      <div><span>TAKE PROFIT — SOLD</span><strong className="positive-value">{response.summary.takeProfitSold}</strong></div>
      <div><span>STRONG BUY — HOLDING</span><strong style={{ color: "var(--amber)" }}>{response.summary.holdingLots}</strong></div>
      <div><span>Target-hit rate</span><strong>{response.summary.targetHitRate.toFixed(2)}%</strong></div>
      <div><span>Realized / unrealized</span><strong>{money(response.summary.realizedPnl)} / {money(response.summary.unrealizedPnl)}</strong></div>
    </section>
    <section className="backtest-panel">
      <div className="panel-title"><div><span className="section-kicker">Independent lots</span><h2>Strong Buy entries and target outcomes</h2></div><span className="cost-note">Orange = holding · green = sold</span></div>
      <div className="analysis-table-wrap"><table className="analysis-table"><thead><tr><th>State</th><th>Symbol</th><th>Cycle / lot</th><th>Strong Buy</th><th>Entry</th><th>Qty</th><th>Target</th><th>Exit</th><th>P&amp;L</th></tr></thead><tbody>
        {lots.map((lot) => <tr key={lot.lotId}><td><span className={"trade-status " + (lot.status === "TAKE_PROFIT_SOLD" ? "hit" : "open")}>{lot.status === "TAKE_PROFIT_SOLD" ? "TAKE PROFIT — SOLD" : "STRONG BUY — HOLDING"}</span></td><td><strong>{lot.symbol}</strong></td><td>{lot.cycleId} · Lot {lot.lotNumber}</td><td>{time(lot.signalTimestamp)}</td><td>{money(lot.entryPrice)} · {time(lot.entryTimestamp)}</td><td>{lot.quantity}</td><td>{money(lot.targetPrice)} (+{lot.targetPct}%)</td><td>{lot.exitTimestamp ? money(lot.exitPrice) + " · " + time(lot.exitTimestamp) : "Not sold"}</td><td className={lot.status === "TAKE_PROFIT_SOLD" ? "positive-value" : ""}>{money(lot.realizedPnl ?? lot.unrealizedPnl)}</td></tr>)}
        {!lots.length && <tr><td colSpan={9}>No executed Strong Buy lots were found for this run.</td></tr>}
      </tbody></table></div>
    </section>
  </>;
}
