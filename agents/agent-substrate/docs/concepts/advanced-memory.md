# Advanced Memory

Agent Substrate ships three additional memory backends beyond the default in-memory
history provider — each trading infrastructure complexity for capacity and persistence.

| Backend | Module | Best for |
|---|---|---|
| **Vector Memory** | `capabilities/vector/` | Semantic recall across long histories |
| **Graph Memory** | `capabilities/graph/` | Relationship-aware retrieval (entities + edges) |
| **Paged Memory** | `capabilities/history/` | Compaction-free infinite history via pagination |

Select the pages in this section for full API reference and usage examples.
