# Loamz

A governed data platform and durable agents built on it.

| Directory | Distribution | What it is |
| --- | --- | --- |
| [`server/engine`](server/engine) | `loamz-engine` | Embeddable storage engine: SQL `Database` (SQLite & Postgres), vectors, memory, files, documents |
| [`server/server/src/loamz/server/identity`](server/server/src/loamz/server/identity) | `loamz-identity` | OIDC verifier, JWKS, FastAPI helpers, and multi-tenant isolation rules |
| [`sdk/python/data`](sdk/python/data) | `loamz-data` | Typed HTTP client for Loamz Data platform API |
| [`server/server`](server/server) | `loamz-server` | Governed data platform: catalog, SQL, tables, compute, files, documents, memory, databases, audit |
| [`server/reader`](server/reader) | `loamz-reader` | High-throughput document reader service (PaddleOCR & layout analysis) |
| [`apps/data-workspace`](apps/data-workspace) | `data-workspace` | Next.js data workspace application |
| [`server/engine`](server/engine) | `loamz-agents` | Agent reasoning loop, tool execution, sandboxes, durable runs, and agent server (`[server]`) |
| [`apps/agents-console`](apps/agents-console) | `agents-console` | Next.js agent chat and operations console |
| [`sdk/js/oidc-bff`](sdk/js/oidc-bff) | `@loamz/oidc-bff` | TypeScript browser BFF session library (Keycloak authorization code + PKCE) |
| [`meta/loamz`](meta/loamz) | `loamz` | Convenience metapackage (`pip install "loamz[agents,data]"`) |

## Quick Start

```bash
make sync     # uv (Python 3.14) and pnpm workspaces
make check    # lint, import contracts, fast unit test suite
```
