/**
 * Spotify Token API
 * GET /api/spotify/token
 *   Reads from httpOnly cookies first, then Prisma DB (for post-restart recovery).
 *   When a valid token is found from DB it is pushed to the engine so subsequent
 *   MCP app calls (playlists, liked-songs) work without re-auth.
 * DELETE /api/spotify/token — clears session cookies only.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getCredentialManager } from '@/lib/credentials';
import { prisma } from '@/lib/prisma';
import { getSessionFromRequest } from '@/lib/session';

const BACKEND_URL = process.env.BACKEND_API_URL ?? 'http://localhost:8000';
const SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token';

export async function GET(req: NextRequest) {
  try {
    // ── 1. Cookie (fastest — set by /api/spotify/callback) ───────────────────
    const cookieToken = req.cookies.get('spotify_access_token')?.value;
    if (cookieToken) {
      // Silently push to engine so MCP app playlists/liked-songs work
      pushToEngine(cookieToken, req.cookies.get('spotify_refresh_token')?.value ?? null, 3600);
      return NextResponse.json({ access_token: cookieToken, authenticated: true });
    }

    // ── 2. Prisma DB (survives cookie expiry / restart) ──────────────────────
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
      try {
        await cm.storeCredential(userId, 'spotify', accessToken, refreshToken ?? '', expiresIn);
      } catch { /* non-fatal */ }
    }

    // Push to engine so MCP app calls work
    pushToEngine(accessToken, refreshToken, expiresIn);

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

function pushToEngine(accessToken: string, refreshToken: string | null, expiresIn: number): void {
  fetch(`${BACKEND_URL}/auth/spotify/set-token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ access_token: accessToken, refresh_token: refreshToken, expires_in: expiresIn }),
  }).catch(() => { /* non-fatal — engine may not be running */ });
}

async function resolveUserId(req: NextRequest): Promise<string | null> {
  const session = await getSessionFromRequest(req);
  return session?.id ?? null;
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
