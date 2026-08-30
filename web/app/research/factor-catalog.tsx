"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { platformGet } from "../platform/platform-client";
import { ErrorState, LoadingState, StatusBadge } from "../platform/workspace-ui";

export type Factor = { factor_id: string; version: string; name: string; family: string; description: string; measures: string; use_when: string; avoid_when: string; misunderstanding: string; required_data: string[]; supported_markets: string[]; supported_timeframes: string[]; warmup_bars: number; missing_data_behavior: string; parameters: { name: string; default: number; minimum: number; maximum: number; description: string }[] };
type Response = { rows: Factor[]; count: number };

export function useFactors() {
  const [factors, setFactors] = useState<Factor[]>([]); const [error, setError] = useState(""); const [loading, setLoading] = useState(true);
  const load = useCallback(async () => { setLoading(true); setError(""); try { setFactors((await platformGet<Response>("factors")).rows); } catch (reason) { setError(reason instanceof Error ? reason.message : "Factor catalogue is unavailable"); } finally { setLoading(false); } }, []);
  useEffect(() => { const initial = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(initial); }, [load]);
  return { factors, error, loading, reload: load };
}

export function FactorCatalog({ factors, error, loading, reload }: { factors: Factor[]; error: string; loading: boolean; reload: () => Promise<void> }) {
  const [family, setFamily] = useState("ALL");
  const families = useMemo(() => ["ALL", ...Array.from(new Set(factors.map((factor) => factor.family)))], [factors]);
  const shown = family === "ALL" ? factors : factors.filter((factor) => factor.family === family);
  if (loading) return <LoadingState label="Loading educational factor definitions" />;
  if (error) return <ErrorState message={error} retry={() => void reload()} />;
  return <><div className="quant-family-filter" aria-label="Factor family filter">{families.map((item) => <button type="button" className={family === item ? "active" : ""} key={item} onClick={() => setFamily(item)}>{item.replaceAll("_", " ")}</button>)}</div><div className="factor-catalog-grid">{shown.map((factor) => <article key={factor.factor_id}><div className="quant-card-title"><div><span className="mono">{factor.factor_id} · v{factor.version}</span><h2>{factor.name}</h2></div><StatusBadge>{factor.family.replaceAll("_", " ")}</StatusBadge></div><p>{factor.description}</p><dl><div><dt>Measures</dt><dd>{factor.measures}</dd></div><div><dt>Use when</dt><dd>{factor.use_when}</dd></div><div><dt>Avoid when</dt><dd>{factor.avoid_when}</dd></div><div><dt>Common misunderstanding</dt><dd>{factor.misunderstanding}</dd></div></dl><footer><span>Warm-up: {factor.warmup_bars} bars</span><span>Data: {factor.required_data.join(", ")}</span><span>{factor.missing_data_behavior}</span></footer></article>)}</div></>;
}
