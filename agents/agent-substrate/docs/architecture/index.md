# Architecture

Raavan is structured around a simple developer-facing model and a more robust execution model underneath it.

At the API level you create an agent, register tools, attach memory, and call `run()`. Underneath that, the framework manages prompt assembly, guardrails, tool execution, event streaming, and optionally a durable runtime that can survive process crashes or human wait states.

## Core layers

### Client and UI

- The browser or frontend UI sends chat input and receives streaming updates over SSE.
- The Next.js BFF proxies requests and keeps frontend-specific concerns outside the Python runtime.

### Application server

- FastAPI handles routing, dependency wiring, auth, and streaming response orchestration.
- `app.state.*` is the dependency container for runtime services, tools, model clients, and storage.

### Agent core

- `ReActAgent` coordinates the Think → Act → Observe loop.
- Guardrails inspect input, tool calls, and output.
- Memory and context builders determine what the model sees on each iteration.

### Durable runtime

- Restate virtual objects and handlers provide resumable workflows.
- Activities isolate external I/O such as model calls, tool execution, persistence, and event emission.
- HITL requests suspend a workflow without burning worker capacity.

### Infrastructure

- PostgreSQL stores persistent application state.
- Redis stores hot session state and supports distributed coordination.
- NATS or the event bridge fans runtime events out to clients.

## Start here

- [Diagrams](diagrams.md) for system pictures and deep links.
- [Legacy Deep Dives](legacy-deep-dives.md) for archived design docs and the interactive explorer.