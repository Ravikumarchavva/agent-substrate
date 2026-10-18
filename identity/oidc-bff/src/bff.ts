// Backend-for-frontend session for Keycloak: Authorization Code + PKCE against a confidential client.
// Tokens live only in an AES-GCM encrypted, httpOnly, SameSite=Lax cookie; the browser never sees one.
// Framework-neutral: handlers take a Web `Request` and return a Web `Response` (Next.js route handlers,
// Hono, Bun, Deno, plain Node 22 all work).

import { challenge, claimsOf, randomToken, seal, unseal } from "./crypto";
import { clearCookies, hasCookie, readCookie, setCookies } from "./cookies";

export type BffConfig = {
  /** Realm URL, e.g. https://id.example.com/realms/substrate */
  issuer: string;
  clientId: string;
  clientSecret: string;
  /** 32+ characters; derives the cookie encryption keys. */
  sessionSecret: string;
  /** The address users reach the app at. Redirect URIs and cookies must agree on one origin. */
  publicUrl: string;
  scope?: string;
  /** Extra authorization request parameters, e.g. { kc_idp_hint: "corp" } or { organization: "acme" }. */
  authParams?: Record<string, string>;
  sessionCookie?: string;
  loginCookie?: string;
  /** Seconds the browser session lives (a refresh keeps it alive up to the refresh token's own lifetime). */
  sessionMaxAge?: number;
  /** Seconds a sign-in attempt may take. */
  loginMaxAge?: number;
  /** Refresh the access token when it expires within this many milliseconds. */
  refreshSkewMs?: number;
  callbackPath?: string;
  /** Where Keycloak sends the browser after RP-initiated logout (a path on publicUrl). */
  postLogoutPath?: string;
  /** Defaults to true when publicUrl is https. */
  secureCookies?: boolean;
  fetch?: typeof fetch;
  now?: () => number;
};

export type Session = {
  access_token: string;
  refresh_token: string;
  /** Epoch milliseconds. */
  expires_at: number;
  /** Epoch milliseconds; absent for offline tokens. */
  refresh_expires_at?: number;
  name: string;
};

export type LoginState = {
  state: string;
  verifier: string;
  nonce: string;
  next: string;
};

export type SessionResult = {
  session: Session;
  /** Set-Cookie header values to append to the response when the session was refreshed. */
  setCookies: string[];
};

export type TokenResponse = {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  expires_in: number;
  refresh_expires_in?: number;
};

const SESSION_PURPOSE = "session";
const LOGIN_PURPOSE = "login";

export function problem(
  status: number,
  title: string,
  detail: string,
  extra?: HeadersInit,
): Response {
  return new Response(
    JSON.stringify({
      type: `https://substrate.dev/problems/${title.toLowerCase().replace(/\s+/g, "-")}`,
      title,
      status,
      detail,
    }),
    {
      status,
      headers: {
        "Content-Type": "application/problem+json",
        "Cache-Control": "no-store",
        ...extra,
      },
    },
  );
}

/**
 * Only same-origin relative paths: no open redirects. Anything else (absolute URLs, `//host`, `/\host`,
 * control characters) becomes "/".
 */
export function safeNext(requested: string | null | undefined): string {
  if (!requested || !requested.startsWith("/") || requested.startsWith("//"))
    return "/";
  if (/[\\\u0000-\u001f\u007f]/.test(requested)) return "/";
  try {
    const base = "http://bff.invalid";
    const url = new URL(requested, base);
    return url.origin === base ? url.pathname + url.search : "/";
  } catch {
    return "/";
  }
}

export function createBff(configOrFactory: BffConfig | (() => BffConfig)) {
  let cached: Required<Omit<BffConfig, "authParams">> &
    Pick<BffConfig, "authParams">;
  const settings = () => {
    if (cached) return cached;
    const c =
      typeof configOrFactory === "function"
        ? configOrFactory()
        : configOrFactory;
    if (!c.issuer || !c.clientId || !c.clientSecret || !c.publicUrl)
      throw new Error(
        "oidc-bff: issuer, clientId, clientSecret and publicUrl are required",
      );
    if (!c.sessionSecret || c.sessionSecret.length < 32)
      throw new Error("oidc-bff: sessionSecret must be at least 32 characters");
    const publicUrl = c.publicUrl.replace(/\/$/, "");
    cached = {
      ...c,
      issuer: c.issuer.replace(/\/$/, ""),
      publicUrl,
      scope: c.scope ?? "openid profile email",
      sessionCookie: c.sessionCookie ?? "session",
      loginCookie: c.loginCookie ?? "login",
      sessionMaxAge: c.sessionMaxAge ?? 60 * 60 * 10,
      loginMaxAge: c.loginMaxAge ?? 600,
      refreshSkewMs: c.refreshSkewMs ?? 30_000,
      callbackPath: c.callbackPath ?? "/auth/callback",
      postLogoutPath: c.postLogoutPath ?? "/",
      secureCookies: c.secureCookies ?? publicUrl.startsWith("https:"),
      fetch: c.fetch ?? ((...args) => globalThis.fetch(...args)),
      now: c.now ?? Date.now,
    };
    return cached;
  };

  const inflight = new Map<string, Promise<Session | null>>();

  async function tokenRequest(
    form: Record<string, string>,
  ): Promise<TokenResponse> {
    const c = settings();
    const response = await c.fetch(
      `${c.issuer}/protocol/openid-connect/token`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          Accept: "application/json",
        },
        body: new URLSearchParams({
          ...form,
          client_id: c.clientId,
          client_secret: c.clientSecret,
        }),
        cache: "no-store",
      },
    );
    if (!response.ok)
      throw new Error(`Token endpoint returned ${response.status}`);
    return (await response.json()) as TokenResponse;
  }

  function sessionFrom(tokens: TokenResponse, previous?: Session): Session {
    const c = settings();
    const claims = claimsOf(tokens.access_token);
    const refresh = tokens.refresh_token ?? previous?.refresh_token ?? "";
    const refreshLife = tokens.refresh_expires_in;
    return {
      access_token: tokens.access_token,
      refresh_token: refresh,
      expires_at: c.now() + tokens.expires_in * 1000,
      refresh_expires_at: refreshLife
        ? c.now() + refreshLife * 1000
        : undefined,
      name: String(
        claims.name || claims.preferred_username || previous?.name || "user",
      ),
    };
  }

  /** Refresh shortly before expiry. Concurrent requests share one rotation (a rotated refresh token is single-use). */
  function refreshed(session: Session): Promise<Session | null> {
    const pending = inflight.get(session.refresh_token);
    if (pending) return pending;
    const run = (async () => {
      try {
        if (!session.refresh_token) return null;
        return sessionFrom(
          await tokenRequest({
            grant_type: "refresh_token",
            refresh_token: session.refresh_token,
          }),
          session,
        );
      } catch {
        return null;
      } finally {
        inflight.delete(session.refresh_token);
      }
    })();
    inflight.set(session.refresh_token, run);
    return run;
  }

  async function sealSession(
    request: Request,
    session: Session,
  ): Promise<string[]> {
    const c = settings();
    return setCookies(
      request,
      c.sessionCookie,
      await seal(
        c.sessionSecret,
        SESSION_PURPOSE,
        session,
        c.sessionMaxAge,
        c.now(),
      ),
      { maxAge: c.sessionMaxAge, secure: c.secureCookies },
    );
  }

  function append(response: Response, cookies: string[]): Response {
    for (const cookie of cookies) response.headers.append("Set-Cookie", cookie);
    return response;
  }

  function redirect(location: string, cookies: string[]): Response {
    const response = new Response(null, {
      status: 302,
      headers: { Location: location, "Cache-Control": "no-store" },
    });
    return append(response, cookies);
  }

  /**
   * The caller's session, refreshed when it is about to expire. `null` means signed out (no cookie,
   * tampered, expired, or the refresh token was refused): send the user to sign in again and
   * append `clearSession(request)` to the response.
   */
  async function getSession(request: Request): Promise<SessionResult | null> {
    const c = settings();
    const stored = await unseal<Session>(
      c.sessionSecret,
      SESSION_PURPOSE,
      readCookie(request, c.sessionCookie),
      c.now(),
    );
    if (!stored) return null;
    if (stored.expires_at - c.now() > c.refreshSkewMs)
      return { session: stored, setCookies: [] };
    if (stored.refresh_expires_at && stored.refresh_expires_at <= c.now())
      return null;
    const next = await refreshed(stored);
    return next
      ? { session: next, setCookies: await sealSession(request, next) }
      : null;
  }

  /** Set-Cookie values that sign the browser out locally. */
  function clearSession(request: Request): string[] {
    const c = settings();
    return clearCookies(request, c.sessionCookie, c.secureCookies);
  }

  return {
    getSession,
    clearSession,

    /** Cheap, optimistic: is there a session cookie at all? (For routing; verify with getSession.) */
    hasSessionCookie(request: Request): boolean {
      return hasCookie(request, settings().sessionCookie);
    },

    /** The access token to call an API with, plus any cookies to append to the response. */
    async bearerFor(
      request: Request,
    ): Promise<{ token: string; setCookies: string[] } | null> {
      const result = await getSession(request);
      return result
        ? { token: result.session.access_token, setCookies: result.setCookies }
        : null;
    },

    /** Append the cookies from `getSession`/`bearerFor` (refresh rotation) to a response. */
    withCookies(response: Response, cookies: string[]): Response {
      return append(response, cookies);
    },

    /** The 401 problem+json answer for an API call without a valid session, clearing the cookie. */
    unauthorized(request: Request): Response {
      return append(
        problem(401, "Unauthorized", "Sign in again"),
        clearSession(request),
      );
    },

    /** Same-origin check for state-changing requests (CSRF defence in depth on top of SameSite=Lax). */
    sameOrigin(request: Request): boolean {
      if (
        request.method === "GET" ||
        request.method === "HEAD" ||
        request.method === "OPTIONS"
      )
        return true;
      const origin = request.headers.get("origin");
      return !origin || origin === new URL(settings().publicUrl).origin;
    },

    async loginHandler(request: Request): Promise<Response> {
      const c = settings();
      const url = new URL(request.url);
      const state = randomToken();
      const verifier = randomToken(48);
      const nonce = randomToken();
      const next = safeNext(url.searchParams.get("next"));
      const target = new URL(`${c.issuer}/protocol/openid-connect/auth`);
      target.search = new URLSearchParams({
        ...c.authParams,
        client_id: c.clientId,
        response_type: "code",
        scope: c.scope,
        redirect_uri: `${c.publicUrl}${c.callbackPath}`,
        state,
        nonce,
        code_challenge: await challenge(verifier),
        code_challenge_method: "S256",
      }).toString();
      const login: LoginState = { state, verifier, nonce, next };
      return redirect(
        target.toString(),
        setCookies(
          request,
          c.loginCookie,
          await seal(
            c.sessionSecret,
            LOGIN_PURPOSE,
            login,
            c.loginMaxAge,
            c.now(),
          ),
          { maxAge: c.loginMaxAge, secure: c.secureCookies },
        ),
      );
    },

    async callbackHandler(request: Request): Promise<Response> {
      const c = settings();
      const url = new URL(request.url);
      const login = await unseal<LoginState>(
        c.sessionSecret,
        LOGIN_PURPOSE,
        readCookie(request, c.loginCookie),
        c.now(),
      );
      const clearLogin = clearCookies(request, c.loginCookie, c.secureCookies);
      const code = url.searchParams.get("code");
      if (url.searchParams.get("error"))
        return append(
          problem(
            401,
            "Sign-in refused",
            `The identity provider refused sign-in: ${url.searchParams.get("error")}`,
          ),
          clearLogin,
        );
      if (!login || !code || url.searchParams.get("state") !== login.state)
        return append(
          problem(
            400,
            "Sign-in failed",
            "Sign-in could not be verified; start again",
          ),
          clearLogin,
        );
      let tokens: TokenResponse;
      try {
        tokens = await tokenRequest({
          grant_type: "authorization_code",
          code,
          redirect_uri: `${c.publicUrl}${c.callbackPath}`,
          code_verifier: login.verifier,
        });
      } catch {
        return append(
          problem(
            502,
            "Sign-in failed",
            "The identity provider rejected the sign-in",
          ),
          clearLogin,
        );
      }
      // The ID token came straight from the token endpoint over TLS; check it answers *this* login.
      const id = tokens.id_token ? claimsOf(tokens.id_token) : null;
      const audiences = ([] as unknown[]).concat(id?.aud ?? []);
      if (
        !id ||
        id.nonce !== login.nonce ||
        id.iss !== c.issuer ||
        !audiences.includes(c.clientId)
      )
        return append(
          problem(
            400,
            "Sign-in failed",
            "The ID token does not match this sign-in",
          ),
          clearLogin,
        );
      return redirect(`${c.publicUrl}${login.next}`, [
        ...(await sealSession(request, sessionFrom(tokens))),
        ...clearLogin,
      ]);
    },

    /** RP-initiated logout: drop the cookie, then end the Keycloak SSO session and come back to postLogoutPath. */
    logoutHandler(request: Request): Response {
      const c = settings();
      const target = new URL(`${c.issuer}/protocol/openid-connect/logout`);
      target.search = new URLSearchParams({
        client_id: c.clientId,
        post_logout_redirect_uri: `${c.publicUrl}${safeNext(c.postLogoutPath)}`,
      }).toString();
      return redirect(
        target.toString(),
        clearCookies(request, c.sessionCookie, c.secureCookies),
      );
    },
  };
}

export type Bff = ReturnType<typeof createBff>;
