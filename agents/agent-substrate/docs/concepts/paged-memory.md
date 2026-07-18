# Paged Memory

## The Problem With Truncation

Every LLM has a finite context window. The simplest strategy when a session grows too long is to drop the oldest messages. That works for short tasks but breaks agents that need to refer back to something said 50 turns ago — a user preference, a constraint, a decision.

Vector retrieval (embed everything, search by similarity) solves recall but introduces a different failure: it is probabilistic. A critically important message may score low against the current query and never surface. The agent silently forgets.

Paged memory takes a different approach: **make forgetting explicit and reversible**.

---

## The Core Idea

Messages are stored in numbered **pages**. Each page is a fixed-size chunk of conversation turns (e.g. 20 turns per page). When a page fills up it is frozen, summarised, and its summary is recorded in a compact **page index**. The index is always kept in the active context window. Old page bodies are stored but not loaded unless explicitly requested.

```
Active context window
┌──────────────────────────────────────────────────────┐
│  Page Index                                          │
│  ┌────┬─────────────┬──────────────────────────────┐ │
│  │ #  │ Turns       │ Summary                      │ │
│  │ 1  │ 1–20        │ User set up project, chose   │ │
│  │    │             │ Python, budget is $5k/month  │ │
│  │ 2  │ 21–40       │ Discussed API design, user   │ │
│  │    │             │ prefers REST over GraphQL    │ │
│  │ 3  │ 41–55       │ Deployment to AWS, wants     │ │
│  │    │             │ Fargate, no Kubernetes       │ │
│  └────┴─────────────┴──────────────────────────────┘ │
│                                                      │
│  Current page (in progress)                          │
│  Turn 56: user  — "What about the database?"        │
│  Turn 57: agent — "Given your $5k budget…"          │
│  Turn 58: user  — "Can you remind me what we…"      │
└──────────────────────────────────────────────────────┘
```

The agent can read the index and — if a summary is not enough — ask for a specific page body to be loaded.

---

## Why This Is Orthogonal To Vector and Graph Memory

| Approach | How it works | What it's good at | Failure mode |
|---|---|---|---|
| **Truncation** | Drop oldest turns | Zero overhead | Loses everything old |
| **Vector retrieval** | Embed + cosine search | Fuzzy, semantic recall | Misses causally important but semantically distant turns |
| **Graph memory** | Entity nodes + edges | Relationship traversal across topics | Misses unstructured narrative context |
| **Paged memory** | Explicit pages + index | Structured, deterministic recall; agent controls what it loads | Index summary may miss detail; costs one LLM summarisation per page freeze |

They are not mutually exclusive. You could run paged memory as the primary structure, and build the vector/graph indexes from the same page bodies as secondary retrieval layers.

---

## Data Model

```
Page
├── page_id:    str          # "p1", "p2", …
├── turn_start: int          # first turn number in this page
├── turn_end:   int          # last turn number (None if current)
├── messages:   list[ChatMessage]
├── summary:    str | None   # generated when page is frozen
└── frozen:     bool

PageIndex
├── pages:      list[PageIndexEntry]
│   └── PageIndexEntry
│       ├── page_id:   str
│       ├── turns:     tuple[int, int]
│       ├── summary:   str
│       └── topics:    list[str]    # optional keyword hints
└── current_page_id: str
```

The `PageIndex` is small by design. With 20 turns per page and a 50-word summary per page, 100 pages ≈ 5 000 tokens — always fits in context.

---

## Lifecycle

```
New message arrives
       │
       ▼
Append to current page
       │
       ▼
Current page full? (turns >= page_size)
   │              │
  No             Yes
   │              │
   │              ▼
   │       Freeze current page
   │              │
   │              ▼
   │       Generate summary via LLM
   │              │
   │              ▼
   │       Add entry to PageIndex
   │              │
   │              ▼
   │       Open new current page
   │
   ▼
Build context window:
  [system prompt]
  + [page index as a formatted block]
  + [current page messages]
  + [any explicitly retrieved page bodies]
```

---

## Retrieval

Retrieval is agent-controlled, not automatic. Two mechanisms:

**1. Passive — index always in context**

The page index is injected as a system block before the current page. The LLM can read the summaries and mention relevant past context directly. No tool call needed.

**2. Active — retrieve a page body**

The agent is given a `retrieve_page` tool:

```python
# Tool schema
{
  "name": "retrieve_page",
  "description": "Fetch the full message history for a past conversation page.",
  "parameters": {
    "page_id": {"type": "string", "description": "e.g. 'p2'"}
  }
}
```

When the LLM calls `retrieve_page("p2")`, the page body is returned as a tool result, injected into the context for the current turn. The LLM then continues with full detail from that page.

This means the agent **decides** what to load. It doesn't rely on a similarity score — it reads the index, reasons about which page is relevant, and requests it explicitly.

---

## Where It Lives In The Codebase

Paged memory is a **compaction strategy** — it slots into the existing `CompactionPipeline` in `agents/context/compaction/`.

```
agents/context/compaction/
├── _base.py                 ← CompactionStrategy ABC
├── sliding_window.py        ← current default (truncate)
├── summarise.py             ← LLM summarisation (existing)
└── paged.py                 ← NEW: PagedMemoryCompactor
```

`PagedMemoryCompactor` implements the same `compact(messages) -> list[ChatMessage]` interface. It replaces old messages with the index block. The `retrieve_page` tool is registered separately in the agent's `Toolbox`.

Storage for page bodies:
- **Dev / tests** — `InMemoryFileStore` (already in `agents/storage/`)
- **Production** — `S3FileStore` or `DurableMemoryStore` (already in `capabilities/`)

No new kernel contracts are needed. The compactor only imports from `kernel` (content types, history protocol) and `agents/storage`.

---

## Implementation Sketch

```python
# agents/context/compaction/paged.py

@dataclass
class PageIndexEntry:
    page_id: str
    turns: tuple[int, int]
    summary: str
    topics: list[str] = field(default_factory=list)

class PagedMemoryCompactor:
    def __init__(
        self,
        *,
        page_size: int = 20,
        summariser: LLMClient,
        store: BlobStore | None = None,
    ) -> None:
        self._page_size = page_size
        self._summariser = summariser
        self._store = store
        self._index: list[PageIndexEntry] = []
        self._current_page: list[ChatMessage] = []
        self._turn_counter: int = 0

    async def compact(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        # 1. Ingest any new messages into current page
        # 2. If current page exceeds page_size → freeze + summarise
        # 3. Return: [index_block] + [current_page_messages]
        ...

    async def retrieve(self, page_id: str) -> list[ChatMessage]:
        """Called by retrieve_page tool to load a frozen page body."""
        ...

    async def _freeze_page(self) -> None:
        summary = await self._summarise(self._current_page)
        entry = PageIndexEntry(
            page_id=f"p{len(self._index) + 1}",
            turns=(self._turn_counter - len(self._current_page), self._turn_counter),
            summary=summary,
        )
        self._index.append(entry)
        if self._store:
            await self._store.put(entry.page_id, self._current_page)
        self._current_page = []

    def _render_index(self) -> ChatMessage:
        """Format the page index as a system message injected into context."""
        lines = ["## Conversation Page Index\n"]
        for e in self._index:
            lines.append(f"**{e.page_id}** (turns {e.turns[0]}–{e.turns[1]}): {e.summary}")
        return ChatMessage(role="system", content=[TextBlock(text="\n".join(lines))])
```

---

## Comparison With Current Compaction Strategies

| Strategy | When to use |
|---|---|
| `SlidingWindowCompactor` | Short tasks, throwaway sessions |
| `SummariseCompactor` | Medium sessions, summary is good enough |
| `PagedMemoryCompactor` | Long-running agents, personal assistants, anything where the agent may need to revisit a specific past decision |
| Semantic retrieval (future) | Fuzzy recall over very large histories; pair with paged memory for best results |

---

## Open Questions Before Implementing

1. **Who triggers retrieval?** The LLM via tool call (agent-controlled) is the simplest and most explicit. Automatic pre-fetch based on index keywords is possible but adds latency and may load irrelevant pages.
2. **How many pages in one retrieval?** Start with 1. Allowing bulk retrieval risks blowing the context window.
3. **Index size limit?** If the session runs for thousands of turns, even the index grows large. A "meta-index" (summaries of groups of pages) solves this at the cost of one more indirection level.
4. **Page size?** 20 turns is a reasonable default. Semantic boundaries (topic shifts) would be better but require more machinery.
5. **Persistence?** `InMemoryFileStore` for dev. Wire `S3FileStore` / `DurableMemoryStore` behind the `BlobStore` Protocol for production — the compactor itself doesn't change.
