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
  // A localhost→127.0.0.1 redirect used to live here, for consistent OAuth
  // state-cookie host matching (GOOGLE_REDIRECT_URI is registered against
  // 127.0.0.1). Removed: verified by real HTTP trace, across three
  // different construction attempts (NextURL mutation, forced string
  // serialization, a plain `new URL(req.url)` rebuild), that Next's dev
  // server always collapsed the Location header back to a bare relative
  // path for this specific case — almost certainly deliberate normalization
  // tied to `allowedDevOrigins` in next.config.ts (127.0.0.1 is listed
  // there as a trusted dev origin for this same app), not something
  // reachable from application code. A relative Location resolves against
  // the *current* host, so the redirect was silently a no-op. Broken code
  // that looks like it works is worse than no code — if you need this,
  // just always develop via http://127.0.0.1:3000 directly (verified
  // clean: no redirect, cookie sets correctly, matches the registered
  // OAuth redirect_uri already).
  return withAnonId(req, NextResponse.next());
}

export const config = {
  // Two entries, not one: the single-pattern form requires a "/" before its
  // capture group, so it never matched the bare basePath root itself
  // (real URL "/chat" with no trailing segment) — verified by HTTP trace
  // that the very first page load (the welcome screen, before any user
  // interaction) got no anon_id cookie at all, defeating the "before the
  // first request is proxied" guarantee this file's own docstring promises.
  matcher: ["/", "/((?!_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
