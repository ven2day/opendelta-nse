export type PlatformMarket = "NSE" | "CRYPTO";

export type ApiError = { detail?: string };

/** Reads the `?market=` query value; anything other than CRYPTO falls back to NSE. */
export function parseMarket(value: string | null | undefined): PlatformMarket {
  return value?.toUpperCase() === "CRYPTO" ? "CRYPTO" : "NSE";
}

export async function platformGet<T>(action: string, parameters?: Record<string, string>): Promise<T> {
  const query = new URLSearchParams({ action, ...parameters });
  const response = await fetch(`/api/platform?${query.toString()}`, { cache: "no-store" });
  const payload = await response.json() as T & ApiError;
  if (!response.ok) throw new Error(payload.detail || "The platform request failed");
  return payload;
}

export async function platformPost<T>(action: string, payload: unknown, idempotencyKey?: string): Promise<T> {
  const response = await fetch(`/api/platform?action=${encodeURIComponent(action)}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-idempotency-key": idempotencyKey ?? crypto.randomUUID(),
    },
    body: JSON.stringify(payload),
  });
  const result = await response.json() as T & ApiError;
  if (!response.ok) throw new Error(result.detail || "The platform operation failed");
  return result;
}

export async function cancelPlatformJob<T>(jobId: string): Promise<T> {
  const response = await fetch(`/api/platform?jobId=${encodeURIComponent(jobId)}`, { method: "DELETE" });
  const result = await response.json() as T & ApiError;
  if (!response.ok) throw new Error(result.detail || "The job could not be cancelled");
  return result;
}
