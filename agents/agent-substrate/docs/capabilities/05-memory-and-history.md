# 5 · Memory & History

Two distinct concepts — both live in L2 but serve different purposes:

| | Memory | History |
|---|---|---|
| **What it stores** | Arbitrary key-value session state | Ordered `ChatMessage` conversation log |
| **Who reads it** | Agent business logic (`ctx.session_store.get_state()`) | `InMemoryHistoryProvider` / compaction inside ReActAgent |
| **Kernel Protocol** | `SessionStore` (`kernel/storage/memory.py`) | `HistoryProvider` (`kernel/storage/history.py`) |
| **Scope** | Per-session dict | Per-agent × per-session list |

## Memory backends

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart TB
    classDef proto fill:#E8EAF6,stroke:#3949AB,color:#1A237E,font-weight:bold
    classDef impl  fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef store fill:#FFF3E0,stroke:#E65100,color:#BF360C,stroke-dasharray:4 2

    SM["SessionStore — kernel Protocol (kernel/storage/memory.py)<br/>get_state · set_state · update_state · clear"]:::proto

    subgraph REDIS["RedisSessionStore (memory/redis_session_store.py)"]
        direction TB
        RSS["RedisSessionStore — ttl=3600<br/>get → HGETALL · set → DEL+HSET+EXPIRE<br/>update → HSET+EXPIRE (patch) · clear → DEL"]:::impl
        RD["Redis HASH — key session:state:{session_id}<br/>field → JSON value · TTL reset on every write"]:::store
        RSS --> RD
    end

    subgraph POSTGRES["PostgresMemoryStore (memory/postgres_memory_store.py)"]
        direction TB
        PMS["PostgresMemoryStore<br/>save(agent_id, content, metadata?, namespace?) → mem_id<br/>search(agent_id, query, limit) → list[dict] (FTS)<br/>delete(agent_id, mem_id) · create_tables()"]:::impl
        PG["PostgreSQL table agent_memories<br/>id · agent_name · content · metadata JSONB · namespace<br/>search_vec TSVECTOR (GIN index) · created_at"]:::store
        PMS --> PG
    end

    SM --> RSS
    SM --> PMS
```

### `RedisSessionStore`

Short-term, volatile session state. Stored as a **Redis HASH** (`session:state:<session_id>`). Each field is a JSON-serialised value.

```python
from substrate.capabilities.memory import RedisSessionStore

store = RedisSessionStore(redis_url="redis://localhost:6379/0", ttl=3600)
await store.connect()

await store.update_state("sess-123", {"preferred_language": "Python", "step": 3})
state = await store.get_state("sess-123")   # → {"preferred_language": "Python", "step": 3}
await store.set_state("sess-123", {})       # overwrite entire state
await store.clear("sess-123")              # delete key
await store.disconnect()
```

| Operation | Redis command | Notes |
|---|---|---|
| `get_state` | `HGETALL` | Returns full dict |
| `set_state` | `DEL` + `HSET` + `EXPIRE` | Atomic via pipeline |
| `update_state` | `HSET` + `EXPIRE` | Merges (patch, not replace) |
| `clear` | `DEL` | Removes key entirely |

### `PostgresMemoryStore`

Long-term, searchable memory. Stored in an **`agent_memories`** table with a generated `tsvector` column for full-text search — no embeddings required.

```python
from substrate.capabilities.memory import PostgresMemoryStore

store = PostgresMemoryStore(database_url="postgresql+asyncpg://...")
async with store:
    mem_id = await store.save(agent_id, "User prefers Python over JavaScript")
    memories = await store.search(agent_id, "language preference", limit=5)
    await store.delete(agent_id, mem_id)
```

Schema (auto-created by `create_tables()`):

```sql
CREATE TABLE agent_memories (
    id          TEXT PRIMARY KEY,
    agent_name  TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',
    namespace   VARCHAR(255) NOT NULL DEFAULT 'default',
    search_vec  TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON agent_memories USING GIN (search_vec);
```

## History backends

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','fontSize': '13px'}}}%%
flowchart TB
    classDef proto fill:#E8EAF6,stroke:#3949AB,color:#1A237E,font-weight:bold
    classDef impl  fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef store fill:#FFF3E0,stroke:#E65100,color:#BF360C,stroke-dasharray:4 2
    classDef dev   fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C

    HP["HistoryProvider — kernel Protocol (kernel/storage/history.py)<br/>append · get_messages · clear · count_messages<br/>keyed by (agent_type, agent_key, session_id)"]:::proto

    INPROV["InMemoryHistoryProvider (agents/context/)<br/>_store: dict[key, list[ChatMessage]]<br/>no TTL · no persistence — dev / tests only"]:::dev

    subgraph REDISHIST["RedisHistoryProvider (history/redis_history.py)"]
        direction TB
        RHP["RedisHistoryProvider — ttl=3600, max_messages=200<br/>append → LPUSH + LTRIM + EXPIRE<br/>get_messages → LRANGE · clear → DEL<br/>refresh_ttl · clear_run(run_id)"]:::impl
        RD["Redis LIST — key ravi:hist:{type}:{key}:{session}<br/>element JSON {run_id, msg} · LTRIM caps length"]:::store
        RHP --> RD
    end

    subgraph PGHIST["PostgresHistoryProvider (history/postgres_history.py)"]
        direction TB
        PHP["PostgresHistoryProvider — SQLAlchemy 2 async<br/>append → upsert session + INSERT message<br/>get_messages → SELECT ORDER BY sequence<br/>session_id validated by regex at boundary"]:::impl
        PG["PostgreSQL — history_sessions (unique key, count)<br/>history_messages (sequence, payload JSONB, run_id)"]:::store
        PHP --> PG
    end

    HP --> INPROV
    HP --> RHP
    HP --> PHP
```

### `RedisHistoryProvider`

Redis LIST (`ravi:hist:{agent_type}:{agent_key}:{session_id}`). Each element is JSON: `{"run_id": "...", "msg": <ChatMessage dict>}`.

Key behaviors:
- **`max_messages` cap** — `LTRIM` on every write keeps the list bounded (default 200)
- **TTL refresh** — `EXPIRE` is reset on every `append` and `refresh_ttl()`
- **`clear_run`** — can selectively delete messages from a specific run without clearing the whole session

```python
from substrate.capabilities.history import RedisHistoryProvider

provider = RedisHistoryProvider(
    redis_url="redis://localhost:6379/0",
    ttl=3600,
    max_messages=200,
)
await provider.connect()
# Pass to agent via context config
```

### `PostgresHistoryProvider`

SQLAlchemy 2.0 async ORM. Two tables:

- **`history_sessions`** — one row per `(agent_type, agent_key, session_id)` tuple, tracks created/updated timestamps and message count
- **`history_messages`** — one row per message, JSONB payload, ordered by sequence number

All queries are fully parameterised — no raw SQL string interpolation. Raw `session_id` values are validated at the public boundary (regex check) before being composed into the internal `agent_type:agent_key:session_id` key.

```python
from substrate.capabilities.history import PostgresHistoryProvider

provider = PostgresHistoryProvider(
    database_url="postgresql+asyncpg://postgres:postgres@localhost/agentdb",
)
await provider.connect()
```

## Choosing a backend

| Need | Use |
|---|---|
| Fast, ephemeral session state | `RedisSessionStore` |
| Durable long-term agent memory with search | `PostgresMemoryStore` |
| Dev/testing (no infra) | `InMemoryHistoryProvider` |
| Production chat history, restartable | `PostgresHistoryProvider` |
| High-throughput, tolerate loss on restart | `RedisHistoryProvider` |

## Wiring

Both providers are injected at lifespan and passed through `ContextConfig`:

```python
# In lifespan
history = RedisHistoryProvider(redis_url=settings.REDIS_URL)
await history.connect()
app.state.history_provider = history

# In agent factory
context = ContextConfig(history_provider=app.state.history_provider)
agent = ReActAgent(llm_client, context=context)
```
