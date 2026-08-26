import { getSessionUser } from "../../server-auth";

export const dynamic = "force-dynamic";

const PROXY_TOKEN_HEADER = "x-opendelta-proxy-token";
const GET_ACTIONS: Record<string, string> = {
  signals: "/live-signals",
  status: "/live-signals/status",
  settings: "/live-signals/settings",
  paper: "/paper-trades",
  study: "/live-signals/study",
};

async function authorized(request: Request): Promise<boolean> {
  const expected = process.env.BACKTEST_PROXY_TOKEN?.trim();
  const supplied = request.headers.get(PROXY_TOKEN_HEADER)?.trim();
  return Boolean(expected && supplied && supplied === expected) || Boolean(await getSessionUser());
}

function serviceUrl(): string | null {
  const value = process.env.BACKTEST_SERVICE_URL?.trim();
  return value ? value.replace(/\/$/, "") : null;
}

function proxyHeaders(includeContentType = false): Record<string, string> {
  const headers: Record<string, string> = {};
  if (includeContentType) headers["content-type"] = "application/json";
  const proxyToken = process.env.BACKTEST_PROXY_TOKEN?.trim();
  if (proxyToken) headers[PROXY_TOKEN_HEADER] = proxyToken;
  return headers;
}

function endpointForMutation(action: string, url: URL): { endpoint: string; method: "POST" | "PUT" } | null {
  if (action === "settings") return { endpoint: "/live-signals/settings", method: "PUT" };
  const signalId = url.searchParams.get("signalId");
  const paperTradeId = url.searchParams.get("paperTradeId");
  if (action === "decision" && signalId) return { endpoint: `/live-signals/${encodeURIComponent(signalId)}/decision`, method: "POST" };
  if (action === "paper-buy" && signalId) return { endpoint: `/live-signals/${encodeURIComponent(signalId)}/paper-buy`, method: "POST" };
  if (action === "close" && paperTradeId) return { endpoint: `/paper-trades/${encodeURIComponent(paperTradeId)}/close`, method: "POST" };
  return null;
}

export async function GET(request: Request): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const url = new URL(request.url);
  const action = url.searchParams.get("action") ?? "signals";
  const endpoint = GET_ACTIONS[action];
  if (!endpoint) return Response.json({ detail: "Unknown live-signals action" }, { status: 404 });
  const manualAction = action === "signals" ? url.searchParams.get("manualAction") : null;
  const suffix = manualAction ? `?action=${encodeURIComponent(manualAction)}` : "";
  try {
    const upstream = await fetch(`${service}${endpoint}${suffix}`, {
      headers: proxyHeaders(),
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "private, no-store",
      },
    });
  } catch {
    return Response.json({ detail: "Live-signal service is temporarily unavailable" }, { status: 502 });
  }
}

export async function POST(request: Request): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const url = new URL(request.url);
  const target = endpointForMutation(url.searchParams.get("action") ?? "", url);
  if (!target) return Response.json({ detail: "Unknown live-signals action or missing record id" }, { status: 404 });
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return Response.json({ detail: "Request body must be valid JSON" }, { status: 400 });
  }
  try {
    const upstream = await fetch(`${service}${target.endpoint}`, {
      method: target.method,
      headers: proxyHeaders(true),
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "private, no-store",
      },
    });
  } catch {
    return Response.json({ detail: "Live-signal service is temporarily unavailable" }, { status: 502 });
  }
}
