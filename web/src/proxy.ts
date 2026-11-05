import { type NextRequest, NextResponse } from "next/server";
import { COOKIE, readSession } from "@/lib/session";

/** Everything needs a signed-in user except the login page itself and the container health check. */

const PUBLIC = new Set(["/api/auth/login", "/api/health"]);

export function proxy(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  if (PUBLIC.has(pathname)) return NextResponse.next();

  const user = readSession(req.cookies.get(COOKIE)?.value);
  if (pathname === "/login") return user ? NextResponse.redirect(new URL("/", req.url)) : NextResponse.next();
  if (user) return NextResponse.next();

  if (pathname.startsWith("/api/")) return NextResponse.json({ detail: "sign in first" }, { status: 401 });
  const login = new URL("/login", req.url);
  login.searchParams.set("next", pathname + search);
  return NextResponse.redirect(login);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
