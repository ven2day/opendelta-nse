"use client";

import { FlaskConical } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import {
  formatInteger, formatMinutes, formatMoney, formatPercent, shortId, tone,
} from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import type { ConfigValues } from "../platform/schema-form";
import { useV2Resource } from "../platform/use-v2";
import { v2Get } from "../platform/v2-client";
import type {
  BacktestMetrics, BacktestRun, WalkForwardFold,
  WalkForwardValidation, WalkForwardValidationsResponse,
} from "../platform/v2-types";
import { EmptyState, LoadingState, Panel, RequestErrorState, StatusBadge } from "../platform/workspace-ui";

type ComparisonRow = {
  key: string;
  fold: WalkForwardFold;
  dataset: "TRAINING" | "UNSEEN TEST";
  candidate: string;
  run: BacktestRun | null;
  configuration: ConfigValues | null;
  execution: ConfigValues | null;
};

function costs(metrics: BacktestMetrics | null | undefined): number {
  return (metrics?.fees ?? 0) + (metrics?.slippage ?? 0);
}

function returnDrawdown(metrics: BacktestMetrics | null | undefined): number {
  const net = (metrics?.realizedPnl ?? 0) + (metrics?.unrealizedPnl ?? 0);
  return net / Math.max(Math.abs(metrics?.maximumDrawdown ?? 0), 1);
}

function rowsFor(validation: WalkForwardValidation): ComparisonRow[] {
  return validation.folds.flatMap((fold) => {
    const training = fold.trainingCandidates.find((item) => item.variantId === fold.selectedVariantId) ?? null;
    return [
      {
        key: `${fold.foldId}:training`, fold, dataset: "TRAINING", candidate: training?.name ?? "Pending selection",
        run: training?.run ?? null,
        configuration: training?.run.configurationSnapshot ?? training?.configuration ?? fold.selectedConfiguration ?? null,
        execution: training?.run.executionSettings ?? training?.execution ?? fold.selectedExecution ?? null,
      },
      {
        key: `${fold.foldId}:unseen`, fold, dataset: "UNSEEN TEST", candidate: fold.selectedCandidateName ?? "Pending selection",
        run: fold.testRun ?? null,
        configuration: fold.testRun?.configurationSnapshot ?? fold.selectedConfiguration ?? null,
        execution: fold.testRun?.executionSettings ?? fold.selectedExecution ?? null,
      },
    ];
  });
}

export function WalkForwardComparison({ market }: { market: PlatformMarket }) {
  const validations = useV2Resource(
    useCallback(() => v2Get<WalkForwardValidationsResponse>("research/walk-forward", { market }), [market]),
    5_000,
  );
  const [validationId, setValidationId] = useState("");
  const available = validations.data?.validations ?? [];
  const validation = available.find((item) => item.validationId === validationId) ?? available[0] ?? null;
  const rows = useMemo(() => validation ? rowsFor(validation) : [], [validation]);
  const [inspectionKey, setInspectionKey] = useState<string | null>(null);
  const inspection = rows.find((row) => row.key === inspectionKey) ?? null;

  return <Panel
    icon={<FlaskConical size={17} />}
    title="Training versus unseen comparison"
    description="Fold winners are selected from TRAINING only. UNSEEN TEST metrics never affect the earlier selection."
  >
    {validations.loading ? <LoadingState label="Loading walk-forward comparisons" />
      : validations.error ? <RequestErrorState error={validations.error} retry={validations.reload} />
        : !validation ? <EmptyState title="No walk-forward results" description="Completed, failed, and running folds will remain visible here." />
          : <div className="quant-panel-body research-walk-comparison">
            <div className="research-comparison-controls"><label><span>Validation</span><select aria-label="Walk-forward comparison" value={validation.validationId} onChange={(event) => { setValidationId(event.target.value); setInspectionKey(null); }}>{available.map((item) => <option key={item.validationId} value={item.validationId}>{item.name} · {item.foldCount} folds</option>)}</select></label></div>
            <div className="quant-table-scroll"><table className="quant-table research-comparison-table"><thead><tr>
              <th>Fold</th><th>Dataset</th><th>Candidate</th><th>Status</th><th className="numeric">Net P&amp;L</th>
              <th className="numeric">Maximum drawdown</th><th className="numeric">Win rate</th><th className="numeric">Costs</th>
              <th className="numeric">Completed / open</th><th className="numeric">Targets / stops / expiries</th>
              <th className="numeric">Average holding</th><th className="numeric">Exposure</th><th className="numeric">Failed symbols</th>
              <th className="numeric">Return / drawdown</th><th>Inspect</th>
            </tr></thead><tbody>{rows.map((row) => {
              const metrics = row.run?.metrics;
              const netPnl = (metrics?.realizedPnl ?? 0) + (metrics?.unrealizedPnl ?? 0);
              return <tr key={row.key}>
                <td><strong>Fold {row.fold.position}</strong>{row.dataset === "TRAINING" && row.fold.trainingRank ? <small>Rank {row.fold.trainingRank}</small> : null}</td>
                <td><span className={row.dataset === "TRAINING" ? "research-training-label" : "research-unseen-label"}>{row.dataset}</span></td>
                <td>{row.candidate}</td><td><StatusBadge tone={tone(row.run?.status ?? "QUEUED")}>{row.run?.status ?? "NOT STARTED"}</StatusBadge></td>
                <td className="numeric">{row.run?.metrics ? formatMoney(netPnl, market) : "—"}</td>
                <td className="numeric">{row.run?.metrics ? formatMoney(metrics?.maximumDrawdown ?? 0, market) : "—"}</td>
                <td className="numeric">{metrics?.winRate == null ? "—" : formatPercent(metrics.winRate, 1)}</td>
                <td className="numeric">{row.run?.metrics ? formatMoney(costs(metrics), market) : "—"}</td>
                <td className="numeric">{row.run?.metrics ? `${formatInteger(metrics?.completedTrades ?? 0)} / ${formatInteger(metrics?.openTrades ?? 0)}` : "—"}</td>
                <td className="numeric">{row.run?.metrics ? `${formatInteger(metrics?.targetHits ?? 0)} / ${formatInteger(metrics?.stoppedTrades ?? 0)} / ${formatInteger(metrics?.expiredTrades ?? 0)}` : "—"}</td>
                <td className="numeric">{metrics?.averageHoldingMinutes == null ? "—" : formatMinutes(metrics.averageHoldingMinutes)}</td>
                <td className="numeric">{metrics?.exposureMinutes == null ? "—" : formatMinutes(metrics.exposureMinutes)}</td>
                <td className="numeric">{row.run ? formatInteger(row.run.failedSymbols?.length ?? metrics?.symbolsFailed ?? 0) : "—"}</td>
                <td className="numeric">{row.run?.metrics ? returnDrawdown(metrics).toFixed(3) : "—"}</td>
                <td>{row.run ? <div className="quant-row-actions"><button type="button" onClick={() => setInspectionKey(row.key)}>JSON</button><a className="quant-inline-link" href={`/backtest?${new URLSearchParams({ market, runId: row.run.runId })}`}>Chart</a></div> : "—"}</td>
              </tr>;
            })}</tbody></table></div>
            {inspection && <details className="quant-details research-comparison-inspection" open><summary>Exact immutable inputs · {inspection.dataset} · run {shortId(inspection.run?.runId)}</summary><pre>{JSON.stringify({ configuration: inspection.configuration, execution: inspection.execution }, null, 2)}</pre></details>}
            <p className="quant-inline-note">Failed symbols are operational failures. Rejected trades: unavailable — true rejection analytics require a future bounded decision-event audit model; a strategy emitting no BUY is not a rejected trade.</p>
          </div>}
  </Panel>;
}
