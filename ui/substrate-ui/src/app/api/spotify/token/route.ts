/**
 * Spotify Token API
 * GET /api/spotify/token
 * Returns the user's Spotify access token from httpOnly cookies (auto-refreshes
 * if the access token is missing but a refresh token is present).
 * Cookie-based — no Prisma session required, consistent with Google OAuth flow.
 */
import { NextRequest, NextResponse } from 'next/server';

const SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token';

export async function GET(req: NextRequest) {
  try {
    const accessToken = req.cookies.get('spotify_access_token')?.value;
    const refreshToken = req.cookies.get('spotify_refresh_token')?.value;

    // Valid access token present — return immediately
    if (accessToken) {
      return NextResponse.json({
        access_token: accessToken,
        authenticated: true,
      });
    }

    // No access token but we have a refresh token — try to refresh
    if (refreshToken) {
      const refreshed = await refreshAccessToken(refreshToken);
      if (refreshed) {
        const res = NextResponse.json({
          access_token: refreshed.access_token,
          authenticated: true,
        });
        res.cookies.set('spotify_access_token', refreshed.access_token, {
          httpOnly: true,
          maxAge: refreshed.expires_in || 3600,
          path: '/',
          sameSite: 'lax',
          secure: process.env.NODE_ENV === 'production',
        });
        if (refreshed.refresh_token) {
          res.cookies.set('spotify_refresh_token', refreshed.refresh_token, {
            httpOnly: true,
            maxAge: 60 * 60 * 24 * 30,
            path: '/',
            sameSite: 'lax',
            secure: process.env.NODE_ENV === 'production',
          });
        }
        return res;
      }
      // Refresh failed — clear stale cookies
      const res = NextResponse.json(
        { authenticated: false, error: 'Spotify not connected' },
        { status: 401 }
      );
      res.cookies.delete('spotify_access_token');
      res.cookies.delete('spotify_refresh_token');
      return res;
    }

    return NextResponse.json(
      { authenticated: false, error: 'Spotify not connected' },
      { status: 401 }
    );
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

async function refreshAccessToken(
  refreshToken: string
): Promise<{ access_token: string; refresh_token?: string; expires_in: number } | null> {
  try {
    const clientId = process.env.SPOTIFY_CLIENT_ID!;
    const clientSecret = process.env.SPOTIFY_CLIENT_SECRET!;
    const basic = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');

    const tokenRes = await fetch(SPOTIFY_TOKEN_URL, {
      method: 'POST',
      headers: {
        Authorization: `Basic ${basic}`,
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: new URLSearchParams({
        grant_type: 'refresh_token',
        refresh_token: refreshToken,
      }),
    });

    if (!tokenRes.ok) {
      console.error('[Spotify Token] Refresh failed:', await tokenRes.text());
      return null;
    }

    return await tokenRes.json();
  } catch (error) {
    console.error('[Spotify Token] Refresh error:', error);
    return null;
  }
}
