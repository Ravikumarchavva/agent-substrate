"""Restate runtime backend.

Provides:
- ``RestateRuntime`` — ``AgentRuntime`` with durable execution via Restate
  virtual objects, journalling, and HITL suspension via durable promises.
- ``RestateWorkflowClient`` — HTTP client for starting/querying/cancelling
  durable workflows (pipelines, chains, agent loops).  Drop-in replacement
  for the former ``TemporalClient``.
- ``ToolPolicy`` / ``derive_policy_from_tool`` — per-tool execution policies.

Requires: ``restate-sdk``, ``httpx``.
"""

from __future__ import annotations

from ravi.integrations.runtime.restate.client import RestateWorkflowClient
from ravi.integrations.runtime.restate.policies import (
    ToolPolicy,
    derive_policy_from_tool,
)
from ravi.integrations.runtime.restate.runtime import RestateRuntime

__all__ = [
    "RestateRuntime",
    "RestateWorkflowClient",
    "ToolPolicy",
    "derive_policy_from_tool",
]
