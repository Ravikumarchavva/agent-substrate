# Concepts

These pages explain how Ravi works internally without forcing you to read the whole source tree.

## Read in this order

1. [Agent Lifecycle](agent-lifecycle.md)
2. [Tools And HITL](tools-and-hitl.md)
3. [Memory And Context](memory-and-context.md)
4. [Streaming And Events](streaming-and-events.md)
5. [Durable Runtime](durable-runtime.md)

## Advanced Memory Strategies

Three orthogonal approaches to long-context memory — each solves a different failure mode, and they can run simultaneously.

| Strategy | How it recalls | Best for |
|---|---|---|
| [Vector Memory](vector-memory.md) | Embed + cosine search | Fuzzy semantic recall over large histories |
| [Graph Memory](graph-memory.md) | Entity nodes + relationship traversal | Structured facts, constraints, decisions |
| [Paged Memory](paged-memory.md) | Explicit pages + index + agent-controlled retrieval | Full fidelity recall; agent decides what to load |