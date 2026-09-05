import type { Metadata } from "next";
import { parseMarket } from "../platform/platform-client";
import { requireSessionUser } from "../server-auth";
import { ScreenerWorkspace } from "./screener-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Watchlist",
  description: "Find eligible NSE and crypto candidates, then select the watchlist monitored by trading strategies.",
};

export default async function ScreenerPage({ searchParams }: { searchParams: Promise<{ market?: string }> }) {
  await requireSessionUser();
  return <ScreenerWorkspace market={parseMarket((await searchParams).market)} />;
}
