"""Distributed durable agent execution via Restate + NATS.

Three complementary primitives:
- **Restate**: Durable execution — tool idempotency, HITL promise survival,
  crash recovery (journal-based replay).
- **NATS JetStream**: SSE fan-out — event streaming to N browser clients.
- **Actor Runtime (gRPC)**: Agent-to-agent RPC — handled separately by
  ``core/runtime`` + ``integrations/runtime/grpc``.

Package layout::

    distributed/
    ├── streaming.py     ← NATSStreamingBridge (thread-scoped SSE fan-out)
    ├── policies.py      ← ToolPolicy + derive_policy_from_tool()
    ├── activities.py    ← Restate-journaled functions (LLM, tool, memory)
    ├── workflow.py      ← AgentWorkflow (ReAct loop inside Restate)
    ├── restate_app.py   ← ASGI app serving Restate handlers
    ├── client.py        ← RestateClient (HTTP wrapper)
    └── worker.py        ← Standalone worker entry point
"""

from __future__ import annotations

from raavan.distributed.streaming import NATSStreamingBridge
from raavan.distributed.client import RestateClient
from raavan.distributed.policies import ToolPolicy, TOOL_POLICIES

__all__ = [
    "NATSStreamingBridge",
    "RestateClient",
    "ToolPolicy",
    "TOOL_POLICIES",
]
