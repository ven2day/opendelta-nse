import type { PlatformMarket } from "./platform-client";

export const MARKETS: PlatformMarket[] = ["NSE", "CRYPTO"];

export function marketLabel(market: PlatformMarket): string {
  return market === "CRYPTO" ? "Crypto" : "NSE";
}

export function marketCurrency(market: PlatformMarket): string {
  return market === "CRYPTO" ? "USDT" : "INR";
}

export function marketTimeZone(market: PlatformMarket): string {
  return market === "CRYPTO" ? "UTC" : "Asia/Kolkata";
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatNumber(value: unknown, digits = 2): string {
  if (!finite(value)) return "—";
  return value.toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

export function formatInteger(value: unknown): string {
  return formatNumber(value, 0);
}

export function formatPercent(value: unknown, digits = 2): string {
  if (!finite(value)) return "—";
  return `${value.toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: digits })}%`;
}

/** Currency-aware money formatting: INR for NSE, USDT for crypto. */
export function formatMoney(value: unknown, market: PlatformMarket, currency = marketCurrency(market)): string {
  if (!finite(value)) return "—";
  if (currency === "INR") return value.toLocaleString("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
  return `${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}

export function formatSignedMoney(value: unknown, market: PlatformMarket, currency?: string): string {
  if (!finite(value)) return "—";
  const formatted = formatMoney(Math.abs(value), market, currency);
  return value < 0 ? `−${formatted}` : value > 0 ? `+${formatted}` : formatted;
}

export function formatDateTime(value: unknown, market: PlatformMarket): string {
  if (typeof value !== "string" || !value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: marketTimeZone(market),
  }).format(date);
}

export function formatDate(value: unknown): string {
  if (typeof value !== "string" || !value) return "—";
  return value.slice(0, 10);
}

export function formatAge(seconds: unknown): string {
  if (!finite(seconds)) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3_600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86_400) return `${(seconds / 3_600).toFixed(1)}h`;
  return `${(seconds / 86_400).toFixed(1)}d`;
}

export function formatMinutes(value: unknown): string {
  if (!finite(value)) return "—";
  if (value < 60) return `${Math.round(value)}m`;
  return `${(value / 60).toFixed(1)}h`;
}

/** Turns camelCase identifiers such as `minimumAverageVolume` into `Minimum average volume`. */
export function humanize(key: string): string {
  const spaced = key.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

export function shortId(value: unknown): string {
  return typeof value === "string" ? value.slice(0, 8) : "—";
}

export function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function tone(status: unknown): "good" | "warn" | "bad" | "neutral" {
  const value = String(status ?? "").toUpperCase();
  if (["HEALTHY", "FRESH", "RUNNING", "AVAILABLE", "COMPLETE", "COMPLETED", "ACTIVE", "CONNECTED", "FILLED", "TARGET_HIT", "READY", "STRONG_BUY"].includes(value)) return "good";
  if (["FAILED", "UNAVAILABLE", "INVALID", "ERROR", "REJECTED", "CANCELLED", "INTERRUPTED", "DISCONNECTED", "EXITED", "EXPIRED", "STOPPED_OUT"].includes(value)) return "bad";
  if (["STALE", "DEGRADED", "STOPPED", "QUEUED", "PENDING", "HOLDING", "OPEN", "CONNECTING", "MARKET_CLOSED", "IDLE"].includes(value)) return "warn";
  return "neutral";
}
