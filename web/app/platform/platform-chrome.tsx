"use client";
/* eslint-disable @next/next/no-html-link-for-pages -- Native navigation avoids stalled production transitions. */

import {
  Activity,
  Gauge,
  LayoutDashboard,
  LogOut,
  Moon,
  Radio,
  ScanSearch,
  Settings2,
  ShieldCheck,
  Sun,
  Wallet,
} from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { marketLabel, MARKETS } from "./format";
import { parseMarket, platformGet, type PlatformMarket } from "./platform-client";

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

/** The complete main navigation, in display order. Legacy tools map onto their modern counterpart. */
export const navigation: NavigationItem[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard, match: (path) => path === "/" || path.startsWith("/legacy/markets") },
  { href: "/screener", label: "Screener", icon: ScanSearch, match: (path) => path.startsWith("/screener") || path.startsWith("/legacy/screener") },
  { href: "/backtest", label: "Backtest", icon: Gauge, match: (path) => path.startsWith("/backtest") || path.startsWith("/legacy/backtest") },
  { href: "/signals", label: "Signals", icon: Radio, match: (path) => path.startsWith("/signals") || path.startsWith("/legacy/signals") },
  { href: "/paper-trading", label: "Paper Trading", icon: Wallet, match: (path) => path.startsWith("/paper-trading") },
  { href: "/settings", label: "Settings", icon: Settings2, match: (path) => path.startsWith("/settings") || path.startsWith("/admin") },
];

const OVERVIEW_REFRESH_INTERVAL_MS = 15_000;

export function usesPlatformShell(pathname: string): boolean {
  return pathname !== "/login" && !pathname.startsWith("/api/");
}

function isLegacyMarketPath(path: string): boolean {
  return path.startsWith("/legacy/backtest") || path.startsWith("/legacy/signals");
}

/** Legacy backtest/signals pages encode the market in the path; every modern page reads `?market=`. */
function marketFor(path: string, selected?: string | null): PlatformMarket {
  if (isLegacyMarketPath(path)) return path.includes("/crypto") ? "CRYPTO" : "NSE";
  return parseMarket(selected);
}

function marketHref(path: string, search: string, market: PlatformMarket): string {
  if (path.startsWith("/legacy/backtest")) return market === "CRYPTO" ? "/legacy/backtest/crypto" : "/legacy/backtest";
  if (path.startsWith("/legacy/signals")) return market === "CRYPTO" ? "/legacy/signals/crypto" : "/legacy/signals";
  const params = new URLSearchParams(search);
  params.set("market", market);
  return `${path}?${params.toString()}`;
}

function navigationHref(item: NavigationItem, market: PlatformMarket): string {
  return market === "CRYPTO" ? `${item.href}?market=CRYPTO` : item.href;
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
  // Seeded after mount only: a server-rendered time can never match the client and would break hydration.
  const [clock, setClock] = useState<Date | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [overviewUnavailable, setOverviewUnavailable] = useState(false);
  const [overviewUpdatedAt, setOverviewUpdatedAt] = useState<Date | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    if (typeof window === "undefined") return "dark";
    const saved = window.localStorage.getItem("opendelta-theme");
    return saved === "light" ? "light" : "dark";
  });
  const shellEnabled = usesPlatformShell(pathname);
  const search = searchParams.toString();
  const market = marketFor(pathname, searchParams.get("market"));
  const legacy = pathname.startsWith("/legacy/") || pathname.startsWith("/admin");
  const activeItem = navigation.find((item) => item.match(pathname)) ?? navigation[0];

  useEffect(() => {
    if (!shellEnabled) return;
    const tick = () => setClock(new Date());
    const initial = window.setTimeout(tick, 0);
    const timer = window.setInterval(tick, 1_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [shellEnabled]);

  useEffect(() => {
    if (!shellEnabled) return;
    let cancelled = false;
    const load = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const next = await platformGet<Overview>("overview");
        if (!cancelled) {
          setOverview(next);
          setOverviewUnavailable(false);
          setOverviewUpdatedAt(new Date());
        }
      } catch {
        if (!cancelled) setOverviewUnavailable(true);
      }
    };
    void load();
    const timer = window.setInterval(load, OVERVIEW_REFRESH_INTERVAL_MS);
    const refreshVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", refreshVisible);
    window.addEventListener("focus", refreshVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
      window.removeEventListener("focus", refreshVisible);
    };
  }, [pathname, shellEnabled]);

  const marketClock = useMemo(() => (clock ? new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: market === "NSE" ? "Asia/Kolkata" : "UTC",
  }).format(clock) : "--:--:--"), [clock, market]);

  if (!shellEnabled) return <>{children}</>;
  const freshness = overviewUnavailable ? "UNAVAILABLE" : (overview?.dataFreshness?.status ?? "CHECKING");
  const worker = overviewUnavailable ? "UNAVAILABLE" : (overview?.jobStatus?.status ?? "CHECKING");
  const freshnessLabel = overview?.dataFreshness?.reason === "MARKET_CLOSED_LAST_SESSION_CURRENT" ? "current" : freshness.toLowerCase();
  const overviewRefreshTitle = overviewUpdatedAt
    ? `Live overview refreshes every 15 seconds. Last update ${overviewUpdatedAt.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })} IST.`
    : "Connecting to the live overview service.";

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    window.localStorage.setItem("opendelta-theme", next);
  };

  return (
    <div className="platform-frame" data-theme={theme} data-ui-version="unified-v2" suppressHydrationWarning>
      <a className="platform-skip-link" href="#main-content">Skip to content</a>
      <div className="platform-shell">
        <header className="platform-topbar">
          <a className="platform-identity" href="/" aria-label="OpenDelta dashboard">
            <span aria-hidden="true">Δ</span>
            <div><strong>OpenDelta</strong><small>Quant research</small></div>
          </a>
          <div className="platform-route-context" aria-label="Current workspace">
            <span>{legacy ? "Legacy tool" : "Workspace"}</span>
            <strong>{activeItem.label}</strong>
          </div>
          <div className="platform-market-switch" role="group" aria-label="Active market">
            {MARKETS.map((item) => (
              <a key={item} className={market === item ? "active" : ""} href={marketHref(pathname, search, item)} aria-current={market === item ? "true" : undefined}>{marketLabel(item)}</a>
            ))}
          </div>
          <nav className="platform-topnav" aria-label="Main navigation">
            {navigation.map((item) => {
              const { href, label, icon: Icon, match } = item;
              return (
                <a key={href} href={navigationHref(item, market)} className={match(pathname) ? "active" : ""} aria-current={match(pathname) ? "page" : undefined}>
                  <Icon size={15} /><span>{label}</span>
                </a>
              );
            })}
          </nav>
          <div className="platform-live-strip">
            <span className="platform-clock" title="Market clock"><Activity size={14} /><b>{marketClock}</b><small>{market === "NSE" ? "IST" : "UTC"}</small></span>
            <span className="platform-status" data-tone={statusTone(freshness)} title={overviewRefreshTitle}><i />Data <b>{freshnessLabel}</b></span>
            <span className="platform-status" data-tone={statusTone(worker)} title={overviewRefreshTitle}><i />Worker <b>{worker.toLowerCase()}</b></span>
            <span className="platform-environment">{overview?.environment ?? "Connecting"}</span>
            <button className="platform-icon-action" type="button" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
              {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <a className="platform-icon-action platform-signout" href="/api/logout" aria-label="Sign out" title="Sign out"><LogOut size={17} /></a>
          </div>
          <span className="platform-safety-chip" title="Paper research only · Broker execution disabled">
            <ShieldCheck size={15} /><strong><span>Paper research only</span><i>Paper only</i></strong><small>Broker disabled</small>
          </span>
        </header>
      </div>
      <div className="platform-content" id="main-content">{children}</div>
    </div>
  );
}
