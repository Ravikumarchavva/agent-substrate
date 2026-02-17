/**
 * Spotify OAuth – Refresh endpoint
 * POST /api/spotify/refresh
 * Forces a token refresh using the stored refresh_token cookie.
 */
import { NextRequest, NextResponse } from "next/server";

const SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token";

export async function POST(req: NextRequest) {
  const refreshToken = req.cookies.get("spotify_refresh_token")?.value;

  if (!refreshToken) {
    return NextResponse.json(
      { error: "No refresh token", authenticated: false },
      { status: 401 }
    );
  }

  const clientId = process.env.SPOTIFY_CLIENT_ID!;
  const clientSecret = process.env.SPOTIFY_CLIENT_SECRET!;
  const basic = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");

  try {
    const tokenRes = await fetch(SPOTIFY_TOKEN_URL, {
      method: "POST",
      headers: {
        Authorization: `Basic ${basic}`,
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        refresh_token: refreshToken,
      }),
    });

    if (!tokenRes.ok) {
      const errText = await tokenRes.text();
      console.error("[Spotify OAuth] Refresh failed:", errText);
      const res = NextResponse.json(
        { error: "Refresh failed", authenticated: false },
        { status: 401 }
      );
      res.cookies.delete("spotify_access_token");
      res.cookies.delete("spotify_refresh_token");
      return res;
    }

    const data = await tokenRes.json();
    const expiresIn = data.expires_in || 3600;

    const res = NextResponse.json({
      access_token: data.access_token,
      expires_in: expiresIn,
      authenticated: true,
    });

    res.cookies.set("spotify_access_token", data.access_token, {
      httpOnly: true,
      maxAge: expiresIn,
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    });

    // Spotify sometimes rotates refresh tokens
    if (data.refresh_token) {
      res.cookies.set("spotify_refresh_token", data.refresh_token, {
        httpOnly: true,
        maxAge: 60 * 60 * 24 * 30,
        path: "/",
        sameSite: "lax",
        secure: process.env.NODE_ENV === "production",
      });
    }

    return res;
  } catch (err) {
    console.error("[Spotify OAuth] Refresh error:", err);
    return NextResponse.json(
      { error: "Internal error", authenticated: false },
      { status: 500 }
    );
  }
}
