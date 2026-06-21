"""Skill contract — a named prompt package injected into an agent's system instructions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Skill:
    """A prompt-skill that extends an agent's behaviour via injected instructions.

    Skills are loaded from ``capabilities/tools/skills/<name>/SKILL.md`` or
    constructed inline.  When attached to an agent, ``instructions`` are
    appended to the effective system prompt and ``allowed_tools`` names are
    cross-referenced against the agent's tool registry at runtime.

    Example::

        from substrate.kernel import Skill

        summarise = Skill(
            name="summarisation",
            instructions="Always end your reply with a one-sentence TL;DR.",
        )
    """

    name: str
    instructions: str
    description: str = ""
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    path: str | None = None
    version: str = "1"


__all__ = ["Skill"]
