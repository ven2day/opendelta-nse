export type V2Params = Record<string, string | number | boolean | null | undefined>;

/** Error raised by the unified `/api/v2` client; `status` mirrors the upstream HTTP status. */
export class V2Error extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "V2Error";
    this.status = status;
  }
}

/** A 503 from the platform means the unified platform database is not configured. */
export function isPlatformUnconfigured(error: unknown): boolean {
  return error instanceof V2Error && error.status === 503;
}

export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function requestUrl(path: string, params?: V2Params): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const cleanPath = path.replace(/^\/+/, "");
  const search = query.toString();
  return `/api/v2/${cleanPath}${search ? `?${search}` : ""}`;
}

function detailFrom(payload: unknown): string | null {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) return null;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg?: unknown }).msg) : null))
      .filter((item): item is string => Boolean(item));
    if (messages.length) return messages.join("; ");
  }
  return null;
}

async function parse<T>(response: Response, fallback: string): Promise<T> {
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) throw new V2Error(detailFrom(payload) ?? fallback, response.status);
  return payload as T;
}

export async function v2Get<T>(path: string, params?: V2Params): Promise<T> {
  const response = await fetch(requestUrl(path, params), { cache: "no-store" });
  return parse<T>(response, "The platform request failed");
}

export async function v2Post<T>(path: string, body?: unknown, params?: V2Params): Promise<T> {
  const response = await fetch(requestUrl(path, params), {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-idempotency-key": crypto.randomUUID(),
    },
    body: JSON.stringify(body ?? {}),
  });
  return parse<T>(response, "The platform operation failed");
}

export async function v2Delete<T>(path: string, params?: V2Params): Promise<T> {
  const response = await fetch(requestUrl(path, params), { method: "DELETE" });
  return parse<T>(response, "The platform delete request failed");
}
