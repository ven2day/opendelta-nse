"use client";
/* eslint-disable @next/next/no-html-link-for-pages -- Matches the production shell navigation. */

import { LayoutDashboard, LogOut, Radio, Save, Settings2, TrendingUp } from "lucide-react";
import { FormEvent, useState } from "react";
import {
  DEFAULT_GLOBAL_PRICE_RANGE,
  GLOBAL_PRICE_MAXIMUM,
  formatGlobalPriceRange,
  parseGlobalSettings,
  type GlobalSettingsPayload,
} from "../global-settings-shared";

type Props = {
  initialSettings: GlobalSettingsPayload;
  userName: string;
  signOutHref: string;
};

function validateDraft(minimum: string, maximum: string): string | null {
  if (minimum.trim() === "" || maximum.trim() === "") return "Enter both a minimum and maximum price.";
  const low = Number(minimum);
  const high = Number(maximum);
  if (!Number.isFinite(low) || !Number.isFinite(high)) return "Enter valid numeric prices.";
  if (low < 0 || high > GLOBAL_PRICE_MAXIMUM) return `Enter prices from ₹0 to ₹${GLOBAL_PRICE_MAXIMUM.toLocaleString("en-IN")}.`;
  if (low >= high) return "Minimum price must be less than maximum price.";
  return null;
}

/** The global price range form on its own, so Settings can embed it and /admin can wrap it in the legacy shell. */
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
      setNotice(`Saved. Dashboard, Signals and Backtest now use ${formatGlobalPriceRange(saved.priceRange)}.`);
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
    <div className="admin-scope-note"><strong>What changes</strong><span>Dashboard rows and counts, Signal observation cards, and the available Backtest symbol universe.</span><strong>What does not change</strong><span>Signal generation, paper positions, strategy calculations, saved results, or historical candles.</span></div>
  </form>;
}

export function AdminSettings({ initialSettings, userName, signOutHref }: Props) {
  const initials = userName.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();

  return <div className="site-shell backtest-shell admin-shell">
    <header className="global-header"><div className="header-inner">
      <a className="brand" href="/"><div className="brand-mark" aria-hidden="true">₹</div><div><strong>OpenDelta</strong><span>Market intelligence</span></div></a>
      <nav className="top-nav" aria-label="Main navigation">
        <a className="nav-item" href="/legacy/screener"><LayoutDashboard size={16} />Dashboard</a>
        <a className="nav-item" href="/legacy/backtest"><TrendingUp size={16} />Backtest</a>
        <a className="nav-item" href="/legacy/signals"><Radio size={16} />Signals</a>
        <a className="nav-item active" href="/admin" aria-current="page"><Settings2 size={16} />Admin</a>
      </nav>
      <div className="header-actions"><div className="user-chip"><div className="avatar">{initials}</div><span>{userName}</span></div><a href={signOutHref} className="icon-button" aria-label="Sign out"><LogOut size={17} /></a></div>
    </div></header>
    <main className="main-content admin-main">
      <section className="admin-intro"><span className="section-kicker">Application-wide settings</span><h1>Global price range</h1><p>Control which currently priced symbols are shown in Dashboard and Signals and offered to Backtest. Boundaries are inclusive.</p></section>
      <GlobalPriceRangeForm initialSettings={initialSettings} />
    </main>
  </div>;
}
