import type { Metadata } from "next";
import { parseMarket } from "../platform/platform-client";
import { requireSessionUser } from "../server-auth";
import { BacktestWorkspace } from "./backtest-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Backtest",
  description: "Run registered strategies against a saved universe with database-backed incremental backtests.",
};

export default async function BacktestPage({ searchParams }: { searchParams: Promise<{ market?: string; runId?: string }> }) {
  await requireSessionUser();
  const parameters = await searchParams;
  return <BacktestWorkspace market={parseMarket(parameters.market)} initialRunId={parameters.runId} />;
}
