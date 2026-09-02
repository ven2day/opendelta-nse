import type { Metadata } from "next";
import { readGlobalSettings } from "../global-settings-server";
import { parseMarket } from "../platform/platform-client";
import { requireSessionUser } from "../server-auth";
import { SettingsWorkspace } from "./settings-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Settings",
  description: "Strategy configuration, risk defaults, the global price range and links to legacy tools.",
};

export default async function SettingsPage({ searchParams }: { searchParams: Promise<{ market?: string }> }) {
  await requireSessionUser();
  const [globalSettings, query] = await Promise.all([readGlobalSettings(), searchParams]);
  return <SettingsWorkspace initialMarket={parseMarket(query.market)} globalSettings={globalSettings} />;
}
