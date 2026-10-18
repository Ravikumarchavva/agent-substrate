# Architecture

Substrate Data splits a data platform into three planes. Only the data plane ever reads table bytes.

| Plane | Process | Owns | Never does |
| --- | --- | --- | --- |
| Control | API (`serve`, 8020) | Authentication, catalog, grants, audit, statement planning, Iceberg commits, signing tickets and upload URLs | Read or write table data |
| Scheduling | Controller (`controller`, 8014) | Attached-compute registry, health, the durable batch-job queue | Run queries, start or stop compute |
| Data | Workers (`worker`, you operate) | DuckDB statements, Polars ingest and ETL, direct object-store I/O | Open the catalog, see user credentials, read outside the warehouse |

## A query

1. The client sends `POST /api/v1/statements` with an OIDC access token.
2. The control plane verifies the token against the IdP's JWKS (signature, `iss`, `aud`, `exp`, `typ`).
3. `sql/analysis.py` parses the statement **once**: read-only policy, table references, and `column op literal`
   conjuncts that are safe to use for pruning (never through outer joins).
4. Table names resolve in one indexed catalog query; each table needs `SELECT` plus `USE_CATALOG` and
   `USE_SCHEMA` on its parents. A table the caller cannot see is reported as not found.
5. `tables/iceberg.py` plans each table: Iceberg partition and column statistics reduce the file list.
   Plans are cached per snapshot.
6. `sql/planner.py` rewrites table references to ticket aliases and signs an **Ed25519 ticket**
   (≤ 60 s, single use) holding the principal, rewritten SQL, exact file list, column types and limits.
7. The control plane forwards the ticket to a ready SQL worker. The worker verifies it, registers **only the
   listed files**, and runs the statement on a per-statement DuckDB cursor of a persistent, locked-down
   database (`enable_external_access=false`, `allowed_directories` = the warehouse, configuration locked).
8. Results stream back as Arrow IPC. JSON is produced once, only for browsers, and bounded by `max_rows`.

Cancellation, time limits, row/byte limits and statement history apply to every statement.

## A view

A view is a stored `SELECT` (`catalog/store.py`, `api/routes/views.py`). Creating one runs its definition
once as the creator with `LIMIT 0`: that validates the SQL, proves the creator may read what it touches,
and records the column names and types. At query time `sql/planner.py` replaces each view reference with
its definition as a subquery (names fully qualified, nesting up to 8 deep, cycles refused) and authorizes
every reference as the right subject: the caller needs `SELECT` on the view and on any table they wrote
themselves; what the view reads is checked against the view's **definer**, the creator's identity and
groups as of creation. A reader can therefore be granted a filtered or aggregated slice without access to
the table. If the owner later loses access, or an underlying table is dropped, queries fail clearly
(`view-definer-access`, `view-broken`) instead of leaking or returning partial data. Filters written
inside a view definition still prune files; filters written outside it are applied by DuckDB after.

## An import

1. `POST /api/v1/imports` authorizes the target and returns presigned URLs (single PUT or multipart).
2. The client uploads straight to the staging bucket; the API never carries file bytes.
3. `:complete` finishes the multipart uploads and queues work. For a new table, a worker first infers the
   schema; then a Polars worker streams the files once, normalizing types, partitioned, row groups
   about 1M rows and files about 256 MB, **directly into the table's `data/<write-id>/` location**.
4. The control plane commits all files in one Iceberg transaction tagged with the write id, so a replayed
   publication never registers the files twice. A killed worker leaves unreferenced files, never a corrupt
   table.

## Storage

- **Tables:** Iceberg v2 in a SeaweedFS table bucket (`warehouse`). SeaweedFS runs the REST catalog, so
  commits are compare-and-swap; Spark, Trino, DuckDB and PyIceberg can read the same tables.
- **Staging:** a separate bucket for uploads. Workers may read it, not write it.
- **Two storage identities.** The control plane's S3 key can commit and sign uploads; the workers' key can
  read and write data files but has no catalog or staging-write rights (`scripts/bootstrap.py`).
- **Control-plane state:** SQLite in WAL mode (`catalog/store.py`): securables, grants, audit, statement
  and import history, pipelines, durable publications. Schema is typed and indexed.

## Files

User files live in volumes and are served by the Files API ([FILES.md](FILES.md)): the control plane keeps paths,
versions, quotas and audit in the catalog database and signs URLs; bytes move directly between clients and a
dedicated `volumes` bucket whose key only the control plane holds.

## Layout

| Path | Role |
| --- | --- |
| `api/` | FastAPI app, `routes/` (catalog, statements, imports, workloads), publisher for finished jobs |
| `auth/` | OIDC verification, principals, Ed25519 tickets |
| `catalog/` | Securables, privilege inheritance, grants, audit, history |
| `files/` | Files domain: paths, ownership policy, quotas, versions, volumes bucket, sweeper |
| `sql/` | One-pass analysis and pruning filters; planner and ticket builder |
| `tables/` | Iceberg service (planning, commits, schema), presigned staging |
| `compute/` | Controller and its API, attach registry, job store; worker service, batch (Polars) |
| `core/` | Settings, problem details, HTTP conventions |
| `web/` | Next.js workspace with a backend-for-frontend session |

## Conventions

Routes live under `/api/v1` (public) and `/internal/v1` (controller and workers); breaking changes need a
new major path. Errors are RFC 9457 `application/problem+json` with a stable `type` and a request id.
Lists use opaque cursor tokens. Mutations accept `If-Match`; creating imports and pipelines returns
`Location`. `docs/openapi.json` is checked in and guarded by a test.

## Roadmap

Done: agent access ([AGENTS.md](AGENTS.md)), maintenance and materialized views, PostgreSQL catalog and
controller high availability, row filters and column masks, credential vending, internal mTLS, quotas, audit export
([OPERATIONS.md](OPERATIONS.md)).

Remaining: Kubernetes sandbox data access, an NVMe block cache, incremental refresh of aggregates, Arrow Flight or
Flight SQL for BI tools, SCIM, KMS/HSM for the token-signing key, global (not per replica) rate limits, and a
measured 300 to 500 GB ingest.
