/**
 * Spotify OAuth – Callback
 * GET /api/spotify/callback?code=...&state=...
 * Exchanges code for tokens, stores them as httpOnly cookies (cookie-based auth,
 * consistent with Google OAuth flow — no Prisma session required).
 */
import { NextRequest, NextResponse } from "next/server";

const SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const code = searchParams.get("code");
  const state = searchParams.get("state");
  const error = searchParams.get("error");

  if (error) {
    return new NextResponse(
      buildCallbackHTML(false, error),
      { status: 400, headers: { "Content-Type": "text/html" } }
    );
  }

  if (!code || !state) {
    return new NextResponse(
      buildCallbackHTML(false, "Missing code or state"),
      { status: 400, headers: { "Content-Type": "text/html" } }
    );
  }

  // Validate CSRF state
  const savedState = req.cookies.get("spotify_oauth_state")?.value;
  if (!savedState || savedState !== state) {
    return new NextResponse(
      buildCallbackHTML(false, "Invalid state parameter (CSRF protection failed)"),
      { status: 400, headers: { "Content-Type": "text/html" } }
    );
  }

  const clientId = process.env.SPOTIFY_CLIENT_ID!;
  const clientSecret = process.env.SPOTIFY_CLIENT_SECRET!;
  const redirectUri = process.env.SPOTIFY_REDIRECT_URI || "http://127.0.0.1:3000/api/spotify/callback";

  // Exchange code for tokens
  const basic = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");

  const tokenRes = await fetch(SPOTIFY_TOKEN_URL, {
    method: "POST",
    headers: {
      Authorization: `Basic ${basic}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri,
    }),
  });

  if (!tokenRes.ok) {
    const errText = await tokenRes.text();
    console.error("[Spotify OAuth] Token exchange failed:", errText);
    return new NextResponse(
      buildCallbackHTML(false, "Token exchange failed"),
      { status: 500, headers: { "Content-Type": "text/html" } }
    );
  }

  const tokens = await tokenRes.json();
  const expiresIn = tokens.expires_in || 3600;

  // Build response HTML that posts the token directly to the opener window.
  // Cookies are also set as a same-origin fallback, but the primary token
  // delivery is via postMessage — this bypasses the localhost/127.0.0.1
  // cookie isolation that would otherwise cause a 401 on /api/spotify/token.
  const html = buildCallbackHTML(true, undefined, tokens.access_token, expiresIn);
  const res = new NextResponse(html, {
    status: 200,
    headers: { "Content-Type": "text/html" },
  });

  res.cookies.set("spotify_access_token", tokens.access_token, {
    httpOnly: true,
    maxAge: expiresIn,
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
  });

  if (tokens.refresh_token) {
    res.cookies.set("spotify_refresh_token", tokens.refresh_token, {
      httpOnly: true,
      maxAge: 60 * 60 * 24 * 30, // 30 days
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    });
  }

  // Clear the state cookie
  res.cookies.delete("spotify_oauth_state");

  return res;
}

function buildCallbackHTML(
  success: boolean,
  error?: string,
  accessToken?: string,
  expiresIn?: number,
): string {
  if (!success) {
    return `<!DOCTYPE html><html><body>
      <h1>Spotify Authentication Failed</h1>
      <p>${error || "Unknown error"}</p>
      <script>
        window.opener?.postMessage({ type: "spotify_auth_error", error: "${error || "unknown"}" }, "*");
        setTimeout(() => window.close(), 3000);
      </script>
    </body></html>`;
  }

  return `<!DOCTYPE html><html><body>
    <h1>Connected to Spotify!</h1>
    <p>You can close this window...</p>
    <script>
      // Deliver the token directly to the opener via postMessage.
      // This works even when the popup and parent are on different origins
      // (e.g. 127.0.0.1 vs localhost) because no cookies are involved.
      window.opener?.postMessage({
        type: "spotify_auth_success",
        access_token: ${accessToken ? JSON.stringify(accessToken) : "null"},
        expires_in: ${expiresIn ?? 3600}
      }, "*");
      setTimeout(() => window.close(), 1000);
    </script>
  </body></html>`;
}
