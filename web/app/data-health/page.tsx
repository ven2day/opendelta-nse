import type { Metadata } from "next";
import { requireSessionUser } from "../server-auth";
import { DataHealthWorkspace } from "./data-health-workspace";
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Data Health", description: "Provider, freshness and data-quality status." };
export default async function DataHealthPage() { await requireSessionUser(); return <DataHealthWorkspace />; }
