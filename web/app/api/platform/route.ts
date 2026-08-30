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

function safeJobId(value: string | null): string | null {
  const candidate = value?.trim() ?? "";
  return /^job-[a-f0-9-]{36}$/.test(candidate) ? candidate : null;
}

export async function GET(request: Request): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const url = new URL(request.url);
  const action = url.searchParams.get("action") ?? "overview";
  const market = url.searchParams.get("market");
  const endpoints: Record<string, string> = {
    overview: "/platform/overview",
    health: "/platform/health/ready",
    factors: `/platform/factors${url.searchParams.get("family") ? `?family=${encodeURIComponent(url.searchParams.get("family") ?? "")}` : ""}`,
    strategies: `/platform/strategies${market ? `?market=${encodeURIComponent(market)}` : ""}`,
    instruments: `/platform/instruments?market=${encodeURIComponent(market ?? "NSE")}&offset=${encodeURIComponent(url.searchParams.get("offset") ?? "0")}&limit=${encodeURIComponent(url.searchParams.get("limit") ?? "100")}`,
    "market-context": `/platform/market-context?market=${encodeURIComponent(market ?? "NSE")}`,
    risk: "/platform/risk",
    "data-health": "/platform/data-health",
    jobs: `/platform/jobs?limit=${encodeURIComponent(url.searchParams.get("limit") ?? "100")}`,
    metrics: "/platform/metrics",
  };
  let endpoint = endpoints[action];
  if (action === "job") {
    const jobId = safeJobId(url.searchParams.get("jobId"));
    if (!jobId) return Response.json({ detail: "A valid jobId is required" }, { status: 400 });
    endpoint = `/platform/jobs/${encodeURIComponent(jobId)}`;
  }
  if (!endpoint) return Response.json({ detail: "Unknown platform action" }, { status: 404 });
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

export async function POST(request: Request): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const action = new URL(request.url).searchParams.get("action") ?? "";
  const endpoint = action === "estimate"
    ? "/platform/research/estimate"
    : action === "experiment"
      ? "/platform/research/experiments"
      : null;
  if (!endpoint) return Response.json({ detail: "Unknown platform operation" }, { status: 404 });
  let body: string;
  try {
    body = JSON.stringify(await request.json());
  } catch {
    return Response.json({ detail: "Request body must be valid JSON" }, { status: 400 });
  }
  try {
    const upstream = await fetch(`${service}${endpoint}`, {
      method: "POST",
      headers: proxyHeaders(request, true),
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
    return responseFrom(upstream, await upstream.text());
  } catch {
    return Response.json({ detail: "Research request could not reach the worker service" }, { status: 502 });
  }
}

export async function DELETE(request: Request): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const jobId = safeJobId(new URL(request.url).searchParams.get("jobId"));
  if (!jobId) return Response.json({ detail: "A valid jobId is required" }, { status: 400 });
  try {
    const upstream = await fetch(`${service}/platform/jobs/${encodeURIComponent(jobId)}`, {
      method: "DELETE",
      headers: proxyHeaders(),
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
    return responseFrom(upstream, await upstream.text());
  } catch {
    return Response.json({ detail: "Job cancellation could not reach the worker service" }, { status: 502 });
  }
}
