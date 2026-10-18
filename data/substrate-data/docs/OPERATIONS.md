# Operating Substrate Data

## Catalog database: SQLite or PostgreSQL

The control plane keeps governed metadata (catalogs, grants, audit, history) in one database.

| | SQLite (default) | PostgreSQL |
| --- | --- | --- |
| Use | development, one API process | production, several API replicas, high availability |
| Setting | `SUBSTRATE_DATA_METADATA=/path/catalog.sqlite` | `SUBSTRATE_DATA_METADATA_URL=postgresql://user:pass@host/db?sslmode=verify-full` |

- A PostgreSQL URL **must** carry `sslmode=require|verify-ca|verify-full` unless the database is on the same machine;
  the service refuses to start otherwise. Prefer `verify-full` with the CA in `sslrootcert`.
- Schema is created on first start (safe when replicas start together) and uses 64-bit integers (row counts and
  sizes pass two billion), a native full-text index for document search, and the same SQL as SQLite.
- Run any number of API replicas against one database. They serve requests independently; **one** of them (the
  holder of a PostgreSQL advisory lock, taken over automatically if it dies) commits finished batch jobs to Iceberg.
  Commits are idempotent by write id, so a takeover never double-registers files.
- Cancelling a statement works on the replica that started it. The compute **controller** has its own
  database setting; see "Controller high availability" below.
- Test it: `make test-postgres` runs the whole suite against a throwaway PostgreSQL in Docker.

## Controller high availability

By default the controller keeps its job queue in a local SQLite file and refuses to start if another controller
owns that directory. To run several, give them a shared PostgreSQL database (separate from the catalog's):

```bash
SUBSTRATE_DATA_CONTROLLER_STATE_URL=postgresql://substrate:secret@db.example.eu/controller?sslmode=verify-full
```

One controller is elected (a PostgreSQL advisory lock held on its own connection) and schedules; the others stay up
on standby and answer `503 not-leader` with `Retry-After: 1` on `/internal/v1/*` and on `/readyz`, so a load
balancer or Kubernetes `Service` using readiness sends traffic only to the leader. If the leader dies (or its
database session breaks), the server releases the lock and a standby takes over within about a second. The new
leader asks workers to stop whatever the old one had started and replays it, which is exactly the recovery a
restart already performs: nothing is committed twice because each attempt writes under a fresh write id and only
the control plane publishes files. Queued jobs, attached compute and idempotency keys live in the shared
database and survive. A leader that loses the lock stops scheduling immediately.

## Table maintenance

`POST /api/v1/catalogs/{c}/schemas/{s}/tables/{t}:optimize` (needs `MODIFY`) rewrites files on attached compute and
swaps them in one atomic Iceberg commit. Only the files it planned are replaced: rows appended meanwhile are kept,
and if another rewrite got there first the swap is refused (`409 table-changed`) rather than losing data.

| Body | Effect |
| --- | --- |
| `{"strategy": "compact"}` | merge files under 75% of `target_file_mb` (default 256) |
| `{"strategy": "sort", "sort_by": ["PULocationID"]}` | rewrite ordered by those columns, so files get tight min/max ranges and the planner skips more of them |
| `{"strategy": "zorder", "sort_by": ["x", "y"]}` | order by interleaving 2 to 4 columns, so files are narrow in *all* of them (a sort does that for the first column only; measured: a box on the second column touches under 75% of files after Z-order, all of them after a sort) |
| `"bloom_columns": ["id"]` | write Parquet bloom filters on those columns: equality filters (`WHERE id = 42`) skip row groups that cannot hold the value even when min/max cannot tell |
| `"where": [{"column": "ts", "op": "ge", "value": "2026-03-01"}]` | only rewrite files that can match: one partition at a time |

`GET …/tables/{t}/maintenance-runs` lists runs. A table has one maintenance operation at a time.

`POST …/tables/{t}:expire {older_than_hours, retain_last, remove_orphans}` forgets snapshots older than the window
(never the current one, never the newest `retain_last`; time travel to them ends) and deletes the data files that
only they used, on attached compute. With `remove_orphans` it also deletes files in the table's `data/` folder
that no snapshot references **and** are older than `orphan_min_age_hours` (at least 12, default 24: jobs run up to
six hours, so a younger unreferenced file may belong to a write that has not committed yet). The control plane's
storage key therefore needs *list* on the warehouse bucket (it already has read/write there). A delete job
refuses anything that is not a `.parquet` file under a `data/` folder of the warehouse.

**Memory.** Sorting holds its input in memory: measured at roughly 30x the compressed Parquet size. Inputs over
`SUBSTRATE_DATA_SORT_MAX_INPUT_MB` (default 256) are refused with `413 too-large-to-sort`; sort a partition at a
time with `where`. Every batch job also runs under a memory ceiling (`SUBSTRATE_DATA_WORKER_JOB_MEMORY_MB`,
default half of the worker's memory): a job that exceeds it is stopped and reported, never allowed to take the
host down. With several concurrent jobs per worker, lower it.

## Materialized views

`POST …/schemas/{s}/materialized-views {name, sql}` stores a query's result as an Iceberg table. Reads are ordinary
pruned table scans; nothing is recomputed. `POST …/tables/{t}:refresh` recomputes it (`?full=true` forces a full
rebuild).

- The definition is planned and authorized as its **definer** (the creator, as they were when they created it), like
  a logical view, **including the access policies of the tables it reads**: the stored rows are what the definer may
  see. Readers need `SELECT` on the materialized view only. Refreshing needs `MODIFY`. If the definer later loses
  access to a source, refresh fails with `403 materialized-view-definer-access`; the last good rows keep serving.
- A worker streams the result into Parquet; the control plane commits it atomically. Readers see old rows or new, never a mix.
- `GET …/tables/{t}` shows `materialized: {status, refreshed_at, stale, sources, incremental, refresh_every_minutes}`.
  `stale` is true when a source table has a newer snapshot than the last refresh read.
- `partitioning` works as for tables.
- `refresh_every_minutes` (5 to 10080) refreshes it by itself, as its owner, when due **and** a source changed. The
  replica holding the publisher role runs the schedule.
- `incremental: true` processes only the source files added since the last refresh and appends them. It is accepted
  only for a plain filter/projection of one table (no aggregates, DISTINCT, joins, windows, LIMIT, ORDER BY, CTEs,
  subqueries or non-deterministic functions), because only then does "new files" mean "new rows". At refresh time it
  falls back to a full recompute whenever any snapshot since the last refresh was not a plain append (an overwrite, a
  delete, a compaction) or the starting snapshot was expired, so it can never append duplicates. Aggregates are
  always recomputed in full.

## Worker result cache and routing

A worker remembers results of up to `SUBSTRATE_DATA_WORKER_RESULT_CACHE_ENTRY_MB` (8) each, 128 MiB in total
(`…_RESULT_CACHE_MB`, 0 disables). The key is the rewritten SQL plus the **exact file list** the statement reads,
so it is current by construction: a commit changes the files and therefore the key. Authorization, access policies
and planning run before any lookup (a policy is part of the rewritten SQL), so someone who may not read a table is
refused whether or not the answer is cached. Statements using `now()`, `random()`, `current_user` or any function
the planner does not recognise are never cached. Responses report `result_cache: hit|miss`.

With several SQL workers, statements are routed by rendezvous hashing on the tables they read, so repeated work on
the same tables lands on the same worker and its warm caches; losing a worker moves only that worker's tables.

## Access policies: row filters and column masks

`PUT /api/v1/catalogs/{c}/schemas/{s}/tables/{t}/policy` (owner or admin; also `GET`, `DELETE`):

```json
{"row_filter": "(is_member('eu-analysts') AND region = 'EU') OR (is_member('us-analysts') AND region = 'US')",
 "column_masks": [{"column": "email", "mask": "hash"}, {"column": "salary", "mask": "null"},
                  {"column": "name", "mask": "redact"},
                  {"column": "salary", "mask": "expression", "expression": "round(salary, -3)"}],
 "exempt": ["group:auditors"]}
```

The planner replaces the table in every statement with a subquery that applies the mask expressions and the row
filter for *that reader*, before the statement is signed; a worker only ever sees SQL that already contains the
rules. Everything the reader writes (WHERE, JOIN, GROUP BY, subqueries, CTEs) runs on the masked output, so
`WHERE salary > 0` cannot probe a hidden value. Masks: `null`, `redact` (`***` for text, `NULL` otherwise), `hash`
(salted SHA-256; needs `SUBSTRATE_DATA_MASK_SALT`, refused otherwise because unsalted hashes of emails or ids can be
reversed by guessing; changing the salt changes every hash) and `expression`. Expressions are a small safe language:
the table's columns, literals, operators, CASE/CAST and a short function allow-list; no subqueries, no table access,
no unknown functions. `is_member('group')`, `current_user()`, `current_tenant()` (an agent run's tenant) and
`current_principal()` are resolved to literals per reader, so rules like `tenant = current_tenant()` give agents tenant
isolation. Filters the reader wrote on *unmasked* columns are repeated inside the subquery, so file pruning still
works. A reader matching no rule sees an empty table, not an error. Policies are checked at every statement, so a
change applies to the very next query even when answers are cached.

Be aware: a **view or materialized view** is evaluated as its definer, so a view owned by an exempt user is a
deliberate, auditable way to share an aggregate over data the readers cannot see raw; a view owned by someone
the policy applies to shows only what that owner may see. Masking protects values, not their existence: with a
row filter, counts and aggregates are over the visible rows only.

## Fair use

Per authenticated principal: a request rate (`SUBSTRATE_DATA_RATE_LIMIT_PER_MINUTE`, default 1200, token bucket,
`429` with `Retry-After`), concurrent statements (`…_MAX_CONCURRENT_STATEMENTS` 16, and per agent tenant
`…_PER_TENANT` 64), and an optional daily scan budget (`…_DAILY_SCAN_BYTES_PER_PRINCIPAL`, 0 = off) counted from the
bytes the planner chose to read, shared by all replicas through the catalog and reset at 00:00 UTC. Request rate and
concurrency are counted per API replica. Usage rows are deleted with the rest of the history after the retention
period.

## Audit trail

Audit events form a hash chain: each stores a SHA-256 over its fields and its predecessor's hash. Altering or
deleting an event breaks every hash after it, which `GET /api/v1/audit-events:verify` (admin) detects and
locates (`first_bad_id`). Retention may trim the oldest events; the chain verifies from the first one that remains.
Writers are serialized (PostgreSQL: a table lock), so the chain cannot fork. This detects tampering after the fact
by anyone who can edit the database; it does not stop a database administrator who rewrites the whole tail, so
ship events off-box too:

`GET /api/v1/audit-events:export?after=<id>&limit=<n>&format=ndjson|cef` (admin) returns events oldest first;
`X-Next-After` is the last id, to pass as `after` next time (a collector polls this). CEF output suits most SIEMs.

## Storage credentials (vending)

By default every SQL worker holds one long-lived key that reads the warehouse. Set
`SUBSTRATE_DATA_STORAGE_STS_ROLE_ARN` (and optionally `…_STS_ENDPOINT`) and the control plane instead calls the
object store's STS `AssumeRole` per statement with an inline session policy allowing `s3:GetObject` only under the
folders of the tables that statement was planned against, for 15 minutes. The credentials ride in the signed
ticket; the worker installs them as a temporary, scoped DuckDB secret (never written to disk). Credentials are
cached per set of tables, so there is no STS call per statement in steady state. SQL workers then need **no storage
key at all**: a compromised one can read only what live tickets allow. The session policy is enforced by the object
store, not by this code. Works with AWS S3, SeaweedFS (with an `-s3.iam.config` STS section and a role),
MinIO and Ceph. Limits: a statement may read at most 25 tables; batch jobs (ingest, optimize, materialize, delete)
write and keep their own key; the ticket carries the credentials, so keep the API-to-worker link on mTLS.

## Internal mTLS

The API, controller and workers authenticate each other with certificates when configured. Servers: set
`SUBSTRATE_DATA_TLS_CERT_FILE`/`…_KEY_FILE` and `…_TLS_CLIENT_CA_FILE` (require client certificates) on the
controller and each worker as well as the API (all three refuse plaintext on a non-loopback address unless
`…_BEHIND_TLS_PROXY=true`). Clients: `SUBSTRATE_DATA_INTERNAL_CA_FILE` (the CA that signed the servers) and
`…_INTERNAL_CERT_FILE`/`…_INTERNAL_KEY_FILE` (this process's own certificate) on the API and controller. Bearer
tokens remain as a second factor. Attached-compute URLs and the controller URL must be `https://` off loopback.

## Ingest at scale

Measured on this development machine (20 cores, local SeaweedFS), CSV to a month-partitioned Iceberg table through
presigned multipart upload and one jobs worker (`benchmarks/ingest.py`, job ceiling 4 GiB):

| Input | Time | Rate | Parquet | Jobs worker memory (peak) |
| --- | --- | --- | --- | --- |
| 20M rows, 1.0 GB CSV | 22 s | 45 MB/s | 11x smaller | 1.7 GB |
| 100M rows, 4.9 GB CSV | 60 s | 81 MB/s | 11x smaller | 2.7 GB |

Every row was present exactly once and a one-month query scanned 2 of 23 files. Five times the input took 1.6 times
the memory. The plausible reason is that Polars buffers up to one target-size file per partition, which would
plateau, but **that is a hypothesis, not a measurement**, and this was not run at 300 to 500 GB: that is
extrapolation (about 1.5 hours at the rate above, if throughput holds). The job memory ceiling is what makes a wrong
extrapolation safe: a job that outgrows it is stopped and reported.

## Files (volumes bucket, quotas, sweeper)

Create the `volumes` bucket (`SUBSTRATE_DATA_VOLUMES_BUCKET`, default `substrate-volumes`; `scripts/infra_ready.py` does in
development) and give **only the control plane's S3 identity** read/write/list on it; a worker identity must carry
no action on it. Settings, quotas, version retention, the expiry sweeper (it runs inside every API replica) and the
tenant provisioning role are in [FILES.md](FILES.md). Raise `SUBSTRATE_DATA_RATE_LIMIT_PER_MINUTE` for file-heavy
agents. Set `SUBSTRATE_DATA_VOLUMES_PUBLIC_ENDPOINT_URL` when clients reach storage at another address than the control
plane does.

## Not built

- On-disk (NVMe) block cache: DuckDB runs with extensions locked, and the community extension that provides one
  cannot be loaded. The in-memory file and metadata caches and the result cache above are what exist.
- Incremental refresh for aggregates (they are always recomputed in full).
- Token binding to the gateway's certificate, and rate limits that are global rather than per replica.
