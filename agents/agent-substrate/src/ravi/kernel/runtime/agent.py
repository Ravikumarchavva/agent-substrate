"""DurableAgent — the revised agent contract for the durable runtime.

Replaces ``kernel/agent.py::Agent`` (synchronous request-reply model) with a
coroutine that survives crashes, multi-day pauses, and worker migration.

Why the old contract cannot scale
----------------------------------
``Agent.on_message(ctx, payload) -> reply | None`` assumes the reply is
produced in a single in-process call.  It cannot survive a process crash, a
multi-day HITL pause, or a run migrating to a different worker.

The new model
-------------
``DurableAgent.run(ctx, inbox)`` receives a batch of messages and an execution
context that wires in the runtime's durability machinery.  The runtime calls
``run`` each time the agent wakes from SUSPENDED, after folding the EventLog
to reconstruct state.

The author writes normal async code.  Durability is transparent:

    async def run(self, ctx: DurableContextProtocol, inbox: list[Message]) -> None:
        for msg in inbox:
            result = await ctx.tool("summarise", content=msg.payload)  # journaled
            await ctx.emit(self.output_topic, result)                   # journaled effect
        await ctx.sleep_until_signal("new_item")                        # suspend → 0 cost

On crash/resume the coroutine re-runs from the top, but journaled calls return
their cached result instead of re-executing — the LLM call isn't repeated,
the email isn't re-sent.

DurableContextProtocol (kernel-visible slice)
----------------------------------------------
The full ``DurableContext`` lives at L1 (agents/) — it composes Journal,
EventLog, Supervisor, and capability clients.  The kernel only sees the minimal
slice it needs to define the contract: ``run_id``, ``tenant_id``, ``check()``.
The author's actual ctx at runtime IS a ``DurableContext`` and has all the
journaled methods (ctx.llm, ctx.tool, ctx.spawn, ctx.join, etc.).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import Message


class DurableContextProtocol(Protocol):
    """Kernel-visible slice of DurableContext.

    The full ``DurableContext`` (L1) satisfies this protocol and adds the
    journaled capability methods (``ctx.llm()``, ``ctx.tool()``,
    ``ctx.spawn()``, ``ctx.join()``, ``ctx.emit()``, ``ctx.sleep()``, etc.).

    Kernel code (contracts, type signatures) uses ``DurableContextProtocol``.
    Agent authors type-hint with ``DurableContext`` from L1 to get IDE support
    for the full surface.
    """

    run_id: str
    tenant_id: str | None

    def check(self) -> None:
        """Raise ``CancellationError`` if cancelled or deadline exceeded."""
        ...


@runtime_checkable
class DurableAgent(Protocol):
    """Contract every durable agent must satisfy.

    ``id`` — stable routing identity; used by the Inbox and Scheduler to
    address this agent.

    ``run`` — the entry point called each time the agent wakes from SUSPENDED.
    ``inbox`` is the batch of messages drained from the Inbox for this wake
    cycle (may be empty if the wakeup was a timer or signal).

    The agent author's body is a normal async coroutine.  It may:
    - Call ``ctx.llm()``, ``ctx.tool()``, etc. (journaled, at-most-once)
    - Call ``ctx.spawn()``, ``ctx.join()`` to create and await subagents
    - Call ``ctx.emit(topic, msg)`` to publish to followers
    - Call ``ctx.send(agent_id, msg)`` for point-to-point delivery
    - Call ``ctx.sleep_until_signal(name)`` or ``ctx.sleep_until(dt)`` to suspend
    - Call ``ctx.check()`` at cooperative cancellation points

    The function returns ``None`` — the final output (if any) is written to the
    EventLog as the ``run.completed`` entry and surfaced as ``RunResult.output``
    to the parent or the caller of ``Supervisor.join``.
    """

    id: AgentId

    async def run(
        self,
        ctx: DurableContextProtocol,
        inbox: list[Message],
    ) -> None: ...


__all__ = ["DurableContextProtocol", "DurableAgent"]
