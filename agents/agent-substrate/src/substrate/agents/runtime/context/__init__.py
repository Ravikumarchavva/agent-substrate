"""RunContext — the L1 journaled execution context for durable agents.

This is what the agent author receives as ``ctx`` in ``agent.run(ctx, inbox)``.

Every capability method is journaled via the EffectCache + Effect system:
- On the **live path**: the real operation runs; the result is appended to
  the EventLog as an ``effect.result`` entry (durable) and cached in memory.
- On the **replay path**: the EffectCache (folded from the EventLog at lease
  time — see ``agents/runtime/effect_cache.py``) returns the cached result;
  the real operation is skipped entirely.

This gives the at-most-once guarantee: even if the worker crashes mid-run
and a new worker replays from the EventLog, effects that already completed
are never re-executed. Unlike a separately-TTL'd journal store, the EventLog
never silently expires an effect out from under a long-suspended run.

Suspension: SuspendInterrupt + replay-from-top
-----------------------------------------------
``ask``, ``sleep_until_signal``, ``sleep_until``, and ``join`` all suspend
the SAME way: consume (a non-blocking, at-most-once claim against the
SignalBus/deadline) fails to find what they're waiting for, so they raise
``SuspendInterrupt`` — a ``BaseException`` that unwinds straight past any
``except Exception`` handler in agent/tool code, out through the Worker.
The Worker catches it, calls ``Scheduler.release(status=SUSPENDED,
wake_on=...)``, and lets the Task end. Nothing is pickled or kept alive:
this is a genuinely dormant run (zero RAM, zero CPU) for both the in-memory
and Postgres backends alike.

Resume works identically for both backends: something fires a signal (or a
deadline/timer passes), the Scheduler flips the run back to ``pending``, any
worker leases it, folds a fresh ``EffectCache`` from the EventLog, and calls
``agent.run()`` again from the top. Every already-completed effect (LLM
calls, tool calls, prior signal consumes) is a cache/consume hit, so replay
fast-forwards silently back to the same wait point — which now succeeds
because the thing it was waiting for has arrived — and the agent's code
continues exactly where it left off, without ever knowing it was
suspended in between.

Capability surface (``ctx.llm()``, ``ctx.tool()``, messaging, spawn/join,
suspension primitives) is split across mixins as submodules of this package
(``journal.py``, ``messaging.py``, ``supervision.py``, ``llm.py``,
``tool.py``) — this ``__init__.py`` holds the ``Agent`` alias,
``RunContext.__init__``, and the properties that don't belong to any one
capability.

``Agent`` here is not its own Protocol — it's ``kernel.runtime.agent.Agent``
(generic over its context type) parametrized with the concrete
``RunContext``, so agent authors get IDE/type-check support for the full
journaled surface (``ctx.llm()``, ``ctx.tool()``, ``ctx.spawn()``, …)
without a second, hand-maintained Protocol declaration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

from substrate.kernel.agent.runtime_context import RunMeta
from substrate.kernel.runtime.agent import Agent as _KernelAgent

from substrate.agents.runtime.effect_cache import EffectCache
from substrate.agents.runtime.context.journal import _JournalMixin
from substrate.agents.runtime.context.messaging import _MessagingMixin
from substrate.agents.runtime.context.supervision import _SupervisionMixin
from substrate.agents.runtime.context.llm import _LLMMixin
from substrate.agents.runtime.context.tool import _ToolMixin

if TYPE_CHECKING:
    from substrate.kernel.runtime.log_entry import EventLog
    from substrate.kernel.runtime.inbox import Inbox
    from substrate.kernel.runtime.scheduler import Scheduler
    from substrate.kernel.runtime.wakeup import SignalBus
    from substrate.kernel.runtime.supervisor import Supervisor
    from substrate.kernel.llm.llm import LLMClient
    from substrate.kernel.runtime.fanout import FanoutStrategy
    from substrate.kernel.runtime.follow_graph import FollowGraph
    from substrate.kernel.storage.blob import BlobStore
    from substrate.agents.tools.invoker import InvokerSession, ToolInvoker


class RunContext(
    _JournalMixin,
    _MessagingMixin,
    _SupervisionMixin,
    _LLMMixin,
    _ToolMixin,
):
    """Journaled execution context — satisfies AgentRunContext (kernel Protocol).

    Created fresh by the Worker for each agent.run() invocation.

    Effect identity uses a hierarchical path, not a flat counter — see
    ``_alloc_path``/``_enter_scope``/``_exit_scope`` (``context/journal.py``).
    This matters because a journal-hit call (e.g. a tool the run already
    executed before a crash) never runs its body again, so any journaled
    calls the body *would* have made (e.g. a suspending tool journaling its
    own ``ctx.uuid()``) are never re-issued on replay. A flat run-wide
    counter would desync from that point on — every subsequent effect_id in
    the run would miss the journal and re-execute (re-billing an LLM call,
    re-sending an email, ...). The hierarchical path avoids this: a
    cache-hit call consumes exactly one index in its *parent's* scope
    regardless of whether its body ran, and nested calls only ever exist
    within their own child scope, which is only entered when the body
    genuinely executes.
    """

    def __init__(
        self,
        *,
        meta: RunMeta,
        event_log: EventLog,
        effect_cache: EffectCache,
        inbox: Inbox,
        follow_graph: FollowGraph,
        fanout: FanoutStrategy,
        scheduler: Scheduler,
        supervisor: Supervisor,
        signal_bus: SignalBus,
        blob_store: BlobStore | None = None,
        llm_client: LLMClient | None = None,
        tool_invoker: ToolInvoker | None = None,
        agent: Agent | None = None,
    ) -> None:
        self.run_id = meta.run_id
        self.tenant_id = meta.tenant_id
        self._meta = meta
        self._event_log = event_log
        self._effect_cache = effect_cache
        self._blob_store = blob_store
        self._inbox = inbox
        self._follow_graph = follow_graph
        self._fanout = fanout
        self._scheduler = scheduler
        self._supervisor = supervisor
        self._signal_bus = signal_bus
        self._path_stack: list[int] = [0]
        # Local seq cursor, seeded from the fold — removes the per-append
        # last_seq() query this used to require, and doubles as zombie-worker
        # fencing: a stale RunContext from a reclaimed lease has a cursor that
        # falls behind the real log the moment any other writer appends, so
        # its next _log() call raises ConcurrentAppendError instead of
        # silently racing.
        self._seq_cursor = effect_cache.last_seq
        self._llm_client = llm_client
        self._tool_invoker = tool_invoker
        self.agent = agent
        self._invoker_session: InvokerSession | None = (
            None  # opened lazily when tool() is first called
        )

    @property
    def meta(self) -> RunMeta:
        """Execution-scoped metadata: deadline, trace_id, supervision, cancellation."""
        return self._meta

    # ------------------------------------------------------------------
    # AgentRunContext surface
    # ------------------------------------------------------------------

    def check(self) -> None:
        """Raise CancellationError if this run has been cancelled or deadline exceeded."""
        self._meta.check()


Agent: TypeAlias = _KernelAgent[RunContext]
"""The one interceptor shape real agents implement — kernel's ``Agent``
Protocol parametrized with the concrete ``RunContext`` instead of the
kernel-minimal ``AgentRunContext``, so ``Runtime``/``Worker`` and agent
authors get IDE/type-check support for the full journaled surface. See
``kernel.runtime.agent.Agent``'s docstring for why this is generic."""


__all__ = ["Agent", "RunContext"]
