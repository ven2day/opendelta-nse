import "server-only";

import {
  DEFAULT_GLOBAL_PRICE_RANGE,
  parseGlobalSettings,
  type GlobalSettingsPayload,
} from "./global-settings-shared";

export async function readGlobalSettings(): Promise<GlobalSettingsPayload> {
  const service = process.env.BACKTEST_SERVICE_URL?.trim().replace(/\/$/, "");
  if (!service) {
    return { schemaVersion: 1, priceRange: DEFAULT_GLOBAL_PRICE_RANGE, updatedAt: null };
  }
  const headers: Record<string, string> = {};
  const proxyToken = process.env.BACKTEST_PROXY_TOKEN?.trim();
  if (proxyToken) headers["x-opendelta-proxy-token"] = proxyToken;
  try {
    const response = await fetch(`${service}/application-settings`, {
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) throw new Error("Application settings unavailable");
    return parseGlobalSettings(await response.json());
  } catch {
    return { schemaVersion: 1, priceRange: DEFAULT_GLOBAL_PRICE_RANGE, updatedAt: null };
  }
}
