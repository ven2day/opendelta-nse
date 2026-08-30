import type { Metadata } from "next";
import { requireSessionUser } from "../server-auth";
import { ResearchLab } from "./research-lab";
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Research Lab", description: "Educational factor catalogue and quant research methods." };
export default async function ResearchPage() { await requireSessionUser(); return <ResearchLab tab="LEARN" />; }
