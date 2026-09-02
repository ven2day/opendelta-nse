"use client";

import { Download, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

type Summary = {
  observations: number;
  goodCount: number;
  goodPct: number;
  badCount: number;
  badPct: number;
  neutralCount: number;
  neutralPct: number;
  fast30mCount: number;
  fast2hCount: number;
  sameDayCount: number;
  slowCount: number;
  trappedCount: number;
};

type Separation = {
  feature_rank: number;
  feature_name: string;
  good_median: number | null;
  bad_median: number | null;
  effect_size: number | null;
  absolute_effect_size: number | null;
  direction: string;
  good_count: number;
  bad_count: number;
  missing_pct: number;
};

type GroupMetrics = {
  observations: number;
  target_hit_rate_pct: number;
  fast_30m_pct: number;
  good_le2h_pct: number;
  target_le24h_pct: number;
  slow_gt24h_pct: number;
  open_pct: number;
  median_target_minutes: number | null;
  median_mae_pct: number | null;
  worst_mae_pct: number | null;
};

type FeatureBin = GroupMetrics & {
  feature_name: string;
  bin_number: number;
  bin_label: string;
  minimum: number | null;
  maximum: number | null;
};

type ConfirmationRow = GroupMetrics & { confirmation_combination: string };
type TimeRow = GroupMetrics & { time_of_day_bucket: string };

type AnalysisResponse = {
  metadata: Record<string, unknown> & {
    observationsAnalyzed: number;
    dataFrom?: string;
    dataTo?: string;
    niftyContextAvailable: boolean;
    sectorContextAvailable: boolean;
    dataAdjustmentWarning?: string;
  };
  summary: Summary;
  topSeparatingFeatures: Separation[];
  featureBins: FeatureBin[];
  confirmationAnalysis: ConfirmationRow[];
  timeOfDayAnalysis: TimeRow[];
  availableFilters: {
    symbols: string[];
    timeframes: string[];
    confirmationCombinations: string[];
    timeOfDayBuckets: string[];
    targetOutcomes: string[];
  };
  reportFiles: string[];
};

type Filters = {
  symbol: string;
  timeframe: string;
  dateFrom: string;
  dateTo: string;
  confirmationCombination: string;
  timeOfDayBucket: string;
  targetOutcome: string;
};

const emptyFilters: Filters = {
  symbol: "",
  timeframe: "",
  dateFrom: "",
  dateTo: "",
  confirmationCombination: "",
  timeOfDayBucket: "",
  targetOutcome: "",
};

function number(value: number | null | undefined, digits = 3): string {
  return value === null || value === undefined || !Number.isFinite(value)
    ? "—"
    : value.toLocaleString("en-IN", { maximumFractionDigits: digits });
}

function featureLabel(value: string): string {
  return value.replace(/^feature_/, "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function duration(value: number | null): string {
  if (value === null) return "—";
  if (value <= 120) return `${number(value, 0)}m`;
  if (value <= 1440) return `${number(value / 60, 1)}h`;
  return `${number(value / 1440, 1)}d`;
}

async function loadAnalysis(filters: Filters): Promise<AnalysisResponse> {
  const payload = Object.fromEntries(Object.entries(filters).filter(([, value]) => value !== ""));
  const response = await fetch("/api/recovery-analysis", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Feature analysis could not be loaded");
  return body as AnalysisResponse;
}

export function FeatureAnalysis() {
  const [analysis, setAnalysis] = useState<AnalysisResponse | null>(null);
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedFeature, setSelectedFeature] = useState("feature_volume_ratio");
  const [descending, setDescending] = useState(true);

  const run = async (nextFilters: Filters) => {
    setLoading(true);
    setError(null);
    try {
      const result = await loadAnalysis(nextFilters);
      setAnalysis(result);
      if (!result.featureBins.some((row) => row.feature_name === selectedFeature)) {
        setSelectedFeature(result.featureBins[0]?.feature_name ?? "");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Feature analysis could not be loaded");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    loadAnalysis(emptyFilters)
      .then((result) => {
        if (!cancelled) setAnalysis(result);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Feature analysis could not be loaded");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, []); // generated server snapshot; no client indicators

  const separation = useMemo(() => {
    const rows = [...(analysis?.topSeparatingFeatures ?? [])];
    return rows.sort((left, right) => {
      const difference = (left.absolute_effect_size ?? -1) - (right.absolute_effect_size ?? -1);
      return descending ? -difference : difference;
    });
  }, [analysis, descending]);
  const bins = (analysis?.featureBins ?? []).filter((row) => row.feature_name === selectedFeature);
  const binFeatures = Array.from(new Set((analysis?.featureBins ?? []).map((row) => row.feature_name)));

  if (loading && !analysis) return <section className="backtest-panel feature-analysis-state"><RefreshCw className="spin" size={17} /> Loading causal feature snapshot…</section>;
  if (error && !analysis) return <section className="backtest-panel feature-analysis-state error-message">{error}</section>;
  if (!analysis) return null;
  const summary = analysis.summary;

  return <div className="feature-analysis-view">
    <div className="research-semantics"><strong>Entry-time analysis only.</strong> Features are frozen on the closed BUY signal candle; target time, MAE, MFE and OPEN status are outcomes—not model inputs. This view does not change or filter the strategy.</div>

    <section className="backtest-panel feature-filter-panel">
      <div className="panel-title"><div><span className="section-kicker">Explore the generated baseline</span><h2>Analysis filters</h2></div><span className="date-window">{summary.observations.toLocaleString("en-IN")} observations</span></div>
      <div className="feature-filter-grid">
        <label>Symbol<select value={filters.symbol} onChange={(event) => setFilters({ ...filters, symbol: event.target.value })}><option value="">All symbols</option>{analysis.availableFilters.symbols.map((symbol) => <option key={symbol}>{symbol}</option>)}</select></label>
        <label>Timeframe<select value={filters.timeframe} onChange={(event) => setFilters({ ...filters, timeframe: event.target.value })}><option value="">All available</option>{analysis.availableFilters.timeframes.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>From<input type="date" value={filters.dateFrom} onChange={(event) => setFilters({ ...filters, dateFrom: event.target.value })} /></label>
        <label>To<input type="date" value={filters.dateTo} onChange={(event) => setFilters({ ...filters, dateTo: event.target.value })} /></label>
        <label>Confirmations<select value={filters.confirmationCombination} onChange={(event) => setFilters({ ...filters, confirmationCombination: event.target.value })}><option value="">All combinations</option>{analysis.availableFilters.confirmationCombinations.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Time of day<select value={filters.timeOfDayBucket} onChange={(event) => setFilters({ ...filters, timeOfDayBucket: event.target.value })}><option value="">All sessions</option>{analysis.availableFilters.timeOfDayBuckets.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Outcome<select value={filters.targetOutcome} onChange={(event) => setFilters({ ...filters, targetOutcome: event.target.value })}><option value="">All outcomes</option>{analysis.availableFilters.targetOutcomes.map((value) => <option key={value}>{value}</option>)}</select></label>
        <div className="feature-filter-actions"><button type="button" className="primary-button" onClick={() => void run(filters)} disabled={loading}>{loading ? "Applying…" : "Apply"}</button><button type="button" onClick={() => { setFilters(emptyFilters); void run(emptyFilters); }}>Reset</button></div>
      </div>
      {error && <p className="error-message">{error}</p>}
    </section>

    <section className="recovery-top-cards feature-summary-cards" aria-label="Feature outcome summary">
      <div><span>FAST ≤2H</span><strong>{summary.goodCount.toLocaleString("en-IN")}</strong><small>{number(summary.goodPct)}%</small></div>
      <div><span>SAME DAY 2–24H</span><strong>{summary.sameDayCount.toLocaleString("en-IN")}</strong><small>{number(summary.neutralPct)}%</small></div>
      <div><span>SLOW &gt;24H</span><strong>{summary.slowCount.toLocaleString("en-IN")}</strong><small>{number(summary.slowCount / summary.observations * 100)}%</small></div>
      <div><span>OPEN / TRAPPED</span><strong className="warning-value">{summary.trappedCount.toLocaleString("en-IN")}</strong><small>{number(summary.trappedCount / summary.observations * 100)}%</small></div>
    </section>

    <section className="backtest-panel recovery-section">
      <div className="panel-title recovery-panel-title"><div><span className="section-kicker">GOOD versus BAD</span><h2>Top univariate separation</h2></div><button type="button" onClick={() => setDescending((value) => !value)}>Sort |effect| {descending ? "↓" : "↑"}</button></div>
      <p className="analysis-caption">GOOD is target ≤2h. BAD is target &gt;24h or OPEN. The 2–24h NEUTRAL group is excluded. Cliff&apos;s delta measures distribution separation, not predictive or causal importance.</p>
      <div className="analysis-table-wrap"><table className="analysis-table"><thead><tr><th>Rank</th><th>Feature</th><th>GOOD median</th><th>BAD median</th><th>Effect</th><th>Direction</th><th>GOOD n</th><th>BAD n</th><th>Missing</th></tr></thead><tbody>{separation.slice(0, 30).map((row) => <tr key={row.feature_name}><td>{row.feature_rank}</td><td>{featureLabel(row.feature_name)}</td><td>{number(row.good_median)}</td><td>{number(row.bad_median)}</td><td><b>{number(row.effect_size, 4)}</b></td><td>{row.direction.replaceAll("_", " ")}</td><td>{row.good_count.toLocaleString("en-IN")}</td><td>{row.bad_count.toLocaleString("en-IN")}</td><td>{number(row.missing_pct)}%</td></tr>)}</tbody></table></div>
    </section>

    <section className="backtest-panel recovery-section">
      <div className="panel-title recovery-panel-title"><div><span className="section-kicker">Coarse quintiles</span><h2>Feature distribution</h2></div><select value={selectedFeature} onChange={(event) => setSelectedFeature(event.target.value)}>{binFeatures.map((feature) => <option key={feature} value={feature}>{featureLabel(feature)}</option>)}</select></div>
      <div className="analysis-table-wrap"><table className="analysis-table"><thead><tr><th>Bin</th><th>Range</th><th>Signals</th><th>≤30m</th><th>≤2h</th><th>≤24h</th><th>&gt;24h</th><th>OPEN</th><th>Median MAE</th></tr></thead><tbody>{bins.map((row) => <tr key={row.bin_number}><td>Q{row.bin_number}</td><td>{number(row.minimum)} – {number(row.maximum)}</td><td>{row.observations.toLocaleString("en-IN")}</td><td>{number(row.fast_30m_pct)}%</td><td>{number(row.good_le2h_pct)}%</td><td>{number(row.target_le24h_pct)}%</td><td>{number(row.slow_gt24h_pct)}%</td><td>{number(row.open_pct)}%</td><td>{number(row.median_mae_pct)}%</td></tr>)}</tbody></table></div>
    </section>

    <div className="recovery-two-column feature-two-column">
      <section className="backtest-panel recovery-section"><div className="panel-title"><div><span className="section-kicker">Entry gate state</span><h2>Confirmation combinations</h2></div></div><div className="analysis-table-wrap"><table className="analysis-table"><thead><tr><th>Combination</th><th>n</th><th>≤2h</th><th>≤24h</th><th>OPEN</th><th>Median target</th><th>Median MAE</th></tr></thead><tbody>{analysis.confirmationAnalysis.map((row) => <tr key={row.confirmation_combination}><td>{row.confirmation_combination}</td><td>{row.observations.toLocaleString("en-IN")}</td><td>{number(row.good_le2h_pct)}%</td><td>{number(row.target_le24h_pct)}%</td><td>{number(row.open_pct)}%</td><td>{duration(row.median_target_minutes)}</td><td>{number(row.median_mae_pct)}%</td></tr>)}</tbody></table></div></section>
      <section className="backtest-panel recovery-section"><div className="panel-title"><div><span className="section-kicker">Asia/Kolkata session</span><h2>Time of day</h2></div></div><div className="analysis-table-wrap"><table className="analysis-table"><thead><tr><th>Bucket</th><th>n</th><th>≤2h</th><th>≤24h</th><th>OPEN</th><th>Median target</th><th>Median MAE</th></tr></thead><tbody>{analysis.timeOfDayAnalysis.map((row) => <tr key={row.time_of_day_bucket}><td>{row.time_of_day_bucket}</td><td>{row.observations.toLocaleString("en-IN")}</td><td>{number(row.good_le2h_pct)}%</td><td>{number(row.target_le24h_pct)}%</td><td>{number(row.open_pct)}%</td><td>{duration(row.median_target_minutes)}</td><td>{number(row.median_mae_pct)}%</td></tr>)}</tbody></table></div></section>
    </div>

    <section className="backtest-panel feature-download-panel"><div><span className="section-kicker">Reproducible outputs</span><h2>Download server reports</h2><p>NIFTY context: {analysis.metadata.niftyContextAvailable ? "available" : "unavailable"}. Sector context: unavailable because this project has no reliable sector mapping.</p></div><div className="export-actions">{["recovery_signal_features.parquet", "recovery_signal_features.csv", "recovery_feature_separation.csv", "recovery_feature_analysis.json"].map((filename) => <a key={filename} href={`/api/recovery-analysis?filename=${encodeURIComponent(filename)}`}><Download size={13} />{filename.replace("recovery_", "")}</a>)}</div></section>
  </div>;
}
