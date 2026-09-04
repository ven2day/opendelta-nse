"use client";

import { Save } from "lucide-react";
import { useState, type FormEvent } from "react";
import {
  DEFAULT_GLOBAL_PRICE_RANGE,
  GLOBAL_PRICE_MAXIMUM,
  formatGlobalPriceRange,
  parseGlobalSettings,
  type GlobalSettingsPayload,
} from "../global-settings-shared";

function validateDraft(minimum: string, maximum: string): string | null {
  if (minimum.trim() === "" || maximum.trim() === "") return "Enter both a minimum and maximum price.";
  const low = Number(minimum);
  const high = Number(maximum);
  if (!Number.isFinite(low) || !Number.isFinite(high)) return "Enter valid numeric prices.";
  if (low < 0 || high > GLOBAL_PRICE_MAXIMUM) return `Enter prices from ₹0 to ₹${GLOBAL_PRICE_MAXIMUM.toLocaleString("en-IN")}.`;
  if (low >= high) return "Minimum price must be less than maximum price.";
  return null;
}

/** The application-wide current-price range form, embedded in the unified Settings workspace. */
export function GlobalPriceRangeForm({ initialSettings }: { initialSettings: GlobalSettingsPayload }) {
  const [settings, setSettings] = useState(initialSettings);
  const [minimum, setMinimum] = useState(String(initialSettings.priceRange.minimumPrice));
  const [maximum, setMaximum] = useState(String(initialSettings.priceRange.maximumPrice));
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const validation = validateDraft(minimum, maximum);
    setError(validation);
    setNotice(null);
    if (validation) return;
    setSaving(true);
    try {
      const response = await fetch("/api/global-settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ minimumPrice: Number(minimum), maximumPrice: Number(maximum) }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Unable to save global settings");
      const saved = parseGlobalSettings(body);
      setSettings(saved);
      setMinimum(String(saved.priceRange.minimumPrice));
      setMaximum(String(saved.priceRange.maximumPrice));
      setNotice(`Saved. Screeners and backtests now use ${formatGlobalPriceRange(saved.priceRange)}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to save global settings");
    } finally {
      setSaving(false);
    }
  };

  return <form className="backtest-panel admin-settings-card" onSubmit={save} noValidate>
    <div className="panel-title"><div><span className="section-kicker">Symbol visibility</span><h2>Current market price</h2></div><span className="global-range-badge">{formatGlobalPriceRange(settings.priceRange)}</span></div>
    <div className="admin-price-grid">
      <label><span>Minimum price</span><small>Symbols below this current price are hidden.</small><div className="admin-price-input"><b>₹</b><input aria-label="Global minimum price" type="number" min="0" max={GLOBAL_PRICE_MAXIMUM} step="0.01" inputMode="decimal" value={minimum} onChange={(event) => { setMinimum(event.target.value); setError(null); }} onBlur={() => setError(validateDraft(minimum, maximum))} /></div></label>
      <label><span>Maximum price</span><small>Symbols above this current price are hidden.</small><div className="admin-price-input"><b>₹</b><input aria-label="Global maximum price" type="number" min="0.01" max={GLOBAL_PRICE_MAXIMUM} step="0.01" inputMode="decimal" value={maximum} onChange={(event) => { setMaximum(event.target.value); setError(null); }} onBlur={() => setError(validateDraft(minimum, maximum))} /></div></label>
    </div>
    {error && <p className="admin-message error" role="alert">{error}</p>}
    {notice && <p className="admin-message success" role="status">{notice}</p>}
    <div className="admin-actions"><button type="button" onClick={() => { setMinimum(String(DEFAULT_GLOBAL_PRICE_RANGE.minimumPrice)); setMaximum(String(DEFAULT_GLOBAL_PRICE_RANGE.maximumPrice)); setError(null); setNotice("All-price defaults loaded. Click Save settings to apply."); }}>Reset to all prices</button><button className="run-backtest" type="submit" disabled={saving}><Save size={16} />{saving ? "Saving…" : "Save settings"}</button></div>
    <div className="admin-scope-note"><strong>What changes</strong><span>The symbol universe offered to screener and backtest runs.</span><strong>What does not change</strong><span>Signal generation, paper positions, strategy calculations, saved results, or historical candles.</span></div>
  </form>;
}