"use client";
/* eslint-disable @next/next/no-html-link-for-pages -- Native navigation avoids stalled production transitions. */

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
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Radio,
  ScanSearch,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  X,
} from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { platformGet, type PlatformMarket } from "./platform-client";

type Overview = {
  dataFreshness?: { status?: string; ageSeconds?: number; reason?: string };
  jobStatus?: { status?: string; running?: number; queueDepth?: number };
  environment?: string;
};

type NavigationItem = {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  match: (path: string) => boolean;
};

const navigationGroups: Array<{ label: string; items: NavigationItem[] }> = [
  {
    label: "Workspace",
    items: [
      { href: "/", label: "Overview", icon: LayoutDashboard, match: (path) => path === "/" },
      { href: "/markets", label: "Markets", icon: BarChart3, match: (path) => path.startsWith("/markets") },
    ],
  },
  {
    label: "Research",
    items: [
      { href: "/scanner", label: "Scanner", icon: ScanSearch, match: (path) => path.startsWith("/scanner") },
      { href: "/signals", label: "Signals", icon: Radio, match: (path) => path.startsWith("/signals") },
      { href: "/strategies", label: "Strategies", icon: SlidersHorizontal, match: (path) => path.startsWith("/strategies") },
      { href: "/backtest", label: "Backtests", icon: Gauge, match: (path) => path.startsWith("/backtest") },
      { href: "/research", label: "Research Lab", icon: FlaskConical, match: (path) => path.startsWith("/research") },
    ],
  },
  {
    label: "Operations",
    items: [
      { href: "/risk", label: "Risk", icon: ShieldCheck, match: (path) => path.startsWith("/risk") },
      { href: "/data-health", label: "Data Health", icon: Database, match: (path) => path.startsWith("/data-health") },
      { href: "/jobs", label: "Jobs", icon: BriefcaseBusiness, match: (path) => path.startsWith("/jobs") },
      { href: "/settings", label: "Settings", icon: Settings2, match: (path) => path.startsWith("/settings") || path.startsWith("/admin") },
    ],
  },
];

const navigation = navigationGroups.flatMap((group) => group.items);

export function usesPlatformShell(pathname: string): boolean {
  return pathname !== "/login" && !pathname.startsWith("/api/");
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

function statusTone(status: string): "good" | "warn" | "bad" | "neutral" {
  if (["HEALTHY", "FRESH", "RUNNING", "AVAILABLE"].includes(status)) return "good";
  if (["FAILED", "UNAVAILABLE", "INVALID"].includes(status)) return "bad";
  if (["STALE", "DEGRADED", "STOPPED"].includes(status)) return "warn";
  return "neutral";
}

export function PlatformChrome({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [open, setOpen] = useState(false);
  const [clock, setClock] = useState(() => new Date());
  const [overview, setOverview] = useState<Overview | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    if (typeof window === "undefined") return "dark";
    const saved = window.localStorage.getItem("opendelta-theme");
    return saved === "light" ? "light" : "dark";
  });
  const shellEnabled = usesPlatformShell(pathname);
  const market = marketFor(pathname, searchParams.get("market"));
  const activeItem = navigation.find((item) => item.match(pathname)) ?? navigation[0];

  useEffect(() => {
    setSidebarCollapsed(window.localStorage.getItem("opendelta-sidebar-collapsed") === "true");
  }, []);

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
  const freshnessLabel = overview?.dataFreshness?.reason === "MARKET_CLOSED_LAST_SESSION_CURRENT" ? "current" : freshness.toLowerCase();

  const toggleSidebar = () => {
    setSidebarCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem("opendelta-sidebar-collapsed", String(next));
      return next;
    });
  };

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    window.localStorage.setItem("opendelta-theme", next);
  };

  return (
    <div className="platform-frame" data-theme={theme} data-sidebar-collapsed={sidebarCollapsed ? "true" : "false"} data-ui-version="unified-v2" suppressHydrationWarning>
      <a className="platform-skip-link" href="#main-content">Skip to content</a>
      <div className="platform-shell" data-navigation-open={open ? "true" : "false"}>
        <header className="platform-topbar">
          <button className="platform-menu" type="button" onClick={() => setOpen((current) => !current)} aria-label="Toggle navigation" aria-expanded={open}>
            {open ? <X size={20} /> : <Menu size={20} />}
          </button>
          <a className="platform-identity" href="/" aria-label="OpenDelta overview" onClick={() => setOpen(false)}>
            <span aria-hidden="true">Δ</span>
            <div><strong>OpenDelta</strong><small>Quant research</small></div>
          </a>
          <div className="platform-route-context" aria-label="Current workspace">
            <span>Workspace</span>
            <strong>{activeItem.label}</strong>
          </div>
          <div className="platform-market-switch" role="group" aria-label="Active market">
            {(["NSE", "CRYPTO"] as PlatformMarket[]).map((item) => (
              <a key={item} className={market === item ? "active" : ""} href={marketHref(pathname, item)} aria-current={market === item ? "true" : undefined} onClick={() => setOpen(false)}>{item === "CRYPTO" ? "Crypto" : "NSE"}</a>
            ))}
          </div>
          <div className="platform-live-strip">
            <span className="platform-clock" title="Market clock"><Activity size={14} /><b>{marketClock}</b><small>{market === "NSE" ? "IST" : "UTC"}</small></span>
            <span className="platform-status" data-tone={statusTone(freshness)} title="Market-data freshness"><i />Data <b>{freshnessLabel}</b></span>
            <span className="platform-status" data-tone={statusTone(worker)} title="Background worker"><i />Worker <b>{worker.toLowerCase()}</b></span>
            <span className="platform-environment">{overview?.environment ?? "Connecting"}</span>
            <button className="platform-icon-action" type="button" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
              {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <a className="platform-icon-action platform-signout" href="/api/logout" aria-label="Sign out" title="Sign out"><LogOut size={17} /></a>
          </div>
        </header>
        <aside className="platform-sidebar" aria-label="Platform navigation">
          <button className="platform-sidebar-toggle" type="button" onClick={toggleSidebar} aria-label={sidebarCollapsed ? "Expand navigation" : "Collapse navigation"} title={sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}>
            {sidebarCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
          </button>
          <nav>
            {navigationGroups.map((group) => (
              <section key={group.label} className="platform-nav-group" aria-label={group.label}>
                <span>{group.label}</span>
                {group.items.map(({ href, label, icon: Icon, match }) => (
                  <a key={href} href={href} className={match(pathname) ? "active" : ""} aria-current={match(pathname) ? "page" : undefined} title={sidebarCollapsed ? label : undefined} onClick={() => setOpen(false)}>
                    <Icon size={17} /><span>{label}</span>
                  </a>
                ))}
              </section>
            ))}
          </nav>
          <div className="platform-safety"><ShieldCheck size={16} /><div><strong>Paper research only</strong><span>Broker execution disabled</span></div></div>
        </aside>
        {open && <button className="platform-backdrop" type="button" onClick={() => setOpen(false)} aria-label="Close navigation" />}
      </div>
      <div className="platform-content" id="main-content">{children}</div>
    </div>
  );
}
