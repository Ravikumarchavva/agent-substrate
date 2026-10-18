import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, config } from "../../../lib/session";

export async function GET(request: NextRequest) {
  const { issuer, clientId, publicUrl } = config();
  const url = new URL(`${issuer}/protocol/openid-connect/logout`);
  url.search = new URLSearchParams({
    client_id: clientId,
    post_logout_redirect_uri: `${publicUrl}/`,
  }).toString();
  const response = NextResponse.redirect(url);
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
