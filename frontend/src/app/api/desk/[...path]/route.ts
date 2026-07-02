import { type NextRequest } from "next/server";

// BFF proxy: the browser talks to same-origin /api/desk/* and never needs to know the FastAPI host.
// This handler forwards method, path, query, and body to the backend at API_BASE (server-only env,
// set by the deploy image — the Dockerfile points it at FastAPI's internal port). Status and body
// pass through unchanged so the typed client (desk/api/client.ts) keeps its existing error semantics;
// an unreachable or unconfigured backend becomes a 502, which the client's poll-failure counter reads
// as "backend unreachable".
const API_BASE = process.env.API_BASE;

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxy(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  // Next 15+ makes route-handler params async.
  const { path: segments = [] } = await ctx.params;
  if (!API_BASE) {
    return Response.json({ detail: "Desk API is not configured (API_BASE unset)." }, { status: 502 });
  }
  // Re-encode each decoded segment so spaces/specials survive to FastAPI (e.g. /library/<doc path>).
  const path = "/" + segments.map(encodeURIComponent).join("/");
  const target = `${API_BASE}${path}${req.nextUrl.search}`;

  const headers: Record<string, string> = {};
  const contentType = req.headers.get("content-type");
  if (contentType) headers["content-type"] = contentType;

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  const init: RequestInit = {
    method: req.method,
    headers,
    ...(hasBody ? { body: await req.text() } : {}),
  };

  let res: Response;
  try {
    res = await fetch(target, init);
  } catch (err) {
    return Response.json(
      { detail: `Desk API unreachable at ${API_BASE}: ${(err as Error).message}` },
      { status: 502 },
    );
  }

  const body = await res.arrayBuffer();
  const out = new Headers();
  const ct = res.headers.get("content-type");
  if (ct) out.set("content-type", ct);
  return new Response(body, { status: res.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
