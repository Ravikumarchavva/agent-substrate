# 6 · Storage & Pipeline

## Storage implementations

Three concrete store types, each implementing a kernel Protocol:

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart LR
    classDef proto fill:#E8EAF6,stroke:#3949AB,color:#1A237E,font-weight:bold
    classDef impl  fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef infra fill:#FFF3E0,stroke:#E65100,color:#BF360C,stroke-dasharray:4 2

    VS["VectorStore\nkernel Protocol\nadd / search / delete"]:::proto
    GS["GraphStore\nkernel Protocol\nadd_entities / add_relationships\nquery_cypher / get_subgraph"]:::proto
    BS["BlobStore\nkernel Protocol\nput / get / delete / list"]:::proto

    PGV["PgVectorStore\nvector/pgvector_store.py\nPostgreSQL + pgvector extension\nraw SQL, no ORM"]:::impl
    AGE["AGEGraphStore\ngraph/age_store.py\nPostgreSQL + Apache AGE\nopenCypher via cypher() function"]:::impl
    S3["S3FileStore\nstorage/s3.py\nwraps MinIOConnector\naiobotocore-based"]:::impl

    PG["PostgreSQL\nagentdb"]:::infra
    MINIO["MinIO / AWS S3"]:::infra

    VS --> PGV --> PG
    GS --> AGE --> PG
    BS --> S3 --> MINIO
```

### `PgVectorStore`

Raw SQL implementation (no SQLAlchemy ORM model — pure `engine.begin()` / `engine.connect()`). Requires the `pgvector` PostgreSQL extension.

```python
from ravi.capabilities.vector import PgVectorStore

store = PgVectorStore(
    session_factory=session_factory,
    engine=engine,
    dimensions=384,   # must match the embedding model
)
await store.ensure_table()   # CREATE TABLE IF NOT EXISTS + indexes

ids = await store.add(documents, collection="kb")
results = await store.search(query_vec, collection="kb", limit=5)
await store.delete(ids, collection="kb")
```

**Schema** (created by `ensure_table()`):

```sql
CREATE TABLE IF NOT EXISTS vector_store_{collection} (
    id          TEXT PRIMARY KEY,
    text        TEXT NOT NULL,
    content_json JSONB NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',
    embedding   VECTOR({dimensions}),
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON vector_store_{collection} USING hnsw (embedding vector_cosine_ops);
```

Each collection is a separate table — no cross-collection queries.

### `AGEGraphStore`

Uses raw asyncpg connections to execute openCypher via the Apache AGE `cypher()` function. The graph is created automatically on first connect.

```python
from ravi.capabilities.graph import AGEGraphStore
from ravi.kernel.storage.graph import Entity, Relationship

store = AGEGraphStore(
    dsn="postgresql://postgres:postgres@localhost/agentdb",
    graph_name="knowledge",
)
await store.connect()

await store.add_entities([
    Entity(label="Person", properties={"name": "Alice", "role": "Engineer"}),
])
await store.add_relationships([
    Relationship(source_label="Person", source_key="Alice",
                 target_label="Company", target_key="Acme",
                 rel_type="WORKS_AT"),
])

subgraph = await store.get_subgraph(entities=["Alice"], depth=2)
results = await store.query_cypher("MATCH (n:Person) RETURN n")
```

### `S3FileStore`

Delegates to `MinIOConnector` (from `infrastructure/storage/minio.py`). Compatible with MinIO in docker-compose and AWS S3 in production.

```python
from ravi.capabilities.storage import S3FileStore

store = S3FileStore(
    endpoint_url="http://localhost:9000",
    access_key="minioadmin",
    secret_key="minioadmin",
    bucket="agent-files",
)
await store.connect()   # also creates the bucket if missing

await store.put("agent-files", "reports/2024-01.pdf", pdf_bytes)
data: bytes = await store.get("agent-files", "reports/2024-01.pdf")
await store.delete("agent-files", "reports/2024-01.pdf")
```

## PipelineEngine

`PipelineEngine` (`capabilities/pipeline/engine.py`) executes declarative pipelines — named sequences of tool calls with `$prev` variable passing between steps.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart TD
    classDef data fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C
    classDef proc fill:#E8EAF6,stroke:#3949AB,color:#1A237E
    classDef out  fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20

    DEF["PipelineDef\nname, description\nsteps: list[PipelineStep]"]:::data
    ENG["PipelineEngine.execute(pipeline)\nfor each step:\n  resolve $-refs in input_mapping\n  tool = registry.get(adapter_name)\n  result = await tool.execute(**inputs)\n  context[output_key] = result\n  context['prev'] = result"]:::proc
    RES["PipelineResult\nsuccess, step_results\nduration_ms, error"]:::out

    DEF --> ENG --> RES
```

### `PipelineStep` fields

| Field | Type | Description |
|---|---|---|
| `adapter_name` | `str` | Tool name in the Toolbox |
| `action` | `str` | Method to call (default: `"execute"`) |
| `input_mapping` | `dict` | Literal values or `$prev.content` / `$step_0.content` refs |
| `output_key` | `str` | Key for result in context dict (default: `step_{i}`) |
| `timeout` | `int` | Per-step timeout in seconds (default: 60) |

### `$`-reference resolution

```python
# Step 0 outputs to context["report"]
PipelineStep(adapter_name="postgres_query",
             input_mapping={"sql": "SELECT * FROM events"},
             output_key="report")

# Step 1 reads previous step's text via $prev.content
PipelineStep(adapter_name="email_sender",
             input_mapping={"to": "user@example.com",
                            "body": "$prev.content"})
```

`_resolve_inputs()` walks the `$`-path through the context dict. Literal values pass through unchanged.

## DataRefStore

`DataRefStore` (`capabilities/pipeline/data_ref.py`) is a **hybrid Redis/S3 pointer store** for large data exchange between pipeline steps — prevents large payloads from bloating the LLM context window.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart LR
    classDef proc fill:#E8EAF6,stroke:#3949AB,color:#1A237E
    classDef store fill:#FFF3E0,stroke:#E65100,color:#BF360C,stroke-dasharray:4 2
    classDef obj fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C

    IN["data: bytes | str | dict"]:::proc
    DRS["DataRefStore.store()\nsize check"]:::proc
    RD["Redis\ndata < 1 MB\nkey: dataref:{ref_id}\nTTL enforced"]:::store
    S3["S3 / MinIO\ndata ≥ 1 MB\nkey: datarefs/{ref_id}"]:::store
    REF["DataRef\nref_id, storage, key\nsize_bytes, ttl, pinned"]:::obj
    RES["DataRefStore.resolve(ref)\n→ bytes"]:::proc

    IN --> DRS
    DRS -->|"< 1 MB"| RD --> REF
    DRS -->|"≥ 1 MB"| S3 --> REF
    REF --> RES
```

| Method | Effect |
|---|---|
| `store(data)` | Routes to Redis or S3 based on size; returns `DataRef` |
| `resolve(ref)` | Fetches from the right backend |
| `pin(ref)` | Removes TTL (prevents expiry for long jobs) |
| `unpin(ref)` | Re-enables TTL |
| `delete(ref)` | Manual cleanup |
| `cleanup_expired()` | Sweeps stale S3 refs (Redis self-expires) |

`DataRefArtifactStore` wraps `DataRefStore` to satisfy the kernel `ArtifactStore` protocol used by `ToolInvoker` and `ToolChainTool` — ref IDs are plain strings at that boundary.
