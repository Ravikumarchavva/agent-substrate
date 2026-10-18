/**
 * Google OAuth – Token endpoint
 * GET /api/auth/google/token
 * Returns current access token (auto-refreshes if expired)
 */
import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE_NAME, deleteSessionByToken } from "@/lib/session";

const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";

export async function GET(req: NextRequest) {
  const accessToken = req.cookies.get("google_access_token")?.value;
  const refreshToken = req.cookies.get("google_refresh_token")?.value;

  // If we have a valid access token, return it
  if (accessToken) {
    return NextResponse.json({
      access_token: accessToken,
      authenticated: true,
    });
  }

  // No access token but refresh token exists – try to refresh
  if (refreshToken) {
    const result = await refreshAccessToken(refreshToken);
    if (result.success) {
      const res = NextResponse.json({
        access_token: result.access_token,
        authenticated: true,
      });
      
      // Update cookie with new token
      res.cookies.set("google_access_token", result.access_token!, {
        httpOnly: true,
        maxAge: result.expires_in || 3600,
        path: "/",
        sameSite: "lax",
        secure: process.env.NODE_ENV === "production",
      });
      
      return res;
    }
    
    // Refresh failed — this app's own session is a separate concern from
    // Google API access, but if we can no longer act as this user's Google
    // account, don't leave a half-valid app session lying around either:
    // destroy it so the UI is forced to re-authenticate cleanly instead of
    // silently continuing to look "logged in" while Google access is dead.
    const sessionToken = req.cookies.get(SESSION_COOKIE_NAME)?.value;
    await deleteSessionByToken(sessionToken);

    const res = NextResponse.json({
      authenticated: false,
      error: "Token refresh failed",
    });
    res.cookies.delete("google_access_token");
    res.cookies.delete("google_refresh_token");
    res.cookies.delete(SESSION_COOKIE_NAME);
    return res;
  }

  // Not authenticated
  return NextResponse.json({ authenticated: false });
}

async function refreshAccessToken(refreshToken: string) {
  const clientId = process.env.GOOGLE_CLIENT_ID!;
  const clientSecret = process.env.GOOGLE_CLIENT_SECRET!;

  try {
    const tokenResponse = await fetch(GOOGLE_TOKEN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: clientId,
        client_secret: clientSecret,
        refresh_token: refreshToken,
        grant_type: "refresh_token",
      }),
    });

    if (!tokenResponse.ok) {
      const errorText = await tokenResponse.text();
      console.error("[Google OAuth] Refresh failed:", errorText);
      return { success: false as const };
    }

    const data = await tokenResponse.json();
    return {
      success: true as const,
      access_token: data.access_token as string,
      expires_in: data.expires_in as number,
    };
  } catch (err) {
    console.error("[Google OAuth] Refresh error:", err);
    return { success: false as const };
  }
}
