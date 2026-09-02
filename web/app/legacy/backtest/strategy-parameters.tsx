"use client";

import { useState } from "react";
import definitionsJson from "../../../strategy-parameters.json";

export type ParameterStrategy = string;
export type ParameterDefinition = {
  key: string;
  label: string;
  description: string;
  type: "number" | "integer" | "select" | "time" | "boolean";
  default: number | string | boolean;
  options?: Array<number | string>;
  minimum: number | null;
  maximum: number | null;
  step: number | null;
  unit: string;
  category: string;
  visibility: string;
  strategy: ParameterStrategy;
};

export const parameterDefinitions = definitionsJson as ParameterDefinition[];

export function parameterDefinition(strategy: ParameterStrategy, key: string): ParameterDefinition {
  const definition = parameterDefinitions.find((item) => item.strategy === strategy && item.key === key);
  if (!definition) throw new Error(`Missing parameter definition for ${strategy}.${key}`);
  return definition;
}

export function strategyDefaults(strategy: ParameterStrategy): Record<string, number | string | boolean> {
  return Object.fromEntries(
    parameterDefinitions
      .filter((item) => item.strategy === strategy)
      .map((item) => [item.key, item.default]),
  );
}

function boundLabel(definition: ParameterDefinition, value: number) {
  if (definition.unit === "%") return `${value}%`;
  return `${value}${definition.unit ? ` ${definition.unit}` : ""}`;
}

export function validateNumericText(definition: ParameterDefinition, text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed) return "Enter a value.";
  const value = Number(trimmed);
  if (!Number.isFinite(value)) return "Enter a valid number.";
  if (definition.type === "integer" && !Number.isInteger(value)) return "Enter a whole number.";
  if (definition.minimum !== null && value < definition.minimum) {
    return definition.maximum === null
      ? `Enter ${boundLabel(definition, definition.minimum)} or more.`
      : `Enter a value from ${boundLabel(definition, definition.minimum)} to ${boundLabel(definition, definition.maximum)}.`;
  }
  if (definition.maximum !== null && value > definition.maximum) {
    return definition.minimum === null
      ? `Enter ${boundLabel(definition, definition.maximum)} or less.`
      : `Enter a value from ${boundLabel(definition, definition.minimum)} to ${boundLabel(definition, definition.maximum)}.`;
  }
  if (definition.step !== null) {
    const quotient = value / definition.step;
    if (Math.abs(quotient - Math.round(quotient)) > 1e-8) {
      return definition.type === "integer"
        ? "Enter a whole number."
        : `Use increments of ${definition.step}${definition.unit ? ` ${definition.unit}` : ""}.`;
    }
  }
  return null;
}

export function NumericField({
  strategy,
  parameterKey,
  value,
  onValueChange,
  onValidityChange,
  onManualChange,
  externalError,
  disabled = false,
}: {
  strategy: ParameterStrategy;
  parameterKey: string;
  value: number;
  onValueChange: (value: number) => void;
  onValidityChange?: (key: string, error: string | null) => void;
  onManualChange?: () => void;
  externalError?: string | null;
  disabled?: boolean;
}) {
  const definition = parameterDefinition(strategy, parameterKey);
  const [editing, setEditing] = useState({ sourceValue: value, text: String(value) });
  const draft = editing.sourceValue === value ? editing.text : String(value);
  const [touched, setTouched] = useState(false);
  const localError = validateNumericText(definition, draft);
  const visibleError = touched ? externalError || localError : null;

  return (
    <label className={`parameter-field ${visibleError ? "invalid" : ""}`} title={definition.description}>
      <span className="parameter-label">{definition.label}</span>
      <small className="parameter-description">{definition.description}</small>
      <span className="parameter-input-row">
        <input
          aria-label={definition.label}
          aria-invalid={Boolean(visibleError)}
          type="number"
          inputMode={definition.type === "integer" ? "numeric" : "decimal"}
          min={definition.minimum ?? undefined}
          max={definition.maximum ?? undefined}
          step={definition.step ?? undefined}
          value={draft}
          disabled={disabled}
          onChange={(event) => {
            const next = event.target.value;
            onManualChange?.();
            const error = validateNumericText(definition, next);
            setEditing({ sourceValue: error ? value : Number(next), text: next });
            onValidityChange?.(parameterKey, error);
            if (!error) onValueChange(Number(next));
          }}
          onBlur={() => {
            setTouched(true);
            onValidityChange?.(parameterKey, validateNumericText(definition, draft));
          }}
        />
        {definition.unit && <i>{definition.unit}</i>}
      </span>
      {visibleError && <span className="parameter-error" role="alert">{visibleError}</span>}
    </label>
  );
}
