import type { Metadata } from "next";
import { requireSessionUser } from "../server-auth";
import { OperationsWorkspace } from "./operations-workspace";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Operations",
  description: "Durable OpenDelta health, workers, alerts, and audit history.",
};

export default async function OperationsPage() {
  await requireSessionUser();
  return <OperationsWorkspace />;
}
