# Loamz rewrite: handoff

You are continuing a one-shot rewrite of this monorepo into a new product family, **Loamz**. Read this file, then `docs/rewrite/porting.md` (binding rules), `docs/guides/api.md`, `docs/rewrite/parity.md` (the checklist) and `.importlinter`. The full approved plan is `PLAN.md` next to this file.

Author every commit as the repo-local git identity (`Ravikumarchavva`, `ravikumarchavva@outlook.com`). **Never add Co-Authored-By or any AI attribution lines.** Never push to `main`; never force-push; ask the owner before any push to a remote other than the `rewrite/loamz` branch.

## What Loamz is

Two products in one monorepo, one PEP 420 namespace, separate PyPI distributions:

- **Loamz Data** (the foundation): anything that stores, retrieves or governs data.
  `loamz-data-storage` (`loamz.data.storage`, embeddable engine: SQL on SQLite/Postgres, files, memory, vectors, documents),
  `loamz-data-client` (`loamz.data.client`), `loamz-data-server` (`loamz.data.server`), `loamz-data-reader` (`loamz.data.reader`, Python 3.13, own lock).
- **Loamz Agents** (`loamz-agents`, `loamz.agents`): anything that thinks, decides or acts; owns only its application state.
- Shared: `loamz-identity` (`loamz.identity`: token verification and `tenancy`, the single source for tenant alias, names, groups, roles, scopes, keys), `@loamz/oidc-bff`, meta package `loamz`.
- **Rule: agents depend on data, never the reverse** (enforced by `.importlinter`).

Replaces the old trees (still present as the source to port from; deleted at the cut-over, never import them from new code):
`agents/agent-substrate`, `agents/substrate-cloud`, `data/substrate-data*`, `identity/substrate-identity`, `data/{embedding-reranker,inference-pool,document-intelligence}`, UIs `agents/substrate-ui` and `data/substrate-data-web`.

## State (as of this handoff)

Branch **`rewrite/loamz`** (pushed to origin) holds, all green:
- scaffolding for every distribution, `loamz.identity` + `tenancy` (66 tests);
- `loamz.data.storage.sql` (SQLite/Postgres, migrations, `under`) + `errors.py` + `testing/` (tests on both databases);
- `loamz.agents` bootstrap (typed `Settings`, `Container` with `get(provider)`), `kernel/caller.py`, `server/` (create_app, RFC 9457), reference feature `features/people/preferences`;
- **Track K merged**: the whole agent engine (kernel, stores incl. thread headers, providers, toolkit, console, CLI, evals, testing). 1,622 tests pass in ~106 s with `-n 4`; 7 import contracts kept.

Two worktrees hold unmerged work (create with `git worktree list`); both were still running when this was written:
- **`.claude/worktrees/d-data`** (branch `loamz/d-data`, 6 commits): Loamz Data port: storage engine (files, memory, vectors, documents, remote embedders on one `Storage` host), client, server in feature folders with the inference worker role, reader, tests covering every data API operation. Looks nearly done; verify and merge.
- **`.claude/worktrees/f-features`** (branch `loamz/f-features`, 3 commits + 37 uncommitted files): Track F: port of all `substrate_cloud` features and the server onto the kernel. **Early**: feature registry, counters port, local file store, settings sections, in-process test server exist; most contexts are not ported yet.

## What remains, in order

1. **Merge Track D** into `rewrite/loamz`: `git merge loamz/d-data`, `uv lock`, `uv sync --all-packages --all-extras --all-groups`, run the gates (below). Reconcile: the kernel still holds local copies of content types (`KernelModel`, `TextBlock`…) and the `MemoryStore` port (`kernel/ports/memory.py`, marked `TODO(integration)`); point them at `loamz.data.storage` types where Track D defined them.
2. **Finish Track F** (agent features + server). Per `PLAN.md`: contexts `conversations, workspace, knowledge, directory, groups, automation, people, governance`; every live SQLAlchemy model becomes a feature store on `loamz.data.storage.sql` (SQL once, keyed by tenant; delete User/Element/Pipeline/PipelineRun; AdapterPipeline → automation); logic out of route functions into services (the old `chat()` is ~380 lines); microservices are the same app split by `--roles` (delete `services/`); re-add invariant I23 (server names the approver); port the minimal single-agent server (`/chat` SSE, `/agui`, approvals); generate `contracts/agents/v1/openapi.json` with a drift test. Source is `agents/substrate-cloud/src/substrate_cloud` (read-only reference; the 37 route modules are mapped per context in `docs/rewrite/parity.md` and the explore notes in `PLAN.md`). Tick all 161 Agents API operations in `parity.md` (`[x]` + covering test, or `[-] reason`).
3. **`loamz.agents.data`** (the `[data]` extra): `DataFileStore`, `DataLibrary`, `DataMemoryStore`, token minter, sandbox relay, through `loamz.data.client`. Source: `agents/agent-substrate/src/substrate/integrations/data/*` and `agents/substrate-cloud/src/substrate_cloud/data_access.py`. Needs the conformance suites run against a real in-process `loamz.data.server`.
4. **Deploy, CI, release** (rename everything): images `loamz-{agents,data,data-reader,agents-console,data-workspace}`, compose (data platform default; `agents` and `microservices` profiles), Helm `loamz-cell` (`agents.enabled`, `console.enabled`, `redis.enabled`, `agents.roles`), Keycloak realm `loamz` (clients renamed; keep the wiring fixes in `deploy/compose/compose.yml` and the realm: `agent-membership` client with `manage-realm`, trusted issuers, insecure-host opt-ins, `*.localhost` public names), generated `.env.example`/Helm schema from the settings models with drift tests, CI path filters and release-please packages per distribution, PyPI publishing order storage → identity → client → server/agents → meta. UIs: rename to `agents-console` and `data-workspace`, use `@loamz/oidc-bff`; the chat UI's callback is `/chat/auth/callback`; the chat UI must update error-code comparisons (codes are now kebab-case: `agent-crashed`, `budget-exhausted`, `content-filter`).
5. **Cut-over** (only when every gate is green): one commit deletes the old trees; update the private platform repo (`../agent-substrate-platform`; rename later to `loamz-platform`) to send only `tenant_id` to `POST /api/v1/tenants` (the alias is now derived by `tenancy.tenant_alias`) and to the new cell-admin contract; merge `rewrite/loamz` into `main`; publish final notices from the old PyPI names.
6. **Lakebase** (managed Postgres in Loamz Data, last): `databases` feature (securable kind `database`, privileges `CONNECT`/`MANAGE`; provisioner interface; `PgCredentialVendor` mirroring `tables/credentials.py` in the old server; sync to Iceberg as a `jobs` operation; erasure/export) and `loamz.agents.data.DataDatabase` (asks for the tenant database DSN, refreshes the login before it expires).
7. **Docs and demos**: ADRs superseding 0003, 0004, 0009 (brand/namespace; data is the foundation; one persistence model; roles instead of services), quickstarts per product, READMEs (PyPI needs absolute image URLs), demo videos following `deploy/compose/demo-user.py` (`demo`/`demo`) and `deploy/compose/e2e.py`.

## Gates (every merge and before the cut-over)

```
uv sync --all-packages --all-extras --all-groups
uv run ruff check . && uv run ruff format --check .                                  # root config, ruff pinned in uv.lock
uv run --package loamz-agents lint-imports --config .importlinter                   # all contracts kept
uv run --package loamz-agents pytest -q -n 4 --timeout=120                           # fast tier < 5 min total
LOAMZ_TEST_POSTGRES_URL=postgresql://postgres:postgres@localhost:5432/postgres ...   # same tests on PostgreSQL
uv run --package loamz-data-storage pytest -q ; (same for client, server, identity)
uv run python deploy/compose/e2e.py deploy/compose/.env                              # cell e2e (needs the compose cell up)
```
Also: `pip install loamz-data-server` into a clean venv must not pull `loamz-agents`; the whole agent server must run on SQLite with no Redis (in-memory bus/cache); `rg -i substrate` finds only ADR history notes after cut-over.

## Machine and tooling notes

- Linux (WSL2), 25 GB RAM, 20 cores: run at most 3 heavy jobs at once; never full Docker stacks in parallel. Use `env -u VIRTUAL_ENV uv ...` (a stray VIRTUAL_ENV from another project breaks `uv run`); `uv` only, never `pip`; ruff version is the one in `uv.lock`.
- Local Postgres for tests: `postgresql://postgres:postgres@localhost:5432/postgres` (the `substrate-cell-postgres` container or `substrate-data-infra`); Redis tests skip without Redis (`redis:7-alpine` on demand).
- The compose cell: `deploy/compose` (`./gen-env.sh`, `docker compose --profile monolith up -d`); Keycloak at `http://keycloak.localhost:8080` (no hosts-file edit needed), sign in at `localhost:3000/chat` as `demo`/`demo` after `python deploy/compose/demo-user.py deploy/compose/.env`. An OpenAI key is in `deploy/compose/.env` (git-ignored; never print or commit it); `CHAT_MODEL=openai/gpt-5.4-mini`.
- Fast tests must stay fast: mark Docker or minutes-long tests `slow`/`integration`; default tier excludes them.

## Do not

- Do not import the old trees from new code; do not keep shims or old names; do not port dead code (13 dead settings, User/Element/Pipeline models, `research_orchestrator`, Chainlit hooks, dead `app.state` objects).
- Do not read configuration anywhere but the settings models; no `app.state` outside `server/app.py`; no module-level singletons; no ORM; no Postgres-only SQL in feature stores.
- Do not push to `main`, force-push, or publish anything to PyPI/npm/GHCR without the owner. Never print or commit secrets or `.env` files.

## Accounts and names already claimed (owner did this)

Domain `loamz.com` bought; GitHub org `loamz` and npm org `@loamz` created; PyPI `loamz` 0.0.1 placeholder uploaded (other names `loamz-agents`, `loamz-data-*`, `loamz-identity` still free, reserve them with 0.0.1 placeholders before the first real release). Logo PNGs: `/tmp/.../scratchpad/logo2/` (the existing two-squares mark from `agents/substrate-ui/public/favicon.svg`).
