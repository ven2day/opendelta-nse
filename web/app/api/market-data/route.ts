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

function proxyHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const proxyToken = process.env.BACKTEST_PROXY_TOKEN?.trim();
  if (proxyToken) headers[PROXY_TOKEN_HEADER] = proxyToken;
  return headers;
}

async function proxy(request: Request, endpoint: string, method: "GET" | "POST"): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  try {
    const upstream = await fetch(`${service}${endpoint}`, {
      method,
      headers: proxyHeaders(),
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "private, no-store",
      },
    });
  } catch {
    return Response.json({ detail: "Market-data refresh service is temporarily unavailable" }, { status: 502 });
  }
}

export async function GET(request: Request): Promise<Response> {
  const format = new URL(request.url).searchParams.get("format");
  return proxy(request, format === "csv" ? "/market-data/csv" : "/market-data/status", "GET");
}

export async function POST(request: Request): Promise<Response> {
  return proxy(request, "/market-data/refresh", "POST");
}
