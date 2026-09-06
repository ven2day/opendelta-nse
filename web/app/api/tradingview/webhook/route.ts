export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 32_768;
const UPSTREAM_TIMEOUT_MS = 2_500;

function serviceUrl(): string | null {
  const value = process.env.BACKTEST_SERVICE_URL?.trim();
  return value ? value.replace(/\/$/, "") : null;
}

/** Public ingress for TradingView. The JSON webhook key is validated upstream. */
export async function POST(request: Request): Promise<Response> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") {
    return Response.json({ detail: "TradingView webhook body must be JSON" }, { status: 415 });
  }
  const body = await request.text();
  if (!body || new TextEncoder().encode(body).byteLength > MAX_BODY_BYTES) {
    return Response.json({ detail: "TradingView webhook body is empty or too large" }, { status: 413 });
  }
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "Quant platform service is not configured" }, { status: 503 });
  const proxyToken = process.env.BACKTEST_PROXY_TOKEN?.trim();
  try {
    const upstream = await fetch(`${service}/v2/integrations/tradingview/webhook`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(proxyToken ? { "x-opendelta-proxy-token": proxyToken } : {}),
      },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "private, no-store",
      },
    });
  } catch {
    return Response.json({ detail: "TradingView ingestion is temporarily unavailable" }, { status: 502 });
  }
}
