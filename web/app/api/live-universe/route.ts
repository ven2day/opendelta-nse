import { getSessionUser } from "../../server-auth";

export const dynamic = "force-dynamic";

const PROXY_TOKEN_HEADER = "x-opendelta-proxy-token";
const GET_ACTIONS: Record<string, string> = {
  config: "/live-universe/config",
  active: "/live-universe/active",
  history: "/live-universe/history",
  symbols: "/live-universe/symbols",
  export: "/live-universe/export",
};
const POST_ACTIONS: Record<string, string> = {
  preview: "/live-universe/preview",
  rebuild: "/live-universe/rebuild",
  save: "/live-universe/save",
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

export async function GET(request: Request): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const url = new URL(request.url);
  const action = url.searchParams.get("action") ?? "config";
  const endpoint = GET_ACTIONS[action];
  if (!endpoint) return Response.json({ detail: "Unknown live-universe action" }, { status: 404 });
  const version = action === "export" ? `?version=${encodeURIComponent(url.searchParams.get("version") ?? "active")}` : "";
  try {
    const upstream = await fetch(`${service}${endpoint}${version}`, {
      headers: proxyHeaders(),
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
    const headers = new Headers({
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "cache-control": "private, no-store",
    });
    const disposition = upstream.headers.get("content-disposition");
    if (disposition) headers.set("content-disposition", disposition);
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch {
    return Response.json({ detail: "Live-universe service is temporarily unavailable" }, { status: 502 });
  }
}

export async function POST(request: Request): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const action = new URL(request.url).searchParams.get("action") ?? "preview";
  const endpoint = POST_ACTIONS[action];
  if (!endpoint) return Response.json({ detail: "Unknown live-universe action" }, { status: 404 });
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return Response.json({ detail: "Request body must be valid JSON" }, { status: 400 });
  }
  try {
    const upstream = await fetch(`${service}${endpoint}`, {
      method: "POST",
      headers: proxyHeaders(true),
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: AbortSignal.timeout(5 * 60 * 1_000),
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "private, no-store",
      },
    });
  } catch {
    return Response.json({ detail: "Live-universe service is temporarily unavailable" }, { status: 502 });
  }
}
