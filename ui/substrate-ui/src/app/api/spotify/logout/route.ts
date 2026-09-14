/**
 * Spotify OAuth – Logout
 * POST /api/spotify/logout
 * Clears all Spotify cookies and removes DB credential.
 */
import { NextRequest, NextResponse } from "next/server";
import { getCredentialManager } from "@/lib/credentials";
import { getSessionFromRequest } from "@/lib/session";

const BACKEND_URL = process.env.BACKEND_API_URL ?? "http://localhost:8000";

export async function POST(req: NextRequest) {
  const res = NextResponse.json({ success: true, authenticated: false });
  res.cookies.delete("spotify_access_token");
  res.cookies.delete("spotify_refresh_token");

  // Also clear the in-memory token on the Python backend
  fetch(`${BACKEND_URL}/auth/spotify/logout`, { method: "POST" }).catch(() => {/* ignore */});

  // Remove from DB
  try {
    const session = await getSessionFromRequest(req);
    if (session) {
      const cm = getCredentialManager();
      await cm.deleteCredential(session.id, "spotify");
    }
  } catch (err) {
    console.error("[Spotify OAuth] Failed to remove DB credential:", err);
  }

  return res;
}
