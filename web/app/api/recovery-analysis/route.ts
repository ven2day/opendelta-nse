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

export async function POST(request: Request): Promise<Response> {
  if (!(await authorized(request))) {
    return Response.json({ detail: "Authentication required" }, { status: 401 });
  }
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return Response.json({ detail: "Request body must be valid JSON" }, { status: 400 });
  }

  try {
    const headers: Record<string, string> = { "content-type": "application/json" };
    const proxyToken = process.env.BACKTEST_PROXY_TOKEN?.trim();
    if (proxyToken) headers[PROXY_TOKEN_HEADER] = proxyToken;
    const upstream = await fetch(`${service}/recovery-analysis`, {
      method: "POST",
      headers,
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
    return Response.json({ detail: "Feature analysis is temporarily unavailable" }, { status: 502 });
  }
}

export async function GET(request: Request): Promise<Response> {
  if (!(await authorized(request))) {
    return Response.json({ detail: "Authentication required" }, { status: 401 });
  }
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  const filename = new URL(request.url).searchParams.get("filename") ?? "";
  try {
    const requestHeaders: Record<string, string> = {};
    const proxyToken = process.env.BACKTEST_PROXY_TOKEN?.trim();
    if (proxyToken) requestHeaders[PROXY_TOKEN_HEADER] = proxyToken;
    const upstream = await fetch(
      `${service}/recovery-analysis/report?filename=${encodeURIComponent(filename)}`,
      { headers: requestHeaders, cache: "no-store", signal: AbortSignal.timeout(60_000) },
    );
    const responseHeaders = new Headers();
    responseHeaders.set("content-type", upstream.headers.get("content-type") ?? "application/octet-stream");
    responseHeaders.set("cache-control", "private, no-store");
    const disposition = upstream.headers.get("content-disposition");
    if (disposition) responseHeaders.set("content-disposition", disposition);
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return Response.json({ detail: "Feature report download is temporarily unavailable" }, { status: 502 });
  }
}
