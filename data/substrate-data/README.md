# Substrate Data

A governed, low-latency data platform for people and agents. Tables are Apache Iceberg on S3-compatible
storage; queries run on **attached compute** (DuckDB and Polars workers you operate); the control plane
authenticates, authorizes, plans and signs, and **never touches table data**.

```
people / agents / BI ── OIDC token ──► Control plane (/api/v1)
                                         catalog · grants · audit · planner · Iceberg commits
                                         │ signed 60 s ticket: exact files + rewritten SQL
                                         ▼
                                       Attached compute (workers)  ◄── job queue (controller)
                                         DuckDB statements, Polars ingest/ETL
                                         │ reads/writes object storage directly
                                         ▼
                                       SeaweedFS (S3 + Iceberg REST catalog)
```

- **Fast reads, one pass.** SQL is parsed once; partition and file statistics prune the file list before
  any bytes move; workers keep DuckDB warm and stream Arrow straight to the client.
- **Secure by construction.** Every route needs an OIDC access token. Workers run only signed tickets,
  can read only the warehouse bucket, and hold no catalog or user credentials.
- **Attach-only compute.** The application contains no engine and starts no processes. See
  [docs/COMPUTE.md](docs/COMPUTE.md).

Measured on the included 88M-row NYC taxi table (24 months, 66 files, 1.7 GB), warm, one machine:

| Query | p50 |
| --- | --- |
| `SELECT 42` | ~5 ms |
| `count(*)` | ~25 ms |
| One month, one zone (point lookup) | ~40 ms |
| One-month aggregate | ~35 ms |
| Full-table aggregate, 88M rows | ~0.27 s |

These are measurements (`make bench`), not guarantees; they depend on hardware and cache state.

**DuckDB or Polars?** Statements run on DuckDB, ingest and ETL on Polars, and both read the same Parquet.
`make bench-engines` runs identical queries on identical files and checks the answers match. Reading from
S3, DuckDB was 2–16× faster on every query (7 queries, 1.5 s total against 4.3 s for Polars' streaming
engine and 9.6 s for its default one). From local disk the gap narrows and Polars wins the small ones
(point lookup 23 ms vs 49 ms, `count(*)` 2 ms vs 6 ms) while DuckDB still wins the large group-bys. So the
gap is mostly DuckDB's object-store reader and metadata caching rather than raw compute. Polars stays the
right tool for dataframe work in the agent sandbox and for ingest.

## Quick start

Needs Docker, [uv](https://docs.astral.sh/uv/) and Node 22+.

```bash
make setup
make up            # SeaweedFS 4.48 (S3 + Iceberg catalog) and Keycloak; writes private config to .env
make dev           # controller (8014), API (8020), UI (3011). Starts no compute.
make dev-compute   # second terminal: stand-alone workers, attached to the controller
make demo          # loads the NYC taxi Parquet files in .workspace/datasets/nyc_taxi/yellow
```

Open **http://127.0.0.1:3011** and sign in as `alice` (admin) or `bob` (analyst); passwords are in `.env`
(`DEV_ALICE_PASSWORD`, `DEV_BOB_PASSWORD`). API reference: **http://127.0.0.1:8020/api/v1/docs**.

Python:

```python
from substrate_data_client import ClientCredentials, DataClient

client = DataClient("http://127.0.0.1:8020", token=ClientCredentials(ISSUER, CLIENT_ID, SECRET))
df = client.sql("SELECT PULocationID, count(*) n FROM main.nyc.yellow GROUP BY 1")  # polars.DataFrame
client.upload("main.nyc.trips", ["trips.parquet"],
              partitioning=[{"column": "pickup_time", "transform": "month"}])
```

See [examples/agent_data.py](examples/agent_data.py).

## What you get

| Area | Behaviour |
| --- | --- |
| Catalog | `catalog.schema.table` namespace, Unity Catalog-style privileges (`USE_CATALOG`, `USE_SCHEMA`, `SELECT`, `MODIFY`, `CREATE_*`) that inherit downward; invisible tables return 404, not 403 |
| Views | Named, governed queries (`POST …/views`): granted separately from their tables, read with the view owner's access, nestable, pruned by their own filters; `demo` creates three over the taxi data |
| Volumes | Governed file namespaces with private S3 prefixes, signed direct uploads/downloads (64 MiB per UI upload), paginated folder listings, and READ_VOLUME / WRITE_VOLUME grants |
| Models | Artifact registration with owner, framework, URI and USE_MODEL grants; initial artifact version 1, without model serving or version promotion |
| Tables | Iceberg v2 through SeaweedFS's REST catalog: partition transforms, per-file statistics, ACID commits, schema evolution; readable by Spark, Trino, DuckDB and PyIceberg |
| Ingest | Files upload straight to object storage (presigned, multipart); a Polars worker streams them once into partitioned Parquet written directly to the table location, then one atomic commit. CSV, Parquet, NDJSON; nanosecond timestamps normalized |
| Queries | `POST /api/v1/statements` returns JSON for browsers or `Accept: application/vnd.apache.arrow.stream` for streaming Arrow; cancel, time limits, byte limits, per-statement history |
| Governance | OIDC (users, groups, service principals; delegated `act` claims recorded), RFC 9457 errors, `If-Match` concurrency, audit log of grants, imports, statements |
| UI | Compact light/dark workspace with persistent collapsible navigation, catalog asset sections, searchable/filterable result grids, sticky pinned columns, column visibility, selection, copy and CSV export; SQL namespace selection is sent to execution and export. Catalog explorer, SQL editor, pipelines, retrieval, compute attachments; sign-in is a backend-for-frontend session, tokens never reach page scripts |

## Documentation

- [Architecture](docs/ARCHITECTURE.md) · [Attached compute](docs/COMPUTE.md) · [Security](docs/SECURITY.md) · [Agent access](docs/AGENTS.md) · [European controls](docs/COMPLIANCE.md) · [Operations](docs/OPERATIONS.md)
- [Decision: table format](docs/decisions/0001-table-format.md) · [Decision: sandbox transport](docs/decisions/0002-sandbox-data-transport.md)
- [OpenAPI contract](docs/openapi.json) (a test fails on any undeclared change)

## Testing

```bash
make test-unit        # no Docker
make test             # + throwaway SeaweedFS in Docker: import, prune, govern, cancel, recover
make test-postgres    # the same suite on a throwaway PostgreSQL catalog
make test-vending     # ... with the SQL worker holding no storage key (STS credential vending)
make test-processes   # every service as its own OS process, with crash recovery
make bench            # latency by query class against main.nyc.yellow
make check            # lint, format, types, UI build
```

The isolated UI check uses fixture API responses and a separate local Next.js server; it does not sign into or change a running data platform:

```bash
cd web
SUBSTRATE_WEB_DIST_DIR=.next/ui-review npx next dev --webpack --port 3023
# In another terminal (install Playwright Chromium, or supply CHROMIUM_PATH):
node scripts/workspace-ui-smoke.cjs
```

`UI_REVIEW_URL` changes the test server URL. Backend asset tests include real signed PUT/GET operations against a throwaway SeaweedFS container (`uv run pytest tests/test_assets.py`).

## Not included yet

Arrow Flight (the data path is HTTP streaming Arrow, also over the sandbox Unix socket), an on-disk block cache,
incremental refresh of aggregate materialized views, SCIM, KMS/HSM for the token-signing key, and code-interpreter
data access on Kubernetes (nsjail only). Ingest is measured to 100M rows / 5 GB of CSV, not to 500 GB. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#roadmap) and [docs/OPERATIONS.md](docs/OPERATIONS.md).
