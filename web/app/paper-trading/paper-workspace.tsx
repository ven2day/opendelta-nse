"use client";

import { ArrowRightLeft, ListChecks, RefreshCw, Trash2, Wallet } from "lucide-react";
import { useCallback, useState } from "react";
import { formatDateTime, formatInteger, formatMoney, formatNumber, formatPercent, marketCurrency, marketLabel, tone } from "../platform/format";
import type { PlatformMarket } from "../platform/platform-client";
import { useV2Resource } from "../platform/use-v2";
import { errorMessage, v2Get, v2Post } from "../platform/v2-client";
import type { PaperAccount, PaperLot, PaperOrder, PaperTrade } from "../platform/v2-types";
import { EmptyState, LoadingState, Message, PaperOnlyBadge, Panel, PnlValue, RequestErrorState, StatusBadge, WorkspaceHeader } from "../platform/workspace-ui";

const PAPER_REFRESH_MS = 15_000;
type Notice = { kind: "success" | "error"; text: string } | null;
type PaperSnapshot = { account: PaperAccount; positions: PaperLot[]; orders: PaperOrder[]; trades: PaperTrade[] };

export function PaperWorkspace({ market }: { market: PlatformMarket }) {
  const load = useCallback(async (): Promise<PaperSnapshot> => {
    const [account, positions, orders, trades] = await Promise.all([
      v2Get<PaperAccount>(`paper/accounts/${market}`),
      v2Get<{ positions: PaperLot[] }>("paper/positions", { market }),
      v2Get<{ orders: PaperOrder[] }>("paper/orders", { market }),
      v2Get<{ trades: PaperTrade[] }>("paper/trades", { market }),
    ]);
    return { account, positions: positions.positions ?? [], orders: orders.orders ?? [], trades: trades.trades ?? [] };
  }, [market]);
  const snapshot = useV2Resource(load, PAPER_REFRESH_MS);
  const { refresh } = snapshot;
  const [tab, setTab] = useState<"orders" | "trades">("orders");
  const [closingLotId, setClosingLotId] = useState<string | null>(null);
  const [resetBalance, setResetBalance] = useState("");
  const [resetting, setResetting] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  const account = snapshot.data?.account ?? null;
  const currency = account?.currency ?? marketCurrency(market);

  const closeLot = async (lot: PaperLot) => {
    if (!window.confirm(`Close paper lot ${lot.symbol} (${formatNumber(lot.quantity, 4)} units) using its latest completed-candle mark? Paper fees and slippage will be applied.`)) return;
    setClosingLotId(lot.lotId);
    setNotice(null);
    try {
      await v2Post(`paper/lots/${lot.lotId}/close`, {}, { market });
      setNotice({ kind: "success", text: `Paper lot ${lot.symbol} closed using the latest market mark.` });
      refresh();
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "The paper lot could not be closed") });
    } finally {
      setClosingLotId(null);
    }
  };

  const resetAccount = async () => {
    const startingBalance = resetBalance.trim() ? Number(resetBalance) : undefined;
    if (startingBalance !== undefined && (!Number.isFinite(startingBalance) || startingBalance <= 0)) {
      setNotice({ kind: "error", text: "Enter a positive starting balance or leave it empty to keep the current default." });
      return;
    }
    if (!window.confirm(`Reset the ${marketLabel(market)} paper account? All simulated lots, orders and fills for this market are discarded.`)) return;
    setResetting(true);
    setNotice(null);
    try {
      await v2Post(`paper/accounts/${market}/reset`, { market, ...(startingBalance !== undefined ? { startingBalance } : {}) });
      setNotice({ kind: "success", text: `${marketLabel(market)} paper account reset.` });
      setResetBalance("");
      refresh();
    } catch (reason) {
      setNotice({ kind: "error", text: errorMessage(reason, "The paper account could not be reset") });
    } finally {
      setResetting(false);
    }
  };

  return <main className="quant-workspace">
    <WorkspaceHeader
      eyebrow={`${marketLabel(market)} paper trading`}
      title="Paper account"
      actions={<div className="quant-header-actions"><PaperOnlyBadge /><button type="button" onClick={refresh}><RefreshCw size={15} />Refresh</button></div>}
    />

    {snapshot.loading ? <LoadingState label="Loading paper account" /> : snapshot.error ? <RequestErrorState error={snapshot.error} retry={snapshot.reload} /> : snapshot.data && account && <>
      <section className="quant-portfolio-hero quant-portfolio-standalone" aria-label="Paper portfolio summary">
        <div className="quant-portfolio-value"><span>Portfolio equity</span><strong>{formatMoney(account.equity, market, currency)}</strong><small>Starting balance {formatMoney(account.startingBalance, market, currency)} · {formatInteger(account.openPositions)} open positions</small></div>
        <dl>
          <div><dt>Today</dt><dd><PnlValue value={account.dailyPnl} market={market} currency={currency} /></dd></div>
          <div><dt>Realized</dt><dd><PnlValue value={account.realizedPnl} market={market} currency={currency} /></dd></div>
          <div><dt>Unrealized</dt><dd><PnlValue value={account.unrealizedPnl} market={market} currency={currency} /></dd></div>
          <div><dt>Cash</dt><dd>{formatMoney(account.cashBalance, market, currency)}</dd></div>
        </dl>
      </section>
      {notice && <Message kind={notice.kind}>{notice.text}</Message>}

      <Panel icon={<Wallet size={17} />} title="Open positions" description="Manual closes use the latest completed-candle mark and apply the configured paper fee and slippage model." aside={<StatusBadge tone="good">{formatInteger(snapshot.data.positions.length)} open</StatusBadge>}>
        {!snapshot.data.positions.length ? <EmptyState title="No open paper positions" description="Lots open automatically when the signal engine records a strong buy." /> : <div className="quant-table-scroll"><table className="quant-table">
          <thead><tr><th>Symbol</th><th className="numeric">Qty</th><th className="numeric">Entry</th><th className="numeric">Last</th><th className="numeric">{market === "NSE" ? "FIFO net target" : "Target"}</th><th className="numeric">Stop</th><th className="numeric">Unrealized</th><th className="numeric">MAE / MFE</th><th>Expires</th><th>Status</th><th>Manual close</th></tr></thead>
          <tbody>{snapshot.data.positions.map((lot) => <tr key={lot.lotId}>
            <td><strong>{lot.symbol}</strong><small>Lot {lot.lotNumber ?? "—"} · {formatDateTime(lot.entryTimestamp, market)}</small></td>
            <td className="numeric">{formatNumber(lot.quantity, 4)}</td>
            <td className="numeric">{formatNumber(lot.entryPrice)}{lot.costBasisPrice != null && lot.entryPrice != null && Math.abs(lot.costBasisPrice - lot.entryPrice) > 0.0001 && <small>FIFO {formatNumber(lot.costBasisPrice)} · {lot.fifoAllocations?.length ?? 1} buys</small>}</td>
            <td className="numeric">{formatNumber(lot.lastPrice)}</td>
            <td className="numeric">{formatNumber(lot.targetPrice)}</td>
            <td className="numeric">{formatNumber(lot.stopPrice)}</td>
            <td className="numeric"><PnlValue value={lot.unrealizedPnl} market={market} currency={currency} /></td>
            <td className="numeric">{formatPercent(lot.maePct)} / {formatPercent(lot.mfePct)}</td>
            <td>{formatDateTime(lot.expiresAt, market)}</td>
            <td><StatusBadge tone={tone(lot.status)}>{lot.status}</StatusBadge></td>
            <td><button type="button" className="danger" disabled={closingLotId === lot.lotId || !lot.lastPrice} onClick={() => void closeLot(lot)}>{closingLotId === lot.lotId ? "Closing…" : "Close at mark"}</button></td>
          </tr>)}</tbody>
        </table></div>}
      </Panel>

      <Panel icon={tab === "orders" ? <ListChecks size={17} /> : <ArrowRightLeft size={17} />} title="Activity" description="Simulated order decisions and the fills they produced." aside={<div className="quant-section-tabs" role="tablist" aria-label="Paper activity">
        <button type="button" role="tab" aria-selected={tab === "orders"} className={tab === "orders" ? "active" : ""} onClick={() => setTab("orders")}>Orders ({formatInteger(snapshot.data.orders.length)})</button>
        <button type="button" role="tab" aria-selected={tab === "trades"} className={tab === "trades" ? "active" : ""} onClick={() => setTab("trades")}>Trades ({formatInteger(snapshot.data.trades.length)})</button>
      </div>}>
        {tab === "orders" ? (!snapshot.data.orders.length ? <EmptyState title="No paper orders" description="Order decisions are recorded when signals are filled or rejected." /> : <div className="quant-table-scroll tall"><table className="quant-table">
          <thead><tr><th>Order</th><th>Symbol</th><th>Side</th><th className="numeric">Qty</th><th className="numeric">Requested</th><th className="numeric">Executed</th><th className="numeric">Fees</th><th className="numeric">Slippage</th><th>Status</th><th>Reason</th><th>Order time</th></tr></thead>
          <tbody>{snapshot.data.orders.map((order) => <tr key={order.orderId}>
            <td className="mono">{order.orderId.slice(0, 8)}</td>
            <td><strong>{order.symbol}</strong></td>
            <td>{order.side}</td>
            <td className="numeric">{formatNumber(order.quantity, 4)}</td>
            <td className="numeric">{formatNumber(order.requestedPrice)}</td>
            <td className="numeric">{formatNumber(order.executedPrice)}</td>
            <td className="numeric">{formatNumber(order.fees)}</td>
            <td className="numeric">{formatNumber(order.slippage)}</td>
            <td><StatusBadge tone={tone(order.status)}>{order.status}</StatusBadge></td>
            <td>{order.reason ?? "—"}</td>
            <td>{formatDateTime(order.createdAt, market)}</td>
          </tr>)}</tbody>
        </table></div>) : (!snapshot.data.trades.length ? <EmptyState title="No paper fills" description="Executed paper trades appear here." /> : <div className="quant-table-scroll tall"><table className="quant-table">
          <thead><tr><th>Symbol</th><th>Side</th><th className="numeric">Qty</th><th className="numeric">Price</th><th className="numeric">Fees</th><th className="numeric">Slippage</th><th>Reason</th><th>Execution time</th></tr></thead>
          <tbody>{snapshot.data.trades.map((trade, index) => <tr key={`${trade.symbol}-${trade.executedAt ?? index}-${index}`}>
            <td><strong>{trade.symbol}</strong></td>
            <td>{trade.side}</td>
            <td className="numeric">{formatNumber(trade.quantity, 4)}</td>
            <td className="numeric">{formatNumber(trade.price)}</td>
            <td className="numeric">{formatNumber(trade.fees)}</td>
            <td className="numeric">{formatNumber(trade.slippage)}</td>
            <td>{trade.reason ?? "—"}</td>
            <td>{formatDateTime(trade.executedAt, market)}</td>
          </tr>)}</tbody>
        </table></div>)}
      </Panel>

      <details className="quant-danger-zone"><summary><Trash2 size={15} />Reset paper account</summary><Panel icon={<Trash2 size={17} />} title="Reset paper account" description="Discards every simulated lot, order and fill for this market and restores the starting balance.">
        <div className="quant-panel-body"><div className="quant-form-grid quant-reset-grid">
          <label><span>Starting balance ({currency})</span><input type="number" min={0} step="any" inputMode="decimal" value={resetBalance} placeholder={account.startingBalance != null ? String(account.startingBalance) : "Platform default"} onChange={(event) => setResetBalance(event.target.value)} /><small>Leave empty to keep the current starting balance</small></label>
        </div></div>
        <div className="quant-form-actions"><button type="button" className="danger" disabled={resetting} onClick={() => void resetAccount()}><Trash2 size={14} />{resetting ? "Resetting…" : "Reset account"}</button><span>Asks for confirmation. Paper only; no broker account is affected.</span></div>
      </Panel></details>
    </>}
  </main>;
}
