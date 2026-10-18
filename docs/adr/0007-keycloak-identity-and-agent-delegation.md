# 0007 — Keycloak for identity everywhere; custom per-run tokens for agent-to-data delegation

- **Status:** accepted, 2026-10-18.

## Decision

- **Each cell has a Keycloak realm `substrate`;** the commercial platform runs a realm `accounts` that managed cells broker to, so one sign-in covers the account and its projects. Enterprise customers add their own IdP as an organization identity provider.
- **Tenants are Keycloak Organizations.** One organization is active per token; `tenant_id` is the immutable organization id. Roles: `platform_admin`, `tenant_admin`, `end_user`; plus scopes.
- All services (every agent-server entry point, including each microservice, and substrate-data) verify RS256 tokens with one shared library, `substrate-identity` (JWKS cache, rotation, stale-if-error, audience and `typ` checks). The shared HS256 secret (`JWT_SECRET`, `ENGINE_JWT_SECRET`), its minting code, refresh tokens and the web apps' own user/session tables are removed. Both UIs share one backend-for-frontend session library.
- **Agent to data uses the platform's own Ed25519 per-run token, not Keycloak token exchange.** Scheduled and background runs have no user token to exchange, and per-run claims (run, thread, acting agent, TTL <= 330 s) should not need an IdP round trip. Hardening: the minter signs only for the run's recorded principal after a cached organization-membership check; tokens do not assert groups (substrate-data derives the tenant itself); each trusted issuer has `allowed_scopes`; the scopes are `data:read`, `files:*`, `documents:*`, `memory:*`, enforced per route. The sandbox gateway stays read-only SQL.

## Consequences

One login, one token format, one verifier. Revoking a user in Keycloak stops new tokens within the membership-cache window (60 s).
