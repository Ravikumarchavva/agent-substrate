/**
 * Workspace Token API
 * GET /api/workspace/token
 *   1. Proxies to backend (which holds the token after frontend OAuth push).
 *   2. Falls back to Prisma if backend has lost the token (e.g. restart).
 * DELETE /api/workspace/token — clears workspace token on backend and Prisma.
 */
import { NextRequest, NextResponse } from "next/server";
import { getCredentialManager } from "@/lib/credentials";
import { prisma } from "@/lib/prisma";
import { BACKEND_API } from "@/lib/backend";
import { requireAuth, withAuthCookies, type Auth } from "@/lib/session";
const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";

interface WorkspaceTokenPayload {
  access_token: string;
  refresh_token: string | null;
  expires_in: number;
}

export async function GET(req: NextRequest): Promise<Response> {
  const auth = await requireAuth(req);
  const res = await getToken(auth);
  return auth ? withAuthCookies(res, auth) : res;
}

async function getToken(session: Auth | null): Promise<NextResponse> {
  try {
    // ── 1. Try backend first (only meaningful with a real session — the
    // route is scoped by caller identity) ──────────────────────────────────
    if (session) {
      const backendRes = await fetch(`${BACKEND_API}/auth/workspace/token`, {
        headers: { Accept: "application/json", ...session.headers },
      });
      if (backendRes.ok) {
        const data = (await backendRes.json()) as { access_token: string };
        return NextResponse.json({ access_token: data.access_token, connected: true });
      }
    }

    // ── 2. Backend has no token — try Prisma DB ───────────────────────────────
    if (!session) {
      return NextResponse.json({ connected: false }, { status: 401 });
    }
    const userId = session.user.id;

    const cm = getCredentialManager();
    const cred = await prisma.userCredential.findUnique({
      where: { userId_provider: { userId, provider: "google_workspace" } },
      select: { accessToken: true, refreshToken: true, expiresAt: true },
    });

    if (!cred) {
      return NextResponse.json({ connected: false }, { status: 401 });
    }

    let accessToken = cm.decrypt(cred.accessToken);
    let refreshToken = cred.refreshToken ? cm.decrypt(cred.refreshToken) : null;
    let expiresIn = 3600;

    // Refresh if expired
    if (cred.expiresAt && cred.expiresAt <= new Date()) {
      if (!refreshToken) {
        return NextResponse.json({ connected: false, error: "Token expired" }, { status: 401 });
      }
      const refreshed = await refreshGoogleToken(refreshToken);
      if (!refreshed) {
        return NextResponse.json({ connected: false, error: "Token refresh failed" }, { status: 401 });
      }
      accessToken = refreshed.access_token;
      if (refreshed.refresh_token) refreshToken = refreshed.refresh_token;
      expiresIn = refreshed.expires_in ?? 3600;
      try {
        await cm.storeCredential(userId, "google_workspace", accessToken, refreshToken ?? "", expiresIn);
      } catch (err) {
        console.error("[Workspace Token] Failed to persist refreshed token:", err);
      }
    }

    const mirrored = await mirrorWorkspaceTokenToBackend(session, {
      access_token: accessToken,
      refresh_token: refreshToken,
      expires_in: expiresIn,
    });
    if (!mirrored) {
      return NextResponse.json(
        {
          connected: false,
          error: "Google Workspace token could not be restored to the backend.",
        },
        { status: 502 },
      );
    }

    return NextResponse.json({ access_token: accessToken, connected: true });
  } catch (err) {
    console.error("[Workspace Token] Error:", err);
    return NextResponse.json({ connected: false, error: "Internal server error" }, { status: 500 });
  }
}

export async function DELETE(req: NextRequest): Promise<Response> {
  const session = await requireAuth(req);
  const res = await clearToken(session);
  return session ? withAuthCookies(res, session) : res;
}

async function clearToken(session: Auth | null): Promise<NextResponse> {
  // Clear from backend (Redis)
  if (session) {
    try {
      await fetch(`${BACKEND_API}/auth/workspace/token`, {
        method: "DELETE",
        headers: session.headers,
      });
    } catch (err) {
      console.error("[Workspace Token] Failed to clear backend token:", err);
    }
  }

  // Also clear from Prisma so the fallback path doesn't re-connect
  try {
    if (session) {
      await prisma.userCredential.deleteMany({
        where: { userId: session.user.id, provider: "google_workspace" },
      });
    }
  } catch (err) {
    console.error("[Workspace Token] Failed to clear Prisma token:", err);
  }

  return NextResponse.json({ success: true });
}

async function refreshGoogleToken(
  refreshToken: string,
): Promise<{ access_token: string; refresh_token?: string; expires_in?: number } | null> {
  try {
    const res = await fetch(GOOGLE_TOKEN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: process.env.GOOGLE_CLIENT_ID!,
        client_secret: process.env.GOOGLE_CLIENT_SECRET!,
        refresh_token: refreshToken,
        grant_type: "refresh_token",
      }),
    });
    if (!res.ok) return null;
    return (await res.json()) as { access_token: string; refresh_token?: string; expires_in?: number };
  } catch (err) {
    console.error("[Workspace Token] Failed to refresh Google token:", err);
    return null;
  }
}

async function mirrorWorkspaceTokenToBackend(
  session: Auth,
  payload: WorkspaceTokenPayload,
): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND_API}/auth/workspace/set-token`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...session.headers },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      console.error(
        "[Workspace Token] Backend mirror failed:",
        res.status,
        await res.text(),
      );
      return false;
    }
    return true;
  } catch (err) {
    console.error("[Workspace Token] Failed to mirror token to backend:", err);
    return false;
  }
}
