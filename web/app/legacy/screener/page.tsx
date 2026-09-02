import type { Metadata } from "next";
import data from "../../data/nse-data.json";
import { Dashboard, type StockRow } from "./dashboard";
import { readGlobalSettings } from "../../global-settings-server";
import { requireSessionUser } from "../../server-auth";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "NSE RSI Dashboard",
  description: "Search and filter NSE symbols by RSI and traded volume.",
};

export default async function Home() {
  const username = await requireSessionUser();
  const globalSettings = await readGlobalSettings();

  return (
    <Dashboard
      stocks={data.rows as StockRow[]}
      latestSession={data.latestSession}
      generatedAt={data.generatedAt}
      userName={username}
      signOutHref="/api/logout"
      globalPriceRange={globalSettings.priceRange}
    />
  );
}
