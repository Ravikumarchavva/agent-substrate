# 0008 — The retrieval index is PostgreSQL with pgvector

- **Status:** accepted, 2026-10-18.

## Decision

Documents and knowledge bases are indexed in PostgreSQL (database `data_retrieval`) with pgvector HNSW for similarity and the native full-text index beside it, using the same dialect code the `Library` engine already runs and tests on SQLite and PostgreSQL. The **OKF bundle in the tenant's volume is the source of truth**; the index is derived and rebuilt with `reindex`.

Rejected: LanceDB on object storage (deletes are tombstones until compaction, which makes erasure hard to prove, and concurrent writers are hard); DuckDB VSS (experimental persistence, single writer); Iceberg (not built for nearest-neighbour search).

## Consequences

Point deletes plus vacuum give provable erasure; filtered nearest-neighbour queries are fast; scale-out means standard PostgreSQL scaling.
