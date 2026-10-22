/**
 * Sign-in and session, for every route handler of this app.
 *
 * The browser session is the shared backend-for-frontend library (`@substrate/oidc-bff`, ADR 0007): Keycloak authorization code + PKCE,
 * tokens in an encrypted httpOnly cookie, refresh rotation. The browser never holds an access token. The Agent API is called with the
 * user's own Keycloak access token; tenant and roles come from that token (one organization per token).
 *
 *   const auth = await requireAuth(req);                 // null -> respond 401
 *   fetch(url, { headers: auth.headers })                // Authorization: Bearer <access token>
 *   return withAuthCookies(response, auth);              // carries a rotated session cookie, if any
 *
 * Most handlers just wrap themselves in `authed(...)`, which does all three.
 */
import { createBff, type Bff } from "@loamz/oidc-bff";

export const SESSION_COOKIE = "agents_console_session";
export const LOGIN_COOKIE = "agents_console_login";

/** The app is mounted under this path (next.config.ts basePath). */
const MOUNT = "/chat";

export function oidcConfig() {
  const issuer = process.env.OIDC_ISSUER;
  const clientSecret = process.env.OIDC_CLIENT_SECRET;
  const sessionSecret = process.env.SESSION_SECRET;
  if (!issuer || !clientSecret || !sessionSecret || sessionSecret.length < 32) {
    throw new Error("Set OIDC_ISSUER, OIDC_CLIENT_SECRET and SESSION_SECRET (32+ characters)");
  }
  return {
    issuer,
    clientSecret,
    sessionSecret,
    clientId: process.env.OIDC_CLIENT_ID || "substrate-ui",
    // The address users reach the app at (origin only). Redirect URIs and cookies must agree on it.
    publicUrl: (process.env.PUBLIC_URL || "http://127.0.0.1:3000").replace(/\/$/, ""),
    // Pins the organization (tenant) of the session; unset lets Keycloak pick the user's only organization.
    organization: process.env.OIDC_ORGANIZATION || "",
  };
}

const ORGANIZATION = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$/;

function create(organization: string): Bff {
  return createBff(() => {
    const c = oidcConfig();
    return {
      issuer: c.issuer,
      clientId: c.clientId,
      clientSecret: c.clientSecret,
      sessionSecret: c.sessionSecret,
      publicUrl: c.publicUrl,
      sessionCookie: SESSION_COOKIE,
      loginCookie: LOGIN_COOKIE,
      callbackPath: `${MOUNT}/auth/callback`,
      postLogoutPath: MOUNT,
      scope: `openid profile email${organization ? ` organization:${organization}` : " organization"}`,
    };
  });
}

const sessions = new Map<string, Bff>();

/**
 * The session handler for one organization. Switching organization (project) is signing in again with `?org=<alias>`:
 * Keycloak issues a token for that single organization and the session cookie is replaced.
 */
export function bffFor(organization?: string | null): Bff {
  const alias = organization && ORGANIZATION.test(organization) ? organization : process.env.OIDC_ORGANIZATION || "";
  let found = sessions.get(alias);
  if (!found) {
    found = create(alias);
    sessions.set(alias, found);
  }
  return found;
}

/** The handler for reading the session and finishing a sign-in (the organization only matters when starting one). */
export const bff: Bff = bffFor();

export interface SessionUser {
  /** The Keycloak user id (`sub`). */
  id: string;
  email: string;
  name: string;
  isAdmin: boolean;
  /** The Keycloak organization id the session is for. */
  tenantId: string;
  roles: string[];
}

export interface Auth {
  user: SessionUser;
  /** `{ Authorization: "Bearer <access token>" }` for the Agent API. */
  headers: Record<string, string>;
  /** Set-Cookie values to append to the response (the session was refreshed). */
  setCookies: string[];
}

function claimsOf(token: string): Record<string, unknown> {
  try {
    const payload = token.split(".")[1] ?? "";
    return JSON.parse(Buffer.from(payload, "base64url").toString("utf8")) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/**
 * Who a token is for. The token came out of our own sealed cookie (it was verified by Keycloak on the way in
 * and is verified again by every service that receives it), so this only reads it for display and for routing UI.
 */
export function userFromAccessToken(token: string, fallbackName = "user"): SessionUser {
  const claims = claimsOf(token);
  const realm = claims.realm_access as { roles?: string[] } | undefined;
  const roles = Array.isArray(realm?.roles) ? realm.roles.map(String) : [];
  const organization = claims.organization;
  let tenantId = "";
  if (Array.isArray(organization)) tenantId = String(organization[0] ?? "");
  else if (organization && typeof organization === "object") {
    const first = Object.entries(organization as Record<string, { id?: string }>)[0];
    tenantId = first ? String(first[1]?.id ?? first[0]) : "";
  }
  return {
    id: String(claims.sub ?? ""),
    email: String(claims.email ?? ""),
    name: String(claims.name ?? claims.preferred_username ?? fallbackName),
    isAdmin: roles.includes("tenant_admin") || roles.includes("platform_admin"),
    tenantId,
    roles,
  };
}

/** The signed-in caller of a request, or null (answer 401). */
export async function requireAuth(req: Request): Promise<Auth | null> {
  const result = await bff.getSession(req);
  if (!result) return null;
  const { session } = result;
  return {
    user: userFromAccessToken(session.access_token, session.name),
    headers: { Authorization: `Bearer ${session.access_token}` },
    setCookies: result.setCookies,
  };
}

/** Append a refreshed session cookie, if there is one, to a response. Streams pass through untouched. */
export function withAuthCookies(res: Response, auth: Auth): Response {
  if (auth.setCookies.length === 0) return res;
  const out = new Response(res.body, { status: res.status, statusText: res.statusText, headers: new Headers(res.headers) });
  return bff.withCookies(out, auth.setCookies);
}

/**
 * Wrap a route handler: no session answers 401 problem+json (clearing the cookie); otherwise the handler gets the caller and
 * its response carries any refreshed session cookie.
 */
export function authed<C = unknown>(
  handler: (req: Request, auth: Auth, context: C) => Promise<Response>,
) {
  return async (req: Request, context: C): Promise<Response> => {
    const auth = await requireAuth(req);
    if (!auth) return bff.unauthorized(req);
    return withAuthCookies(await handler(req, auth, context), auth);
  };
}

/** The same, for handlers that need the caller to be an administrator of the organization. */
export function adminOnly<C = unknown>(
  handler: (req: Request, auth: Auth, context: C) => Promise<Response>,
) {
  return authed<C>(async (req, auth, context) => {
    if (!auth.user.isAdmin) {
      return new Response(
        JSON.stringify({
          type: "https://substrate.dev/problems/forbidden",
          title: "Forbidden",
          status: 403,
          detail: "This needs the tenant_admin role",
        }),
        { status: 403, headers: { "Content-Type": "application/problem+json" } },
      );
    }
    return handler(req, auth, context);
  });
}

/** The signed-in user of a request (read-only, for handlers that do not call the Agent API), or null. */
export async function currentUser(req: Request): Promise<SessionUser | null> {
  const result = await bff.getSession(req);
  return result ? userFromAccessToken(result.session.access_token, result.session.name) : null;
}
