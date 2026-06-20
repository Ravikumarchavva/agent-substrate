# storage/ — What Agents Remember

> **Source:** `kernel/storage/history.py` · `kernel/storage/memory.py` · `kernel/storage/vector.py` · `kernel/storage/graph.py` · `kernel/storage/blob.py` · `kernel/storage/tasks.py`

Six storage Protocols, each with a specific scope and purpose. All are swappable — in-memory for tests, Postgres/Redis for production — without changing any agent code.

---

## Storage Roles at a Glance

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef proto fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef impl fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C,stroke-dasharray:4 2
    classDef question fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20,font-style:italic

    HP["HistoryProvider\nWhat was SAID\nOrdered conversation transcript\nkey: (agent_id, session_id)"]:::proto
    STM["ShortTermMemory\nWhat was LEARNED this session\nKey-value session state\nkey: session_id"]:::proto
    LTM["LongTermMemory\nWhat was LEARNED forever\nExtracted cross-session facts\nkey: (agent_id, namespace)"]:::proto
    VS["VectorStore\nSemantic search corpus\nRAG document chunks\nkey: (id, collection)"]:::proto
    GS["GraphStore\nKnowledge graph\nEntities + relationships\nCypher queries optional"]:::proto
    BS["BlobStore\nBinary objects\nS3/MinIO abstraction\npin/unpin TTL control"]:::proto
    TS["TaskStore\nPer-agent Kanban boards\n6-state task lifecycle\nkey: (conversation_id, agent_id)"]:::proto

    IMP1["InMemoryHistoryProvider (L1)\nRedisHistoryProvider (L2)\nPostgresHistoryProvider (L2)"]:::impl
    IMP2["RedisSessionStore (L2)\nPostgresMemoryStore (L2)"]:::impl
    IMP3["RedisSessionStore (L2)\nPgVectorStore for semantic (L2)"]:::impl
    IMP4["PgVectorStore (L2)"]:::impl
    IMP5["AGEGraphStore (L2)"]:::impl
    IMP6["S3FileStore (L2)"]:::impl
    IMP7["TaskStore in-memory (L1)\nPgTaskStore (infrastructure/)"]:::impl

    HP -.-> IMP1
    STM -.-> IMP2
    LTM -.-> IMP3
    VS -.-> IMP4
    GS -.-> IMP5
    BS -.-> IMP6
    TS -.-> IMP7
```

---

## HistoryProvider — Conversation Transcript

The raw ordered log of `ChatMessage` turns. Does not summarize or compact — that's `CompactionStrategy`'s job.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph LR
    classDef proto fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef method fill:#E8EAF6,stroke:#3949AB,stroke-width:1px,color:#1A237E
    classDef note fill:#FFFDE7,stroke:#F57F17,stroke-width:1px,color:#E65100,font-style:italic

    HP["HistoryProvider"]:::proto

    APP["append(agent_id, msg, session_id, run_id)"]:::method
    APPN["append_many(agent_id, messages, session_id, run_id)"]:::method
    GET["get_messages(agent_id, session_id, limit, offset)"]:::method
    CLR["clear(agent_id, session_id)\nDelete ALL history for this session"]:::method
    CLRR["clear_run(agent_id, session_id, run_id)\nDelete ONLY messages from one run"]:::method
    CNT["count_messages(agent_id, session_id)"]:::method

    HP --> APP
    HP --> APPN
    HP --> GET
    HP --> CLR
    HP --> CLRR
    HP --> CNT

    N1["run_id tags each message\nclear_run() supports HistoryRetention.RUN\nwithout destroying cross-run context"]:::note
    CLRR -.- N1
```

---

## ShortTermMemory vs LongTermMemory

Two distinct memory scopes — session-local vs. permanent across all sessions.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef stm fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef ltm fill:#E8EAF6,stroke:#3949AB,stroke-width:2px,color:#1A237E,font-weight:bold
    classDef method fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20
    classDef mem fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C

    STM["ShortTermMemory\nKey-value state within one session\nsurvives across runs in same session\nValues: JSON-serializable dict"]:::stm
    LTM["LongTermMemory\nExtracted facts across ALL sessions\nscoped by (agent_id, namespace)"]:::ltm

    STMM1["get_state(session_id) → dict"]:::method
    STMM2["set_state(session_id, state)"]:::method
    STMM3["update_state(session_id, patch)\nAtomic merge — other keys preserved\nPrefer over get+set for concurrent agents"]:::method
    STMM4["clear(session_id)"]:::method

    LTMM1["save(agent_id, content, namespace, ttl_seconds) → id"]:::method
    LTMM2["search(agent_id, query, namespace, limit) → list[Memory]"]:::method
    LTMM3["get(agent_id, memory_id, namespace) → Memory | None"]:::method
    LTMM4["delete(agent_id, memory_id, namespace) → bool"]:::method
    LTMM5["clear(agent_id, namespace)"]:::method

    MEM["Memory (frozen)\ncontent: str\nscore: float\nid: str\nmetadata: dict"]:::mem

    STM --> STMM1
    STM --> STMM2
    STM --> STMM3
    STM --> STMM4

    LTM --> LTMM1
    LTM --> LTMM2
    LTM --> LTMM3
    LTM --> LTMM4
    LTM --> LTMM5
    LTMM2 --> MEM
```

**Three memory types — how they differ:**

| Type | Answers | Scope | Example use |
|---|---|---|---|
| `HistoryProvider` | *What was said?* | Per (agent, session) | Conversation replay, compaction |
| `ShortTermMemory` | *What was learned this session?* | Per session | Cart state, user preferences this session |
| `LongTermMemory` | *What is known permanently?* | Per (agent, namespace) | "User's name is Ravi", "Prefers Python" |

---

## VectorStore and GraphStore — RAG

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph LR
    classDef proto fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef doc fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E
    classDef warn fill:#FFEBEE,stroke:#C62828,stroke-width:1px,color:#B71C1C,font-weight:bold

    VS["VectorStore\nadd(documents, collection)\nsearch(query_embedding, collection, limit, filter)\nget(ids, collection)\nupsert(documents, collection)\ndelete(ids, collection)\nlist_collections()\ndelete_collection(collection)"]:::proto

    GS["GraphStore\nadd_entities(entities)\nadd_relationships(relationships)\nget_neighbors(entity_id, depth, types)\ndelete_entity(entity_id)\ndelete_relationship(relationship_id)"]:::proto

    CYPH["CypherCapable (optional)\nquery_cypher(query, params)\n\nCheck: isinstance(store, CypherCapable)\nbefore calling"]:::proto

    DOC["Document (frozen)\ncontent: list[ContentBlock]\nid: str\nembedding: list[float] | None\nmetadata: dict\n\nDocument.from_text(s) → quick text doc"]:::doc

    SR["SearchResult (frozen)\nid: str\ncontent: list[ContentBlock]\nscore: float\nmetadata: dict"]:::doc

    WARN["Document ≠ DocumentBlock\nDocument = RAG text chunk (VectorStore)\nDocumentBlock = ContentBlock carrying a PDF file\nNever conflate them"]:::warn

    VS --> DOC
    VS --> SR
    GS --> CYPH
    DOC -.- WARN
```

`Document.content` is `list[ContentBlock]` — RAG documents can contain images, audio, or structured data, not just text. `Document.from_text(s)` is the shortcut for plain-text chunks.

---

## TaskStore — Per-Agent Kanban Board

Task state is a 6-state lifecycle. Both `Task` and `TaskList` are **frozen** — all mutations use `dataclasses.replace()`.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
stateDiagram-v2
    [*] --> planned : create_task_list()
    planned --> in_progress : update_status(IN_PROGRESS)
    in_progress --> succeeded : update_status(SUCCEEDED)
    in_progress --> blocked : update_status(BLOCKED)
    blocked --> in_progress : update_status(IN_PROGRESS)
    in_progress --> failed : update_status(FAILED)
    failed --> in_progress : increment_retry() retry_count < max_retries
    failed --> abandoned : increment_retry() retry_count >= max_retries
    failed --> in_progress : force_retry() user override — resets retry_count to 0
    abandoned --> in_progress : force_retry() user override
    succeeded --> [*]
    abandoned --> [*]
```

**`TaskList`** groups multiple tasks and tracks `max_retries`. One board per `(conversation_id, agent_id)` pair — sub-agents each get their own board.

**`settle_conversation(conversation_id)`** — flips all `in_progress` tasks to `succeeded` when a run ends normally. Stops the UI Kanban from spinning after the agent finishes.
