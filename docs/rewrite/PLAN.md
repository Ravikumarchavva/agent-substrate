# Loamz: one clean rewrite of the monorepo

## Context

The system works end to end: a compose cell, Keycloak sign-in, chat over a governed file, and `deploy/compose/e2e.py` passes. But it grew in layers, and the structure no longer holds up. The explore pass measured the problems:

- **Three servers for one product.**
  - `substrate.server` in the library is 1.3k lines.
  - `substrate_cloud.monolith` has 37 route modules and about 250 endpoints.
  - The 9 microservices copy about 19 of those endpoints and come with their own duplicate ORM tables.
- **Two persistence systems in one database.**
  - The library's SQL-once `Database` runs on both SQLite and Postgres.
  - Alongside it are 19 SQLAlchemy models, 4 of them dead, using Postgres-only `UUID`/`JSONB`/`ARRAY` types, row-level security and ad-hoc migrations.
  - As a result the product cannot run on SQLite.
- **A service locator instead of a composition root.**
  - `factory.py` is 1,194 lines.
  - There are 302 `app.state` accesses and about 40 objects built in `lifespan`, some of them dead.
  - There are 118 settings in two classes, 13 of them dead.
  - The end-to-end run found 8 wiring bugs.
- **Logic lives in HTTP handlers.** `chat()` is about 380 lines; `files.py` has 914, `groups.py` 830 and `workspace.py` 724.
- **The products are coupled the wrong way.**
  - The data platform imports the agent library.
  - Tenancy is defined in three places.
  - The data platform can't be deployed without the agent server.
- **The name is not ours.**
  - Substrate Labs owns the PyPI name `substrate`, with import name `substrate`.
  - `substrate-data` and `substrate-sdk` belong to other people.
  - Our `import substrate` can be shadowed.

There are no users. The decision is a single full replacement, not an incremental migration: a new, clean library under one brand, built on explicit software principles and verified for parity before the old trees are deleted in one cut-over.

## Decisions (settled)

1. **Brand and namespace: `loamz`.**
   - Free as `.com`, `.ai` and `.dev` domains (RDAP), on PyPI (`loamz`, `loamz-agents`, `loamz-data-*`, `loamz-identity`), as the npm scope `@loamz`, and as a GitHub org.
   - Products are **Loamz Data** and **Loamz Agents**. Docs drop "Substrate".
2. **Direction: the data platform is the foundation, and agents are built on it.** This is the Databricks shape: lakehouse, catalog and Lakebase underneath; agents on top.
   - Rule: *anything that durably stores, retrieves or governs data belongs to Loamz Data.*
   - Agents compute. They own only their application schema, the way Airflow does.
   - Dependencies point from agents to data, never back.
3. **Packaging works like Google's: separate distributions in one implicit namespace (PEP 420).** Install only what you use, and each part is versioned by release-please.
4. **Microservices are the same app, split by role.** There is one codebase. `--roles` mounts a subset of feature routers per process. No route copies.
5. **Managed Postgres (Lakebase-style) is part of this plan**, as its last workstream.

## Distributions and namespace

`loamz/` and `loamz/data/` have no `__init__.py`; they are namespaces. Each leaf below is a regular package.

| Distribution | Import | Contents | Depends on |
|---|---|---|---|
| `loamz-data-storage` | `loamz.data.storage` | The embeddable engine: SQL `Database` (SQLite, Postgres), migrations, text search, vectors, files, memory, and documents (Library, Reader, OKF). Pure Python, no web, no LLM SDKs. Works like DuckDB embedded. | pydantic; `[postgres]`, `[pdf]`, `[ocr]` extras |
| `loamz-data-client` | `loamz.data.client` | Typed HTTP client, sync and async, `[arrow]` extra. Owns the data API contract. | httpx, `loamz-identity` (tenancy) |
| `loamz-data-server` | `loamz.data.server` | Data platform: catalog, sql, tables, compute, files, documents, memory, databases, tenants, privacy, audit. Worker roles `sql`, `jobs`, `documents`, `inference`. Absorbs embedding-reranker and inference-pool. | storage, client, identity, FastAPI, DuckDB, pyiceberg |
| `loamz-data-reader` | `loamz.data.reader` | The heavy document parser service (was document-intelligence). Python 3.13 because of Paddle; outside the uv workspace. | own lock |
| `loamz-identity` | `loamz.identity` | OIDC verifier, JWKS, FastAPI helpers, and **`tenancy`**: org id and alias rule, catalog/volume/database naming, `org:` groups, role and scope names, key parsing. | pyjwt |
| `loamz-agents` | `loamz.agents` | Agent library and agent server (`[server]` extra) | storage, identity; `[data]` adds client; provider extras |
| `loamz` (meta, no code) | — | `pip install loamz[agents,data]` convenience | the above |

JavaScript packages:
- `@loamz/oidc-bff`
- apps: `agents-console` (was substrate-ui) and `data-workspace` (was substrate-data-web)

Images: `ghcr.io/<org>/loamz-{agents,data,data-reader,agents-console,data-workspace}`. Helm chart: `loamz-cell`.

## Repo layout

```
loamz/                                   (GitHub repo; was agent-substrate)
  data/storage/      → loamz-data-storage      src/loamz/data/storage/…
  data/client/       → loamz-data-client       src/loamz/data/client/…
  data/server/       → loamz-data-server       src/loamz/data/server/…
  data/reader/       → loamz-data-reader (py3.13, own lock)
  data/workspace/    → data-workspace (Next.js)
  agents/library/    → loamz-agents            src/loamz/agents/…
  agents/console/    → agents-console (Next.js)
  identity/python/   → loamz-identity ;  identity/oidc-bff → @loamz/oidc-bff
  contracts/{agents,data}/v1/openapi.json      generated, drift-tested
  deploy/{compose,helm/loamz-cell,terraform,docker,keycloak}
  docs/{adr,guides}   meta/loamz (meta distribution)
```

## Inside `loamz.agents`: vertical feature slices on a small kernel

```
loamz/agents/
  __init__.py      curated public API (explicit __all__): Agent, ReActAgent, tool, Runtime, connect, …
  kernel/          types, errors (typed domain errors), ids (UUIDv7), clock
                   agents/ runtime/ (journal, worker, scheduler, channels; persistence on storage.sql)
                   context/ middleware/ tools/ (API, approval, chain) models/ (ports) wire/ (engine↔UI events)
                   events/ (typed event catalog + EventBus port) telemetry/ safety/
  stores/          agent facets on loamz.data.storage.sql: threads (+header), tasks, session, graph, runtime;
                   AgentStore = storage + facets
  providers/       llm/{openai,anthropic,gemini,compatible} embeddings/ redis/{cache,bus,short_term} tts/ safety/
  toolkit/         code (sandbox runtimes) · web · sql · mcp · skills · human_input · data_catalog
  data/            [data] extra: DataFileStore, DataLibrary, DataMemoryStore, DataDatabase (Lakebase),
                   token minter, sandbox relay; all through loamz.data.client
  features/        one folder per bounded context, same shape everywhere:
    <context>/model.py   value types         store.py  facet: SCHEMA + migrations + repository (scoped)
              service.py use cases (no HTTP)  api.py    thin FastAPI router   events.py  emitted/consumed
    conversations  threads, chat (run start, model choice, quotas, context assembly, intents), stream/history/search,
                   cancel, hitl/approvals, feedback, branches, shares, task boards, mcp apps, audio
    workspace      files, uploads, versions, browser, artifacts
    knowledge      rag, knowledge bases, enrichment queue, reader config
    directory      agent profiles, assembly, delegation, pairs, contacts, avatars, pins
    groups         groups, members, drives, media, live feed
    automation     schedules (+runs, catch-up), triggers, pipelines
    people         preferences, notifications (+email), memory view, usage, export-my-data
    governance     entitlements, admin/storage, erasure, rate limits, connectors (OAuth token caches)
  server/          create_app(container, roles): auth (loamz.identity), RFC 9457 errors, SSE, CORS, roles→routers
  bootstrap/       settings.py (one typed tree) · container.py (the only composition root)
  console/  cli.py (loamz-agents chat|serve|up|settings)  testing/ (conformance suites, fakes, harnesses)
```

`loamz.data.server` uses the same shape: a `kernel` (settings, container, errors, auth, audit) plus `features/` for catalog, sql, tables, compute, files, documents, memory, databases, tenants and privacy.

## Engineering principles, enforced rather than just documented

1. **Dependency rule.** Run import-linter on every package and fail CI on any violation.
   - `storage` imports nothing of ours. `data.server` never imports `agents`.
   - In `agents`: `kernel` imports nothing above it; `providers` and `toolkit` implement kernel ports and never import `features` or `server`.
   - `features/<x>` uses another feature only through its `service.py`.
   - `server` is the only importer of the `api.py` modules.
2. **Ports and adapters.** Every external system sits behind a port in `kernel` (LLM, embeddings, bus, cache, sandbox, data) with an in-memory fake in `testing/`. The same conformance suite runs against every adapter.
3. **One composition root.**
   - `bootstrap/container.py` builds everything once, lazily, and has typed `aclose`.
   - There is no `app.state` outside `server/app.py` and no module-level singletons.
   - FastAPI dependencies resolve from the container.
4. **One persistence model.**
   - SQL is written once for SQLite and Postgres, on `loamz.data.storage.sql`.
   - Each feature owns its tables: `SCHEMA` lists plus append-only migrations per component.
   - No ORM, no `create_all`.
   - Tenancy is enforced in the repository (bound `(tenant, user)` scope), proven by an isolation conformance suite on both databases.
   - The whole product runs on a laptop with SQLite.
5. **Typed configuration from one source.**
   - Each product has one pydantic `Settings` tree (`LOAMZ_AGENTS_*`, `LOAMZ_DATA_*`).
   - `.env.example`, compose env, the Helm `values.schema.json` and the config reference docs are *generated* from it, and drift-tested.
   - One DSN per database; driver variants are derived.
6. **Errors.**
   - Typed domain errors live in `kernel/errors`.
   - One table maps them to RFC 9457 problem types at the server edge, with a registry of problem types and stable `type` URIs.
   - The clients map the same table back.
7. **API conventions.** These follow Google's AIP rules and are written down in `docs/guides/api.md`. The OpenAPI contracts are generated and drift-tested; TypeScript types are generated for both UIs.
   - Everything is under `/api/v1`, using resource-oriented nouns.
   - Lists use cursor pagination; custom verbs use the `:verb` suffix.
   - Ids are UUIDv7; timestamps are UTC ISO-8601.
8. **Async all the way.**
   - No blocking I/O on the request path.
   - Background work runs on the runtime worker or scheduler, never as ad-hoc tasks started in `lifespan`.
9. **Events.**
   - A typed event catalog lives in `kernel/events`.
   - The bus port has Redis and in-memory adapters.
   - Role-split deployments talk only through events plus the shared database.
10. **Observability.** One telemetry facade provides structured logs, OpenTelemetry traces when an endpoint is set, and request ids carried through audit.
11. **Tests with budgets.**
    - Unit tests per feature run on in-memory SQLite, and the fast tier stays under 5 minutes (`-n`, timeouts).
    - Conformance suites run per port and per backend. Contract tests run per API.
    - One compose e2e per product, plus the cell e2e.
    - `slow` and `integration` markers keep the heavy runs out of the default.
12. **Deletion over compatibility.** No shims, no re-exports of old names. Dead code (the 13 settings, User/Element/Pipeline models, `research_orchestrator`, Chainlit hooks, dead `app.state` objects) is not ported.

## Execution: build side by side, prove parity, cut over once

1. **Freeze and inventory (me, ½ day).**
   - Write the parity checklist from the explore reports: every endpoint (about 250, as an exported route list), every feature, every library public symbol, CLI commands, the settings that are still live, and the e2e flows.
   - Move the agent contract test out of `substrate_cloud`.
   - Tag `pre-loamz`.
2. **Foundations (me, 2–3 days).**
   - Namespace scaffolding for all distributions (pyproject, uv workspace, import-linter per package).
   - `loamz.identity` with `tenancy`.
   - Kernel error and settings conventions.
   - `bootstrap` container skeleton.
   - The API conventions doc.
   - Templates for a feature slice and a facet, with one fully worked feature (`people.preferences`) to copy.
3. **Parallel porting (three tracks, subagents in worktrees, me integrating).** Code is ported from the old trees and restructured as it moves. Each track ends with its conformance and unit tests green.
   - **Track D, data:** `loamz.data.storage` (from `substrate.stores`, `documents/*`, `integrations/database`, `models/{remote,http}`), `loamz.data.server` (from `substrate_data` with feature folders; embedding-reranker and inference-pool become worker roles), and `loamz.data.client`. Includes the data-only compose.
   - **Track K, agent kernel:** `kernel`, `stores`, `providers`, `toolkit`, `data` adapters, `console` and `cli`, from the library. Includes the structure and invariant tests.
   - **Track F, agent features and server:** all nine `features/` and `server/`.
     - From `substrate_cloud` monolith routes and services, `stream`, `realtime`, `gdpr` and the `file_*`/`documents_library`/`storage_admin`/`trigger_store` modules.
     - Every SQLAlchemy model becomes a feature facet.
     - Role-split serving replaces `services/`.

   F depends on K's kernel APIs. K publishes those first (day 2–3 of the track), and F builds against them.
4. **Deploy, CI and release (me, 2 days).**
   - Dockerfiles and images, compose (data platform by default; `agents` and `microservices` profiles), Helm `loamz-cell` (`agents.enabled`, `console.enabled`, `redis.enabled`, `agents.roles`).
   - Keycloak realm `loamz` with clients renamed, plus the wiring fixes from today.
   - Generated env and schema files.
   - CI path filters per distribution, release-please packages, PyPI publishing in dependency order (storage → identity → client → server/agents → meta).
5. **Cut-over (me, 1 day).** The parity checklist is 100% done and every gate below is green. Then:
   - One commit deletes `agents/agent-substrate`, `agents/substrate-cloud`, `data/substrate-data*`, `identity/substrate-identity`, `data/{embedding-reranker,inference-pool,document-intelligence}` and the old JS package names.
   - Update the platform repo to `loamz-data-client` / tenancy and the new cell-admin contract.
   - Publish final `agent-substrate` and `substrate-data-client` releases whose README points to loamz, then yank older versions if you want.
6. **Lakebase (two tracks, 1–2 weeks).**
   - **`databases` feature in `loamz.data.server`:**
     - A securable `catalog_assets` kind `database` with privileges `CONNECT` and `MANAGE`.
     - A provisioner interface: the cell's Postgres first, an external admin DSN next, then cloud-managed Postgres.
     - `PgCredentialVendor`, mirroring `tables/credentials.py`: `CREATE ROLE … VALID UNTIL`, caching, a sweeper, and grants checked first.
     - Scopes `databases:*`.
     - Iceberg sync as a `jobs` operation with a watermark.
     - Erasure and export.
   - **`loamz.agents.data.DataDatabase`:** asks for the tenant's database, refreshes the login before it expires, and runs the agent store on it. Without `[data]` the agents use `LOAMZ_AGENTS_DATABASE_URL`, or SQLite by default.
7. **Docs and demos.**
   - New ADRs: brand and namespace; data as the foundation; one persistence model; roles instead of services. They supersede 0003, 0004 and 0009 where needed.
   - Per-product quickstarts and the demo videos (with `demo-user.py`).

## Reuse (port, don't rewrite)

- **SQL layer:** `stores/database.py` (Database, Tx, `migrate`, `under`), `stores/sqlite.py`, `integrations/database/postgres_database.py`, `stores/textsearch.py`.
- **Runtime persistence:** `runtime/persistence/*` (already written once over `Database`).
- **Facet pattern:** `workspace/tables.py` and `stores/task_tables.py`. Tenant scoping: `stores/scoped.py`, `stores/tenant.py`.
- **Tests:** `testing/conformance/*`; the harnesses `_modes.py`, `_oidc.py`, `_pki.py`, `_events.py`; and `data_files/conftest.py`.
- **Identity and errors:** `identity/substrate-identity` (verifier, JWKS), `shared/auth/*`, `shared/problem.py`.
- **Engine pieces:** the Redis `EventBus`, `documents/*` (Library, Reader, OKF), and `server/protocol` → `kernel/wire`.
- **From the data server:** grants and audit (hash chain) in `catalog/store.py`, `tables/credentials.py` (the vending pattern), the compute controller and workers, and Files.
- **Deploy:** `deploy/compose/{e2e.py,demo-user.py}` (renamed), `identity/oidc-bff`, and the Terraform modules.

## Verification (the cut-over gates)

- **Parity:** every checklist item maps to a test or an e2e step. The route inventory diff between old and new contains only intended renames and deletions.
- **Fast tier:** `make check`, which runs lint, import contracts for every package and the fast tests, in under 5 minutes.
- **Conformance:** every port on SQLite and Postgres, including the cross-tenant isolation suite.
- **Product on SQLite:** a full agent server with no Postgres and no Redis (in-memory bus and cache), running chat, files and groups.
- **Roles:** the `_modes.py` contract suite is green for `--roles all` and for split roles.
- **Data standalone:** in a clean venv, `pip install loamz-data-server` does not pull in `loamz-agents`. The data-only compose stack passes the data e2e.
- **Cell e2e:** compose with the `agents` profile passes the extended `e2e.py`: org, user, tenant, upload/download, catalog, chat over the file, cross-org isolation. A browser sign-in works with `demo`/`demo`.
- **Lakebase:** a database is provisioned, the agent server runs on it, the vended login is refreshed, a synced table is queryable through SQL, and tenant erasure drops the database.
- **Release:** the dry run publishes in dependency order. `pip install "loamz[agents,data]"` resolves. The Helm chart renders with `agents.enabled=false`. Images build.
- **No leftovers:** `rg -i "substrate"` finds only the ADR history notes.

## What you need to do first

- Register `loamz.com` (and `.ai`/`.dev` if you want them). Create the GitHub org `loamz` and the npm org `@loamz`.
- Add PyPI pending trusted publishers for `loamz`, `loamz-agents`, `loamz-data-storage`, `loamz-data-client`, `loamz-data-server`, `loamz-data-reader` and `loamz-identity`, all pointing at the renamed repo and `release.yml`.
- These names are free today but can be taken at any time.

## Estimate

About 6–8 weeks of focused work:
- foundations: 3 days
- parallel porting: about 3 weeks
- deploy and cut-over: about 3 days
- Lakebase: 1–2 weeks
- docs: about 1 week

Commits are authored by Ravikumarchavva with no attribution trailers. Branches are pushed as tracks merge, and `main` moves to loamz at the cut-over.
