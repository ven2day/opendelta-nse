import type { Metadata } from "next";
import { requireSessionUser } from "../../server-auth";
import { ResearchLab } from "../research-lab";
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Research Experiments", description: "Bounded factor experiments with untouched test periods." };
export default async function ResearchExperimentsPage() { await requireSessionUser(); return <ResearchLab tab="EXPERIMENTS" />; }
