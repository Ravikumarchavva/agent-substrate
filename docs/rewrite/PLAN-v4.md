# Loamz v4: client SDKs like Azure/Google, open server engines, private workspace

## Context

The owner wants Loamz to work like the big cloud platforms:
- one brand root (`loamz`);
- client libraries in the style of the Azure, Google Cloud, boto3 and Databricks SDKs;
- a set of open-source engines you can self-host (an open "Databricks": the data platform, and an agent runtime);
- a proprietary workspace product: catalog explorer, SQL editor, chat, Genie and an agent builder.

Clients must stay thin: they call APIs and never hold engines. Every language follows the same names (Python first, then JS, then Rust).

This plan replaces the repo and packaging layout of `docs/rewrite/PLAN-v3.md`. The engineering principles of `docs/rewrite/PLAN.md` still apply: one persistence model, typed settings, RFC 9457 errors, tenancy in one place, no shims.

What exists today, on branch `rewrite/loamz` of `Ravikumarchavva/agent-substrate`, checked out in `~/personal/agent-framework/loamz`:
- `data/{storage,client,server,reader,workspace}`
- `agents/{library,console}`
- `identity/{python,oidc-bff}`
- `deploy`, `contracts`

Gates are green, the end-to-end test passes, and the Helm chart is fixed (`f4b2617`). One gap is known: the agent server still keeps uploaded files on its own disk, because the `loamz.agents.data` adapters are not wired into the container.

## Decisions (owner)

1. **Azure/Google SDK style.**
   - `loamz` is a PEP 420 namespace.
   - Each service has its own distribution.
   - A shared core and a shared credential library sit underneath.
   - A `loamz` meta package installs them all, and `loamz-cli` provides the command-line tool.
2. **Server and engine code** lives in the same namespace under `loamz.server.*` and `loamz.engine`. Client distributions never depend on them, and this is enforced.
3. **Clients are thin.** `loamz.agents` is the API for agents: create, deploy, invoke, threads and traces. The durable agent runtime runs server-side. Custom logic reaches agents as tools through MCP servers.
4. **Reserve the names now:**
   - PyPI, npm `loamz`, crates.io `loamz`/`loamz-core`/`loamz-runtime`, and the Docker Hub org `loamz`.
   - On crates.io, publish a minimal real crate, not an empty squat.
   - A future Rust core (`loamz-runtime`) could power the server and the SDKs, the way Temporal's Rust core sits under its language SDKs.

## Naming standard (Azure SDK design guidelines; one rulebook for every language)

- **Packages:** distribution `loamz-<service>` with import `loamz.<service>`. Async lives in `loamz.<service>.aio`. Models are in `loamz.<service>.models`.
- **Clients:** `<Service>Client(endpoint, credential, **kwargs)`, for example `DataClient` or `AgentsClient`. The bundling client is `from loamz.workspace import WorkspaceClient`, with attributes `.data`, `.agents`, `.genie` and so on. The account-level client is `from loamz.account import AccountClient`.
- **Methods:**
  - `list_<things>()` returns a lazy pager (`ItemPaged`, or `AsyncItemPaged` in `.aio`);
  - `get_<thing>`, `create_<thing>`, `update_<thing>`, `delete_<thing>`;
  - long-running work is `begin_<verb>()`, which returns an `LROPoller` with `.result()`;
  - verbs outside CRUD are plain verbs: `execute_statement`, `upload_file`, `invoke_agent`.
- **Arguments:** required arguments are positional; options are keyword-only. There are no `**kwargs` bags apart from the per-call transport options (`timeout`, `retry`, `headers`).
- **Errors:**
  - everything derives from `loamz.core.exceptions.HttpResponseError`;
  - typed subclasses: `ResourceNotFoundError`, `ResourceExistsError`, `ClientAuthenticationError`, `PermissionDeniedError`, `ServiceRequestError`, `ServiceResponseError`;
  - each carries the RFC 9457 `code` the server sends.
- **Credentials:**
  - `from loamz.identity import DefaultLoamzCredential`, with a chain that tries env vars, then the `~/.loamz/config` profile, then a cached CLI login (device code), then workload identity;
  - explicit options: `ClientSecretCredential`, `TokenCredential`, `DeviceCodeCredential`;
  - env vars: `LOAMZ_ENDPOINT`, `LOAMZ_TOKEN`, `LOAMZ_CLIENT_ID`, `LOAMZ_CLIENT_SECRET`, `LOAMZ_PROFILE`.
- **Server resource names** follow Google AIP: `workspaces/{w}/catalogs/{c}/schemas/{s}/tables/{t}` and `workspaces/{w}/agents/{a}`. The REST paths stay `/api/v1/...`.
- **CLI:** `loamz <service> <verb>`, for example `loamz data sql "select 1"`, `loamz agents invoke analyst "…"` and `loamz login`. It is built on the Python SDK.

## Distributions

| Distribution | Import | Contents | Depends on |
|---|---|---|---|
| `loamz-core` | `loamz.core` | Transport pipeline (httpx), retry, redirect and logging policies, paging (`ItemPaged`), `LROPoller`, exceptions, `Configuration`, user-agent | httpx, pydantic |
| `loamz-identity` | `loamz.identity` | The credential chain, config profiles and token cache. Server-side token *verification* moves to `loamz.server.identity`. | loamz-core |
| `loamz-data` | `loamz.data` | `DataClient`: catalog, statements (results as Arrow or Polars with the `[arrow]` extra), volumes and files, documents and search, memory, databases | core, identity |
| `loamz-agents` | `loamz.agents` | `AgentsClient`: agents CRUD, `begin_deploy`, `invoke_agent` (streaming), threads, runs, traces, approvals | core, identity |
| `loamz-genie` | `loamz.genie` | `GenieClient`: spaces, `ask` (streaming), conversations, feedback | core, identity |
| `loamz-workspace` / `loamz-account` | `loamz.workspace` / `loamz.account` | `WorkspaceClient` facade over every service client; `AccountClient` for workspaces, members and billing | the service packages |
| `loamz` (meta) | — | `pip install loamz` installs core, identity, data, agents, genie, workspace and account | the above |
| `loamz-cli` | `loamz.cli` | The `loamz` command | the SDK |
| `loamz-engine` | `loamz.engine` | The embeddable engines: SQL on SQLite/Postgres (`loamz.engine.sql`), files, memory, vectors, documents, and the durable agent runtime (`loamz.engine.agents`) | pydantic (+ extras) |
| `loamz-server` | `loamz.server` | The open data platform (`loamz.server.data`) and agent hosting (`loamz.server.agents`: deploy, invoke, threads, MCP tool calls), plus token verification (`loamz.server.identity`) and the MCP server (`loamz.server.mcp`) | loamz-engine (+ FastAPI, DuckDB, pyiceberg) |
| `loamz-reader` | `loamz.server.reader` | The heavy document parser. Python 3.13, with its own lock. | — |

Enforcement:
- Import contracts: `loamz.{core,identity,data,agents,genie,workspace,account,cli}` never import `loamz.engine` or `loamz.server`.
- `pip install loamz` must pull no server dependencies (no DuckDB, FastAPI or asyncpg). A test checks the installed set and the import time.

## Repos (`loamz` org)

| Repo | Visibility | Holds |
|---|---|---|
| `loamz-server` | public, Apache-2.0 | `loamz-engine`, `loamz-server`, `loamz-reader`. The **API specs** (`specs/<service>/v1/openapi.json`, generated from the server and drift-tested; the single source for every SDK). Deploy (compose and Helm `loamz-server`, Terraform, the Keycloak realm). A minimal admin UI. |
| `loamz-python` | public, Apache-2.0 | Every Python client distribution plus `loamz-cli`, in one repo with one package per folder, like `azure-sdk-for-python`. Generated low-level code (`_generated/`) comes from the specs at a pinned server version, with a hand-written layer on top. |
| `loamz-js` | public (later) | `loamz` and `@loamz/<service>` on npm, generated from the same specs. `@loamz/oidc-bff` also lives here. |
| `loamz-rust` | public (later) | The `loamz` crate (client); later the `loamz-runtime` core |
| `loamz-platform` | private | The workspace UI (Next.js on the JS SDK) and its backend (product features: Genie spaces, agent builder, dashboards, UI-specific APIs), plus the control plane (accounts, billing, provisioning). It uses the released SDKs and `loamz-server` images only. |

## Mapping from today's code

| Today (`~/personal/agent-framework/loamz`) | Becomes |
|---|---|
| `data/storage` | `loamz.engine` (`sql`, `files`, `memory`, `vectors`, `documents`, `errors`) |
| `agents/library` `kernel`, `stores`, `providers`, `toolkit`, `testing` | `loamz.engine.agents` (the durable runtime); providers and toolkit become engine extras or MCP |
| `agents/library` `features`, `server`, `bootstrap` | generic agent hosting goes to `loamz.server.agents`; product features (groups, notifications, UI-only APIs) go to `loamz-platform` backend |
| `data/server` | `loamz.server.data` |
| `identity/python` verifier + tenancy | `loamz.server.identity` (the tenancy rules stay one module, used by both servers) |
| `data/client` | `loamz.data.DataClient`, reshaped to the naming standard, with core and identity split out |
| `data/reader` | `loamz-reader` |
| `agents/console` and `data/workspace` UIs | `loamz-platform` UI |
| `contracts/*` | `loamz-server/specs/*` |

## Phases

0. **Reserve names (owner, today).**
   - PyPI placeholders: `loamz-core`, `loamz-identity`, `loamz-data`, `loamz-agents`, `loamz-genie`, `loamz-workspace`, `loamz-account`, `loamz-cli`, `loamz-engine`, `loamz-server`, `loamz-reader`. (`loamz` already exists.)
   - npm: `loamz`.
   - crates.io: `loamz`, as a minimal real crate.
   - Docker Hub: the `loamz` org.
   - GitHub repos: `loamz-server`, `loamz-python` (public) and `loamz-platform` (private).
1. **Freeze.**
   - Wire the `[data]` file adapters (the known gap) and delete compose's legacy env block.
   - Merge `rewrite/loamz` into `main` of the old repo and tag `pre-v4`.
2. **Split and rename** with `git filter-repo`, keeping history.
   - `loamz-server` gets data, engine, server, reader, deploy, specs and the admin UI.
   - `loamz-python` starts from `data/client` and `identity/python` (client half).
   - `loamz-platform` absorbs both UIs and the product features.
   - The renames follow the mapping table. Import contracts and CI are set up per repo.
3. **`loamz-server` stands alone:**
   - engine and server under the new names;
   - generic agent hosting API (`/api/v1/workspaces/{w}/agents`, deploy and invoke);
   - `loamz.server.mcp`;
   - specs published per release;
   - a data-only e2e test and an agent-hosting e2e test;
   - images `loamz/loamz-server` and `loamz/loamz-reader`.
4. **`loamz-python`:**
   - `loamz-core` and `loamz-identity` first;
   - then `loamz.data`, `loamz.agents`, `loamz.genie`, `loamz.workspace`, `loamz.account`, and `loamz-cli`;
   - a generator script turns the specs into `_generated/`;
   - every client has `.aio`;
   - docs and samples per package;
   - the install-weight test.
5. **Platform backend and UI v1** on the SDKs: catalog explorer, SQL editor, files, chat, account.
6. **Genie.** Spaces, NL→SQL as the asking user, shown SQL/result/chart, feedback, and evals.
7. **Agent builder.** Declare an agent (instructions, model, MCP tools, knowledge, approvals, budgets), test it in a playground, version it, deploy it, schedule it, and trace it.
8. **Later:**
   - `loamz-js`;
   - `loamz-rust`, plus a Rust `loamz-runtime` if the Python engine becomes the bottleneck;
   - Lakebase in the UI;
   - dashboards;
   - the docs site.

## Reuse

These are moved and renamed, not rewritten:
- **Data client:** the HTTP client, auth and Arrow code in `data/client/src/loamz/data/client/{_http.py,auth.py,_arrow.py,errors.py}` are the seed of `loamz-core`, `loamz-identity` and `loamz.data`.
- **Server identity:** `identity/python/src/loamz/identity/{verifier,jwks,tenancy}.py` become `loamz.server.identity`.
- **Engine:** `data/storage/src/loamz/data/storage/*` and the agent kernel under `agents/library/src/loamz/agents/kernel/*`.
- **Deploy:** `deploy/*` and `deploy/compose/{e2e,demo-user}.py`.
- **Contracts:** `contracts/{agents,data}/v1/openapi.json` and their drift tests.

## Verification

- **Each repo:** ruff, import contracts, a fast tier under 5 minutes, and contract drift tests.
- **`loamz-python`:** `pip install loamz` in a clean venv installs no server dependencies and imports `loamz.workspace` in under 100 ms. Every client passes a recorded-HTTP test suite and a live suite against a `loamz-server` started in CI. The naming-standard lint covers method prefixes, `.aio` parity and error types.
- **`loamz-server`:** a clean install of the server pulls no client-only code. The data e2e and the agent-hosting e2e (deploy, then invoke with an MCP tool) pass.
- **End to end:** the e2e script is rewritten on the SDK (`WorkspaceClient`) instead of raw HTTP. That proves the SDK and the server agree.
