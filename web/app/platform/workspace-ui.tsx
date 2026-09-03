import type { ReactNode } from "react";
import { AlertTriangle, DatabaseZap, LoaderCircle } from "lucide-react";
import { formatSignedMoney } from "./format";
import type { PlatformMarket } from "./platform-client";
import { isPlatformUnconfigured } from "./v2-client";

export function WorkspaceHeader({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description?: string; actions?: ReactNode }) {
  return <header className="quant-workspace-header"><div><span>{eyebrow}</span><h1>{title}</h1>{description && <p>{description}</p>}</div>{actions}</header>;
}

export function LoadingState({ label = "Loading current platform state" }: { label?: string }) {
  return <div className="quant-state" role="status"><LoaderCircle className="spin" size={20} /><span>{label}</span></div>;
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="quant-state error" role="alert"><AlertTriangle size={20} /><div><strong>Unable to load this workspace</strong><span>{message}</span></div>{retry && <button type="button" onClick={retry}>Retry</button>}</div>;
}

export function EmptyState({ title, description }: { title: string; description: string }) {
  return <div className="quant-state empty"><div><strong>{title}</strong><span>{description}</span></div></div>;
}

/** Rendered when the platform answers 503: the unified platform database is not configured on the service. */
export function UnconfiguredState({ detail }: { detail?: string }) {
  return <div className="quant-state empty unconfigured" role="status"><DatabaseZap size={22} /><div><strong>Unified platform database not configured</strong><span>{detail || "The v2 platform service has no database configured. Legacy tools remain available from Settings."}</span></div></div>;
}

/** Picks the right state for a failed request: configuration problem versus transient error. */
export function RequestErrorState({ error, retry }: { error: Error; retry?: () => void }) {
  return isPlatformUnconfigured(error) ? <UnconfiguredState detail={error.message} /> : <ErrorState message={error.message} retry={retry} />;
}

/** Inline failure for one dashboard section while the rest of the page keeps rendering. */
export function SectionError({ message }: { message?: string | null }) {
  return <div className="quant-state error" role="alert"><AlertTriangle size={18} /><div><strong>Section unavailable</strong><span>{message || "The platform could not load this section."}</span></div></div>;
}

export function StatusBadge({ children, tone = "neutral" }: { children: ReactNode; tone?: "good" | "warn" | "bad" | "neutral" }) {
  return <span className={`quant-badge ${tone}`}>{children}</span>;
}

export function PaperOnlyBadge() {
  return <StatusBadge tone="good">Paper only · broker execution disabled</StatusBadge>;
}

export function Panel({ icon, title, description, aside, children, className }: { icon?: ReactNode; title: string; description?: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`quant-panel${className ? ` ${className}` : ""}`}><div className="quant-panel-heading"><div>{icon}<div><h2>{title}</h2>{description && <p>{description}</p>}</div></div>{aside}</div>{children}</section>;
}

export function Tag({ children, tone }: { children: ReactNode; tone?: "good" | "warn" | "bad" | "neutral" }) {
  return <span className={`quant-tag${tone ? ` ${tone}` : ""}`}>{children}</span>;
}

/** Signed, currency-aware profit/loss with positive/negative colouring. */
export function PnlValue({ value, market, currency }: { value: unknown; market: PlatformMarket; currency?: string }) {
  const direction = typeof value === "number" ? (value > 0 ? "positive" : value < 0 ? "negative" : "") : "";
  return <span className={`quant-pnl ${direction}`.trim()}>{formatSignedMoney(value, market, currency)}</span>;
}

export function SymbolTags({ symbols, limit = 40 }: { symbols: string[]; limit?: number }) {
  if (!symbols.length) return <span className="quant-inline-note">No symbols</span>;
  const shown = symbols.slice(0, limit);
  return <div className="quant-tag-list">{shown.map((symbol) => <Tag key={symbol}>{symbol}</Tag>)}{symbols.length > shown.length && <Tag>+{symbols.length - shown.length} more</Tag>}</div>;
}

export function Message({ kind, children }: { kind: "success" | "error"; children: ReactNode }) {
  return <p className={`quant-message ${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</p>;
}
