"""AgentConfig — typed configuration value object for agent construction.

Centralises all agent parameters into a single dataclass so call sites
don't need to thread 15+ keyword arguments through every layer.

Usage::

    from ravi.fabric.agents_base.config import AgentConfig

    cfg = AgentConfig(
        name="researcher",
        description="Answers questions",
        system_instructions="You are a helpful assistant.",
        max_iterations=10,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ravi.fabric.agents_base.agent_context import AgentContext

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

    # Context
    context: AgentContext

    # Prompt
    system_instructions: str = (
        "You are a helpful AI assistant. Use the provided tools to solve "
        "the user's request. Think step-by-step."
    )

    # Execution limits
    max_iterations: int = 50

    # Extra kwargs forwarded to the agent constructor (escape hatch)
    extra: dict = field(default_factory=dict)
