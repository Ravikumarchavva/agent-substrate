# Loamz v3: open engines, one SDK, a proprietary workspace

Supersedes the repo layout of `PLAN.md` (one monorepo). Everything else in `PLAN.md` (principles, one persistence
model, typed settings, RFC 9457, tenancy in one place) still holds.

## Decisions (owner, 2026-10-22)

1. **Databricks model.** The engines and the SDK are open source; the workspace people use is the proprietary product.
2. **Three code repos + SDK repos** in the `loamz` GitHub org, history kept with `git filter-repo`.
3. **The existing UIs and the agent-server features merge into the Platform.** Loamz Data keeps only a minimal admin UI for self-hosters.
4. **Order:** split the repos and make Loamz Data solid first; then the Platform UI (catalog explorer + SQL editor), then Genie, then the agent builder.
5. **Loamz Agents is a light library.** Hard things are delegated to MCP servers or to Loamz Data.
6. **A Loamz SDK** like `databricks-sdk` / `google-cloud-*`: one typed client for every Loamz API.

## Products and repos

| Repo (visibility) | Distributions | What it is | Databricks analogue |
|---|---|---|---|
| `loamz/loamz-data` (public, Apache-2.0) | `loamz-sql`, `loamz-data-storage`, `loamz-data-server`, `loamz-data-reader`, `loamz-data-mcp`, `loamz-identity`, `@loamz/oidc-bff` | The data platform: catalog and grants, SQL warehouse (DuckDB over Iceberg), tables, volumes/files, documents and vector search, memory, managed Postgres (Lakebase), audit, erasure. An MCP server exposes it to any agent. The deployable unit is the **data workspace** (compose, Helm `loamz-data`, Terraform). It also ships a minimal admin UI. | Unity Catalog OSS + Delta + SQL warehouse + Lakebase |
| `loamz/loamz-agents` (public, Apache-2.0) | `loamz-agents` | A light agent library: kernel, durable runtime (SQLite by default), tools API, MCP client, model providers as extras. Uses Loamz Data through the SDK (`[data]`) or MCP. | the Mosaic AI Agent Framework SDK |
| `loamz/loamz-sdk-python` (public) | `loamz-sdk` (`loamz.sdk`) | One client for every Loamz API: `WorkspaceClient` (catalog, sql, files, documents, memory, databases, genie, agents, chats, jobs) and `AccountClient` (projects, workspaces, members, billing). Generated from the OpenAPI contracts, with hand-written conveniences on top (Arrow results, uploads, pagination, retries, auth). | `databricks-sdk` |
| `loamz/loamz-sdk-js` (public, later) | `@loamz/sdk` | The same, for TypeScript; the Platform UI uses it. | `@databricks/sdk` |
| `loamz/loamz-platform` (private) | the product | The workspace UI: catalog explorer, SQL editor, results and visualisations, dashboards, files, chat, **Genie**, **agent builder**. Its backend holds the former agent-server features (conversations, directory, groups, automation, people, governance) on `loamz-agents` + `loamz-sdk`. It also contains the control plane: accounts, billing, provisioning, fleet. | the Databricks workspace + account console |

`loamz-data-client` is **replaced by `loamz-sdk`**; there is one client, not two. The `loamz` PyPI meta package becomes `pip install loamz` = `loamz-sdk` (+ extras `[agents]`, `[data]` for the self-host server).

## Dependency rules (across repos: only released versions, never source paths)

```
loamz-sql ◄── loamz-data-storage ◄── loamz-data-server ──► loamz-identity
    ▲                                       ▲   (exposes OpenAPI contracts → generates loamz-sdk)
    │                                loamz-data-mcp ──► loamz-sdk
loamz-agents ──[data]──► loamz-sdk ──► (HTTP only; depends on httpx + pydantic)
loamz-platform ──► loamz-agents, loamz-sdk, @loamz/sdk, @loamz/oidc-bff
```
- Loamz Data never imports Loamz Agents; Loamz Agents core imports nothing but `loamz-sql`.
- `loamz-agents` core install target: **≤ 8 packages, < 50 ms import** (enforced by a test). Today it is 20 packages and
  31 ms; `loamz-identity` (cryptography, httpx) leaves the core; documents/memory/vector engines are not core.
- `loamz-sql` is the ~800-line SQL layer (Database, SQLite, Postgres, migrate, `under`, errors) carved out of
  `loamz-data-storage`; both products build on it.

## What moves where (from today's `rewrite/loamz` branch of `Ravikumarchavva/agent-substrate`)

| Today | Goes to |
|---|---|
| `data/storage` (SQL layer) | `loamz-data` → new `loamz-sql` |
| `data/storage` (files, memory, vectors, documents) | `loamz-data` → `loamz-data-storage` |
| `data/server`, `data/reader`, `identity/*`, `contracts/data`, `deploy/*` (data parts, realm) | `loamz-data` |
| `data/client` | `loamz-sdk-python` as `loamz.sdk` (data namespaces), extended with platform APIs |
| `data/workspace` (data UI) | merged into the Platform UI; a minimal admin UI stays in `loamz-data` |
| `agents/library` kernel, stores (runtime, threads, tasks), providers, toolkit, data adapters, console, cli, evals, testing | `loamz-agents` |
| `agents/library` `features/`, `server/`, server parts of `bootstrap/`, `contracts/agents` | `loamz-platform` backend |
| `agents/console` (chat UI) | merged into the Platform UI |
| `../loamz-platform` (control plane, Next.js) | `loamz-platform` (it becomes the workspace + account console) |

## Phases

**0. Freeze (½ day).**
- Merge `rewrite/loamz` into `main` of the current repo, tag `pre-split`, and record the gates.
- Fix the known gaps first:
  - wire the `[data]` adapters into the container (today uploads land on the agent's own disk);
  - delete the "legacy env" block in compose.

**1. Split (1–2 days).**
- `git filter-repo` into `loamz-data`, `loamz-agents` and `loamz-sdk-python`.
- Move the platform repo into the org.
- In each repo:
  - its own CI;
  - its own release-please;
  - its own CLAUDE.md;
  - its own LICENSE (Apache-2.0 for the public ones);
  - CODEOWNERS.
- Cross-repo dependencies become version pins. A tag in one repo triggers the dependents' CI through `repository_dispatch`.

**2. Loamz Data stands alone (1 week).**
- Carve out `loamz-sql`.
- Rename the deploy unit to the data workspace: compose and Helm `loamz-data`, with no agent services.
- The minimal admin UI.
- `loamz-data-mcp`: tools for catalog browse, SQL (read-only by default, governed as the caller), files, document search and memory. It runs over stdio and streamable HTTP, and authenticates with the caller's token.
- A data-only e2e test.
- Publish `loamz-sql`, `loamz-data-storage`, `loamz-data-server` and `loamz-identity`.

**3. Loamz SDK (1 week).**
- Generate models and low-level services from `contracts/{data,platform}/v1/openapi.json`, with a drift check in CI.
- Hand-write the ergonomic layer:
  - `WorkspaceClient(host, token|client_credentials|device_flow)`;
  - `w.catalog.*`, `w.sql.execute(...)` returning Arrow or Polars, `w.files.upload/download`, `w.documents.search`, `w.memory.*`, `w.databases.*`;
  - later `w.genie.*`, `w.agents.*`, `w.chats.*`;
  - `AccountClient`.
- Unified config: env vars or `~/.loamz/config` profiles, the way the Databricks CLI does it.
- Retries and pagination; typed errors keyed by problem `code`.
- Port the tests from `data/client`.

**4. Light Loamz Agents (1 week).**
- Remove features, server and identity from the core.
- Turn heavy tools into extras or MCP: the code sandbox, the web browser.
- First-class MCP client: stdio, HTTP and auth.
- The `[data]` adapters go through `loamz-sdk`.
- Enforce the dependency and import-time budget with a test.
- Quickstart: 10 lines from `pip install loamz-agents[openai]` to a durable agent.

**5. Platform backend (1–2 weeks).**
- Move the feature slices and the server into `loamz-platform/backend`. It stays a Python service on the same principles: container, typed settings, RFC 9457.
- It uses `loamz-agents` and `loamz-sdk` as pinned dependencies.
- It is deployed beside each data workspace, because it holds customer content (chats, agents). The control plane stays central.

**6. Platform UI v1 (2–3 weeks).** One Next.js app on `@loamz/sdk` and `@loamz/oidc-bff`:
- workspace switcher;
- catalog explorer (catalogs, schemas, tables, volumes, grants);
- SQL editor with results grid, charts, history and saved queries;
- files;
- chat (the former agents console);
- account and projects (the former control-plane pages).

**7. Genie (2 weeks).**
- **Genie spaces:** a curated set of tables plus instructions, sample and verified questions, and trusted SQL.
- **The Genie agent** (`loamz-agents`) uses the catalog metadata and sample values, writes SQL, and runs it through Loamz Data *as the asking user*, under the caller's grants.
- It shows the SQL, a result table and a chart, and asks for clarification when the question is ambiguous.
- Feedback (thumbs and edits) becomes verified examples.
- Evals run on a per-space benchmark set.

**8. Agent builder (2 weeks).**
- Define an agent:
  - instructions;
  - model;
  - tools from MCP servers, including `loamz-data-mcp`;
  - knowledge bases (Loamz Data documents);
  - approvals and budgets.
- Then test it in a playground chat, version it, deploy it as an endpoint (API key and service principal in the workspace), schedule it, and view traces and evals.

**9. Afterwards:** dashboards (AI/BI), Lakebase in the UI, jobs/notebooks if wanted, the JS SDK published, docs site, demo videos.

## Gates (every phase)

- Each repo: ruff, import contracts, a fast tier under 5 minutes, conformance on SQLite and Postgres, and contract drift tests.
- `loamz-agents`: the dependency and import budget test.
- `loamz-data`: a clean `pip install loamz-data-server` pulls no `loamz-agents`. The data-only e2e passes.
- Platform: the cell e2e passes: sign in, create a workspace, query a table in the SQL editor, ask Genie, build and run an agent, and confirm cross-tenant isolation.

## Owner actions

- Create the repos `loamz-data`, `loamz-agents`, `loamz-sdk-python` (public) and `loamz-platform` (private) in the `loamz` org.
- Reserve these PyPI names: `loamz-sdk`, `loamz-sql`, `loamz-data-mcp`, `loamz-agents`, `loamz-data-storage`, `loamz-data-server`, `loamz-data-reader`, `loamz-identity`.
- Reserve these npm names: `@loamz/sdk`, `@loamz/oidc-bff`.
- Pick the license (Apache-2.0 recommended for the open repos).
