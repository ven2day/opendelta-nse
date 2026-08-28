import { getSessionUser } from "../../../../server-auth";

export const dynamic = "force-dynamic";

async function proxy(jobId: string, method: "GET" | "DELETE"): Promise<Response> {
  if (!(await getSessionUser())) {
    return Response.json({ detail: "Authentication required" }, { status: 401 });
  }
  const serviceUrl = process.env.BACKTEST_SERVICE_URL?.trim();
  if (!serviceUrl) {
    return Response.json({ detail: "Backtest service is not configured" }, { status: 503 });
  }
  const headers: Record<string, string> = {};
  const proxyToken = process.env.BACKTEST_PROXY_TOKEN?.trim();
  if (proxyToken) headers["x-opendelta-proxy-token"] = proxyToken;
  try {
    const upstream = await fetch(
      `${serviceUrl.replace(/\/$/, "")}/backtest/jobs/${encodeURIComponent(jobId)}`,
      { method, headers, cache: "no-store", signal: AbortSignal.timeout(15_000) },
    );
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "private, no-store",
      },
    });
  } catch {
    return Response.json({ detail: "Backtest job service is temporarily unavailable" }, { status: 502 });
  }
}

export async function GET(
  _request: Request,
  context: { params: Promise<{ jobId: string }> },
): Promise<Response> {
  return proxy((await context.params).jobId, "GET");
}

export async function DELETE(
  _request: Request,
  context: { params: Promise<{ jobId: string }> },
): Promise<Response> {
  return proxy((await context.params).jobId, "DELETE");
}
