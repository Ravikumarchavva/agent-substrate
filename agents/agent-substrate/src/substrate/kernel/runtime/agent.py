"""Agent — the kernel agent contract.

Durability is the baseline — every agent is durable by default.  The
``Agent`` Protocol defines the single entry point ``run(ctx, inbox)``
that the runtime calls each time the agent wakes.

``Agent.run(ctx, inbox)`` receives a batch of messages and an execution
context that wires in the runtime's durability machinery.  The runtime calls
``run`` each time the agent wakes from SUSPENDED, after folding the EventLog
to reconstruct state.

The author writes normal async code.  Durability is transparent:

    async def run(self, ctx: AgentRunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            result = await ctx.tool("summarise", content=msg.payload)  # journaled
            await ctx.emit(self.output_topic, result)                   # journaled effect
        await ctx.sleep_until_signal("new_item")                        # suspend → 0 cost

On crash/resume the coroutine re-runs from the top, but journaled calls return
their cached result instead of re-executing — the LLM call isn't repeated,
the email isn't re-sent.

AgentRunContext (kernel-visible slice)
--------------------------------------
The full ``RunContext`` lives at L1 (agents/) — it composes the EffectCache,
EventLog, Supervisor, and capability clients.  The kernel only sees the minimal
slice it needs to define the contract: ``run_id``, ``tenant_id``, ``check()``.
The author's actual ctx at runtime IS a ``RunContext`` (L1) and has all the
journaled methods (ctx.llm, ctx.tool, ctx.spawn, ctx.join, etc.).

``Agent`` is generic over its context type (``CtxT``, bound to
``AgentRunContext``) precisely so there's one definition, not two. A
consumer that only needs the minimal shape (e.g. ``fabric/evals``) types
against ``Agent[AgentRunContext]``; L1, which needs IDE/type-check support
for the full journaled surface, types against ``Agent[RunContext]`` instead
of redeclaring its own structurally-identical Protocol. Protocol parameter
types are contravariant, so a plain (non-generic) ``Agent`` typed against
``AgentRunContext`` could not be narrowed to ``RunContext`` in place —
making the contract itself generic is what avoids that fork.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import Message


class AgentRunContext(Protocol):
    """Kernel-visible slice of RunContext (L1).

    The full ``RunContext`` (L1) satisfies this protocol and adds the
    journaled capability methods (``ctx.llm()``, ``ctx.tool()``,
    ``ctx.spawn()``, ``ctx.join()``, ``ctx.emit()``, ``ctx.sleep()``, etc.).

    Kernel code (contracts, type signatures) uses ``AgentRunContext``.
    Agent authors type-hint with ``RunContext`` from L1 to get IDE support
    for the full surface.
    """

    run_id: str
    tenant_id: str | None

    def check(self) -> None:
        """Raise ``CancellationError`` if cancelled or deadline exceeded."""
        ...


CtxT = TypeVar("CtxT", bound=AgentRunContext, contravariant=True)
"""``ctx`` only ever appears in an input position (``run``'s parameter), so
``Agent`` is contravariant in it: ``Agent[AgentRunContext]`` (accepts the
widest ctx) is usable wherever ``Agent[RunContext]`` (accepts only the
narrower, richer ctx) is expected — not the other way around."""


@runtime_checkable
class Agent(Protocol[CtxT]):
    """Contract every agent must satisfy.

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

    ``isinstance(x, Agent)`` (bare, unparametrized) still works —
    ``runtime_checkable`` Protocol checks are structural on member names and
    ignore the type parameter, same as before this became generic.
    """

    id: AgentId

    async def run(
        self,
        ctx: CtxT,
        inbox: list[Message],
    ) -> None: ...


__all__ = ["AgentRunContext", "Agent"]
