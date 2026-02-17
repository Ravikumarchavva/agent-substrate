/**
 * Spotify OAuth – Login redirect
 * GET /api/spotify/login → redirects to Spotify authorization page
 */
import { NextResponse } from "next/server";

const SPOTIFY_AUTHORIZE = "https://accounts.spotify.com/authorize";
const SCOPES = [
  "streaming",
  "user-read-email",
  "user-read-private",
  "user-modify-playback-state",
  "user-read-playback-state",
].join(" ");

export async function GET() {
  const clientId = process.env.SPOTIFY_CLIENT_ID;
  const redirectUri = process.env.SPOTIFY_REDIRECT_URI || "http://127.0.0.1:3001/api/spotify/callback";

  if (!clientId) {
    return NextResponse.json({ error: "SPOTIFY_CLIENT_ID not set" }, { status: 500 });
  }

  // Generate random state for CSRF protection
  const state = crypto.randomUUID().replace(/-/g, "").slice(0, 22);

  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    redirect_uri: redirectUri,
    state,
    scope: SCOPES,
    show_dialog: "false",
  });

  const url = `${SPOTIFY_AUTHORIZE}?${params.toString()}`;

  // Store state in a cookie for validation in the callback
  const res = NextResponse.redirect(url);
  res.cookies.set("spotify_oauth_state", state, {
    httpOnly: true,
    maxAge: 300, // 5 minutes
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
  });

  return res;
}
