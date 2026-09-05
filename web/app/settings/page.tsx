import type { Metadata } from "next";
import { parseMarket } from "../platform/platform-client";
import { requireSessionUser } from "../server-auth";
import { SettingsWorkspace } from "./settings-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Settings",
  description: "Versioned strategy and paper-execution configuration.",
};

export default async function SettingsPage({ searchParams }: { searchParams: Promise<{ market?: string }> }) {
  await requireSessionUser();
  const query = await searchParams;
  return <SettingsWorkspace initialMarket={parseMarket(query.market)} />;
}
