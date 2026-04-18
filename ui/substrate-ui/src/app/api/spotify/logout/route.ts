/**
 * Spotify OAuth – Logout
 * POST /api/spotify/logout
 * Clears all Spotify cookies and removes DB credential.
 */
import { NextRequest, NextResponse } from "next/server";
import { getCredentialManager } from "@/lib/credentials";
import { prisma } from "@/lib/prisma";

export async function POST(req: NextRequest) {
  const res = NextResponse.json({ success: true, authenticated: false });
  res.cookies.delete("spotify_access_token");
  res.cookies.delete("spotify_refresh_token");

  // Remove from DB
  try {
    const userCookie = req.cookies.get("google_user")?.value;
    if (userCookie) {
      const userData = JSON.parse(decodeURIComponent(userCookie));
      if (userData.email) {
        const dbUser = await prisma.user.findUnique({ where: { email: userData.email }, select: { id: true } });
        if (dbUser) {
          const cm = getCredentialManager();
          await cm.deleteCredential(dbUser.id, "spotify");
        }
      }
    }
  } catch (err) {
    console.error("[Spotify OAuth] Failed to remove DB credential:", err);
  }

  return res;
}
