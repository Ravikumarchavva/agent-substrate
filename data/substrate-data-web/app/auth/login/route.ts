import { NextRequest, NextResponse } from "next/server";
import {
  LOGIN_COOKIE,
  challenge,
  config,
  cookieOptions,
  randomToken,
  seal,
} from "../../../lib/session";

export async function GET(request: NextRequest) {
  const { issuer, clientId, publicUrl } = config();
  const state = randomToken();
  const verifier = randomToken(48);
  const nonce = randomToken();
  const requested = request.nextUrl.searchParams.get("next") || "/";
  // Only same-site relative paths: no open redirects.
  const next =
    requested.startsWith("/") && !requested.startsWith("//") ? requested : "/";
  const url = new URL(`${issuer}/protocol/openid-connect/auth`);
  url.search = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    scope: "openid profile email",
    redirect_uri: `${publicUrl}/auth/callback`,
    state,
    nonce,
    code_challenge: await challenge(verifier),
    code_challenge_method: "S256",
  }).toString();
  const response = NextResponse.redirect(url);
  response.cookies.set(
    LOGIN_COOKIE,
    await seal({ state, verifier, nonce, next }),
    cookieOptions(600),
  );
  return response;
}
