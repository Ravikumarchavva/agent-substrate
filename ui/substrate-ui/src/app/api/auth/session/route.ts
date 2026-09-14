/**
 * GET /api/auth/session
 *
 * The single server-verified source of truth for "who is logged in" —
 * looks up the DB-backed session (src/lib/session.ts) rather than trusting
 * any client-readable cookie. AuthContext uses only this to decide
 * isAuthenticated/isAdmin/user; nothing else in the frontend should read
 * an auth cookie directly.
 */
import { NextResponse } from "next/server";
import { getSession } from "@/lib/session";

export async function GET() {
  const session = await getSession();
  if (!session) {
    return NextResponse.json({ authenticated: false, user: null });
  }
  return NextResponse.json({
    authenticated: true,
    user: {
      id: session.id,
      email: session.email,
      name: session.name ?? null,
      avatarUrl: session.avatarUrl ?? null,
      isAdmin: session.isAdmin,
    },
  });
}
