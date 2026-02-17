/**
 * Spotify Token API
 * GET /api/spotify/token
 * Returns the user's Spotify access token from database (decrypted, auto-refreshes)
 */
import { NextResponse } from 'next/server';
import { getSession } from '@/lib/session';
import { getCredentialManager } from '@/lib/credentials';
import prisma from '@/lib/prisma';

const SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token';

export async function GET() {
  try {
    const user = await getSession();
    
    if (!user) {
      return NextResponse.json(
        { authenticated: false, error: 'Not authenticated' },
        { status: 401 }
      );
    }

    const credentialManager = getCredentialManager();
    let credential = await credentialManager.getCredential(user.id, 'spotify');

    // If expired or not found, try to refresh
    if (!credential) {
      const refreshed = await refreshSpotifyToken(user.id);
      if (refreshed) {
        credential = await credentialManager.getCredential(user.id, 'spotify');
      }
    }

    if (!credential) {
      return NextResponse.json(
        { authenticated: false, error: 'Spotify not connected' },
        { status: 404 }
      );
    }

    return NextResponse.json({
      access_token: credential.accessToken,
      authenticated: true,
      expires_at: credential.expiresAt.toISOString(),
    });
  } catch (error) {
    console.error('[Spotify Token] Error:', error);
    return NextResponse.json(
      { authenticated: false, error: 'Internal server error' },
      { status: 500 }
    );
  }
}

export async function DELETE() {
  try {
    const user = await getSession();
    
    if (!user) {
      return NextResponse.json(
        { error: 'Not authenticated' },
        { status: 401 }
      );
    }

    const credentialManager = getCredentialManager();
    await credentialManager.deleteCredential(user.id, 'spotify');

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error('[Spotify Token] Delete error:', error);
    return NextResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }
}

async function refreshSpotifyToken(userId: string): Promise<boolean> {
  try {
    // Get encrypted refresh token from database
    const credential = await prisma.userCredential.findUnique({
      where: {
        userId_provider: {
          userId,
          provider: 'spotify',
        },
      },
    });

    if (!credential || !credential.refreshToken) {
      return false;
    }

    const credentialManager = getCredentialManager();
    const refreshToken = credentialManager.decrypt(credential.refreshToken);

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
      return false;
    }

    const data = await tokenRes.json();
    
    // Store new credentials
    await credentialManager.storeCredential(
      userId,
      'spotify',
      data.access_token,
      data.refresh_token || refreshToken,
      data.expires_in || 3600,
      data.scope
    );

    return true;
  } catch (error) {
    console.error('[Spotify Token] Refresh error:', error);
    return false;
  }
}
