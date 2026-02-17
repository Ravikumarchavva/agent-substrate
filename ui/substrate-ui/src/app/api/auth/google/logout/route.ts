/**
 * Google OAuth – Logout
 * POST /api/auth/google/logout
 * Clears all Google OAuth cookies
 */
import { NextResponse } from "next/server";

export async function POST() {
  const res = NextResponse.json({
    success: true,
    authenticated: false,
  });

  // Clear all Google OAuth cookies
  res.cookies.delete("google_access_token");
  res.cookies.delete("google_refresh_token");
  res.cookies.delete("google_user");

  return res;
}
