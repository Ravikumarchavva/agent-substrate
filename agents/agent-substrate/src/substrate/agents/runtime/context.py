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
"""

from __future__ import annotations

import json
import random as _random
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from substrate.kernel.agent.runtime_context import RunMeta
from substrate.kernel.core.content import (
    ContentBlock,
    JsonObject,
    content_block_from_dict,
)
from substrate.kernel.core.errors import SuspendInterrupt
from substrate.kernel.core.identity import AgentId, TopicId
from substrate.kernel.core.usage import Usage
from substrate.kernel.llm.llm import GenerationOptions, LLMResponse
from substrate.kernel.messaging.message import Message, DataPayload
from substrate.kernel.runtime.communication import AskOutcome, RunStatusSummary
from substrate.kernel.runtime.effects import Effect, EffectResult
from substrate.kernel.runtime.ids import RunId, RunStatus, new_run_id
from substrate.kernel.runtime.log_entry import RunLogEntry
from substrate.kernel.runtime.supervisor import RunHandle, RunResult
from substrate.kernel.runtime.wakeup import Wakeup
from substrate.kernel.agent.supervision import Supervision
from substrate.kernel.tools.chain import InvocationResult

from substrate.agents.runtime.effect_cache import EffectCache

if TYPE_CHECKING:
    from substrate.agents.runtime.backends._event_log import InMemoryEventLog
    from substrate.agents.runtime.backends._inbox import InMemoryInbox
    from substrate.agents.runtime.backends._scheduler import InMemoryScheduler
    from substrate.agents.runtime.backends._signal_bus import InMemorySignalBus
    from substrate.agents.runtime.backends._supervisor import InMemorySupervisor
    from substrate.kernel.llm.llm import LLMClient
    from substrate.kernel.runtime.fanout import FanoutStrategy
    from substrate.kernel.runtime.follow_graph import FollowGraph
    from substrate.kernel.runtime.agent import Agent
    from substrate.kernel.storage.blob import BlobStore
    from substrate.agents.tools.invoker import InvokerSession, ToolInvoker

# Effect results serialized larger than this are offloaded to the BlobStore
# and referenced by ``artifact_ref`` rather than inlined in the EventLog
# entry — keeps large tool/LLM payloads out of the hot append-only log.
_ARTIFACT_OFFLOAD_BYTES = 64 * 1024


class RunContext:
    """Journaled execution context — satisfies AgentRunContext (kernel Protocol).

    Created fresh by the Worker for each agent.run() invocation.

    Effect identity uses a hierarchical path, not a flat counter — see
    ``_alloc_path``/``_enter_scope``/``_exit_scope``. This matters because a
    journal-hit call (e.g. a tool the run already executed before a crash)
    never runs its body again, so any journaled calls the body *would* have
    made (e.g. a suspending tool journaling its own ``ctx.uuid()``) are never
    re-issued on replay. A flat run-wide counter would desync from that point
    on — every subsequent effect_id in the run would miss the journal and
    re-execute (re-billing an LLM call, re-sending an email, ...). The
    hierarchical path avoids this: a cache-hit call consumes exactly one
    index in its *parent's* scope regardless of whether its body ran, and
    nested calls only ever exist within their own child scope, which is only
    entered when the body genuinely executes.
    """

    def __init__(
        self,
        *,
        meta: RunMeta,
        event_log: InMemoryEventLog,
        effect_cache: EffectCache,
        inbox: InMemoryInbox,
        follow_graph: FollowGraph,
        fanout: FanoutStrategy,
        scheduler: InMemoryScheduler,
        supervisor: InMemorySupervisor,
        signal_bus: InMemorySignalBus,
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

    # ------------------------------------------------------------------
    # Hierarchical effect-path allocation (see class docstring for why)
    # ------------------------------------------------------------------

    def _alloc_path(self) -> str:
        """Allocate this call's own path in the current scope.

        Always consumes exactly one index in the current scope — on both the
        live run and every replay — regardless of whether the call turns out
        to be a journal hit or miss. That symmetry is what keeps sibling
        calls after this one aligned between live execution and replay.
        """
        path = ".".join(str(i) for i in self._path_stack)
        self._path_stack[-1] += 1
        return path

    def _enter_scope(self) -> None:
        """Open a child scope for calls made inside a journaled call's body.

        Only call this on the genuine-execution (journal miss) path — a
        cache-hit call must never enter its scope, since its body (and
        anything it would have journaled) does not run.
        """
        self._path_stack.append(0)

    def _exit_scope(self) -> None:
        self._path_stack.pop()

    # ------------------------------------------------------------------
    # Journaled generic effect helper
    # ------------------------------------------------------------------

    async def _journaled(
        self,
        kind: str,
        args: JsonObject,
        fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run fn() with at-most-once semantics via the effect cache.

        ``fn`` may itself make journaled calls — a child scope is opened for
        the duration of its (genuine) execution so any such calls get stable,
        replay-safe paths of their own.
        """
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, kind, args)
        cached = self._lookup_effect(effect_id)
        if cached:
            if cached.status == "error":
                value = await self._resolve_effect_value(cached)
                raise RuntimeError(value.get("error", "journaled error"))
            return await self._resolve_effect_value(cached)
        self._enter_scope()
        try:
            result = await fn()
            await self._record_effect(effect_id, "ok", result or {})
            return result
        except Exception as exc:
            await self._record_effect(effect_id, "error", {"error": str(exc)})
            raise
        finally:
            self._exit_scope()

    async def _log(self, kind: str, payload: JsonObject = {}) -> None:
        seq = self._seq_cursor + 1
        await self._event_log.append(
            self.run_id,
            RunLogEntry(run_id=self.run_id, seq=seq, kind=kind, payload=payload),
            expected_seq=self._seq_cursor,
        )
        self._seq_cursor = seq

    # ------------------------------------------------------------------
    # Effect cache — lookup/record against the EventLog (replaces Journal)
    # ------------------------------------------------------------------

    def _lookup_effect(self, effect_id: str) -> EffectResult | None:
        """Pure in-memory lookup — no I/O. Value may be offloaded; see ``_resolve_effect_value``."""
        return self._effect_cache.lookup(effect_id)

    async def _resolve_effect_value(self, result: EffectResult) -> JsonObject:
        """Dereference an offloaded effect value.  A no-op unless the cached
        result came from a fold() of an entry whose value was too large to
        inline (see ``_record_effect``) — the live-write path always keeps
        the value in memory too, so this only does blob I/O on a genuine
        cross-process replay hitting an offloaded historical effect."""
        if result.artifact_ref and not result.value:
            if self._blob_store is None:
                raise RuntimeError(
                    f"Effect {result.effect_id} references an offloaded artifact "
                    f"({result.artifact_ref}) but no blob_store is configured"
                )
            raw = await self._blob_store.resolve(result.artifact_ref)
            text = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            return json.loads(text)
        return result.value

    async def _record_effect(
        self, effect_id: str, status: str, value: JsonObject
    ) -> None:
        """Append ``effect.result`` to the EventLog (durable, replay source of
        truth) and update the in-run cache.  Large values are offloaded to
        the BlobStore and referenced rather than inlined in the log entry."""
        payload: JsonObject = {"effect_id": effect_id, "status": status}
        artifact_ref: str | None = None
        serialized = json.dumps(value, default=str)
        if (
            self._blob_store is not None
            and len(serialized.encode()) > _ARTIFACT_OFFLOAD_BYTES
        ):
            artifact_ref = await self._blob_store.store(
                serialized, content_type="application/json"
            )
            await self._blob_store.pin(artifact_ref)
            payload["artifact_ref"] = artifact_ref
        else:
            payload["value"] = value
        await self._log("effect.result", payload)
        # Keep the full value in the in-memory cache regardless of offload —
        # we already have it live; only a fresh fold() ever needs to resolve
        # the blob.
        self._effect_cache.put(
            EffectResult(
                effect_id=effect_id,
                status=status,
                value=value,
                artifact_ref=artifact_ref,
            )
        )

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send(self, target: AgentId, msg: Message) -> None:
        """Fire-and-forget delivery.  Does not suspend the caller."""
        await self._inbox.deliver(target, msg)
        await self._scheduler.wake_agent(target)

    async def emit(self, topic: TopicId, msg: Message) -> None:
        """Publish to all followers of ``topic`` (fire-and-forget)."""
        await self._fanout.publish(
            topic, msg, graph=self._follow_graph, inbox=self._inbox
        )

    async def ask(
        self,
        target: AgentId | RunHandle,
        msg: Message,
        *,
        timeout: float,
        idempotency_key: str | None = None,
    ) -> AskOutcome:
        """Send ``msg`` and suspend until a reply, deadline, or target failure.

        Two call shapes, handled differently:

        - ``target`` is a ``RunHandle`` from a PRIOR ``ctx.spawn()``: the
          child is already running, already booted with a message. This call
          only WAITS, using ``handle.boot_correlation_id`` — it does not
          send anything. (Re-sending ``msg`` here — even a copy — would
          collide with the Inbox's idempotent-by-message-id dedup the moment
          the caller reuses the same ``Message`` object for both the
          ``spawn(boot=msg)`` and this call, a natural and common pattern:
          whichever delivery lands second is silently dropped, and if it's
          this one, the correlation_id this wait listens on is never the one
          the child actually replies with.)
        - ``target`` is a plain ``AgentId``: this call both sends and waits.
          The correlation_id is derived from this call's own replay-stable
          path, NOT taken from ``msg.correlation_id`` (unless
          ``idempotency_key`` is given explicitly) — a caller that builds a
          fresh ``Message`` with an auto-generated correlation_id on every
          call to its own ``run()`` would otherwise get a DIFFERENT id on
          every replay attempt, silently defeating both the at-most-once
          send guarantee and the ability to ever find a reply buffered under
          a previous attempt's id.

        The target calls ``ctx.reply(msg, result)`` to complete the ask.  If
        ``target`` is a ``RunHandle``, a supervisor that fires
        ``child:{run_id}`` signals on completion (see
        ``PostgresSupervisor``/``InMemorySupervisor``) makes a
        crashed/cancelled child resolve immediately instead of only after
        the full timeout — this wait watches both names at once.
        """
        self.check()

        target_agent: AgentId = (
            target.agent_id if isinstance(target, RunHandle) else target
        )
        target_run: RunId | None = (
            target.run_id if isinstance(target, RunHandle) else None
        )
        handle = (
            target
            if isinstance(target, RunHandle)
            else RunHandle(
                run_id=target_run or new_run_id(),
                agent_id=target_agent,
                parent_run=self.run_id,
            )
        )

        send_path = self._alloc_path()
        already_booted = isinstance(target, RunHandle) and target.boot_correlation_id
        if already_booted:
            correlation_id = target.boot_correlation_id  # type: ignore[assignment]
        else:
            correlation_id = idempotency_key or f"{self.run_id}.{send_path}"
            enriched = msg.model_copy(
                update={"reply_to": self.run_id, "correlation_id": correlation_id}
            )
            # Journaled send: delivering the ask must happen at most once,
            # even if this run crashes and replays right after sending.
            send_effect_id = Effect.make_id(
                self.run_id, send_path, "ask.send", {"correlation_id": correlation_id}
            )
            if self._lookup_effect(send_effect_id) is None:
                await self._inbox.deliver(target_agent, enriched)
                if target_run:
                    await self._scheduler.wake_suspended(target_run)
                else:
                    await self._scheduler.wake_agent(target_agent)
                await self._log(
                    "ask.sent",
                    {"target": str(target_agent), "correlation_id": correlation_id},
                )
                await self._record_effect(send_effect_id, "ok", {})

        # Deadline is journaled too — frozen at the first attempt, so replay
        # doesn't push it back out every time this wait is re-entered.
        deadline_path = self._alloc_path()
        deadline_effect_id = Effect.make_id(
            self.run_id, deadline_path, "ask.deadline", {"correlation_id": correlation_id}
        )
        deadline = await self._deadline_for(deadline_effect_id, timeout)

        reply_name = f"reply:{correlation_id}"
        child_name = f"child:{target_run}" if target_run else None
        wait_names = [reply_name] + ([child_name] if child_name else [])

        wait_path = self._alloc_path()
        wait_effect_id = Effect.make_id(
            self.run_id, wait_path, "ask.wait", {"correlation_id": correlation_id}
        )

        for name in wait_names:
            payload = await self._signal_bus.consume(
                self.run_id, name, f"{wait_effect_id}:{name}"
            )
            if payload is None:
                continue
            if name == reply_name:
                result = RunResult(
                    run_id=target_run or "",
                    status=RunStatus.COMPLETED,
                    output=DataPayload(data=payload),
                )
                await self._log("ask.replied", {"correlation_id": correlation_id})
                return AskOutcome(kind="replied", result=result, last_seq=0)
            # child_name fired: the supervisor reports how the child ended.
            kind = payload.get("kind", "target_failed")
            await self._log("ask.timeout", {"correlation_id": correlation_id, "kind": kind})
            return AskOutcome(kind=kind, handle=handle, last_seq=-1)

        if datetime.now(tz=timezone.utc) >= deadline:
            await self._log(
                "ask.timeout", {"correlation_id": correlation_id, "kind": "timed_out"}
            )
            return AskOutcome(kind="timed_out", handle=handle, last_seq=-1)

        # Not resolved yet and not past deadline: arrange the wake (needed
        # for Stage 0's real timer; a no-op-beyond-wake_at for Postgres,
        # whose release() below already sets it from the Wakeup) and suspend.
        await self._signal_bus.timer(self.run_id, deadline)
        await self._log("run.suspended", {"waiting_for": wait_names, "deadline": deadline.isoformat()})
        raise SuspendInterrupt(
            self.run_id,
            Wakeup(kind="signal", signals=wait_names, at=deadline),
            reason=f"ask:{correlation_id}",
        )

    async def _deadline_for(self, effect_id: str, timeout: float) -> datetime:
        """Journaled, frozen-at-first-attempt deadline (now + timeout)."""
        cached = self._lookup_effect(effect_id)
        if cached is not None:
            value = await self._resolve_effect_value(cached)
            return datetime.fromisoformat(value["deadline"])
        deadline = datetime.now(tz=timezone.utc) + timedelta(seconds=timeout)
        await self._record_effect(effect_id, "ok", {"deadline": deadline.isoformat()})
        return deadline

    async def reply(self, to: Message, result: JsonObject) -> None:
        """Send a reply to an ``ask``.  Signals the asker's run."""
        if to.reply_to:
            await self._signal_bus.signal(
                to.reply_to,
                f"reply:{to.correlation_id}",
                result,
            )

    async def status(self, handle: RunHandle) -> RunStatusSummary:
        """Opt-in batched peek at a run's progress.  Not a stream."""
        run_status = (
            await self._scheduler.get_status(handle.run_id) or RunStatus.PENDING
        )
        last_seq = await self._event_log.last_seq(handle.run_id)
        last_milestone: str | None = None
        if last_seq >= 0:
            async for entry in self._event_log.read(handle.run_id, from_seq=last_seq):
                last_milestone = entry.kind
        return RunStatusSummary(
            run_id=handle.run_id,
            status=run_status,
            last_seq=last_seq,
            last_milestone=last_milestone,
        )

    # ------------------------------------------------------------------
    # Lifecycle — spawn / cancel
    # ------------------------------------------------------------------

    async def spawn(
        self,
        child_agent: AgentId,
        *,
        boot: Message,
        supervision: Supervision | None = None,
    ) -> RunHandle:
        """Spawn a child run.  Returns a handle; does NOT wait for completion."""
        self.check()
        sup = supervision or Supervision.root(child_agent)
        # The spawn effect's identity, and the boot message's correlation_id,
        # must come from OUR OWN replay-stable path allocation — never from
        # anything the Supervisor computes fresh (e.g. the parent log's
        # current last_seq) or from boot.id/boot.correlation_id (agent
        # authors routinely construct a fresh Message, with fresh
        # auto-generated ids, on every call to their own run()). Any of
        # those would drift across replay attempts: the first defeats
        # "replaying returns the same child_run_id", the second means a
        # later ctx.ask(handle, ...) can never find the reply it's waiting
        # for (see that method's docstring for the full trace).
        path = self._alloc_path()
        correlation_id = f"{self.run_id}.{path}"
        handle = await self._supervisor.spawn(
            child_agent,
            parent=self.run_id,
            supervision=sup,
            boot=boot,
            path=path,
            correlation_id=correlation_id,
        )
        # Supervisor.spawn() appends a "child.spawned" entry directly to this
        # run's own EventLog (bypassing ctx._log — it has no ctx reference,
        # only the shared event_log), so the local seq cursor must be
        # resynced here or the next ctx._log() call would see a stale
        # expected_seq and raise ConcurrentAppendError.
        self._seq_cursor = await self._event_log.last_seq(self.run_id)
        return handle

    async def cancel(self, handle: RunHandle, *, reason: str = "cancelled") -> None:
        """Cancel a child run and its entire subtree."""
        await self._supervisor.cancel(handle, reason=reason)

    async def join(self, handle: RunHandle) -> RunResult:
        """Suspend the parent until the child run reaches a terminal state.

        Non-blocking claim, not a wait (same model as ``ask`` and
        ``sleep_until_signal``): the Supervisor's ``finish_run`` fires a
        ``child:{run_id}`` signal when the child reaches a terminal state,
        so this consumes that signal — a miss raises ``SuspendInterrupt``
        rather than parking on ``Supervisor.join()`` (which blocks a live
        coroutine and would not survive a process restart).
        """
        self.check()
        child_run = handle.run_id
        signal_name = f"child:{child_run}"
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "join.wait", {"child_run": child_run})
        payload = await self._signal_bus.consume(self.run_id, signal_name, effect_id)
        if payload is not None:
            status = RunStatus(payload["status"])
            await self._log("join.completed", {"child_run": child_run, "status": status.value})
            return RunResult(run_id=child_run, status=status, error=payload.get("error"))
        await self._log("run.suspended", {"waiting_for": signal_name})
        raise SuspendInterrupt(
            self.run_id,
            Wakeup(kind="signal", signals=[signal_name]),
            reason=f"join:{child_run}",
        )

    # ------------------------------------------------------------------
    # Suspension primitives
    # ------------------------------------------------------------------

    async def sleep_until_signal(self, name: str) -> JsonObject:
        """Suspend until a named signal arrives on this run.

        Non-blocking claim, not a wait: a miss raises ``SuspendInterrupt``
        (see module docstring) rather than parking a coroutine. The
        ``effect_id`` is deterministic (from this call's hierarchical path),
        so a replay that reaches this same wait re-claims the SAME payload
        it already consumed — or, if nothing has arrived yet, misses again
        and re-suspends, identically to the first attempt.
        """
        self.check()
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "signal.wait", {"name": name})
        payload = await self._signal_bus.consume(self.run_id, name, effect_id)
        if payload is not None:
            await self._log("run.resumed", {"signal": name})
            return payload
        await self._log("run.suspended", {"waiting_for": name})
        raise SuspendInterrupt(
            self.run_id,
            Wakeup(kind="signal", signals=[name]),
            reason=f"sleep_until_signal:{name}",
        )

    async def sleep_until(self, dt: datetime) -> None:
        """Suspend until a wall-clock time.

        Deliberately re-checks the REAL clock (not the journaled ``ctx.now()``
        helper) on every attempt, live or replayed — the whole point of this
        wait is to observe actual wall-clock progress across suspensions;
        journaling it would freeze the check at whatever time the first
        attempt happened and it would never appear to have arrived.
        """
        self.check()
        if datetime.now(tz=timezone.utc) >= dt:
            await self._log("run.resumed", {"via": "timer"})
            return
        await self._signal_bus.timer(self.run_id, dt)
        await self._log("run.suspended", {"until": dt.isoformat()})
        raise SuspendInterrupt(
            self.run_id, Wakeup(kind="timer", at=dt), reason=f"sleep_until:{dt.isoformat()}"
        )

    # ------------------------------------------------------------------
    # Social graph
    # ------------------------------------------------------------------

    async def follow(self, topic: TopicId) -> None:
        """Subscribe this agent to a topic."""
        agent_id = AgentId(type="run", key=self.run_id)
        await self._follow_graph.follow(agent_id, topic)

    async def unfollow(self, topic: TopicId) -> None:
        from substrate.kernel.messaging.message import Subscription

        agent_id = AgentId(type="run", key=self.run_id)
        sub = Subscription(topic=topic, agent_id=agent_id)
        await self._follow_graph.unfollow(sub)

    # ------------------------------------------------------------------
    # LLM capability (journaled — replay never re-bills)
    # ------------------------------------------------------------------

    async def llm(
        self,
        messages: list,
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        """Journaled LLM call.  Replay returns cached response; never re-bills."""
        if self._llm_client is None:
            raise RuntimeError(
                "No LLM client injected into this context.  "
                "Set agent.model before registering with the runtime."
            )

        def _serialize(resp: LLMResponse) -> JsonObject:
            return {
                "content": [b.model_dump(mode="json") for b in resp.content],
                "usage": {
                    "input_tokens": resp.usage.input_tokens,
                    "cached_tokens": resp.usage.cached_tokens,
                    "output_tokens": resp.usage.output_tokens,
                    "reasoning_tokens": resp.usage.reasoning_tokens,
                },
            }

        def _deserialize(v: JsonObject) -> LLMResponse:
            # LLM responses only ever carry known ContentBlock variants; the
            # UnknownBlock fallback from content_block_from_dict never appears here.
            blocks: list[ContentBlock] = [
                content_block_from_dict(d)  # type: ignore[misc]
                for d in v["content"]  # type: ignore[union-attr]
            ]
            u = v["usage"]  # type: ignore[index]
            usage = Usage(
                input_tokens=u["input_tokens"],
                cached_tokens=u["cached_tokens"],
                output_tokens=u["output_tokens"],
                reasoning_tokens=u["reasoning_tokens"],
            )
            return LLMResponse(content=blocks, usage=usage)

        args: JsonObject = {"model": self._llm_client.model, "msg_count": len(messages)}
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "llm", args)
        cached = self._lookup_effect(effect_id)
        if cached:
            if cached.status == "error":
                value = await self._resolve_effect_value(cached)
                raise RuntimeError(value.get("error", "journaled llm error"))
            return _deserialize(await self._resolve_effect_value(cached))
        try:
            from substrate.kernel.messaging.stream import (
                TextDelta,
                ReasoningDelta,
                CompletionEvent,
            )
            from substrate.kernel.core.content import TextBlock

            text_chunks: list[str] = []
            reasoning_chunks: list[str] = []
            final_content: list[ContentBlock] | None = None
            final_usage: Usage | None = None

            try:
                stream = self._llm_client.generate_stream(
                    messages, options=options, ctx=self._meta
                )
            except TypeError:
                stream = self._llm_client.generate_stream(messages, options=options)

            async for chunk in stream:
                if isinstance(chunk, TextDelta):
                    text_chunks.append(chunk.text)
                    await self._log("text.delta", {"text": chunk.text})
                elif isinstance(chunk, ReasoningDelta):
                    reasoning_chunks.append(chunk.text)
                    await self._log("reasoning.delta", {"text": chunk.text})
                elif isinstance(chunk, CompletionEvent):
                    final_content = chunk.content
                    final_usage = chunk.usage

            if final_content is None:
                text_str = "".join(text_chunks)
                final_content = [TextBlock(text=text_str)]
            if final_usage is None:
                final_usage = Usage()

            resp = LLMResponse(content=final_content, usage=final_usage)

            await self._record_effect(effect_id, "ok", _serialize(resp))
            await self._log(
                "llm.call",
                {"model": self._llm_client.model, "tokens": resp.usage.total_tokens},
            )
            return resp
        except Exception as exc:
            await self._record_effect(effect_id, "error", {"error": str(exc)})
            raise

    # ------------------------------------------------------------------
    # Tool capability (journaled — at-most-once side effects)
    # ------------------------------------------------------------------

    async def tool(self, name: str, **args: Any) -> InvocationResult:
        """Journaled tool call via ToolInvoker.  At-most-once: won't re-execute on replay."""
        from substrate.kernel.tools import ToolCallRequest

        if self._tool_invoker is None:
            raise RuntimeError(
                "No ToolInvoker injected into this context.  "
                "Set agent.tools before registering with the runtime."
            )
        if self._invoker_session is None:
            self._invoker_session = self._tool_invoker.open_session()

        call = ToolCallRequest(name=name, arguments=args)
        effect_args: JsonObject = {"name": name, "args_keys": sorted(args.keys())}
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "tool", effect_args)
        cached = self._lookup_effect(effect_id)
        if cached:
            from substrate.kernel.tools.chain import InvocationResult

            cached_value = await self._resolve_effect_value(cached)
            if cached.status == "error":
                # Soft error: tool returned InvocationResult(status="error"). Return it so
                # the LLM sees the same result on replay as it did on the first run.
                # Hard error: tool raised an exception, value is {"error": "..."}. Re-raise.
                try:
                    return InvocationResult.model_validate(cached_value)
                except Exception:
                    raise RuntimeError(
                        cached_value.get("error", "journaled tool error")
                    )
            return InvocationResult.model_validate(cached_value)
        # Journal miss: the tool body genuinely executes, so open a child
        # scope — any journaled calls it makes (e.g. a suspending tool
        # journaling its own ctx.uuid() for a replay-stable id) get paths
        # nested under this call's path rather than colliding with siblings.
        self._enter_scope()
        try:
            await self._log(
                "tool.call",
                {"call_id": effect_id, "tool_name": name, "args": args},
            )
            result = await self._tool_invoker.invoke(
                call, session=self._invoker_session, ctx=self
            )
            await self._record_effect(
                effect_id,
                "ok" if result.status == "ok" else "error",
                result.model_dump(mode="json"),
            )
            ok = result.status == "ok"
            await self._log(
                "tool.result",
                {
                    "call_id": effect_id,
                    "tool_name": name,
                    "ok": ok,
                    "output": result.text or "",
                    "error": None if ok else (result.text or "tool error"),
                    "structured_content": result.structured or {},
                },
            )
            return result
        except Exception as exc:
            await self._record_effect(effect_id, "error", {"error": str(exc)})
            raise
        finally:
            self._exit_scope()

    # ------------------------------------------------------------------
    # Deterministic helpers (for replay safety)
    # ------------------------------------------------------------------

    async def now(self) -> datetime:  # type: ignore[override]
        """Journaled wall-clock — use this instead of datetime.now()."""
        args: JsonObject = {}
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "now", args)
        cached = self._lookup_effect(effect_id)
        if cached and cached.status == "ok":
            value = await self._resolve_effect_value(cached)
            return datetime.fromisoformat(value["ts"])
        ts = datetime.now(tz=timezone.utc)
        await self._record_effect(effect_id, "ok", {"ts": ts.isoformat()})
        return ts

    async def random(self) -> float:
        """Journaled random float — use this instead of random.random()."""
        args: JsonObject = {}
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "random", args)
        cached = self._lookup_effect(effect_id)
        if cached and cached.status == "ok":
            value = await self._resolve_effect_value(cached)
            return float(value["value"])
        result = _random.random()
        await self._record_effect(effect_id, "ok", {"value": result})
        return result

    async def uuid(self) -> str:
        """Journaled UUID — use this instead of uuid.uuid4()."""
        args: JsonObject = {}
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "uuid", args)
        cached = self._lookup_effect(effect_id)
        if cached and cached.status == "ok":
            value = await self._resolve_effect_value(cached)
            return str(value["value"])
        result = _uuid.uuid4().hex
        await self._record_effect(effect_id, "ok", {"value": result})
        return result
