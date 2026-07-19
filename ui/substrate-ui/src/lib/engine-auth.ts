/**
 * Engine authentication helpers.
 * Generates a short-lived JWT signed with ENGINE_JWT_SECRET (= agent-substrate JWT_SECRET)
 * for server-side calls to the agent-substrate API.
 *
 * Two token shapes:
 *   - Service-account token (makeEngineToken/engineAuthHeader) — fixed
 *     sub="substrate-ui", used when no user session is present.
 *   - Per-user token (makeUserToken/userAuthHeader) — sub is the real,
 *     stable Prisma User.id, so agent-substrate's FileMetadata.user_id /
 *     workspace routes (which scope everything by AuthClaims.sub) are
 *     actually scoped per person instead of collapsing every browser
 *     user onto the same service-account identity.
 */
import jwt from "jsonwebtoken";

const ENGINE_JWT_SECRET = process.env.ENGINE_JWT_SECRET ?? "";

export function makeEngineToken(): string {
  if (!ENGINE_JWT_SECRET) return "";
  return jwt.sign(
    { sub: "substrate-ui", email: "ui@substrate-ui.local", type: "access" },
    ENGINE_JWT_SECRET,
    { expiresIn: "1h" }
  );
}

export function engineAuthHeader(): HeadersInit {
  const token = makeEngineToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export interface UserSession {
  id: string;
  email: string;
  isAdmin?: boolean;
}

export function makeUserToken(user: UserSession): string {
  if (!ENGINE_JWT_SECRET) return "";
  return jwt.sign(
    {
      sub: user.id,
      email: user.email,
      role: user.isAdmin ? "platform_admin" : "end_user",
      type: "access",
    },
    ENGINE_JWT_SECRET,
    { expiresIn: "1h" }
  );
}

export function userAuthHeader(user: UserSession): HeadersInit {
  const token = makeUserToken(user);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Pick the engine auth header from a raw Cookie header string: a per-user
 * token when a valid httpOnly `user_session` cookie is present, else the
 * service-account token. Mirrors the `/api/backend/*` proxy's `authHeaderFor`
 * so EVERY engine call (chat, workspace files, …) runs under the SAME
 * identity — otherwise the agent writes files as `substrate-ui` while the
 * browser reads them as the real user (or vice-versa) and they 404.
 */
export function authHeaderFromCookieHeader(cookieHeader: string | null): HeadersInit {
  if (cookieHeader) {
    const entry = cookieHeader
      .split(/;\s*/)
      .find((c) => c.startsWith("user_session="));
    if (entry) {
      try {
        const raw = decodeURIComponent(entry.slice("user_session=".length));
        const session = JSON.parse(raw) as UserSession;
        if (session.id && session.email) return userAuthHeader(session);
      } catch {
        // Malformed cookie — fall through to the service-account token.
      }
    }
  }
  return engineAuthHeader();
}
