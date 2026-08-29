"use client";

import { useState } from "react";
import type { ParameterDefinition } from "./strategy-parameters";
import {
  formatJsonConfiguration,
  parseAndValidateJsonConfiguration,
} from "./json-configuration.mjs";

type JsonEnvelope = {
  schemaVersion: number;
  strategyKey: string;
  settings: Record<string, unknown>;
};

type ValidationResult = {
  valid: boolean;
  errors: string[];
  configuration: JsonEnvelope | null;
  summary: { changed: number; unchanged: number; errors: number } | null;
  belongsToStrategyKey?: string;
  belongsToStrategyName?: string;
};

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const object = value as Record<string, unknown>;
    return `{${Object.keys(object).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(object[key])}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

async function configurationHash(configuration: JsonEnvelope): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(canonicalJson(configuration)),
  );
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

export function JsonConfigurationEditor({
  configuration,
  definitions,
  strategyNames,
  onApply,
  onReset,
  onSwitchStrategy,
}: {
  configuration: JsonEnvelope;
  definitions: ParameterDefinition[];
  strategyNames: Record<string, string>;
  onApply: (settings: Record<string, unknown>) => string | null;
  onReset: () => void;
  onSwitchStrategy: (strategyKey: string) => void;
}) {
  const resolvedText = formatJsonConfiguration(configuration);
  const [draft, setDraft] = useState<string | null>(null);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const editorText = draft ?? resolvedText;

  const validateDraft = (): ValidationResult => {
    const result = parseAndValidateJsonConfiguration(editorText, {
      strategyKey: configuration.strategyKey,
      strategyNames,
      definitions,
      currentSettings: configuration.settings,
    }) as ValidationResult;
    setValidation(result);
    setMessage(result.valid && result.summary
      ? `${result.summary.changed} settings changed · ${result.summary.unchanged} settings unchanged · 0 errors`
      : null);
    return result;
  };

  const copyJson = async () => {
    try {
      await navigator.clipboard.writeText(resolvedText);
      setMessage("Complete resolved configuration copied.");
    } catch {
      setMessage("Clipboard access is unavailable. Select the JSON and copy it manually.");
    }
  };

  const pasteJson = async () => {
    try {
      setDraft(await navigator.clipboard.readText());
      setValidation(null);
      setMessage("JSON pasted. Validate it before applying.");
    } catch {
      setMessage("Clipboard access is unavailable. Paste directly into the editor.");
    }
  };

  const formatDraft = () => {
    try {
      setDraft(JSON.stringify(JSON.parse(editorText), null, 2));
      setValidation(null);
      setMessage("JSON formatted. Validate it before applying.");
    } catch {
      validateDraft();
    }
  };

  const applyDraft = () => {
    const result = validateDraft();
    if (!result.valid || !result.configuration || !result.summary) return;
    const applicationError = onApply(result.configuration.settings);
    if (applicationError) {
      setValidation({ ...result, valid: false, errors: [applicationError] });
      setMessage(null);
      return;
    }
    setDraft(null);
    setMessage(`Configuration applied · ${result.summary.changed} settings changed · calculating configuration hash…`);
    void configurationHash(result.configuration).then((hash) => {
      setMessage(`Configuration applied · Configuration hash ${hash.slice(0, 16)} · Effective settings ${Object.keys(result.configuration!.settings).length}`);
    }).catch(() => {
      setMessage(`Configuration applied · ${result.summary!.changed} settings changed · ${result.summary!.unchanged} settings unchanged`);
    });
  };

  return (
    <details className="json-configuration advanced-settings">
      <summary>JSON configuration</summary>
      <div className="json-configuration-body">
        <div className="json-configuration-toolbar" aria-label="JSON configuration actions">
          <button type="button" onClick={() => void copyJson()}>Copy JSON</button>
          <button type="button" onClick={() => void pasteJson()}>Paste from clipboard</button>
          <button type="button" onClick={formatDraft}>Format</button>
          <button type="button" onClick={validateDraft}>Validate</button>
          <button type="button" className="primary" onClick={applyDraft}>Apply</button>
          <button type="button" onClick={() => { onReset(); setDraft(null); setValidation(null); setMessage("Recommended defaults restored."); }}>Reset</button>
        </div>
        <textarea
          aria-label={`${strategyNames[configuration.strategyKey] ?? configuration.strategyKey} JSON configuration`}
          value={editorText}
          wrap="off"
          spellCheck={false}
          onChange={(event) => {
            setDraft(event.target.value);
            setValidation(null);
            setMessage(null);
          }}
        />
        {validation && !validation.valid && (
          <div className="json-configuration-errors" role="alert">
            {validation.errors.map((error) => <span key={error}>{error}</span>)}
            {validation.belongsToStrategyKey && strategyNames[validation.belongsToStrategyKey] && (
              <button type="button" onClick={() => onSwitchStrategy(validation.belongsToStrategyKey!)}>
                Switch to {validation.belongsToStrategyName}
              </button>
            )}
          </div>
        )}
        {message && <div className="json-configuration-message" aria-live="polite">{message}</div>}
        <small>Includes basic, advanced and expert settings for this strategy. Credentials and server configuration are never included.</small>
      </div>
    </details>
  );
}
