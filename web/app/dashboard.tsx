"use client";
/* eslint-disable @next/next/no-html-link-for-pages -- Native navigation avoids stalled vinext client transitions in production. */

import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  LayoutDashboard,
  LogOut,
  Moon,
  Plus,
  Radio,
  RefreshCw,
  ScanSearch,
  Search,
  Settings2,
  Sun,
  TrendingDown,
  TrendingUp,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { parseMarketCsv, type StockRow } from "./market-data";
import { formatGlobalPriceRange, isPriceInGlobalRange, type GlobalPriceRange } from "./global-settings-shared";

export type { StockRow } from "./market-data";

type DashboardProps = {
  stocks: StockRow[];
  latestSession: string | null;
  generatedAt: string | null;
  userName: string;
  signOutHref: string;
  globalPriceRange: GlobalPriceRange;
};

type BandKey = "all" | "20-30" | "30-40" | "40-50" | "50-plus";
type Movement = "all" | "gainers" | "losers";
type MarketDataRefreshStatus = {
  state: "IDLE" | "RUNNING" | "SUCCEEDED" | "FAILED";
  running: boolean;
  startedAt: string | null;
  completedAt: string | null;
  lastRefreshTimestamp: string | null;
  rowsPublished: number | null;
  processedSymbols: number | null;
  totalSymbols: number | null;
  error: string | null;
};
type SymbolAddResult = {
  symbol: string;
  companyName: string;
  symbolCount: number;
  refresh: MarketDataRefreshStatus & { accepted: boolean };
  detail?: string;
};
type SortKey =
  | "rank"
  | "symbol"
  | "previous_rsi_14"
  | "rsi_14"
  | "previous_close"
  | "entry_price"
  | "change_price"
  | "volume_24h";

const PAGE_SIZE = 25;
const LIVE_DATA_URL = "/api/market-data?format=csv";
const DATA_REFRESH_INTERVAL_MS = 60 * 60 * 1_000;
const STATUS_REFRESH_INTERVAL_MS = 10_000;

const bands: Array<{
  key: BandKey;
  label: string;
  eyebrow: string;
  range: string;
  tone: string;
}> = [
  { key: "all", label: "All symbols", eyebrow: "Full market", range: "Any RSI", tone: "slate" },
  { key: "20-30", label: "Deep value", eyebrow: "Oversold", range: "RSI 20–30", tone: "rose" },
  { key: "30-40", label: "Weak zone", eyebrow: "Building", range: "RSI 30–40", tone: "amber" },
  { key: "40-50", label: "Near neutral", eyebrow: "Watchlist", range: "RSI 40–50", tone: "blue" },
  { key: "50-plus", label: "Above neutral", eyebrow: "Momentum", range: "RSI > 50", tone: "green" },
];

function inBand(value: number | null, band: BandKey) {
  if (band === "all") return true;
  if (value === null) return false;
  if (band === "20-30") return value >= 20 && value < 30;
  if (band === "30-40") return value >= 30 && value < 40;
  if (band === "40-50") return value >= 40 && value <= 50;
  return value > 50;
}

function getPriceChange(stock: StockRow) {
  if (stock.entry_price === null || stock.previous_close === null) return null;
  return stock.entry_price - stock.previous_close;
}

function formatPriceChange(value: number | null) {
  if (value === null) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${formatNumber(Math.abs(value))}`;
}

function formatIstDate(value: string | null) {
  if (!value) return "Awaiting session";
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return `${value} · IST`;

  const date = new Date(Date.UTC(year, month - 1, day, 6, 30));
  const formatted = new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  }).format(date);

  return `${formatted} · IST`;
}

function formatDhanTimestamp(value: string | null) {
  if (!value) return "Awaiting data";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Time unavailable";

  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Kolkata",
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: true,
    })
      .formatToParts(date)
      .map((part) => [part.type, part.value]),
  );

  return `${parts.day} ${parts.month} ${parts.hour}:${parts.minute} ${parts.dayPeriod.toUpperCase()}`;
}

function formatConnectionStatus(value: string) {
  return value
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatRefreshStatus(status: MarketDataRefreshStatus, timestamp: string | null) {
  if (status.running) {
    if (status.processedSymbols !== null && status.totalSymbols) {
      return `Refreshing ${status.processedSymbols}/${status.totalSymbols} symbols`;
    }
    return "Refreshing market data";
  }
  if (status.state === "FAILED") return "Refresh failed";
  return formatDhanTimestamp(timestamp);
}

function isMarketDataStale(value: string | null, now = new Date()) {
  if (!value) return true;
  const refreshedAt = new Date(value);
  if (Number.isNaN(refreshedAt.getTime())) return true;

  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Kolkata",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(now)
      .map((part) => [part.type, part.value]),
  );
  const weekday = parts.weekday;
  const minuteOfDay = Number(parts.hour) * 60 + Number(parts.minute);
  const isTradingWeekday = weekday !== "Sat" && weekday !== "Sun";
  const firstExpectedRefresh = 10 * 60;
  const marketCloseBuffer = 16 * 60 + 30;

  if (!isTradingWeekday || minuteOfDay < firstExpectedRefresh) return false;

  const refreshAgeMinutes = (now.getTime() - refreshedAt.getTime()) / 60_000;
  if (minuteOfDay <= marketCloseBuffer) return refreshAgeMinutes > 90;

  const dateFormatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return dateFormatter.format(refreshedAt) !== dateFormatter.format(now);
}

function getPriceLimits(stocks: StockRow[]) {
  const prices = stocks
    .map((stock) => stock.entry_price)
    .filter((price): price is number => price !== null && Number.isFinite(price) && price > 0);

  if (prices.length === 0) return { min: 1, max: 10_000 };
  const min = Math.max(1, Math.floor(Math.min(...prices)));
  return { min, max: Math.max(min + 1, Math.ceil(Math.max(...prices))) };
}

function priceFromSlider(position: number, min: number, max: number) {
  if (position <= 0) return min;
  if (position >= 100) return max;
  return Math.exp(Math.log(min) + (position / 100) * (Math.log(max) - Math.log(min)));
}

function formatSliderPrice(value: number) {
  return new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: value < 100 ? 2 : 0,
  }).format(value);
}

function formatNumber(value: number | null, digits = 2) {
  if (value === null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function formatVolume(value: number | null) {
  if (value === null || Number.isNaN(value)) return "—";
  const absolute = Math.abs(value);
  if (absolute >= 10_000_000) return `${(value / 10_000_000).toFixed(2)} Cr`;
  if (absolute >= 100_000) return `${(value / 100_000).toFixed(2)} L`;
  if (absolute >= 1_000) return `${(value / 1_000).toFixed(1)} K`;
  return new Intl.NumberFormat("en-IN").format(value);
}

function formatLevelTime(value: string | null) {
  if (!value) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return formatIstDate(value);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return `${value} · IST`;

  return `${new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  }).format(date)} IST`;
}

function PriceLevelPopover({ stock }: { stock: StockRow }) {
  const supports = [
    { price: stock.support_1_price, time: stock.support_1_time },
    { price: stock.support_2_price, time: stock.support_2_time },
  ];
  const resistances = [
    { price: stock.resistance_1_price, time: stock.resistance_1_time },
    { price: stock.resistance_2_price, time: stock.resistance_2_time },
  ];
  const hasLevels = [...supports, ...resistances].some((level) => level.price != null);

  return (
    <div className="current-close">
      <button
        type="button"
        className="current-close-trigger"
        aria-label={`Current close for ${stock.symbol}. Show recent support and resistance levels.`}
      >
        ₹{formatNumber(stock.entry_price)}
      </button>
      <div className="price-level-popover" role="tooltip">
        <div className="level-popover-header">
          <strong>Recent levels</strong>
          <span>Confirmed 1-day pivots · IST</span>
        </div>
        {hasLevels ? (
          <div className="level-columns">
            <div className="level-group support">
              <span className="level-title">Support</span>
              {supports.map((level, index) => (
                <div className="level-row" key={`support-${index + 1}`}>
                  <strong>S{index + 1} · ₹{formatNumber(level.price)}</strong>
                  <span>{formatLevelTime(level.time)}</span>
                </div>
              ))}
            </div>
            <div className="level-group resistance">
              <span className="level-title">Resistance</span>
              {resistances.map((level, index) => (
                <div className="level-row" key={`resistance-${index + 1}`}>
                  <strong>R{index + 1} · ₹{formatNumber(level.price)}</strong>
                  <span>{formatLevelTime(level.time)}</span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <span className="level-empty">Awaiting confirmed daily pivots</span>
        )}
      </div>
    </div>
  );
}

function bandForRsi(value: number | null) {
  if (value === null) return "muted";
  if (value < 30) return "rose";
  if (value < 40) return "amber";
  if (value <= 50) return "blue";
  return "green";
}

function initials(name: string) {
  const value = name.includes("@") ? name.split("@")[0] : name;
  return value
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "OD";
}

export function Dashboard({
  stocks,
  generatedAt,
  userName,
  signOutHref,
  globalPriceRange,
}: DashboardProps) {
  const [marketStocks, setMarketStocks] = useState(stocks);
  const [marketGeneratedAt, setMarketGeneratedAt] = useState(generatedAt);
  const [band, setBand] = useState<BandKey>("all");
  const [movement, setMovement] = useState<Movement>("all");
  const [rsiSliderMin, setRsiSliderMin] = useState(0);
  const [rsiSliderMax, setRsiSliderMax] = useState(100);
  const [priceSliderMin, setPriceSliderMin] = useState(0);
  const [priceSliderMax, setPriceSliderMax] = useState(100);
  const [query, setQuery] = useState("");
  const [newSymbol, setNewSymbol] = useState("");
  const [addingSymbol, setAddingSymbol] = useState(false);
  const [symbolAddMessage, setSymbolAddMessage] = useState<{
    tone: "success" | "error";
    text: string;
  } | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);
  const [darkMode, setDarkMode] = useState(true);
  const [connectionStatus, setConnectionStatus] = useState("CHECKING");
  const [marketSession, setMarketSession] = useState("UNKNOWN");
  const [refreshStatus, setRefreshStatus] = useState<MarketDataRefreshStatus>({
    state: "IDLE",
    running: false,
    startedAt: null,
    completedAt: null,
    lastRefreshTimestamp: generatedAt,
    rowsPublished: null,
    processedSymbols: null,
    totalSymbols: null,
    error: null,
  });
  const loadedRefreshTimestamp = useRef(generatedAt);

  const loadMarketData = useCallback(async () => {
    try {
      const response = await fetch(`${LIVE_DATA_URL}&refresh=${Date.now()}`, {
        cache: "no-store",
        credentials: "same-origin",
      });
      if (!response.ok) return false;

      const payload = parseMarketCsv(await response.text());
      if (payload.rows.length === 0) return false;
      const lastModified = response.headers.get("last-modified");
      const lastModifiedDate = lastModified ? new Date(lastModified) : null;
      setMarketStocks(payload.rows);
      if (lastModifiedDate && !Number.isNaN(lastModifiedDate.getTime())) {
        const timestamp = lastModifiedDate.toISOString();
        setMarketGeneratedAt(timestamp);
        loadedRefreshTimestamp.current = timestamp;
      }
      return true;
    } catch {
      return false;
    }
  }, []);

  const loadServiceStatus = useCallback(async () => {
    try {
      const [refreshResponse, connectionResponse] = await Promise.all([
        fetch("/api/market-data", { cache: "no-store" }),
        fetch("/api/live-signals?action=status", { cache: "no-store" }),
      ]);
      if (refreshResponse.ok) {
        const nextStatus = await refreshResponse.json() as MarketDataRefreshStatus;
        setRefreshStatus(nextStatus);
        if (
          nextStatus.lastRefreshTimestamp &&
          nextStatus.lastRefreshTimestamp !== loadedRefreshTimestamp.current
        ) {
          const loaded = await loadMarketData();
          if (loaded) loadedRefreshTimestamp.current = nextStatus.lastRefreshTimestamp;
        }
      }
      if (connectionResponse.ok) {
        const body = await connectionResponse.json() as {
          connectionStatus?: string;
          marketSession?: string;
        };
        const nextMarketSession = body.marketSession ?? "UNKNOWN";
        setMarketSession(nextMarketSession);
        setConnectionStatus(
          nextMarketSession === "CLOSED" ? "MARKET_CLOSED" : (body.connectionStatus ?? "UNKNOWN"),
        );
      } else {
        setConnectionStatus("DISCONNECTED");
      }
    } catch {
      setConnectionStatus("DISCONNECTED");
    }
  }, [loadMarketData]);

  useEffect(() => {
    void loadMarketData();
    void loadServiceStatus();
    const dataInterval = window.setInterval(() => void loadMarketData(), DATA_REFRESH_INTERVAL_MS);
    const statusInterval = window.setInterval(() => void loadServiceStatus(), STATUS_REFRESH_INTERVAL_MS);
    return () => {
      window.clearInterval(dataInterval);
      window.clearInterval(statusInterval);
    };
  }, [loadMarketData, loadServiceStatus]);

  const startManualRefresh = async () => {
    try {
      const response = await fetch("/api/market-data", {
        method: "POST",
        cache: "no-store",
      });
      const body = await response.json() as MarketDataRefreshStatus & { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "Unable to start market-data refresh");
      setRefreshStatus(body);
    } catch (error) {
      setRefreshStatus((current) => ({
        ...current,
        state: "FAILED",
        running: false,
        error: error instanceof Error ? error.message : "Unable to start market-data refresh",
      }));
    }
  };

  const addMarketSymbol = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const symbol = newSymbol.trim().toUpperCase().replace(/\.NS$/, "");
    if (!symbol) {
      setSymbolAddMessage({ tone: "error", text: "Enter an NSE equity symbol." });
      return;
    }

    setAddingSymbol(true);
    setSymbolAddMessage(null);
    try {
      const response = await fetch("/api/market-symbols", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ symbol }),
        cache: "no-store",
      });
      const body = await response.json() as SymbolAddResult;
      if (!response.ok) throw new Error(body.detail ?? "Unable to add the symbol");
      setNewSymbol("");
      setRefreshStatus(body.refresh);
      setSymbolAddMessage({
        tone: "success",
        text: `${body.symbol} added as symbol ${body.symbolCount}. ${
          body.refresh.accepted ? "Refresh started." : "It will be included in the next refresh."
        }`,
      });
    } catch (error) {
      setSymbolAddMessage({
        tone: "error",
        text: error instanceof Error ? error.message : "Unable to add the symbol",
      });
    } finally {
      setAddingSymbol(false);
    }
  };

  const globalStocks = useMemo(
    () => marketStocks.filter((stock) => isPriceInGlobalRange(stock.entry_price, globalPriceRange)),
    [globalPriceRange, marketStocks],
  );

  const bandCounts = useMemo(
    () =>
      Object.fromEntries(
        bands.map(({ key }) => [
          key,
          globalStocks.filter((stock) => inBand(stock.rsi_14, key)).length,
        ]),
      ) as Record<BandKey, number>,
    [globalStocks],
  );

  const priceLimits = useMemo(() => getPriceLimits(globalStocks), [globalStocks]);
  const selectedMinPrice = priceFromSlider(priceSliderMin, priceLimits.min, priceLimits.max);
  const selectedMaxPrice = priceFromSlider(priceSliderMax, priceLimits.min, priceLimits.max);
  const refreshTimestamp = refreshStatus.lastRefreshTimestamp ?? marketGeneratedAt;
  const marketDataStale = marketSession !== "CLOSED" && isMarketDataStale(refreshTimestamp);
  const refreshStatusText = formatRefreshStatus(refreshStatus, refreshTimestamp);

  const filteredStocks = useMemo(() => {
    const normalizedQuery = query.trim().toUpperCase();
    const filtered = globalStocks.filter((stock) => {
      const matchesBand = inBand(stock.rsi_14, band);
      const matchesSearch =
        normalizedQuery.length === 0 ||
        `${stock.symbol} ${stock.company_name ?? ""}`.toUpperCase().includes(normalizedQuery);
      const matchesMovement =
        movement === "all" ||
        (movement === "gainers" && (stock.change_percent ?? 0) > 0) ||
        (movement === "losers" && (stock.change_percent ?? 0) < 0);
      const matchesRsiSlicer =
        stock.rsi_14 !== null &&
        stock.rsi_14 >= rsiSliderMin &&
        stock.rsi_14 <= rsiSliderMax;
      const matchesPriceSlicer =
        stock.entry_price !== null &&
        stock.entry_price >= selectedMinPrice &&
        stock.entry_price <= selectedMaxPrice;

      return matchesBand && matchesSearch && matchesMovement && matchesRsiSlicer && matchesPriceSlicer;
    });

    return [...filtered].sort((left, right) => {
      const first = sortKey === "change_price" ? getPriceChange(left) : left[sortKey];
      const second = sortKey === "change_price" ? getPriceChange(right) : right[sortKey];

      if (first === null) return 1;
      if (second === null) return -1;

      const comparison =
        typeof first === "string"
          ? first.localeCompare(String(second))
          : Number(first) - Number(second);

      return sortDirection === "asc" ? comparison : -comparison;
    });
  }, [
    band,
    globalStocks,
    movement,
    query,
    rsiSliderMax,
    rsiSliderMin,
    selectedMaxPrice,
    selectedMinPrice,
    sortDirection,
    sortKey,
  ]);

  const pageCount = Math.max(1, Math.ceil(filteredStocks.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const visibleStocks = filteredStocks.slice(
    (safePage - 1) * PAGE_SIZE,
    safePage * PAGE_SIZE,
  );

  const handleSort = (nextKey: SortKey) => {
    if (nextKey === sortKey) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(nextKey);
    setSortDirection(nextKey === "symbol" || nextKey === "rank" ? "asc" : "desc");
  };

  return (
    <div className="site-shell" data-theme={darkMode ? "dark" : "light"}>
      <header className="global-header">
        <div className="header-inner">
          <a className="brand" href="#dashboard" aria-label="OpenDelta dashboard">
            <div className="brand-mark" aria-hidden="true">₹</div>
            <div>
              <strong>OpenDelta</strong>
              <span>Market intelligence</span>
            </div>
          </a>

          <nav className="top-nav" aria-label="Main navigation">
            <a className="nav-item active" href="/" aria-current="page">
              <LayoutDashboard size={16} />
              Dashboard
            </a>
            <a className="nav-item" href="/scanner">
              <ScanSearch size={16} />
              Stock Scanner
            </a>
            <a className="nav-item" href="/backtest">
              <TrendingUp size={16} />
              Backtest
            </a>
            <a className="nav-item" href="/signals">
              <Radio size={16} />
              Signals
            </a>
            <a className="nav-item" href="/admin">
              <Settings2 size={16} />
              Admin
            </a>
          </nav>

          <label className="search-field header-search">
            <Search size={17} />
            <span className="sr-only">Search symbols</span>
            <input
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
              placeholder="Search NSE symbol"
            />
            {query && (
              <button
                type="button"
                onClick={() => {
                  setQuery("");
                  setPage(1);
                }}
                aria-label="Clear search"
              >
                <X size={15} />
              </button>
            )}
          </label>

          <div className="header-actions">
            <div
              className={`dhan-refresh-control ${marketDataStale || refreshStatus.state === "FAILED" ? "stale" : ""} ${marketSession === "CLOSED" && refreshStatus.state !== "FAILED" ? "market-closed" : ""} ${refreshStatus.running ? "refreshing" : ""}`}
              title={refreshStatus.error ?? (marketDataStale ? "Market data has missed its expected refresh" : undefined)}
            >
              <div className="dhan-data-status" aria-label="Dhan market data status">
                <span className="status-dot" />
                <span className="dhan-status-copy">
                  <span className="dhan-status-title">
                    <strong>Dhan</strong>
                    <span className="connection-state">{formatConnectionStatus(connectionStatus)}</span>
                  </span>
                  <span className="dhan-status-time" aria-live="polite">{refreshStatusText}</span>
                </span>
              </div>
              <button
                type="button"
                className="manual-data-refresh"
                onClick={() => void startManualRefresh()}
                disabled={refreshStatus.running}
                aria-label="Refresh all NSE data from Dhan"
                title={refreshStatus.running ? refreshStatusText : "Refresh all NSE data from Dhan"}
              >
                <RefreshCw className={refreshStatus.running ? "spin" : ""} size={16} />
              </button>
            </div>
            <div className="user-chip" title={userName}>
              <div className="avatar">{initials(userName)}</div>
              <span>{userName}</span>
            </div>
            <button
              type="button"
              className="icon-button theme-toggle"
              onClick={() => setDarkMode((current) => !current)}
              aria-label={darkMode ? "Switch to light theme" : "Switch to dark theme"}
              title={darkMode ? "Light theme" : "Dark theme"}
            >
              {darkMode ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <a href={signOutHref} className="icon-button" aria-label="Sign out" title="Sign out">
              <LogOut size={17} />
            </a>
          </div>
        </div>
      </header>

      <main className="main-content" id="dashboard">
        <section className="band-grid" id="rsi-groups" aria-label="RSI filter groups">
            {bands.map((item) => (
              <button
                key={item.key}
                type="button"
                className={`band-card ${item.tone} ${band === item.key ? "selected" : ""}`}
                onClick={() => {
                  setBand(item.key);
                  setPage(1);
                }}
                aria-pressed={band === item.key}
              >
                <span className="band-topline">
                  <span>{item.eyebrow}</span>
                  <span className="band-count">{bandCounts[item.key]}</span>
                </span>
                <span className="band-mainline">
                  <strong>{item.label}</strong>
                  <span className="band-range">{item.range}</span>
                </span>
                <span className="band-meter" aria-hidden="true">
                  <span />
                </span>
              </button>
            ))}
        </section>

        <section className="table-panel" id="market-table">
          <div className="filters-row">
            <div className="filter-controls">
              <a className="global-range-badge" href="/admin" title="Change global price range">
                Global: {formatGlobalPriceRange(globalPriceRange)}
              </a>
              <div className="filter-group">
                <span className="filter-label">Movement</span>
                <div className="segmented" aria-label="Price movement filter">
                  {(["all", "gainers", "losers"] as Movement[]).map((item) => (
                    <button
                      key={item}
                      type="button"
                      className={movement === item ? "active" : ""}
                      onClick={() => {
                        setMovement(item);
                        setPage(1);
                      }}
                    >
                      {item === "all" ? "All moves" : item === "gainers" ? "Gainers" : "Losers"}
                    </button>
                  ))}
                </div>
              </div>

              <div className="filter-group rsi-slicer-group">
                <span className="filter-label">RSI slicer</span>
                <div className="price-slicer-control rsi-slicer-control">
                  <div className="price-slicer-values" aria-live="polite">
                    <span>RSI {rsiSliderMin}</span>
                    {(rsiSliderMin > 0 || rsiSliderMax < 100) && (
                      <button
                        type="button"
                        onClick={() => {
                          setRsiSliderMin(0);
                          setRsiSliderMax(100);
                          setPage(1);
                        }}
                      >
                        Reset
                      </button>
                    )}
                    <span>RSI {rsiSliderMax}</span>
                  </div>
                  <div className="dual-range">
                    <span className="dual-range-track" aria-hidden="true">
                      <span style={{ left: `${rsiSliderMin}%`, right: `${100 - rsiSliderMax}%` }} />
                    </span>
                    <input
                      type="range"
                      min="0"
                      max="100"
                      step="1"
                      value={rsiSliderMin}
                      onChange={(event) => {
                        setRsiSliderMin(Math.min(Number(event.target.value), rsiSliderMax - 1));
                        setPage(1);
                      }}
                      aria-label="Minimum current RSI"
                      aria-valuetext={`RSI ${rsiSliderMin}`}
                    />
                    <input
                      type="range"
                      min="0"
                      max="100"
                      step="1"
                      value={rsiSliderMax}
                      onChange={(event) => {
                        setRsiSliderMax(Math.max(Number(event.target.value), rsiSliderMin + 1));
                        setPage(1);
                      }}
                      aria-label="Maximum current RSI"
                      aria-valuetext={`RSI ${rsiSliderMax}`}
                    />
                  </div>
                </div>
              </div>

              <div className="filter-group price-slicer-group">
                <span className="filter-label">Price slicer</span>
                <div className="price-slicer-control">
                  <div className="price-slicer-values" aria-live="polite">
                    <span>₹{formatSliderPrice(selectedMinPrice)}</span>
                    {(priceSliderMin > 0 || priceSliderMax < 100) && (
                      <button
                        type="button"
                        onClick={() => {
                          setPriceSliderMin(0);
                          setPriceSliderMax(100);
                          setPage(1);
                        }}
                      >
                        Reset
                      </button>
                    )}
                    <span>₹{formatSliderPrice(selectedMaxPrice)}</span>
                  </div>
                  <div className="dual-range">
                    <span className="dual-range-track" aria-hidden="true">
                      <span
                        style={{
                          left: `${priceSliderMin}%`,
                          right: `${100 - priceSliderMax}%`,
                        }}
                      />
                    </span>
                    <input
                      type="range"
                      min="0"
                      max="100"
                      step="1"
                      value={priceSliderMin}
                      onChange={(event) => {
                        setPriceSliderMin(Math.min(Number(event.target.value), priceSliderMax - 1));
                        setPage(1);
                      }}
                      aria-label="Minimum current price"
                      aria-valuetext={`₹${formatSliderPrice(selectedMinPrice)}`}
                    />
                    <input
                      type="range"
                      min="0"
                      max="100"
                      step="1"
                      value={priceSliderMax}
                      onChange={(event) => {
                        setPriceSliderMax(Math.max(Number(event.target.value), priceSliderMin + 1));
                        setPage(1);
                      }}
                      aria-label="Maximum current price"
                      aria-valuetext={`₹${formatSliderPrice(selectedMaxPrice)}`}
                    />
                  </div>
                </div>
              </div>
            </div>
            <div className="market-table-actions">
              <form className="symbol-add-form" onSubmit={(event) => void addMarketSymbol(event)}>
                <label className="symbol-add-control">
                  <span className="sr-only">Add NSE equity symbol</span>
                  <input
                    type="text"
                    value={newSymbol}
                    onChange={(event) => {
                      setNewSymbol(event.target.value.toUpperCase());
                      if (symbolAddMessage?.tone === "error") setSymbolAddMessage(null);
                    }}
                    placeholder="Add NSE symbol"
                    maxLength={40}
                    autoComplete="off"
                    disabled={addingSymbol || refreshStatus.running}
                    aria-describedby={symbolAddMessage ? "symbol-add-message" : undefined}
                  />
                  <button
                    type="submit"
                    disabled={addingSymbol || refreshStatus.running || !newSymbol.trim()}
                    title="Validate with Dhan and add this symbol"
                  >
                    <Plus size={16} />
                    {addingSymbol ? "Adding" : "Add"}
                  </button>
                </label>
                {symbolAddMessage && (
                  <span
                    id="symbol-add-message"
                    className={`symbol-add-message ${symbolAddMessage.tone}`}
                    role={symbolAddMessage.tone === "error" ? "alert" : "status"}
                  >
                    {symbolAddMessage.text}
                  </span>
                )}
              </form>
            </div>
          </div>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <SortableHeader label="Symbol" field="symbol" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                  <SortableHeader label="Yesterday RSI" field="previous_rsi_14" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                  <SortableHeader label="Current RSI" field="rsi_14" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                  <SortableHeader label="Yesterday price" field="previous_close" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                  <SortableHeader label="Current close" field="entry_price" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                  <SortableHeader label="Change (₹)" field="change_price" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                  <SortableHeader label="24h volume" field="volume_24h" activeKey={sortKey} direction={sortDirection} onSort={handleSort} />
                  <th>Session</th>
                </tr>
              </thead>
              <tbody>
                {visibleStocks.map((stock) => {
                  const change = getPriceChange(stock);
                  const positive = (change ?? 0) >= 0;
                  return (
                    <tr key={stock.symbol}>
                      <td data-label="Symbol">
                        <div className="symbol-cell">
                          <strong
                            title={stock.company_name ?? stock.symbol}
                            aria-label={`${stock.symbol}: ${stock.company_name ?? stock.symbol}`}
                          >
                            {stock.symbol}
                          </strong>
                        </div>
                      </td>
                      <td data-label="Yesterday RSI">
                        <span className={`rsi-badge ${bandForRsi(stock.previous_rsi_14)}`}>
                          {formatNumber(stock.previous_rsi_14)}
                        </span>
                      </td>
                      <td data-label="Current RSI">
                        <div className="rsi-cell">
                          <span className={`rsi-badge ${bandForRsi(stock.rsi_14)}`}>
                            {formatNumber(stock.rsi_14)}
                          </span>
                          <span className="rsi-line" aria-hidden="true">
                            <span style={{ width: `${Math.min(100, Math.max(0, stock.rsi_14 ?? 0))}%` }} />
                          </span>
                        </div>
                      </td>
                      <td className="numeric" data-label="Yesterday price">₹{formatNumber(stock.previous_close)}</td>
                      <td className="numeric" data-label="Current close">
                        <PriceLevelPopover stock={stock} />
                      </td>
                      <td data-label="Change (₹)">
                        <span className={`change-badge ${positive ? "positive" : "negative"}`}>
                          {positive ? <TrendingUp size={14} /> : <TrendingDown size={14} />}
                          {formatPriceChange(change)}
                        </span>
                      </td>
                      <td className="numeric volume" data-label="24h volume">{formatVolume(stock.volume_24h)}</td>
                      <td className="session-cell" data-label="Session">{formatIstDate(stock.trading_date)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {visibleStocks.length === 0 && (
              <div className="empty-state">
                <Search size={24} />
                <strong>No symbols found</strong>
                <span>Adjust the RSI or price slicer, or clear your search.</span>
              </div>
            )}
          </div>

          <div className="pagination">
            <span>
              Showing {filteredStocks.length === 0 ? 0 : (safePage - 1) * PAGE_SIZE + 1}–
              {Math.min(safePage * PAGE_SIZE, filteredStocks.length)} of {filteredStocks.length}
            </span>
            <div>
              <button
                type="button"
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                disabled={safePage === 1}
                aria-label="Previous page"
              >
                <ChevronLeft size={17} />
              </button>
              <span>Page {safePage} of {pageCount}</span>
              <button
                type="button"
                onClick={() => setPage((current) => Math.min(pageCount, current + 1))}
                disabled={safePage === pageCount}
                aria-label="Next page"
              >
                <ChevronRight size={17} />
              </button>
            </div>
          </div>
        </section>

        <footer>
          Market data is indicative and intended for research. It is not investment advice.
        </footer>
      </main>
    </div>
  );
}

function SortableHeader({
  label,
  field,
  activeKey,
  direction,
  onSort,
}: {
  label: string;
  field: SortKey;
  activeKey: SortKey;
  direction: "asc" | "desc";
  onSort: (field: SortKey) => void;
}) {
  const active = activeKey === field;
  return (
    <th>
      <button type="button" className="sort-button" onClick={() => onSort(field)}>
        {label}
        {active && (direction === "asc" ? <ArrowUp size={13} /> : <ArrowDown size={13} />)}
      </button>
    </th>
  );
}
