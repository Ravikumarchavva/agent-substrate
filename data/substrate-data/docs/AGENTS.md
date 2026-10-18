# Connecting agent-substrate (code interpreter)

The data service and agent-substrate are separate deployments and are assumed to run **on different machines,
networks and possibly clouds**. They share no filesystem, no key file and no process. Everything that crosses
between them is authenticated and encrypted:

```
 agent host (agent-substrate)                                   data host (Substrate Data)
┌──────────────────────────────────────────────┐              ┌───────────────────────────────┐
│ nsjail sandbox (no network)                  │              │ API (TLS 1.3, optional mTLS)  │
│   substrate_data_client                      │              │   verifies token              │
│        │  Unix socket + token file           │              │   authorizes, plans, audits   │
│        ▼  (this host only)                   │   HTTPS      │            │                  │
│ DataGateway ── allow-list, TLS, client cert ─┼─────────────►│            ▼ signed tickets   │
│ DataTokenMinter ── signs per-run tokens      │              │   attached compute → storage  │
│ GET /.well-known/data-token-jwks.json ◄──────┼── HTTPS ─────┤ fetches the public keys       │
└──────────────────────────────────────────────┘              └───────────────────────────────┘
```

Why there is a Unix socket at all: the jail has **no network namespace access**, so it cannot open a TCP
connection to anything, which is the point. The socket exists only on the agent host, as the last hop between
the jail and the gateway; it never connects two machines. The gateway makes the one outbound HTTPS connection.

## What the agent does

```python
import substrate_data_client as sd

sd.search("taxi")                       # tables and views the caller may read
sd.describe("main.nyc.monthly_revenue") # columns, size, partitioning, sample rows
df = sd.sql("SELECT * FROM main.nyc.monthly_revenue")   # Polars; filters/aggregation run on the worker
df.write_excel("/workspace/report.xlsx")
```

Large results: aggregate in SQL, or iterate `sd.stream(sql)` in Arrow batches. `sd.sql`/`sd.arrow` refuse to
hold more than 512 MiB (`ResultTooLarge`, with advice in the message). The model is told this by the tool
description and the `data_analysis` skill; a `data_catalog` tool lets it discover names before it writes code.

## Trust between the two services

- **Tokens.** For each run agent-substrate signs a short-lived (60–330 s) **EdDSA** token: `sub` is the end user,
  groups are `tenant-<id>`, `act` names the agent, scope is `data:read`, and `jti` is a unique id. Context claims
  (`tenant_id`, `thread_id`, `agent_id`) and the `jti` (as `token_id`) are stored with every statement.
- **Keys are fetched, never copied.** agent-substrate serves its **public** keys at
  `https://<backend>/.well-known/data-token-jwks.json`. The data service lists it as a trusted issuer by URL:

  ```bash
  SUBSTRATE_DATA_TRUSTED_ISSUERS='[{"issuer":"agent-substrate","audience":"substrate-data",
    "jwks_uri":"https://agents.example.eu/.well-known/data-token-jwks.json",
    "jwks_ca_file":"/etc/substrate-data/agents-ca.pem"}]'
  ```

  `jwks_uri` must be `https://` (plain `http://` only to localhost); `jwks_ca_file` is for a private CA. Fields:
  `issuer`, `audience`, `jwks_uri`, `jwks_ca_file`, `algorithms` (default `["EdDSA"]`), `username_claim`,
  `group_claim`, `read_only` (default `true`).
- **Rotation without coordination.** Keys are cached (5 min). A token naming an unknown `kid` triggers one
  refresh, at most once per 30 s, so random key ids cannot make the data service hammer the issuer. If the agent
  side is down, cached keys keep working for up to an hour and then fail closed (`503`).
  To rotate: on the agent side rename `data-token.pem` to `data-token.previous.pem` and restart. A new key signs,
  both public keys are published, and after 5 minutes the previous file can be deleted. Nothing changes on the
  data side.
- **Read-only, never admin.** `data:read` tokens may run `SELECT` statements, cancel them and browse the catalog.
  Anything else is `403 token-read-only`; a trusted issuer can never mint a metastore admin.
- **Least privilege.** Grant the tenant group (`group:tenant-<id>`) `USE_CATALOG`/`USE_SCHEMA` and `SELECT` on
  **views** where you can: definer-rights views give an agent an aggregated slice without raw-table access. A
  table with no privilege answers `404`.

## Transport

| Hop | Protection |
| --- | --- |
| Gateway → data API | HTTPS, server certificate verified against `DATA_SERVICE_CA_FILE` (or the system store), TLS 1.2+ on the client; the API enforces **TLS 1.3** by default. Optional **mutual TLS**: the gateway presents `DATA_CLIENT_CERT_FILE`/`DATA_CLIENT_KEY_FILE`, and the API requires a certificate signed by `SUBSTRATE_DATA_TLS_CLIENT_CA_FILE`. |
| Data API → agent-substrate keys endpoint | HTTPS, verified, no redirects, 256 KiB cap |
| Sandbox → gateway | Unix socket on the agent host, `0700` directory, `0600` socket; the token is a `0400` file in a separate `0700` directory removed after the run, never an environment variable or argument |

The data API refuses to serve plaintext on any non-loopback address. Give it
`SUBSTRATE_DATA_TLS_CERT_FILE` and `SUBSTRATE_DATA_TLS_KEY_FILE`, or set `SUBSTRATE_DATA_BEHIND_TLS_PROXY=true` if a
proxy or service mesh you operate terminates TLS in front of it. The gateway refuses a remote `http://` data
service URL at startup.

**The gateway is the sandbox's single egress point and enforces an allow-list**: `GET` of `/api/v1/search` and
the catalog browse paths, `POST /api/v1/statements` and `…:cancel`. Everything else (uploads, grants, compute,
imports, statement history, `..` path tricks) is refused on the agent host and never forwarded. It forwards only
`Authorization`, content negotiation and trace headers, ignores any `Host` or absolute URL from the sandbox,
follows no redirects, keeps no cookies, ignores proxy environment variables, caps request bodies at 1 MiB and
concurrency at 64, and logs an unreachable data service or rejected certificate at startup.

## Set up

1. **Data host.** Run the API with TLS (above) and `SUBSTRATE_DATA_TRUSTED_ISSUERS` pointing at the agent
   platform's keys URL. Optionally require mutual TLS.
2. **Grants.** `USE_CATALOG`/`USE_SCHEMA` on the schema and `SELECT` on views or tables to `group:tenant-<id>`.
3. **Agent host** (`apps/substrate-cloud`): `DATA_SERVICE_URL` (`https://…`), optionally `DATA_SERVICE_CA_FILE`,
   `DATA_CLIENT_CERT_FILE`, `DATA_CLIENT_KEY_FILE`, `DATA_TOKEN_ISSUER`, `DATA_TOKEN_AUDIENCE`,
   `DATA_TOKEN_KEY_FILE`, `DATA_RELAY_DIR`. An unset URL disables the feature. Expose the keys path on the
   backend's public address.
4. **Sandbox Python.** `substrate-data-client` (in `client/`) must be importable by the sandbox interpreter, with
   `polars`, `pyarrow`, `httpx`, `xlsxwriter` and `kaleido`. agent-substrate's lockfile is intentionally not
   changed by this repo.

## Verify

`AGENT_VENV_DIR=… AGENT_PYTHON=… scripts/e2e_agent_sandbox/run.sh` builds two certificate domains and runs the
real tool in a real nsjail jail against an API that requires TLS 1.3 and client certificates. The agent
container receives only the CA, its own client certificate and its keys-endpoint certificate, never the data
host's private key. It checks that plaintext and certificate-less clients are refused, that the data service
fetched the agent's keys over HTTPS, that the agent sees only the granted view (raw table `404`), that the
internet is unreachable, and that a chart and an Excel file are produced. See
[scripts/e2e_agent_sandbox/README.md](../scripts/e2e_agent_sandbox/README.md). EU-oriented controls and their
limits are in [COMPLIANCE.md](COMPLIANCE.md).

## Limits

nsjail runtime only; the Kubernetes runtime has no data access yet. Use row filters on `tenant = current_tenant()` for tenant isolation (see OPERATIONS.md), or views.
Tokens are not bound to the gateway's certificate (the API cannot see the peer certificate behind uvicorn; a
mesh that terminates mTLS can enforce this). Tenant isolation is only as strong as the grants you give
`group:tenant-<id>`.
