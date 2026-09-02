"use client";

import { Activity, LoaderCircle, RefreshCw, Wifi, WifiOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { isPriceInGlobalRange, parseGlobalSettings, type GlobalPriceRange } from "../../global-settings-shared";

type EngineStatus = {
  connectionStatus: string;
  engineStatus: string;
  message: string;
  lastCompletedCandle: string | null;
};

type Signal = {
  signalId: string;
  symbol: string;
  strategyName?: string;
  signalTimestamp: string;
  signalClose: number;
  currentPrice: number | null;
  systemTargetPrice?: number;
};

type PaperTrade = {
  signalId: string;
  entryTimestamp: string;
  entryPrice: number;
  targetPrice: number;
};

const EMPTY_STATUS: EngineStatus = {
  connectionStatus: "DISCONNECTED",
  engineStatus: "STARTING",
  message: "Loading live-signal runtime",
  lastCompletedCandle: null,
};

const SIGNAL_REFRESH_INTERVAL_MS = 10_000;

function money(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `₹${value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatIst(value: string | null | undefined) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true }).format(new Date(value)) + " IST";
}

async function payload(response: Response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text || "The service returned an unreadable response" };
  }
}

export function SignalsWorkspace({ initialGlobalPriceRange }: { userName: string; signOutHref: string; initialGlobalPriceRange: GlobalPriceRange }) {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [paperTrades, setPaperTrades] = useState<PaperTrade[]>([]);
  const [status, setStatus] = useState<EngineStatus>(EMPTY_STATUS);
  const [globalPriceRange, setGlobalPriceRange] = useState(initialGlobalPriceRange);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const loadInFlight = useRef(false);
  const configurationLoaded = useRef(false);

  const load = useCallback(async (quiet = false) => {
    if (loadInFlight.current) return;
    loadInFlight.current = true;
    if (!quiet) setLoading(true);
    else setRefreshing(true);
    try {
      const includeConfiguration = !configurationLoaded.current;
      const [signalResponse, paperResponse, globalSettingsResponse] = await Promise.all([
        fetch("/api/live-signals?action=signals", { cache: "no-store" }),
        fetch("/api/live-signals?action=paper", { cache: "no-store" }),
        includeConfiguration ? fetch("/api/global-settings", { cache: "no-store" }) : Promise.resolve(undefined),
      ]);
      const [signalBody, paperBody, globalSettingsBody] = await Promise.all([
        payload(signalResponse),
        payload(paperResponse),
        globalSettingsResponse ? payload(globalSettingsResponse) : Promise.resolve(null),
      ]);
      if (!signalResponse.ok) throw new Error(signalBody.detail ?? "Unable to load live signals");
      if (!paperResponse.ok) throw new Error(paperBody.detail ?? "Unable to load paper positions");
      if (globalSettingsResponse?.ok && globalSettingsBody) {
        setGlobalPriceRange(parseGlobalSettings(globalSettingsBody).priceRange);
        configurationLoaded.current = true;
      }
      setSignals(signalBody.signals ?? []);
      setStatus(signalBody.status ?? EMPTY_STATUS);
      setPaperTrades(paperBody.paperTrades ?? []);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to load live signals");
    } finally {
      loadInFlight.current = false;
      if (!quiet) setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    const refreshVisible = () => {
      if (document.visibilityState === "visible") void load(true);
    };
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Initial runtime snapshot belongs to this external polling subscription.
    void load();
    const timer = window.setInterval(refreshVisible, SIGNAL_REFRESH_INTERVAL_MS);
    document.addEventListener("visibilitychange", refreshVisible);
    window.addEventListener("focus", refreshVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
      window.removeEventListener("focus", refreshVisible);
    };
  }, [load]);

  const paperBySignal = new Map(paperTrades.map((trade) => [trade.signalId, trade]));
  const rows = signals
    .filter((item) => isPriceInGlobalRange(item.currentPrice ?? item.signalClose, globalPriceRange))
    .sort((a, b) => b.signalTimestamp.localeCompare(a.signalTimestamp));
  const connected = status.connectionStatus === "CONNECTED";

  return <div className="site-shell backtest-shell signals-shell">
    <main className="main-content signals-main">
      <nav className="market-workspace-tabs" aria-label="Market workspace"><a className="active" href="/legacy/signals">NSE</a><a href="/legacy/signals/crypto">Crypto &amp; metals</a></nav>
      <section className="signals-healthbar">
        <div className="signals-health-title"><h1>Signals</h1></div>
        <div className={`health-item ${connected ? "healthy" : "warning"}`}>{connected ? <Wifi size={16} /> : <WifiOff size={16} />}<div><span>Dhan market data</span><strong>{status.connectionStatus}</strong></div></div>
        <div className={`health-item ${status.engineStatus === "READY" ? "healthy" : "warning"}`}><Activity size={16} /><div><span>Signal engine</span><strong>{status.engineStatus.replaceAll("_", " ")}</strong></div></div>
        <a className="global-range-badge signals-global-range" href="/admin">Global price: {money(globalPriceRange.minimumPrice)} – {money(globalPriceRange.maximumPrice)}</a>
        <button className="icon-button" onClick={() => { configurationLoaded.current = false; void load(); }} aria-label="Refresh signals"><RefreshCw className={refreshing ? "spin" : undefined} size={16} /></button>
      </section>

      {error && <div className="backtest-error" role="alert">{error}</div>}

      <section className="backtest-panel">
        <table className="signals-plain-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Signal time</th>
              <th>Entry datetime</th>
              <th>Price</th>
              <th>Take profit</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={5} className="signals-empty"><LoaderCircle className="spin" size={16} />Loading signals…</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={5} className="signals-empty">No signals yet.</td></tr>
            ) : (
              rows.map((signal) => {
                const trade = paperBySignal.get(signal.signalId);
                return (
                  <tr key={signal.signalId}>
                    <td>{signal.symbol}</td>
                    <td>{formatIst(signal.signalTimestamp)}</td>
                    <td>{trade ? formatIst(trade.entryTimestamp) : "—"}</td>
                    <td>{money(trade ? trade.entryPrice : signal.signalClose)}</td>
                    <td>{money(trade ? trade.targetPrice : signal.systemTargetPrice)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </section>
    </main>
  </div>;
}
