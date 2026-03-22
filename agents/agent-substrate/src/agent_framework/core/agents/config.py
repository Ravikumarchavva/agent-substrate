"""AgentConfig — typed configuration value object for agent construction.

Centralises all agent parameters into a single dataclass so call sites
don't need to thread 15+ keyword arguments through every layer.

Usage::

    from agent_framework.core.agents.config import AgentConfig

    cfg = AgentConfig(
        name="researcher",
        description="Answers questions",
        system_instructions="You are a helpful assistant.",
        max_iterations=10,
    )
    agent = ReActAgent.from_config(cfg, model_client=client, tools=tools)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

from agent_framework.core.memory.memory_scope import MemoryScope


@dataclass
class AgentConfig:
    """All knobs for a ``ReActAgent`` in one place.

    Required
    --------
    name        Human-readable identifier shown in traces and logs.
    description One-sentence description used for routing and observability.

    Optional
    --------
    See individual field docstrings below.
    """

    # Identity
    name: str = "agent"
    description: str = "A helpful AI assistant."

    # Prompt
    system_instructions: str = (
        "You are a helpful AI assistant. Use the provided tools to solve "
        "the user's request. Think step-by-step."
    )

    # Execution limits
    max_iterations: int = 10
    run_timeout: Optional[float] = None       # seconds; None = no limit
    tool_timeout: float = 30.0                # per-tool timeout in seconds
    verbose: bool = True

    # Memory
    memory_scope: MemoryScope = MemoryScope.ISOLATED

    # Skills / prompt enrichment
    skill_dirs: Optional[List[Union[str, Path]]] = None

    # HITL
    tools_requiring_approval: Optional[List[str]] = None

    # Extra kwargs forwarded to the agent constructor (escape hatch)
    extra: dict = field(default_factory=dict)
