import type { Metadata } from "next";
import { parseMarket } from "../platform/platform-client";
import { requireSessionUser } from "../server-auth";
import { SignalsWorkspace } from "./signals-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Signals",
  description: "Live completed-candle signals from the unified engine with health and colour-coded lifecycle status.",
};

export default async function SignalsPage({ searchParams }: { searchParams: Promise<{ market?: string }> }) {
  await requireSessionUser();
  return <SignalsWorkspace market={parseMarket((await searchParams).market)} />;
}
