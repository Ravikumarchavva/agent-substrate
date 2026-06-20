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
import time
from typing import Any

from ravi.kernel.tools.approval import (
    ApprovalDecision,
    ApprovalHandler,
    ApprovalRequest,
)
from ravi.kernel.storage.blob import BlobStore
from ravi.kernel.tools.chain import (
    ChainCallRecord,
    ChainFile,
    ChainPolicy,
    InvocationResult,
)
from ravi.kernel.core.content import ImageBlock, JsonObject
from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.stream import AgentProgress, AgentStep
from ravi.kernel.tools import (
    ToolCallRequest,
    ToolRegistry,
    ToolRisk,
    is_hosted_tool,
    is_provider_defined_tool,
)

from ravi.agents.hooks.manager import HookEvent, HookManager
from ravi.logger import setup_logging

logger = setup_logging("ravi.agents.tools.invoker")

_RISK_ORDER: dict[ToolRisk, int] = {
    ToolRisk.SAFE: 0,
    ToolRisk.HIGH: 1,
    ToolRisk.CRITICAL: 2,
}

_CHAIN_TOOL_NAME = "tool_chain"


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
        ctx: Any | None = None,
        progress_sink: Any | None = None,
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
            if hasattr(ctx, "agent") and ctx.agent is not None and hasattr(ctx.agent, "id"):
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

        # 3. Risk / approval gate
        tool_risk = ToolRisk(getattr(tool, "risk", ToolRisk.SAFE))
        max_allowed = policy.max_risk_unapproved
        if _RISK_ORDER[tool_risk] > _RISK_ORDER[max_allowed]:
            if self._approval is None:
                return InvocationResult(
                    status="denied",
                    text=(
                        f"Tool '{tool_name}' has risk={tool_risk.value} which requires "
                        f"approval, but no ApprovalHandler is configured. "
                        "Call this tool directly outside the chain."
                    ),
                )
            from ravi.kernel.core.identity import AgentId

            approval_req = ApprovalRequest(
                call=call,
                risk=tool_risk,
                agent_id=AgentId(type="chain", key=call.call_id),
                run_id=call.call_id,
                context={"source": "tool_chain"},
            )
            try:
                decision = await asyncio.wait_for(
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
            if decision != ApprovalDecision.APPROVED:
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

        # 7. Execute with per-call timeout
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
        exec_result: Any,
        *,
        tool_name: str,
        session: InvokerSession,
    ) -> InvocationResult:
        text = exec_result.text if hasattr(exec_result, "text") else str(exec_result)
        structured = dict(getattr(exec_result, "structured_content", {}) or {})
        is_error = getattr(exec_result, "is_error", False)
        content = getattr(exec_result, "content", [])
        policy = self._policy

        # Separate media blocks from text blocks
        media_blocks = [b for b in content if isinstance(b, ImageBlock)]
        files: list[ChainFile] = []

        # Offload media blocks to artifact store
        if media_blocks and self._store is not None:
            for block in media_blocks:
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
    sink: Any,
    step: AgentStep,
    content: str,
    seq: int,
    agent_id: AgentId | None = None,
    run_id: str = "",
) -> None:
    try:
        aid = agent_id or AgentId(type="chain", key="tool_invoker")
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


__all__ = ["ToolInvoker", "InvokerSession"]
