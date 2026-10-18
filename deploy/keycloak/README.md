# Keycloak realm for a Substrate cell

`realm-substrate.json` is an importable realm template for [ADR 0007](../../docs/adr/0007-keycloak-identity-and-agent-delegation.md):
one realm `substrate` per cell, tenants are Keycloak **Organizations**, one organization is active per token.

Validated against `quay.io/keycloak/keycloak:26.8.1` (`start-dev --import-realm`, ready in about 20 s): the import
is clean (no warnings), and tokens for a user in two organizations, a cell-admin service account and a device-grant
request were checked against `substrate-identity` using OIDC discovery.

## Import

```sh
export SUBSTRATE_UI_CLIENT_SECRET=...          # all three are required, see "Secrets"
export SUBSTRATE_DATA_WEB_CLIENT_SECRET=...
export CELL_ADMIN_CLIENT_SECRET=...
docker run --rm -p 127.0.0.1:8080:8080 \
  -e KC_BOOTSTRAP_ADMIN_USERNAME -e KC_BOOTSTRAP_ADMIN_PASSWORD \
  -e SUBSTRATE_UI_CLIENT_SECRET -e SUBSTRATE_DATA_WEB_CLIENT_SECRET -e CELL_ADMIN_CLIENT_SECRET \
  -v "$PWD/realm-substrate.json:/opt/keycloak/data/import/realm-substrate.json:ro" \
  quay.io/keycloak/keycloak:26.8.1 start-dev --import-realm
```

Production uses `start` with TLS/hostname settings and the same file. `--import-realm` skips a realm that already
exists: it seeds a new cell, it does not reconcile. For a running cell use the Terraform in [`terraform/`](terraform/).

### Secrets

No secret is in the file. Keycloak substitutes `${NAME}` from the process environment while importing:

| Variable | Used by | Default |
| --- | --- | --- |
| `SUBSTRATE_UI_CLIENT_SECRET` | `substrate-ui` client secret (the BFF's `clientSecret`) | none |
| `SUBSTRATE_DATA_WEB_CLIENT_SECRET` | `substrate-data-web` client secret (`WEB_CLIENT_SECRET`) | none |
| `CELL_ADMIN_CLIENT_SECRET` | `cell-admin` service account | none |
| `SUBSTRATE_UI_URL` | UI origin for redirect/logout URIs | `http://127.0.0.1:3000` |
| `SUBSTRATE_DATA_WEB_URL` | data workspace origin | `http://127.0.0.1:3011` |

**An unset secret variable is not an error**: Keycloak keeps the literal text `${CELL_ADMIN_CLIENT_SECRET}` and that
becomes the secret (verified). Check that all three are set before importing, and rotate secrets after import
through the admin API or Terraform if they were ever wrong.

## What the realm contains

| Clients | Type | Purpose |
| --- | --- | --- |
| `substrate-ui` | confidential, code + PKCE S256 | the agents console's backend-for-frontend (`@substrate/oidc-bff`) |
| `substrate-data-web` | confidential, code + PKCE S256 | the data workspace's backend-for-frontend |
| `agent-substrate`, `substrate-data` | bearer-only | the resource servers: their client ids are the token **audiences**, and they own the client roles `provision-tenant` and `erase-tenant` |
| `cell-admin` | confidential, client credentials | the platform's service account for provisioning and erasure |
| `substrate-cli` | public, device grant | people at a terminal; the SDK/CLI sign-in |

- **Realm roles:** `platform_admin` (operates the cell; never given to tenant users), `tenant_admin` (administers one organization),
  `end_user`. They arrive in `realm_access.roles`; client roles in `resource_access.<client>.roles`.
- **Client scopes:** `agents-api` adds `aud=agent-substrate`, `data-api` adds `aud=substrate-data`. The delegated scopes
  `data:read files:read files:write documents:read documents:write memory:read memory:write` are optional scopes on the
  human clients (what a CLI or integration may ask for). The standard scopes (`basic` with the `sub` claim, `profile`, `email`, `roles`, `web-origins`,
  `acr`) are defined **explicitly**: a realm file that defines any client scope suppresses Keycloak's built-ins, and a missing `basic`
  scope means access tokens without `sub`.
- **`organization` scope:** its mapper emits the `organization` claim as `{"<alias>": {"id": "<org id>"}}` (`addOrganizationId`).
- `groups` scope: group names for substrate-data's metastore admin group (`data-admins`, created empty).

### One active organization per token

A user may belong to several organizations, but a token is for exactly one. Clients request `scope=openid organization:<alias>`;
the claim then holds that single organization. Observed with 26.8.1 for a member of `acme` and `beta`:

| Requested scope | `organization` claim |
| --- | --- |
| `organization:acme` | `{"acme": {"id": "..."}}` |
| `organization:*` | both organizations |
| `organization` | **absent** for a multi-organization user |
| (none) | absent |

`substrate-identity` takes the immutable organization **id** as `tenant_id` and rejects a token with zero (403
`organization-required`) or several (403 `ambiguous-organization`) organizations. The UIs therefore pass the chosen
organization to the BFF (`authParams` / `scope`), and a user with several organizations picks one before sign-in.
Platform service accounts (`service-account-*`, such as `cell-admin`) carry no organization: they act across tenants, name the tenant in the
request, and are authorized by their client roles.

### Accounts realm brokering (planned)

The commercial platform runs a second realm, `accounts`, where people hold their account. A managed cell's realm
`substrate` will list `accounts` as an OIDC identity provider (broker), so one sign-in covers the account and its
projects; organizations in the cell map to the account's workspaces. Enterprise customers add their own IdP as an
**organization identity provider** (Organizations feature), which also routes users to the right organization by
email domain. Neither is in this template: add the identity provider per cell after import (or in Terraform).

### How the platform uses `cell-admin`

The control plane obtains a client-credentials token (`cell-admin`, scopes `agents-api data-api`) and calls the cell's
provisioning and erasure endpoints. The token has `aud` of both services and the client roles `provision-tenant` /
`erase-tenant` on each; the services check the role in `resource_access.<their client id>.roles`. Creating a tenant means
creating a Keycloak organization (admin API) and then calling `provision-tenant`; erasure calls `erase-tenant` and then deletes
the organization. `cell-admin` has no realm roles and cannot sign anyone in. Keep its secret in the platform's secret store and rotate it
per cell.

## Development realm

`data/substrate-data/scripts/bootstrap.py` still generates its own realm for the local workspace
(`.workspace/infra/substrate-realm.json`: users `alice`/`bob`, clients `substrate-data-web`, `substrate-data-cli`,
`substrate-data-bench`) with random dev secrets, and is unchanged. It works with the new libraries as is: substrate-data runs
with `require_organization=False`, so tokens without an organization are accepted. To move the dev flow onto this template:
import `realm-substrate.json` instead, create an organization and a user, point `SUBSTRATE_DATA_OIDC_ISSUER` at the realm, set
`WEB_CLIENT_ID=substrate-data-web` and `WEB_CLIENT_SECRET=$SUBSTRATE_DATA_WEB_CLIENT_SECRET`, and add users to the group `data-admins`
for administrator rights (the template carries no users and no passwords).

## Terraform

[`terraform/`](terraform/) is a skeleton with the same clients, scopes and roles for reconciling a live cell (provider
`keycloak/keycloak`). It has not been applied or validated with `terraform validate`.
