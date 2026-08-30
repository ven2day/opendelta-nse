"use client";

import {
  Activity,
  BarChart3,
  BriefcaseBusiness,
  Database,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  LogOut,
  Menu,
  Radio,
  ScanSearch,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { platformGet, type PlatformMarket } from "./platform-client";

type Overview = {
  dataFreshness?: { status?: string; ageSeconds?: number };
  jobStatus?: { status?: string; running?: number; queueDepth?: number };
  environment?: string;
};

const navigation = [
  { href: "/", label: "Overview", icon: LayoutDashboard, match: (path: string) => path === "/" },
  { href: "/markets", label: "Markets", icon: BarChart3, match: (path: string) => path.startsWith("/markets") },
  { href: "/scanner", label: "Screener", icon: ScanSearch, match: (path: string) => path.startsWith("/scanner") },
  { href: "/research", label: "Research Lab", icon: FlaskConical, match: (path: string) => path.startsWith("/research") },
  { href: "/strategies", label: "Strategies", icon: SlidersHorizontal, match: (path: string) => path.startsWith("/strategies") },
  { href: "/backtest", label: "Backtests", icon: Gauge, match: (path: string) => path.startsWith("/backtest") },
  { href: "/signals", label: "Signals", icon: Radio, match: (path: string) => path.startsWith("/signals") },
  { href: "/risk", label: "Risk", icon: ShieldCheck, match: (path: string) => path.startsWith("/risk") },
  { href: "/data-health", label: "Data Health", icon: Database, match: (path: string) => path.startsWith("/data-health") },
  { href: "/jobs", label: "Jobs", icon: BriefcaseBusiness, match: (path: string) => path.startsWith("/jobs") },
  { href: "/settings", label: "Settings", icon: Settings2, match: (path: string) => path.startsWith("/settings") || path.startsWith("/admin") },
];

const platformRoutePrefixes = [
  "/markets",
  "/research",
  "/strategies",
  "/risk",
  "/data-health",
  "/jobs",
  "/settings",
];

export function usesPlatformShell(pathname: string): boolean {
  return platformRoutePrefixes.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

function marketFor(path: string, selected?: string | null): PlatformMarket {
  if (path.startsWith("/markets") && selected === "CRYPTO") return "CRYPTO";
  return path.includes("crypto") ? "CRYPTO" : "NSE";
}

function marketHref(path: string, market: PlatformMarket): string {
  if (path.startsWith("/backtest")) return market === "CRYPTO" ? "/backtest/crypto" : "/backtest";
  if (path.startsWith("/signals")) return market === "CRYPTO" ? "/signals/crypto" : "/signals";
  return market === "CRYPTO" ? "/markets?market=CRYPTO" : "/markets?market=NSE";
}

export function PlatformChrome({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [open, setOpen] = useState(false);
  const [clock, setClock] = useState(() => new Date());
  const [overview, setOverview] = useState<Overview | null>(null);
  const shellEnabled = usesPlatformShell(pathname);
  const market = marketFor(pathname, searchParams.get("market"));

  useEffect(() => {
    if (!shellEnabled) return;
    const timer = window.setInterval(() => setClock(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, [shellEnabled]);

  useEffect(() => {
    if (!shellEnabled) return;
    let cancelled = false;
    const load = async () => {
      try {
        const next = await platformGet<Overview>("overview");
        if (!cancelled) setOverview(next);
      } catch {
        if (!cancelled) setOverview(null);
      }
    };
    void load();
    const timer = window.setInterval(load, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pathname, shellEnabled]);

  const marketClock = useMemo(() => new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: market === "NSE" ? "Asia/Kolkata" : "UTC",
  }).format(clock), [clock, market]);

  if (!shellEnabled) return <>{children}</>;
  const freshness = overview?.dataFreshness?.status ?? "CHECKING";
  const worker = overview?.jobStatus?.status ?? "CHECKING";

  return (
    <>
    <div className="platform-shell" data-navigation-open={open ? "true" : "false"}>
      <header className="platform-topbar">
        <button className="platform-menu" type="button" onClick={() => setOpen((current) => !current)} aria-label="Toggle navigation" aria-expanded={open}>
          {open ? <X size={20} /> : <Menu size={20} />}
        </button>
        <Link className="platform-identity" href="/" aria-label="OpenDelta overview">
          <span aria-hidden="true">Δ</span>
          <div><strong>OpenDelta</strong><small>Quant research</small></div>
        </Link>
        <div className="platform-market-switch" role="group" aria-label="Active market">
          {(["NSE", "CRYPTO"] as PlatformMarket[]).map((item) => (
            <a key={item} className={market === item ? "active" : ""} href={marketHref(pathname, item)} aria-current={market === item ? "true" : undefined}>{item === "CRYPTO" ? "Crypto" : "NSE"}</a>
          ))}
        </div>
        <div className="platform-live-strip">
          <span title="Market clock"><Activity size={14} /><b>{marketClock}</b><small>{market === "NSE" ? "IST" : "UTC"}</small></span>
          <span data-status={freshness}><i />Data {freshness.toLowerCase()}</span>
          <span data-status={worker}><i />Worker {worker.toLowerCase()}</span>
          <span className="platform-environment">{overview?.environment ?? "Connecting"}</span>
          <a className="platform-signout" href="/api/logout" aria-label="Sign out"><LogOut size={17} /></a>
        </div>
      </header>
      <aside className="platform-sidebar" aria-label="Platform navigation">
        <nav>
          {navigation.map(({ href, label, icon: Icon, match }) => (
            <a key={href} href={href} className={match(pathname) ? "active" : ""} aria-current={match(pathname) ? "page" : undefined} onClick={() => setOpen(false)}>
              <Icon size={17} /><span>{label}</span>
            </a>
          ))}
        </nav>
        <div className="platform-safety"><ShieldCheck size={16} /><div><strong>Paper research only</strong><span>Broker execution disabled</span></div></div>
      </aside>
      {open && <button className="platform-backdrop" type="button" onClick={() => setOpen(false)} aria-label="Close navigation" />}
    </div>
    <div className="platform-content">{children}</div>
    </>
  );
}
