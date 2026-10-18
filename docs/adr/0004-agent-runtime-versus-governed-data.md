# 0004 — The agent runtime keeps contracts and local implementations; governed data lives in substrate-data

- **Status:** accepted, 2026-10-18.

## Context

`agent-substrate` ships its own object store, document reader, knowledge-base vectors, graph and long-term memory, ungoverned. `substrate-data` has a second, governed store with grants, policies, audit, retention and credential vending. Two object stores means two security models. But the agent runtime also has **operational state** (run journal, scheduler, signals, threads, tasks) that must commit in one transaction with the scheduler (`agent-substrate` decision "Coordination state is all-Postgres").

## Decision

1. **Contracts and local, folder-based implementations stay in `agent-substrate`**, with their conformance suites, so the library installs and runs with no infrastructure.
2. **Governed user content moves to `substrate-data`:** file bytes and versions, uploads, documents and knowledge bases (catalog, full-text, vectors, rerank), long-term memory, erasure and export. The optional extra `agent-substrate[data]` provides adapters (`DataFileStore`, `DataLibrary`, `DataMemoryStore`) that implement the same ports through `substrate-data-client`.
3. **Operational state stays in the runtime's own database** (`agent-substrate[postgres]`, `postgres_runtime_store`). It is not user content, and moving it behind a network API would remove the transaction the scheduler relies on.
4. **substrate-data runs the existing engines** (`Library`, `Memory`, `Reader`) behind its authorization, audit and tenancy rather than re-implementing them. The dialect-neutral code stays in `agent-substrate`; substrate-data depends on `agent-substrate` core (pydantic, opentelemetry-api, pypdfium2).
5. **Heavy services move** to the data side as worker roles: document-intelligence, embedding-reranker, inference-pool.
6. LLM **enrichment and its token budget stay in the agent server** (model keys and LLM spend are an agent concern); it writes results back through the port.
7. The generic vector and graph ports keep their contracts and folder implementations; no substrate-data graph domain is built until something consumes it.

## Enforced by tests

- Every adapter passes the **same conformance suite** as the local implementation.
- With `DATA_SERVICE_URL` set, the agent database contains no `vector_docs`, `memory_records`, `library_*` or `files` tables.
- In production the agent server holds no S3 credentials (ADR 0005).

## Consequences

An extra network hop on document, file and memory calls (mitigated with HTTP/2 pooling, batch URL signing, a short memory cache, and fail-soft behaviour). The agent platform keeps working with substrate-data down, minus those features.
