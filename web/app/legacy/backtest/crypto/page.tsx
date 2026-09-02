import type { Metadata } from "next";
import { requireSessionUser } from "../../../server-auth";
import { CryptoWorkspace } from "../../crypto/crypto-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Crypto Backtest | OpenDelta",
  description: "Backtest completed-candle crypto and metals strategies with OKX or VALR public market data.",
};

export default async function CryptoBacktestPage() {
  const username = await requireSessionUser();
  return <CryptoWorkspace mode="backtest" userName={username} signOutHref="/api/logout" />;
}
