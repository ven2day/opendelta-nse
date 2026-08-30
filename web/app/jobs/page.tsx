import type { Metadata } from "next";
import { requireSessionUser } from "../server-auth";
import { JobsWorkspace } from "./jobs-workspace";
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Jobs", description: "OpenDelta background job queue and worker health." };
export default async function JobsPage() { await requireSessionUser(); return <JobsWorkspace />; }
