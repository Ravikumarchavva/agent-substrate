"""ToolInvoker — the single enforcement core for programmatic tool invocations.

Every tool call that originates from a chain (``ToolChainTool``) passes through
here.  Responsibilities:

1. Registry lookup and type gate (hosted/provider-defined/unknown/self → error)
2. Risk/approval with a bounded timeout (HITL can't block a sandbox forever)
3. Inbound ref resolution: ``{"$artifact": "<ref>"}`` args are fetched from
   ``BlobStore`` server-side before ``execute()`` — big data never enters
   the sandbox
4. ``execute()`` with per-call timeout + ``ctx.check()`` for cancellation
5. Result shaping: inline when small; ``BlobStore`` offload + pinning when
   large; media ``ContentBlock``s become ``ChainFile``s
6. Budget: per-chain call counter enforced against ``ChainPolicy.max_tool_calls``
7. Call trace: every invocation appended for crash-safe at-most-once semantics
8. Progress events emitted per call so orchestrators/UI see inside the chain
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable, cast

from substrate.kernel.abstractions.core.content import (
    JsonObject,
    MediaBlock,
    content_blocks_to_str,
)
from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.messaging.stream import AgentProgress, AgentStep
from substrate.kernel.abstractions.runtime.log_entry import RunLogKind
from substrate.kernel.abstractions.storage.blob import BlobStore
from substrate.kernel.abstractions.tools import (
    ToolCallRequest,
    ToolRegistry,
    ToolRisk,
    is_hosted_tool,
    is_provider_defined_tool,
)
from substrate.kernel.abstractions.tools.approval import (
    ApprovalDecision,
    ApprovalHandler,
    ApprovalRequest,
    ApprovalResult,
)
from substrate.kernel.abstractions.tools.chain import (
    ChainCallRecord,
    ChainFile,
    ChainPolicy,
    InvocationResult,
)
from substrate.kernel.hooks.manager import HookEvent, HookManager

if TYPE_CHECKING:
    from substrate.kernel.abstractions.tools.tools import ToolExecutionResult
    from substrate.kernel.runtime.context import RunContext

logger = logging.getLogger(__name__)


_CHAIN_TOOL_NAME = "tool_chain"

# Policy for tools an agent calls directly (``ctx.tool()``). ``ChainPolicy``'s
# own defaults — 50 calls, 60 s per call — are sized for a sandbox *script*
# chaining tools; applied to the agent loop they capped every run at 50 tool
# calls and killed any tool slower than a minute, including the code
# interpreter, which advertises up to 300 s. The loop is already bounded by
# the agent's ``max_iterations``, so the call cap here is only a runaway guard.
DIRECT_CALL_POLICY = ChainPolicy(
    max_tool_calls=10_000,
    call_timeout_s=600.0,
    approval_timeout_s=300.0,
    total_timeout_s=3_600.0,
)


class ToolInvoker:
    """Enforce risk/approval/ctx/budget for every bridged tool call in a chain.

    Constructor arguments
    ---------------------
    registry        : ToolRegistry — where tools are looked up
    approval_handler: optional HITL handler; absence means all non-SAFE tools
                      are denied immediately
    artifact_store  : optional large-data backend; absence means all results
                      are returned inline (no offloading)
    policy          : ChainPolicy — timeouts, budget, inline threshold

    The invoker is instantiated once per lifespan and shared across all chains.
    Per-chain state (call counter, trace, pinned refs) is passed in / out via
    ``InvokerSession`` objects returned by ``open_session()``.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        approval_handler: ApprovalHandler | None = None,
        artifact_store: BlobStore | None = None,
        policy: ChainPolicy | None = None,
        hooks: HookManager | None = None,
    ) -> None:
        self._registry = registry
        self._approval = approval_handler
        self._store = artifact_store
        self._policy = policy or ChainPolicy()
        self._hooks = hooks

    @property
    def registry(self) -> ToolRegistry:
        """The tools this invoker can call."""
        return self._registry

    def open_session(self) -> InvokerSession:
        """Create a fresh per-chain session (call counter, trace, pinned refs)."""
        return InvokerSession(self)

    @property
    def policy(self) -> ChainPolicy:
        return self._policy

    async def invoke(
        self,
        call: ToolCallRequest,
        *,
        session: InvokerSession,
        ctx: RunContext | None = None,
        progress_sink: Callable[[AgentProgress], object] | None = None,
    ) -> InvocationResult:
        """Invoke a single tool call with full enforcement.

        ``session`` carries per-chain state and must be obtained via
        ``open_session()``.  All per-chain mutations (counter, trace, pins)
        happen inside the session.
        """
        start_ms = int(time.monotonic() * 1000)
        tool_name = call.name
        status: str = "ok"

        if self._hooks:
            await self._hooks.dispatch(HookEvent.TOOL_START, {"tool_name": tool_name})

        try:
            result = await self._invoke_inner(
                call, session=session, ctx=ctx, progress_sink=progress_sink
            )
            status = result.status
            return result
        except Exception as exc:
            logger.exception("ToolInvoker unexpected error for %s", tool_name)
            status = "error"
            return InvocationResult(
                status="error",
                text=f"Invoker error: {type(exc).__name__}: {exc}",
            )
        finally:
            duration_ms = int(time.monotonic() * 1000) - start_ms
            if self._hooks:
                await self._hooks.dispatch(
                    HookEvent.TOOL_END,
                    {
                        "tool_name": tool_name,
                        "status": status,
                        "duration_ms": duration_ms,
                    },
                )
            args_digest = _digest(call.arguments)
            session._trace.append(
                ChainCallRecord(
                    tool=tool_name,
                    args_digest=args_digest,
                    status=status,  # type: ignore[arg-type]
                    duration_ms=duration_ms,
                )
            )

    async def _invoke_inner(
        self,
        call: ToolCallRequest,
        *,
        session: InvokerSession,
        ctx: Any | None,
        progress_sink: Any | None,
    ) -> InvocationResult:
        policy = self._policy
        tool_name = call.name

        # Resolve agent_id and run_id for progress reporting
        agent_id = None
        run_id = ""
        if ctx is not None:
            if (
                hasattr(ctx, "agent")
                and ctx.agent is not None
                and hasattr(ctx.agent, "id")
            ):
                agent_id = ctx.agent.id
            elif hasattr(ctx, "agent_id") and ctx.agent_id is not None:
                agent_id = ctx.agent_id
            if hasattr(ctx, "run_id"):
                run_id = ctx.run_id

        # 1. Budget check
        if session._call_count >= policy.max_tool_calls:
            return InvocationResult(
                status="error",
                text=f"Chain budget exhausted: max_tool_calls={policy.max_tool_calls}",
            )
        session._call_count += 1

        # 2. Registry lookup & type gate
        tool = self._registry.get(tool_name)
        if tool is None:
            return InvocationResult(
                status="error",
                text=f"Unknown tool: '{tool_name}'",
            )
        if is_hosted_tool(tool):
            return InvocationResult(
                status="error",
                text=f"Tool '{tool_name}' is provider-hosted and cannot be called from a chain.",
            )
        if is_provider_defined_tool(tool):
            return InvocationResult(
                status="error",
                text=f"Tool '{tool_name}' is provider-defined and cannot be called from a chain.",
            )
        if tool_name == _CHAIN_TOOL_NAME:
            return InvocationResult(
                status="error",
                text="Recursive tool_chain calls are not allowed.",
            )

        # 3. Risk / approval gate (dynamic classification takes precedence over static .risk)
        risk_summary: str | None = None
        classifier = getattr(tool, "classify_risk", None)
        if callable(classifier):
            tool_risk, risk_summary = await cast(
                "Awaitable[tuple[ToolRisk, str | None]]",
                classifier(dict(call.arguments)),
            )
        else:
            tool_risk = tool.risk
        max_allowed = policy.max_risk_unapproved
        if tool_risk > max_allowed:
            if self._approval is None:
                return InvocationResult(
                    status="denied",
                    text=(
                        f"Tool '{tool_name}' has risk={tool_risk.value} which requires "
                        f"approval, but no ApprovalHandler is configured. "
                        "Call this tool directly outside the chain."
                    ),
                )
            if ctx is not None and getattr(
                self._approval, "suspends_via_signal", False
            ):
                # Durable suspension: request_id is replay-stable via ctx.uuid()
                request_id = await ctx.uuid()
                log_payload = {
                    "request_id": request_id,
                    "tool_name": tool_name,
                    "args": dict(call.arguments),
                    "risk": tool_risk.value,
                    "summary": risk_summary or "",
                }
                try:
                    await ctx.log_once(RunLogKind.APPROVAL_REQUESTED, log_payload)
                except Exception:
                    pass
                decision_key = request_id
                signal_payload = await ctx.sleep_until_signal(f"hitl:{request_id}")
                result: ApprovalResult = ApprovalResult.from_response(signal_payload)
            else:
                from substrate.kernel.abstractions.core.identity import Actor

                decision_key = call.call_id
                approval_req = ApprovalRequest(
                    call=call,
                    risk=tool_risk,
                    agent_id=Actor(type="internal", key=call.call_id),
                    run_id=call.call_id,
                    context={"source": "tool_chain", "summary": risk_summary or ""},
                )
                try:
                    result = await asyncio.wait_for(
                        self._approval.request(approval_req),
                        timeout=policy.approval_timeout_s,
                    )
                except TimeoutError:
                    return InvocationResult(
                        status="denied",
                        text=(
                            f"Approval for '{tool_name}' timed out after "
                            f"{policy.approval_timeout_s}s. "
                            "Call this tool directly outside the chain for interactive approval."
                        ),
                    )
            await self._journal_decision(ctx, decision_key, tool_name, tool_risk, result)
            if result.decision == ApprovalDecision.MODIFIED:
                call = call.model_copy(update={"arguments": result.modified_args or {}})
            elif result.decision != ApprovalDecision.APPROVED:
                return InvocationResult(
                    status="denied",
                    text=f"Approval denied for tool '{tool_name}'.",
                )

        # 4. Inbound ref resolution
        args = await self._resolve_inbound_refs(call.arguments)

        # 5. ctx check before dispatch
        if ctx is not None and hasattr(ctx, "check"):
            ctx.check()

        # 6. Emit progress: TOOL_CALL
        if progress_sink is not None:
            _emit_progress(
                progress_sink,
                AgentStep.TOOL_CALL,
                f"Executing tool {tool_name}",
                session._call_count,
                agent_id=agent_id,
                run_id=run_id,
            )

        # 7. Execute with per-call timeout.
        # 7. Execute with per-call timeout (suspending tools are exempt)
        if getattr(tool, "suspends", False):
            exec_result = await tool.execute(ctx=ctx, **args)  # type: ignore[union-attr]
        else:
            try:
                exec_result = await asyncio.wait_for(
                    tool.execute(ctx=ctx, **args),  # type: ignore[union-attr]
                    timeout=policy.call_timeout_s,
                )
            except TimeoutError:
                return InvocationResult(
                    status="error",
                    text=f"Tool '{tool_name}' timed out after {policy.call_timeout_s}s.",
                )

        # 8. Emit progress: TOOL_RESULT
        if progress_sink is not None:
            _emit_progress(
                progress_sink,
                AgentStep.TOOL_RESULT,
                f"Tool {tool_name} finished",
                session._call_count,
                agent_id=agent_id,
                run_id=run_id,
            )

        # 9. Result shaping
        return await self._shape_result(
            exec_result, tool_name=tool_name, session=session
        )

    async def _journal_decision(
        self, ctx: Any, request_id: str, tool_name: str, risk: ToolRisk, result: ApprovalResult
    ) -> None:
        """Record who decided what, and when, on the run's own journal. Written once per
        request however often the run replays; a run with no journal to write to (a bare
        tool call outside a run) has nothing to record into."""
        log_once = getattr(ctx, "log_once", None)
        if log_once is None:
            return
        await log_once(
            RunLogKind.APPROVAL_DECIDED,
            {
                "request_id": request_id,
                "tool_name": tool_name,
                "risk": risk.value,
                "decision": result.decision.value,
                "decided_by": result.decided_by,
                "decided_at": result.decided_at.isoformat() if result.decided_at else None,
                "reason": result.reason,
                "modified": result.decision == ApprovalDecision.MODIFIED,
            },
        )

    async def _resolve_inbound_refs(self, arguments: JsonObject) -> dict[str, Any]:
        """Replace ``{"$artifact": ref}`` argument values with resolved bytes."""
        if self._store is None:
            return dict(arguments)
        resolved: dict[str, Any] = {}
        for k, v in arguments.items():
            if isinstance(v, dict) and "$artifact" in v:
                ref = str(v["$artifact"])
                try:
                    data = await self._store.resolve(ref)
                    resolved[k] = data
                except Exception as exc:
                    logger.warning("Failed to resolve artifact ref %s: %s", ref, exc)
                    resolved[k] = v
            else:
                resolved[k] = v
        return resolved

    async def _shape_result(
        self,
        exec_result: ToolExecutionResult,
        *,
        tool_name: str,
        session: InvokerSession,
    ) -> InvocationResult:
        structured = dict(getattr(exec_result, "structured_content", {}) or {})
        is_error = getattr(exec_result, "is_error", False)
        content = getattr(exec_result, "content", [])
        policy = self._policy

        # Every media block rides on InvocationResult.media — bytes, URL and
        # file_id references alike — so the model sees exactly what the tool
        # returned. The text omits them: each reaches the model natively, and a
        # "[Image: image/png]" placeholder next to the real image is just noise.
        media_blocks = [b for b in content if isinstance(b, MediaBlock)]
        files: list[ChainFile] = []
        media = media_blocks
        if content:
            text = content_blocks_to_str(
                [b for b in content if not isinstance(b, MediaBlock)]
            )
        else:
            text = exec_result.text if hasattr(exec_result, "text") else str(exec_result)

        # Offload to artifact store for chain runs when configured
        if media_blocks and self._store is not None:
            for block in media_blocks:
                if block.data is None:
                    continue  # url/file_id-only image — nothing to offload
                raw: bytes = (
                    block.data if isinstance(block.data, bytes) else block.data.encode()
                )
                ref = await self._store.store(
                    raw, content_type=block.media_type or "image/png"
                )
                await self._store.pin(ref)
                session._pinned_refs.append(ref)
                files.append(
                    ChainFile(
                        path=f"/workspace/media/{tool_name}_{len(files)}.png",
                        media_type=block.media_type or "image/png",
                        artifact_ref=ref,
                    )
                )

        # Decide inline vs offload for text result
        text_bytes = text.encode("utf-8")
        if len(text_bytes) <= policy.max_inline_result_bytes or self._store is None:
            return InvocationResult(
                status="error" if is_error else "ok",
                text=text,
                structured=structured,
                files=files,
                media=media,
            )

        # Offload large text result
        ref = await self._store.store(text_bytes, content_type="text/plain")
        await self._store.pin(ref)
        session._pinned_refs.append(ref)
        preview = text[: policy.max_inline_result_bytes] + "…"
        return InvocationResult(
            status="error" if is_error else "ok",
            text=preview,
            structured=structured,
            artifact_ref=ref,
            files=files,
            media=media,
        )


def build_invoker(agent: Any) -> ToolInvoker:
    """The ``ToolInvoker`` for an agent, from what the agent declares: its tools, its
    approval handler, its blob store, its hooks and the highest risk it lets through
    without approval."""
    from substrate.kernel.abstractions.tools.approval import (
        ApprovalDecision,
        ApprovalResult,
    )
    from substrate.kernel.tools.toolbox import Toolbox

    registry = getattr(agent, "tools", None) or Toolbox()
    approval = getattr(agent, "approval_handler", None)
    if approval is not None and not hasattr(approval, "request"):
        callback = approval

        class _CallbackApproval:
            """Adapts a bare ``async def (name, args) -> bool`` into the approval port.
            A bare bool can say yes or no, never "yes, but change this"."""

            async def request(self, req: Any) -> ApprovalResult:
                approved = await callback(req.call.name, req.call.arguments)
                return ApprovalResult(decision=ApprovalDecision.APPROVED if approved else ApprovalDecision.DENIED)

        approval = _CallbackApproval()

    policy = getattr(agent, "tool_policy", None) or DIRECT_CALL_POLICY
    required = getattr(agent, "approval_required_risk", None)
    if required is not None:
        # Approval is required from ``required`` upward, so what may pass unapproved is
        # the level just below it.
        below = {ToolRisk.CRITICAL: ToolRisk.HIGH, ToolRisk.HIGH: ToolRisk.SAFE}
        policy = policy.model_copy(update={"max_risk_unapproved": below.get(required, ToolRisk.SAFE)})
    return ToolInvoker(
        registry=registry,
        approval_handler=approval,
        artifact_store=getattr(agent, "blob_store", None),
        policy=policy,
        hooks=getattr(agent, "hooks", None),
    )


# ---------------------------------------------------------------------------
# InvokerSession — per-chain mutable state
# ---------------------------------------------------------------------------


class InvokerSession:
    """Mutable state for a single chain run.

    Obtained from ``ToolInvoker.open_session()``.  Must be closed via
    ``close()`` (or ``async with`` context manager) when the chain completes
    to unpin all artifacts.
    """

    def __init__(self, invoker: ToolInvoker) -> None:
        self._invoker = invoker
        self._call_count: int = 0
        self._trace: list[ChainCallRecord] = []
        self._pinned_refs: list[str] = []

    @property
    def trace(self) -> list[ChainCallRecord]:
        return list(self._trace)

    @property
    def call_count(self) -> int:
        return self._call_count

    async def close(self) -> None:
        """Unpin all artifacts pinned during this chain run."""
        if self._invoker._store is None:
            return
        for ref in self._pinned_refs:
            try:
                await self._invoker._store.unpin(ref)
            except Exception as exc:
                logger.warning("Failed to unpin artifact %s: %s", ref, exc)
        self._pinned_refs.clear()

    async def __aenter__(self) -> InvokerSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _digest(arguments: JsonObject) -> str:
    raw = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _emit_progress(
    sink: Callable[[AgentProgress], object],
    step: AgentStep,
    content: str,
    seq: int,
    agent_id: Actor | None = None,
    run_id: str = "",
) -> None:
    try:
        aid = agent_id or Actor(type="internal", key="tool_invoker")
        progress = AgentProgress(
            agent_id=aid,
            step=step,
            content=content,
            run_id=run_id,
            seq=seq,
        )
        if asyncio.iscoroutinefunction(sink):
            asyncio.ensure_future(sink(progress))
        elif callable(sink):
            sink(progress)
    except Exception as exc:
        logger.warning("Failed to emit progress: %s", exc)


__all__ = ["ToolInvoker", "InvokerSession", "build_invoker"]
