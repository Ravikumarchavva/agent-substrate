/**
 * Spotify OAuth – Login redirect
 * GET /api/spotify/login → redirects to Spotify authorization page
 *
 * CSRF protection uses HMAC-signed state (not cookies) to avoid
 * localhost ↔ 127.0.0.1 cookie domain mismatch in dev.
 */
import { createHmac } from "crypto";
import { NextRequest, NextResponse } from "next/server";

const SPOTIFY_AUTHORIZE = "https://accounts.spotify.com/authorize";
const SCOPES = [
  "streaming",
  "user-read-email",
  "user-read-private",
  "user-modify-playback-state",
  "user-read-playback-state",
].join(" ");

function resolveRedirectUri(req: NextRequest): string {
  if (process.env.SPOTIFY_REDIRECT_URI) return process.env.SPOTIFY_REDIRECT_URI;
  // Spotify rejects localhost redirect URIs — always use 127.0.0.1
  const proto = req.headers.get("x-forwarded-proto") ?? "http";
  const host = req.headers.get("host") ?? "127.0.0.1:3000";
  const normalizedHost = host.replace(/^localhost(:\d+)?$/, "127.0.0.1$1");
  return `${proto}://${normalizedHost}/api/spotify/callback`;
}

/** Build a signed state: `nonce.hmac` — verifiable without cookies. */
export function signState(nonce: string): string {
  const secret = process.env.SPOTIFY_CLIENT_SECRET ?? "dev-fallback";
  return createHmac("sha256", secret).update(nonce).digest("hex").slice(0, 16);
}

export async function GET(req: NextRequest) {
  const clientId = process.env.SPOTIFY_CLIENT_ID;
  const redirectUri = resolveRedirectUri(req);

  if (!clientId) {
    return NextResponse.json({ error: "SPOTIFY_CLIENT_ID not set" }, { status: 500 });
  }

  const nonce = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
  const state = `${nonce}.${signState(nonce)}`;

  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    redirect_uri: redirectUri,
    state,
    scope: SCOPES,
    show_dialog: "false",
  });

  return NextResponse.redirect(`${SPOTIFY_AUTHORIZE}?${params.toString()}`);
}
