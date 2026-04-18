/**
 * Spotify Token API
 * GET /api/spotify/token
 *   1. Proxies to backend (which holds the token after frontend OAuth push).
 *   2. Falls back to Prisma DB if backend has lost the token (e.g. restart).
 *      When a DB token is found it is re-pushed to the backend so subsequent
 *      calls skip the fallback.
 * DELETE /api/spotify/token — clears session cookies only.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getCredentialManager } from '@/lib/credentials';
import { prisma } from '@/lib/prisma';

const BACKEND_URL = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
const SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token';

export async function GET(req: NextRequest) {
  try {
    // ── 1. Try backend (fastest path, always works after OAuth push) ──────────
    const backendRes = await fetch(`${BACKEND_URL}/auth/spotify/token`, {
      headers: { Accept: 'application/json' },
    });
    if (backendRes.ok) {
      const data = (await backendRes.json()) as { access_token: string };
      return NextResponse.json({ access_token: data.access_token, authenticated: true });
    }

    // ── 2. Backend has no token (restart?) — try Prisma DB ───────────────────
    const userId = await resolveUserId(req);
    if (!userId) {
      return NextResponse.json(
        { authenticated: false, error: 'Spotify not connected' },
        { status: 401 }
      );
    }

    const cm = getCredentialManager();
    const cred = await prisma.userCredential.findUnique({
      where: { userId_provider: { userId, provider: 'spotify' } },
      select: { accessToken: true, refreshToken: true, expiresAt: true },
    });

    if (!cred) {
      return NextResponse.json(
        { authenticated: false, error: 'Spotify not connected' },
        { status: 401 }
      );
    }

    let accessToken = cm.decrypt(cred.accessToken);
    let refreshToken = cred.refreshToken ? cm.decrypt(cred.refreshToken) : null;
    let expiresIn = 3600;

    // Refresh if expired
    if (cred.expiresAt && cred.expiresAt <= new Date()) {
      if (!refreshToken) {
        return NextResponse.json(
          { authenticated: false, error: 'Spotify token expired' },
          { status: 401 }
        );
      }
      const refreshed = await refreshAccessToken(refreshToken);
      if (!refreshed) {
        return NextResponse.json(
          { authenticated: false, error: 'Spotify token refresh failed' },
          { status: 401 }
        );
      }
      accessToken = refreshed.access_token;
      if (refreshed.refresh_token) refreshToken = refreshed.refresh_token;
      expiresIn = refreshed.expires_in ?? 3600;
      // Persist updated token
      try {
        await cm.storeCredential(userId, 'spotify', accessToken, refreshToken ?? '', expiresIn);
      } catch { /* non-fatal */ }
    }

    // Re-push to backend so subsequent calls are fast
    fetch(`${BACKEND_URL}/auth/spotify/set-token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: accessToken, refresh_token: refreshToken, expires_in: expiresIn }),
    }).catch(() => { /* non-fatal */ });

    return NextResponse.json({ access_token: accessToken, authenticated: true });
  } catch (error) {
    console.error('[Spotify Token] Error:', error);
    return NextResponse.json(
      { authenticated: false, error: 'Internal server error' },
      { status: 500 }
    );
  }
}

export async function DELETE() {
  const res = NextResponse.json({ success: true });
  res.cookies.delete('spotify_access_token');
  res.cookies.delete('spotify_refresh_token');
  return res;
}

async function resolveUserId(req: NextRequest): Promise<string | null> {
  try {
    const userCookie = req.cookies.get('google_user')?.value;
    if (!userCookie) return null;
    const userData = JSON.parse(decodeURIComponent(userCookie)) as { email?: string };
    if (!userData.email) return null;
    const dbUser = await prisma.user.findUnique({ where: { email: userData.email }, select: { id: true } });
    return dbUser?.id ?? null;
  } catch {
    return null;
  }
}

async function refreshAccessToken(
  refreshToken: string,
): Promise<{ access_token: string; refresh_token?: string; expires_in?: number } | null> {
  try {
    const clientId = process.env.SPOTIFY_CLIENT_ID!;
    const clientSecret = process.env.SPOTIFY_CLIENT_SECRET!;
    const basic = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
    const res = await fetch(SPOTIFY_TOKEN_URL, {
      method: 'POST',
      headers: { Authorization: `Basic ${basic}`, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ grant_type: 'refresh_token', refresh_token: refreshToken }),
    });
    if (!res.ok) return null;
    return await res.json() as { access_token: string; refresh_token?: string; expires_in?: number };
  } catch {
    return null;
  }
}
