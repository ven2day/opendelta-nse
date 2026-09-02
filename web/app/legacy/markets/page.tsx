import type { Metadata } from "next";
import { requireSessionUser } from "../../server-auth";
import { MarketWorkspace } from "./market-workspace";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Markets", description: "NSE and crypto market workspaces." };

export default async function MarketsPage({ searchParams }: { searchParams: Promise<{ market?: string }> }) {
  await requireSessionUser();
  const market = (await searchParams).market === "CRYPTO" ? "CRYPTO" : "NSE";
  return <MarketWorkspace initialMarket={market} />;
}
