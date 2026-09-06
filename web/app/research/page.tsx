import type { Metadata } from "next";
import { parseMarket } from "../platform/platform-client";
import { requireSessionUser } from "../server-auth";
import { ResearchWorkspace } from "./research-workspace";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Research Lab", description: "Run controlled immutable strategy experiments." };

export default async function ResearchPage({ searchParams }: { searchParams: Promise<{ market?: string }> }) {
  await requireSessionUser();
  return <ResearchWorkspace market={parseMarket((await searchParams).market)} />;
}
