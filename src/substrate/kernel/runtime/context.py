"""RunContext — what an agent holds while it runs.

An agent is ordinary async code that may be stopped at any ``await`` and replayed
from the top. ``ctx`` is how that stays safe: every capability here either answers
from the run's journal or does its work exactly once and records it. The agent
author writes straight-line code and never sees the machinery.

* ``llm``, ``tool`` — journaled calls out to the world.
* ``spawn``, ``join``, ``ask``, ``reply``, ``send``, ``emit`` — talking to other
  agents; each is one atomic commit, so a send and its record cannot come apart.
* ``sleep_until_signal``, ``sleep_until`` — dormancy. A wait that is not yet
  satisfied raises ``SuspendInterrupt``; the worker parks the run and replays it on
  wakeup, when the wait finds what it was waiting for.
* ``now``, ``uuid``, ``random`` — the non-deterministic inputs, journaled so a replay
  sees the values the first run saw.
* ``log``, ``log_once`` — entries in the run's record.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from substrate.kernel.abstractions.agent.runtime_context import RunMeta, RunScope
from substrate.kernel.abstractions.agent.supervision import Supervision
from substrate.kernel.abstractions.core.content import (
    ChatMessage,
    JsonObject,
    MediaBlock,
    TextBlock,
    parse_content_block,
)
from substrate.kernel.abstractions.core.identity import Actor, Topic
from substrate.kernel.abstractions.core.trace import TraceContext
from substrate.kernel.abstractions.core.usage import Usage
from substrate.kernel.abstractions.exceptions import ControlSignal, SuspendInterrupt
from substrate.kernel.abstractions.ids import new_id, new_run_id
from substrate.kernel.abstractions.llm.llm import FinishReason, GenerationOptions, LLMClient, LLMResponse
from substrate.kernel.abstractions.messaging.message import DataPayload, Message
from substrate.kernel.abstractions.messaging.stream import CompletionEvent, ReasoningDelta, TextDelta
from substrate.kernel.abstractions.runtime.communication import AskOutcome, RunStatusSummary
from substrate.kernel.abstractions.runtime.ids import RunId, RunStatus
from substrate.kernel.abstractions.runtime.log_entry import RunLogKind
from substrate.kernel.abstractions.runtime.store import (
    Commit,
    CommitResult,
    Delivery,
    Lease,
    NewEntry,
    RunSpec,
    RuntimeStore,
    SpawnSpec,
)
from substrate.kernel.abstractions.runtime.agent import Agent as _KernelAgent
from substrate.kernel.abstractions.runtime.supervisor import RunHandle, RunResult
from substrate.kernel.abstractions.runtime.wakeup import Wakeup
from substrate.kernel.abstractions.tools.chain import InvocationResult
from substrate.kernel.runtime.journal import Journal, current_idempotency_key
from substrate.kernel.telemetry import instruments, semconv, span
from substrate.logger import setup_logging

if TYPE_CHECKING:
    from substrate.kernel.tools.invoker import InvokerSession, ToolInvoker

logger = setup_logging()

# Live tokens are appended to the run's record in batches, not one row each.
_STREAM_FLUSH_CHARS = 96
_STREAM_FLUSH_S = 0.05

# A bare scheme, not a path: the backend names the resource and the frontend
# resolves it to wherever its authenticated proxy lives. Never a presigned URL — this
# record is replayed months later and an expired link would leave dead images.
_OBJECT_URL_TEMPLATE = "object:{key}"


def _attachment_url(img: MediaBlock) -> str | None:
    """A durable reference for the UI: the file-store key, else the image's own URL,
    else the bytes inline. ``None`` for a provider ``file_id`` with no bytes."""
    if img.storage_key:
        return _OBJECT_URL_TEMPLATE.format(key=img.storage_key)
    if img.url:
        return img.url
    if img.data is None:
        return None
    return f"data:{img.media_type or 'image/png'};base64,{base64.b64encode(img.data).decode()}"


def _is_idempotent(invoker: Any, name: str) -> bool:
    """Whether the named tool declares itself safe to run twice."""
    tool = invoker.registry.get(name) if hasattr(invoker, "registry") else None
    return bool(getattr(tool, "idempotent", False))


class RunContext:
    """The journaled capability handle for one lease of one run."""

    def __init__(
        self,
        *,
        meta: RunMeta,
        lease: Lease,
        store: RuntimeStore,
        journal: Journal,
        blob_store: Any | None = None,
        llm_client: LLMClient | None = None,
        tool_invoker: ToolInvoker | None = None,
        agent: Any | None = None,
    ) -> None:
        self.run_id = meta.run_id
        self.tenant_id = meta.tenant_id
        self._meta = meta
        self._lease = lease
        self._store = store
        self._journal = journal
        self._blob_store = blob_store
        self._llm_client = llm_client
        self._tool_invoker = tool_invoker
        self.agent = agent
        self._invoker_session: InvokerSession | None = None
        self._latest_workspace_snapshot_id: str | None = None

    # ------------------------------------------------------------------ identity

    @property
    def meta(self) -> RunMeta:
        return self._meta

    @property
    def scope(self) -> RunScope:
        """Whose work the message currently being handled is."""
        return self._meta.scope

    def set_scope(self, scope: RunScope) -> None:
        self._meta = replace(self._meta, scope=scope)

    @property
    def idempotency_key(self) -> str | None:
        """Stable across every attempt at the journaled step now executing; a tool
        passes it to the system it calls so that system can dedupe."""
        return current_idempotency_key()

    @property
    def latest_workspace_snapshot_id(self) -> str | None:
        return self._latest_workspace_snapshot_id

    def record_workspace_snapshot(self, snapshot_id: str) -> None:
        """A tool that committed a workspace snapshot this turn reports it here."""
        self._latest_workspace_snapshot_id = snapshot_id

    def check(self) -> None:
        """Raise ``CancellationError`` if the run was cancelled or its deadline passed."""
        self._meta.check()

    def _trace_child(self) -> TraceContext | None:
        return self._meta.trace.child() if self._meta.trace else None

    # ------------------------------------------------------------------ the record

    async def _commit(self, entries: Sequence[NewEntry] = (), **parts: Any) -> CommitResult:
        return await self._store.commit(self._lease, Commit(entries=tuple(entries), **parts))

    async def log(self, kind: str, payload: JsonObject | None = None) -> int:
        """Append an entry to this run's record. Not deduplicated: use ``log_once``
        for anything written from code that replays."""
        result = await self._commit([NewEntry(kind=kind, payload=payload or {})])
        return result.seqs[0]

    async def log_once(self, kind: str, payload: JsonObject | None = None) -> int:
        """Append an entry at most once across all replays.

        Needed for any entry written from a body that re-executes — a tool that
        suspends internally re-runs on every resume, and a plain ``log`` would
        duplicate the entry (and the UI card built from it) each time.
        """
        path = self._journal.alloc_path()
        result = await self._commit([NewEntry(kind=kind, payload=payload or {}, dedup_key=f"once:{path}:{kind}")])
        return result.seqs[0]

    async def _live(self, entries: Sequence[NewEntry]) -> None:
        """Live output: visible to a tail, never part of replay state. Losing some is
        harmless, so a store blip here must not fail the call that produced it."""
        try:
            await self._store.append_ephemeral(self._lease, entries)
        except ControlSignal:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("live output dropped for run %s", self.run_id, exc_info=True)

    # ------------------------------------------------------------------ deterministic inputs

    async def now(self) -> datetime:
        """Wall-clock time, journaled: a replay sees the time the first run saw."""

        def build(_p: str, _e: str):
            ts = datetime.now(tz=timezone.utc)
            return {"ts": ts.isoformat()}, lambda entries: self._commit(entries)

        outcome = await self._journal.record_atomic("now", {}, build)
        return datetime.fromisoformat(outcome.value["ts"])

    async def random(self) -> float:
        import random as _random

        def build(_p: str, _e: str):
            return {"value": _random.random()}, lambda entries: self._commit(entries)

        return float((await self._journal.record_atomic("random", {}, build)).value["value"])

    async def uuid(self) -> str:
        def build(_p: str, _e: str):
            return {"value": new_id()}, lambda entries: self._commit(entries)

        return str((await self._journal.record_atomic("uuid", {}, build)).value["value"])

    # ------------------------------------------------------------------ LLM

    async def llm(self, messages: list[ChatMessage], *, options: GenerationOptions = GenerationOptions()) -> LLMResponse:  # noqa: B008
        """A journaled model call. A replay returns the recorded response and never re-bills."""
        client = self._llm_client
        if client is None:
            raise RuntimeError("no LLM client: set agent.model before registering the agent")

        async def run() -> JsonObject:
            return _serialize(await self._generate(client, messages, options))

        outcome = await self._journal.effect(
            "llm", {"model": client.model, "msg_count": len(messages)}, run, idempotent=True
        )
        response = _deserialize(outcome.value)
        if not outcome.replayed:
            await self._commit(
                [
                    NewEntry(
                        kind=RunLogKind.LLM_CALL,
                        payload={
                            "model": client.model,
                            "tokens": response.usage.total_tokens,
                            "cost_usd": response.cost_usd,
                            "finish_reason": response.finish_reason.value,
                        },
                        dedup_key=f"llm.call:{outcome.effect_id}",
                    )
                ]
            )
        return response

    async def _generate(self, client: LLMClient, messages: list[ChatMessage], options: GenerationOptions) -> LLMResponse:
        from substrate.kernel.llm.errors import classify_llm_error

        started = time.monotonic()
        attributes = {
            semconv.GEN_AI_OPERATION: "chat",
            semconv.GEN_AI_REQUEST_MODEL: client.model,
            semconv.GEN_AI_AGENT_NAME: str(self.agent.id) if self.agent else "",
        }
        with span(semconv.SPAN_LLM, attributes=attributes) as handle:
            try:
                response = await self._through_middleware(client, messages, options)
            except Exception as exc:
                classified = classify_llm_error(exc)
                instruments().llm_errors.add(
                    1, {semconv.GEN_AI_REQUEST_MODEL: client.model, semconv.ERROR_CODE: getattr(classified, "code", "error")}
                )
                if classified is exc:
                    raise
                raise classified from exc
            elapsed = time.monotonic() - started
            labels = {semconv.GEN_AI_REQUEST_MODEL: client.model}
            handle.set_attributes(
                {
                    semconv.GEN_AI_INPUT_TOKENS: response.usage.input_tokens,
                    semconv.GEN_AI_OUTPUT_TOKENS: response.usage.output_tokens,
                    semconv.GEN_AI_FINISH_REASONS: [response.finish_reason.value],
                    semconv.GEN_AI_RESPONSE_ID: response.response_id,
                    semconv.GEN_AI_RESPONSE_MODEL: response.served_model,
                    semconv.LLM_COST_USD: response.cost_usd,
                }
            )
            instruments().llm_duration.record(elapsed, labels)
            instruments().llm_tokens.record(response.usage.input_tokens, {**labels, semconv.GEN_AI_TOKEN_TYPE: "input"})
            instruments().llm_tokens.record(response.usage.output_tokens, {**labels, semconv.GEN_AI_TOKEN_TYPE: "output"})
            if response.cost_usd:
                instruments().llm_cost.add(response.cost_usd, labels)
            return response

    async def _through_middleware(
        self, client: LLMClient, messages: list[ChatMessage], options: GenerationOptions
    ) -> LLMResponse:
        middleware = getattr(self.agent, "middleware", None)
        if middleware is None:
            return await self._stream(client, messages, options)
        from substrate.kernel.abstractions.agent.middleware import MiddlewareStage
        from substrate.kernel.middleware._contracts import MiddlewareContext

        chat_ctx = MiddlewareContext(
            stage=MiddlewareStage.CHAT,
            agent_name=str(self.agent.id) if self.agent else "unknown",
            run_id=self.run_id,
            messages=messages,
            system_instructions=options.system_instructions or "",
            tools=options.tools,
        )

        async def final(c: MiddlewareContext) -> None:
            c.chat_result = await self._stream(client, c.messages or messages, options)

        await middleware.execute(chat_ctx, final)
        if chat_ctx.chat_result is None:
            raise RuntimeError("the middleware pipeline finished without producing a chat result")
        return chat_ctx.chat_result

    async def _stream(self, client: LLMClient, messages: list[ChatMessage], options: GenerationOptions) -> LLMResponse:
        """Consume the client's stream, publishing live tokens in batches."""
        text: list[str] = []
        reasoning: list[str] = []
        pending: list[NewEntry] = []
        buffered = {RunLogKind.TEXT_DELTA: "", RunLogKind.REASONING_DELTA: ""}
        last_flush = time.monotonic()
        done: CompletionEvent | None = None

        async def flush() -> None:
            nonlocal last_flush
            for kind, chunk in buffered.items():
                if chunk:
                    pending.append(NewEntry(kind=kind, payload={"text": chunk}))
                    buffered[kind] = ""
            if pending:
                batch, pending[:] = list(pending), []
                await self._live(batch)
            last_flush = time.monotonic()

        async for event in client.generate_stream(messages, options=options, ctx=self._meta):
            if isinstance(event, TextDelta):
                text.append(event.text)
                buffered[RunLogKind.TEXT_DELTA] += event.text
            elif isinstance(event, ReasoningDelta):
                reasoning.append(event.text)
                buffered[RunLogKind.REASONING_DELTA] += event.text
            elif isinstance(event, CompletionEvent):
                done = event
            size = sum(len(v) for v in buffered.values())
            if size >= _STREAM_FLUSH_CHARS or (size and time.monotonic() - last_flush >= _STREAM_FLUSH_S):
                await flush()
        await flush()

        usage = done.usage if done else Usage()
        return LLMResponse(
            content=list(done.content) if done else [TextBlock(text="".join(text))],
            usage=usage,
            cost_usd=client.capabilities.cost_usd(usage),
            finish_reason=done.finish_reason if done else FinishReason.UNSPECIFIED,
            response_id=done.response_id if done else None,
            served_model=done.served_model if done else None,
        )

    # ------------------------------------------------------------------ tools

    async def tool(self, name: str, args: dict[str, Any] | None = None) -> InvocationResult:
        """A journaled tool call.

        ``args`` is its own dict, not ``**kwargs``: it is whatever the model supplied
        for the tool's schema, and splatting an untrusted dict into this method's own
        keywords collides the first time a schema names an argument ``name``.
        """
        from substrate.kernel.abstractions.tools import ToolCallRequest

        invoker = self._tool_invoker
        if invoker is None:
            raise RuntimeError("no ToolInvoker: set agent.tools before registering the agent")
        if self._invoker_session is None:
            self._invoker_session = invoker.open_session()
        session = self._invoker_session
        args = args or {}
        call = ToolCallRequest(name=name, arguments=args)

        async def run() -> JsonObject:
            effect_id = current_idempotency_key()
            await self._commit(
                [
                    NewEntry(
                        kind=RunLogKind.TOOL_CALL,
                        payload={"call_id": effect_id, "tool_name": name, "args": args},
                        dedup_key=f"tool.call:{effect_id}",
                    )
                ]
            )
            started = time.monotonic()
            with span(
                semconv.SPAN_TOOL,
                attributes={semconv.GEN_AI_TOOL_NAME: name, semconv.GEN_AI_OPERATION: "execute_tool"},
            ) as handle:
                result = await self._invoke_through_middleware(invoker, call, session)
                outcome = "ok" if result.status == "ok" else result.status
                handle.set_attribute(semconv.TOOL_OUTCOME, outcome)
            labels = {semconv.GEN_AI_TOOL_NAME: name, semconv.TOOL_OUTCOME: outcome}
            instruments().tool_calls.add(1, labels)
            instruments().tool_duration.record(time.monotonic() - started, labels)
            return result.model_dump(mode="json")

        outcome = await self._journal.effect(
            "tool", {"name": name, "args": args}, run, idempotent=_is_idempotent(invoker, name)
        )
        result = InvocationResult.model_validate(outcome.value)
        await self._log_tool_result(outcome.effect_id, name, result)
        return result

    async def _invoke_through_middleware(self, invoker: Any, call: Any, session: Any) -> InvocationResult:
        async def invoke() -> InvocationResult:
            return await invoker.invoke(call, session=session, ctx=cast("RunContext", self))

        middleware = getattr(self.agent, "middleware", None)
        if middleware is None:
            return await invoke()
        from substrate.kernel.abstractions.agent.middleware import MiddlewareStage
        from substrate.kernel.middleware._contracts import MiddlewareContext

        func_ctx = MiddlewareContext(
            stage=MiddlewareStage.TOOL,
            agent_name=str(self.agent.id) if self.agent else "unknown",
            run_id=self.run_id,
            function_name=call.name,
            arguments=call.arguments,
        )

        async def final(c: MiddlewareContext) -> None:
            c.tool_result = await invoke()

        await middleware.execute(func_ctx, final)
        if func_ctx.tool_result is None:
            raise RuntimeError("the middleware pipeline finished without producing a tool result")
        return func_ctx.tool_result

    async def _log_tool_result(self, effect_id: str, name: str, result: InvocationResult) -> None:
        """Write the UI-facing ``tool.result`` entry — on replay too, so it exists
        even if the worker died between recording the step and writing this."""
        ok = result.status == "ok"
        # Images that came from the file store carry their key, so the entry points at a
        # durable endpoint instead of embedding bytes; this log is permanent and
        # inlining stored every image again, base64-inflated, on every call.
        attachments = [
            {
                "id": new_id(),
                "name": f"{name}-{i}.{(img.media_type or 'image/png').split('/')[-1]}",
                "mime": img.media_type or "image/png",
                "size": len(img.data or b""),
                "url": url,
            }
            for i, img in enumerate(result.media)
            if (url := _attachment_url(img)) is not None
        ]
        await self._commit(
            [
                NewEntry(
                    kind=RunLogKind.TOOL_RESULT,
                    payload={
                        "call_id": effect_id,
                        "tool_name": name,
                        "ok": ok,
                        "output": result.text or "",
                        "error": None if ok else (result.text or "tool error"),
                        "structured_content": result.structured or {},
                        "attachments": attachments,
                    },
                    dedup_key=f"tool.result:{effect_id}",
                )
            ]
        )

    async def tool_batch(self, calls: list[tuple[str, dict[str, Any]]]) -> list[InvocationResult]:
        """Run several tool calls concurrently; results in call order.

        Each call is journaled under the path it would have had run one after another,
        so a replay finds each at its own place. Every call runs to completion even if
        another raises or suspends — a finished sibling is recorded, so a resume replays
        it instead of running it again — and only then is the first error, or failing
        that the first suspension, raised.
        """
        if len(calls) <= 1:
            return [await self.tool(name, args) for name, args in calls]
        stacks = self._journal.fork_scopes(len(calls))
        outcomes = await asyncio.gather(
            *(self._journal.in_scope(stack, lambda n=name, a=args: self.tool(n, a)) for stack, (name, args) in zip(stacks, calls)),
            return_exceptions=True,
        )
        errors = [o for o in outcomes if isinstance(o, BaseException)]
        if errors:
            raise next((e for e in errors if isinstance(e, Exception)), errors[0])
        return cast("list[InvocationResult]", outcomes)

    # ------------------------------------------------------------------ messaging

    async def send(self, target: Actor, msg: Message) -> None:
        """Fire-and-forget delivery; never suspends the caller."""
        await self._commit(deliveries=(Delivery(agent=target, msg=msg, tenant=self.tenant_id or "default"),))

    async def emit(self, topic: Topic, msg: Message) -> None:
        """Publish to every follower of ``topic``."""
        followers = await self._store.followers_of(topic)
        if followers:
            await self._commit(deliveries=tuple(Delivery(agent=f, msg=msg, tenant=self.tenant_id or "default") for f in followers))

    async def reply(self, to: Message, result: JsonObject) -> None:
        """Answer an ``ask``: signals the asker's run."""
        if to.reply_to:
            await self._store.signal(RunId(to.reply_to), f"reply:{to.correlation_id}", result)

    async def ask(
        self, target: Actor | RunHandle, msg: Message, *, timeout: float, idempotency_key: str | None = None
    ) -> AskOutcome:
        """Send ``msg`` and suspend until a reply, the deadline, or the target's end.

        A ``RunHandle`` from an earlier ``spawn`` is only *waited on*: the child was
        already booted, and sending ``msg`` again would collide with the inbox's
        dedup by message id whenever the caller reuses one ``Message`` for both. A
        plain ``Actor`` is both sent to and waited on; its correlation id comes from
        this call's own path rather than ``msg.correlation_id``, which an agent that
        builds a fresh message every time would get differently on every replay.
        """
        self.check()
        target_agent = target.agent_id if isinstance(target, RunHandle) else target
        target_run = target.run_id if isinstance(target, RunHandle) else None
        handle = target if isinstance(target, RunHandle) else RunHandle(run_id=target_run or new_run_id(), agent_id=target_agent, parent_run=RunId(self.run_id))

        if isinstance(target, RunHandle) and target.boot_correlation_id:
            correlation_id = target.boot_correlation_id
        else:
            correlation_id = idempotency_key or f"{self.run_id}.{self._journal.peek_path()}"
            enriched = msg.model_copy(update={"reply_to": self.run_id, "correlation_id": correlation_id})

            def build_send(_p: str, _e: str):
                return {}, lambda entries: self._commit(
                    [*entries, NewEntry(kind="ask.sent", payload={"target": str(target_agent), "correlation_id": correlation_id}, dedup_key=f"ask.sent:{correlation_id}")],
                    deliveries=(Delivery(agent=target_agent, msg=enriched, tenant=self.tenant_id or "default"),),
                )

            await self._journal.record_atomic("ask.send", {"correlation_id": correlation_id}, build_send)

        def build_deadline(_p: str, _e: str):
            deadline = datetime.now(tz=timezone.utc) + timedelta(seconds=timeout)
            return {"deadline": deadline.isoformat()}, lambda entries: self._commit(entries)

        deadline = datetime.fromisoformat(
            (await self._journal.record_atomic("ask.deadline", {"correlation_id": correlation_id}, build_deadline)).value["deadline"]
        )

        reply_name = f"reply:{correlation_id}"
        names = [reply_name] + ([f"child:{target_run}"] if target_run else [])
        _path, wait_id, _ = self._journal.peek("ask.wait", {"correlation_id": correlation_id})
        for name in names:
            payload = await self._store.consume(RunId(self.run_id), name, f"{wait_id}:{name}")
            if payload is None:
                continue
            if name == reply_name:
                await self._commit([NewEntry(kind="ask.replied", payload={"correlation_id": correlation_id}, dedup_key=f"ask.replied:{correlation_id}")])
                return AskOutcome(
                    kind="replied",
                    result=RunResult(run_id=RunId(target_run or ""), status=RunStatus.COMPLETED, output=DataPayload(data=payload)),
                )
            kind = payload.get("kind", "target_failed")
            kind = kind if kind in ("target_failed", "target_cancelled") else "target_failed"
            await self._commit([NewEntry(kind="ask.timeout", payload={"correlation_id": correlation_id, "kind": kind}, dedup_key=f"ask.timeout:{correlation_id}")])
            return AskOutcome(kind=kind, handle=handle)

        if datetime.now(tz=timezone.utc) >= deadline:
            await self._commit([NewEntry(kind="ask.timeout", payload={"correlation_id": correlation_id, "kind": "timed_out"}, dedup_key=f"ask.timeout:{correlation_id}")])
            return AskOutcome(kind="timed_out", handle=handle)
        raise SuspendInterrupt(self.run_id, Wakeup(kind="signal", signals=names, at=deadline), reason=f"ask:{correlation_id}")

    async def status(self, handle: RunHandle) -> RunStatusSummary:
        """A one-off peek at a run's progress. Not a stream."""
        run = await self._store.get_run(handle.run_id)
        last_seq = await self._store.last_seq(handle.run_id)
        last_kind: str | None = None
        if last_seq >= 0:
            entries = await self._store.read_events(handle.run_id, from_seq=last_seq)
            last_kind = entries[-1].kind if entries else None
        return RunStatusSummary(
            run_id=handle.run_id, status=run.status if run else RunStatus.PENDING, last_seq=last_seq, last_milestone=last_kind
        )

    async def follow(self, topic: Topic) -> None:
        await self._store.follow(Actor(type="internal", key=self.run_id), topic)

    async def unfollow(self, topic: Topic) -> None:
        await self._store.unfollow(Actor(type="internal", key=self.run_id), topic)

    # ------------------------------------------------------------------ supervision

    async def spawn(self, child_agent: Actor, *, boot: Message, supervision: Supervision | None = None) -> RunHandle:
        """Spawn a child run at an address you already know — a flow's fixed steps, an
        orchestrator's configured sub-agents. For a *new* actor by type, use
        ``spawn_child``, which derives a collision-free address."""
        self.check()
        return await self._spawn(child_agent, boot=boot, supervision=supervision)

    async def spawn_child(self, actor_type: str, *, boot: Message, supervision: Supervision | None = None) -> RunHandle:
        """Spawn a new actor of ``actor_type`` at an address derived from the tree, this
        run and this call's position, so two parents spawning the same type cannot land
        on one mailbox."""
        self.check()
        root = self._meta.supervision.root_id if self._meta.supervision else None
        root_key = (root.key or root.type) if root is not None else self.run_id
        path = self._journal.peek_path()
        return await self._spawn(Actor(type=actor_type, key=f"{root_key}/{self.run_id}.{path}"), boot=boot, supervision=supervision)

    async def _spawn(self, child_agent: Actor, *, boot: Message, supervision: Supervision | None) -> RunHandle:
        if supervision is not None:
            sup = supervision
        elif self._meta.supervision is not None:
            sup = self._meta.supervision.spawn_child(child_agent)
        else:
            sup = Supervision.root(child_agent)

        def build(path: str, effect_id: str):
            # The correlation id comes from this call's own path, never from anything the
            # caller builds fresh each time: a later ``ask(handle)`` has to find the reply.
            correlation_id = f"{self.run_id}.{path}"
            child_run = new_run_id()
            boot_msg = boot.model_copy(update={"reply_to": self.run_id, "correlation_id": correlation_id})
            spec = RunSpec(
                agent=child_agent,
                tenant=self.tenant_id or "default",
                run_id=child_run,
                thread_id=None,
                supervision=sup,
                priority=sup.priority,
                trace=self._trace_child(),
                agent_version=self._lease.agent_version,
            )
            value = {"run_id": str(child_run), "agent": str(child_agent), "correlation_id": correlation_id}
            return value, lambda entries: self._commit(
                entries, spawns=(SpawnSpec(effect_id=effect_id, child=spec, boot=boot_msg),)
            )

        outcome = await self._journal.record_atomic("spawn", {"agent": str(child_agent)}, build)
        v = outcome.value
        return RunHandle(
            run_id=RunId(v["run_id"]),
            agent_id=Actor.from_str(v["agent"]),
            parent_run=RunId(self.run_id),
            boot_correlation_id=v["correlation_id"],
        )

    async def cancel(self, handle: RunHandle, *, reason: str = "cancelled") -> None:
        """Cancel a child run and everything under it."""
        await self._store.request_cancel(handle.run_id, reason=reason)

    async def join(self, handle: RunHandle) -> RunResult:
        """Suspend until the child reaches a terminal state, then return how it ended."""
        self.check()
        name = f"child:{handle.run_id}"
        _path, claim_id, _ = self._journal.peek("join.wait", {"child_run": str(handle.run_id)})
        payload = await self._store.consume(RunId(self.run_id), name, claim_id)
        if payload is not None:
            status = RunStatus(payload["status"])
            error = payload.get("error")
            await self._commit(
                [NewEntry(kind="join.completed", payload={"child_run": str(handle.run_id), "status": status.value}, dedup_key=f"join:{handle.run_id}")]
            )
            return RunResult(run_id=handle.run_id, status=status, error=str(error["message"]) if isinstance(error, dict) else None)
        raise SuspendInterrupt(self.run_id, Wakeup(kind="signal", signals=[name]), reason=f"join:{handle.run_id}")

    async def sleep_until_signal(self, name: str) -> JsonObject:
        """Suspend until a named signal arrives. A replay re-claims the same payload
        it already consumed; if nothing has arrived it suspends again identically."""
        self.check()
        _path, claim_id, _ = self._journal.peek("signal.wait", {"name": name})
        payload = await self._store.consume(RunId(self.run_id), name, claim_id)
        if payload is not None:
            await self._commit([NewEntry(kind=RunLogKind.RUN_RESUMED, payload={"signal": name}, dedup_key=f"resumed:{claim_id}")])
            return payload
        raise SuspendInterrupt(self.run_id, Wakeup(kind="signal", signals=[name]), reason=f"sleep_until_signal:{name}")

    async def sleep_until(self, dt: datetime) -> None:
        """Suspend until a wall-clock time. Deliberately reads the real clock on every
        attempt, not the journaled one: the point is to observe time passing."""
        self.check()
        if datetime.now(tz=timezone.utc) >= dt:
            return
        raise SuspendInterrupt(self.run_id, Wakeup(kind="timer", at=dt), reason=f"sleep_until:{dt.isoformat()}")


def _serialize(resp: LLMResponse) -> JsonObject:
    return {
        "content": [b.model_dump(mode="json") for b in resp.content],
        "usage": {
            "input_tokens": resp.usage.input_tokens,
            "cached_tokens": resp.usage.cached_tokens,
            "output_tokens": resp.usage.output_tokens,
            "reasoning_tokens": resp.usage.reasoning_tokens,
            "cache_write_tokens": resp.usage.cache_write_tokens,
        },
        "cost_usd": resp.cost_usd,
        "finish_reason": resp.finish_reason.value,
        "response_id": resp.response_id,
        "served_model": resp.served_model,
    }


def _deserialize(value: JsonObject) -> LLMResponse:
    usage = value["usage"]
    return LLMResponse(
        # Read back from the journal, which a newer version may have written.
        content=[parse_content_block(d, forward_compatible=True) for d in value["content"]],
        usage=Usage(
            input_tokens=usage["input_tokens"],
            cached_tokens=usage["cached_tokens"],
            output_tokens=usage["output_tokens"],
            reasoning_tokens=usage["reasoning_tokens"],
            cache_write_tokens=usage.get("cache_write_tokens", 0),
        ),
        cost_usd=value.get("cost_usd", 0.0),
        finish_reason=FinishReason(value.get("finish_reason", "unspecified")),
        response_id=value.get("response_id"),
        served_model=value.get("served_model"),
    )


Agent: TypeAlias = _KernelAgent[RunContext]
"""The kernel's ``Agent`` protocol, parametrised with the concrete ``RunContext`` so an
agent author gets the full journaled surface in their editor."""


__all__ = ["Agent", "RunContext"]
