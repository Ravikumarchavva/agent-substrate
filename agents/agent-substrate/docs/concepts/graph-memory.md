# Graph Memory

## What It Is

Graph memory models the conversation as a **knowledge graph** rather than a list of messages. Entities mentioned by the user or agent (people, projects, preferences, decisions) become nodes. Relationships between them become edges. When context is needed, the agent traverses the graph from a relevant starting node instead of scanning all past messages.

---

## How It Works

```
Turn arrives
     │
     ▼
Extract entities + relationships from the message text
(via LLM extraction call or NER)
     │
     ▼
Upsert nodes into GraphStore
Upsert edges (subject → predicate → object)
     │
     ▼
On next query: identify seed entities in the current message
     │
     ▼
Traverse GraphStore (BFS from seeds, depth=N)
     │
     ▼
Build context window:
  [system prompt]
  + [subgraph rendered as structured facts]
  + [recent turns]
```

Example subgraph rendered into context:

```
Known facts:
  User.name      = "Alice"
  User.budget    = "$5k/month"
  Project.stack  = ["Python", "FastAPI"]
  Project.deploy = "AWS Fargate (not Kubernetes)"
  Decision.db    = "Postgres" (decided turn 34)
```

The LLM sees structured facts instead of raw conversation text. It is harder to miss a constraint stored as a node than one buried in a long message.

---

## Strengths

- **Structured, queryable recall** — constraints and decisions are explicit nodes, not hidden in prose.
- **Relationship traversal** — "what do we know about the deployment?" → traverse from `Project.deploy` node to linked nodes.
- **Compact context representation** — a subgraph of 20 entities takes far fewer tokens than 20 turns of dialogue.
- **Durable across sessions** — the graph persists independently of the message log; it accumulates across multiple conversations.

## Weaknesses

- **Extraction quality is everything** — if the LLM extraction call misses an entity or mislabels a relationship, that fact is silently lost from the graph.
- **Unstructured narrative is lost** — jokes, tone, emotional context, nuanced explanations don't map cleanly to nodes and edges.
- **Extraction cost** — every turn requires an LLM call (or NER pipeline) to extract entities before storing.
- **`InMemoryGraphStore` is not persistent** — lost on restart. Use `AGEGraphStore` (L2, Apache AGE on Postgres) for production.

---

## When To Use It

| Scenario | Good fit? |
|---|---|
| Personal assistant that tracks user preferences, projects, people | Excellent |
| Agent that needs to enforce hard decisions made earlier ("we agreed on Fargate") | Excellent |
| Task-completion agent over a single session | Overkill |
| Creative or open-ended conversation where tone matters | Poor fit — narrative is lost |

---

## Data Model

```
Entity
├── entity_id:    str
├── type:         str          # "User", "Project", "Decision", "Preference", …
├── name:         str
└── properties:   dict[str, str]

Relationship
├── relationship_id: str
├── source_id:       str       # entity_id
├── target_id:       str       # entity_id
├── type:            str       # "has_budget", "prefers", "decided", …
└── properties:      dict[str, str]   # {"turn": "34", "confidence": "high"}
```

---

## Where It Lives In The Codebase

```
kernel/storage/graph.py       ← GraphStore Protocol, Entity, Relationship, SubGraph
agents/storage/graph.py       ← InMemoryGraphStore  (dev / tests)
capabilities/graph/           ← AGEGraphStore        (production, Apache AGE)
capabilities/knowledge/       ← GraphRAGPipeline     (document-level graph RAG)
```

The session-memory graph compactor would live at:

```
agents/context/compaction/graph.py   ← GraphMemoryCompactor (not yet built)
```

It implements `compact(messages) -> list[ChatMessage]`. Internally it calls an extractor LLM to pull entities from each new turn, writes them to a `GraphStore`, traverses from the current query's seed entities, and injects the resulting subgraph as a structured system block.

---

## Relationship To Other Memory Approaches

Graph memory is **orthogonal** to Vector memory and Paged memory.

- **Vector memory** recalls semantically similar past messages; graph memory recalls structured facts about known entities. A fact that scores low on cosine similarity (e.g. a budget set 200 turns ago) is still reliably recalled via graph traversal if the entity is mentioned.
- **Paged memory** stores raw turns in pages and lets the agent retrieve whole pages explicitly. Graph memory distils those turns into structured facts — lossy but far more compact.

All three can run simultaneously as independent compaction layers.

See [Vector Memory](vector-memory.md) and [Paged Memory](paged-memory.md).
