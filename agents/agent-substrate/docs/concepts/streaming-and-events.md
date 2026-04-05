# Streaming And Events

Raavan streams runtime progress over SSE so the frontend can update in real time.

## Typical event flow

1. user sends a message
2. the backend starts the agent run
3. `text_delta` events stream partial response content
4. `tool_call` events describe tool invocations
5. `tool_result` events deliver structured results
6. HITL events request approval or human input
7. `completion` or `error` closes the turn

## Why SSE

SSE is simple, browser-native, and works well for ordered, server-originated event streams.

See [Events](../reference/events.md) for the payload-level reference.