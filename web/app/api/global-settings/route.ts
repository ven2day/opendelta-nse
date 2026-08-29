import { getSessionUser } from "../../server-auth";

export const dynamic = "force-dynamic";

function upstreamHeaders(json = false): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = process.env.BACKTEST_PROXY_TOKEN?.trim();
  if (token) headers["x-opendelta-proxy-token"] = token;
  if (json) headers["content-type"] = "application/json";
  return headers;
}

async function proxy(request: Request, method: "GET" | "PUT"): Promise<Response> {
  if (!(await getSessionUser())) return Response.json({ detail: "Authentication required" }, { status: 401 });
  const service = process.env.BACKTEST_SERVICE_URL?.trim().replace(/\/$/, "");
  if (!service) return Response.json({ detail: "Settings service is not configured" }, { status: 503 });
  let body: string | undefined;
  if (method === "PUT") {
    try { body = JSON.stringify(await request.json()); }
    catch { return Response.json({ detail: "Enter a valid price range" }, { status: 400 }); }
  }
  try {
    const upstream = await fetch(`${service}/application-settings`, {
      method,
      headers: upstreamHeaders(method === "PUT"),
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "private, no-store",
      },
    });
  } catch {
    return Response.json({ detail: "Settings service is temporarily unavailable" }, { status: 502 });
  }
}

export function GET(request: Request) { return proxy(request, "GET"); }
export function PUT(request: Request) { return proxy(request, "PUT"); }
