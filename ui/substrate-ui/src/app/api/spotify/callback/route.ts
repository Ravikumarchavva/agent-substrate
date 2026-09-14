/**
 * Spotify OAuth – Callback
 * GET /api/spotify/callback?code=...&state=...
 * Exchanges code for tokens, stores them as httpOnly cookies + persists in DB.
 * CSRF verified via HMAC-signed state (no cookie needed).
 */
import { NextRequest, NextResponse } from "next/server";
import { signState } from "../login/route";
import { getCredentialManager } from "@/lib/credentials";
import { getSessionFromRequest } from "@/lib/session";

const SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token";

function resolveRedirectUri(req: NextRequest): string {
  if (process.env.SPOTIFY_REDIRECT_URI) return process.env.SPOTIFY_REDIRECT_URI;
  const proto = req.headers.get("x-forwarded-proto") ?? "http";
  const host = req.headers.get("host") ?? "127.0.0.1:3000";
  const normalizedHost = host.replace(/^localhost(:\d+)?$/, "127.0.0.1$1");
  return `${proto}://${normalizedHost}/api/spotify/callback`;
}

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

  // Verify HMAC-signed state: format is "nonce.signature"
  const dotIdx = state.indexOf(".");
  if (dotIdx < 1) {
    return new NextResponse(
      buildCallbackHTML(false, "Invalid state format"),
      { status: 400, headers: { "Content-Type": "text/html" } }
    );
  }
  const nonce = state.slice(0, dotIdx);
  const sig = state.slice(dotIdx + 1);
  if (signState(nonce) !== sig) {
    return new NextResponse(
      buildCallbackHTML(false, "Invalid state parameter (CSRF protection failed)"),
      { status: 400, headers: { "Content-Type": "text/html" } }
    );
  }

  const clientId = process.env.SPOTIFY_CLIENT_ID!;
  const clientSecret = process.env.SPOTIFY_CLIENT_SECRET!;
  const redirectUri = resolveRedirectUri(req);

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

  // Clear the state cookie (legacy, no-op if not present)
  res.cookies.delete("spotify_oauth_state");

  // Push token to backend so it can use it for MCP tool calls and SDK
  const backendUrl = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
  fetch(`${backendUrl}/auth/spotify/set-token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      access_token: tokens.access_token,
      refresh_token: tokens.refresh_token ?? null,
      expires_in: expiresIn,
    }),
  }).catch((err) => console.error('[Spotify OAuth] Failed to push token to backend:', err));

  // Persist tokens in DB (encrypted) for the signed-in session's own user.
  try {
    const session = await getSessionFromRequest(req);
    if (session) {
      const cm = getCredentialManager();
      await cm.storeCredential(
        session.id,
        "spotify",
        tokens.access_token,
        tokens.refresh_token,
        expiresIn,
        tokens.scope,
      );
    }
  } catch (err) {
    console.error("[Spotify OAuth] Failed to persist tokens in DB:", err);
  }

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
    <h1>Spotify connected</h1>
    <p>Your account stayed the same. You can close this window.</p>
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
