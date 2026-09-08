"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import type { PlatformMarket } from "../platform/platform-client";
import { useV2Resource } from "../platform/use-v2";
import { v2Get } from "../platform/v2-client";
import type { BacktestChartResponse, IndicatorSourcesResponse } from "../platform/v2-types";
import { EmptyState, LoadingState, RequestErrorState, StatusBadge } from "../platform/workspace-ui";

const W = 1200, H = 560, LEFT = 12, RIGHT = 76, TOP = 20, BOTTOM = 34;
const COLORS = ["#d2a84a", "#55a8ff", "#ab7df6", "#2dbe82", "#ef8f4f", "#e56bd2"];

function StrategySvg({ data, bars }: { data: BacktestChartResponse; bars: number | "trades" }) {
  const svg = useRef<SVGSVGElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const model = useMemo(() => {
    const total = data.candles.timestamp.length;
    const byMinute = new Map(data.candles.timestamp.map((stamp, index) => [Math.floor(new Date(stamp).getTime() / 60_000), index]));
    const nearest = (stamp?: string | null) => stamp ? byMinute.get(Math.floor(new Date(stamp).getTime() / 60_000)) ?? null : null;
    const tradeIndices = data.trades.flatMap((trade) => [nearest(trade.signalTimestamp), nearest(trade.entryTimestamp), nearest(trade.exitTimestamp)]).filter((value): value is number => value != null);
    const start = bars === "trades" && tradeIndices.length ? Math.max(0, Math.min(...tradeIndices) - 20) : Math.max(0, total - Number(bars));
    const end = bars === "trades" && tradeIndices.length ? Math.min(total - 1, Math.max(...tradeIndices) + 20) : total - 1;
    const indices = Array.from({ length: Math.max(0, end - start + 1) }, (_, offset) => start + offset);
    const visibleTrades = data.trades.filter((trade) => {
      const from = nearest(trade.entryTimestamp) ?? nearest(trade.signalTimestamp);
      const to = nearest(trade.exitTimestamp) ?? end;
      return from != null && to >= start && from <= end;
    }).slice(-300);
    const tradePrices = visibleTrades.flatMap((trade) => [trade.entryPrice, trade.exitPrice, trade.targetPrice, trade.stopPrice].filter((value): value is number => value != null));
    const indicatorPrices = data.indicator?.rows.slice(start, end + 1).flatMap((row) => Object.values(row).filter((value): value is number => value != null)) ?? [];
    const prices = [...indices.flatMap((index) => [data.candles.low[index], data.candles.high[index]]), ...tradePrices, ...indicatorPrices];
    const low = Math.min(...prices), high = Math.max(...prices);
    const margin = Math.max((high - low) * .05, Math.abs(high) * .001, .01);
    const min = low - margin, max = high + margin;
    const x = (index: number) => LEFT + ((index - start) + .5) * (W - LEFT - RIGHT) / Math.max(1, indices.length);
    const y = (price: number) => TOP + (max - price) * (H - TOP - BOTTOM) / (max - min);
    return { start, end, indices, min, max, x, y, nearest, visibleTrades, candleWidth: Math.max(1, Math.min(8, (W - LEFT - RIGHT) / Math.max(1, indices.length) * .65)) };
  }, [bars, data]);
  const onPointerMove = (event: PointerEvent<SVGSVGElement>) => {
    const box = svg.current?.getBoundingClientRect();
    if (!box) return;
    const viewX = (event.clientX - box.left) * W / box.width;
    const offset = Math.floor((viewX - LEFT) / (W - LEFT - RIGHT) * model.indices.length);
    setHover(model.indices[Math.max(0, Math.min(model.indices.length - 1, offset))]);
  };
  const selected = hover == null ? null : { time: data.candles.timestamp[hover], open: data.candles.open[hover], high: data.candles.high[hover], low: data.candles.low[hover], close: data.candles.close[hover] };
  return <div className="quant-chart-stage">{selected && <div className="quant-chart-hover"><strong>{new Date(selected.time).toLocaleString()}</strong><span>O {selected.open.toFixed(4)}</span><span>H {selected.high.toFixed(4)}</span><span>L {selected.low.toFixed(4)}</span><span>C {selected.close.toFixed(4)}</span></div>}<svg ref={svg} viewBox={`0 0 ${W} ${H}`} className="quant-chart-canvas" role="img" aria-label={`${data.symbol} candlestick strategy chart`} onPointerMove={onPointerMove} onPointerLeave={() => setHover(null)}>
    <rect width={W} height={H} fill="#090b0f" />
    {[0, 1, 2, 3, 4].map((tick) => { const price = model.max - (model.max - model.min) * tick / 4, y = model.y(price); return <g key={tick}><line x1={LEFT} x2={W - RIGHT} y1={y} y2={y} stroke="#1c222c" /><text x={W - RIGHT + 8} y={y + 4} fill="#a1a8b3" fontSize="11">{price.toFixed(2)}</text></g>; })}
    {model.indices.map((index) => { const x = model.x(index), open = model.y(data.candles.open[index]), close = model.y(data.candles.close[index]), color = data.candles.close[index] >= data.candles.open[index] ? "#2dbe82" : "#ef626c"; return <g key={data.candles.timestamp[index]}><line x1={x} x2={x} y1={model.y(data.candles.high[index])} y2={model.y(data.candles.low[index])} stroke={color} /><rect x={x - model.candleWidth / 2} y={Math.min(open, close)} width={model.candleWidth} height={Math.max(1, Math.abs(close - open))} fill={color} /></g>; })}
    {model.visibleTrades.map((trade) => { const signal = model.nearest(trade.signalTimestamp), entry = model.nearest(trade.entryTimestamp), from = entry ?? signal, to = model.nearest(trade.exitTimestamp) ?? model.indices.at(-1) ?? null; if (from == null || to == null || to < model.start) return null; const signalPrice = trade.signalPrice ?? trade.entryPrice ?? 0, entryPrice = trade.entryPrice ?? signalPrice, exitPrice = trade.exitPrice ?? trade.targetPrice ?? 0; return <g key={trade.lotId}>{trade.targetPrice != null && <line x1={model.x(Math.max(from, model.start))} x2={model.x(to)} y1={model.y(trade.targetPrice)} y2={model.y(trade.targetPrice)} stroke="#2dbe82" strokeDasharray="5 4" />}{trade.stopPrice != null && <line x1={model.x(Math.max(from, model.start))} x2={model.x(to)} y1={model.y(trade.stopPrice)} y2={model.y(trade.stopPrice)} stroke="#ef626c" strokeDasharray="5 4" />}{signal != null && signal >= model.start && <><polygon points={`${model.x(signal)},${model.y(signalPrice) - 11} ${model.x(signal) - 5},${model.y(signalPrice) - 2} ${model.x(signal) + 5},${model.y(signalPrice) - 2}`} fill="#55a8ff" /><text x={model.x(signal) + 7} y={model.y(signalPrice) - 4} fill="#55a8ff" fontSize="10">BUY</text></>}{entry != null && entry >= model.start && <><circle cx={model.x(entry)} cy={model.y(entryPrice)} r="4" fill="#d2a84a" /><text x={model.x(entry) + 7} y={model.y(entryPrice) + 4} fill="#d2a84a" fontSize="10">ENTRY</text></>}{trade.exitTimestamp && to >= model.start && <><polygon points={`${model.x(to)},${model.y(exitPrice) + 11} ${model.x(to) - 5},${model.y(exitPrice) + 2} ${model.x(to) + 5},${model.y(exitPrice) + 2}`} fill={trade.status === "STOPPED" ? "#ef626c" : "#2dbe82"} /><text x={model.x(to) + 7} y={model.y(exitPrice) + 12} fill={trade.status === "STOPPED" ? "#ef626c" : "#2dbe82"} fontSize="10">SELL</text></>}</g>; })}
    {data.indicator?.outputs.map((output, outputIndex) => <polyline key={output.name} points={model.indices.flatMap((index) => { const value = data.indicator?.rows[index]?.[output.name]; return value == null ? [] : [`${model.x(index)},${model.y(value)}`]; }).join(" ")} fill="none" stroke={COLORS[outputIndex % COLORS.length]} strokeWidth="2" />)}
    {hover != null && <line x1={model.x(hover)} x2={model.x(hover)} y1={TOP} y2={H - BOTTOM} stroke="#a1a8b3" strokeDasharray="3 3" />}
  </svg></div>;
}

export function StrategyChartWorkspace({ runId, symbols, preferredSymbol }: { runId: string; symbols: string[]; preferredSymbol?: string; market: PlatformMarket }) {
  const [symbol, setSymbol] = useState(preferredSymbol ?? symbols[0] ?? "");
  const [indicatorSourceId, setIndicatorSourceId] = useState("");
  const [bars, setBars] = useState<number | "trades">("trades");
  useEffect(() => setSymbol(preferredSymbol && symbols.includes(preferredSymbol) ? preferredSymbol : symbols[0] ?? ""), [runId, preferredSymbol, symbols]);
  const effectiveSymbol = symbols.includes(symbol) ? symbol : symbols[0] ?? "";
  const loadIndicators = useCallback(() => v2Get<IndicatorSourcesResponse>("indicator-studio/sources", { status: "VALIDATED" }), []);
  const indicators = useV2Resource(loadIndicators);
  const loadChart = useCallback(() => v2Get<BacktestChartResponse>(`backtests/${runId}/chart`, { symbol: effectiveSymbol, indicatorSourceId: indicatorSourceId || undefined }), [runId, effectiveSymbol, indicatorSourceId]);
  const chart = useV2Resource(loadChart);
  const trades = chart.data?.trades ?? [];
  return <div className="quant-panel-body quant-strategy-chart"><div className="quant-chart-toolbar"><label><span>Symbol</span><select value={effectiveSymbol} onChange={(event) => setSymbol(event.target.value)}>{symbols.map((item) => <option key={item}>{item}</option>)}</select></label><label><span>Indicator overlay</span><select value={indicatorSourceId} onChange={(event) => setIndicatorSourceId(event.target.value)}><option value="">None</option>{(indicators.data?.sources ?? []).map((item) => <option key={item.sourceId} value={item.sourceId}>{item.name} · v{item.indicatorVersion}</option>)}</select></label><label><span>Chart range</span><select value={bars} onChange={(event) => setBars(event.target.value === "trades" ? "trades" : Number(event.target.value))}><option value="trades">Fit all trades</option>{[100, 250, 500, 1000].map((item) => <option key={item} value={item}>Latest {item} candles</option>)}</select></label><div className="quant-chart-legend"><StatusBadge>{trades.length} BUY</StatusBadge><StatusBadge tone="good">{trades.filter((trade) => trade.exitTimestamp).length} SELL</StatusBadge><span><i />Target</span><span><i data-kind="stop" />Stop</span></div></div>{chart.loading ? <LoadingState label="Loading chart candles and trades" /> : chart.error ? <RequestErrorState error={chart.error} retry={chart.reload} /> : !chart.data ? <EmptyState title="Chart unavailable" description="No completed candles were returned." /> : <><StrategySvg data={chart.data} bars={bars} /><small className="quant-chart-note">Showing the selected run only. Blue BUY, gold ENTRY, green SELL, red STOP. Hover for exact OHLC.</small></>}</div>;
}
