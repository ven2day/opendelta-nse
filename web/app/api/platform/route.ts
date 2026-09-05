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

function proxyHeaders(request?: Request, includeContentType = false): Record<string, string> {
  const headers: Record<string, string> = {};
  if (includeContentType) headers["content-type"] = "application/json";
  const token = process.env.BACKTEST_PROXY_TOKEN?.trim();
  if (token) headers[PROXY_TOKEN_HEADER] = token;
  const idempotencyKey = request?.headers.get("x-idempotency-key")?.trim();
  if (idempotencyKey && idempotencyKey.length <= 200) headers["x-idempotency-key"] = idempotencyKey;
  return headers;
}

function responseFrom(upstream: Response, body: string): Response {
  return new Response(body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "cache-control": "private, no-store",
      "x-request-id": upstream.headers.get("x-request-id") ?? "",
    },
  });
}

export async function GET(request: Request): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const url = new URL(request.url);
  const action = url.searchParams.get("action") ?? "overview";
  const market = url.searchParams.get("market");
  const endpoints: Record<string, string> = {
    overview: `/platform/overview?market=${encodeURIComponent(market ?? "NSE")}`,
    instruments: `/platform/instruments?market=${encodeURIComponent(market ?? "NSE")}&offset=${encodeURIComponent(url.searchParams.get("offset") ?? "0")}&limit=${encodeURIComponent(url.searchParams.get("limit") ?? "100")}`,
    "market-context": `/platform/market-context?market=${encodeURIComponent(market ?? "NSE")}`,
  };
  const endpoint = endpoints[action];
  if (!endpoint) return Response.json({ detail: "Unknown platform action" }, { status: 404 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  try {
    const upstream = await fetch(`${service}${endpoint}`, {
      headers: proxyHeaders(),
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
    return responseFrom(upstream, await upstream.text());
  } catch {
    return Response.json({ detail: "Quant platform service is temporarily unavailable" }, { status: 502 });
  }
}
