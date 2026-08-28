import type { Metadata } from "next";
import { readGlobalSettings } from "../global-settings-server";
import { requireSessionUser } from "../server-auth";
import { AdminSettings } from "./admin-settings";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Admin settings · OpenDelta",
  description: "Manage application-wide OpenDelta display and symbol-universe settings.",
};

export default async function AdminPage() {
  const userName = await requireSessionUser();
  const settings = await readGlobalSettings();
  return <AdminSettings initialSettings={settings} userName={userName} signOutHref="/api/logout" />;
}
