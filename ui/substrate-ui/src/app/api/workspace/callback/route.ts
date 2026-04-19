/**
 * Google Workspace OAuth – Callback
 * GET /api/workspace/callback?code=...&state=...
 *
 * Exchanges code for tokens, persists as provider='google_workspace' in Prisma,
 * and pushes the access token to the backend for use by GoogleWorkspaceTool.
 * Notifies the opener window on success.
 */
import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { getCredentialManager } from "@/lib/credentials";

const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";
const GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo";
const BACKEND_URL = process.env.BACKEND_API_URL ?? "http://localhost:8000";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const { searchParams } = new URL(req.url);
  const code = searchParams.get("code");
  const state = searchParams.get("state");
  const error = searchParams.get("error");

  if (error) {
    return new NextResponse(buildHTML(false, `Google OAuth error: ${error}`), {
      status: 400,
      headers: { "Content-Type": "text/html" },
    });
  }

  if (!code || !state) {
    return new NextResponse(buildHTML(false, "Missing code or state parameter"), {
      status: 400,
      headers: { "Content-Type": "text/html" },
    });
  }

  // CSRF validation
  const savedState = req.cookies.get("workspace_oauth_state")?.value;
  if (!savedState || savedState !== state) {
    return new NextResponse(buildHTML(false, "Invalid state (CSRF protection failed)"), {
      status: 400,
      headers: { "Content-Type": "text/html" },
    });
  }

  const clientId = process.env.GOOGLE_CLIENT_ID!;
  const clientSecret = process.env.GOOGLE_CLIENT_SECRET!;
  const redirectUri =
    process.env.WORKSPACE_REDIRECT_URI ??
    process.env.GOOGLE_REDIRECT_URI?.replace("/api/auth/google/callback", "/api/workspace/callback") ??
    "http://127.0.0.1:3000/api/workspace/callback";

  try {
    // Exchange code for tokens
    const tokenRes = await fetch(GOOGLE_TOKEN_URL, {
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

    if (!tokenRes.ok) {
      const errText = await tokenRes.text();
      console.error("[Workspace OAuth] Token exchange failed:", errText);
      return new NextResponse(buildHTML(false, "Token exchange failed"), {
        status: 500,
        headers: { "Content-Type": "text/html" },
      });
    }

    const tokens = (await tokenRes.json()) as {
      access_token: string;
      refresh_token?: string;
      expires_in?: number;
    };
    const expiresIn = tokens.expires_in ?? 3600;

    // Fetch email so we can link to the user record
    let email: string | null = null;
    try {
      const userRes = await fetch(GOOGLE_USERINFO_URL, {
        headers: { Authorization: `Bearer ${tokens.access_token}` },
      });
      if (userRes.ok) {
        const info = (await userRes.json()) as { email?: string };
        email = info.email ?? null;
      }
    } catch (err) {
      console.error("[Workspace OAuth] Failed to fetch user info:", err);
    }

    try {
      const backendRes = await fetch(`${BACKEND_URL}/auth/workspace/set-token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          access_token: tokens.access_token,
          refresh_token: tokens.refresh_token ?? null,
          expires_in: expiresIn,
        }),
      });
      if (!backendRes.ok) {
        console.error(
          "[Workspace OAuth] Failed to push token to backend:",
          backendRes.status,
          await backendRes.text(),
        );
      }
    } catch (err) {
      console.error("[Workspace OAuth] Failed to push token to backend:", err);
    }

    // Persist in Prisma as provider='google_workspace'
    if (email) {
      try {
        const dbUser = await prisma.user.findUnique({
          where: { email },
          select: { id: true },
        });
        if (dbUser) {
          const cm = getCredentialManager();
          await cm.storeCredential(
            dbUser.id,
            "google_workspace",
            tokens.access_token,
            tokens.refresh_token ?? "",
            expiresIn,
          );
        }
      } catch (err) {
        console.error("[Workspace OAuth] Failed to persist tokens:", err);
      }
    }

    const res = new NextResponse(buildHTML(true), {
      status: 200,
      headers: { "Content-Type": "text/html" },
    });
    res.cookies.delete("workspace_oauth_state");
    return res;
  } catch (err) {
    console.error("[Workspace OAuth] Unexpected error:", err);
    return new NextResponse(buildHTML(false, "An unexpected error occurred"), {
      status: 500,
      headers: { "Content-Type": "text/html" },
    });
  }
}

function buildHTML(success: boolean, error?: string): string {
  if (!success) {
    return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Workspace Connect Failed</title>
<style>body{font-family:system-ui;padding:40px;text-align:center;background:#1a1a1a;color:#fff}</style>
</head><body>
  <h1>❌ Workspace Connection Failed</h1>
  <p>${error ?? "Unknown error"}</p>
  <script>
    window.opener?.postMessage({ type: "workspace_auth_error", error: ${JSON.stringify(error ?? "unknown")} }, window.location.origin);
    setTimeout(() => window.close(), 3000);
  </script>
</body></html>`;
  }

  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Workspace Connected</title>
<style>body{font-family:system-ui;padding:40px;text-align:center;background:#1a1a1a;color:#fff}</style>
</head><body>
  <h1>✅ Google Workspace Connected</h1>
  <p>Drive, Calendar and Gmail are now accessible. You can close this window.</p>
  <script>
    window.opener?.postMessage({ type: "workspace_auth_success" }, window.location.origin);
    setTimeout(() => window.close(), 800);
  </script>
</body></html>`;
}
