/**
 * Google OAuth – Login
 * GET /api/auth/google/login → Redirects to Google OAuth consent screen
 * Supports bypass parameter or auto-bypass in dev/unconfigured environment
 */
import { NextRequest, NextResponse } from "next/server";
import { randomBytes } from "crypto";
import { prisma } from "@/lib/prisma";

const GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";

// Google OAuth scopes
const SCOPES = [
  "openid",
  "https://www.googleapis.com/auth/userinfo.email",
  "https://www.googleapis.com/auth/userinfo.profile",
].join(" ");

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const bypass = searchParams.get("bypass") === "true" || process.env.BYPASS_OAUTH === "true" || !process.env.GOOGLE_CLIENT_ID;

  if (bypass) {
    const email = "chavvaravikumarreddy2004@gmail.com";
    const name = "Ravikumar Chavva";
    const isAdmin = true;

    // Upsert user in database
    try {
      await prisma.user.upsert({
        where: { email },
        update: {
          name,
          isAdmin,
        },
        create: {
          email,
          googleId: "mock-google-id",
          name,
          isAdmin,
        },
      });
    } catch (err) {
      console.error("[OAuth Bypass] Database upsert failed:", err);
    }

    // Success callback response
    const html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Google Sign-In Successful (Bypassed)</title>
  <style>
    body{font-family:system-ui;padding:40px;text-align:center;background:#1a1a1a;color:#fff}
    .user{margin:20px 0}
  </style>
</head>
<body>
  <h1>✅ Signed in with Google (Bypassed)</h1>
  <div class="user">
    <p><strong>${name}</strong></p>
    <p>${email}</p>
  </div>
  <p>Redirecting...</p>
  <script>
    if (window.opener) {
      window.opener.postMessage({
        type: "google_auth_success",
        user: { email: "${email}", name: "${name}", picture: "", isAdmin: true }
      }, window.location.origin);
      setTimeout(() => window.close(), 500);
    } else {
      setTimeout(() => window.location.href = "/", 1500);
    }
  </script>
</body>
</html>`;

    const res = new NextResponse(html, {
      status: 200,
      headers: { "Content-Type": "text/html" },
    });

    // Store tokens in cookies
    res.cookies.set("google_access_token", "mock-access-token", {
      httpOnly: true,
      maxAge: 3600,
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    });

    res.cookies.set("google_refresh_token", "mock-refresh-token", {
      httpOnly: true,
      maxAge: 60 * 60 * 24 * 365,
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    });

    res.cookies.set("google_user", JSON.stringify({
      email,
      name,
      picture: "",
      isAdmin,
    }), {
      httpOnly: false,
      maxAge: 60 * 60 * 24 * 365,
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    });

    return res;
  }

  const clientId = process.env.GOOGLE_CLIENT_ID;
  const redirectUri = process.env.GOOGLE_REDIRECT_URI || "http://127.0.0.1:3000/api/auth/google/callback";

  if (!clientId) {
    return NextResponse.json(
      { error: "GOOGLE_CLIENT_ID not configured" },
      { status: 500 }
    );
  }

  // Generate CSRF protection state
  const state = randomBytes(16).toString("hex");

  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: SCOPES,
    state,
    access_type: "offline",
    prompt: "consent",
  });

  const authUrl = `${GOOGLE_AUTH_URL}?${params.toString()}`;

  // Store state in cookie for validation
  const res = NextResponse.redirect(authUrl);
  res.cookies.set("google_oauth_state", state, {
    httpOnly: true,
    maxAge: 300,
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
  });

  return res;
}
