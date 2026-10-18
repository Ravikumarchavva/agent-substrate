import { cookies } from 'next/headers';
import type { NextRequest } from 'next/server';
import prisma from './prisma';

export const SESSION_COOKIE_NAME = 'session_token';
const SESSION_DURATION_MS = 30 * 24 * 60 * 60 * 1000; // 30 days

export interface SessionUser {
  id: string;
  email: string;
  name?: string;
  avatarUrl?: string;
  isAdmin: boolean;
}

/**
 * Create a DB-backed session (Prisma `Session` row) and set the opaque
 * `session_token` cookie on `res`. This is the ONLY thing that establishes
 * identity — the cookie carries nothing but a random token; the server
 * looks up who it belongs to on every request (see `getSession`/
 * `getSessionFromRequest`). Replaces the old `user_session` cookie, which
 * was plain unsigned JSON an attacker could edit in devtools to become any
 * user (including an admin).
 */
export async function createSession(
  res: { cookies: { set: (name: string, value: string, opts: Record<string, unknown>) => void } },
  user: { id: string; email: string },
): Promise<string> {
  const token = crypto.randomUUID();
  const expiresAt = new Date(Date.now() + SESSION_DURATION_MS);

  await prisma.session.create({
    data: { userId: user.id, token, expiresAt },
  });

  res.cookies.set(SESSION_COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    expires: expiresAt,
  });

  return token;
}

function toSessionUser(user: {
  id: string;
  email: string;
  name: string | null;
  avatarUrl: string | null;
  isAdmin: boolean;
}): SessionUser {
  return {
    id: user.id,
    email: user.email,
    name: user.name ?? undefined,
    avatarUrl: user.avatarUrl ?? undefined,
    isAdmin: user.isAdmin,
  };
}

async function resolveToken(token: string | undefined | null): Promise<SessionUser | null> {
  if (!token) return null;
  const session = await prisma.session.findUnique({
    where: { token },
    include: { user: true },
  });
  if (!session) return null;
  if (session.expiresAt < new Date()) {
    await prisma.session.delete({ where: { id: session.id } }).catch(() => {});
    return null;
  }
  return toSessionUser(session.user);
}

/** For Server Components / Route Handlers reading cookies via `next/headers`. */
export async function getSession(): Promise<SessionUser | null> {
  const token = (await cookies()).get(SESSION_COOKIE_NAME)?.value;
  return resolveToken(token);
}

/** For Route Handlers that received a `NextRequest` directly. */
export async function getSessionFromRequest(req: NextRequest): Promise<SessionUser | null> {
  return resolveToken(req.cookies.get(SESSION_COOKIE_NAME)?.value);
}

/** For code that only has a raw `Cookie` header string (e.g. a request
 *  forwarded from elsewhere) rather than a `NextRequest`. */
export async function getSessionFromCookieHeader(
  cookieHeader: string | null,
): Promise<SessionUser | null> {
  if (!cookieHeader) return null;
  const entry = cookieHeader
    .split(/;\s*/)
    .find((c) => c.startsWith(`${SESSION_COOKIE_NAME}=`));
  if (!entry) return null;
  return resolveToken(decodeURIComponent(entry.slice(SESSION_COOKIE_NAME.length + 1)));
}

export async function deleteSessionByToken(token: string | undefined | null): Promise<void> {
  if (!token) return;
  await prisma.session.deleteMany({ where: { token } }).catch(() => {});
}

/** Delete every session for a user — used when isAdmin or other identity
 *  fields change, so a stale session can't keep an old isAdmin value. */
export async function deleteAllSessionsForUser(userId: string): Promise<void> {
  await prisma.session.deleteMany({ where: { userId } }).catch(() => {});
}

export async function getOrCreateUser(
  email: string,
  googleId?: string,
  name?: string,
  avatarUrl?: string,
  isAdmin?: boolean,
): Promise<SessionUser> {
  const adminEmail = process.env.ADMIN_EMAIL?.toLowerCase().trim();
  const resolvedIsAdmin = isAdmin ?? (!!adminEmail && adminEmail === email.toLowerCase().trim());

  let user = await prisma.user.findFirst({
    where: { OR: [{ email }, ...(googleId ? [{ googleId }] : [])] },
  });

  if (!user) {
    user = await prisma.user.create({
      data: { email, googleId, name, avatarUrl, isAdmin: resolvedIsAdmin },
    });
  } else {
    user = await prisma.user.update({
      where: { id: user.id },
      data: {
        googleId: googleId || user.googleId,
        name: name || user.name,
        avatarUrl: avatarUrl || user.avatarUrl,
        isAdmin: resolvedIsAdmin,
      },
    });
  }

  return toSessionUser(user);
}
