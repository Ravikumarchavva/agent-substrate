# Memory And Context

Memory stores the conversation history. Context decides what subset of that history gets sent to the model.

## Memory backends

- `InMemoryHistoryProvider` for simple in-process sessions
- `RedisHistoryProvider` for Redis-backed session memory
- `PostgresHistoryProvider` for Postgres-backed persistent session memory

## Context builders

Context builders shape the final prompt by applying ordering, compaction (e.g. sliding window, summarization), and truncation rules.

## Important design point

Memory and context are separate concerns. This lets you keep a full history while still trimming the prompt to the model’s effective budget.