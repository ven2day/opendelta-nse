"use client";

type PeriodMetrics = {
  executedTrades: number;
  closedTrades: number;
  winningTrades: number;
  losingTrades: number;
  openPositions: number;
  netPnl: number;
  profitFactor: number | null;
  maximumDrawdown: number;
  expectancy: number | null;
  winRate: number;
};

type ComparisonRow = {
  rank: number;
  parameters: {
    rsiArmLow: number;
    rsiArmHigh: number;
    rsiRecovery: number;
    profitExitRsi: number;
    minimumProfitPct: number;
    hardStopLossPct: number;
    maxHoldingSessions: number;
  };
  training: PeriodMetrics;
  validation: PeriodMetrics;
  criteriaPassed: boolean;
  criteriaWarnings: string[];
  label: string;
  stability: {
    warning: boolean;
    neighbourCount: number;
    medianNeighbourValidationPnl: number | null;
    unstableBetweenFolds: boolean;
  };
};

export type RsiExitComparisonResponse = {
  metadata: {
    configurationCount: number;
    foldCount: number;
    symbolCount: number;
    symbolsRequested: number;
    symbolsProcessed: number;
    symbolsFailed: number;
    runtimeSeconds: number;
    chronological: boolean;
    shuffled: boolean;
  };
  topConfigurations: ComparisonRow[];
  warning: string;
  errors: Array<{ symbol: string; message: string }>;
};

function number(value: number | null, digits = 2) {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function money(value: number | null) {
  return value === null ? "—" : `₹${number(value)}`;
}

export function RsiExitComparisonResults({ response }: { response: RsiExitComparisonResponse }) {
  return <section className="backtest-panel recovery-section optimizer-results">
    <div className="panel-title">
      <div><span className="section-kicker">Chronological validation</span><h2>RSI exit research candidates</h2></div>
      <span className="date-window">{response.metadata.symbolCount} symbols · {response.metadata.configurationCount} settings · {number(response.metadata.runtimeSeconds)}s</span>
    </div>
    <div className="research-semantics">
      <span><strong>Research candidate — not live approved.</strong> Configurations use one common setting across all selected symbols. Validation P&amp;L, profit factor, expectancy, drawdown, trade count, and neighbouring-setting stability drive the order—not win rate alone.</span>
    </div>
    <div className="table-wrap" role="region" aria-label="RSI exit comparison results">
      <table className="atr-optimization-table">
        <thead><tr><th>Rank</th><th>Entry / exit settings</th><th>Training</th><th>Validation</th><th>Validation PF</th><th>Expectancy</th><th>Max drawdown</th><th>Assessment</th></tr></thead>
        <tbody>{response.topConfigurations.map((row) => <tr key={`${row.rank}-${JSON.stringify(row.parameters)}`}>
          <td data-label="Rank"><strong>#{row.rank}</strong></td>
          <td data-label="Entry / exit settings"><b>Arm {row.parameters.rsiArmLow}–{row.parameters.rsiArmHigh} · recover {row.parameters.rsiRecovery}</b><small>Exit RSI {row.parameters.profitExitRsi} · min +{number(row.parameters.minimumProfitPct)}% · stop {number(row.parameters.hardStopLossPct)}% · {row.parameters.maxHoldingSessions} sessions</small></td>
          <td data-label="Training"><b>{money(row.training.netPnl)}</b><small>{row.training.closedTrades} closed · PF {number(row.training.profitFactor)}</small></td>
          <td data-label="Validation"><b className={row.validation.netPnl >= 0 ? "positive-value" : "negative-value"}>{money(row.validation.netPnl)}</b><small>{row.validation.closedTrades} closed · {number(row.validation.winRate)}% wins</small></td>
          <td data-label="Validation PF">{number(row.validation.profitFactor)}</td>
          <td data-label="Expectancy">{money(row.validation.expectancy)}</td>
          <td data-label="Max drawdown" className="negative-value">{money(-row.validation.maximumDrawdown)}</td>
          <td data-label="Assessment"><b className={row.criteriaPassed ? "positive-value" : "warning-value"}>{row.criteriaPassed ? "Criteria passed" : "Research only"}</b><small>{row.criteriaWarnings.length ? row.criteriaWarnings.join(" · ").replaceAll("_", " ") : "No configured warning triggered"}</small></td>
        </tr>)}</tbody>
      </table>
    </div>
    {response.errors.map((error) => <p key={error.symbol}><strong>{error.symbol}:</strong> {error.message}</p>)}
    <p className="cost-note">{response.warning}</p>
  </section>;
}
