import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE } from "./lib/session-cookie";

// Optimistic check only: pages without a session cookie go to sign-in. The API route handler
// and the data API itself do the real verification.
export function proxy(request: NextRequest) {
  if (request.cookies.has(SESSION_COOKIE)) return NextResponse.next();
  const login = new URL("/auth/login", request.url);
  login.searchParams.set(
    "next",
    request.nextUrl.pathname + request.nextUrl.search,
  );
  return NextResponse.redirect(login);
}

export const config = {
  matcher: [
    "/((?!auth|api|_next|monaco|favicon.ico|.*\\.(?:svg|png|ico|js|css|woff2?)$).*)",
  ],
};
