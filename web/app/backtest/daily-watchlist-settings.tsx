"use client";

import { ChevronDown } from "lucide-react";
import {
  NumericField,
  parameterDefinitions,
  type ParameterDefinition,
} from "./strategy-parameters";
import { TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY } from "./top-5-opening-range-breakout-contract.mjs";

export { TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY };
export type Top5OpeningRangeBreakoutSettings = Record<string, number | string | boolean>;

type Props = {
  settings: Top5OpeningRangeBreakoutSettings;
  onChange: (key: string, value: number | string | boolean) => void;
  onValidityChange: (key: string, error: string | null) => void;
  cachePolicy: "USE_CACHE" | "RUN_AGAIN";
  onCachePolicyChange: (value: "USE_CACHE" | "RUN_AGAIN") => void;
};

const definitions = parameterDefinitions.filter(
  (definition) => definition.strategy === TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY,
);

function SelectField({ definition, value, onChange }: {
  definition: ParameterDefinition;
  value: string | number;
  onChange: (value: string | number) => void;
}) {
  return <label className="parameter-field" title={definition.description}>
    <span className="parameter-label">{definition.label}</span>
    <small className="parameter-description">{definition.description}</small>
    <select value={String(value)} onChange={(event) => onChange(event.target.value)}>
      {(definition.options ?? []).map((option) => <option key={String(option)} value={String(option)}>{String(option).replaceAll("_", " ")}</option>)}
    </select>
  </label>;
}

function Field({ definition, settings, onChange, onValidityChange }: Props & { definition: ParameterDefinition }) {
  const value = settings[definition.key] ?? definition.default;
  if (definition.type === "number" || definition.type === "integer") {
    return <NumericField
      strategy={TOP_5_OPENING_RANGE_BREAKOUT_STRATEGY_KEY}
      parameterKey={definition.key}
      value={Number(value)}
      onValueChange={(next) => onChange(definition.key, next)}
      onValidityChange={onValidityChange}
      disabled={definition.key === "quantityPerTrade"}
    />;
  }
  if (definition.type === "select") {
    return <SelectField definition={definition} value={value as string | number} onChange={(next) => onChange(definition.key, next)} />;
  }
  if (definition.type === "boolean") {
    return <label className="parameter-field parameter-checkbox" title={definition.description}>
      <span className="parameter-label">{definition.label}</span>
      <small className="parameter-description">{definition.description}</small>
      <span><input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(definition.key, event.target.checked)} /> Enabled</span>
    </label>;
  }
  return <label className="parameter-field" title={definition.description}>
    <span className="parameter-label">{definition.label}</span>
    <small className="parameter-description">{definition.description}</small>
    <input type="time" value={String(value)} onChange={(event) => onChange(definition.key, event.target.value)} />
  </label>;
}

function Fields({ keys, ...props }: Props & { keys: string[] }) {
  return <div className="market-settings-grid">
    {keys.map((key) => {
      const definition = definitions.find((item) => item.key === key);
      return definition ? <Field key={key} definition={definition} {...props} /> : null;
    })}
  </div>;
}

const basicKeys = ["watchlistMode", "quantityPerTrade", "maximumTradesPerDay"];
const watchlistKeys = definitions.filter((item) => item.visibility === "watchlist").map((item) => item.key);
const openingKeys = definitions.filter((item) => item.visibility === "opening").map((item) => item.key);
const middayKeys = definitions.filter((item) => item.visibility === "midday").map((item) => item.key);
const riskKeys = definitions.filter((item) => item.visibility === "risk").map((item) => item.key);
const advancedKeys = definitions.filter((item) => item.visibility === "advanced").map((item) => item.key);

export function Top5OpeningRangeBreakoutSettingsPanel(props: Props) {
  return <div className="market-aligned-settings daily-watchlist-settings">
    <section className="market-settings-card market-main-settings" aria-labelledby="watchlist-basic-settings">
      <div className="panel-title"><div><span className="section-kicker">Always visible</span><h2 id="watchlist-basic-settings">Basic settings</h2></div></div>
      <Fields keys={basicKeys} {...props} />
      <p className="market-main-settings-note">Top-5 Opening Range Breakout research and paper signals only. Every executed backtest trade uses exactly 50 shares; live broker orders are disabled.</p>
    </section>
    <details className="market-settings-card market-settings-section">
      <summary><span><strong>Watchlist selection</strong><small>Opening selection, rolling rescans and replacement controls</small></span><ChevronDown size={17} /></summary>
      <Fields keys={watchlistKeys} {...props} />
    </details>
    <details className="market-settings-card market-settings-section">
      <summary><span><strong>Opening range breakout</strong><small>Completed 09:15–09:30 range and causal next-bar entry</small></span><ChevronDown size={17} /></summary>
      <Fields keys={openingKeys} {...props} />
    </details>
    <details className="market-settings-card market-settings-section">
      <summary><span><strong>Midday breakout</strong><small>Six-bar breakout rules for newly promoted symbols</small></span><ChevronDown size={17} /></summary>
      <Fields keys={middayKeys} {...props} />
    </details>
    <details className="market-settings-card market-settings-section">
      <summary><span><strong>Exit and risk</strong><small>Causal stop, 1.5R target and portfolio limits</small></span><ChevronDown size={17} /></summary>
      <Fields keys={riskKeys} {...props} />
    </details>
    <details className="market-settings-card market-settings-section">
      <summary><span><strong>Advanced settings</strong><small>Indicators, liquidity, costs and cache policy</small></span><ChevronDown size={17} /></summary>
      <Fields keys={advancedKeys} {...props} />
      <label className="parameter-field">
        <span className="parameter-label">Identical completed run</span>
        <small className="parameter-description">Reuse only when configuration, code, universe and source-data fingerprints match.</small>
        <select value={props.cachePolicy} onChange={(event) => props.onCachePolicyChange(event.target.value as "USE_CACHE" | "RUN_AGAIN")}><option value="USE_CACHE">Use cached result</option><option value="RUN_AGAIN">Run again</option></select>
      </label>
    </details>
  </div>;
}
