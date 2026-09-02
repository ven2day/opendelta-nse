"use client";

import { AlertTriangle, FlaskConical } from "lucide-react";

type PeriodMetrics = {
  trades: number;
  closedTrades: number;
  netPnl: number;
  profitFactor: number | null;
  maximumDrawdown: number;
  expectancy: number | null;
};

type OptimizationRow = {
  rank: number;
  parameters: {
    atrLength: number;
    stopAtrMultiplier: number;
    rewardRiskRatio: number;
    maxHoldingSessions: number;
    minimumStopPct: number;
    maximumStopPct: number;
  };
  training: PeriodMetrics;
  validation: PeriodMetrics;
  criteriaPassed: boolean;
  criteriaWarnings: string[];
  label: string;
  stability: {
    neighbourCount: number;
    neighbourSensitivityPct: number;
    positiveValidationFoldsPct: number;
    warning: boolean;
  };
};

export type AtrOptimizationResponse = {
  metadata: {
    optimizerVersion: string;
    timeframe: string;
    symbolCount: number;
    configurationCount: number;
    foldCount: number;
    runtimeSeconds: number;
    symbolsRequested: number;
    symbolsProcessed: number;
    symbolsFailed: number;
  };
  folds: Array<{
    fold: number;
    trainingStart: string;
    trainingEnd: string;
    validationStart: string;
    validationEnd: string;
  }>;
  topConfigurations: OptimizationRow[];
  warning: string;
  errors: Array<{ symbol: string; message: string }>;
};

function number(value: number | null, digits = 2) {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

function money(value: number | null) {
  return value === null ? "—" : `₹${number(value)}`;
}

export function AtrOptimizationResults({ response }: { response: AtrOptimizationResponse }) {
  return <section className="backtest-panel atr-optimization-results" aria-label="ATR exit optimization results">
    <div className="panel-title">
      <div><span className="section-kicker">Chronological walk-forward research</span><h2>ATR exit candidates</h2></div>
      <span className="date-window">{response.metadata.symbolsProcessed} symbols · {response.metadata.configurationCount} settings · {response.metadata.foldCount} fold(s)</span>
    </div>
    <div className="research-semantics"><FlaskConical size={16} /><span><strong>Research candidates — not live approved.</strong> One common parameter set is evaluated across the selected universe. Validation results remain separate from training.</span></div>
    <div className="table-wrap">
      <table className="atr-optimization-table">
        <thead><tr><th>Rank</th><th>ATR</th><th>Stop × ATR</th><th>Reward:risk</th><th>Hold</th><th>Stop clamp</th><th>Training trades</th><th>Training net</th><th>Validation trades</th><th>Validation net</th><th>Validation PF</th><th>Validation drawdown</th><th>Expectancy</th><th>Stability</th></tr></thead>
        <tbody>{response.topConfigurations.map((row) => <tr key={`${row.rank}-${JSON.stringify(row.parameters)}`}>
          <td data-label="Rank"><strong>#{row.rank}</strong></td>
          <td data-label="ATR length">{row.parameters.atrLength}</td>
          <td data-label="Stop × ATR">{number(row.parameters.stopAtrMultiplier)}</td>
          <td data-label="Reward:risk">{number(row.parameters.rewardRiskRatio)}</td>
          <td data-label="Hold">{row.parameters.maxHoldingSessions} sessions</td>
          <td data-label="Stop clamp">{number(row.parameters.minimumStopPct)}–{number(row.parameters.maximumStopPct)}%</td>
          <td data-label="Training trades">{row.training.trades.toLocaleString("en-IN")}</td>
          <td data-label="Training net" className={row.training.netPnl >= 0 ? "positive-value" : "negative-value"}>{money(row.training.netPnl)}</td>
          <td data-label="Validation trades">{row.validation.trades.toLocaleString("en-IN")}</td>
          <td data-label="Validation net" className={row.validation.netPnl >= 0 ? "positive-value" : "negative-value"}>{money(row.validation.netPnl)}</td>
          <td data-label="Validation PF">{number(row.validation.profitFactor, 3)}</td>
          <td data-label="Validation drawdown" className="negative-value">{money(-row.validation.maximumDrawdown)}</td>
          <td data-label="Expectancy">{money(row.validation.expectancy)}</td>
          <td data-label="Stability" title={row.criteriaWarnings.join(", ") || "No threshold warning"}>{row.stability.warning ? <span className="optimizer-warning"><AlertTriangle size={14} />Fragile</span> : <span className="positive-value">Neighbour-stable</span>}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <p className="optimizer-footnote">{response.warning} Runtime {number(response.metadata.runtimeSeconds, 1)}s. Costs and slippage from the active backtest settings are included.</p>
  </section>;
}
