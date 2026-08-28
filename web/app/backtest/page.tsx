import type { Metadata } from "next";
import data from "../data/nse-data.json";
import { requireSessionUser } from "../server-auth";
import { isPriceInGlobalRange } from "../global-settings-shared";
import { readGlobalSettings } from "../global-settings-server";
import { BacktestDashboard } from "./backtest-dashboard";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "RSI Backtest",
  description: "Backtest RSI entry and exit ranges across NSE symbols.",
};

export default async function BacktestPage() {
  const username = await requireSessionUser();
  const globalSettings = await readGlobalSettings();
  const symbols = data.rows
    .filter((row) => isPriceInGlobalRange(row.entry_price, globalSettings.priceRange))
    .map((row) => row.symbol)
    .sort((left, right) => left.localeCompare(right));

  return (
    <BacktestDashboard
      symbols={symbols}
      userName={username}
      signOutHref="/api/logout"
      globalPriceRange={globalSettings.priceRange}
    />
  );
}
