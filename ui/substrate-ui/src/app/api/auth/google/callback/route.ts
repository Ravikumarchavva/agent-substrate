/**
 * Google OAuth – Callback
 * GET /api/auth/google/callback?code=...&state=...
 * Exchanges code for tokens, creates a DB-backed session.
 */
import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { createSession } from "@/lib/session";

const ADMIN_EMAILS = new Set(process.env.ADMIN_EMAIL ? [process.env.ADMIN_EMAIL.toLowerCase().trim()] : []);

const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";
const GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo";

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const code = searchParams.get("code");
  const state = searchParams.get("state");
  const error = searchParams.get("error");

  // Check for OAuth errors
  if (error) {
    return new NextResponse(
      buildCallbackHTML(false, `Google OAuth error: ${error}`),
      { status: 400, headers: { "Content-Type": "text/html" } }
    );
  }

  // Validate required parameters
  if (!code || !state) {
    return new NextResponse(
      buildCallbackHTML(false, "Missing code or state parameter"),
      { status: 400, headers: { "Content-Type": "text/html" } }
    );
  }

  // Validate CSRF state
  const savedState = req.cookies.get("google_oauth_state")?.value;
  if (!savedState || savedState !== state) {
    return new NextResponse(
      buildCallbackHTML(false, "Invalid state parameter (CSRF protection failed)"),
      { status: 400, headers: { "Content-Type": "text/html" } }
    );
  }

  const clientId = process.env.GOOGLE_CLIENT_ID!;
  const clientSecret = process.env.GOOGLE_CLIENT_SECRET!;
  const redirectUri = process.env.GOOGLE_REDIRECT_URI || "http://127.0.0.1:3000/api/auth/google/callback";

  try {
    // Exchange authorization code for tokens
    const tokenResponse = await fetch(GOOGLE_TOKEN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code,
        client_id: clientId,
        client_secret: clientSecret,
        redirect_uri: redirectUri,
        grant_type: "authorization_code",
      }),
    });

    if (!tokenResponse.ok) {
      const errorText = await tokenResponse.text();
      console.error("[Google OAuth] Token exchange failed:", errorText);
      return new NextResponse(
        buildCallbackHTML(false, "Failed to exchange code for tokens"),
        { status: 500, headers: { "Content-Type": "text/html" } }
      );
    }

    const tokens = await tokenResponse.json();

    // Fetch user info
    let userInfo = null;
    try {
      const userResponse = await fetch(GOOGLE_USERINFO_URL, {
        headers: { Authorization: `Bearer ${tokens.access_token}` },
      });
      if (userResponse.ok) {
        userInfo = await userResponse.json();
      }
    } catch (err) {
      console.warn("[Google OAuth] Failed to fetch user info:", err);
    }

    if (!userInfo?.email) {
      return new NextResponse(
        buildCallbackHTML(false, "Google did not return an email address"),
        { status: 502, headers: { "Content-Type": "text/html" } }
      );
    }

    // Upsert user in Prisma database. On failure, set NO cookies at all —
    // a partial login (display cookie but no real session) is exactly the
    // silent-identity-mismatch bug this flow used to have.
    const isAdmin = ADMIN_EMAILS.has(userInfo.email.toLowerCase().trim());
    let dbUserId: string;
    try {
      const dbUser = await prisma.user.upsert({
        where: { email: userInfo.email },
        update: {
          name: userInfo.name ?? undefined,
          avatarUrl: userInfo.picture ?? undefined,
          isAdmin,
        },
        create: {
          email: userInfo.email,
          googleId: userInfo.id ?? undefined,
          name: userInfo.name ?? undefined,
          avatarUrl: userInfo.picture ?? undefined,
          isAdmin,
        },
      });
      dbUserId = dbUser.id;
    } catch (err) {
      console.error("[Google OAuth] Failed to upsert user:", err);
      return new NextResponse(
        buildCallbackHTML(false, "Failed to create your account. Please try again."),
        { status: 500, headers: { "Content-Type": "text/html" } }
      );
    }

    const html = buildCallbackHTML(true, undefined, {
      email: userInfo.email,
      name: userInfo.name,
      picture: userInfo.picture,
      isAdmin,
    });

    const res = new NextResponse(html, {
      status: 200,
      headers: { "Content-Type": "text/html" },
    });

    // Store tokens in httpOnly cookies (Google API access, unrelated to
    // this app's own session).
    const expiresIn = tokens.expires_in || 3600;
    res.cookies.set("google_access_token", tokens.access_token, {
      httpOnly: true,
      maxAge: expiresIn,
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    });

    if (tokens.refresh_token) {
      res.cookies.set("google_refresh_token", tokens.refresh_token, {
        httpOnly: true,
        maxAge: 60 * 60 * 24 * 365, // 1 year
        path: "/",
        sameSite: "lax",
        secure: process.env.NODE_ENV === "production",
      });
    }

    // The one cookie that establishes identity: an opaque, DB-backed
    // session token (see lib/session.ts). Nothing readable/parseable by a
    // script determines who this request acts as anymore.
    await createSession(res, { id: dbUserId, email: userInfo.email });

    // Clear state cookie
    res.cookies.delete("google_oauth_state");

    return res;
  } catch (err) {
    console.error("[Google OAuth] Unexpected error:", err);
    return new NextResponse(
      buildCallbackHTML(false, "An unexpected error occurred"),
      { status: 500, headers: { "Content-Type": "text/html" } }
    );
  }
}

function buildCallbackHTML(
  success: boolean,
  error?: string,
  user?: { email?: string; name?: string; picture?: string; isAdmin?: boolean }
): string {
  if (!success) {
    const safeError = escapeHtml(error || "Unknown error");
    return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Google Sign-In Failed</title>
  <style>body{font-family:system-ui;padding:40px;text-align:center;background:#1a1a1a;color:#fff}</style>
</head>
<body>
  <h1>❌ Sign-In Failed</h1>
  <p>${safeError}</p>
  <p><a href="/chat" style="color:#4285f4">Return to Home</a></p>
  <script>
    if (window.opener) {
      window.opener.postMessage({ type: "google_auth_error", error: ${JSON.stringify(error || "Unknown error")} }, window.location.origin);
      setTimeout(() => window.close(), 3000);
    } else {
      setTimeout(() => window.location.href = "/chat", 3000);
    }
  </script>
</body>
</html>`;
  }

  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Google Sign-In Successful</title>
  <style>
    body{font-family:system-ui;padding:40px;text-align:center;background:#1a1a1a;color:#fff}
    .user{margin:20px 0}
    img{border-radius:50%;width:80px;height:80px}
  </style>
</head>
<body>
  <h1>✅ Signed in with Google</h1>
  ${user ? `
    <div class="user">
      ${user.picture ? `<img src="${escapeHtml(user.picture)}" alt="Profile" />` : ""}
      <p><strong>${escapeHtml(user.name || "User")}</strong></p>
      <p>${escapeHtml(user.email || "")}</p>
    </div>
  ` : ""}
  <p>Redirecting...</p>
  <script>
    // Notify parent window and close popup
    if (window.opener) {
      window.opener.postMessage({
        type: "google_auth_success",
        user: ${JSON.stringify(user || {})}
      }, window.location.origin);
      setTimeout(() => window.close(), 500);
    } else {
      // Not a popup — redirect directly
      setTimeout(() => window.location.href = "/chat", 1500);
    }
  </script>
</body>
</html>`;
}
