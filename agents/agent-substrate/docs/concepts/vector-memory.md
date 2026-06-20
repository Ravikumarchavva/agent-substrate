# Vector Memory

## What It Is

Vector memory stores every conversation turn as an embedding (a numeric vector that captures semantic meaning). When a new message arrives, the agent embeds it and searches for the most similar past turns. Only the top-k matches are pulled back into the context window — the rest stay in storage.

This is **retrieval-augmented memory (RAM)**: the agent always has the most *semantically relevant* history, not just the most recent.

---

## How It Works

```
Turn arrives
     │
     ▼
Embed the message text
     │
     ▼
Upsert into VectorStore (collection = session_id)
     │
     ▼
Embed the current user query
     │
     ▼
Search VectorStore → top-k similar past turns
     │
     ▼
Build context window:
  [system prompt]
  + [retrieved k turns, in chronological order]
  + [last N recent turns]
  + [current turn]
```

The final context the LLM sees is a blend of **relevant** history (semantic) and **recent** history (recency), not a raw sliding window.

---

## Strengths

- **No explicit context loss** — every turn is stored; nothing is dropped, just not always loaded.
- **Scales to very long sessions** — only the embedding index grows, not the active context.
- **Zero agent behaviour change** — the agent doesn't need to call any retrieval tool; the compactor handles it transparently.

## Weaknesses

- **Probabilistic recall** — a past turn that is causally important but semantically distant from the current query may never surface. The agent silently acts as if it doesn't exist.
- **Embedding cost per turn** — requires an `EmbeddingClient` call on every message in and every query.
- **Ordering artefacts** — retrieved turns are reinserted out of chronological sequence; some models are confused by non-contiguous history.
- **`InMemoryVectorStore` is not persistent** — lost on restart. Use `PgVectorStore` (L2) for real sessions.

---

## When To Use It

| Scenario | Good fit? |
|---|---|
| User asks questions over a large knowledge base already in the vector store | Yes |
| Long personal-assistant sessions where old preferences matter | Yes (but watch causal-chain gap) |
| Short task-completion agents | Overkill — use sliding window |
| Agent needs to enforce an earlier hard constraint | Risky — prefer Paged Memory |

---

## Where It Lives In The Codebase

```
kernel/storage/vector.py      ← VectorStore Protocol, Document, SearchResult
agents/storage/vector.py      ← InMemoryVectorStore  (dev / tests)
capabilities/vector/          ← PgVectorStore         (production)
capabilities/knowledge/       ← RAGPipeline, GraphRAGPipeline (document-level RAG)
```

For session memory specifically, the compactor would live at:

```
agents/context/compaction/semantic.py   ← SemanticMemoryCompactor (not yet built)
```

It implements `compact(messages) -> list[ChatMessage]` — same interface as every other compaction strategy. Internally it writes to a `VectorStore` and reads back the top-k at compact time.

---

## Relationship To Other Memory Approaches

Vector memory is **orthogonal** to Graph memory and Paged memory.

- It can run alongside either without conflict.
- Use vector as the fuzzy recall layer and paged memory as the structured/explicit layer.
- Use graph memory to follow entity relationships that vector similarity would miss.

See [Graph Memory](graph-memory.md) and [Paged Memory](paged-memory.md).
