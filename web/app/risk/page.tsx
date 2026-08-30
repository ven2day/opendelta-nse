import type { Metadata } from "next";
import { requireSessionUser } from "../server-auth";
import { RiskWorkspace } from "./risk-workspace";
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Risk", description: "OpenDelta portfolio and research risk controls." };
export default async function RiskPage() { await requireSessionUser(); return <RiskWorkspace />; }
