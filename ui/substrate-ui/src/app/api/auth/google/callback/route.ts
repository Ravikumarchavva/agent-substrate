/**
 * Google OAuth – Callback
 * GET /api/auth/google/callback?code=...&state=...
 * Exchanges code for tokens, stores in httpOnly cookies
 */
import { NextRequest, NextResponse } from "next/server";

const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";
const GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo";

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
  const redirectUri = process.env.GOOGLE_REDIRECT_URI || "http://127.0.0.1:3001/api/auth/google/callback";

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

    // Build success response with cookies
    const html = buildCallbackHTML(true, undefined, {
      email: userInfo?.email,
      name: userInfo?.name,
      picture: userInfo?.picture,
    });

    const res = new NextResponse(html, {
      status: 200,
      headers: { "Content-Type": "text/html" },
    });

    // Store tokens in httpOnly cookies
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

    // Store user info cookie (not httpOnly, so frontend can read it)
    if (userInfo) {
      res.cookies.set("google_user", JSON.stringify({
        email: userInfo.email,
        name: userInfo.name,
        picture: userInfo.picture,
      }), {
        httpOnly: false,
        maxAge: 60 * 60 * 24 * 365,
        path: "/",
        sameSite: "lax",
        secure: process.env.NODE_ENV === "production",
      });
    }

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
  user?: { email?: string; name?: string; picture?: string }
): string {
  if (!success) {
    return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Google Sign-In Failed</title>
  <style>body{font-family:system-ui;padding:40px;text-align:center;background:#1a1a1a;color:#fff}</style>
</head>
<body>
  <h1>❌ Sign-In Failed</h1>
  <p>${error || "Unknown error"}</p>
  <p><a href="/" style="color:#4285f4">Return to Home</a></p>
  <script>
    setTimeout(() => window.location.href = "/", 3000);
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
      ${user.picture ? `<img src="${user.picture}" alt="Profile" />` : ""}
      <p><strong>${user.name || "User"}</strong></p>
      <p>${user.email || ""}</p>
    </div>
  ` : ""}
  <p>Redirecting...</p>
  <script>
    // Notify parent window if opened as popup
    if (window.opener) {
      window.opener.postMessage({
        type: "google_auth_success",
        user: ${JSON.stringify(user || {})}
      }, "*");
    }
    // Redirect to home
    setTimeout(() => window.location.href = "/", 1500);
  </script>
</body>
</html>`;
}
