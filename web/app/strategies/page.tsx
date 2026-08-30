import type { Metadata } from "next";
import { requireSessionUser } from "../server-auth";
import { StrategyCatalog } from "./strategy-catalog";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Strategies", description: "Versioned OpenDelta strategy definitions." };
export default async function StrategiesPage() { await requireSessionUser(); return <StrategyCatalog />; }
