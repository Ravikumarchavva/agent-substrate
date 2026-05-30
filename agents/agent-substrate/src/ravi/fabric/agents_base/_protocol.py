"""Structural contract for registrable agents.

The kernel defines *what an agent is* — the methods the runtime drives. The
concrete base implementation, ``ActorAgent`` (with runtime wiring, catalog,
tools), lives one layer up in :mod:`ravi.fabric.actors.actor`.

The plugin registry validates ``@register_agent`` targets against this protocol
so the kernel never needs to import the fabric layer. It is intentionally
**method-only** so it can be used with :func:`issubclass` (a ``runtime_checkable``
protocol with data members cannot be).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AgentProtocol(Protocol):
    """Minimal structural interface every agent satisfies.

    ``ActorAgent`` and all concrete agents (``AssistantAgent``,
    ``OrchestratorAgent``, …) conform to this. ``on_message`` is the single
    runtime entry point; ``start``/``stop`` manage registration lifecycle.
    """

    async def on_message(self, ctx: object, content: object) -> object: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...
