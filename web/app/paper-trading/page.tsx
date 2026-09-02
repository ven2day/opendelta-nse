import type { Metadata } from "next";
import { parseMarket } from "../platform/platform-client";
import { requireSessionUser } from "../server-auth";
import { PaperWorkspace } from "./paper-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Paper Trading",
  description: "Simulated NSE and crypto paper accounts: equity, open lots, orders and fills. Broker execution is disabled.",
};

export default async function PaperTradingPage({ searchParams }: { searchParams: Promise<{ market?: string }> }) {
  await requireSessionUser();
  return <PaperWorkspace market={parseMarket((await searchParams).market)} />;
}
