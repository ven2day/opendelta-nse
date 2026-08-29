"use client";

import { ChevronDown } from "lucide-react";
import {
  NumericField,
  parameterDefinitions,
  type ParameterDefinition,
} from "./strategy-parameters";

export const VWAP_PULLBACK_STRATEGY_KEY = "market_aligned_vwap_pullback_scalper";

export type VwapPullbackSettings = Record<string, number | string | boolean>;

type Props = {
  settings: VwapPullbackSettings;
  onChange: (key: string, value: number | string | boolean) => void;
  onValidityChange: (key: string, error: string | null) => void;
  cachePolicy: "USE_CACHE" | "RUN_AGAIN";
  onCachePolicyChange: (value: "USE_CACHE" | "RUN_AGAIN") => void;
};

const definitions = parameterDefinitions.filter(
  (definition) => definition.strategy === VWAP_PULLBACK_STRATEGY_KEY,
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
      strategy={VWAP_PULLBACK_STRATEGY_KEY}
      parameterKey={definition.key}
      value={Number(value)}
      onValueChange={(next) => onChange(definition.key, next)}
      onValidityChange={onValidityChange}
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

const basicKeys = ["positionSizing", "quantityPerTrade", "rupeeRiskBudget", "maximumTradesPerDay"];
const entryKeys = definitions.filter((item) => item.visibility === "entry").map((item) => item.key);
const riskKeys = definitions.filter((item) => item.visibility === "risk").map((item) => item.key);
const marketKeys = definitions.filter((item) => item.visibility === "market").map((item) => item.key);
const advancedKeys = definitions.filter((item) => item.visibility === "advanced").map((item) => item.key);

export function VwapPullbackSettingsPanel(props: Props) {
  const sizing = String(props.settings.positionSizing ?? "FIXED_QUANTITY");
  const visibleBasic = basicKeys.filter((key) => key !== (sizing === "RISK_BUDGET" ? "quantityPerTrade" : "rupeeRiskBudget"));
  return <div className="market-aligned-settings vwap-pullback-settings">
    <section className="market-settings-card market-main-settings" aria-labelledby="vwap-basic-settings">
      <div className="panel-title"><div><span className="section-kicker">Always visible</span><h2 id="vwap-basic-settings">Basic settings</h2></div></div>
      <Fields keys={visibleBasic} {...props} />
    </section>
    <details className="market-settings-card market-settings-section">
      <summary><span><strong>Entry rules</strong><small>Completed-candle pullback and trigger rules</small></span><ChevronDown size={17} /></summary>
      <Fields keys={entryKeys} {...props} />
    </details>
    <details className="market-settings-card market-settings-section">
      <summary><span><strong>Exit and risk</strong><small>Frozen ATR exits and portfolio limits</small></span><ChevronDown size={17} /></summary>
      <Fields keys={riskKeys} {...props} />
    </details>
    <details className="market-settings-card market-settings-section">
      <summary><span><strong>Market context</strong><small>One mandatory NIFTY safety rule; other context ranks candidates</small></span><ChevronDown size={17} /></summary>
      <Fields keys={marketKeys} {...props} />
      <p className="market-main-settings-note">OI is optional advisory context only and defaults to OFF. Missing context is not treated as bearish.</p>
    </details>
    <details className="market-settings-card market-settings-section">
      <summary><span><strong>Advanced settings</strong><small>Costs, liquidity and research assumptions</small></span><ChevronDown size={17} /></summary>
      <Fields keys={advancedKeys} {...props} />
      <label className="parameter-field">
        <span className="parameter-label">Identical completed run</span>
        <small className="parameter-description">Reuse only when configuration, code, universe and source-data fingerprints match.</small>
        <select value={props.cachePolicy} onChange={(event) => props.onCachePolicyChange(event.target.value as "USE_CACHE" | "RUN_AGAIN")}><option value="USE_CACHE">Use cached result</option><option value="RUN_AGAIN">Run again</option></select>
      </label>
    </details>
  </div>;
}
