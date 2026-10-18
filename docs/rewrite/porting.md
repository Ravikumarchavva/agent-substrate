# Porting guide: building Loamz from the pre-loamz trees

Read with the plan (`~/.claude/plans/i-want-to-build-eager-dusk.md`, summarised in `docs/rewrite/parity.md` intro).
The old trees (`agents/agent-substrate`, `agents/substrate-cloud`, `data/substrate-data*`, `identity/substrate-identity`,
`data/{embedding-reranker,inference-pool,document-intelligence}`) are the **source to port from**; they are deleted at
the cut-over, so never import them from new code.

## Rules
1. **Port, restructure, delete.** Move code into its new home, rename imports to `loamz.*`, and fix it to the
   principles below as it moves. Do not port dead code (13 dead settings, `User`/`Element`/`Pipeline*` models,
   `research_orchestrator`, Chainlit hooks, dead `app.state` objects). No shims, no re-exports of old names.
2. **Dependencies point down** (enforced by `.importlinter`, run `uv run --package loamz-agents lint-imports --config .importlinter`):
   storage ← identity ← client ← server (data); kernel ← stores ← providers/toolkit/data ← bootstrap ← features ← server (agents).
   Loamz Data never imports Loamz Agents.
3. **Errors**: raise `loamz.data.storage.errors.*` (`NotFoundError`, `ForbiddenError`, `ConflictError`,
   `InvalidRequestError`, `QuotaExceededError`, `ScopeViolationError`, `RateLimitedError`, `ServiceUnavailableError`, …).
   Never `HTTPException` in a service. Old names map: `ObjectNotFoundError`→`NotFoundError`,
   `WorkspaceQuotaExceededError`→`QuotaExceededError`, `StoreVersionError`→`SchemaVersionError`.
4. **Persistence**: SQL written once on `loamz.data.storage.sql` (`Database`, `migrate`, `under`), placeholders `?`,
   no ORM, no `create_all`, no Postgres-only types. Each feature owns its tables: `COMPONENT` + `SCRIPTS` (append-only)
   in its `store.py`; every query is keyed by `(tenant_id, …)`. Tests run the store on SQLite and PostgreSQL
   (the `db` fixture; PostgreSQL via `LOAMZ_TEST_POSTGRES_URL`, skipped when absent).
5. **Configuration** only in `bootstrap/settings.py` (agents) or the data server's settings; nothing reads `os.environ`.
6. **Wiring** only through the container: infrastructure is a `Container` property; a feature's service is built by a
   provider function in its `service.py` and resolved with `container.get(provider)` / `Depends(use(provider))`.
   No `app.state` outside `server/app.py`, no module-level singletons.
7. **Tenancy** comes from `loamz.identity.tenancy` (alias, names, groups, roles, scopes, keys). Never re-derive it.
8. **Tests**: fast tier (no Docker) under 5 minutes for the whole repo; mark Docker/minutes-long tests `slow` or
   `integration`. Keep the behaviour of the ported tests; rewrite their fixtures to the new APIs.
9. **Commits**: conventional commits, author is the repo-local git config (Ravikumarchavva), **no Co-Authored-By or any
   AI attribution lines**. Small commits per ported area.
10. **Parity**: tick items in `docs/rewrite/parity.md` (`[x]` ported with the test that covers it, `[-] reason` deleted).

## The feature template (copy `agents/library/src/loamz/agents/features/people/preferences/`)
```
features/<context>/<feature>/
  model.py    pydantic value types + validation
  store.py    COMPONENT, SCRIPTS, a store class keyed by tenant (and user where private), erase(tenant, user=None)
  service.py  the use cases, taking a `Caller`; raises typed errors; ends with a provider `def <name>(container) -> Service`
  api.py      APIRouter(prefix="/api/v1/..."): parse, call the service, return. Uses CallerDep and Depends(use(provider))
```
Register the router in `features/__init__.py` `FEATURES` with its roles (`gateway`, `runtime`, `tools`, …).
Contexts: conversations, workspace, knowledge, directory, groups, automation, people, governance.
