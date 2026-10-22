import { describe, expect, it, vi } from "vitest";
import { createBff, safeNext, seal, type BffConfig } from "../src/index";

const ISSUER = "https://id.example.com/realms/loamz";
const SECRET = "x".repeat(40);
const PUBLIC = "https://app.example.com";

const b64 = (value: unknown) =>
  Buffer.from(JSON.stringify(value)).toString("base64url");
const jwt = (claims: Record<string, unknown>) =>
  `${b64({ alg: "RS256" })}.${b64(claims)}.sig`;

type Tokens = {
  access_token: string;
  refresh_token: string;
  id_token?: string;
  expires_in: number;
};

function setup(overrides: Partial<BffConfig> = {}) {
  let clock = 1_000_000;
  const calls: { url: string; body: URLSearchParams }[] = [];
  let handler: (body: URLSearchParams) => Response | Promise<Response> = () =>
    new Response("{}", { status: 400 });
  const fetchMock = vi.fn(
    async (url: string | URL | Request, init?: RequestInit) => {
      const body = new URLSearchParams(String(init?.body));
      calls.push({ url: String(url), body });
      return handler(body);
    },
  );
  const bff = createBff({
    issuer: ISSUER,
    clientId: "web",
    clientSecret: "client-secret",
    sessionSecret: SECRET,
    publicUrl: PUBLIC + "/",
    sessionCookie: "sd_session",
    loginCookie: "sd_login",
    fetch: fetchMock as unknown as typeof fetch,
    now: () => clock,
    ...overrides,
  });
  const tokens = (
    nonce: string | null,
    extra: Partial<Tokens> = {},
  ): Tokens => ({
    access_token: jwt({ name: "Alice", preferred_username: "alice" }),
    refresh_token: "refresh-1",
    id_token:
      nonce === null ? undefined : jwt({ iss: ISSUER, aud: "web", nonce }),
    expires_in: 300,
    ...extra,
  });
  return {
    bff,
    calls,
    advance: (ms: number) => (clock += ms),
    respond: (fn: typeof handler) => (handler = fn),
    json: (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200 }),
    tokens,
  };
}

/** A tiny cookie jar: applies Set-Cookie headers and builds the next request's Cookie header. */
class Jar {
  cookies = new Map<string, string>();
  apply(response: Response) {
    for (const line of response.headers.getSetCookie()) {
      const [pair, ...attrs] = line.split(";").map((p) => p.trim());
      const index = pair.indexOf("=");
      const name = pair.slice(0, index);
      const value = pair.slice(index + 1);
      const maxAge = attrs.find((a) => a.startsWith("Max-Age="));
      if (maxAge === "Max-Age=0" || value === "") this.cookies.delete(name);
      else this.cookies.set(name, value);
    }
  }
  request(url: string, init: RequestInit = {}) {
    const header = [...this.cookies].map(([k, v]) => `${k}=${v}`).join("; ");
    return new Request(url, {
      ...init,
      headers: { ...(init.headers ?? {}), cookie: header },
    });
  }
}

async function signIn(
  s: ReturnType<typeof setup>,
  jar: Jar,
  next = "/data?x=1",
  extra?: Partial<Tokens>,
) {
  const login = await s.bff.loginHandler(
    jar.request(`${PUBLIC}/auth/login?next=${encodeURIComponent(next)}`),
  );
  jar.apply(login);
  const authUrl = new URL(login.headers.get("location")!);
  s.respond(() => s.json(s.tokens(authUrl.searchParams.get("nonce"), extra)));
  const callback = await s.bff.callbackHandler(
    jar.request(
      `${PUBLIC}/auth/callback?code=abc&state=${authUrl.searchParams.get("state")}`,
    ),
  );
  jar.apply(callback);
  return { login, authUrl, callback };
}

describe("loginHandler", () => {
  it("redirects to Keycloak with PKCE S256, state and nonce, and stores them in a short-lived cookie", async () => {
    const s = setup();
    const response = await s.bff.loginHandler(
      new Request(`${PUBLIC}/auth/login?next=/files`),
    );
    expect(response.status).toBe(302);
    const url = new URL(response.headers.get("location")!);
    expect(url.origin + url.pathname).toBe(
      `${ISSUER}/protocol/openid-connect/auth`,
    );
    const p = url.searchParams;
    expect(p.get("client_id")).toBe("web");
    expect(p.get("response_type")).toBe("code");
    expect(p.get("scope")).toBe("openid profile email");
    expect(p.get("redirect_uri")).toBe(`${PUBLIC}/auth/callback`);
    expect(p.get("code_challenge_method")).toBe("S256");
    expect(p.get("code_challenge")).toMatch(/^[\w-]{43}$/);
    expect(p.get("state")!.length).toBeGreaterThan(20);
    expect(p.get("nonce")!.length).toBeGreaterThan(20);
    const cookie = response.headers.getSetCookie()[0];
    expect(cookie).toMatch(/^sd_login=/);
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=Lax");
    expect(cookie).toContain("Max-Age=600");
    expect(cookie).toContain("Secure");
    expect(cookie).not.toContain("files"); // sealed, not readable
  });

  it("passes extra authorization parameters", async () => {
    const s = setup({
      authParams: { kc_idp_hint: "corp" },
      scope: "openid data:read",
    });
    const response = await s.bff.loginHandler(
      new Request(`${PUBLIC}/auth/login`),
    );
    const p = new URL(response.headers.get("location")!).searchParams;
    expect(p.get("kc_idp_hint")).toBe("corp");
    expect(p.get("scope")).toBe("openid data:read");
  });

  it("refuses short session secrets and incomplete configuration", async () => {
    const short = setup({ sessionSecret: "short" });
    await expect(
      short.bff.loginHandler(new Request(`${PUBLIC}/auth/login`)),
    ).rejects.toThrow(/32/);
  });

  it("does not use Secure cookies on plain-http public URLs", async () => {
    const s = setup({ publicUrl: "http://127.0.0.1:3011" });
    const response = await s.bff.loginHandler(
      new Request("http://127.0.0.1:3011/auth/login"),
    );
    expect(response.headers.getSetCookie()[0]).not.toContain("Secure");
  });
});

describe("safeNext", () => {
  it.each([
    ["/data", "/data"],
    ["/data?x=1&y=2", "/data?x=1&y=2"],
    ["/", "/"],
    [null, "/"],
    ["", "/"],
    ["https://evil.example/", "/"],
    ["//evil.example/", "/"],
    ["/\\evil.example", "/"],
    ["/%2F/ok", "/%2F/ok"],
    ["javascript:alert(1)", "/"],
    ["relative", "/"],
    ["/a\r\nSet-Cookie: x=1", "/"],
  ])("%s -> %s", (input, expected) => {
    expect(safeNext(input)).toBe(expected);
  });
});

describe("callbackHandler", () => {
  it("completes the code exchange, stores the session and returns to `next`", async () => {
    const s = setup();
    const jar = new Jar();
    const { callback } = await signIn(s, jar);
    expect(callback.status).toBe(302);
    expect(callback.headers.get("location")).toBe(`${PUBLIC}/data?x=1`);
    const call = s.calls[0];
    expect(call.url).toBe(`${ISSUER}/protocol/openid-connect/token`);
    expect(call.body.get("grant_type")).toBe("authorization_code");
    expect(call.body.get("code")).toBe("abc");
    expect(call.body.get("code_verifier")!.length).toBeGreaterThanOrEqual(43);
    expect(call.body.get("client_secret")).toBe("client-secret");
    expect(call.body.get("redirect_uri")).toBe(`${PUBLIC}/auth/callback`);
    expect(jar.cookies.has("sd_session")).toBe(true);
    expect(jar.cookies.has("sd_login")).toBe(false); // consumed
    const result = await s.bff.getSession(jar.request(`${PUBLIC}/`));
    expect(result?.session.name).toBe("Alice");
    expect(result?.setCookies).toEqual([]);
  });

  it("rejects an open-redirect `next`", async () => {
    const s = setup();
    const jar = new Jar();
    const { callback } = await signIn(s, jar, "https://evil.example/steal");
    expect(callback.headers.get("location")).toBe(`${PUBLIC}/`);
  });

  it("rejects a state mismatch, a missing login cookie and a code-less callback", async () => {
    const s = setup();
    const jar = new Jar();
    jar.apply(await s.bff.loginHandler(jar.request(`${PUBLIC}/auth/login`)));
    for (const request of [
      jar.request(`${PUBLIC}/auth/callback?code=abc&state=forged`),
      jar.request(`${PUBLIC}/auth/callback?state=x`),
      new Request(`${PUBLIC}/auth/callback?code=abc&state=x`),
    ]) {
      const response = await s.bff.callbackHandler(request);
      expect(response.status).toBe(400);
      expect(response.headers.get("content-type")).toBe(
        "application/problem+json",
      );
    }
    expect(s.calls).toHaveLength(0);
  });

  it("rejects an IdP error, a failed token exchange and an ID token for another login", async () => {
    const s = setup();
    const jar = new Jar();
    jar.apply(await s.bff.loginHandler(jar.request(`${PUBLIC}/auth/login`)));
    const refused = await s.bff.callbackHandler(
      jar.request(`${PUBLIC}/auth/callback?error=access_denied`),
    );
    expect(refused.status).toBe(401);

    const fresh = new Jar();
    const first = await s.bff.loginHandler(
      fresh.request(`${PUBLIC}/auth/login`),
    );
    fresh.apply(first);
    const params = new URL(first.headers.get("location")!).searchParams;
    const callback = () =>
      s.bff.callbackHandler(
        fresh.request(
          `${PUBLIC}/auth/callback?code=abc&state=${params.get("state")}`,
        ),
      );
    s.respond(() => new Response("{}", { status: 400 }));
    expect((await callback()).status).toBe(502);
    s.respond(() => s.json(s.tokens("someone-elses-nonce")));
    expect((await callback()).status).toBe(400);
    s.respond(() => s.json(s.tokens(null)));
    expect((await callback()).status).toBe(400); // no ID token at all
  });

  it("does not accept a login cookie as a session, or the reverse", async () => {
    const s = setup();
    const jar = new Jar();
    const login = await s.bff.loginHandler(jar.request(`${PUBLIC}/auth/login`));
    const value = login.headers.getSetCookie()[0].split(";")[0].split("=")[1];
    const forged = new Request(PUBLIC, {
      headers: { cookie: `sd_session=${value}` },
    });
    expect(await s.bff.getSession(forged)).toBeNull();
  });
});

describe("getSession / bearerFor", () => {
  it("returns null for no cookie, garbage and tampered cookies", async () => {
    const s = setup();
    const jar = new Jar();
    await signIn(s, jar);
    expect(await s.bff.getSession(new Request(PUBLIC))).toBeNull();
    expect(
      await s.bff.getSession(
        new Request(PUBLIC, { headers: { cookie: "sd_session=junk" } }),
      ),
    ).toBeNull();
    const value = jar.cookies.get("sd_session")!;
    const tampered =
      value.slice(0, -4) + (value.endsWith("AAAA") ? "BBBB" : "AAAA");
    expect(
      await s.bff.getSession(
        new Request(PUBLIC, { headers: { cookie: `sd_session=${tampered}` } }),
      ),
    ).toBeNull();
  });

  it("returns the bearer token while the access token is fresh, without calling Keycloak", async () => {
    const s = setup();
    const jar = new Jar();
    await signIn(s, jar);
    s.calls.length = 0;
    const bearer = await s.bff.bearerFor(jar.request(`${PUBLIC}/api`));
    expect(bearer?.token.split(".")).toHaveLength(3);
    expect(bearer?.setCookies).toEqual([]);
    expect(s.calls).toHaveLength(0);
  });

  it("rotates the refresh token shortly before expiry and issues a new cookie", async () => {
    const s = setup();
    const jar = new Jar();
    await signIn(s, jar);
    s.calls.length = 0;
    s.advance(280_000); // 20 s left: inside the 30 s skew
    s.respond(() =>
      s.json({
        access_token: jwt({ name: "Alice" }),
        refresh_token: "refresh-2",
        expires_in: 300,
      }),
    );
    const result = await s.bff.getSession(jar.request(`${PUBLIC}/api`));
    expect(result?.session.refresh_token).toBe("refresh-2");
    expect(s.calls[0].body.get("grant_type")).toBe("refresh_token");
    expect(s.calls[0].body.get("refresh_token")).toBe("refresh-1");
    expect(result?.setCookies[0]).toMatch(/^sd_session=/);
    const response = s.bff.withCookies(new Response("ok"), result!.setCookies);
    jar.apply(response);
    const again = await s.bff.getSession(jar.request(`${PUBLIC}/api`));
    expect(again?.session.refresh_token).toBe("refresh-2");
    expect(s.calls).toHaveLength(1); // the new access token is fresh
  });

  it("shares one refresh between concurrent requests (rotated refresh tokens are single-use)", async () => {
    const s = setup();
    const jar = new Jar();
    await signIn(s, jar);
    s.calls.length = 0;
    s.advance(290_000);
    s.respond(async () => {
      await new Promise((r) => setTimeout(r, 20));
      return s.json({
        access_token: jwt({}),
        refresh_token: "refresh-2",
        expires_in: 300,
      });
    });
    const results = await Promise.all(
      [1, 2, 3, 4].map(() => s.bff.getSession(jar.request(`${PUBLIC}/api`))),
    );
    expect(s.calls).toHaveLength(1);
    expect(results.every((r) => r?.session.refresh_token === "refresh-2")).toBe(
      true,
    );
  });

  it("signs out when the refresh is refused or the refresh token has expired", async () => {
    const s = setup();
    const jar = new Jar();
    await signIn(s, jar);
    s.advance(290_000);
    s.respond(() => new Response("{}", { status: 400 }));
    expect(await s.bff.getSession(jar.request(`${PUBLIC}/api`))).toBeNull();

    const expiring = setup();
    const jar2 = new Jar();
    await signIn(expiring, jar2, "/", {
      refresh_expires_in: 100,
    } as Partial<Tokens>);
    expiring.calls.length = 0;
    expiring.advance(290_000);
    expect(
      await expiring.bff.getSession(jar2.request(`${PUBLIC}/api`)),
    ).toBeNull();
    expect(expiring.calls).toHaveLength(0); // no pointless round trip
  });

  it("expires the session cookie's payload even if the browser keeps the cookie", async () => {
    const s = setup({ sessionMaxAge: 60 });
    const jar = new Jar();
    await signIn(s, jar);
    s.advance(61_000);
    expect(await s.bff.getSession(jar.request(`${PUBLIC}/api`))).toBeNull();
  });

  it("stores large sessions across several cookies and shrinks them again", async () => {
    const s = setup();
    const jar = new Jar();
    const big = jwt({ name: "Alice", roles: "r".repeat(7000) });
    await signIn(s, jar, "/", { access_token: big });
    expect(
      [...jar.cookies.keys()].filter((k) => k.startsWith("sd_session")).length,
    ).toBeGreaterThan(1);
    for (const value of jar.cookies.values())
      expect(value.length).toBeLessThan(4000);
    expect(
      (await s.bff.getSession(jar.request(PUBLIC)))?.session.access_token,
    ).toBe(big);

    s.advance(290_000);
    s.respond(() =>
      s.json({
        access_token: jwt({ name: "Alice" }),
        refresh_token: "r2",
        expires_in: 300,
      }),
    );
    const refreshed = await s.bff.bearerFor(jar.request(PUBLIC));
    jar.apply(s.bff.withCookies(new Response(), refreshed!.setCookies));
    expect(
      [...jar.cookies.keys()].filter((k) => k.startsWith("sd_session")),
    ).toEqual(["sd_session"]);
    expect(
      (await s.bff.getSession(jar.request(PUBLIC)))?.session.refresh_token,
    ).toBe("r2");
  });

  it("a session sealed with another secret is rejected", async () => {
    const s = setup();
    const other = await seal(
      "y".repeat(40),
      "session",
      { access_token: "a" },
      60,
    );
    expect(
      await s.bff.getSession(
        new Request(PUBLIC, { headers: { cookie: `sd_session=${other}` } }),
      ),
    ).toBeNull();
  });
});

describe("logoutHandler and helpers", () => {
  it("clears the session and redirects to Keycloak's end-session endpoint", async () => {
    const s = setup();
    const jar = new Jar();
    await signIn(s, jar);
    const response = s.bff.logoutHandler(jar.request(`${PUBLIC}/auth/logout`));
    const url = new URL(response.headers.get("location")!);
    expect(url.origin + url.pathname).toBe(
      `${ISSUER}/protocol/openid-connect/logout`,
    );
    expect(url.searchParams.get("client_id")).toBe("web");
    expect(url.searchParams.get("post_logout_redirect_uri")).toBe(`${PUBLIC}/`);
    jar.apply(response);
    expect(jar.cookies.has("sd_session")).toBe(false);
  });

  it("unauthorized() answers problem+json 401 and clears the cookie", async () => {
    const s = setup();
    const response = s.bff.unauthorized(
      new Request(PUBLIC, { headers: { cookie: "sd_session=x" } }),
    );
    expect(response.status).toBe(401);
    expect(response.headers.get("content-type")).toBe(
      "application/problem+json",
    );
    expect(response.headers.getSetCookie()[0]).toContain("Max-Age=0");
  });

  it("sameOrigin lets safe methods through and refuses foreign origins on writes", () => {
    const s = setup();
    const post = (origin?: string) =>
      new Request(PUBLIC, {
        method: "POST",
        headers: origin ? { origin } : {},
      });
    expect(s.bff.sameOrigin(new Request(PUBLIC))).toBe(true);
    expect(s.bff.sameOrigin(post(PUBLIC))).toBe(true);
    expect(s.bff.sameOrigin(post("https://evil.example"))).toBe(false);
  });

  it("hasSessionCookie is an optimistic routing check", () => {
    const s = setup();
    expect(
      s.bff.hasSessionCookie(
        new Request(PUBLIC, { headers: { cookie: "sd_session=x" } }),
      ),
    ).toBe(true);
    expect(s.bff.hasSessionCookie(new Request(PUBLIC))).toBe(false);
  });
});
