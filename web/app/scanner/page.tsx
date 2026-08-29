import type { Metadata } from "next";
import { requireSessionUser } from "../server-auth";
import { StockScanner } from "./stock-scanner";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Stock Scanner · OpenDelta",
  description: "Causal NSE Top-5 opportunity scanner for paper research.",
};

export default async function StockScannerPage() {
  const userName = await requireSessionUser();
  return <StockScanner userName={userName} signOutHref="/api/logout" />;
}
