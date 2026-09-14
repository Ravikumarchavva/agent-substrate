/**
 * Engine authentication helpers.
 * Generates a short-lived JWT signed with ENGINE_JWT_SECRET (= agent-substrate JWT_SECRET)
 * for server-side calls to the agent-substrate API.
 *
 * Identity is resolved from the DB-backed session (see src/lib/session.ts) —
 * the `session_token` cookie is an opaque random token, looked up against
 * the Prisma `Session` table on every request. There is deliberately no
 * anonymous identity anymore: `requireUserAuthHeader` returns `null` when
 * there's no valid session, and every route that touches user data must
 * respond 401 in that case rather than falling back to a shared or
 * per-browser identity (the previous anon/service-account fallback chain
 * is what let a request act as someone other than who the UI displayed as
 * logged in — see the beta security audit this replaces).
 */
import jwt from "jsonwebtoken";
import type { NextRequest } from "next/server";
import { getSessionFromCookieHeader } from "@/lib/session";

const ENGINE_JWT_SECRET = process.env.ENGINE_JWT_SECRET ?? "";

// Set by ravi at deploy time when this instance belongs to a project (see
// docs/claude_docs/roadmap.md's "Explicitly deferred" entry on real Instance
// provisioning) — scopes agent-substrate's per-tenant rate limiting so every
// visitor to THIS deployed chatbot shares one project-level quota instead of
// each getting their own.
const RAVI_PROJECT_ID = process.env.RAVI_PROJECT_ID ?? "";

// Every token needs a non-empty tenant_id (agent-substrate rejects tokens
// without one) — this is the fallback for local dev / a standalone instance
// that isn't provisioned through the SaaS platform.
const TENANT_ID = RAVI_PROJECT_ID || "substrate-ui-local";

/**
 * Service-account token — fixed sub="substrate-ui". For genuine
 * server-to-server calls only (nothing that scopes data to a person). Never
 * used as an automatic fallback for a request that's missing a user
 * session; those must 401 instead.
 */
export function makeEngineToken(): string {
  if (!ENGINE_JWT_SECRET) return "";
  return jwt.sign(
    {
      sub: "substrate-ui",
      email: "ui@substrate-ui.local",
      type: "access",
      tenant_id: TENANT_ID,
    },
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

/**
 * `sub` is the real, stable Prisma `User.id`, so agent-substrate's
 * FileMetadata.user_id / workspace routes (which scope everything by
 * `AuthClaims.sub`) are scoped per person. `role` is `tenant_admin`, not
 * `platform_admin` — this deployment's admin is scoped to its own tenant;
 * `platform_admin` is reserved for genuine cross-tenant/platform operators,
 * which substrate-ui itself never mints (see rls_deps.py / thread_service.py
 * on the backend, which only fully bypass tenant scoping for
 * `platform_admin`).
 */
export function makeUserToken(user: UserSession): string {
  if (!ENGINE_JWT_SECRET) return "";
  return jwt.sign(
    {
      sub: user.id,
      email: user.email,
      role: user.isAdmin ? "tenant_admin" : "end_user",
      type: "access",
      tenant_id: TENANT_ID,
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
 * The one place every route resolves "who is this request from" — looks up
 * the DB-backed session from a raw Cookie header (works for both
 * `NextRequest` and plain `Request`, since both expose `headers.get`).
 * Returns `null` when there's no valid session; callers MUST respond 401
 * rather than falling back to any other identity.
 */
export async function requireUserAuthHeader(
  cookieHeader: string | null,
): Promise<{ headers: HeadersInit; user: UserSession } | null> {
  const session = await getSessionFromCookieHeader(cookieHeader);
  if (!session) return null;
  const user: UserSession = { id: session.id, email: session.email, isAdmin: session.isAdmin };
  return { headers: userAuthHeader(user), user };
}

/** Convenience wrapper for routes that already have a `NextRequest`. */
export async function requireUserAuthHeaderFromRequest(
  req: NextRequest,
): Promise<{ headers: HeadersInit; user: UserSession } | null> {
  return requireUserAuthHeader(req.headers.get("cookie"));
}
