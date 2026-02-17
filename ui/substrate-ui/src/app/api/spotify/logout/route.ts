/**
 * Spotify OAuth – Logout
 * POST /api/spotify/logout
 * Clears all Spotify cookies.
 */
import { NextResponse } from "next/server";

export async function POST() {
  const res = NextResponse.json({ success: true, authenticated: false });
  res.cookies.delete("spotify_access_token");
  res.cookies.delete("spotify_refresh_token");
  return res;
}
