import type { Metadata } from "next";
import { requireSessionUser } from "../server-auth";
import { readGlobalSettings } from "../global-settings-server";
import { LiveUniverse } from "./live-universe";
import { SignalsWorkspace } from "./signals-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Signals",
  description: "Discover completed-candle OpenDelta RSI Recovery signals and record manual paper decisions.",
};

export default async function SignalsPage({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  const username = await requireSessionUser();
  const globalSettings = await readGlobalSettings();
  const query = await searchParams;
  return query.view === "universe"
    ? <LiveUniverse userName={username} signOutHref="/api/logout" />
    : <SignalsWorkspace userName={username} signOutHref="/api/logout" initialGlobalPriceRange={globalSettings.priceRange} />;
}
