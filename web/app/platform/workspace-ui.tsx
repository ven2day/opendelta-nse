import type { ReactNode } from "react";
import { AlertTriangle, LoaderCircle } from "lucide-react";

export function WorkspaceHeader({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description: string; actions?: ReactNode }) {
  return <header className="quant-workspace-header"><div><span>{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{actions}</header>;
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

export function StatusBadge({ children, tone = "neutral" }: { children: ReactNode; tone?: "good" | "warn" | "bad" | "neutral" }) {
  return <span className={`quant-badge ${tone}`}>{children}</span>;
}
