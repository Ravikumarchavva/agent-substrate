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

const BACKEND_URL = process.env.BACKEND_API_URL ?? "http://localhost:8000";
const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";

export async function GET(req: NextRequest): Promise<NextResponse> {
  try {
    // ── 1. Try backend first ──────────────────────────────────────────────────
    const backendRes = await fetch(`${BACKEND_URL}/auth/workspace/token`, {
      headers: { Accept: "application/json" },
    });
    if (backendRes.ok) {
      const data = (await backendRes.json()) as { access_token: string };
      return NextResponse.json({ access_token: data.access_token, connected: true });
    }

    // ── 2. Backend has no token — try Prisma DB ───────────────────────────────
    const userId = await resolveUserId(req);
    if (!userId) {
      return NextResponse.json({ connected: false }, { status: 401 });
    }

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
      } catch { /* non-fatal */ }
    }

    // Re-push to backend
    fetch(`${BACKEND_URL}/auth/workspace/set-token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ access_token: accessToken, refresh_token: refreshToken, expires_in: expiresIn }),
    }).catch(() => { /* non-fatal */ });

    return NextResponse.json({ access_token: accessToken, connected: true });
  } catch (err) {
    console.error("[Workspace Token] Error:", err);
    return NextResponse.json({ connected: false, error: "Internal server error" }, { status: 500 });
  }
}

export async function DELETE(): Promise<NextResponse> {
  // Clear from backend
  fetch(`${BACKEND_URL}/auth/workspace/token`, { method: "DELETE" }).catch(() => { /* non-fatal */ });
  return NextResponse.json({ success: true });
}

async function resolveUserId(req: NextRequest): Promise<string | null> {
  try {
    const userCookie = req.cookies.get("google_user")?.value;
    if (!userCookie) return null;
    const userData = JSON.parse(decodeURIComponent(userCookie)) as { email?: string };
    if (!userData.email) return null;
    const dbUser = await prisma.user.findUnique({
      where: { email: userData.email },
      select: { id: true },
    });
    return dbUser?.id ?? null;
  } catch {
    return null;
  }
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
  } catch {
    return null;
  }
}
