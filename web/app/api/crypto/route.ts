import { getSessionUser } from "../../server-auth";

export const dynamic = "force-dynamic";

const PROXY_TOKEN_HEADER = "x-opendelta-proxy-token";

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
  const token = process.env.BACKTEST_PROXY_TOKEN?.trim();
  if (token) headers[PROXY_TOKEN_HEADER] = token;
  return headers;
}

function responseFrom(upstream: Response, body: string): Response {
  return new Response(body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "cache-control": "private, no-store",
    },
  });
}

export async function GET(request: Request): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });

  const url = new URL(request.url);
  const action = url.searchParams.get("action") ?? "instruments";
  const endpoints: Record<string, string> = {
    providers: "/crypto/providers",
    instruments: "/crypto/instruments",
    signals: `/crypto/signals?limit=${encodeURIComponent(url.searchParams.get("limit") ?? "200")}`,
    status: "/crypto/signals/status",
  };
  let endpoint = endpoints[action];
  if (action === "catalog") {
    const params = new URLSearchParams();
    params.set("provider", url.searchParams.get("provider") ?? "OKX");
    params.set("query", url.searchParams.get("query") ?? "");
    params.set("limit", url.searchParams.get("limit") ?? "100");
    const instrumentType = url.searchParams.get("instrumentType");
    if (instrumentType) params.set("instrumentType", instrumentType);
    endpoint = `/crypto/catalog?${params.toString()}`;
  }
  if (!endpoint) return Response.json({ detail: "Unknown crypto action" }, { status: 404 });

  try {
    const upstream = await fetch(`${service}${endpoint}`, {
      headers: proxyHeaders(),
      cache: "no-store",
      signal: AbortSignal.timeout(120_000),
    });
    return responseFrom(upstream, await upstream.text());
  } catch {
    return Response.json({ detail: "Crypto market-data service is temporarily unavailable" }, { status: 502 });
  }
}

export async function POST(request: Request): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const url = new URL(request.url);
  const action = url.searchParams.get("action") ?? "";
  const endpoint = action === "add"
    ? "/crypto/instruments"
    : action === "backtest"
      ? "/crypto/backtest"
      : action === "scan"
        ? `/crypto/signals/scan?timeframe=${encodeURIComponent(url.searchParams.get("timeframe") ?? "5m")}`
        : null;
  if (!endpoint) return Response.json({ detail: "Unknown crypto action" }, { status: 404 });

  let payload: unknown = {};
  try {
    const text = await request.text();
    payload = text ? JSON.parse(text) : {};
  } catch {
    return Response.json({ detail: "Request body must be valid JSON" }, { status: 400 });
  }
  try {
    const upstream = await fetch(`${service}${endpoint}`, {
      method: "POST",
      headers: proxyHeaders(true),
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: AbortSignal.timeout(action === "backtest" ? 300_000 : 120_000),
    });
    return responseFrom(upstream, await upstream.text());
  } catch {
    return Response.json({ detail: "Crypto operation could not reach the market-data service" }, { status: 502 });
  }
}

export async function DELETE(request: Request): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const instrumentId = new URL(request.url).searchParams.get("instrumentId")?.trim();
  if (!instrumentId) return Response.json({ detail: "instrumentId is required" }, { status: 400 });
  try {
    const upstream = await fetch(`${service}/crypto/instruments/${encodeURIComponent(instrumentId)}`, {
      method: "DELETE",
      headers: proxyHeaders(),
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
    return responseFrom(upstream, await upstream.text());
  } catch {
    return Response.json({ detail: "Crypto instrument could not be removed" }, { status: 502 });
  }
}
