"""AgentCheckpoint — serializable snapshot of agent state for fault recovery."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class AgentCheckpoint(BaseModel):
    """Serializable snapshot of an agent's execution state.

    Stores enough information to resume a ReAct run from the last checkpoint
    iteration after a process restart, network failure, or planned pause.

    Fields:
        checkpoint_id: Unique ID for this checkpoint (auto-generated UUID).
        run_id: The execution run ID from ExecutionContext.
        agent_id: The agent's name/ID (from ExecutionContext.agent_id or agent.name).
        thread_id: Conversation thread ID for session continuity.
        iteration: The last completed ReAct loop iteration number (1-based).
        messages: Serialised message history as raw dicts (model_dump output).
        pending_tool_ids: Tool call IDs that were sent but not yet resolved.
        extra: Open-ended dict for subclass-specific extra state.
        created_at: UTC timestamp of when this checkpoint was created.
    """

    checkpoint_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    agent_id: str
    thread_id: str = ""
    iteration: int
    messages: list[dict[str, Any]] = Field(default_factory=list)
    pending_tool_ids: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"frozen": False}
