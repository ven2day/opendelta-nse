"use client";

import { humanize } from "./format";

export type SchemaField = {
  type: "integer" | "integer_array" | "number" | "boolean" | "string";
  default?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: Array<string | number>;
  label?: string;
  description?: string;
  minItems?: number;
  maxItems?: number;
};

export type ConfigSchema = Record<string, SchemaField>;
export type ConfigValues = Record<string, unknown>;

/** Rejects unknown keys and values that do not match a published configuration schema. */
export function validateConfigValues(values: ConfigValues, schema: ConfigSchema, label = "Configuration"): void {
  const unknownKeys = Object.keys(values).filter((key) => !(key in schema));
  if (unknownKeys.length) throw new Error(`${label} contains unknown setting${unknownKeys.length === 1 ? "" : "s"}: ${unknownKeys.join(", ")}.`);
  for (const [key, value] of Object.entries(values)) {
    const field = schema[key];
    const validType = field.type === "integer_array"
      ? Array.isArray(value) && value.every((item) => typeof item === "number" && Number.isInteger(item))
      : field.type === "integer"
      ? typeof value === "number" && Number.isInteger(value)
      : field.type === "number"
        ? typeof value === "number" && Number.isFinite(value)
        : typeof value === field.type;
    if (!validType) throw new Error(`${label}.${key} must be ${field.type === "integer" ? "an integer" : `a ${field.type}`}.`);
    if (field.enum && !field.enum.some((option) => option === value)) throw new Error(`${label}.${key} must be one of: ${field.enum.join(", ")}.`);
    if (field.type === "integer_array" && Array.isArray(value)) {
      if (field.minItems !== undefined && value.length < field.minItems) throw new Error(`${label}.${key} needs at least ${field.minItems} values.`);
      if (field.maxItems !== undefined && value.length > field.maxItems) throw new Error(`${label}.${key} allows at most ${field.maxItems} values.`);
      if (field.minimum !== undefined && value.some((item) => Number(item) < field.minimum!)) throw new Error(`${label}.${key} values must be at least ${field.minimum}.`);
      if (field.maximum !== undefined && value.some((item) => Number(item) > field.maximum!)) throw new Error(`${label}.${key} values must be at most ${field.maximum}.`);
    }
    if (typeof value === "number" && field.minimum !== undefined && value < field.minimum) throw new Error(`${label}.${key} must be at least ${field.minimum}.`);
    if (typeof value === "number" && field.maximum !== undefined && value > field.maximum) throw new Error(`${label}.${key} must be at most ${field.maximum}.`);
  }
}

/** Builds the initial values for a schema: explicit overrides first, then the strategy defaults, then field defaults. */
export function schemaDefaults(schema: ConfigSchema, ...overrides: Array<ConfigValues | null | undefined>): ConfigValues {
  const values: ConfigValues = {};
  for (const [key, field] of Object.entries(schema)) {
    const override = overrides.find((candidate) => candidate && candidate[key] !== undefined)?.[key];
    // Fields without any default stay empty ("") so optional numbers are omitted rather than sent as zero.
    values[key] = override !== undefined ? override : field.default ?? (field.type === "boolean" ? false : field.type === "string" ? (field.enum?.[0] ?? "") : "");
  }
  return values;
}

function parseNumber(raw: string, integer: boolean): number | "" {
  if (raw.trim() === "") return "";
  const value = integer ? Number.parseInt(raw, 10) : Number(raw);
  return Number.isFinite(value) ? value : "";
}

/** Renders one form control for a schema field; never hard-codes strategy-specific inputs. */
export function SchemaFieldInput({ name, field, value, onChange, disabled }: { name: string; field: SchemaField; value: unknown; onChange: (next: unknown) => void; disabled?: boolean }) {
  const label = field.label ?? humanize(name);
  if (field.type === "integer_array") {
    const current = Array.isArray(value) ? value.join(", ") : "";
    return <label><span>{label}</span><input type="text" inputMode="numeric" value={current} disabled={disabled} onChange={(event) => onChange(event.target.value.split(",").map((item) => Number(item.trim())).filter(Number.isInteger))} />{field.description && <small>{field.description}</small>}</label>;
  }
  if (field.type === "boolean") {
    return <label className="checkbox"><input type="checkbox" checked={Boolean(value)} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><span>{label}</span>{field.description && <small>{field.description}</small>}</label>;
  }
  if (field.type === "string") {
    if (field.enum?.length) {
      const current = String(value ?? "");
      return <label><span>{label}</span><select value={current} disabled={disabled} onChange={(event) => onChange(event.target.value)}>{!field.enum.some((option) => String(option) === current) && <option value="">—</option>}{field.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select>{field.description && <small>{field.description}</small>}</label>;
    }
    return <label><span>{label}</span><input type="text" value={String(value ?? "")} disabled={disabled} onChange={(event) => onChange(event.target.value)} />{field.description && <small>{field.description}</small>}</label>;
  }
  const integer = field.type === "integer";
  if (field.enum?.length) {
    return <label><span>{label}</span><select value={String(value ?? "")} disabled={disabled} onChange={(event) => onChange(parseNumber(event.target.value, integer))}>{field.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select>{field.description && <small>{field.description}</small>}</label>;
  }
  return <label><span>{label}</span><input type="number" inputMode={integer ? "numeric" : "decimal"} step={integer ? 1 : "any"} min={field.minimum} max={field.maximum} value={typeof value === "number" ? value : ""} disabled={disabled} onChange={(event) => onChange(parseNumber(event.target.value, integer))} />{(field.description || field.minimum !== undefined || field.maximum !== undefined) && <small>{field.description ?? `${field.minimum !== undefined ? `min ${field.minimum}` : ""}${field.minimum !== undefined && field.maximum !== undefined ? " · " : ""}${field.maximum !== undefined ? `max ${field.maximum}` : ""}`}</small>}</label>;
}

/** A complete strategy configuration form generated from `configSchema`. */
export function SchemaForm({ schema, values, onChange, disabled }: { schema: ConfigSchema; values: ConfigValues; onChange: (next: ConfigValues) => void; disabled?: boolean }) {
  const entries = Object.entries(schema);
  if (!entries.length) return <p className="quant-inline-note">This strategy exposes no configurable parameters.</p>;
  return <div className="quant-form-grid">{entries.map(([name, field]) => <SchemaFieldInput key={name} name={name} field={field} value={values[name]} disabled={disabled} onChange={(next) => onChange({ ...values, [name]: next })} />)}</div>;
}

/** Infers a schema from a flat defaults object (used for risk and execution settings the API describes only by defaults). */
export function schemaFromValues(values: ConfigValues, labels?: Record<string, string>): ConfigSchema {
  const schema: ConfigSchema = {};
  for (const [key, value] of Object.entries(values)) {
    const label = labels?.[key];
    if (typeof value === "boolean") schema[key] = { type: "boolean", default: value, label };
    else if (typeof value === "number") schema[key] = { type: Number.isInteger(value) ? "integer" : "number", default: value, label, minimum: 0 };
    else schema[key] = { type: "string", default: value === null || value === undefined ? "" : String(value), label };
  }
  return schema;
}

/** Drops empty inputs so optional fields (null defaults) are omitted rather than sent as empty strings or null. */
export function compactValues(values: ConfigValues): ConfigValues {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== "" && value !== undefined && value !== null));
}

/** Keeps only allow-listed keys, in allow-list order, so a request body matches its contract exactly. */
export function pickValues(values: ConfigValues, keys: readonly string[]): ConfigValues {
  return Object.fromEntries(keys.filter((key) => key in values).map((key) => [key, values[key]]));
}
