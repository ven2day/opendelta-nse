import type { Metadata } from "next";
import { DashboardWorkspace } from "./dashboard-workspace";
import { parseMarket } from "./platform/platform-client";
import { requireSessionUser } from "./server-auth";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Dashboard",
  description: "Unified NSE and crypto research dashboard: market data, screener, backtests, signals and paper trading.",
};

export default async function DashboardPage({ searchParams }: { searchParams: Promise<{ market?: string }> }) {
  await requireSessionUser();
  return <DashboardWorkspace market={parseMarket((await searchParams).market)} />;
}
