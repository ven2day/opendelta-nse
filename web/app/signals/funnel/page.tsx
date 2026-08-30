import type { Metadata } from "next";
import { requireSessionUser } from "../../server-auth";
import { StockScanner } from "../../scanner/stock-scanner";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "NSE Signal Funnel | OpenDelta",
  description: "Rank completed-candle RSI Recovery and VWAP Pullback setups across the eligible NSE universe.",
};

export default async function NseSignalFunnelPage() {
  const username = await requireSessionUser();
  return <StockScanner userName={username} signOutHref="/api/logout" focusSignals />;
}
