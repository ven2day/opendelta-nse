"use client";

import { humanize } from "./format";

export type SchemaField = {
  type: "integer" | "number" | "boolean" | "string";
  default?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: Array<string | number>;
  label?: string;
  description?: string;
};

export type ConfigSchema = Record<string, SchemaField>;
export type ConfigValues = Record<string, unknown>;

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
  if (field.type === "boolean") {
    return <label className="checkbox"><input type="checkbox" checked={Boolean(value)} disabled={disabled} onChange={(event) => onChange(event.target.checked)} /><span>{label}</span>{field.description && <small>{field.description}</small>}</label>;
  }
  if (field.type === "string") {
    if (field.enum?.length) {
      return <label><span>{label}</span><select value={String(value ?? "")} disabled={disabled} onChange={(event) => onChange(event.target.value)}>{field.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select>{field.description && <small>{field.description}</small>}</label>;
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

/** Drops empty numeric inputs so optional fields are omitted rather than sent as empty strings. */
export function compactValues(values: ConfigValues): ConfigValues {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== "" && value !== undefined && value !== null));
}
