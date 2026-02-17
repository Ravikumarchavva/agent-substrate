/**
 * Spotify OAuth – Callback
 * GET /api/spotify/callback?code=...&state=...
 * Exchanges code for tokens, stores in database (encrypted), closes popup.
 */
import { NextRequest, NextResponse } from "next/server";
import { getSession, getOrCreateUser, createSession } from "@/lib/session";
import { getCredentialManager } from "@/lib/credentials";

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
  const redirectUri = process.env.SPOTIFY_REDIRECT_URI || "http://127.0.0.1:3001/api/spotify/callback";

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

  // Get or create user session
  let user = await getSession();
  if (!user) {
    // Create a temporary user (later we'll link with Google account)
    user = await getOrCreateUser(`spotify_${tokens.profile?.id || Date.now()}@temp.local`);
    await createSession(user);
  }

  // Store encrypted credentials in database
  const credentialManager = getCredentialManager();
  await credentialManager.storeCredential(
    user.id,
    'spotify',
    tokens.access_token,
    tokens.refresh_token,
    expiresIn,
    tokens.scope
  );

  // Build response
  const html = buildCallbackHTML(true);

  const res = new NextResponse(html, {
    status: 200,
    headers: { "Content-Type": "text/html" },
  });

  // Clear the state cookie
  const cookieStore = await import('next/headers').then(m => m.cookies());
  (await cookieStore).delete("spotify_oauth_state");

  return res;
}

function buildCallbackHTML(success: boolean, error?: string): string {
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
    <p>Credentials saved securely. You can close this window...</p>
    <script>
      window.opener?.postMessage({ type: "spotify_auth_success" }, "*");
      setTimeout(() => window.close(), 1500);
    </script>
  </body></html>`;
}
