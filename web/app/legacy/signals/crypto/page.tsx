import type { Metadata } from "next";
import { requireSessionUser } from "../../server-auth";
import { CryptoWorkspace } from "../../crypto/crypto-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Crypto Signals | OpenDelta",
  description: "Monitor paper-only completed-candle crypto and metals signals from OKX or VALR public market data.",
};

export default async function CryptoSignalsPage() {
  const username = await requireSessionUser();
  return <CryptoWorkspace mode="signals" userName={username} signOutHref="/api/logout" />;
}
