import type { Metadata } from "next";
import { requireSessionUser } from "../../server-auth";
import { StockScanner } from "../../scanner/stock-scanner";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "NSE Signal Engine V2 | OpenDelta",
  description: "Evaluate every eligible NSE symbol for explainable Trend Pullback and Breakout-Retest paper signals.",
};

export default async function NseSignalFunnelPage() {
  const username = await requireSessionUser();
  return <StockScanner userName={username} signOutHref="/api/logout" focusSignals />;
}
