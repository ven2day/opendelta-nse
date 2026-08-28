import { getSessionUser } from "../../server-auth";

export const dynamic = "force-dynamic";

const PROXY_TOKEN_HEADER = "x-opendelta-proxy-token";

async function authorized(request: Request): Promise<boolean> {
  const expected = process.env.BACKTEST_PROXY_TOKEN?.trim();
  const supplied = request.headers.get(PROXY_TOKEN_HEADER)?.trim();
  return Boolean(expected && supplied && supplied === expected) || Boolean(await getSessionUser());
}

export async function POST(request: Request): Promise<Response> {
  if (!(await authorized(request))) {
    return Response.json({ detail: "Authentication required" }, { status: 401 });
  }

  const service = process.env.BACKTEST_SERVICE_URL?.trim().replace(/\/$/, "");
  if (!service) {
    return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ detail: "Enter a valid NSE symbol" }, { status: 400 });
  }

  const headers: Record<string, string> = { "content-type": "application/json" };
  const proxyToken = process.env.BACKTEST_PROXY_TOKEN?.trim();
  if (proxyToken) headers[PROXY_TOKEN_HEADER] = proxyToken;

  try {
    const upstream = await fetch(`${service}/market-data/symbols`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
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
    return Response.json({ detail: "The symbol service is temporarily unavailable" }, { status: 502 });
  }
}
