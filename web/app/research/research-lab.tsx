"use client";

import { ExperimentBuilder } from "./experiment-builder";
import { FactorCatalog, useFactors } from "./factor-catalog";
import { WorkspaceHeader } from "../platform/workspace-ui";
import { ResearchResults } from "./research-results";

export type ResearchTab = "LEARN" | "EXPERIMENTS" | "RESULTS";

export function ResearchLab({ tab }: { tab: ResearchTab }) {
  const factors = useFactors();
  return <main className="quant-workspace"><WorkspaceHeader eyebrow="Research Lab" title={tab === "LEARN" ? "Learn the factor catalogue" : tab === "EXPERIMENTS" ? "Design a bounded experiment" : "Experiment results"} description={tab === "LEARN" ? "Understand what each factor measures, its data requirements, when it helps and the mistakes it cannot prevent." : "Compare factors with fixed settings, causal execution, chronological splits and an untouched final test period."} />
    <nav className="quant-section-tabs" aria-label="Research Lab"><a className={tab === "LEARN" ? "active" : ""} href="/research">Learn</a><a className={tab === "EXPERIMENTS" ? "active" : ""} href="/research/experiments">Experiments</a><a className={tab === "RESULTS" ? "active" : ""} href="/research/results">Results</a></nav>
    {tab === "LEARN" ? <FactorCatalog {...factors} /> : tab === "EXPERIMENTS" ? factors.loading ? <FactorCatalog {...factors} /> : <ExperimentBuilder factors={factors.factors} /> : <ResearchResults />}
  </main>;
}
