/**
 * Google OAuth – Login
 * GET /api/auth/google/login → Redirects to Google OAuth consent screen
 *
 * Dev-only bypass: requires BOTH NODE_ENV !== "production" AND
 * BYPASS_OAUTH=true set explicitly. Previously this also auto-activated
 * whenever GOOGLE_CLIENT_ID was unset, which meant a fresh clone that
 * hadn't configured OAuth yet would silently log every visitor in as a
 * hardcoded admin identity — a real problem for a project meant to be
 * cloned and self-hosted. An unconfigured GOOGLE_CLIENT_ID now fails
 * loudly instead (see below) so misconfiguration is visible, not a
 * silent admin backdoor. The bypass identity is a generic placeholder,
 * not a real person, and is never admin by default — set ADMIN_EMAIL to
 * this address if you need admin access while developing locally.
 */
import { NextRequest, NextResponse } from "next/server";
import { randomBytes } from "crypto";
import { prisma } from "@/lib/prisma";
import { createSession } from "@/lib/session";

const GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";

// Google OAuth scopes
const SCOPES = [
  "openid",
  "https://www.googleapis.com/auth/userinfo.email",
  "https://www.googleapis.com/auth/userinfo.profile",
].join(" ");

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const bypass =
    process.env.NODE_ENV !== "production" &&
    (searchParams.get("bypass") === "true" || process.env.BYPASS_OAUTH === "true");

  if (bypass) {
    const email = "dev@localhost";
    const name = "Dev User";
    const adminEmail = process.env.ADMIN_EMAIL?.toLowerCase().trim();
    const isAdmin = !!adminEmail && adminEmail === email;

    // Upsert user in database. On failure, don't pretend login succeeded —
    // no session, no cookies, matches the real OAuth callback's behavior.
    let dbUserId: string;
    try {
      const dbUser = await prisma.user.upsert({
        where: { email },
        update: { name, isAdmin },
        create: { email, googleId: "dev-bypass", name, isAdmin },
      });
      dbUserId = dbUser.id;
    } catch (err) {
      console.error("[OAuth Bypass] Database upsert failed:", err);
      return NextResponse.json(
        { error: "Dev-bypass login failed: could not create/update the local user." },
        { status: 500 },
      );
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
        user: { email: "${email}", name: "${name}", picture: "", isAdmin: ${isAdmin} }
      }, window.location.origin);
      setTimeout(() => window.close(), 500);
    } else {
      setTimeout(() => window.location.href = "/chat", 1500);
    }
  </script>
</body>
</html>`;

    const res = new NextResponse(html, {
      status: 200,
      headers: { "Content-Type": "text/html" },
    });

    // Store mock tokens (Google API access — unused by the bypass path,
    // kept only so downstream code that checks for their presence doesn't
    // need a special case).
    res.cookies.set("google_access_token", "mock-access-token", {
      httpOnly: true,
      maxAge: 3600,
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    });

    // The real identity: a DB-backed session, same as the production OAuth
    // callback creates. Previously this route never set one, so every
    // dev-bypass login left the browser acting as an anonymous visitor for
    // every actual API call despite the UI showing "signed in."
    await createSession(res, { id: dbUserId, email });

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
