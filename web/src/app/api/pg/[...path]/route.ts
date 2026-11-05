import { type NextRequest, NextResponse } from "next/server";
import { COOKIE, readSession } from "@/lib/session";

/**
 * Authenticated proxy for browser-side calls (launching runs, polling jobs, adding cases).
 * The admin API key is attached here, on the server, so it never reaches the browser.
 * Only `/v1/*` is forwarded, and only for a signed-in user: this route acts as the admin.
 */

const BASE = (process.env.PGROUND_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const KEY = process.env.PGROUND_API_KEY ?? "dev-admin-key";

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }): Promise<NextResponse> {
  if (!readSession(req.cookies.get(COOKIE)?.value)) return NextResponse.json({ detail: "sign in first" }, { status: 401 });
  const { path } = await ctx.params;
  if (path[0] !== "v1") return NextResponse.json({ detail: "only /v1 is proxied" }, { status: 404 });
  const url = `${BASE}/${path.join("/")}${req.nextUrl.search}`;
  const hasBody = !["GET", "HEAD", "DELETE"].includes(req.method);
  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: req.method,
      headers: { "x-api-key": KEY, ...(hasBody ? { "content-type": req.headers.get("content-type") ?? "application/json" } : {}) },
      body: hasBody ? await req.text() : undefined,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: `cannot reach the API at ${BASE}` }, { status: 503 });
  }
  const headers = new Headers();
  const type = upstream.headers.get("content-type");
  if (type) headers.set("content-type", type);
  const disposition = upstream.headers.get("content-disposition");
  if (disposition) headers.set("content-disposition", disposition);
  return new NextResponse(upstream.status === 204 ? null : await upstream.arrayBuffer(), { status: upstream.status, headers });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const DELETE = forward;
