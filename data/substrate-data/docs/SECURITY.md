# Security model

## Identity

- Every `/api/v1` route requires an OIDC **access token**: signature against the IdP's JWKS (cached,
  refreshed on key rotation), `iss`, `aud=substrate-data`, `exp`, `iat`, `sub`, and `typ=Bearer`. ID,
  refresh, `alg=none` and wrong-audience tokens are rejected.
- Principals are `user:`, `service:` or `group:`. Groups come from the token; the metastore admin group is
  configured (`SUBSTRATE_DATA_ADMIN_GROUP`). When the IdP issues a delegated token (RFC 8693 `act` claim), the
  actor is carried into the audit and statement records so an agent acting for a user is attributable to both.
  The IdP performs the token exchange; this service validates its result.
- The web UI is a backend-for-frontend: Authorization Code + PKCE against a confidential client; tokens live
  only in an AES-GCM encrypted, `httpOnly`, `SameSite=Lax` cookie; the API proxy attaches the bearer token
  and refuses cross-origin writes; the browser never sees a token.
- Applications use their own service principals (client credentials). There are no shared endpoint keys.
- **Trusted issuers.** Besides the primary IdP, `SUBSTRATE_DATA_TRUSTED_ISSUERS` lists issuers whose public keys
  are fetched over HTTPS from a `jwks_uri` (cached, refreshed on an unknown `kid` at most every 30 s, stale-if-error
  for an hour then fail closed). Their principals are never metastore admins and, by default, tokens must carry
  `scope: data:read`, which limits them to `SELECT` statements and catalog browsing (`403 token-read-only`
  otherwise). Context claims (`tenant_id`, `thread_id`, `agent_id`) and `jti` (as `token_id`) are stored with each
  statement. This is how agent-substrate's per-run token is accepted across machines; see [AGENTS.md](AGENTS.md).

## Transport

The API serves TLS from files (`SUBSTRATE_DATA_TLS_CERT_FILE`, `…_KEY_FILE`), TLS 1.3 by default
(`SUBSTRATE_DATA_TLS_MIN_VERSION=1.2` restricts to ECDHE + AEAD suites), and requires client certificates signed
by `SUBSTRATE_DATA_TLS_CLIENT_CA_FILE` when set (mutual TLS). It **refuses to listen in plaintext on a non-loopback
address** unless `SUBSTRATE_DATA_BEHIND_TLS_PROXY=true` declares that a proxy or mesh terminates TLS. Service URLs
to other systems must be HTTPS off loopback. Controller and worker links keep their bearer-token model; mTLS
for them is planned. Audit and statement history are deleted after `SUBSTRATE_DATA_HISTORY_RETENTION_DAYS`
(default 365). See [COMPLIANCE.md](COMPLIANCE.md).

## Authorization

Unity Catalog-style securables: catalog → schema → table. Privileges (`USE_CATALOG`, `USE_SCHEMA`,
`SELECT`, `MODIFY`, `CREATE_SCHEMA`, `CREATE_TABLE`) inherit downward and are granted to users, groups or
service principals. Reading a table needs `SELECT` **and** `USE_CATALOG` + `USE_SCHEMA` on its parents;
owning a table does not bypass the parents. A table or view the caller holds no privilege on returns **404**, so existence is not disclosed; one they can browse but lack the needed privilege on returns **403**. Attached compute is gated by `USE_COMPUTE`. Optimistic concurrency uses `ETag`/`If-Match`.

## Views

Views use definer rights: a reader needs `SELECT` on the view only, and the view reads its tables as its
owner (creator identity and groups captured when the view was defined). Creation requires the creator
to be able to read everything the definition touches. If the owner later loses access the view stops
working (403 `view-definer-access`) rather than widening access. Views cannot reference themselves.

## The data path

The control plane signs a ticket (Ed25519, 60 s, single use) naming the principal, the rewritten SQL and the
exact files; workers hold only the public key. A worker cannot be asked to read a file the planner did not
list, and even a validly signed statement cannot reach outside the warehouse bucket (locked DuckDB). Statement
SQL must be a single read-only `SELECT`; table functions, file access, system catalogs, environment and
configuration functions, `COPY`, `ATTACH`, `PRAGMA`, `SET` and DDL are refused at the control plane.

## Storage credentials

| Identity | Can | Cannot |
| --- | --- | --- |
| Control plane | Commit Iceberg metadata (`s3tables:*` on the warehouse), sign staging uploads, sign and copy objects in the volumes bucket | (reads no table data) |
| Worker | Read and write warehouse data files, read staging | Commit table metadata, write staging, use the catalog, **touch the volumes bucket (user files)** |

Uploads go browser/SDK → presigned URL → staging bucket, scoped to one key, one hour. SeaweedFS allows only
the UI origin for browser uploads.

## Files

User files ([FILES.md](FILES.md)) sit in their own bucket under opaque keys; every access is a presigned URL issued
after grants (`READ_VOLUME` / `WRITE_VOLUME`), delegated-token scopes (`files:read` / `files:write`) and the
path policy (`users/{sub}/**` belongs to that subject for agent tokens). Paths are normalised and traversal is
refused. Mutations are audited with ids and path hashes only. Tenant provisioning needs an admin or the
provisioner group and is closed to delegated tokens.

## Boundaries and secrets

- Errors are RFC 9457 problems with a request id; secrets, state paths and storage keys are redacted.
- Secrets live in `.env` (mode 0600, git-ignored) in development, never on a command line; the attach CLI
  reads the worker token from an environment variable.
- Remote service URLs must be HTTPS and carry no credentials; plain HTTP needs an explicit host opt-in.
- Two records, both with principal and acting agent: the **audit log** (`GET /api/v1/audit-events`, admin
  only) covers catalog changes, grants, imports, pipelines and compute attachment; **statement history**
  (`GET /api/v1/statements`, own statements, `?all=true` for admins) records every statement, denied ones
  included, with tables, row count and timing.

## Verified by tests

Unauthenticated, wrong-audience, expired, tampered and forged tokens; tampered, expired, replayed and
foreign-signed tickets; access tokens used as tickets; path escapes from a signed statement; worker job
payload prefix checks; invisible-table 404 versus denied 403; grant changes taking effect immediately;
provisioning fields rejected; the control plane never importing an engine or spawning a process.

## Data protection inside the platform

- **Row filters and column masks** are enforced by the planner, per reader, before a statement is signed; see
  [OPERATIONS.md](OPERATIONS.md#access-policies-row-filters-and-column-masks).
- **Storage credentials** can be vended per statement and scoped to the tables it reads, so workers hold no storage
  key for reads; see [OPERATIONS.md](OPERATIONS.md#storage-credentials-vending).
- **Internal mTLS** between API, controller and workers; see [OPERATIONS.md](OPERATIONS.md#internal-mtls).
- **Fair use**: per-principal rate, concurrency and scan budget.
- **Audit**: tamper-evident hash chain, SIEM export (NDJSON/CEF).

## Verified by tests (additions)

Policy bypass attempts (predicates, joins, subqueries, CTEs, EXISTS on masked values; views and materialized views
as definer; warm result cache after a policy change; agents isolated by tenant), credential scoping against a real
STS (a worker holding vended credentials for one table is refused the other; a keyless worker reads nothing),
mutual TLS with a certificate from another authority, audit tampering (edit, forge, delete), and controller failover
with exactly one active controller.

## Known gaps

Credentials are vended for statements only (batch jobs keep their key, because they write). Agent tokens are not
bound to the gateway's client certificate. Rate limits are per API replica. Audit hash chains detect tampering but
cannot stop a database administrator from rewriting the whole tail; export events off-box. No SCIM, and no HSM or
KMS integration for the token-signing key. Encryption at rest is the storage layer's.
