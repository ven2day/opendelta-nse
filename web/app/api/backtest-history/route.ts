import { getSessionUser } from "../../server-auth";

export const dynamic = "force-dynamic";

const PROXY_TOKEN_HEADER = "x-opendelta-proxy-token";
const OWNER_HEADER = "x-opendelta-history-owner";
const MAX_BODY_BYTES = 100 * 1024 * 1024;
const RUN_ID_PATTERN = /^[A-Za-z0-9._-]{1,120}$/;

async function ownerKey(username: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(username.trim().toLocaleLowerCase()),
  );
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

async function context(): Promise<
  | { service: string; headers: Record<string, string> }
  | Response
> {
  const username = await getSessionUser();
  if (!username) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = process.env.BACKTEST_SERVICE_URL?.trim();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const headers: Record<string, string> = { [OWNER_HEADER]: await ownerKey(username) };
  const proxyToken = process.env.BACKTEST_PROXY_TOKEN?.trim();
  if (proxyToken) headers[PROXY_TOKEN_HEADER] = proxyToken;
  return { service: service.replace(/\/$/, ""), headers };
}

function requestedRunId(request: Request): string | null | Response {
  const runId = new URL(request.url).searchParams.get("id");
  if (runId !== null && !RUN_ID_PATTERN.test(runId)) {
    return Response.json({ detail: "Backtest run ID is invalid" }, { status: 400 });
  }
  return runId;
}

async function forward(
  upstream: string,
  init: RequestInit,
): Promise<Response> {
  try {
    const response = await fetch(upstream, {
      ...init,
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
    return new Response(await response.arrayBuffer(), {
      status: response.status,
      headers: {
        "content-type": response.headers.get("content-type") ?? "application/json",
        "cache-control": "private, no-store",
      },
    });
  } catch {
    return Response.json(
      { detail: "Backtest history is temporarily unavailable" },
      { status: 502, headers: { "cache-control": "private, no-store" } },
    );
  }
}

export async function GET(request: Request): Promise<Response> {
  const resolved = await context();
  if (resolved instanceof Response) return resolved;
  const runId = requestedRunId(request);
  if (runId instanceof Response) return runId;
  const path = runId === null ? "/backtest-history" : `/backtest-history/${encodeURIComponent(runId)}`;
  return forward(`${resolved.service}${path}`, { method: "GET", headers: resolved.headers });
}

export async function POST(request: Request): Promise<Response> {
  const resolved = await context();
  if (resolved instanceof Response) return resolved;
  const declaredLength = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
    return Response.json({ detail: "Backtest result exceeds the 100 MB storage limit" }, { status: 413 });
  }
  const body = await request.arrayBuffer();
  if (body.byteLength > MAX_BODY_BYTES) {
    return Response.json({ detail: "Backtest result exceeds the 100 MB storage limit" }, { status: 413 });
  }
  return forward(`${resolved.service}/backtest-history`, {
    method: "POST",
    headers: { ...resolved.headers, "content-type": "application/json" },
    body,
  });
}

export async function DELETE(request: Request): Promise<Response> {
  const resolved = await context();
  if (resolved instanceof Response) return resolved;
  const runId = requestedRunId(request);
  if (runId instanceof Response) return runId;
  if (runId === null) return Response.json({ detail: "Backtest run ID is required" }, { status: 400 });
  return forward(`${resolved.service}/backtest-history/${encodeURIComponent(runId)}`, {
    method: "DELETE",
    headers: resolved.headers,
  });
}
