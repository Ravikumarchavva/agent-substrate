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
import { userAuthHeader, type UserSession } from "@/lib/engine-auth";
import { getSessionFromRequest } from "@/lib/session";

const BACKEND_URL = process.env.BACKEND_API_URL ?? "http://localhost:8000";
const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";

interface WorkspaceTokenPayload {
  access_token: string;
  refresh_token: string | null;
  expires_in: number;
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  try {
    const session = await resolveUserSession(req);

    // ── 1. Try backend first (only meaningful with a real session — the
    // route is scoped by caller identity) ──────────────────────────────────
    if (session) {
      const backendRes = await fetch(`${BACKEND_URL}/auth/workspace/token`, {
        headers: { Accept: "application/json", ...userAuthHeader(session) },
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
    const userId = session.id;

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

export async function DELETE(req: NextRequest): Promise<NextResponse> {
  const session = await resolveUserSession(req);

  // Clear from backend (Redis)
  if (session) {
    try {
      await fetch(`${BACKEND_URL}/auth/workspace/token`, {
        method: "DELETE",
        headers: userAuthHeader(session),
      });
    } catch (err) {
      console.error("[Workspace Token] Failed to clear backend token:", err);
    }
  }

  // Also clear from Prisma so the fallback path doesn't re-connect
  try {
    if (session) {
      await prisma.userCredential.deleteMany({
        where: { userId: session.id, provider: "google_workspace" },
      });
    }
  } catch (err) {
    console.error("[Workspace Token] Failed to clear Prisma token:", err);
  }

  return NextResponse.json({ success: true });
}

// The DB-backed session (src/lib/session.ts) is the only thing that
// determines identity here — no cookie-derived fallback.
async function resolveUserSession(req: NextRequest): Promise<UserSession | null> {
  const session = await getSessionFromRequest(req);
  if (!session) return null;
  return { id: session.id, email: session.email, isAdmin: session.isAdmin };
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
  session: UserSession,
  payload: WorkspaceTokenPayload,
): Promise<boolean> {
  try {
    const res = await fetch(`${BACKEND_URL}/auth/workspace/set-token`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...userAuthHeader(session) },
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
