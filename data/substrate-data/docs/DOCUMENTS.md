# Documents, memory, erasure and export

Governed documents, knowledge bases and long-term memory, run by the agent library's `Library` and `Memory` engines
behind this service's authorization, tenancy and audit ([ADR 0004](../../../docs/adr/0004-agent-runtime-versus-governed-data.md),
[ADR 0008](../../../docs/adr/0008-retrieval-index-on-postgres-pgvector.md)). The agent server reaches it through
`substrate-data-client` (`client.documents`, `client.memory`, `client.erasure`, `client.exports`) and, for the agent runtime,
the `agent-substrate[data]` adapters `DataLibrary` and `DataMemoryStore`.

## Model

| Thing | Where | Notes |
| --- | --- | --- |
| OKF bundle (markdown, figures) | the tenant's `workspace.files` volume, under the collection's name | **Source of truth.** Ordinary versioned, quota-counted files: `users/{sub}/…` stays that subject's. Written by the control plane itself (it never signs a URL for them). |
| Catalog, full-text index, chunk vectors | database `data_retrieval` (PostgreSQL + pgvector, schema `retrieval`) | Derived. `POST …/documents:reindex` rebuilds it from the bundle. HNSW for similarity, native full-text beside it. SQLite file for development. |
| Collection registry | same database, `data_collections` | `id` = first 24 hex digits of SHA-256 of the name (a published contract: `substrate_data_client.documents.collection_id`). |
| Memory records | same database (`memory_records`) | The `Memory` engine: visibility is enforced in SQL by namespace. |

A **collection** is named by the agent library's key prefix: `tenants/{tenant}/users/{sub}/conversations/{c}/documents` (a
conversation's uploads) or `tenants/{tenant}/knowledge/{kb}/library` (a knowledge base). The tenant in the name must be the token's
verified tenant (any other tenant's collection is `404`), and `users/{sub}/…` belongs to that subject for delegated tokens, as for files
(`403 path-forbidden`). With the bundle on a volume, the tenant volume's `READ_VOLUME` / `WRITE_VOLUME` grants apply too.

## Parsing is a worker role

The control plane never parses an upload. `POST …/documents` takes either `content_base64` (read by the document server at
`SUBSTRATE_DATA_DOCUMENT_READER_URL`, i.e. `data/document-intelligence`; never with a local fallback) or an `extraction` the caller
already has. With neither a reader URL nor `SUBSTRATE_DATA_DOCUMENTS_PARSE_INLINE=true` (development, tests) a raw upload is
`503 reader-unavailable`; an unreadable document is `422 unreadable-document`. Embedding and reranking likewise happen here, against
`SUBSTRATE_DATA_DOCUMENT_EMBEDDING_URL` (`data/embedding-reranker`), for collections ensured with `semantic: true`. A service that is down
degrades search to words (`note` in the answer) and stores chunks without vectors for `documents:embed-missing`; it never fails an upload.

## Routes (`/api/v1`)

Reads need `documents:read` (or `documents:write`), mutations `documents:write`, for delegated tokens. Errors are RFC 9457.

| Route | Purpose |
| --- | --- |
| `POST /collections:ensure` | `{name, semantic?}`. Idempotent (`201` the first time). Returns `{id, name, semantic, created}`. |
| `GET /collections?under=` | Collections at or below a prefix (default the tenant) that the caller may see, with document counts. |
| `DELETE /collections/{id}` | Removes the collection: catalog, vectors and bundle. |
| `POST /collections/{id}/documents` | `{filename, content_base64 \| extraction, content_type?, resource?, metadata?, sha256?}`. `201`, or `200 duplicate`. |
| `GET /collections/{id}/documents[?limit&after]` | Listing with cursor. `GET …/documents/{doc}` is its `DocumentInfo`. |
| `GET …/documents/{doc}/outline[?section]`, `…/read[?section&first_page&last_page&offset]`, `…/sections/{n}` | Navigate: outline, a page range, a section (long ones continue from `next_offset`). |
| `GET /collections/{id}/images?image\|document&page` | A figure or page image as bytes (`x-picture-*` headers carry the facts). |
| `DELETE …/documents/{doc}` | Remove a document. |
| `POST …/documents:reindex` `{missing_only}` / `…:embed-missing` | Rebuild the derived index from the bundle / embed chunks stored without a vector. |
| `POST /collections/{id}:search` | `{query, document?, limit}` -> `{hits, note}`. Words and meaning, fused and reranked server-side. |
| `GET …/topics[?topic]`, `GET …/topic-tree`, `PUT …/topics` | Browse and set the filing tree. |
| `POST …/enrichment:pending`, `POST /enrichment:pending`, `GET …/documents/{doc}/brief`, `…/enrichment`, `POST …/enrichment:claim` / `:fail` / `:annotate`, `GET …/needs-reorganising` | Enrichment bookkeeping. The model call and its token budget stay with the caller (ADR 0004); it reads a `brief`, writes `annotate`. |

## Memory

`POST /memory/records:save` (the record, with its `id`), `:get`, `:delete` (`{namespace, id}`), `:query`, `:touch`, `:erase`. Ids travel in
bodies, never paths (they are caller-supplied text). The **tenant is the token's**. A record that names a user belongs to that user (a token
may only name its own subject); a record with no user is a tenant-level fact that only a tenant administrator (`tenant-admins` group) may write,
delete or erase, and `tenant_wide_reason` queries are administrators' too. Scopes `memory:read` / `memory:write`.

## Erasure and export

Both declare no scope, so a delegated (agent) token can never call them. The subject themself, a `tenant-admins` member of that tenant, or a
platform administrator may.

`POST /api/v1/erasure-requests {scope: user|tenant, subject?, tenant_id?}` removes, in this order: vectors, library catalog and bundles,
memory, then files **with every version** (hard delete, then the objects nothing references any more) and unfinished uploads and upload
sessions, then reclaims the database files. It returns a receipt:

```json
{"id": "…", "status": "complete", "scope": "user", "tenant_id": "acme", "subject_hash": "9f2c…", "completed_at": 1760000000.0,
 "removed": {"vectors": 12, "libraries": {"collections": 2, "documents": 5}, "memory": 3,
             "files": {"files": 9, "versions": 14, "objects": 14}, "upload_sessions": 0},
 "retained": {"audit_log": "ids and digests only; the hash chain is kept"}}
```

Erasing again removes nothing and says so. The audit log is evidence, not data: it holds digests and counts, never names or content, and its
hash chain verifies afterwards (`erasure.complete` is itself an event).

`POST /api/v1/exports` (same body) returns one JSON manifest, `substrate-export/v1`: the files (path, size, SHA-256, version and a presigned
URL valid for an hour), the collections with their documents' metadata, and the memory records. `truncated` is set past 10 000 items.

## Audit

Every mutation is a hash-chained event carrying ids and counts only: the collection by its opaque id, a document by a 16-digit digest of its id
(its id includes its file name), a memory record by a digest. Never a name, a query or content.

## Settings

| Variable | Default | |
| --- | --- | --- |
| `SUBSTRATE_DATA_RETRIEVAL_URL` | (empty) | PostgreSQL (with pgvector) for the index, normally database `data_retrieval`. Empty uses SQLite under `RETRIEVAL_DIR`. |
| `SUBSTRATE_DATA_RETRIEVAL_SCHEMA` | `retrieval` | Schema inside that database. |
| `SUBSTRATE_DATA_RETRIEVAL_DIR` | `.workspace/retrieval` | Scratch and the SQLite file; must be writable. |
| `SUBSTRATE_DATA_DOCUMENT_READER_URL` / `…_TOKEN` | (empty) | The document server that reads uploads. |
| `SUBSTRATE_DATA_DOCUMENTS_PARSE_INLINE` | `false` | Read with the built-in reader in this process (development only). |
| `SUBSTRATE_DATA_DOCUMENT_EMBEDDING_URL` / `…_TOKEN` / `…_TIMEOUT_SECONDS` | (empty), 30 | The embedding and reranking service. |
| `SUBSTRATE_DATA_DOCUMENTS_MAX_UPLOAD_BYTES` | 100 MiB | Largest raw upload in a request. |

## Client

`client.documents` (`ensure`, `add`, `list`, `get`, `outline`, `read`, `section`, `image`, `search`, `delete`, `reindex`, `topics`, enrichment
calls), `client.memory`, `client.erasure.request(subject)` and `client.exports.create(subject)` — synchronous, and awaited on
`AsyncDataClient`. Results are the service's JSON; `substrate.integrations.data.DataLibrary` / `DataMemoryStore` turn them into the library's
types and pass the same conformance suites as the folder implementations.

## Not built

- **Large raw uploads.** `content_base64` rides in the request body (up to `DOCUMENTS_MAX_UPLOAD_BYTES`); a document bigger than that, or one
  already in a volume, cannot be added by reference yet (`{volume, path}` or an upload session would remove the base64 cost).
- **A process-wide enrichment sweep.** `enrichment:pending` across collections is confined to one tenant per token, so the platform's startup
  sweep over every tenant is not available through the adapter; enrichment after each upload is.
- **Upload sessions in a tenant erasure.** Sessions are keyed by principal, not tenant: a tenant erasure removes the uploads aimed at its volume,
  and a user erasure those the subject created; other members' unfinished sessions expire on their 24 hour TTL.
- **Reader as a separate worker process for in-process parsing.** Parsing is only ever delegated (document server) or skipped (an extraction
  supplied); there is no built-in `rag`-role worker that parses on the control plane's behalf.
