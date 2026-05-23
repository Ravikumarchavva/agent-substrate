"""Agent checkpointing — fault recovery for long-running agent runs.

The checkpointing system has been upgraded to hierarchical tree-structured
checkpoints.  The canonical implementation now lives in
``ravi.core.runtime._checkpoint``.

This module re-exports the new types for backward compatibility and also
preserves access to the legacy ``AgentCheckpoint`` model.

Usage::

    from ravi.core.checkpointing import RunCheckpoint, InMemoryCheckpointStore

    store = InMemoryCheckpointStore()
    catalog = AgentCatalog()
    catalog.register_model("primary", model_client)
    catalog.register_checkpoint_store("default", store)
    agent = ReActAgent(catalog=catalog, checkpoint_every=5)
    result = await agent.run_with_recovery("long task...")
"""

from __future__ import annotations

# Legacy model (kept for backward compat — new code should use RunCheckpoint)
from ravi.core.checkpointing.models import AgentCheckpoint

# New hierarchical checkpointing (canonical location: core.runtime._checkpoint)
from ravi.core.runtime._checkpoint import (
    CheckpointStatus,
    CheckpointStore,
    InMemoryCheckpointStore,
    RunCheckpoint,
)

__all__ = [
    # Legacy
    "AgentCheckpoint",
    # New
    "RunCheckpoint",
    "CheckpointStatus",
    "CheckpointStore",
    "InMemoryCheckpointStore",
]
