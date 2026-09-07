export const dynamic = "force-dynamic";

const MAX_BODY_BYTES = 65_536;
const UPSTREAM_TIMEOUT_MS = 60_000;

function serviceUrl(): string | null {
  const value = process.env.BACKTEST_SERVICE_URL?.trim();
  return value ? value.replace(/\/$/, "") : null;
}

export async function POST(request: Request): Promise<Response> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") {
    return Response.json({ detail: "MCP requests must use application/json", code: "CONTENT_TYPE_REQUIRED" }, { status: 415 });
  }
  const authorization = request.headers.get("authorization")?.trim();
  if (!authorization?.toLowerCase().startsWith("bearer ")) {
    return Response.json({ detail: "A scoped Bearer token is required", code: "AUTHENTICATION_REQUIRED" }, { status: 401 });
  }
  const body = await request.text();
  if (!body || new TextEncoder().encode(body).byteLength > MAX_BODY_BYTES) {
    return Response.json({ detail: "MCP request is empty or too large", code: "PAYLOAD_TOO_LARGE" }, { status: 413 });
  }
  const service = serviceUrl();
  if (!service) return Response.json({ detail: "OpenDelta service is not configured", code: "UNAVAILABLE" }, { status: 503 });
  const requestId = request.headers.get("x-request-id")?.trim();
  try {
    const upstream = await fetch(`${service}/mcp`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization,
        ...(requestId && requestId.length <= 128 ? { "x-request-id": requestId } : {}),
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
        "x-request-id": upstream.headers.get("x-request-id") ?? "",
      },
    });
  } catch {
    return Response.json({ detail: "OpenDelta MCP is temporarily unavailable", code: "UNAVAILABLE" }, { status: 502 });
  }
}
