import { cookies } from 'next/headers';
import { v4 as uuidv4 } from 'uuid';
import prisma from './prisma';

const SESSION_COOKIE_NAME = 'session_token';
const SESSION_DURATION = 30 * 24 * 60 * 60 * 1000; // 30 days

export interface SessionUser {
  id: string;
  email: string;
  name?: string;
  avatarUrl?: string;
}

export async function createSession(user: SessionUser): Promise<string> {
  const token = uuidv4();
  const expiresAt = new Date(Date.now() + SESSION_DURATION);

  await prisma.session.create({
    data: {
      userId: user.id,
      token,
      expiresAt,
    },
  });

  (await cookies()).set(SESSION_COOKIE_NAME, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    expires: expiresAt,
  });

  return token;
}

export async function getSession(): Promise<SessionUser | null> {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;

  if (!token) {
    return null;
  }

  const session = await prisma.session.findUnique({
    where: { token },
    include: { user: true },
  });

  if (!session || session.expiresAt < new Date()) {
    // Session expired or not found
    if (session) {
      await prisma.session.delete({ where: { id: session.id } });
    }
    return null;
  }

  return {
    id: session.user.id,
    email: session.user.email,
    name: session.user.name || undefined,
    avatarUrl: session.user.avatarUrl || undefined,
  };
}

export async function deleteSession(): Promise<void> {
  const cookieStore = await cookies();
  const token = cookieStore.get(SESSION_COOKIE_NAME)?.value;

  if (token) {
    await prisma.session.deleteMany({ where: { token } });
    cookieStore.delete(SESSION_COOKIE_NAME);
  }
}

export async function getOrCreateUser(
  email: string,
  googleId?: string,
  name?: string,
  avatarUrl?: string
): Promise<SessionUser> {
  // Try to find existing user
  let user = await prisma.user.findFirst({
    where: {
      OR: [{ email }, ...(googleId ? [{ googleId }] : [])],
    },
  });

  if (!user) {
    // Create new user
    user = await prisma.user.create({
      data: {
        email,
        googleId,
        name,
        avatarUrl,
      },
    });
  } else {
    // Update user info if changed
    user = await prisma.user.update({
      where: { id: user.id },
      data: {
        googleId: googleId || user.googleId,
        name: name || user.name,
        avatarUrl: avatarUrl || user.avatarUrl,
      },
    });
  }

  return {
    id: user.id,
    email: user.email,
    name: user.name || undefined,
    avatarUrl: user.avatarUrl || undefined,
  };
}
