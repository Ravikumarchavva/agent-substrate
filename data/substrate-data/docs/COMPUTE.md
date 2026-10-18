# Attached compute

Substrate Data is a control plane. It owns the catalog, governance and a durable batch-job queue, and it
routes work to compute that someone else operates. It contains **no compute engine** and **cannot create
compute**: no process or container provider, and no host `subprocess`/`fork`/`spawn` anywhere in the
application. `tests/test_attach_only.py` enforces this by scanning the source and by checking that the API
and controller never load DuckDB or Polars.

## Responsibilities

| Control plane (this application) | Compute operator (you) |
| --- | --- |
| Catalog, grants, audit, planning, ticket signing, job queue, retries, cancellation | Starting, sizing, scaling, patching and stopping workers |
| Attach, pause, resume, edit and detach records; health checks and routing | CPU, memory, GPU, isolation and networking of each worker |
| Verifies the worker's storage binding | The worker's own secrets and TLS |

Pausing, resuming or detaching a resource only changes where work is routed.

## Run and attach

```bash
make dev           # control plane
make dev-compute   # stand-in operator: runs sql, jobs and rag workers outside the application, attaches them
```

A worker is started with `substrate-data worker --role sql|jobs|rag|inference --port N` (or the image from
`make compute-image`; `docker run … substrate-data-worker:local sql 8011`). It needs, all as environment:

| Variable | Purpose |
| --- | --- |
| `SUBSTRATE_DATA_WORKER_TOKEN` | Shared secret the controller presents (32+ chars); distinct from the controller's own |
| `SUBSTRATE_DATA_S3_ENDPOINT_URL`, `…_ACCESS_KEY_ID`, `…_SECRET_ACCESS_KEY` | The **worker** storage identity (SQL workers can omit the key when credential vending is on) |
| `SUBSTRATE_DATA_TICKET_PUBLIC_KEY_FILE` | Public half of the ticket key (SQL and jobs workers) |
| `SUBSTRATE_DATA_STORAGE_ID` | Names the warehouse independent of how each process reaches it; must match everywhere |
| `SUBSTRATE_DATA_WORKER_MEMORY_LIMIT`, `…_THREADS`, `…_CONCURRENCY` | DuckDB memory cap, threads (default: all cores), simultaneous statements |
| `SUBSTRATE_DATA_INSECURE_HTTP_HOSTS` | Dev-only opt-in for plain HTTP to a named non-loopback host |

Workers never receive the controller token, the ticket **private** key, or catalog access.

Attach through the Compute screen, `POST /api/v1/compute/resources`, or
`substrate-data attach --name NAME --role ROLE --url URL --token-env VAR` (idempotent by name, token read
from the environment). Provisioning fields (`provider`, `cpus`, `memory_mb`, `gpus`, instance counts) are
rejected. The token is stored only in the controller's private state and never returned.

## Resource types

| Role | Runs |
| --- | --- |
| `sql` | Signed SQL statements |
| `jobs` | SQL statements, schema inference, ingest, pipeline transforms |
| `rag` | Text chunking |
| `inference` | Embedding batches through the operator's configured model endpoint |

A resource is routable only when `/internal/v1/health` answers, accepts the token, serves its declared role
and reports the same storage binding. Otherwise it shows as **unreachable** with the reason; queued work waits
until it recovers or hits its queue deadline. Use of attached compute is itself a grant (`USE_COMPUTE`).

## Interactive statements and batch jobs

Statements do **not** go through the queue. The API signs a ticket and talks to a SQL worker directly:
no controller hop, no polling, no stored result.

Batch work (ingest, transform, chunk, embed) is durable. Jobs move through `queued`, `running`,
`succeeded`, `failed`, `cancelled`; SQLite transactions serialize idempotency keys and execution claims; the
controller holds an exclusive lease on its state directory; attempt fencing stops a late response from an
older attempt overwriting a newer one; transport failures retry with bounded backoff, validation failures do
not. Dispatch is event-driven (a submit wakes the scheduler), not timer-polled.

Crash behaviour, tested with real processes: when the controller dies mid-ingest it asks workers to stop the
interrupted execution (best effort), revokes the lease and replays. Each attempt writes into a fresh
directory and only the control plane publishes files to a table, tagged with a write id, so exactly one copy
of the data is ever committed. If the API dies after queueing, publications resume on restart.

## Worker isolation

- **Tickets only.** `/internal/v1/statements` accepts only a valid, unexpired, single-use Ed25519 ticket;
  access tokens, forged and replayed tickets are rejected.
- **Locked DuckDB.** External access off, `allowed_directories` limited to the warehouse bucket, extensions
  not auto-installed, configuration locked. A signed `read_csv('/etc/passwd')`, a staging-bucket read, `ATTACH`
  or `COPY … TO` all fail.
- **Bounded jobs.** Job payloads are validated against allowed prefixes (writes only under the warehouse;
  ingest sources only from staging); each job runs in a forked child with a deadline and can be cancelled.
- **Least-privilege storage keys.** The worker key cannot commit table metadata or write staging.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET/POST /api/v1/compute/resources` | List / attach (admin) |
| `PUT /api/v1/compute/resources/{id}` | Edit a paused attachment |
| `POST /api/v1/compute/resources/{id}:start\|:stop\|:delete` | Resume routing / pause / detach (must be paused) |
| `GET /api/v1/compute/resources/{id}/events` | Connection events |
| `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`, `POST /api/v1/jobs/{id}:cancel` | Batch jobs (admin) |
| `/internal/v1/*` | Controller and worker contracts (not for clients) |

The full contract is [docs/openapi.json](openapi.json).

## Limits

The controller is a single active scheduler; for high availability run several against one PostgreSQL database
(`SUBSTRATE_DATA_CONTROLLER_STATE_URL`): one is elected, the others stand by ([OPERATIONS.md](OPERATIONS.md)). Workers
and the controller refuse to serve plaintext on a non-loopback address: give them certificates (and
`…_TLS_CLIENT_CA_FILE` for mutual TLS) or declare a TLS-terminating proxy. Worker memory and CPU are the operator's
concern, but a batch job is stopped when it exceeds its memory ceiling. Embedding workers call an existing model service; nothing downloads or hosts a model.
