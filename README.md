# agent-substrate

Agents + substrate: an open-source stack for building, running and governing AI agents and the data they work with.

| Directory | What it is |
| --- | --- |
| [`agents/agent-substrate`](agents/agent-substrate) | The agent library (PyPI: `agent-substrate`): reasoning loop, tools, sandboxes, durable runs. Works with no infrastructure; `agent-substrate[data]` delegates governed data to substrate-data |
| [`agents/substrate-cloud`](agents/substrate-cloud) | The multi-tenant agent server (monolith or microservices) |
| [`agents/substrate-ui`](agents/substrate-ui) | The chat UI |
| [`data/substrate-data`](data/substrate-data) | The governed data platform: catalog, SQL, files, documents and knowledge, memory, policies, audit |
| [`data/substrate-data-client`](data/substrate-data-client) | Python client (PyPI: `substrate-data-client`) |
| [`data/substrate-data-web`](data/substrate-data-web) | The data workspace UI |
| [`data/document-intelligence`](data/document-intelligence), [`embedding-reranker`](data/embedding-reranker), [`inference-pool`](data/inference-pool) | Document extraction and embedding services |
| `identity/`, `contracts/`, `deploy/` | Shared identity libraries and Keycloak realm; versioned API contracts; deployment (compose, Helm, Terraform) |
| [`docs/adr`](docs/adr) | Architecture decision records: start with 0003 to 0009 |

The commercial control plane (accounts, billing, provisioning) is a separate private repository, `agent-substrate-platform`. It deploys released versions of this stack and is never in a customer's data path ([ADR 0006](docs/adr/0006-cells-and-control-plane-split.md)).

## Working here

```bash
make sync     # uv (Python 3.14) and pnpm workspaces, one lockfile each
make check    # lint, import contracts, tests that need no infrastructure
```

Each package documents its own commands (`agents/agent-substrate/CLAUDE.md`, `data/substrate-data/README.md`). Changes follow conventional commits; each package is released on its own version.

**Status:** being reorganised from four repositories into this one (history preserved). `deploy/legacy-agents` is the previous compose setup, kept until the unified `substrate-cell` chart replaces it.
