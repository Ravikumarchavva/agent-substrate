/**
 * Engine authentication helpers.
 * Generates a short-lived JWT signed with ENGINE_JWT_SECRET (= agent-substrate JWT_SECRET)
 * for server-side calls to the agent-substrate API.
 *
 * Three token shapes, in descending order of preference:
 *   - Per-user token (makeUserToken/userAuthHeader) — sub is the real,
 *     stable Prisma User.id, so agent-substrate's FileMetadata.user_id /
 *     workspace routes (which scope everything by AuthClaims.sub) are
 *     actually scoped per person instead of collapsing every browser
 *     user onto the same service-account identity.
 *   - Anonymous token (makeAnonToken/anonAuthHeader) — sub is
 *     `anon-{uuid}` from the `anon_id` cookie planted by middleware.ts, so a
 *     visitor with no login still gets their own `users/{sub}/...` workspace
 *     and quota rather than sharing one with every other anonymous visitor.
 *   - Service-account token (makeEngineToken/engineAuthHeader) — fixed
 *     sub="substrate-ui". Last resort only: any request reaching it shares
 *     one identity with all others, so it must never be the normal path for
 *     user-owned data.
 */
import jwt from "jsonwebtoken";

const ENGINE_JWT_SECRET = process.env.ENGINE_JWT_SECRET ?? "";

export const ANON_COOKIE = "anon_id";

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
 * Token for a not-logged-in visitor. `anon-` prefixed (not `anon:`) because the
 * sub becomes a path segment in `users/{sub}/...` object keys and filesystem
 * paths, and a colon there is needless trouble.
 */
export function makeAnonToken(anonId: string): string {
  if (!ENGINE_JWT_SECRET) return "";
  return jwt.sign(
    {
      sub: `anon-${anonId}`,
      email: `anon-${anonId}@substrate-ui.local`,
      role: "end_user",
      type: "access",
    },
    ENGINE_JWT_SECRET,
    { expiresIn: "1h" }
  );
}

export function anonAuthHeader(anonId: string): HeadersInit {
  const token = makeAnonToken(anonId);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function cookieValue(cookieHeader: string, name: string): string | null {
  const entry = cookieHeader.split(/;\s*/).find((c) => c.startsWith(`${name}=`));
  return entry ? decodeURIComponent(entry.slice(name.length + 1)) : null;
}

/**
 * Pick the engine auth header from a raw Cookie header string: a per-user token
 * when a valid httpOnly `user_session` cookie is present, else a per-browser
 * anonymous token, else the shared service-account token. Mirrors the
 * `/api/backend/*` proxy's `authHeaderFor` so EVERY engine call (chat,
 * workspace files, …) runs under the SAME identity — otherwise the agent writes
 * files as one sub while the browser reads them as another and they 404.
 */
export function authHeaderFromCookieHeader(cookieHeader: string | null): HeadersInit {
  if (cookieHeader) {
    const raw = cookieValue(cookieHeader, "user_session");
    if (raw) {
      try {
        const session = JSON.parse(raw) as UserSession;
        if (session.id && session.email) return userAuthHeader(session);
      } catch {
        // Malformed cookie — fall through to a weaker identity.
      }
    }
    const anonId = cookieValue(cookieHeader, ANON_COOKIE);
    if (anonId) return anonAuthHeader(anonId);
  }
  return engineAuthHeader();
}
