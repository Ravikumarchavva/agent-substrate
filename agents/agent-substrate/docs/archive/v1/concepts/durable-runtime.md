# Durable Runtime

The local in-process loop is good for development. The durable runtime is what you use when the workflow must survive crashes, restarts, and human delays.

## What Restate adds

- resumable workflows
- exactly-once semantics around completed activity steps
- wait states for HITL without tying up a worker thread
- explicit workflow handlers for start, resolve, query, and cancel operations

## Runtime shape

- workflow handlers coordinate the long-lived state machine
- activities isolate model calls, tool execution, persistence, and event publishing
- the worker process hosts the Restate app and injected dependencies

## When to use it

Use the durable runtime when you care about correctness more than simplicity: approvals, long-running jobs, expensive tool calls, or production operations.