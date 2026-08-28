"use client";
/* eslint-disable @next/next/no-html-link-for-pages -- Native navigation avoids stalled vinext client transitions in production. */

import {
  AlertTriangle,
  Check,
  ChevronDown,
  Clock3,
  Info,
  LayoutDashboard,
  LineChart,
  LoaderCircle,
  LogOut,
  MoveHorizontal,
  Moon,
  Radio,
  RotateCcw,
  Search,
  Square,
  Sun,
  TrendingUp,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  RecoveryResults,
  mergeRecoveryResponses,
  type RecoveryBacktestResponse,
} from "./recovery-results";
import {
  AtrOptimizationResults,
  type AtrOptimizationResponse,
} from "./atr-optimization-results";
import {
  RsiExitComparisonResults,
  type RsiExitComparisonResponse,
} from "./rsi-exit-comparison-results";
import {
  NumericField,
  parameterDefinition,
  strategyDefaults,
} from "./strategy-parameters";
import {
  backtestHistorySummary,
  migrateBrowserBacktestHistory,
  readAccountBacktestHistory,
  readAccountBacktestResult,
  readBacktestHistory,
  saveAccountBacktestHistory,
  saveBacktestHistory,
  type BacktestHistoryEntry,
  type BacktestHistorySummary,
} from "./backtest-history";

type BacktestDashboardProps = {
  symbols: string[];
  userName: string;
  signOutHref: string;
};

type ChartPoint = {
  time: string;
  close: number | null;
  rsi: number | null;
  equity: number | null;
  action: "buy" | "sell" | "hold" | null;
  entryPrice?: number | null;
  candidateExitPrice?: number | null;
  netReturnPct?: number | null;
  requiredNetProfitPct?: number | null;
  estimatedFees?: number | null;
  signalRsi?: number | null;
  reason?: string;
};

type Trade = {
  entrySignalTime: string;
  entryTime: string;
  entryRsi: number;
  entryPrice: number;
  exitSignalTime: string;
  exitTime: string;
  exitRsi: number;
  exitPrice: number;
  quantity: number;
  holdingBars: number;
  netPnl: number;
  returnPct: number;
  fees: number;
};

type RangeEvent = {
  range: "entry" | "exit";
  signalTime: string;
  rsi: number;
  signalClose: number;
  nextCandleTime: string | null;
  nextOpen: number | null;
};

type BacktestResult = {
  symbol: string;
  verdict: "profitable" | "unprofitable" | "no-trades";
  firstCandle: string;
  lastCandle: string;
  bars: number;
  closedTrades: number;
  wins: number;
  losses: number;
  winRate: number | null;
  strategyReturnPct: number;
  cagrPct: number;
  buyHoldReturnPct: number;
  niftyReturnPct: number | null;
  maxDrawdownPct: number;
  sharpe: number | null;
  sortino: number | null;
  profitFactor: number | null;
  averageTradeReturnPct: number | null;
  averageWinPct: number | null;
  averageLossPct: number | null;
  endingCapital: number;
  heldExitSignals: number;
  openPosition: {
    entryTime: string;
    entryRsi: number;
    entryPrice: number;
    quantity: number;
    lastPrice: number;
    unrealizedPnl: number;
    estimatedReturnPct: number;
  } | null;
  trades: Trade[];
  events: RangeEvent[];
  chart: ChartPoint[];
};

type BacktestResponse = {
  metadata: {
    runId: string;
    strategyMode: "rsi_range";
    strategyVersion: string;
    generatedAt: string;
    analysisStart: string;
    durationYears: number;
    timeframe: string;
    entryRange: [number, number];
    exitRange: [number, number];
    initialCapitalPerSymbol: number;
    minimumNetProfitPct: number;
    timezone: string;
    benchmark: string;
    costModel: {
      variableFeePerSidePct: number;
      fixedFeePerOrder: number;
      slippagePerSidePct: number;
    };
  };
  results: BacktestResult[];
  errors: Array<{ symbol: string; message: string }>;
  warnings: string[];
};

type BacktestPayload = (BacktestResponse | RecoveryBacktestResponse) & { detail?: string };
type StrategyMode = "rsi_range" | "rsi_recovery" | "market_aligned_rsi_scalper";
type OiFilterMode = "OFF" | "ADVISORY" | "RESEARCH_FILTER" | "ENFORCED";
type MarketAlignedPreset = "Recommended" | "Strict" | "Custom";
type OiHistoryStatus = {
  state: string;
  request?: { fromDate?: string; toDate?: string; strikesEachSide?: number };
  optionRowsImported?: number;
  regimeSnapshotsCreated?: number;
  enforceableSnapshots?: number;
  historicalDepthAvailable?: boolean;
  enforcementReady?: boolean;
  reason?: string;
};
type OiFilterComparisonResponse = {
  metadata: { runId: string; generatedAt: string; researchLabel: string; defaultOiMode: "ADVISORY" };
  comparison: {
    tradesAccepted: number;
    tradesRejected: number;
    tradesRejectedByStrongBearishOi: number;
    tradesRejectedByBearishOi: number;
    tradesRejectedByVolatilityExpansion: number;
    tradesSkippedForMissingData: number;
    rejectedTradesThatWouldHaveWon: number;
    rejectedTradesThatWouldHaveLost: number;
    falseRejectionRate: number;
    stopExitsAvoided: number;
    netPnlChange: number | null;
    profitFactorChange: number | null;
    expectancyChange: number | null;
    drawdownChange: number | null;
    winRateChange: number | null;
    tradeCountChange: number | null;
    advisoryExecutionIdentical: boolean;
  };
  walkForwardValidation: { method: string; complete: boolean; folds: Array<Record<string, string | number | boolean | null>> };
  acceptance: { passed: boolean; defaultMayBeEnabled: boolean; reason: string };
  runs: Record<OiFilterMode, { summary: Record<string, unknown>; errors: Array<{ symbol: string; message: string }> }>;
  detail?: string;
};

type StoredBacktest = BacktestHistoryEntry<BacktestResponse | RecoveryBacktestResponse>;
type StoredBacktestSummary = BacktestHistorySummary;

const timeframes = ["5m", "15m", "30m", "1h", "2h", "4h", "1d"] as const;
const marketRecommendedDefaults = strategyDefaults("market_aligned_rsi_scalper");
const rangeRecommendedDefaults = strategyDefaults("rsi_range");
const recoveryRecommendedDefaults = strategyDefaults("rsi_recovery");

function defaultNumber(strategy: "rsi_range" | "rsi_recovery", key: string) {
  const defaults = strategy === "rsi_range" ? rangeRecommendedDefaults : recoveryRecommendedDefaults;
  return Number(defaults[key]);
}

function numericConstraints(strategy: "rsi_range" | "rsi_recovery", key: string) {
  const definition = parameterDefinition(strategy, key);
  return {
    min: definition.minimum ?? undefined,
    max: definition.maximum ?? undefined,
    step: definition.step ?? undefined,
  };
}

function isRecoveryResponse(value: BacktestResponse | RecoveryBacktestResponse | null): value is RecoveryBacktestResponse {
  return value?.metadata.strategyMode === "rsi_recovery" || value?.metadata.strategyMode === "market_aligned_rsi_scalper";
}

function isRangeResponse(value: BacktestResponse | RecoveryBacktestResponse | null): value is BacktestResponse {
  return value?.metadata.strategyMode === "rsi_range";
}

function strategyDisplayName(strategy: StrategyMode): string {
  if (strategy === "rsi_recovery") return "RSI Recovery Scalping";
  if (strategy === "market_aligned_rsi_scalper") return "Market-Aligned RSI Scalper";
  return "RSI Range Strategy";
}

async function readBacktestPayload(result: Response, batchStart: string): Promise<BacktestPayload> {
  const body = await result.text();
  let payload: unknown;
  try {
    payload = JSON.parse(body);
  } catch {
    throw new Error(
      `Backtest service returned an unreadable response near ${batchStart} (HTTP ${result.status}). Please retry; completed batches are still shown.`,
    );
  }

  if (!payload || typeof payload !== "object") {
    throw new Error(`Backtest service returned an empty response near ${batchStart}. Please retry.`);
  }
  return payload as BacktestPayload;
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

function number(value: number | null, digits = 2) {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function money(value: number | null) {
  return value === null ? "—" : `₹${number(value)}`;
}

function percent(value: number | null) {
  if (value === null) return "—";
  return `${value > 0 ? "+" : ""}${number(value)}%`;
}

function formatIst(value: string | null, daily = false) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    ...(daily ? {} : { hour: "2-digit", minute: "2-digit", hour12: false }),
    timeZone: "Asia/Kolkata",
  }).format(parsed) + (daily ? "" : " IST");
}

function tone(value: number | null) {
  if (value === null || value === 0) return "neutral-value";
  return value > 0 ? "positive-value" : "negative-value";
}

function parseOptimizationGrid(value: string, label: string, wholeNumbers = false): number[] {
  const parsed = Array.from(new Set(value.split(",").map((item) => Number(item.trim()))));
  if (!parsed.length || parsed.some((item) => !Number.isFinite(item) || item <= 0 || (wholeNumbers && !Number.isInteger(item)))) {
    throw new Error(`${label} must be a comma-separated list of positive ${wholeNumbers ? "whole numbers" : "numbers"}.`);
  }
  return parsed;
}

function parseArmZoneGrid(value: string): Array<[number, number]> {
  const zones = value.split(",").map((item) => {
    const [lowText, highText, ...extra] = item.trim().split(/\s*[-–]\s*/);
    const low = Number(lowText);
    const high = Number(highText);
    if (extra.length || !Number.isFinite(low) || !Number.isFinite(high) || !(0 <= low && low < high && high <= 100)) {
      throw new Error("RSI arm zones must use low-high pairs such as 20-35, 25-35, 30-40.");
    }
    return [low, high] as [number, number];
  });
  if (!zones.length) throw new Error("Add at least one RSI arm zone.");
  return zones;
}

function chartDecisionTitle(point: ChartPoint) {
  const lines = [
    `${point.action?.toUpperCase() ?? "PRICE"} - ${formatIst(point.time)}`,
    `RSI: ${number(point.signalRsi ?? point.rsi)} | Chart close: ${money(point.close)}`,
  ];

  if (!point.action) {
    lines.push(`Equity: ${money(point.equity)}`);
  } else if (point.action === "buy") {
    lines.push(`Buy price: ${money(point.entryPrice ?? null)}`);
    lines.push(`Entry fee estimate: ${money(point.estimatedFees ?? null)}`);
    lines.push("Condition: low-RSI signal, executed at the next candle open.");
  } else {
    lines.push(`Buy price: ${money(point.entryPrice ?? null)}`);
    lines.push(`Candidate sell price: ${money(point.candidateExitPrice ?? null)}`);
    lines.push(`Net return after fees and slippage: ${percent(point.netReturnPct ?? null)}`);
    lines.push(`Required net profit: at least ${number(point.requiredNetProfitPct ?? 1)}%`);
    lines.push(`Total fee estimate: ${money(point.estimatedFees ?? null)}`);
  }

  if (point.reason) lines.push(point.reason);
  return lines.join("\n");
}

function formatChartAxisIst(value: string, daily: boolean): [string, string?] {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return [value];
  const date = new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
    timeZone: "Asia/Kolkata",
  }).format(parsed);
  if (daily) return [date];
  const time = new Intl.DateTimeFormat("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  }).format(parsed);
  return [date, time];
}

function PerformanceChart({
  points,
  entryRange,
  exitRange,
  timeframe,
}: {
  points: ChartPoint[];
  entryRange: [number, number];
  exitRange: [number, number];
  timeframe: string;
}) {
  const valid = useMemo(
    () => points.filter((point) => point.close !== null && point.rsi !== null),
    [points],
  );
  const [activePoint, setActivePoint] = useState<{
    point: ChartPoint;
    left: number;
    top: number;
    placement: "above" | "below";
  } | null>(null);
  const [windowState, setWindowState] = useState({ start: 0, count: Math.max(valid.length, 2) });
  const [dragging, setDragging] = useState(false);
  const chartCanvasRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ pointerId: number; startX: number; startIndex: number } | null>(null);

  if (valid.length < 2) return <div className="chart-empty">Not enough points to draw a chart.</div>;

  const width = 1000;
  const height = 380;
  const plotLeft = 78;
  const plotRight = 982;
  const plotWidth = plotRight - plotLeft;
  const priceTop = 22;
  const priceBottom = 220;
  const rsiTop = 258;
  const rsiBottom = 310;
  const minimumWindow = Math.min(18, valid.length);
  const visibleCount = Math.max(minimumWindow, Math.min(windowState.count, valid.length));
  const maximumStart = Math.max(0, valid.length - visibleCount);
  const visibleStart = Math.min(windowState.start, maximumStart);
  const visible = valid.slice(visibleStart, visibleStart + visibleCount);
  const prices = visible.map((point) => point.close as number);
  const rawMinPrice = Math.min(...prices);
  const rawMaxPrice = Math.max(...prices);
  const rawSpread = rawMaxPrice - rawMinPrice;
  const pricePadding = rawSpread > 0 ? rawSpread * 0.08 : Math.max(Math.abs(rawMaxPrice) * 0.02, 1);
  const minPrice = rawMinPrice - pricePadding;
  const maxPrice = rawMaxPrice + pricePadding;
  const spread = Math.max(maxPrice - minPrice, 1);
  const x = (index: number) => plotLeft + (index / Math.max(visible.length - 1, 1)) * plotWidth;
  const priceY = (value: number) => priceBottom - ((value - minPrice) / spread) * (priceBottom - priceTop);
  const rsiY = (value: number) => rsiBottom - (value / 100) * (rsiBottom - rsiTop);
  const priceLine = visible.map((point, index) => `${x(index)},${priceY(point.close as number)}`).join(" ");
  const rsiLine = visible.map((point, index) => `${x(index)},${rsiY(point.rsi as number)}`).join(" ");
  const priceTicks = Array.from({ length: 5 }, (_, index) => minPrice + (spread * index) / 4);
  const xTickIndices = Array.from(
    new Set(Array.from({ length: 6 }, (_, index) => Math.round(index * (visible.length - 1) / 5))),
  );

  const zoom = (factor: number, anchor = 0.5) => {
    setWindowState((current) => {
      const currentCount = Math.max(minimumWindow, Math.min(current.count, valid.length));
      const nextCount = Math.max(minimumWindow, Math.min(valid.length, Math.round(currentCount * factor)));
      const anchorIndex = Math.min(current.start, valid.length - currentCount) + currentCount * anchor;
      const nextStart = Math.max(0, Math.min(valid.length - nextCount, Math.round(anchorIndex - nextCount * anchor)));
      return { start: nextStart, count: nextCount };
    });
    setActivePoint(null);
  };

  const panTo = (start: number) => {
    setWindowState((current) => ({
      ...current,
      start: Math.max(0, Math.min(valid.length - Math.min(current.count, valid.length), start)),
    }));
    setActivePoint(null);
  };

  const activatePoint = (point: ChartPoint, index: number) => {
    const pointY = priceY(point.close as number);
    setActivePoint({
      point,
      left: Math.min(86, Math.max(14, x(index) / width * 100)),
      top: pointY / height * 100,
      placement: pointY < 120 ? "below" : "above",
    });
  };

  const resetChart = () => {
    setWindowState({ start: 0, count: valid.length });
    setActivePoint(null);
  };

  return (
    <div className="performance-chart">
      <div className="chart-toolbar">
        <div className="chart-legend">
          <span><i className="legend-price" />Price</span>
          <span><i className="legend-rsi" />RSI</span>
          <span><i className="legend-buy" />Buy</span>
          <span><i className="legend-sell" />Sell</span>
          <span><i className="legend-hold" />Hold</span>
        </div>
        <div className="chart-controls" aria-label="Chart zoom controls">
          <span>{visibleStart + 1}-{visibleStart + visible.length} of {valid.length}</span>
          <button type="button" onClick={() => zoom(1.5)} disabled={visibleCount >= valid.length} aria-label="Zoom out"><ZoomOut size={14} /></button>
          <button type="button" onClick={() => zoom(0.65)} disabled={visibleCount <= minimumWindow} aria-label="Zoom in"><ZoomIn size={14} /></button>
          <button type="button" onClick={resetChart} disabled={visibleCount >= valid.length && visibleStart === 0} aria-label="Reset chart"><RotateCcw size={14} /></button>
        </div>
      </div>
      <div
        ref={chartCanvasRef}
        className={`chart-canvas ${dragging ? "dragging" : ""}`}
        onWheel={(event) => {
          event.preventDefault();
          if (Math.abs(event.deltaX) > Math.abs(event.deltaY) || event.shiftKey) {
            panTo(visibleStart + Math.sign(event.deltaX || event.deltaY) * Math.max(1, Math.round(visibleCount * 0.08)));
            return;
          }
          const bounds = event.currentTarget.getBoundingClientRect();
          const anchor = Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width));
          zoom(event.deltaY > 0 ? 1.22 : 0.82, anchor);
        }}
        onPointerDown={(event) => {
          if (event.button !== 0 || visibleCount >= valid.length) return;
          dragRef.current = { pointerId: event.pointerId, startX: event.clientX, startIndex: visibleStart };
          event.currentTarget.setPointerCapture(event.pointerId);
          setDragging(true);
          setActivePoint(null);
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current;
          if (!drag || drag.pointerId !== event.pointerId) return;
          const bounds = chartCanvasRef.current?.getBoundingClientRect();
          if (!bounds) return;
          const candleDelta = Math.round((drag.startX - event.clientX) / bounds.width * visibleCount);
          panTo(drag.startIndex + candleDelta);
        }}
        onPointerUp={(event) => {
          if (dragRef.current?.pointerId === event.pointerId) {
            dragRef.current = null;
            if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
            setDragging(false);
          }
        }}
        onPointerCancel={() => { dragRef.current = null; setDragging(false); }}
      >
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Interactive historical price and RSI chart with IST date and time axes">
          <defs><clipPath id="backtest-chart-clip"><rect x={plotLeft} y={priceTop} width={plotWidth} height={rsiBottom - priceTop} /></clipPath></defs>
          <rect x={plotLeft} y={rsiY(exitRange[1])} width={plotWidth} height={rsiY(exitRange[0]) - rsiY(exitRange[1])} className="chart-exit-zone" />
          <rect x={plotLeft} y={rsiY(entryRange[1])} width={plotWidth} height={rsiY(entryRange[0]) - rsiY(entryRange[1])} className="chart-entry-zone" />
          {priceTicks.map((tick) => (
            <g key={tick}>
              <line x1={plotLeft} x2={plotRight} y1={priceY(tick)} y2={priceY(tick)} className="chart-grid-line" />
              <text x={plotLeft - 9} y={priceY(tick) + 3} textAnchor="end" className="chart-axis-label">{number(tick)}</text>
            </g>
          ))}
          {[30, 50, 70].map((tick) => <text key={tick} x={plotLeft - 9} y={rsiY(tick) + 3} textAnchor="end" className="chart-axis-label">{tick}</text>)}
          {xTickIndices.map((index) => {
            const [dateLabel, timeLabel] = formatChartAxisIst(visible[index].time, timeframe === "1d");
            return (
              <g key={`${visible[index].time}-${index}`}>
                <line x1={x(index)} x2={x(index)} y1={priceTop} y2={rsiBottom} className="chart-grid-line vertical" />
                <text x={x(index)} y="334" textAnchor="middle" className="chart-axis-label date-time">
                  <tspan x={x(index)}>{dateLabel}</tspan>
                  {timeLabel && <tspan x={x(index)} dy="12">{timeLabel}</tspan>}
                </text>
              </g>
            );
          })}
          <line x1={plotLeft} x2={plotRight} y1={priceBottom} y2={priceBottom} className="chart-axis" />
          <line x1={plotLeft} x2={plotRight} y1={rsiTop} y2={rsiTop} className="chart-divider" />
          <text x="15" y={(priceTop + priceBottom) / 2} transform={`rotate(-90 15 ${(priceTop + priceBottom) / 2})`} textAnchor="middle" className="chart-axis-title">Price (INR)</text>
          <text x="15" y={(rsiTop + rsiBottom) / 2} transform={`rotate(-90 15 ${(rsiTop + rsiBottom) / 2})`} textAnchor="middle" className="chart-axis-title">RSI</text>
          <text x={(plotLeft + plotRight) / 2} y="372" textAnchor="middle" className="chart-axis-title">Date and time (IST)</text>
          <g clipPath="url(#backtest-chart-clip)">
            <polyline points={priceLine} className="chart-price-line" />
            <polyline points={rsiLine} className="chart-rsi-line" />
            {visible.map((point, index) => (
              <circle
                key={`${point.time}-target`}
                cx={x(index)}
                cy={priceY(point.close as number)}
                r="10"
                className="chart-point-target"
                onMouseEnter={() => activatePoint(point, index)}
                onMouseLeave={() => setActivePoint(null)}
              />
            ))}
            {visible.map((point, index) => point.action ? (
              <circle
                key={`${point.time}-${point.action}`}
                cx={x(index)}
                cy={priceY(point.close as number)}
                r="6"
                className={`chart-action ${point.action}`}
                tabIndex={0}
                aria-label={chartDecisionTitle(point)}
                onMouseEnter={() => activatePoint(point, index)}
                onMouseLeave={() => setActivePoint(null)}
                onFocus={() => activatePoint(point, index)}
                onBlur={() => setActivePoint(null)}
              >
                <title>{chartDecisionTitle(point)}</title>
              </circle>
            ) : null)}
          </g>
        </svg>
        {activePoint && (
          <div
            className={`chart-decision-tooltip ${activePoint.point.action ?? "point"} ${activePoint.placement}`}
            style={{ left: `${activePoint.left}%`, top: `${activePoint.top}%` }}
            role="tooltip"
          >
            <div className="decision-tooltip-top">
              <strong>{activePoint.point.action?.toUpperCase() ?? "CANDLE"}</strong>
              <span>{formatIst(activePoint.point.time)}</span>
            </div>
            <p>{activePoint.point.reason ?? "Price, RSI, and portfolio value at this candle."}</p>
            <dl>
              <div><dt>Close price</dt><dd>{money(activePoint.point.close)}</dd></div>
              <div><dt>RSI</dt><dd>{number(activePoint.point.signalRsi ?? activePoint.point.rsi)}</dd></div>
              {!activePoint.point.action && <div><dt>Portfolio value</dt><dd>{money(activePoint.point.equity)}</dd></div>}
              {activePoint.point.action && <div><dt>Buy price</dt><dd>{money(activePoint.point.entryPrice ?? null)}</dd></div>}
              {activePoint.point.action && activePoint.point.action !== "buy" && <>
                <div><dt>Candidate sell</dt><dd>{money(activePoint.point.candidateExitPrice ?? null)}</dd></div>
                <div><dt>Net after costs</dt><dd className={tone(activePoint.point.netReturnPct ?? null)}>{percent(activePoint.point.netReturnPct ?? null)}</dd></div>
                <div><dt>Required</dt><dd>at least {number(activePoint.point.requiredNetProfitPct ?? 1)}%</dd></div>
              </>}
              {activePoint.point.action && <div><dt>Estimated fees</dt><dd>{money(activePoint.point.estimatedFees ?? null)}</dd></div>}
            </dl>
          </div>
        )}
      </div>
      <div className="chart-navigator">
        <span>{formatIst(valid[0].time, timeframe === "1d")}</span>
        <input type="range" min="0" max={maximumStart} step="1" value={visibleStart} disabled={maximumStart === 0} onChange={(event) => panTo(Number(event.target.value))} aria-label="Scroll through chart dates" />
        <span>{formatIst(valid.at(-1)?.time ?? null, timeframe === "1d")}</span>
      </div>
      <div className="chart-scale">
        <span><MoveHorizontal size={12} /> Drag to scroll / wheel to zoom</span>
        <span>Price axis auto-fits visible candles</span>
        <span>RSI {entryRange[0]}-{entryRange[1]} buy / {exitRange[0]}-{exitRange[1]} sell check</span>
      </div>
    </div>
  );
}

export function BacktestDashboard({ symbols, userName, signOutHref }: BacktestDashboardProps) {
  const [darkMode, setDarkMode] = useState(true);
  const [strategyMode, setStrategyMode] = useState<StrategyMode>("rsi_range");
  const [availableSymbols, setAvailableSymbols] = useState<string[]>(symbols);
  const [symbolRegistryError, setSymbolRegistryError] = useState<string | null>(null);
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>(symbols.includes("LUPIN") ? ["LUPIN"] : symbols.slice(0, 1));
  const [useAllSymbols, setUseAllSymbols] = useState(false);
  const [symbolQuery, setSymbolQuery] = useState("");
  const [symbolMenuOpen, setSymbolMenuOpen] = useState(false);
  const [durationYears, setDurationYears] = useState<1 | 3>(1);
  const [timeframe, setTimeframe] = useState<(typeof timeframes)[number]>("1d");
  const [entryLow, setEntryLow] = useState(defaultNumber("rsi_range", "entryLow"));
  const [entryHigh, setEntryHigh] = useState(defaultNumber("rsi_range", "entryHigh"));
  const [exitLow, setExitLow] = useState(defaultNumber("rsi_range", "exitLow"));
  const [exitHigh, setExitHigh] = useState(defaultNumber("rsi_range", "exitHigh"));
  const [rsiLength, setRsiLength] = useState(defaultNumber("rsi_recovery", "rsiLength"));
  const [rsiArmLow, setRsiArmLow] = useState(defaultNumber("rsi_recovery", "rsiArmLow"));
  const [rsiArmHigh, setRsiArmHigh] = useState(defaultNumber("rsi_recovery", "rsiArmHigh"));
  const [rsiRecovery, setRsiRecovery] = useState(defaultNumber("rsi_recovery", "rsiRecovery"));
  const [setupExpiryBars, setSetupExpiryBars] = useState(defaultNumber("rsi_recovery", "setupExpiryBars"));
  const [emaEnabled, setEmaEnabled] = useState(true);
  const [emaFast, setEmaFast] = useState(defaultNumber("rsi_recovery", "emaFast"));
  const [emaSlow, setEmaSlow] = useState(defaultNumber("rsi_recovery", "emaSlow"));
  const [vwapEnabled, setVwapEnabled] = useState(true);
  const [volumeEnabled, setVolumeEnabled] = useState(true);
  const [volumeEma, setVolumeEma] = useState(defaultNumber("rsi_recovery", "volumeEma"));
  const [minimumConfirmations, setMinimumConfirmations] = useState(defaultNumber("rsi_recovery", "minimumConfirmations"));
  const [targetPct, setTargetPct] = useState(defaultNumber("rsi_recovery", "targetPct"));
  const [executionModel, setExecutionModel] = useState<"SIGNAL_CLOSE" | "NEXT_BAR_OPEN">("SIGNAL_CLOSE");
  const [buyCostBps, setBuyCostBps] = useState(defaultNumber("rsi_recovery", "buyCostBps"));
  const [sellCostBps, setSellCostBps] = useState(defaultNumber("rsi_recovery", "sellCostBps"));
  const [slippageBps, setSlippageBps] = useState(defaultNumber("rsi_recovery", "slippageBps"));
  const [exitModel, setExitModel] = useState<"LEGACY_FIXED_TARGET" | "FIXED_TP_SL" | "ATR_DYNAMIC_TP_SL" | "RSI_PROFIT_RISK_CONTROL">("LEGACY_FIXED_TARGET");
  const [fixedStopLossPct, setFixedStopLossPct] = useState(defaultNumber("rsi_recovery", "fixedStopLossPct"));
  const [atrLength, setAtrLength] = useState(defaultNumber("rsi_recovery", "atrLength"));
  const [stopAtrMultiplier, setStopAtrMultiplier] = useState(defaultNumber("rsi_recovery", "stopAtrMultiplier"));
  const [rewardRiskRatio, setRewardRiskRatio] = useState(defaultNumber("rsi_recovery", "rewardRiskRatio"));
  const [minimumStopPct, setMinimumStopPct] = useState(defaultNumber("rsi_recovery", "minimumStopPct"));
  const [maximumStopPct, setMaximumStopPct] = useState(defaultNumber("rsi_recovery", "maximumStopPct"));
  const [positionSizing, setPositionSizing] = useState<"FIXED_QUANTITY" | "RISK_BUDGET">("FIXED_QUANTITY");
  const [quantityPerTrade, setQuantityPerTrade] = useState(defaultNumber("rsi_recovery", "quantityPerTrade"));
  const [rupeeRiskBudget, setRupeeRiskBudget] = useState(defaultNumber("rsi_recovery", "rupeeRiskBudget"));
  const [maximumQuantity, setMaximumQuantity] = useState(defaultNumber("rsi_recovery", "maximumQuantity"));
  const [maximumCapitalPerPosition, setMaximumCapitalPerPosition] = useState(defaultNumber("rsi_recovery", "maximumCapitalPerPosition"));
  const [maxOpenLotsPerSymbol, setMaxOpenLotsPerSymbol] = useState(defaultNumber("rsi_recovery", "maxOpenLotsPerSymbol"));
  const [maxHoldingTradingDays, setMaxHoldingTradingDays] = useState(defaultNumber("rsi_recovery", "maxHoldingTradingDays"));
  const [minimumProfitPct, setMinimumProfitPct] = useState(defaultNumber("rsi_recovery", "minimumProfitPct"));
  const [profitExitRsi, setProfitExitRsi] = useState(defaultNumber("rsi_recovery", "profitExitRsi"));
  const [upperRsiLevel, setUpperRsiLevel] = useState(defaultNumber("rsi_recovery", "upperRsiLevel"));
  const [hardStopLossPct, setHardStopLossPct] = useState(defaultNumber("rsi_recovery", "hardStopLossPct"));
  const [rsiExitExecutionModel, setRsiExitExecutionModel] = useState<"SIGNAL_CLOSE" | "NEXT_BAR_OPEN">("SIGNAL_CLOSE");
  const [oiFilterMode, setOiFilterMode] = useState<OiFilterMode>("ADVISORY");
  const [signalRsiMaximum, setSignalRsiMaximum] = useState(50);
  const [minimumRvol, setMinimumRvol] = useState(1.5);
  const [minimumNiftyTrendScore, setMinimumNiftyTrendScore] = useState(25);
  const [minimumBreadthPct, setMinimumBreadthPct] = useState(45);
  const [minimumBreadthSymbols, setMinimumBreadthSymbols] = useState(10);
  const [minimumSectorMembers, setMinimumSectorMembers] = useState(2);
  const [relativeStrengthLookbackBars, setRelativeStrengthLookbackBars] = useState(3);
  const [roomLookbackBars, setRoomLookbackBars] = useState(20);
  const [minimumAverageTradedValue, setMinimumAverageTradedValue] = useState(100000);
  const [maximumIntrabarRangePct, setMaximumIntrabarRangePct] = useState(5);
  const [minimumAlignmentScore, setMinimumAlignmentScore] = useState(85);
  const [minimumSectorBullishPct, setMinimumSectorBullishPct] = useState(50);
  const [marketPreset, setMarketPreset] = useState<MarketAlignedPreset>("Recommended");
  const [entryStartTime, setEntryStartTime] = useState("09:20");
  const [lastEntryTime, setLastEntryTime] = useState("14:45");
  const [squareOffTime, setSquareOffTime] = useState("15:15");
  const [marketStopLossPct, setMarketStopLossPct] = useState(1);
  const [maximumHoldingBars, setMaximumHoldingBars] = useState(12);
  const [maximumTradesPerDay, setMaximumTradesPerDay] = useState(5);
  const [maximumOpenPositions, setMaximumOpenPositions] = useState(3);
  const [maximumDailyLossPct, setMaximumDailyLossPct] = useState(2);
  const [stopAfterFirstLoss, setStopAfterFirstLoss] = useState(false);
  const [cooldownBars, setCooldownBars] = useState(1);
  const [maximumSpreadPct, setMaximumSpreadPct] = useState(0.5);
  const [marketDataStaleSeconds, setMarketDataStaleSeconds] = useState(360);
  const [oiMinimumConfidence, setOiMinimumConfidence] = useState(0.5);
  const [numericErrors, setNumericErrors] = useState<Record<string, string>>({});
  const [marketFormRevision, setMarketFormRevision] = useState(0);
  const [oiHistoryStatus, setOiHistoryStatus] = useState<OiHistoryStatus | null>(null);
  const [oiLookbackBars, setOiLookbackBars] = useState(3);
  const [oiStrikesEachSide, setOiStrikesEachSide] = useState(5);
  const [oiMinimumPriceChangePct, setOiMinimumPriceChangePct] = useState(0.05);
  const [oiMinimumChangePct, setOiMinimumChangePct] = useState(0.5);
  const [oiMaximumSpreadPct, setOiMaximumSpreadPct] = useState(20);
  const [oiStaleDataSeconds, setOiStaleDataSeconds] = useState(360);
  const [oiMinimumValidContractFraction, setOiMinimumValidContractFraction] = useState(0.5);
  const [oiMinimumFuturesVolume, setOiMinimumFuturesVolume] = useState(1);
  const [oiVolatilityPriceRisePct, setOiVolatilityPriceRisePct] = useState(0.25);
  const [oiVolatilityIvRise, setOiVolatilityIvRise] = useState(0.5);
  const [oiMinimumCoverage, setOiMinimumCoverage] = useState(0.65);
  const [oiOptionsWeight, setOiOptionsWeight] = useState(0.35);
  const [oiFuturesWeight, setOiFuturesWeight] = useState(0.35);
  const [oiSpotWeight, setOiSpotWeight] = useState(0.30);
  const [oiStronglyBearishThreshold, setOiStronglyBearishThreshold] = useState(-60);
  const [oiBearishThreshold, setOiBearishThreshold] = useState(-20);
  const [oiBullishThreshold, setOiBullishThreshold] = useState(20);
  const [oiStronglyBullishThreshold, setOiStronglyBullishThreshold] = useState(60);
  const [oiElevatedQualityThreshold, setOiElevatedQualityThreshold] = useState(95);
  const [oiFailPolicy, setOiFailPolicy] = useState<"SKIP" | "ALLOW">("SKIP");

  const setNumericValidity = (key: string, value: string | null) => {
    setNumericErrors((current) => {
      if (!value) {
        if (!(key in current)) return current;
        const next = { ...current };
        delete next[key];
        return next;
      }
      return current[key] === value ? current : { ...current, [key]: value };
    });
  };

  const markMarketPresetCustom = () => setMarketPreset("Custom");

  const applyMarketValues = (values: Record<string, number | string | boolean>) => {
    const numeric = (key: string) => Number(values[key] ?? marketRecommendedDefaults[key]);
    setRsiLength(numeric("rsiLength"));
    setRsiArmLow(numeric("rsiArmLow"));
    setRsiArmHigh(numeric("rsiArmHigh"));
    setRsiRecovery(numeric("rsiRecovery"));
    setSignalRsiMaximum(numeric("signalRsiMaximum"));
    setSetupExpiryBars(numeric("setupExpiryBars"));
    setEmaFast(numeric("emaFast"));
    setEmaSlow(numeric("emaSlow"));
    setVolumeEma(numeric("rvolPeriod"));
    setMinimumRvol(numeric("minimumRvol"));
    setMinimumNiftyTrendScore(numeric("minimumNiftyTrendScore"));
    setMinimumSectorBullishPct(numeric("minimumSectorBullishPct"));
    setMinimumBreadthPct(numeric("minimumBreadthPct"));
    setTargetPct(numeric("targetPct"));
    setMarketStopLossPct(numeric("stopLossPct"));
    setQuantityPerTrade(numeric("quantityPerTrade"));
    setMaximumHoldingBars(numeric("maximumHoldingBars"));
    setMaximumTradesPerDay(numeric("maximumTradesPerDay"));
    setMaximumOpenPositions(numeric("maximumOpenPositions"));
    setMinimumAlignmentScore(numeric("minimumAlignmentScore"));
    setBuyCostBps(numeric("buyCostBps"));
    setSellCostBps(numeric("sellCostBps"));
    setSlippageBps(numeric("slippageBps"));
    setMaximumCapitalPerPosition(numeric("maximumCapitalPerPosition"));
    setMaximumDailyLossPct(numeric("maximumDailyLossPct"));
    setCooldownBars(numeric("cooldownBars"));
    setMaximumSpreadPct(numeric("maximumSpreadPct"));
    setMinimumAverageTradedValue(numeric("minimumAverageTradedValue"));
    setRoomLookbackBars(numeric("roomLookbackBars"));
    setOiMinimumConfidence(numeric("oiMinimumConfidence"));
    setOiStronglyBearishThreshold(numeric("oiStronglyBearishThreshold"));
    setOiStaleDataSeconds(numeric("oiStaleDataSeconds"));
    setRelativeStrengthLookbackBars(numeric("relativeStrengthLookbackBars"));
    setMinimumBreadthSymbols(numeric("minimumBreadthSymbols"));
    setMinimumSectorMembers(numeric("minimumSectorMembers"));
    setMaximumIntrabarRangePct(numeric("maximumIntrabarRangePct"));
    setMarketDataStaleSeconds(numeric("marketDataStaleSeconds"));
    setOiLookbackBars(numeric("oiLookbackBars"));
    setOiStrikesEachSide(numeric("oiStrikesEachSide"));
    setOiMinimumPriceChangePct(numeric("oiMinimumPriceChangePct"));
    setOiMinimumChangePct(numeric("oiMinimumChangePct"));
    setOiMaximumSpreadPct(numeric("oiMaximumSpreadPct"));
    setOiMinimumValidContractFraction(numeric("oiMinimumValidContractFraction"));
    setOiMinimumFuturesVolume(numeric("oiMinimumFuturesVolume"));
    setOiVolatilityPriceRisePct(numeric("oiVolatilityPriceRisePct"));
    setOiVolatilityIvRise(numeric("oiVolatilityIvRise"));
    setOiMinimumCoverage(numeric("oiMinimumCoverage"));
    setOiOptionsWeight(numeric("oiOptionsWeight"));
    setOiFuturesWeight(numeric("oiFuturesWeight"));
    setOiSpotWeight(numeric("oiSpotWeight"));
    setOiBearishThreshold(numeric("oiBearishThreshold"));
    setOiBullishThreshold(numeric("oiBullishThreshold"));
    setOiStronglyBullishThreshold(numeric("oiStronglyBullishThreshold"));
    setOiElevatedQualityThreshold(numeric("oiElevatedQualityThreshold"));
    setExecutionModel(String(values.executionModel ?? "SIGNAL_CLOSE") as typeof executionModel);
    setPositionSizing(String(values.positionSizing ?? "FIXED_QUANTITY") as typeof positionSizing);
    setEntryStartTime(String(values.entryStartTime ?? "09:20"));
    setLastEntryTime(String(values.lastEntryTime ?? "14:45"));
    setSquareOffTime(String(values.squareOffTime ?? "15:15"));
    setStopAfterFirstLoss(Boolean(values.stopAfterFirstLoss));
    setOiFilterMode(String(values.oiMode ?? "ADVISORY") as OiFilterMode);
    setOiFailPolicy(String(values.oiFailPolicy ?? "SKIP") as typeof oiFailPolicy);
    setNumericErrors({});
    setMarketFormRevision((current) => current + 1);
  };

  const applyMarketPreset = (preset: Exclude<MarketAlignedPreset, "Custom">) => {
    const values = {
      ...marketRecommendedDefaults,
      ...(preset === "Strict" ? { minimumAlignmentScore: 90, maximumTradesPerDay: 2, oiMode: "ADVISORY" } : {}),
    };
    applyMarketValues(values);
    setMarketPreset(preset);
  };

  const restoreExpertOiDefaults = () => {
    const numeric = (key: string) => Number(marketRecommendedDefaults[key]);
    setOiLookbackBars(numeric("oiLookbackBars"));
    setOiStrikesEachSide(numeric("oiStrikesEachSide"));
    setOiMinimumPriceChangePct(numeric("oiMinimumPriceChangePct"));
    setOiMinimumChangePct(numeric("oiMinimumChangePct"));
    setOiMaximumSpreadPct(numeric("oiMaximumSpreadPct"));
    setOiMinimumValidContractFraction(numeric("oiMinimumValidContractFraction"));
    setOiMinimumFuturesVolume(numeric("oiMinimumFuturesVolume"));
    setOiVolatilityPriceRisePct(numeric("oiVolatilityPriceRisePct"));
    setOiVolatilityIvRise(numeric("oiVolatilityIvRise"));
    setOiMinimumCoverage(numeric("oiMinimumCoverage"));
    setOiOptionsWeight(numeric("oiOptionsWeight"));
    setOiFuturesWeight(numeric("oiFuturesWeight"));
    setOiSpotWeight(numeric("oiSpotWeight"));
    setOiBearishThreshold(numeric("oiBearishThreshold"));
    setOiBullishThreshold(numeric("oiBullishThreshold"));
    setOiStronglyBullishThreshold(numeric("oiStronglyBullishThreshold"));
    setOiElevatedQualityThreshold(numeric("oiElevatedQualityThreshold"));
    setOiFailPolicy(String(marketRecommendedDefaults.oiFailPolicy ?? "SKIP") as typeof oiFailPolicy);
    setNumericErrors({});
    setMarketFormRevision((current) => current + 1);
    markMarketPresetCustom();
  };

  const marketRelationshipErrors = useMemo(() => {
    const errors: Record<string, string> = {};
    if (!(rsiArmLow < rsiArmHigh)) errors.rsiArmHigh = "RSI arm minimum must be below RSI arm maximum.";
    if (!(rsiArmHigh < rsiRecovery)) errors.rsiRecovery = "RSI arm maximum must be below the recovery level.";
    if (!(rsiRecovery <= signalRsiMaximum)) errors.signalRsiMaximum = "Maximum signal RSI must be at least the recovery level.";
    if (!(emaFast < emaSlow)) errors.emaSlow = "EMA fast length must be below EMA slow length.";
    if (!(entryStartTime < lastEntryTime && lastEntryTime < squareOffTime)) errors.sessionTimes = "Use entry start < last entry < square-off.";
    const weightTotal = oiOptionsWeight + oiFuturesWeight + oiSpotWeight;
    if (Math.abs(weightTotal - 1) > 1e-9) errors.oiWeights = "Options, futures and spot weights must total 1.00.";
    if (!(oiStronglyBearishThreshold < oiBearishThreshold && oiBearishThreshold < oiBullishThreshold && oiBullishThreshold < oiStronglyBullishThreshold)) errors.oiThresholds = "Use strongly bearish < bearish < bullish < strongly bullish.";
    return errors;
  }, [emaFast, emaSlow, entryStartTime, lastEntryTime, oiBearishThreshold, oiBullishThreshold, oiFuturesWeight, oiOptionsWeight, oiSpotWeight, oiStronglyBearishThreshold, oiStronglyBullishThreshold, rsiArmHigh, rsiArmLow, rsiRecovery, signalRsiMaximum, squareOffTime]);

  const marketFormInvalid = Object.keys(numericErrors).length > 0 || Object.keys(marketRelationshipErrors).length > 0;

  useEffect(() => {
    let active = true;
    void fetch("/api/backtest?action=oi-history-status", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
      cache: "no-store",
    }).then(async (response) => {
      const body = await response.text();
      if (!response.ok) throw new Error("Historical OI status is unavailable");
      return JSON.parse(body) as OiHistoryStatus;
    }).then((status) => {
      if (active) setOiHistoryStatus(status);
    }).catch(() => {
      if (active) setOiHistoryStatus(null);
    });
    return () => { active = false; };
  }, []);
  const [optimizerGrid, setOptimizerGrid] = useState({
    stopAtrMultipliers: "0.75, 1.00, 1.25, 1.50, 2.00",
    rewardRiskRatios: "1.00, 1.25, 1.50, 2.00",
    maxHoldingSessions: "1, 3, 5",
    minimumStopPcts: "0.50, 0.75, 1.00",
    maximumStopPcts: "2.00, 3.00, 5.00",
  });
  const [optimizing, setOptimizing] = useState(false);
  const [optimization, setOptimization] = useState<AtrOptimizationResponse | null>(null);
  const [rsiComparisonGrid, setRsiComparisonGrid] = useState({
    armZones: "20-35, 25-35, 30-40",
    recoveryThresholds: "35, 40, 45",
    profitExitRsiLevels: "50, 60, 70",
    minimumProfitPcts: "0.50, 1.00",
    hardStopLossPcts: "1.00, 1.50, 2.00, 3.00",
    maxHoldingSessions: "3, 5, 10",
  });
  const [comparingRsiExits, setComparingRsiExits] = useState(false);
  const [rsiComparison, setRsiComparison] = useState<RsiExitComparisonResponse | null>(null);
  const [comparingOi, setComparingOi] = useState(false);
  const [oiComparison, setOiComparison] = useState<OiFilterComparisonResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<BacktestResponse | RecoveryBacktestResponse | null>(null);
  const [backtestHistory, setBacktestHistory] = useState<StoredBacktestSummary[]>([]);
  const [activeHistoryId, setActiveHistoryId] = useState<string | null>(null);
  const [loadingHistoryId, setLoadingHistoryId] = useState<string | null>(null);
  const [historyMessage, setHistoryMessage] = useState<string | null>(null);
  const [detailSymbol, setDetailSymbol] = useState<string | null>(null);
  const [runProgress, setRunProgress] = useState<{ completed: number; total: number } | null>(null);
  const runAbortRef = useRef<AbortController | null>(null);
  const exitProtectionEnabled = exitModel !== "LEGACY_FIXED_TARGET";

  useEffect(() => {
    let active = true;
    const loadSymbols = () => {
      void fetch("/api/market-symbols", { cache: "no-store" })
        .then(async (result) => {
          const payload = JSON.parse(await result.text()) as { symbols?: unknown; detail?: string };
          if (!result.ok) throw new Error(payload.detail ?? "The live symbol list is unavailable");
          if (!Array.isArray(payload.symbols)) throw new Error("The live symbol list is invalid");
          const next = Array.from(new Set(payload.symbols.filter((item): item is string => typeof item === "string" && item.length > 0)))
            .sort((left, right) => left.localeCompare(right));
          if (!next.length) throw new Error("The live symbol list is empty");
          if (active) {
            setAvailableSymbols(next);
            setSymbolRegistryError(null);
          }
        })
        .catch(() => {
          if (active) setSymbolRegistryError("Live symbol list unavailable; showing the bundled fallback.");
        });
    };
    loadSymbols();
    window.addEventListener("focus", loadSymbols);
    return () => {
      active = false;
      window.removeEventListener("focus", loadSymbols);
    };
  }, []);

  useEffect(() => {
    let active = true;
    void (async () => {
      let browserEntries: StoredBacktest[] = [];
      try {
        browserEntries = await readBacktestHistory<BacktestResponse | RecoveryBacktestResponse>();
        if (active && browserEntries.length) {
          const latest = browserEntries[0];
          setBacktestHistory(browserEntries.map(backtestHistorySummary));
          setResponse(latest.response);
          setActiveHistoryId(latest.id);
          if (isRangeResponse(latest.response)) setDetailSymbol(latest.response.results[0]?.symbol ?? null);
        }
      } catch {
        if (active) setHistoryMessage("Browser cache is unavailable; account history will still be loaded.");
      }

      try {
        const accountEntries = browserEntries.length
          ? await migrateBrowserBacktestHistory(browserEntries)
          : await readAccountBacktestHistory();
        if (!active) return;
        setBacktestHistory(accountEntries);
        setHistoryMessage(null);
        if (accountEntries.length) {
          const latestSummary = accountEntries[0];
          const cached = browserEntries.find((entry) => entry.id === latestSummary.id);
          const latest = cached ?? await readAccountBacktestResult<BacktestResponse | RecoveryBacktestResponse>(latestSummary.id);
          if (!active) return;
          setResponse(latest.response);
          setActiveHistoryId(latest.id);
          setDetailSymbol(isRangeResponse(latest.response) ? latest.response.results[0]?.symbol ?? null : null);
          if (!cached) void saveBacktestHistory(latest).catch(() => undefined);
        }
      } catch {
        if (active) {
          setHistoryMessage(browserEntries.length
            ? "Account sync is temporarily unavailable; showing this browser's saved results."
            : "Saved backtest history is temporarily unavailable; new backtests will still run.");
        }
      }
    })();
    return () => { active = false; };
  }, []);

  const viewStoredBacktest = async (item: StoredBacktestSummary) => {
    setLoadingHistoryId(item.id);
    try {
      let stored: StoredBacktest;
      try {
        stored = await readAccountBacktestResult<BacktestResponse | RecoveryBacktestResponse>(item.id);
        void saveBacktestHistory(stored).catch(() => undefined);
      } catch {
        const browserEntries = await readBacktestHistory<BacktestResponse | RecoveryBacktestResponse>();
        const cached = browserEntries.find((entry) => entry.id === item.id);
        if (!cached) throw new Error("Saved backtest result is unavailable");
        stored = cached;
      }
      setResponse(stored.response);
      setActiveHistoryId(stored.id);
      setError(null);
      setOptimization(null);
      setRsiComparison(null);
      setOiComparison(null);
      setDetailSymbol(isRangeResponse(stored.response) ? stored.response.results[0]?.symbol ?? null : null);
      setHistoryMessage(null);
    } catch (caught) {
      setHistoryMessage(caught instanceof Error ? caught.message : "Saved backtest result is unavailable");
    } finally {
      setLoadingHistoryId(null);
    }
  };

  const switchStrategy = (next: StrategyMode) => {
    if (strategyMode === "rsi_recovery") {
      window.localStorage.setItem("vento-nse-backtest-preset:rsi_recovery", JSON.stringify({
        rsiLength, rsiArmLow, rsiArmHigh, rsiRecovery, signalRsiMaximum,
        emaFast, emaSlow, volumeEma, minimumRvol, targetPct, setupExpiryBars,
        executionModel, buyCostBps, sellCostBps, slippageBps,
      }));
    }
    if (strategyMode === "market_aligned_rsi_scalper") {
      window.localStorage.setItem("vento-nse-backtest-preset:market_aligned_rsi_scalper", JSON.stringify({
        marketPreset, rsiLength, rsiArmLow, rsiArmHigh, rsiRecovery, signalRsiMaximum,
        emaFast, emaSlow, rvolPeriod: volumeEma, minimumRvol, targetPct,
        stopLossPct: marketStopLossPct, setupExpiryBars, executionModel, positionSizing,
        entryStartTime, lastEntryTime, squareOffTime, quantityPerTrade, maximumHoldingBars,
        maximumTradesPerDay, maximumOpenPositions, buyCostBps, sellCostBps, slippageBps,
        maximumCapitalPerPosition, maximumDailyLossPct, stopAfterFirstLoss, cooldownBars,
        maximumSpreadPct, minimumNiftyTrendScore, minimumSectorBullishPct, minimumBreadthPct,
        minimumBreadthSymbols, minimumSectorMembers, relativeStrengthLookbackBars,
        roomLookbackBars, minimumAverageTradedValue, maximumIntrabarRangePct,
        minimumAlignmentScore, marketDataStaleSeconds, oiMode: oiFilterMode,
        oiMinimumConfidence, oiLookbackBars, oiStrikesEachSide, oiMinimumPriceChangePct,
        oiMinimumChangePct, oiMaximumSpreadPct, oiStaleDataSeconds,
        oiMinimumValidContractFraction, oiMinimumFuturesVolume, oiVolatilityPriceRisePct,
        oiVolatilityIvRise, oiMinimumCoverage, oiOptionsWeight, oiFuturesWeight, oiSpotWeight,
        oiStronglyBearishThreshold, oiBearishThreshold, oiBullishThreshold,
        oiStronglyBullishThreshold, oiElevatedQualityThreshold,
      }));
    }
    if (next === "market_aligned_rsi_scalper") {
      let values = { ...marketRecommendedDefaults };
      try {
        values = { ...values, ...JSON.parse(window.localStorage.getItem("vento-nse-backtest-preset:market_aligned_rsi_scalper") ?? "{}") };
      } catch { values = { ...marketRecommendedDefaults }; }
      applyMarketValues(values);
      setMarketPreset((values.marketPreset as MarketAlignedPreset | undefined) ?? "Recommended");
      setExitModel("LEGACY_FIXED_TARGET");
    } else if (next === "rsi_recovery") {
      const defaults = {
      rsiLength: 14, rsiArmLow: 30, rsiArmHigh: 40, rsiRecovery: 40,
      signalRsiMaximum: 50, emaFast: 9, emaSlow: 20, volumeEma: 20,
      minimumRvol: 1.5, targetPct: 0.5, setupExpiryBars: 50,
      executionModel: "SIGNAL_CLOSE", buyCostBps: 0, sellCostBps: 0,
      slippageBps: 0,
      };
      let preset = defaults;
      try {
        preset = { ...defaults, ...JSON.parse(window.localStorage.getItem("vento-nse-backtest-preset:rsi_recovery") ?? "{}") };
      } catch { preset = defaults; }
      setRsiLength(preset.rsiLength); setRsiArmLow(preset.rsiArmLow); setRsiArmHigh(preset.rsiArmHigh);
      setRsiRecovery(preset.rsiRecovery); setSignalRsiMaximum(preset.signalRsiMaximum);
      setEmaFast(preset.emaFast); setEmaSlow(preset.emaSlow); setVolumeEma(preset.volumeEma);
      setMinimumRvol(preset.minimumRvol); setTargetPct(preset.targetPct); setSetupExpiryBars(preset.setupExpiryBars);
      setExecutionModel(preset.executionModel as typeof executionModel); setBuyCostBps(preset.buyCostBps);
      setSellCostBps(preset.sellCostBps); setSlippageBps(preset.slippageBps);
    }
    setNumericErrors({});
    setStrategyMode(next); if (next !== "rsi_range") setTimeframe("5m");
    setResponse(null); setActiveHistoryId(null); setError(null); setOiComparison(null);
  };

  const choices = useMemo(() => {
    const query = symbolQuery.trim().toUpperCase();
    return availableSymbols.filter((symbol) => !selectedSymbols.includes(symbol) && (!query || symbol.includes(query))).slice(0, 12);
  }, [availableSymbols, selectedSymbols, symbolQuery]);

  const rangeResponse = isRangeResponse(response) ? response : null;
  const detail = rangeResponse?.results.find((result) => result.symbol === detailSymbol) ?? rangeResponse?.results[0] ?? null;
  const profitableCount = rangeResponse?.results.filter((result) => result.verdict === "profitable").length ?? 0;

  const addSymbol = (symbol: string) => {
    if (selectedSymbols.length >= 10) return;
    setUseAllSymbols(false);
    setSelectedSymbols((current) => [...current, symbol]);
    setSymbolQuery("");
    setSymbolMenuOpen(false);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const symbolsToRun = useAllSymbols ? availableSymbols : selectedSymbols;
    if (!symbolsToRun.length) {
      setError("Select at least one symbol.");
      return;
    }
    if (strategyMode === "rsi_range") {
      if (!(entryLow < entryHigh && entryHigh < exitLow && exitLow < exitHigh)) {
        setError("RSI ranges must be ordered from the low entry range to the high exit range.");
        return;
      }
    } else if (strategyMode === "market_aligned_rsi_scalper") {
      if (marketFormInvalid) {
        setError(Object.values(numericErrors)[0] ?? Object.values(marketRelationshipErrors)[0] ?? "Review the highlighted Market-Aligned settings.");
        return;
      }
    } else {
      const rsiValues = [rsiArmLow, rsiArmHigh, rsiRecovery];
      if (!rsiValues.every((value) => Number.isFinite(value) && value >= 0 && value <= 100)) {
        setError("RSI arm and recovery values must be between 0 and 100.");
        return;
      }
      if (!(rsiArmLow < rsiArmHigh)) {
        setError("RSI arm low must be lower than RSI arm high.");
        return;
      }
      if (![rsiLength, emaFast, emaSlow, volumeEma].every((value) => Number.isInteger(value) && value > 0)) {
        setError("RSI, EMA, and volume EMA lengths must be whole numbers greater than 0.");
        return;
      }
      if (!(targetPct > 0)) {
        setError("Profit target must be greater than 0%.");
        return;
      }
      if (!Number.isInteger(setupExpiryBars) || setupExpiryBars < 0) {
        setError("Setup expiry must be 0 or a positive whole number of bars.");
        return;
      }
      const enabledConfirmations = [emaEnabled, vwapEnabled, volumeEnabled].filter(Boolean).length;
      if (minimumConfirmations < 0 || minimumConfirmations > enabledConfirmations) {
        setError(`Minimum confirmations must be between 0 and ${enabledConfirmations}, the number of enabled filters.`);
        return;
      }
      if (![buyCostBps, sellCostBps, slippageBps].every((value) => Number.isFinite(value) && value >= 0)) {
        setError("Cost and slippage assumptions cannot be negative.");
        return;
      }
      if (exitProtectionEnabled && ![quantityPerTrade, maxOpenLotsPerSymbol, maxHoldingTradingDays].every((value) => Number.isInteger(value) && value > 0)) {
        setError("Quantity, maximum open lots, and maximum holding days must be positive whole numbers.");
        return;
      }
      if (exitModel === "FIXED_TP_SL" && !(fixedStopLossPct > 0)) {
        setError("Fixed stop-loss percentage must be greater than 0%.");
        return;
      }
      if (exitModel === "ATR_DYNAMIC_TP_SL" && !(
        Number.isInteger(atrLength) && atrLength > 0
        && stopAtrMultiplier > 0
        && rewardRiskRatio > 0
        && minimumStopPct > 0
        && maximumStopPct >= minimumStopPct
      )) {
        setError("ATR length and multipliers must be positive, and maximum stop must be at least minimum stop.");
        return;
      }
      if (exitModel === "RSI_PROFIT_RISK_CONTROL" && !(
        minimumProfitPct > 0
        && hardStopLossPct > 0 && hardStopLossPct < 100
        && profitExitRsi >= 0 && profitExitRsi <= 100
        && upperRsiLevel >= profitExitRsi && upperRsiLevel <= 100
      )) {
        setError("Minimum profit and hard stop must be positive, and upper RSI must be at or above the profit-exit RSI.");
        return;
      }
      if (exitProtectionEnabled && exitModel !== "RSI_PROFIT_RISK_CONTROL" && positionSizing === "RISK_BUDGET" && !(
        rupeeRiskBudget > 0
        && Number.isInteger(maximumQuantity) && maximumQuantity > 0
        && maximumCapitalPerPosition > 0
      )) {
        setError("Risk budget, maximum quantity, and maximum capital must be positive.");
        return;
      }
    }

    setLoading(true);
    setError(null);
    setResponse(null);
    setActiveHistoryId(null);
    setOptimization(null);
    setRsiComparison(null);
    setOiComparison(null);
    setDetailSymbol(null);
    setRunProgress({ completed: 0, total: symbolsToRun.length });
    const controller = new AbortController();
    runAbortRef.current = controller;
    let completedCount = 0;
    const runId = crypto.randomUUID();
    try {
      let aggregate: BacktestResponse | RecoveryBacktestResponse | null = null;
      // Market breadth and OI portfolio capacity require one chronological cross-symbol stream.
      const batchSize = 10;
      const effectiveBatchSize = strategyMode === "market_aligned_rsi_scalper" ? symbolsToRun.length : batchSize;
      for (let offset = 0; offset < symbolsToRun.length; offset += effectiveBatchSize) {
        const batch = symbolsToRun.slice(offset, offset + effectiveBatchSize);
        const strategyPayload = strategyMode === "rsi_range" ? {
          entryLow,
          entryHigh,
          exitLow,
          exitHigh,
        } : strategyMode === "market_aligned_rsi_scalper" ? {
          marketAlignedConfiguration: {
            rsiLength, rsiArmLow, rsiArmHigh, rsiRecovery, signalRsiMaximum,
            emaFast, emaSlow, rvolPeriod: volumeEma, minimumRvol,
            relativeStrengthLookbackBars, roomLookbackBars, targetPct,
            setupExpiryBars, executionModel, positionSizing,
            entryStartTime, lastEntryTime, squareOffTime, stopLossPct: marketStopLossPct,
            quantityPerTrade, maximumHoldingBars, maximumTradesPerDay, maximumOpenPositions,
            buyCostBps, sellCostBps, slippageBps, maximumCapitalPerPosition,
            maximumDailyLossPct, stopAfterFirstLoss, cooldownBars, maximumSpreadPct,
            minimumNiftyTrendScore, minimumSectorBullishPct, minimumBreadthPct, minimumBreadthSymbols,
            minimumSectorMembers, minimumAverageTradedValue, maximumIntrabarRangePct,
            minimumAlignmentScore, marketDataStaleSeconds,
            oiMode: oiFilterMode, oiLookbackBars, oiStrikesEachSide,
            oiMinimumPriceChangePct, oiMinimumChangePct, oiMaximumSpreadPct,
            oiStaleDataSeconds, oiMinimumConfidence, oiMinimumValidContractFraction, oiMinimumFuturesVolume,
            oiVolatilityPriceRisePct, oiVolatilityIvRise, oiMinimumCoverage,
            oiOptionsWeight, oiFuturesWeight, oiSpotWeight,
            oiStronglyBearishThreshold, oiBearishThreshold, oiBullishThreshold,
            oiStronglyBullishThreshold, oiElevatedQualityThreshold, oiFailPolicy,
          },
        } : {
          rsiLength,
          rsiArmLow,
          rsiArmHigh,
          rsiRecovery,
          emaEnabled,
          emaFast,
          emaSlow,
          vwapEnabled,
          volumeEnabled,
          volumeEma,
          minimumConfirmations,
          targetPct,
          setupExpiryBars,
          executionModel,
          buyCostBps,
          sellCostBps,
          slippageBps,
          exitModel,
          exitProtectionEnabled,
          fixedStopLossPct,
          atrLength,
          stopAtrMultiplier,
          rewardRiskRatio,
          minimumStopPct,
          maximumStopPct,
          positionSizing,
          quantityPerTrade,
          rupeeRiskBudget,
          maximumQuantity,
          maximumCapitalPerPosition,
          maxOpenLotsPerSymbol,
          maxHoldingTradingDays,
          minimumProfitPct,
          profitExitRsi,
          upperRsiLevel,
          hardStopLossPct,
          rsiExitExecutionModel,
          timeExit: "NEXT_TRADING_SESSION_OPEN",
        };
        const result = await fetch("/api/backtest", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            symbols: batch,
            strategyMode,
            universeMode: useAllSymbols ? "all" : "selected",
            runId,
            durationYears,
            timeframe,
            ...strategyPayload,
          }),
          signal: controller.signal,
        });
        const payload = await readBacktestPayload(result, batch[0]);
        if (!result.ok) throw new Error(payload.detail ?? `Backtest stopped near ${batch[0]}.`);
        if (!Array.isArray(payload.results) || !Array.isArray(payload.errors) || !Array.isArray(payload.warnings)) {
          throw new Error(`Backtest service returned incomplete data near ${batch[0]}. Please retry.`);
        }

        if (strategyMode === "rsi_recovery" || strategyMode === "market_aligned_rsi_scalper") {
          if (!isRecoveryResponse(payload)) throw new Error("Backtest service returned the wrong strategy mode.");
          aggregate = mergeRecoveryResponses(
            isRecoveryResponse(aggregate) ? aggregate : null,
            payload,
          );
        } else {
          if (!isRangeResponse(payload)) throw new Error("Backtest service returned the wrong strategy mode.");
          const previous = aggregate as BacktestResponse | null;
          aggregate = previous ? {
            metadata: previous.metadata,
            results: [...previous.results, ...payload.results],
            errors: [...previous.errors, ...payload.errors],
            warnings: Array.from(new Set([...previous.warnings, ...payload.warnings])),
          } : payload;
        }

        const completed = Math.min(offset + batch.length, symbolsToRun.length);
        completedCount = completed;
        setResponse(aggregate);
        setRunProgress({ completed, total: symbolsToRun.length });
        if (strategyMode === "rsi_range") {
          setDetailSymbol((current) => current ?? aggregate?.results[0]?.symbol ?? null);
        }
      }
      if (aggregate) {
        const completedAt = "completedAt" in aggregate.metadata
          ? aggregate.metadata.completedAt
          : aggregate.metadata.generatedAt;
        const stored: StoredBacktest = {
          id: aggregate.metadata.runId || runId,
          completedAt,
          strategyMode,
          strategyName: strategyDisplayName(strategyMode),
          timeframe,
          durationYears,
          symbolCount: symbolsToRun.length,
          response: aggregate,
        };
        let browserEntries: StoredBacktest[] | null = null;
        try {
          browserEntries = await saveBacktestHistory(stored);
        } catch {
          browserEntries = null;
        }
        try {
          await saveAccountBacktestHistory(stored);
          setBacktestHistory(await readAccountBacktestHistory());
          setHistoryMessage(null);
        } catch {
          if (browserEntries) {
            setBacktestHistory(browserEntries.map(backtestHistorySummary));
            setHistoryMessage("Account sync is temporarily unavailable; this result is cached in this browser.");
          } else {
            setHistoryMessage("This result is displayed, but it could not be saved to account history or browser cache.");
          }
        }
        setActiveHistoryId(stored.id);
      }
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        setError(`Backtest stopped after ${completedCount} of ${symbolsToRun.length} symbols. Completed results are still shown.`);
      } else {
        setError(caught instanceof Error ? caught.message : "Backtest could not be completed.");
      }
    } finally {
      runAbortRef.current = null;
      setLoading(false);
    }
  };

  const optimizeAtrExits = async () => {
    const symbolsToRun = useAllSymbols ? availableSymbols : selectedSymbols;
    if (!symbolsToRun.length) {
      setError("Select at least one symbol before optimizing ATR exits.");
      return;
    }
    try {
      const controller = new AbortController();
      runAbortRef.current = controller;
      setOptimizing(true);
      setOptimization(null);
      setError(null);
      const result = await fetch("/api/backtest?action=optimize-atr", {
        method: "POST",
        headers: { "content-type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          symbols: symbolsToRun,
          strategyMode: "rsi_recovery",
          universeMode: useAllSymbols ? "all" : "selected",
          runId: crypto.randomUUID(),
          durationYears,
          timeframe,
          rsiLength,
          rsiArmLow,
          rsiArmHigh,
          rsiRecovery,
          emaEnabled,
          emaFast,
          emaSlow,
          vwapEnabled,
          volumeEnabled,
          volumeEma,
          minimumConfirmations,
          targetPct,
          setupExpiryBars,
          executionModel,
          buyCostBps,
          sellCostBps,
          slippageBps,
          exitModel: "ATR_DYNAMIC_TP_SL",
          atrLength,
          stopAtrMultiplier,
          rewardRiskRatio,
          minimumStopPct,
          maximumStopPct,
          positionSizing,
          quantityPerTrade,
          rupeeRiskBudget,
          maximumQuantity,
          maximumCapitalPerPosition,
          maxOpenLotsPerSymbol,
          maxHoldingTradingDays,
          atrLengths: [atrLength],
          stopAtrMultipliers: parseOptimizationGrid(optimizerGrid.stopAtrMultipliers, "Stop ATR multipliers"),
          rewardRiskRatios: parseOptimizationGrid(optimizerGrid.rewardRiskRatios, "Reward:risk values"),
          maxHoldingSessionsGrid: parseOptimizationGrid(optimizerGrid.maxHoldingSessions, "Holding sessions", true),
          minimumStopPcts: parseOptimizationGrid(optimizerGrid.minimumStopPcts, "Minimum stops"),
          maximumStopPcts: parseOptimizationGrid(optimizerGrid.maximumStopPcts, "Maximum stops"),
        }),
      });
      const body = await result.text();
      let payload: AtrOptimizationResponse & { detail?: string };
      try {
        payload = JSON.parse(body) as AtrOptimizationResponse & { detail?: string };
      } catch {
        throw new Error(`ATR optimization returned an unreadable response (HTTP ${result.status}).`);
      }
      if (!result.ok) throw new Error(payload.detail ?? "ATR optimization could not be completed.");
      setOptimization(payload);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        setError("ATR optimization was stopped.");
      } else {
        setError(caught instanceof Error ? caught.message : "ATR optimization could not be completed.");
      }
    } finally {
      runAbortRef.current = null;
      setOptimizing(false);
    }
  };

  const compareRsiExits = async () => {
    const symbolsToRun = useAllSymbols ? availableSymbols : selectedSymbols;
    if (!symbolsToRun.length) {
      setError("Select at least one symbol before comparing RSI exit settings.");
      return;
    }
    try {
      const controller = new AbortController();
      runAbortRef.current = controller;
      setComparingRsiExits(true);
      setRsiComparison(null);
      setError(null);
      const result = await fetch("/api/backtest?action=compare-rsi-exits", {
        method: "POST",
        headers: { "content-type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          symbols: symbolsToRun,
          strategyMode: "rsi_recovery",
          universeMode: useAllSymbols ? "all" : "selected",
          runId: crypto.randomUUID(),
          durationYears,
          timeframe,
          rsiLength,
          rsiArmLow,
          rsiArmHigh,
          rsiRecovery,
          emaEnabled,
          emaFast,
          emaSlow,
          vwapEnabled,
          volumeEnabled,
          volumeEma,
          minimumConfirmations,
          setupExpiryBars,
          executionModel,
          buyCostBps,
          sellCostBps,
          slippageBps,
          exitModel: "RSI_PROFIT_RISK_CONTROL",
          quantityPerTrade,
          maxOpenLotsPerSymbol,
          maxHoldingTradingDays,
          minimumProfitPct,
          profitExitRsi,
          upperRsiLevel,
          hardStopLossPct,
          rsiExitExecutionModel,
          rsiArmZones: parseArmZoneGrid(rsiComparisonGrid.armZones),
          rsiRecoveryThresholds: parseOptimizationGrid(rsiComparisonGrid.recoveryThresholds, "Recovery thresholds"),
          profitExitRsiLevels: parseOptimizationGrid(rsiComparisonGrid.profitExitRsiLevels, "Profit-exit RSI levels"),
          minimumProfitPcts: parseOptimizationGrid(rsiComparisonGrid.minimumProfitPcts, "Minimum profits"),
          hardStopLossPcts: parseOptimizationGrid(rsiComparisonGrid.hardStopLossPcts, "Hard stops"),
          maxHoldingSessionsGrid: parseOptimizationGrid(rsiComparisonGrid.maxHoldingSessions, "Holding sessions", true),
        }),
      });
      const body = await result.text();
      let payload: RsiExitComparisonResponse & { detail?: string };
      try {
        payload = JSON.parse(body) as RsiExitComparisonResponse & { detail?: string };
      } catch {
        throw new Error(`RSI exit comparison returned an unreadable response (HTTP ${result.status}).`);
      }
      if (!result.ok) throw new Error(payload.detail ?? "RSI exit comparison could not be completed.");
      setRsiComparison(payload);
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        setError("RSI exit comparison was stopped.");
      } else {
        setError(caught instanceof Error ? caught.message : "RSI exit comparison could not be completed.");
      }
    } finally {
      runAbortRef.current = null;
      setComparingRsiExits(false);
    }
  };

  const compareOiFilter = async () => {
    const symbolsToRun = useAllSymbols ? availableSymbols : selectedSymbols;
    if (!symbolsToRun.length) {
      setError("Select at least one symbol before comparing the OI filter.");
      return;
    }
    const controller = new AbortController();
    runAbortRef.current = controller;
    setComparingOi(true);
    setOiComparison(null);
    setError(null);
    try {
      const result = await fetch("/api/backtest?action=compare-oi-filter", {
        method: "POST",
        headers: { "content-type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          symbols: symbolsToRun,
          strategyMode: "market_aligned_rsi_scalper",
          universeMode: useAllSymbols ? "all" : "selected",
          runId: crypto.randomUUID(),
          durationYears,
          timeframe,
          marketAlignedConfiguration: {
            rsiLength, rsiArmLow, rsiArmHigh, rsiRecovery, signalRsiMaximum,
            emaFast, emaSlow, rvolPeriod: volumeEma, minimumRvol,
            relativeStrengthLookbackBars, roomLookbackBars, targetPct,
            setupExpiryBars, executionModel, positionSizing,
            entryStartTime, lastEntryTime, squareOffTime, stopLossPct: marketStopLossPct,
            quantityPerTrade, maximumHoldingBars, maximumTradesPerDay, maximumOpenPositions,
            buyCostBps, sellCostBps, slippageBps, maximumCapitalPerPosition,
            maximumDailyLossPct, stopAfterFirstLoss, cooldownBars, maximumSpreadPct,
            minimumNiftyTrendScore, minimumSectorBullishPct, minimumBreadthPct, minimumBreadthSymbols,
            minimumSectorMembers, minimumAverageTradedValue, maximumIntrabarRangePct,
            minimumAlignmentScore, marketDataStaleSeconds,
            oiMode: "ADVISORY", oiLookbackBars, oiStrikesEachSide,
            oiMinimumPriceChangePct, oiMinimumChangePct, oiMaximumSpreadPct,
            oiStaleDataSeconds, oiMinimumConfidence, oiMinimumValidContractFraction, oiMinimumFuturesVolume,
            oiVolatilityPriceRisePct, oiVolatilityIvRise, oiMinimumCoverage,
            oiOptionsWeight, oiFuturesWeight, oiSpotWeight,
            oiStronglyBearishThreshold, oiBearishThreshold, oiBullishThreshold,
            oiStronglyBullishThreshold, oiElevatedQualityThreshold, oiFailPolicy,
          },
        }),
      });
      const responseBody = await result.text();
      const payload = JSON.parse(responseBody) as OiFilterComparisonResponse;
      if (!result.ok) throw new Error(payload.detail ?? "OI filter comparison could not be completed.");
      setOiComparison(payload);
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === "AbortError")) {
        setError(caught instanceof Error ? caught.message : "OI filter comparison could not be completed.");
      }
    } finally {
      runAbortRef.current = null;
      setComparingOi(false);
    }
  };

  const changeExitModel = (model: typeof exitModel) => {
    setExitModel(model);
    setOptimization(null);
    setRsiComparison(null);
    setOiComparison(null);
    if (model === "RSI_PROFIT_RISK_CONTROL") {
      setRsiArmLow(20);
      setRsiArmHigh(35);
      setRsiRecovery(40);
      setSetupExpiryBars(50);
      setMinimumConfirmations(2);
      setMinimumProfitPct(0.5);
      setProfitExitRsi(50);
      setUpperRsiLevel(70);
      setHardStopLossPct(1.5);
      setMaxHoldingTradingDays(5);
      setMaxOpenLotsPerSymbol(1);
      setQuantityPerTrade(50);
      setPositionSizing("FIXED_QUANTITY");
    }
  };

  const marketNumber = (
    parameterKey: string,
    value: number,
    setter: (value: number) => void,
    options: { externalError?: string | null; presetControlled?: boolean; disabled?: boolean } = {},
  ) => <NumericField
    key={`${parameterKey}:${marketFormRevision}`}
    strategy="market_aligned_rsi_scalper"
    parameterKey={parameterKey}
    value={value}
    onValueChange={setter}
    onValidityChange={setNumericValidity}
    onManualChange={options.presetControlled ? markMarketPresetCustom : undefined}
    externalError={options.externalError}
    disabled={options.disabled}
  />;

  return (
    <div className="site-shell backtest-shell" data-theme={darkMode ? "dark" : "light"}>
      <header className="global-header">
        <div className="header-inner backtest-header-inner">
          <a className="brand" href="/" aria-label="OpenDelta dashboard">
            <div className="brand-mark" aria-hidden="true">₹</div>
            <div><strong>OpenDelta</strong><span>Market intelligence</span></div>
          </a>
          <nav className="top-nav" aria-label="Main navigation">
            <a className="nav-item" href="/"><LayoutDashboard size={16} />Dashboard</a>
            <a className="nav-item active" href="/backtest" aria-current="page"><TrendingUp size={16} />Backtest</a>
            <a className="nav-item" href="/signals"><Radio size={16} />Signals</a>
          </nav>
          <div className="header-actions">
            <div className="snapshot-pill"><LineChart size={15} /><span className="status-dot" /><div><strong>Dhan history</strong><span>IST candles</span></div></div>
            <div className="user-chip" title={userName}><div className="avatar">{initials(userName)}</div><span>{userName}</span></div>
            <button type="button" className="icon-button" onClick={() => setDarkMode((current) => !current)} aria-label="Toggle theme">
              {darkMode ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <a href={signOutHref} className="icon-button" aria-label="Sign out"><LogOut size={17} /></a>
          </div>
        </div>
      </header>

      <main className="backtest-main">
        <div className="strategy-mode-switch segmented" role="group" aria-label="Backtest mode">
          <button type="button" className={strategyMode === "rsi_range" ? "active" : ""} onClick={() => switchStrategy("rsi_range")}>RSI Range Strategy</button>
          <button type="button" className={strategyMode === "rsi_recovery" ? "active" : ""} onClick={() => switchStrategy("rsi_recovery")}>RSI Recovery Scalping</button>
          <button type="button" className={strategyMode === "market_aligned_rsi_scalper" ? "active" : ""} onClick={() => switchStrategy("market_aligned_rsi_scalper")}>Market-Aligned RSI Scalper</button>
        </div>
        <section className="backtest-intro">
          <div><span className="section-kicker">RSI strategy lab</span><h1>Historical backtest</h1><p>{strategyMode === "rsi_range" ? "Buy at low RSI. At high RSI, sell only for at least 1% net profit after fees; otherwise keep holding." : strategyMode === "rsi_recovery" ? "RSI recovery entries using the existing EMA, VWAP and volume confirmation logic." : "High-selectivity RSI scalping aligned with NIFTY, sector, breadth, relative strength, RVOL, liquidity and optional OI context."}</p></div>
          <button type="button" className="method-pill strategy-rule-trigger" aria-label="Hover or focus to view all backtest conditions">
            <Clock3 size={16} />
            <span><strong>Investment rules</strong>{strategyMode === "rsi_range" ? "Signals execute at the next candle open" : strategyMode === "rsi_recovery" ? "Existing RSI recovery behavior" : "Fourteen point-in-time alignment gates"}; hover for all conditions</span>
            <span className="strategy-rules-popover" role="tooltip">
              {strategyMode === "rsi_range" ? <>
                <strong>Buy / hold / sell conditions</strong>
                <span><b>BUY:</b> RSI enters {entryLow}-{entryHigh}; execute at the next candle open.</span>
                <span><b>CHECK:</b> When RSI is {exitLow}-{exitHigh}, estimate the next-open sale after entry fee, exit fee, and slippage.</span>
                <span><b>SELL:</b> Execute only when estimated net profit is at least 1% of the buy cost.</span>
                <span><b>HOLD:</b> If net profit is below 1%, cancel that exit and wait for a later high-RSI opportunity.</span>
              </> : strategyMode === "market_aligned_rsi_scalper" ? <>
                <strong>Market-Aligned RSI BUY conditions</strong>
                <span>NIFTY trend at least +{minimumNiftyTrendScore}; sector bullish; breadth not bearish; stock outperforms sector and NIFTY.</span>
                <span>RSI arms at {rsiArmLow} to {rsiArmHigh}, crosses above {rsiRecovery}, and remains at or below {signalRsiMaximum}.</span>
                <span>Price above session VWAP; EMA{emaFast} above EMA{emaSlow} and rising; RVOL at least {minimumRvol}.</span>
                <span>Room-to-target, historical liquidity/range quality, and Market Alignment Score at least {minimumAlignmentScore} must pass.</span>
                <span>OI mode {oiFilterMode} is optional context and never creates a BUY signal or increases size.</span>
              </> : <>
                <strong>RSI Recovery BUY and hold conditions</strong>
                <span><b>ARM:</b> RSI enters {rsiArmLow}–{rsiArmHigh}. Falling below the arm range does not cancel it.</span>
                <span><b>MANDATORY TRIGGER:</b> Armed RSI crosses above {rsiRecovery}; RSI is never counted as an optional confirmation.</span>
                <span><b>CONFIRM:</b> Require {minimumConfirmations} of enabled EMA, session VWAP, and volume filters.</span>
                <span><b>ENTRY:</b> {executionModel === "SIGNAL_CLOSE" ? "Reference the completed signal candle close." : "Execute at the following candle open."}</span>
                <span><b>TARGET:</b> {targetPct}% from entry, monitored only from the following candle.</span>
                {exitProtectionEnabled ? <>
                  <span><b>POSITION LIMIT:</b> Use {positionSizing === "FIXED_QUANTITY" ? `${quantityPerTrade} shares` : `a ${rupeeRiskBudget.toLocaleString("en-IN")} INR risk budget`} while fewer than {maxOpenLotsPerSymbol} lot(s) are open for that symbol.</span>
                  <span><b>EXITS:</b> {exitModel === "ATR_DYNAMIC_TP_SL" ? `Freeze ATR(${atrLength}) TP/SL at entry using ${stopAtrMultiplier}× ATR and ${rewardRiskRatio}:1 reward:risk.` : exitModel === "RSI_PROFIT_RISK_CONTROL" ? `Exit at RSI ${profitExitRsi}+ only with at least ${minimumProfitPct}% profit; RSI ${upperRsiLevel}+ uses the overbought exit label. A ${hardStopLossPct}% hard stop remains mandatory.` : `Use fixed ${targetPct}% TP and ${fixedStopLossPct}% SL.`}</span>
                  <span><b>TIME EXIT:</b> Hold through {maxHoldingTradingDays} NSE sessions including entry day, then exit at the next available session open.</span>
                </> : <span><b>OBSERVATIONS:</b> Every fresh RSI arm/recovery cycle is recorded independently. Open signals do not block later signals; there is no stop loss, end-of-day exit, or leverage.</span>}
              </>}
            </span>
          </button>
        </section>

        <form className="backtest-controls" onSubmit={submit} noValidate>
          <div className="control-block symbol-control">
            <label>Symbol universe <span>{useAllSymbols ? `${availableSymbols.length} symbols` : `${selectedSymbols.length}/10 selected`}</span></label>
            <div className="universe-toggle segmented backtest-segmented">
              <button type="button" className={!useAllSymbols ? "active" : ""} onClick={() => setUseAllSymbols(false)}>Selected symbols</button>
              <button type="button" className={useAllSymbols ? "active" : ""} onClick={() => { setUseAllSymbols(true); setSymbolMenuOpen(false); }}>All {availableSymbols.length} symbols</button>
            </div>
            {useAllSymbols ? (
              <div className="all-symbols-selection">
                <strong>Entire symbols.csv universe</strong>
                <span>Runs automatically in safe groups of 10. Keep this page open to see progress.</span>
              </div>
            ) : (
              <div className="selected-symbols">
                {selectedSymbols.map((symbol) => (
                  <button key={symbol} type="button" className="symbol-chip" onClick={() => setSelectedSymbols((current) => current.filter((item) => item !== symbol))}>
                    {symbol}<X size={12} />
                  </button>
                ))}
                <div className="symbol-picker">
                  <Search size={15} />
                  <input value={symbolQuery} onFocus={() => setSymbolMenuOpen(true)} onChange={(event) => { setSymbolQuery(event.target.value); setSymbolMenuOpen(true); }} placeholder="Add NSE symbol" disabled={selectedSymbols.length >= 10} />
                  <button type="button" onClick={() => setSymbolMenuOpen((current) => !current)} aria-label="Show symbols"><ChevronDown size={15} /></button>
                  {symbolMenuOpen && selectedSymbols.length < 10 && (
                    <div className="symbol-options">
                      {choices.length ? choices.map((symbol) => <button key={symbol} type="button" onClick={() => addSymbol(symbol)}>{symbol}<Check size={13} /></button>) : <span>No matching symbol</span>}
                    </div>
                  )}
                </div>
              </div>
            )}
            {symbolRegistryError && <small className="symbol-registry-warning">{symbolRegistryError}</small>}
          </div>

          <div className="control-block compact-control"><span className="control-title">Duration</span><div className="segmented backtest-segmented">{([1, 3] as const).map((years) => <button key={years} type="button" className={durationYears === years ? "active" : ""} onClick={() => setDurationYears(years)}>{years} year{years > 1 ? "s" : ""}</button>)}</div></div>
          <div className="control-block timeframe-control"><span className="control-title">Timeframe</span><div className="segmented backtest-segmented">{timeframes.map((item) => <button key={item} type="button" className={timeframe === item ? "active" : ""} onClick={() => setTimeframe(item)}>{item}</button>)}</div></div>
          {strategyMode === "rsi_range" ? <>
            <div className="control-block range-control"><span className="control-title">Buy RSI range</span><div className="range-inputs"><input type="number" {...numericConstraints("rsi_range", "entryLow")} value={entryLow} onChange={(event) => setEntryLow(Number(event.target.value))} aria-label="Buy RSI lower value" /><span>to</span><input type="number" {...numericConstraints("rsi_range", "entryHigh")} value={entryHigh} onChange={(event) => setEntryHigh(Number(event.target.value))} aria-label="Buy RSI upper value" /></div></div>
            <div className="control-block range-control"><span className="control-title">Sell RSI range</span><div className="range-inputs"><input type="number" {...numericConstraints("rsi_range", "exitLow")} value={exitLow} onChange={(event) => setExitLow(Number(event.target.value))} aria-label="Sell RSI lower value" /><span>to</span><input type="number" {...numericConstraints("rsi_range", "exitHigh")} value={exitHigh} onChange={(event) => setExitHigh(Number(event.target.value))} aria-label="Sell RSI upper value" /></div></div>
          </> : strategyMode === "market_aligned_rsi_scalper" ? <div className="market-aligned-settings">
            <section className="market-preset-bar" aria-label="Market-Aligned presets">
              <div><span className="section-kicker">Preset</span><strong>{marketPreset}</strong><small>Presets are research starting points and do not guarantee profitability.</small></div>
              <div className="market-preset-actions">
                {(["Recommended", "Strict"] as const).map((preset) => <button key={preset} type="button" className={marketPreset === preset ? "active" : ""} onClick={() => applyMarketPreset(preset)}>{preset}</button>)}
                <button type="button" className={marketPreset === "Custom" ? "active" : ""} onClick={markMarketPresetCustom}>Custom</button>
              </div>
            </section>

            <section className="market-settings-card market-main-settings" aria-labelledby="market-main-settings-title">
              <div className="market-section-heading"><div><span className="section-kicker">Common controls</span><h2 id="market-main-settings-title">Main settings</h2></div><span>Strategy, universe, duration and timeframe are selected above.</span></div>
              <div className="market-settings-grid">
                <label className="parameter-field"><span className="parameter-label">Execution model</span><small className="parameter-description">Choose the price used after a completed signal bar.</small><select value={executionModel} onChange={(event) => setExecutionModel(event.target.value as typeof executionModel)}><option value="SIGNAL_CLOSE">Signal close</option><option value="NEXT_BAR_OPEN">Next bar open</option></select></label>
                {marketNumber("quantityPerTrade", quantityPerTrade, setQuantityPerTrade)}
                <label className="parameter-field"><span className="parameter-label">Position sizing</span><small className="parameter-description">The strategy never increases size because of a bullish OI reading.</small><select value={positionSizing} onChange={(event) => setPositionSizing(event.target.value as typeof positionSizing)}><option value="FIXED_QUANTITY">Fixed quantity</option><option value="RISK_BUDGET">Risk budget</option></select></label>
                <label className="parameter-field"><span className="parameter-label">Entry start time</span><small className="parameter-description">First IST time at which a new entry may be considered.</small><input type="time" value={entryStartTime} onChange={(event) => setEntryStartTime(event.target.value)} />{marketRelationshipErrors.sessionTimes && <span className="parameter-error" role="alert">{marketRelationshipErrors.sessionTimes}</span>}</label>
                <label className="parameter-field"><span className="parameter-label">Last entry time</span><small className="parameter-description">No new entries are accepted at or after this IST time.</small><input type="time" value={lastEntryTime} onChange={(event) => setLastEntryTime(event.target.value)} />{marketRelationshipErrors.sessionTimes && <span className="parameter-error" role="alert">{marketRelationshipErrors.sessionTimes}</span>}</label>
                <label className="parameter-field"><span className="parameter-label">Square-off time</span><small className="parameter-description">Open positions are closed by this IST time.</small><input type="time" value={squareOffTime} onChange={(event) => setSquareOffTime(event.target.value)} />{marketRelationshipErrors.sessionTimes && <span className="parameter-error" role="alert">{marketRelationshipErrors.sessionTimes}</span>}</label>
                {marketNumber("targetPct", targetPct, setTargetPct)}
                {marketNumber("stopLossPct", marketStopLossPct, setMarketStopLossPct)}
                {marketNumber("maximumHoldingBars", maximumHoldingBars, setMaximumHoldingBars)}
                {marketNumber("maximumTradesPerDay", maximumTradesPerDay, setMaximumTradesPerDay, { presetControlled: true })}
                {marketNumber("maximumOpenPositions", maximumOpenPositions, setMaximumOpenPositions)}
                {marketNumber("minimumAlignmentScore", minimumAlignmentScore, setMinimumAlignmentScore, { presetControlled: true })}
                <label className="parameter-field"><span className="parameter-label">OI mode</span><small className="parameter-description">ADVISORY records point-in-time OI without blocking a trade.</small><select aria-label="NIFTY OI regime filter" value={oiFilterMode} onChange={(event) => { setOiFilterMode(event.target.value as OiFilterMode); setOiComparison(null); markMarketPresetCustom(); }}><option value="OFF">OFF — no OI context</option><option value="ADVISORY">ADVISORY — record only (default)</option><option value="RESEARCH_FILTER">RESEARCH FILTER — experimental</option><option value="ENFORCED">ENFORCED — validated data only</option></select></label>
              </div>
            </section>

            <details className="market-settings-card market-settings-section">
              <summary><span><strong>Entry filters</strong><small>RSI, EMA and market-alignment gates</small></span><ChevronDown size={17} /></summary>
              <div className="market-settings-grid">
                {marketNumber("rsiLength", rsiLength, setRsiLength)}
                {marketNumber("rsiArmLow", rsiArmLow, setRsiArmLow)}
                {marketNumber("rsiArmHigh", rsiArmHigh, setRsiArmHigh, { externalError: marketRelationshipErrors.rsiArmHigh })}
                {marketNumber("rsiRecovery", rsiRecovery, setRsiRecovery, { externalError: marketRelationshipErrors.rsiRecovery })}
                {marketNumber("signalRsiMaximum", signalRsiMaximum, setSignalRsiMaximum, { externalError: marketRelationshipErrors.signalRsiMaximum })}
                {marketNumber("setupExpiryBars", setupExpiryBars, setSetupExpiryBars)}
                {marketNumber("emaFast", emaFast, setEmaFast)}
                {marketNumber("emaSlow", emaSlow, setEmaSlow, { externalError: marketRelationshipErrors.emaSlow })}
                {marketNumber("minimumRvol", minimumRvol, setMinimumRvol)}
                {marketNumber("minimumNiftyTrendScore", minimumNiftyTrendScore, setMinimumNiftyTrendScore)}
                {marketNumber("minimumSectorBullishPct", minimumSectorBullishPct, setMinimumSectorBullishPct)}
                {marketNumber("minimumBreadthPct", minimumBreadthPct, setMinimumBreadthPct)}
              </div>
            </details>

            <details className="market-settings-card market-settings-section">
              <summary><span><strong>Risk and execution</strong><small>Costs, capacity, loss and liquidity limits</small></span><ChevronDown size={17} /></summary>
              <div className="market-settings-grid">
                {marketNumber("buyCostBps", buyCostBps, setBuyCostBps)}
                {marketNumber("sellCostBps", sellCostBps, setSellCostBps)}
                {marketNumber("slippageBps", slippageBps, setSlippageBps)}
                {marketNumber("maximumCapitalPerPosition", maximumCapitalPerPosition, setMaximumCapitalPerPosition)}
                {marketNumber("maximumDailyLossPct", maximumDailyLossPct, setMaximumDailyLossPct)}
                <label className="parameter-field parameter-checkbox"><span className="parameter-label">Stop after first loss</span><small className="parameter-description">Prevent another entry after the first realised loss of the session.</small><span><input type="checkbox" checked={stopAfterFirstLoss} onChange={(event) => setStopAfterFirstLoss(event.target.checked)} /> Enabled</span></label>
                {marketNumber("cooldownBars", cooldownBars, setCooldownBars)}
                {marketNumber("maximumSpreadPct", maximumSpreadPct, setMaximumSpreadPct)}
                {marketNumber("minimumAverageTradedValue", minimumAverageTradedValue, setMinimumAverageTradedValue)}
                {marketNumber("roomLookbackBars", roomLookbackBars, setRoomLookbackBars)}
              </div>
            </details>

            <details className="market-settings-card market-settings-section open-interest-settings">
              <summary><span><strong>Open Interest</strong><small>Normal OI controls and data status</small></span><ChevronDown size={17} /></summary>
              <div className="market-settings-grid">
                <label className="parameter-field"><span className="parameter-label">OI mode</span><small className="parameter-description">The default is ADVISORY.</small><select value={oiFilterMode} onChange={(event) => { setOiFilterMode(event.target.value as OiFilterMode); setOiComparison(null); markMarketPresetCustom(); }}><option value="OFF">OFF</option><option value="ADVISORY">ADVISORY</option><option value="RESEARCH_FILTER">RESEARCH FILTER</option><option value="ENFORCED">ENFORCED</option></select></label>
                {marketNumber("oiMinimumConfidence", oiMinimumConfidence, setOiMinimumConfidence)}
                {marketNumber("oiStronglyBearishThreshold", oiStronglyBearishThreshold, setOiStronglyBearishThreshold)}
                {marketNumber("oiStaleDataSeconds", oiStaleDataSeconds, setOiStaleDataSeconds)}
              </div>
              <div className="oi-mode-explainer"><span><b>OFF</b> — OI is ignored</span><span><b>ADVISORY</b> — OI is displayed but cannot block trades</span><span><b>RESEARCH FILTER</b> — experimental backtest filter</span><span><b>ENFORCED</b> — can block trades only when validated data is available</span></div>
              <div className={`oi-history-status ${oiHistoryStatus?.enforcementReady ? "ready" : "limited"}`}>
                <strong>Historical OI: {oiHistoryStatus?.state ?? "CHECKING"}</strong>
                {oiHistoryStatus?.request?.fromDate && <span>{oiHistoryStatus.request.fromDate} to {oiHistoryStatus.request.toDate} · {oiHistoryStatus.optionRowsImported ?? 0} option rows</span>}
                <span>{oiHistoryStatus?.enforcementReady ? `${oiHistoryStatus.enforceableSnapshots ?? 0} enforceable regimes` : oiHistoryStatus?.reason ?? "Checking imported coverage…"}</span>
              </div>

              <details className="expert-oi-settings">
                <summary><span><strong>Expert OI settings</strong><small>Research and engineering parameters</small></span><ChevronDown size={16} /></summary>
                <div className="market-settings-grid expert-settings-grid">
                  {marketNumber("oiLookbackBars", oiLookbackBars, setOiLookbackBars)}
                  {marketNumber("oiStrikesEachSide", oiStrikesEachSide, setOiStrikesEachSide)}
                  {marketNumber("oiMinimumPriceChangePct", oiMinimumPriceChangePct, setOiMinimumPriceChangePct)}
                  {marketNumber("oiMinimumChangePct", oiMinimumChangePct, setOiMinimumChangePct)}
                  {marketNumber("oiMaximumSpreadPct", oiMaximumSpreadPct, setOiMaximumSpreadPct)}
                  {marketNumber("oiMinimumValidContractFraction", oiMinimumValidContractFraction, setOiMinimumValidContractFraction)}
                  {marketNumber("oiMinimumFuturesVolume", oiMinimumFuturesVolume, setOiMinimumFuturesVolume)}
                  {marketNumber("oiVolatilityPriceRisePct", oiVolatilityPriceRisePct, setOiVolatilityPriceRisePct)}
                  {marketNumber("oiVolatilityIvRise", oiVolatilityIvRise, setOiVolatilityIvRise)}
                  {marketNumber("oiMinimumCoverage", oiMinimumCoverage, setOiMinimumCoverage)}
                  {marketNumber("oiOptionsWeight", oiOptionsWeight, setOiOptionsWeight, { externalError: marketRelationshipErrors.oiWeights })}
                  {marketNumber("oiFuturesWeight", oiFuturesWeight, setOiFuturesWeight, { externalError: marketRelationshipErrors.oiWeights })}
                  {marketNumber("oiSpotWeight", oiSpotWeight, setOiSpotWeight, { externalError: marketRelationshipErrors.oiWeights })}
                  {marketNumber("oiBearishThreshold", oiBearishThreshold, setOiBearishThreshold, { externalError: marketRelationshipErrors.oiThresholds })}
                  {marketNumber("oiBullishThreshold", oiBullishThreshold, setOiBullishThreshold, { externalError: marketRelationshipErrors.oiThresholds })}
                  {marketNumber("oiStronglyBullishThreshold", oiStronglyBullishThreshold, setOiStronglyBullishThreshold, { externalError: marketRelationshipErrors.oiThresholds })}
                  {marketNumber("oiElevatedQualityThreshold", oiElevatedQualityThreshold, setOiElevatedQualityThreshold)}
                  <label className="parameter-field"><span className="parameter-label">Missing-data policy</span><small className="parameter-description">Research defaults skip decisions that do not meet data coverage.</small><select value={oiFailPolicy} onChange={(event) => setOiFailPolicy(event.target.value as typeof oiFailPolicy)}><option value="SKIP">Skip and record</option><option value="ALLOW">Allow long (explicit override)</option></select></label>
                  {marketNumber("relativeStrengthLookbackBars", relativeStrengthLookbackBars, setRelativeStrengthLookbackBars)}
                  {marketNumber("minimumBreadthSymbols", minimumBreadthSymbols, setMinimumBreadthSymbols)}
                  {marketNumber("minimumSectorMembers", minimumSectorMembers, setMinimumSectorMembers)}
                  {marketNumber("maximumIntrabarRangePct", maximumIntrabarRangePct, setMaximumIntrabarRangePct)}
                  {marketNumber("marketDataStaleSeconds", marketDataStaleSeconds, setMarketDataStaleSeconds)}
                </div>
                <div className="expert-actions"><button type="button" className="secondary-action" onClick={restoreExpertOiDefaults}>Restore recommended defaults</button><button type="button" className="secondary-action" disabled={comparingOi || loading || marketFormInvalid} onClick={compareOiFilter}>{comparingOi ? <><LoaderCircle className="spin" size={15} />Comparing…</> : "Compare OI filter"}</button></div>
              </details>
            </details>
          </div> : <>
            <div className="recovery-config recovery-simple-config">
              <fieldset className="recovery-config-card target-card">
                <legend>Execution &amp; target</legend>
                <label><span>{exitModel === "RSI_PROFIT_RISK_CONTROL" ? "Minimum profit" : exitModel === "ATR_DYNAMIC_TP_SL" ? "Target" : "Profit target"}</span>{exitModel === "ATR_DYNAMIC_TP_SL" ? <strong className="derived-value">ATR-derived</strong> : <span className="suffixed-input"><input aria-label={exitModel === "RSI_PROFIT_RISK_CONTROL" ? "Minimum profitable exit" : "Profit target"} type="number" {...numericConstraints("rsi_recovery", exitModel === "RSI_PROFIT_RISK_CONTROL" ? "minimumProfitPct" : "targetPct")} value={exitModel === "RSI_PROFIT_RISK_CONTROL" ? minimumProfitPct : targetPct} onChange={(event) => exitModel === "RSI_PROFIT_RISK_CONTROL" ? setMinimumProfitPct(Number(event.target.value)) : setTargetPct(Number(event.target.value))} /><i>%</i></span>}</label>
                <div className="execution-model"><span>Execution model</span><div className="segmented backtest-segmented"><button type="button" className={executionModel === "SIGNAL_CLOSE" ? "active" : ""} onClick={() => setExecutionModel("SIGNAL_CLOSE")}>Signal close</button><button type="button" className={executionModel === "NEXT_BAR_OPEN" ? "active" : ""} onClick={() => setExecutionModel("NEXT_BAR_OPEN")}>Next open</button></div></div>
                {strategyMode === "rsi_recovery" && <label><span>Exit model</span><select aria-label="Exit model" value={exitModel} onChange={(event) => changeExitModel(event.target.value as typeof exitModel)}><option value="LEGACY_FIXED_TARGET">Legacy fixed target</option><option value="FIXED_TP_SL">Fixed TP and SL</option><option value="ATR_DYNAMIC_TP_SL">ATR dynamic TP and SL</option><option value="RSI_PROFIT_RISK_CONTROL">RSI profitable exit with risk control</option></select></label>}
                <div className="fixed-strategy-rules">{exitModel === "LEGACY_FIXED_TARGET" ? <><span>No stop loss</span><span>No end-of-day exit</span><span>Hold until target</span></> : exitModel === "FIXED_TP_SL" ? <><span>Fixed TP + SL</span><span>Session time exit</span><span>Stop-first OHLC rule</span></> : exitModel === "ATR_DYNAMIC_TP_SL" ? <><span>ATR-frozen TP + SL</span><span>Session time exit</span><span>Stop-first OHLC rule</span></> : <><span>Profitable RSI exit</span><span>Hard stop</span><span>Session time exit</span></>}</div>
              </fieldset>
              {strategyMode === "rsi_recovery" && <fieldset className="recovery-config-card position-config-card">
                <legend>Position limits</legend>
                <label><span>Quantity</span><span className="suffixed-input"><input aria-label="Quantity per trade" type="number" {...numericConstraints("rsi_recovery", "quantityPerTrade")} value={quantityPerTrade} disabled={!exitProtectionEnabled || positionSizing === "RISK_BUDGET"} onChange={(event) => setQuantityPerTrade(Number(event.target.value))} /><i>shares</i></span></label>
                <label><span>Maximum open lots</span><input aria-label="Maximum open lots per symbol" type="number" {...numericConstraints("rsi_recovery", "maxOpenLotsPerSymbol")} value={maxOpenLotsPerSymbol} disabled={!exitProtectionEnabled} onChange={(event) => setMaxOpenLotsPerSymbol(Number(event.target.value))} /></label>
                <label><span>Maximum holding sessions</span><span className="suffixed-input"><input aria-label="Maximum holding trading sessions" type="number" {...numericConstraints("rsi_recovery", "maxHoldingTradingDays")} value={maxHoldingTradingDays} disabled={!exitProtectionEnabled} onChange={(event) => setMaxHoldingTradingDays(Number(event.target.value))} /><i>NSE sessions</i></span><small>Entry session is session 1. An unresolved position exits at the next available session open.</small></label>
                {exitModel === "ATR_DYNAMIC_TP_SL" && <small>TP and SL are calculated from each signal&apos;s ATR and frozen at entry.</small>}
                {exitModel === "RSI_PROFIT_RISK_CONTROL" && <><label><span>Profit-exit RSI</span><input aria-label="Profit-exit RSI" type="number" {...numericConstraints("rsi_recovery", "profitExitRsi")} value={profitExitRsi} onChange={(event) => setProfitExitRsi(Number(event.target.value))} /></label><label><span>Hard stop</span><span className="suffixed-input"><input aria-label="Hard stop loss" type="number" {...numericConstraints("rsi_recovery", "hardStopLossPct")} value={hardStopLossPct} onChange={(event) => setHardStopLossPct(Number(event.target.value))} /><i>%</i></span></label></>}
              </fieldset>}
            </div>
            {strategyMode === "rsi_recovery" && exitModel === "RSI_PROFIT_RISK_CONTROL" && <div className="research-semantics"><Info size={16} /><span>The setup is armed when RSI enters the low zone. BUY occurs after RSI recovery and confirmation. SELL occurs when RSI recovers above the profit-exit level and the configured minimum profit is available. Stop and time exits prevent indefinite losing positions.</span></div>}
            <details className="advanced-settings">
              <summary>Advanced settings</summary>
              <div className="recovery-config advanced-recovery-grid">
                <fieldset className="recovery-config-card">
                  <legend>Detailed RSI configuration</legend>
                  <label><span>RSI length</span><input type="number" {...numericConstraints("rsi_recovery", "rsiLength")} value={rsiLength} onChange={(event) => setRsiLength(Number(event.target.value))} /></label>
                  <label className="wide-config-field"><span>Arm RSI</span><div className="inline-number-range"><input type="number" {...numericConstraints("rsi_recovery", "rsiArmLow")} value={rsiArmLow} onChange={(event) => setRsiArmLow(Number(event.target.value))} /><i>to</i><input type="number" {...numericConstraints("rsi_recovery", "rsiArmHigh")} value={rsiArmHigh} onChange={(event) => setRsiArmHigh(Number(event.target.value))} /></div></label>
                  <label><span>Recovery</span><input type="number" {...numericConstraints("rsi_recovery", "rsiRecovery")} value={rsiRecovery} onChange={(event) => setRsiRecovery(Number(event.target.value))} /></label>
                  <label><span>Setup expiry</span><span className="suffixed-input"><input type="number" {...numericConstraints("rsi_recovery", "setupExpiryBars")} value={setupExpiryBars} onChange={(event) => setSetupExpiryBars(Number(event.target.value))} /><i>bars</i></span><small>0 = never expire</small></label>
                </fieldset>
                {strategyMode === "rsi_recovery" && <fieldset className="recovery-config-card confirmation-card">
                  <legend>EMA · VWAP · volume</legend>
                  <label className="toggle-config"><input type="checkbox" checked={emaEnabled} onChange={(event) => setEmaEnabled(event.target.checked)} /><span>EMA trend</span><span className="inline-number-range"><input aria-label="Fast EMA length" type="number" {...numericConstraints("rsi_recovery", "emaFast")} value={emaFast} onChange={(event) => setEmaFast(Number(event.target.value))} /><i>/</i><input aria-label="Slow EMA length" type="number" {...numericConstraints("rsi_recovery", "emaSlow")} value={emaSlow} onChange={(event) => setEmaSlow(Number(event.target.value))} /></span></label>
                  <label className="toggle-config"><input type="checkbox" checked={vwapEnabled} onChange={(event) => setVwapEnabled(event.target.checked)} /><span>VWAP</span><small>Close &gt; session VWAP</small></label>
                  <label className="toggle-config"><input type="checkbox" checked={volumeEnabled} onChange={(event) => setVolumeEnabled(event.target.checked)} /><span>Volume</span><span className="suffixed-input"><input aria-label="Volume EMA length" type="number" {...numericConstraints("rsi_recovery", "volumeEma")} value={volumeEma} onChange={(event) => setVolumeEma(Number(event.target.value))} /><i>EMA</i></span></label>
                  <label><span>Minimum confirmations</span><input type="number" {...numericConstraints("rsi_recovery", "minimumConfirmations")} value={minimumConfirmations} onChange={(event) => setMinimumConfirmations(Number(event.target.value))} /><small>RSI recovery remains mandatory and is not scored</small></label>
                </fieldset>}
                {strategyMode === "rsi_recovery" && exitModel === "FIXED_TP_SL" && <fieldset className="recovery-config-card">
                  <legend>Fixed exits</legend>
                  <label title="Take-profit price = actual entry × (1 + take-profit %)"><span>Take-profit</span><span className="suffixed-input"><input type="number" {...numericConstraints("rsi_recovery", "targetPct")} value={targetPct} onChange={(event) => setTargetPct(Number(event.target.value))} /><i>%</i></span></label>
                  <label title="Stop-loss price = actual entry × (1 - stop-loss %)"><span>Stop-loss</span><span className="suffixed-input"><input type="number" {...numericConstraints("rsi_recovery", "fixedStopLossPct")} value={fixedStopLossPct} onChange={(event) => setFixedStopLossPct(Number(event.target.value))} /><i>%</i></span></label>
                </fieldset>}
                {strategyMode === "rsi_recovery" && exitModel === "ATR_DYNAMIC_TP_SL" && <fieldset className="recovery-config-card">
                  <legend>ATR dynamic exits</legend>
                  <label title="Wilder RMA of true range on completed strategy candles"><span>ATR length</span><input type="number" {...numericConstraints("rsi_recovery", "atrLength")} value={atrLength} onChange={(event) => setAtrLength(Number(event.target.value))} /></label>
                  <label title="Raw ATR % × this multiplier, then clamped to the configured stop bounds"><span>Stop ATR multiplier</span><input type="number" {...numericConstraints("rsi_recovery", "stopAtrMultiplier")} value={stopAtrMultiplier} onChange={(event) => setStopAtrMultiplier(Number(event.target.value))} /></label>
                  <label title="Dynamic take-profit % = dynamic stop % × reward:risk"><span>Reward:risk</span><input type="number" {...numericConstraints("rsi_recovery", "rewardRiskRatio")} value={rewardRiskRatio} onChange={(event) => setRewardRiskRatio(Number(event.target.value))} /></label>
                  <label><span>Minimum stop</span><span className="suffixed-input"><input type="number" {...numericConstraints("rsi_recovery", "minimumStopPct")} value={minimumStopPct} onChange={(event) => setMinimumStopPct(Number(event.target.value))} /><i>%</i></span></label>
                  <label><span>Maximum stop</span><span className="suffixed-input"><input type="number" {...numericConstraints("rsi_recovery", "maximumStopPct")} value={maximumStopPct} onChange={(event) => setMaximumStopPct(Number(event.target.value))} /><i>%</i></span></label>
                  <small>TP and SL use the signal candle ATR and are frozen when the position is created.</small>
                </fieldset>}
                {strategyMode === "rsi_recovery" && exitModel === "RSI_PROFIT_RISK_CONTROL" && <fieldset className="recovery-config-card">
                  <legend>RSI profitable exit</legend>
                  <label><span>Upper RSI level</span><input aria-label="Upper RSI level" type="number" {...numericConstraints("rsi_recovery", "upperRsiLevel")} min={profitExitRsi} value={upperRsiLevel} onChange={(event) => setUpperRsiLevel(Number(event.target.value))} /></label>
                  <label><span>RSI exit execution</span><select aria-label="RSI exit execution model" value={rsiExitExecutionModel} onChange={(event) => setRsiExitExecutionModel(event.target.value as typeof rsiExitExecutionModel)}><option value="SIGNAL_CLOSE">Signal close</option><option value="NEXT_BAR_OPEN">Next bar open</option></select><small>Next-open exits recheck that the configured minimum profit still exists.</small></label>
                  <small>Exit priority: hard stop, profitable RSI exit, then next-session time exit. RSI never forces a sale below the minimum profit.</small>
                </fieldset>}
                {strategyMode === "rsi_recovery" && exitProtectionEnabled && exitModel !== "RSI_PROFIT_RISK_CONTROL" && <fieldset className="recovery-config-card">
                  <legend>Position sizing</legend>
                  <label><span>Method</span><select value={positionSizing} onChange={(event) => setPositionSizing(event.target.value as typeof positionSizing)}><option value="FIXED_QUANTITY">Fixed quantity</option><option value="RISK_BUDGET">Risk budget</option></select></label>
                  {positionSizing === "FIXED_QUANTITY" ? <label><span>Quantity</span><span className="suffixed-input"><input type="number" {...numericConstraints("rsi_recovery", "quantityPerTrade")} value={quantityPerTrade} onChange={(event) => setQuantityPerTrade(Number(event.target.value))} /><i>shares</i></span></label> : <>
                    <label title="Quantity = floor(rupee risk budget ÷ risk per share)"><span>Rupee risk budget</span><span className="suffixed-input"><input type="number" {...numericConstraints("rsi_recovery", "rupeeRiskBudget")} value={rupeeRiskBudget} onChange={(event) => setRupeeRiskBudget(Number(event.target.value))} /><i>INR</i></span></label>
                    <label><span>Maximum quantity</span><input type="number" {...numericConstraints("rsi_recovery", "maximumQuantity")} value={maximumQuantity} onChange={(event) => setMaximumQuantity(Number(event.target.value))} /></label>
                    <label><span>Maximum capital</span><span className="suffixed-input"><input type="number" {...numericConstraints("rsi_recovery", "maximumCapitalPerPosition")} value={maximumCapitalPerPosition} onChange={(event) => setMaximumCapitalPerPosition(Number(event.target.value))} /><i>INR</i></span></label>
                  </>}
                </fieldset>}
                {strategyMode === "rsi_recovery" && exitModel === "ATR_DYNAMIC_TP_SL" && <fieldset className="recovery-config-card optimizer-card">
                  <legend>ATR exit research</legend>
                  <label><span>Stop multipliers</span><input value={optimizerGrid.stopAtrMultipliers} onChange={(event) => setOptimizerGrid((current) => ({ ...current, stopAtrMultipliers: event.target.value }))} /></label>
                  <label><span>Reward:risk grid</span><input value={optimizerGrid.rewardRiskRatios} onChange={(event) => setOptimizerGrid((current) => ({ ...current, rewardRiskRatios: event.target.value }))} /></label>
                  <label><span>Holding sessions</span><input value={optimizerGrid.maxHoldingSessions} onChange={(event) => setOptimizerGrid((current) => ({ ...current, maxHoldingSessions: event.target.value }))} /></label>
                  <label><span>Minimum stops %</span><input value={optimizerGrid.minimumStopPcts} onChange={(event) => setOptimizerGrid((current) => ({ ...current, minimumStopPcts: event.target.value }))} /></label>
                  <label><span>Maximum stops %</span><input value={optimizerGrid.maximumStopPcts} onChange={(event) => setOptimizerGrid((current) => ({ ...current, maximumStopPcts: event.target.value }))} /></label>
                  <button type="button" className="secondary-action" disabled={optimizing || loading} onClick={optimizeAtrExits}>{optimizing ? <><LoaderCircle className="spin" size={15} />Optimizing…</> : "Optimize ATR exits"}</button>
                  <small>Uses chronological walk-forward validation and one common configuration across every selected symbol.</small>
                </fieldset>}
                {strategyMode === "rsi_recovery" && exitModel === "RSI_PROFIT_RISK_CONTROL" && <fieldset className="recovery-config-card optimizer-card">
                  <legend>RSI exit research</legend>
                  <label><span>Arm zones</span><input value={rsiComparisonGrid.armZones} onChange={(event) => setRsiComparisonGrid((current) => ({ ...current, armZones: event.target.value }))} /></label>
                  <label><span>Recovery RSI</span><input value={rsiComparisonGrid.recoveryThresholds} onChange={(event) => setRsiComparisonGrid((current) => ({ ...current, recoveryThresholds: event.target.value }))} /></label>
                  <label><span>Profit-exit RSI</span><input value={rsiComparisonGrid.profitExitRsiLevels} onChange={(event) => setRsiComparisonGrid((current) => ({ ...current, profitExitRsiLevels: event.target.value }))} /></label>
                  <label><span>Minimum profit %</span><input value={rsiComparisonGrid.minimumProfitPcts} onChange={(event) => setRsiComparisonGrid((current) => ({ ...current, minimumProfitPcts: event.target.value }))} /></label>
                  <label><span>Hard stops %</span><input value={rsiComparisonGrid.hardStopLossPcts} onChange={(event) => setRsiComparisonGrid((current) => ({ ...current, hardStopLossPcts: event.target.value }))} /></label>
                  <label><span>Holding sessions</span><input value={rsiComparisonGrid.maxHoldingSessions} onChange={(event) => setRsiComparisonGrid((current) => ({ ...current, maxHoldingSessions: event.target.value }))} /></label>
                  <button type="button" className="secondary-action" disabled={comparingRsiExits || loading} onClick={compareRsiExits}>{comparingRsiExits ? <><LoaderCircle className="spin" size={15} />Comparing…</> : "Compare RSI exit settings"}</button>
                  <small>Uses chronological validation and one common configuration across every selected symbol. Research candidates are never live-approved automatically.</small>
                </fieldset>}
                <fieldset className="recovery-config-card cost-card">
                  <legend>Estimated costs</legend>
                  <label><span>Buy cost</span><span className="suffixed-input"><input type="number" {...numericConstraints("rsi_recovery", "buyCostBps")} value={buyCostBps} onChange={(event) => setBuyCostBps(Number(event.target.value))} /><i>bps</i></span></label>
                  <label><span>Sell cost</span><span className="suffixed-input"><input type="number" {...numericConstraints("rsi_recovery", "sellCostBps")} value={sellCostBps} onChange={(event) => setSellCostBps(Number(event.target.value))} /><i>bps</i></span></label>
                  <label><span>Slippage / side</span><span className="suffixed-input"><input type="number" {...numericConstraints("rsi_recovery", "slippageBps")} value={slippageBps} onChange={(event) => setSlippageBps(Number(event.target.value))} /><i>bps</i></span></label>
                  <small>Defaults remain zero. These assumptions are applied to net position P&amp;L.</small>
                </fieldset>
              </div>
            </details>
          </>}
          {loading ? (
            <button className="run-backtest stop-backtest" type="button" onClick={() => runAbortRef.current?.abort()}><Square size={15} />Stop {runProgress ? `${runProgress.completed}/${runProgress.total}` : "run"}</button>
          ) : (
            <button className="run-backtest" type="submit" disabled={strategyMode === "market_aligned_rsi_scalper" && marketFormInvalid}><TrendingUp size={17} />Run backtest</button>
          )}
        </form>

        <section className="backtest-panel backtest-history-panel" aria-labelledby="backtest-history-title">
          <div className="panel-title">
            <div><span className="section-kicker">Synced to your account</span><h2 id="backtest-history-title">Recent backtests</h2></div>
            <span className="cost-note">Latest 10 completed results · available in every signed-in browser</span>
          </div>
          {historyMessage && <div className="backtest-history-message"><AlertTriangle size={16} /><span>{historyMessage}</span></div>}
          {backtestHistory.length ? <div className="backtest-history-list">
            {backtestHistory.map((item) => (
              <article className={activeHistoryId === item.id ? "active" : ""} key={item.id}>
                <div>
                  <strong>{item.strategyName}</strong>
                  <span>{formatIst(item.completedAt)} · {item.durationYears}Y · {item.timeframe} · {item.symbolCount} symbol{item.symbolCount === 1 ? "" : "s"}</span>
                </div>
                <button type="button" className="secondary-action" disabled={loadingHistoryId === item.id} onClick={() => void viewStoredBacktest(item)}>
                  {loadingHistoryId === item.id ? "Loading..." : activeHistoryId === item.id ? "Viewing" : "View result"}
                </button>
              </article>
            ))}
          </div> : <p className="backtest-history-empty">Completed backtests will appear here automatically and sync to the signed-in account.</p>}
        </section>

        {error && <div className="backtest-message error"><AlertTriangle size={17} /><span>{error}</span></div>}
        {loading && <div className="backtest-loading"><LoaderCircle className="spin" size={22} /><div><strong>Fetching and testing historical candles</strong><span>{runProgress ? `${runProgress.completed} of ${runProgress.total} symbols complete. ` : ""}Intraday universe runs can take much longer the first time; cached runs are faster.</span></div></div>}
        {optimizing && <div className="backtest-loading"><LoaderCircle className="spin" size={22} /><div><strong>Running chronological ATR walk-forward analysis</strong><span>The configurable grid is evaluated with one common setting across all selected symbols. This optional research run can take time.</span></div></div>}
        {optimization && <AtrOptimizationResults response={optimization} />}
        {comparingRsiExits && <div className="backtest-loading"><LoaderCircle className="spin" size={22} /><div><strong>Comparing RSI exit settings chronologically</strong><span>Training and validation stay separate, costs are included, and one common configuration is applied to all selected symbols.</span></div></div>}
        {rsiComparison && <RsiExitComparisonResults response={rsiComparison} />}
        {comparingOi && <div className="backtest-loading"><LoaderCircle className="spin" size={22} /><div><strong>Comparing NIFTY OI modes chronologically</strong><span>OFF, ADVISORY, RESEARCH_FILTER and ENFORCED reuse one candidate specification and one market-regime configuration.</span></div></div>}
        {oiComparison && <section className="backtest-panel oi-comparison-panel">
          <div className="panel-title"><div><span className="section-kicker">{oiComparison.metadata.researchLabel}</span><h2>OI filter comparison</h2></div><span className="cost-note">Default remains {oiComparison.metadata.defaultOiMode}</span></div>
          <div className="metric-grid">
            <div><span>Accepted</span><strong>{number(oiComparison.comparison.tradesAccepted, 0)}</strong></div>
            <div><span>Rejected</span><strong>{number(oiComparison.comparison.tradesRejected, 0)}</strong></div>
            <div><span>Strong-bearish rejects</span><strong>{number(oiComparison.comparison.tradesRejectedByStrongBearishOi, 0)}</strong></div>
            <div><span>Bearish rejects</span><strong>{number(oiComparison.comparison.tradesRejectedByBearishOi, 0)}</strong></div>
            <div><span>Volatility rejects</span><strong>{number(oiComparison.comparison.tradesRejectedByVolatilityExpansion, 0)}</strong></div>
            <div><span>Missing-data skips</span><strong>{number(oiComparison.comparison.tradesSkippedForMissingData, 0)}</strong></div>
            <div><span>Rejected winners</span><strong>{number(oiComparison.comparison.rejectedTradesThatWouldHaveWon, 0)}</strong></div>
            <div><span>Avoided losers</span><strong>{number(oiComparison.comparison.rejectedTradesThatWouldHaveLost, 0)}</strong></div>
            <div><span>False-rejection rate</span><strong>{percent(oiComparison.comparison.falseRejectionRate)}</strong></div>
            <div><span>Stop exits avoided</span><strong>{number(oiComparison.comparison.stopExitsAvoided, 0)}</strong></div>
            <div><span>Net P&amp;L change</span><strong className={tone(oiComparison.comparison.netPnlChange)}>{money(oiComparison.comparison.netPnlChange)}</strong></div>
            <div><span>Profit-factor change</span><strong className={tone(oiComparison.comparison.profitFactorChange)}>{number(oiComparison.comparison.profitFactorChange)}</strong></div>
          </div>
          <div className={`backtest-message ${oiComparison.acceptance.passed ? "open-position" : "error"}`}><Info size={17} /><span><strong>Acceptance: {oiComparison.acceptance.passed ? "passed" : "not proven"}.</strong> {oiComparison.acceptance.reason} Advisory parity: {oiComparison.comparison.advisoryExecutionIdentical ? "identical" : "mismatch detected"}. Walk-forward folds: {oiComparison.walkForwardValidation.folds.length}.</span></div>
        </section>}

        {response && (
          isRecoveryResponse(response) ? <RecoveryResults response={response} /> : <>
            <section className="backtest-overview">
              <div><span>Symbols tested</span><strong>{response.results.length}</strong></div>
              <div><span>Profitable</span><strong className="positive-value">{profitableCount}</strong></div>
              <div><span>Total closed trades</span><strong>{response.results.reduce((sum, result) => sum + result.closedTrades, 0)}</strong></div>
              <div><span>Test window</span><strong>{response.metadata.durationYears}Y · {response.metadata.timeframe}</strong></div>
              <div><span>Generated</span><strong>{formatIst(response.metadata.generatedAt)}</strong></div>
            </section>

            <section className="backtest-panel">
              <div className="panel-title"><div><span className="section-kicker">All selected symbols</span><h2>Performance summary</h2></div><span className="cost-note">Includes fees + {number(response.metadata.costModel.slippagePerSidePct)}% slippage per side / sell target at least {number(response.metadata.minimumNetProfitPct)}% net</span></div>
              <div className="summary-grid summary-head"><span>Symbol</span><span>Result</span><span>Trades</span><span>Win rate</span><span>Strategy</span><span>Buy & hold</span><span>NIFTY 50</span><span>Max drawdown</span><span>Profit factor</span></div>
              {response.results.map((result) => (
                <button key={result.symbol} type="button" className={`summary-grid summary-row ${detail?.symbol === result.symbol ? "selected" : ""}`} onClick={() => setDetailSymbol(result.symbol)}>
                  <strong>{result.symbol}</strong><span className={`verdict ${result.verdict}`}>{result.verdict === "no-trades" ? "No trades" : result.verdict}</span><span data-label="Trades">{result.closedTrades}</span><span data-label="Win rate">{percent(result.winRate)}</span><span data-label="Strategy" className={tone(result.strategyReturnPct)}>{percent(result.strategyReturnPct)}</span><span data-label="Buy & hold" className={tone(result.buyHoldReturnPct)}>{percent(result.buyHoldReturnPct)}</span><span data-label="NIFTY 50" className={tone(result.niftyReturnPct)}>{percent(result.niftyReturnPct)}</span><span data-label="Max drawdown" className="negative-value">{percent(result.maxDrawdownPct)}</span><span data-label="Profit factor">{number(result.profitFactor)}</span>
                </button>
              ))}
            </section>

            {detail && (
              <section className="backtest-panel detail-panel">
                <div className="panel-title"><div><span className="section-kicker">Selected result</span><h2>{detail.symbol} trade history</h2></div><span className="date-window">{formatIst(detail.firstCandle, response.metadata.timeframe === "1d")} – {formatIst(detail.lastCandle, response.metadata.timeframe === "1d")}</span></div>
                <div className="metric-grid">
                  <div><span>Strategy return</span><strong className={tone(detail.strategyReturnPct)}>{percent(detail.strategyReturnPct)}</strong></div>
                  <div><span>CAGR</span><strong className={tone(detail.cagrPct)}>{percent(detail.cagrPct)}</strong></div>
                  <div><span>Win rate</span><strong>{percent(detail.winRate)}</strong></div>
                  <div><span>Sharpe</span><strong>{number(detail.sharpe)}</strong></div>
                  <div><span>Sortino</span><strong>{number(detail.sortino)}</strong></div>
                  <div><span>Avg win / loss</span><strong>{percent(detail.averageWinPct)} / {percent(detail.averageLossPct)}</strong></div>
                  <div><span>Ending capital</span><strong>{money(detail.endingCapital)}</strong></div>
                  <div><span>Candles tested</span><strong>{number(detail.bars, 0)}</strong></div>
                  <div><span>High-RSI exits held</span><strong>{number(detail.heldExitSignals, 0)}</strong></div>
                </div>
                <PerformanceChart key={`${detail.symbol}-${detail.firstCandle}-${detail.lastCandle}`} points={detail.chart} entryRange={response.metadata.entryRange} exitRange={response.metadata.exitRange} timeframe={response.metadata.timeframe} />

                {detail.openPosition && <div className="backtest-message open-position"><Clock3 size={17} /><span><strong>Open position:</strong> bought {detail.openPosition.quantity} shares at {money(detail.openPosition.entryPrice)} on {formatIst(detail.openPosition.entryTime)}; marked at {money(detail.openPosition.lastPrice)} ({percent(detail.openPosition.estimatedReturnPct)}).</span></div>}

                <div className="history-columns">
                  <div className="history-block">
                    <h3>Completed trades</h3>
                    {detail.trades.length ? <div className="trade-list">{detail.trades.map((trade, index) => (
                      <div className="trade-card" key={`${trade.entryTime}-${index}`}>
                        <div className="trade-card-top"><strong>Trade {index + 1}</strong><span className={tone(trade.returnPct)}>{percent(trade.returnPct)} · {money(trade.netPnl)}</span></div>
                        <div className="trade-leg buy"><span>BUY · RSI {number(trade.entryRsi)}</span><strong>{money(trade.entryPrice)}</strong><small>{formatIst(trade.entryTime)}</small></div>
                        <div className="trade-leg sell"><span>SELL · RSI {number(trade.exitRsi)}</span><strong>{money(trade.exitPrice)}</strong><small>{formatIst(trade.exitTime)}</small></div>
                        <div className="trade-foot"><span>{trade.holdingBars} candles held</span><span>{money(trade.fees)} estimated fees</span></div>
                      </div>
                    ))}</div> : <div className="empty-history">No completed low-to-high RSI trade occurred in this window.</div>}
                  </div>
                  <div className="history-block">
                    <h3>RSI range occurrences</h3>
                    {detail.events.length ? <div className="event-list">{[...detail.events].reverse().map((item, index) => (
                      <div className="event-row" key={`${item.signalTime}-${item.range}-${index}`}><span className={`event-range ${item.range}`}>{item.range === "entry" ? `${response.metadata.entryRange[0]}–${response.metadata.entryRange[1]}` : `${response.metadata.exitRange[0]}–${response.metadata.exitRange[1]}`}</span><div><strong>RSI {number(item.rsi)} · {money(item.signalClose)}</strong><span>{formatIst(item.signalTime)}</span></div><div><strong>{item.nextOpen === null ? "No next candle" : money(item.nextOpen)}</strong><span>{item.nextCandleTime ? `Next open · ${formatIst(item.nextCandleTime)}` : "Signal only"}</span></div></div>
                    ))}</div> : <div className="empty-history">No RSI range entries occurred in this window.</div>}
                  </div>
                </div>
              </section>
            )}

            {(response.errors.length > 0 || response.warnings.length > 0) && <section className="backtest-notes"><h3>Backtest notes</h3>{response.errors.map((item) => <p key={item.symbol}><strong>{item.symbol}:</strong> {item.message}</p>)}{response.warnings.map((warning) => <p key={warning}>{warning}</p>)}</section>}
          </>
        )}
      </main>
    </div>
  );
}
