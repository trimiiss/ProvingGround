import { type NextRequest, NextResponse } from "next/server";
import { checkPassword, COOKIE, createSession, MAX_AGE_S } from "@/lib/session";

const MAX_FAILURES = 10;
const WINDOW_MS = 15 * 60 * 1000;

/** Failed attempts per client. In memory: it resets on restart, which is fine for slowing down guessing. */
const failures = new Map<string, { count: number; until: number }>();

// The last hop is the one added by the nearest proxy, so a client can't fake it behind one.
const clientOf = (req: NextRequest) => req.headers.get("x-forwarded-for")?.split(",").at(-1)?.trim() ?? "unknown";

function blocked(client: string): boolean {
  const entry = failures.get(client);
  if (entry && entry.until <= Date.now()) failures.delete(client);
  return (failures.get(client)?.count ?? 0) >= MAX_FAILURES;
}

function recordFailure(client: string) {
  for (const [key, entry] of failures) if (entry.until <= Date.now()) failures.delete(key);
  const count = (failures.get(client)?.count ?? 0) + 1;
  failures.set(client, { count, until: Date.now() + WINDOW_MS });
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const client = clientOf(req);
  if (blocked(client)) {
    return NextResponse.json({ detail: "Too many failed attempts. Try again in 15 minutes." }, { status: 429 });
  }
  const body: unknown = await req.json().catch(() => null);
  const { username, password } = (body ?? {}) as { username?: unknown; password?: unknown };
  if (typeof username !== "string" || typeof password !== "string" || !checkPassword(username, password)) {
    recordFailure(client);
    return NextResponse.json({ detail: "Wrong username or password." }, { status: 401 });
  }
  failures.delete(client);
  const res = NextResponse.json({ ok: true });
  res.cookies.set(COOKIE, createSession(username), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: MAX_AGE_S,
    // A browser drops a Secure cookie on plain http, which is how the local stack runs.
    secure: req.headers.get("x-forwarded-proto") === "https",
  });
  return res;
}
