/**
 * Spotify OAuth – Logout
 * POST /api/spotify/logout
 * Clears all Spotify cookies and removes DB credential.
 */
import { NextRequest, NextResponse } from "next/server";
import { getCredentialManager } from "@/lib/credentials";
import { currentUser } from "@/lib/session";

export async function POST(req: NextRequest) {
  const res = NextResponse.json({ success: true, authenticated: false });
  res.cookies.delete("spotify_access_token");
  res.cookies.delete("spotify_refresh_token");

  // Remove from DB
  try {
    const session = await currentUser(req);
    if (session) {
      const cm = getCredentialManager();
      await cm.deleteCredential(session.id, "spotify");
    }
  } catch (err) {
    console.error("[Spotify OAuth] Failed to remove DB credential:", err);
  }

  return res;
}
