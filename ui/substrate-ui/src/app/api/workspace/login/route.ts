/**
 * Google Workspace OAuth – Login
 * GET /api/workspace/login → Redirects to Google OAuth with Drive/Calendar/Gmail scopes.
 *
 * Uses a separate OAuth flow from the basic Google sign-in so workspace scopes
 * can be granted independently. The token is stored as provider='google_workspace'
 * in Prisma and pushed to the backend for use by GoogleWorkspaceTool.
 */
import { NextResponse } from "next/server";
import { randomBytes } from "crypto";

const GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";

const WORKSPACE_SCOPES = [
  "openid",
  "https://www.googleapis.com/auth/userinfo.email",
  "https://www.googleapis.com/auth/drive.readonly",
  "https://www.googleapis.com/auth/calendar.events",
  "https://www.googleapis.com/auth/gmail.readonly",
].join(" ");

export async function GET(): Promise<NextResponse> {
  const clientId = process.env.GOOGLE_CLIENT_ID;
  const redirectUri =
    process.env.WORKSPACE_REDIRECT_URI ??
    process.env.GOOGLE_REDIRECT_URI?.replace("/api/auth/google/callback", "/api/workspace/callback") ??
    "http://127.0.0.1:3000/api/workspace/callback";

  if (!clientId) {
    return NextResponse.json(
      { error: "GOOGLE_CLIENT_ID not configured" },
      { status: 500 }
    );
  }

  const state = randomBytes(16).toString("hex");

  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: WORKSPACE_SCOPES,
    state,
    access_type: "offline",
    prompt: "consent",
    include_granted_scopes: "true",
  });

  const authUrl = `${GOOGLE_AUTH_URL}?${params.toString()}`;

  const res = NextResponse.redirect(authUrl);
  res.cookies.set("workspace_oauth_state", state, {
    httpOnly: true,
    maxAge: 300, // 5 minutes
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
  });

  return res;
}
