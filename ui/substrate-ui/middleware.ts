import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

/**
 * Stable pseudonymous id for visitors with no login.
 *
 * Without this, every not-logged-in browser authenticates to agent-substrate as
 * the fixed `substrate-ui` service account, so they all share one
 * `users/{sub}/...` workspace: one storage quota between them, and any
 * anonymous visitor can read every other anonymous visitor's uploads and
 * generated files. Minting a per-browser id restores that isolation without
 * forcing a login.
 *
 * Set here rather than in a route handler so it exists before the first request
 * is proxied — otherwise whichever route the user happened to hit first would
 * still run under the shared service identity.
 */
const ANON_COOKIE = "anon_id";
const ANON_MAX_AGE = 60 * 60 * 24 * 365; // a year — losing it orphans their files

function withAnonId(req: NextRequest, res: NextResponse): NextResponse {
  if (!req.cookies.get(ANON_COOKIE)?.value) {
    res.cookies.set(ANON_COOKIE, crypto.randomUUID(), {
      httpOnly: true,
      sameSite: "lax",
      path: "/",
      maxAge: ANON_MAX_AGE,
      secure: process.env.NODE_ENV === "production",
    });
  }
  return res;
}

export function middleware(req: NextRequest) {
  if (process.env.NODE_ENV === "production") {
    return withAnonId(req, NextResponse.next());
  }

  if (req.nextUrl.hostname !== "localhost") {
    return withAnonId(req, NextResponse.next());
  }

  const redirectUrl = req.nextUrl.clone();
  redirectUrl.hostname = "127.0.0.1";
  return withAnonId(req, NextResponse.redirect(redirectUrl));
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
