import { getSessionUser } from "../../server-auth";
import { historyOwnerKey } from "../history-owner";

export const dynamic = "force-dynamic";

const PROXY_TOKEN_HEADER = "x-opendelta-proxy-token";
const OWNER_HEADER = "x-opendelta-history-owner";

function hasValidProxyToken(request: Request): boolean {
  const expectedToken = process.env.BACKTEST_PROXY_TOKEN?.trim();
  const suppliedToken = request.headers.get(PROXY_TOKEN_HEADER)?.trim();
  return Boolean(expectedToken && suppliedToken && suppliedToken === expectedToken);
}

export async function POST(request: Request): Promise<Response> {
  const sessionUser = await getSessionUser();
  if (!hasValidProxyToken(request) && !sessionUser) {
    return Response.json({ detail: "Authentication required" }, { status: 401 });
  }

  const serviceUrl = process.env.BACKTEST_SERVICE_URL?.trim();
  if (!serviceUrl) {
    return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  }

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
    if (sessionUser) headers[OWNER_HEADER] = await historyOwnerKey(sessionUser);

    const action = new URL(request.url).searchParams.get("action");
    const historyStatus = action === "oi-history-status";
    const upstreamPath = historyStatus
      ? "/nifty-oi/history/status"
      : action === "start-job"
      ? "/backtest/jobs"
      : action === "optimize-atr"
      ? "/backtest/optimize-atr"
      : action === "compare-rsi-exits"
        ? "/backtest/compare-rsi-exits"
        : "/backtest";
    const upstream = await fetch(`${serviceUrl.replace(/\/$/, "")}${upstreamPath}`, {
      method: historyStatus ? "GET" : "POST",
      headers,
      body: historyStatus ? undefined : JSON.stringify(payload),
      cache: "no-store",
      signal: AbortSignal.timeout((action === "optimize-atr" || action === "compare-rsi-exits" ? 60 : action === "start-job" ? 1 : 15) * 60 * 1_000),
    });
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "private, no-store",
      },
    });
  } catch {
    return Response.json(
      { detail: "The historical-data service is temporarily unavailable" },
      { status: 502 },
    );
  }
}
