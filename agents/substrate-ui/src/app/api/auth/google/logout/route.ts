/**
 * Google OAuth – Logout
 * POST /api/auth/google/logout
 * Destroys the DB-backed session and clears all Google OAuth cookies.
 */
import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE_NAME, deleteSessionByToken } from "@/lib/session";

export async function POST(req: NextRequest) {
  const token = req.cookies.get(SESSION_COOKIE_NAME)?.value;
  await deleteSessionByToken(token);

  const res = NextResponse.json({
    success: true,
    authenticated: false,
  });

  res.cookies.delete("google_access_token");
  res.cookies.delete("google_refresh_token");
  res.cookies.delete(SESSION_COOKIE_NAME);

  return res;
}
