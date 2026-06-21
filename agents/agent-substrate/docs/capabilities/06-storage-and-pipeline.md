# 6 · Storage & Pipeline

## Storage implementations

Three concrete store types, each implementing a kernel Protocol:

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart TB
    classDef proto fill:#E8EAF6,stroke:#3949AB,color:#1A237E,font-weight:bold
    classDef impl  fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef infra fill:#FFF3E0,stroke:#E65100,color:#BF360C,stroke-dasharray:4 2

    subgraph VEC["Vector storage"]
        direction TB
        VS["VectorStore — kernel Protocol (vector.py)<br/>add(docs, collection) · delete(ids)<br/>search(vec, limit, filter) → list[SearchResult]"]:::proto
        PGV["PgVectorStore · vector/pgvector_store.py<br/>raw SQL (no ORM) · dimensions must match model<br/>ensure_table() → vector_store_{collection}<br/>HNSW index (embedding vector_cosine_ops)"]:::impl
        PG1["PostgreSQL + pgvector extension"]:::infra
        VS --> PGV --> PG1
    end

    subgraph GR["Graph storage"]
        direction TB
        GS["GraphStore — kernel Protocol (graph.py)<br/>add_entities · add_relationships<br/>get_subgraph(entities, depth) · query_cypher"]:::proto
        AGE["AGEGraphStore · graph/age_store.py<br/>asyncpg raw SQL · Apache AGE cypher()<br/>graph auto-created on connect"]:::impl
        PG2["PostgreSQL + Apache AGE (same instance)"]:::infra
        GS --> AGE --> PG2
    end

    subgraph BL["Blob storage"]
        direction TB
        BS["BlobStore — kernel Protocol (blob.py)<br/>put · get · delete · list"]:::proto
        S3["S3FileStore · storage/s3.py<br/>wraps MinIOConnector (aiobotocore)<br/>connect() creates bucket if missing"]:::impl
        MINIO["MinIO (dev) / AWS S3 (prod)"]:::infra
        BS --> S3 --> MINIO
    end

    PG1 ~~~ GS
    PG2 ~~~ BS
```

### `PgVectorStore`

Raw SQL implementation (no SQLAlchemy ORM model — pure `engine.begin()` / `engine.connect()`). Requires the `pgvector` PostgreSQL extension.

```python
from agent_substrate.capabilities.vector import PgVectorStore

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
from agent_substrate.capabilities.graph import AGEGraphStore
from agent_substrate.kernel.storage.graph import Entity, Relationship

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
from agent_substrate.capabilities.storage import S3FileStore

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
    classDef dec  fill:#FFF3E0,stroke:#E65100,color:#BF360C,font-weight:bold

    DEF["PipelineDef<br/>──────────────────────<br/>name: str<br/>description: str<br/>steps: list[PipelineStep]"]:::data

    subgraph STEP["PipelineStep (per step in steps list)"]
        style STEP fill:#e8f4fd,stroke:#1565C0,color:#0D47A1
        S1["PipelineStep fields:<br/>──────────────────────<br/>adapter_name: str  (tool name in Toolbox)<br/>action: str = 'execute'<br/>input_mapping: dict  (literal or $-ref values)<br/>output_key: str = 'step_{i}'<br/>timeout: int = 60  (seconds per step)"]:::data
    end

    RESOLVE["_resolve_inputs(input_mapping, context)<br/>──────────────────────<br/>for each value in input_mapping:<br/>  if starts with '$': walk path through context dict<br/>    '$prev.content' → context['prev'].content<br/>    '$step_0.structured' → context['step_0'].structured<br/>  else: pass through as literal<br/>→ dict  (resolved kwargs)"]:::proc

    GET["registry.get(adapter_name)<br/>──────────────────────<br/>Toolbox lookup by name<br/>→ Tool instance"]:::proc

    EXEC["await tool.execute(**resolved_inputs)<br/>──────────────────────<br/>→ ToolExecutionResult(content, is_error,<br/>  structured_content, app_data)"]:::proc

    CTX["context dict update:<br/>──────────────────────<br/>context[step.output_key] = result<br/>context['prev'] = result<br/>context['$step_{i}'] = result"]:::proc

    ERR{"is_error or<br/>timeout?"}:::dec

    ENG["PipelineEngine<br/>engine.py<br/>──────────────────────<br/>toolbox: Toolbox<br/>execute(pipeline: PipelineDef) → PipelineResult"]:::proc

    RES["PipelineResult<br/>──────────────────────<br/>success: bool<br/>step_results: list[StepResult]<br/>duration_ms: float<br/>error: str | None"]:::out

    DEF -->|"pipeline"| ENG
    ENG --> STEP
    STEP --> RESOLVE
    RESOLVE --> GET
    GET --> EXEC
    EXEC --> ERR
    ERR -->|"error"| RES
    ERR -->|"ok"| CTX
    CTX -->|"next step"| STEP
    CTX -->|"all steps done"| RES
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
flowchart TD
    classDef proc  fill:#E8EAF6,stroke:#3949AB,color:#1A237E
    classDef store fill:#FFF3E0,stroke:#E65100,color:#BF360C,stroke-dasharray:4 2
    classDef obj   fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C
    classDef dec   fill:#E3F2FD,stroke:#1565C0,color:#0D47A1,font-weight:bold

    IN["data: bytes | str | dict<br/>──────────────────────<br/>str/dict → UTF-8 encoded to bytes<br/>before size check"]:::proc

    DRS["DataRefStore.store(data)<br/>──────────────────────<br/>ref_id = uuid4()  (unique pointer)<br/>size_bytes = len(encoded_data)"]:::proc

    THRESH{"size_bytes<br/>< 1 MB?"}:::dec

    RD["Redis<br/>──────────────────────<br/>key: dataref:{ref_id}<br/>value: raw bytes<br/>TTL: default 3600s<br/>SETEX with TTL enforced"]:::store
    S3["S3 / MinIO<br/>──────────────────────<br/>key: datarefs/{ref_id}<br/>bucket: agent-artifacts<br/>no automatic TTL<br/>(cleanup_expired() sweeps stale)"]:::store

    REF["DataRef<br/>──────────────────────<br/>ref_id: str  (UUID)<br/>storage: 'redis' | 's3'<br/>key: str  (Redis key or S3 object key)<br/>size_bytes: int<br/>ttl: int  (seconds)<br/>pinned: bool  (prevent expiry for long jobs)"]:::obj

    RES["DataRefStore.resolve(ref: DataRef) → bytes<br/>──────────────────────<br/>routes to Redis.GET or S3.get_object<br/>based on ref.storage field"]:::proc

    WRAP["DataRefArtifactStore<br/>──────────────────────<br/>wraps DataRefStore<br/>satisfies kernel ArtifactStore Protocol<br/>used by ToolInvoker + ToolChainTool<br/>ref IDs exposed as plain strings<br/>at the tool boundary"]:::proc

    IN --> DRS
    DRS --> THRESH
    THRESH -->|"yes  (< 1MB)"| RD --> REF
    THRESH -->|"no  (≥ 1MB)"| S3 --> REF
    REF --> RES
    REF -.->|"pin(ref): remove TTL<br/>unpin(ref): restore TTL<br/>delete(ref): manual cleanup"| REF
    REF -.->|"ArtifactStore adapter"| WRAP
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
