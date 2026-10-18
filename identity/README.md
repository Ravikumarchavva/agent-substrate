# Identity

Implements [ADR 0007](../docs/adr/0007-keycloak-identity-and-agent-delegation.md) (Keycloak everywhere) for the services of a cell
([ADR 0006](../docs/adr/0006-cells-and-control-plane-split.md)). Two libraries, one realm template ([`../deploy/keycloak`](../deploy/keycloak)).

| Package | Language | What it does |
| --- | --- | --- |
| [`substrate-identity`](substrate-identity) | Python >= 3.13 (`pyjwt[crypto]`, `httpx`) | Verify access tokens in a service: JWKS cache, `typ`/`aud`/`iss`/`exp` checks, one-organization tenant rule, trusted issuers, FastAPI helpers |
| [`@substrate/oidc-bff`](oidc-bff) | TypeScript, Node >= 22, Web `Request`/`Response` | The browser side: authorization code + PKCE session in an encrypted cookie, refresh rotation, logout |

## Token format expected from Keycloak 26

An access token (`typ: Bearer`, RS256 by default) with:

```json
{
  "iss": "https://id.example.com/realms/substrate",
  "aud": "agent-substrate",              // or ["agent-substrate", "substrate-data"]: client scopes `agents-api` / `data-api`
  "sub": "<user id>", "iat": 0, "exp": 0, "jti": "...",
  "preferred_username": "alice",         // "service-account-<client>" for client credentials
  "email": "alice@acme.test",
  "organization": { "acme": { "id": "<organization id>" } },   // or ["acme"]; exactly one entry
  "realm_access": { "roles": ["tenant_admin"] },
  "resource_access": { "agent-substrate": { "roles": ["provision-tenant"] } },
  "groups": ["data-admins"],
  "scope": "openid profile email organization:acme data:read",
  "act": { "sub": "service-account-..." }  // RFC 8693, optional
}
```

`sub`, `exp`, `iat`, `iss`, `aud` are required. `sub` comes from the `basic` client scope, which the realm template defines.
`tenant_id` is the organization **id** (the alias when the claim is a list). Roles are the realm roles plus the roles of the
service's own audience client(s). `scopes` is `None` for an ordinary user token and the set of delegated scopes (`data:`, `files:`,
`documents:`, `memory:` prefixes) otherwise.

## Python service

```python
from substrate_identity import PrimaryIssuer, TokenVerifier, TrustedIssuer
from substrate_identity.fastapi import identity_dependency, require_roles, require_scopes

verifier = TokenVerifier(
    PrimaryIssuer("https://id.example.com/realms/substrate", audience="agent-substrate"),  # JWKS found by discovery
    trusted=[TrustedIssuer("agent-substrate", "substrate-data", "https://agents.internal/.well-known/jwks.json",
                           allowed_scopes=frozenset({"data:read", "files:read"}))],
)
current = identity_dependency(verifier)

@app.get("/me")
async def me(who: Identity = Depends(current)): ...            # verification runs in a worker thread

@app.delete("/tenants/{id}", dependencies=[Depends(require_roles(current, "erase-tenant"))])
```

Everything fails closed: unknown issuer, algorithm outside RS256/ES256/EdDSA (never HS256, never `none`), key bound to another
algorithm, wrong `typ`, missing claim, zero or several organizations. Errors are typed (`IdentityError` subclasses carrying `status`
and `code`): 401 for bad tokens, 403 for organization and scope problems, 503 when no signing key is available. Key refresh on an
unknown `kid` happens at most every 30 s; a failing JWKS endpoint keeps cached keys for up to an hour, then the verifier answers 503.
Plain-http issuer or JWKS URLs need `insecure_http=True` (tests and loopback only).

substrate-data wraps the library in [`auth/oidc.py`](../data/substrate-data/src/substrate_data/auth/oidc.py) to produce its own `Principal`
(administrator by group, read-only by scope). It runs with `require_organization=False` until its tenancy moves to organizations.

## Web app

```ts
// lib/session.ts
export const bff = createBff(() => ({ issuer, clientId, clientSecret, sessionSecret, publicUrl,
  sessionCookie: "sd_session", loginCookie: "sd_login" }));
// app/auth/login/route.ts    export const GET = (r: Request) => bff.loginHandler(r);
// app/auth/callback/route.ts export const GET = (r: Request) => bff.callbackHandler(r);
// app/auth/logout/route.ts   export const GET = (r: Request) => bff.logoutHandler(r);
// in an API proxy:
const bearer = await bff.bearerFor(request);                  // refreshes and rotates when needed
if (!bearer) return bff.unauthorized(request);                // 401 problem+json, cookie cleared
... fetch(api, { headers: { Authorization: `Bearer ${bearer.token}` } }) ...
return bff.withCookies(response, bearer.setCookies);          // new cookie after a refresh
```

The package exports TypeScript source (consumers need `transpilePackages: ["@substrate/oidc-bff"]` in Next.js). Tokens never reach the
browser. The session is AES-256-GCM sealed with a key derived (HKDF) per purpose from `sessionSecret` (32+ characters), stored in an
`httpOnly`, `SameSite=Lax` cookie (`Secure` when the public URL is https) split across `name`, `name.1`... when large. State, nonce and PKCE verifier live in a ten-minute
sealed login cookie; the callback checks the state and the ID token's nonce, issuer and audience. `next` must be a same-origin relative path.
Concurrent requests share one refresh, because rotated refresh tokens are single use. To pin the organization pass
`scope: "openid profile email organization:acme"` (see the realm README: a bare `organization` scope yields no claim for users in several organizations).

## Deliberately not done here

- **agent-substrate still verifies HS256** (`JWT_SECRET`, `ENGINE_JWT_SECRET`) and still mints those tokens; removing them and adopting
  `substrate-identity` there is Track D's job. This change adds no code to `agents/`.
- **The agents UI (`substrate-ui`) still has its own session and user tables**; only `substrate-data-web` consumes `@substrate/oidc-bff` so far.
- substrate-data keeps `require_organization=False`: its catalog is not tenant-scoped yet, so organization claims are carried into the audit
  `context.tenant_id` but do not gate access. Trusted issuer `allowed_scopes` is a new optional setting (`SUBSTRATE_DATA_TRUSTED_ISSUERS`).
- RS384/RS512/PS*/ES384 tokens are no longer accepted (the allowed set is RS256, ES256, EdDSA); Keycloak signs RS256 by default.
- Logout sends `client_id` + `post_logout_redirect_uri` and does not send `id_token_hint` (it would be one more large token in the cookie).
- Docker images of substrate-data and the data workspace must now include `identity/` in the build context (the workspace dependencies); nothing was built here.
- The Terraform in `deploy/keycloak/terraform` and the production `start` flow of the realm template were not run.
