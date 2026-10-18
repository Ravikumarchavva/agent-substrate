import { describe, expect, it, beforeAll } from "vitest";

beforeAll(() => {
  process.env.OIDC_ISSUER = "http://keycloak.test/realms/substrate";
  process.env.OIDC_CLIENT_SECRET = "client-secret";
  process.env.SESSION_SECRET = "s".repeat(40);
  process.env.PUBLIC_URL = "http://127.0.0.1:3000";
});

function token(claims: Record<string, unknown>): string {
  const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "RS256" })}.${encode(claims)}.signature`;
}

describe("userFromAccessToken", () => {
  it("takes the tenant from the organization id and the role from the realm roles", async () => {
    const { userFromAccessToken } = await import("./session");
    const user = userFromAccessToken(
      token({
        sub: "u1",
        email: "a@acme.test",
        name: "Alice",
        organization: { acme: { id: "org-42" } },
        realm_access: { roles: ["tenant_admin", "offline_access"] },
      }),
    );
    expect(user).toMatchObject({ id: "u1", email: "a@acme.test", name: "Alice", tenantId: "org-42", isAdmin: true });
  });

  it("is not an administrator without tenant_admin or platform_admin", async () => {
    const { userFromAccessToken } = await import("./session");
    expect(userFromAccessToken(token({ sub: "u2", realm_access: { roles: ["end_user"] } })).isAdmin).toBe(false);
    expect(userFromAccessToken(token({ sub: "u3", realm_access: { roles: ["platform_admin"] } })).isAdmin).toBe(true);
  });

  it("falls back to the alias when the claim is a list, and survives a malformed token", async () => {
    const { userFromAccessToken } = await import("./session");
    expect(userFromAccessToken(token({ sub: "u", organization: ["acme"] })).tenantId).toBe("acme");
    expect(userFromAccessToken("not-a-jwt", "someone")).toMatchObject({ id: "", name: "someone", isAdmin: false });
  });
});

describe("authed", () => {
  it("answers 401 problem+json without a session and never runs the handler", async () => {
    const { authed } = await import("./session");
    let ran = false;
    const route = authed(async () => {
      ran = true;
      return new Response("secret");
    });
    const response = await route(new Request("http://127.0.0.1:3000/chat/api/x"), undefined);
    expect(response.status).toBe(401);
    expect(response.headers.get("content-type")).toContain("application/problem+json");
    expect(ran).toBe(false);
  });

  it("refuses a tampered session cookie", async () => {
    const { authed, SESSION_COOKIE } = await import("./session");
    const route = authed(async () => new Response("secret"));
    const response = await route(
      new Request("http://127.0.0.1:3000/chat/api/x", { headers: { cookie: `${SESSION_COOKIE}=forged-value` } }),
      undefined,
    );
    expect(response.status).toBe(401);
  });

  it("does not accept a plain HS256 bearer or the old cookie names as a session", async () => {
    const { requireAuth } = await import("./session");
    const request = new Request("http://127.0.0.1:3000/chat/api/x", {
      headers: { authorization: "Bearer a.b.c", cookie: "session_token=abc; user_session=%7B%7D" },
    });
    expect(await requireAuth(request)).toBeNull();
  });
});

describe("bffFor", () => {
  it("reuses one handler per organization and ignores a malformed alias", async () => {
    const { bffFor } = await import("./session");
    expect(bffFor("acme")).toBe(bffFor("acme"));
    expect(bffFor("acme")).not.toBe(bffFor("globex"));
    expect(bffFor("a b; scope=admin")).toBe(bffFor(null));
  });

  it("asks Keycloak for exactly the chosen organization", async () => {
    const { bffFor } = await import("./session");
    const response = await bffFor("acme").loginHandler(new Request("http://127.0.0.1:3000/chat/auth/login?next=/chat/agents"));
    const target = new URL(response.headers.get("location")!);
    expect(target.origin + target.pathname).toBe("http://keycloak.test/realms/substrate/protocol/openid-connect/auth");
    expect(target.searchParams.get("scope")).toBe("openid profile email organization:acme");
    expect(target.searchParams.get("code_challenge_method")).toBe("S256");
    expect(target.searchParams.get("redirect_uri")).toBe("http://127.0.0.1:3000/chat/auth/callback");
  });
});
