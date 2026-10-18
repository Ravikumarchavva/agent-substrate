# Loamz

A governed data platform and durable agents built on it.

| Directory | Distribution | What it is |
| --- | --- | --- |
| [`data/storage`](data/storage) | `loamz-data-storage` | Embeddable storage engine: SQL `Database` (SQLite & Postgres), vectors, memory, files, documents |
| [`identity/python`](identity/python) | `loamz-identity` | OIDC verifier, JWKS, FastAPI helpers, and multi-tenant isolation rules |
| [`data/client`](data/client) | `loamz-data-client` | Typed HTTP client for Loamz Data platform API |
| [`data/server`](data/server) | `loamz-data-server` | Governed data platform: catalog, SQL, tables, compute, files, documents, memory, databases, audit |
| [`data/reader`](data/reader) | `loamz-data-reader` | High-throughput document reader service (PaddleOCR & layout analysis) |
| [`data/workspace`](data/workspace) | `data-workspace` | Next.js data workspace application |
| [`agents/library`](agents/library) | `loamz-agents` | Agent reasoning loop, tool execution, sandboxes, durable runs, and agent server (`[server]`) |
| [`agents/console`](agents/console) | `agents-console` | Next.js agent chat and operations console |
| [`identity/oidc-bff`](identity/oidc-bff) | `@loamz/oidc-bff` | TypeScript browser BFF session library (Keycloak authorization code + PKCE) |
| [`meta/loamz`](meta/loamz) | `loamz` | Convenience metapackage (`pip install "loamz[agents,data]"`) |

## Quick Start

```bash
make sync     # uv (Python 3.14) and pnpm workspaces
make check    # lint, import contracts, fast unit test suite
```
