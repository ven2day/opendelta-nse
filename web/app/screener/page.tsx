import type { Metadata } from "next";
import { parseMarket } from "../platform/platform-client";
import { requireSessionUser } from "../server-auth";
import { ScreenerWorkspace } from "./screener-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Screener",
  description: "Screen NSE and crypto instruments by liquidity, price and volatility, then save the result as a universe.",
};

export default async function ScreenerPage({ searchParams }: { searchParams: Promise<{ market?: string }> }) {
  await requireSessionUser();
  return <ScreenerWorkspace market={parseMarket((await searchParams).market)} />;
}
