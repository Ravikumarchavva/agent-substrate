/**
 * Platform SSO – GET /api/auth/platform
 *
 * agent-substrate-platform (the SaaS control plane) and this app now share
 * one origin (see next.config.ts's basePath + the platform's /chat rewrite),
 * so the browser already sends the platform's Auth.js session cookie along
 * with every request here — we just can't decode it ourselves without
 * reimplementing Auth.js's own JWE encoding. Instead we ask the platform
 * directly: Auth.js exposes GET /api/auth/session out of the box, reading
 * from whatever cookie the request carries. Forwarding this request's
 * Cookie header to that endpoint is the standard "ask the identity
 * provider" pattern (the same shape as an OIDC userinfo call) — no shared
 * secret, no crypto to keep in sync between the two apps.
 *
 * On a valid platform session we upsert-by-email into this app's own User
 * table (the exact same upsert the Google OAuth callback already does) and
 * set the same google_user/user_session cookies it sets — so every
 * downstream consumer (AuthContext, engine-auth.ts, admin checks) needs no
 * changes at all. The platform account and the local account are the same
 * row, joined on email; nothing is duplicated as a separate identity.
 */
import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const PLATFORM_URL = process.env.PLATFORM_URL || "http://localhost:3000";

type PlatformSession = {
  user?: { email?: string; name?: string; image?: string };
};

export async function GET(req: NextRequest) {
  const cookie = req.headers.get("cookie");
  if (!cookie) {
    return NextResponse.json({ authenticated: false });
  }

  let session: PlatformSession;
  try {
    const res = await fetch(`${PLATFORM_URL}/api/auth/session`, {
      headers: { cookie },
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json({ authenticated: false });
    }
    session = await res.json();
  } catch (err) {
    console.error("[Platform SSO] Failed to reach platform session endpoint:", err);
    return NextResponse.json({ authenticated: false });
  }

  const email = session.user?.email;
  if (!email) {
    return NextResponse.json({ authenticated: false });
  }

  const adminEmail = process.env.ADMIN_EMAIL?.toLowerCase().trim();
  const isAdmin = !!adminEmail && adminEmail === email.toLowerCase().trim();
  let dbUserId: string;
  try {
    const dbUser = await prisma.user.upsert({
      where: { email },
      update: {
        name: session.user?.name ?? undefined,
        avatarUrl: session.user?.image ?? undefined,
        isAdmin,
      },
      create: {
        email,
        name: session.user?.name ?? undefined,
        avatarUrl: session.user?.image ?? undefined,
        isAdmin,
      },
    });
    dbUserId = dbUser.id;
  } catch (err) {
    console.error("[Platform SSO] Failed to upsert user:", err);
    return NextResponse.json({ authenticated: false });
  }

  const res = NextResponse.json({ authenticated: true });

  // Readable by the client (UI display) — same shape as the Google login flow.
  res.cookies.set(
    "google_user",
    JSON.stringify({
      email,
      name: session.user?.name,
      picture: session.user?.image,
      isAdmin,
    }),
    {
      httpOnly: false,
      maxAge: 60 * 60 * 24 * 365,
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    },
  );

  // httpOnly trust boundary used by the /api/backend proxy to mint a
  // per-user engine JWT — see src/lib/engine-auth.ts::makeUserToken.
  res.cookies.set(
    "user_session",
    JSON.stringify({ id: dbUserId, email, isAdmin }),
    {
      httpOnly: true,
      maxAge: 60 * 60 * 24 * 365,
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    },
  );

  return res;
}
