import { getSessionUser } from "../../../server-auth";

export const dynamic = "force-dynamic";

const PROXY_TOKEN_HEADER = "x-opendelta-proxy-token";
const UPSTREAM_TIMEOUT_MS = 30_000;
const SEGMENT_PATTERN = /^[A-Za-z0-9_.%:@-]+$/;

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

function decodedSegment(segment: string): string | null {
  try {
    return decodeURIComponent(segment);
  } catch {
    return null;
  }
}

/** Rejects empty, dot and encoded-separator segments so the upstream path can never escape `/v2/`. */
function safeSegment(segment: string): boolean {
  if (!segment || !SEGMENT_PATTERN.test(segment)) return false;
  const decoded = decodedSegment(segment);
  return Boolean(decoded) && decoded !== "." && decoded !== ".." && !/[/\\]/.test(decoded ?? "");
}

/** Maps `/api/v2/<path>?<query>` onto the upstream `/v2/<path>?<query>` without allowing traversal. */
function upstreamPath(request: Request): string | null {
  const url = new URL(request.url);
  const match = url.pathname.match(/^\/api\/v2\/(.+)$/);
  if (!match) return null;
  const segments = match[1].split("/");
  if (!segments.every(safeSegment)) return null;
  return `/v2/${segments.join("/")}${url.search}`;
}

async function forward(request: Request, method: "GET" | "POST" | "DELETE"): Promise<Response> {
  if (!(await authorized(request))) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const path = upstreamPath(request);
  if (!path) return Response.json({ detail: "Unknown platform path" }, { status: 404 });
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const hasBody = method === "POST";
  const body = hasBody ? await request.text() : undefined;
  try {
    const upstream = await fetch(`${service}${path}`, {
      method,
      headers: proxyHeaders(request, hasBody),
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    return responseFrom(upstream, await upstream.text());
  } catch {
    return Response.json({ detail: "Quant platform service is temporarily unavailable" }, { status: 502 });
  }
}

export async function GET(request: Request): Promise<Response> {
  return forward(request, "GET");
}

export async function POST(request: Request): Promise<Response> {
  return forward(request, "POST");
}

export async function DELETE(request: Request): Promise<Response> {
  return forward(request, "DELETE");
}
