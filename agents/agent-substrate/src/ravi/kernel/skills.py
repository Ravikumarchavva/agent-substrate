"""Skill contract — a named prompt package injected into an agent's system instructions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Skill:
    """A prompt-skill that extends an agent's behaviour via injected instructions.

    Skills are loaded from ``capabilities/skills/<name>/SKILL.md`` or constructed
    inline. When attached to a ``ReActAgent`` their ``instructions`` are appended
    to the effective system prompt and their ``allowed_tools`` names are
    cross-referenced against the agent's tool registry at runtime.

    Example::

        from ravi.kernel import Skill

        summarise = Skill(
            name="summarisation",
            instructions="Always end your reply with a one-sentence TL;DR.",
        )
    """

    name: str
    instructions: str
    description: str = ""
    allowed_tools: list[str] = field(default_factory=list)


__all__ = ["Skill"]
