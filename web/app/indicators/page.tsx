import type { Metadata } from "next";
import { parseMarket } from "../platform/platform-client";
import { requireSessionUser } from "../server-auth";
import { IndicatorStudioWorkspace } from "./indicator-studio-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Indicators",
  description: "Create and validate immutable Python indicator versions.",
};

export default async function IndicatorsPage({ searchParams }: { searchParams: Promise<{ market?: string }> }) {
  await requireSessionUser();
  const query = await searchParams;
  return <IndicatorStudioWorkspace initialMarket={parseMarket(query.market)} />;
}
