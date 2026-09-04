import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { requireSessionUser } from "../server-auth";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Admin settings",
  description: "Application-wide OpenDelta settings live in the unified Settings workspace.",
};

export default async function AdminPage() {
  await requireSessionUser();
  // The standalone admin shell was retired with the legacy pages; the global
  // price range now lives in the unified Settings workspace.
  redirect("/settings");
}