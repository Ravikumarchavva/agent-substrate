import { NextRequest, NextResponse } from "next/server";
import {
  LOGIN_COOKIE,
  SESSION_COOKIE,
  type LoginState,
  cookieOptions,
  config,
  seal,
  sessionFrom,
  tokenRequest,
  unseal,
} from "../../../lib/session";

export async function GET(request: NextRequest) {
  const login = await unseal<LoginState>(
    request.cookies.get(LOGIN_COOKIE)?.value,
  );
  const code = request.nextUrl.searchParams.get("code");
  if (
    !login ||
    !code ||
    request.nextUrl.searchParams.get("state") !== login.state
  ) {
    return NextResponse.json(
      { detail: "Sign-in could not be verified; start again" },
      { status: 400 },
    );
  }
  const tokens = await tokenRequest({
    grant_type: "authorization_code",
    code,
    redirect_uri: `${config().publicUrl}/auth/callback`,
    code_verifier: login.verifier,
  });
  const response = NextResponse.redirect(
    new URL(login.next, config().publicUrl),
  );
  response.cookies.set(
    SESSION_COOKIE,
    await seal(sessionFrom(tokens)),
    cookieOptions(60 * 60 * 10),
  );
  response.cookies.delete(LOGIN_COOKIE);
  return response;
}
