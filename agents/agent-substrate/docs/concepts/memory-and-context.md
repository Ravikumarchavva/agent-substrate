# Memory And Context

Memory stores the conversation history. Context decides what subset of that history gets sent to the model.

## Memory backends

- `UnboundedMemory` for simple in-process sessions
- `SlidingWindowMemory` for bounded local sessions
- `RedisMemory` for durable or resumable sessions

## Context builders

Context builders shape the final prompt by applying ordering, truncation, and strategy rules.

## Important design point

Memory and context are separate concerns. This lets you keep a full history while still trimming the prompt to the model’s effective budget.