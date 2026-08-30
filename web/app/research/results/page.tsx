import type { Metadata } from "next";
import { requireSessionUser } from "../../server-auth";
import { ResearchLab } from "../research-lab";
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Research Results", description: "Versioned experiment results and stability metrics." };
export default async function ResearchResultsPage() { await requireSessionUser(); return <ResearchLab tab="RESULTS" />; }
