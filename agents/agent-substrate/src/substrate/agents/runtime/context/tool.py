"""RunContext tool mixin — journaled tool calls (at-most-once side effects).

Split out of ``context/__init__.py`` (see that module's docstring for the full
suspend/resume/replay contract this all serves). Depends on
``_JournalMixin``'s path/log/effect helpers — see the ``TYPE_CHECKING``
stubs below.
"""

from __future__ import annotations

import base64
import uuid
from typing import TYPE_CHECKING, Any, Literal, cast

from substrate.kernel.core.content import ImageBlock, JsonObject
from substrate.kernel.runtime.effects import Effect, EffectResult
from substrate.kernel.tools.chain import InvocationResult

# A bare scheme, not a real path: same precedent as `sandbox:<path>` in
# assistant markdown (serving/monolith/routes/chat_intents.py) — the backend
# names the resource, and the *frontend* resolves it to wherever this deployed
# frontend's authenticated proxy lives (substrate-ui:
# lib/api/_client.ts::buildObjectUrl → /api/backend/files/object?key=...).
# Never a real ``/files/...`` path here: agent-substrate doesn't know which
# frontend (or how many) sit in front of it, and a bare path would either 401
# (no proxy in between to attach the JWT) or hardcode one specific frontend's
# routing into the engine.
#
# Also never a presigned URL: this log is replayed months later, and a link
# that expires would leave old conversations full of dead images.
#
# The key rides raw, not percent-encoded: same convention as `sandbox:<path>`,
# whose frontend resolver (buildWorkspaceFileUrl) also strips the scheme then
# encodes once itself. Encoding here too would double-encode every `/` in the
# key by the time it reaches a real URL.
_OBJECT_URL_TEMPLATE = "object:{key}"


def _attachment_url(img: ImageBlock) -> str:
    """Durable reference when the image is backed by the file store, else the
    bytes inline."""
    if img.storage_key:
        return _OBJECT_URL_TEMPLATE.format(key=img.storage_key)
    return (
        f"data:{img.media_type or 'image/png'};base64,"
        f"{base64.b64encode(img.data or b'').decode()}"
    )


if TYPE_CHECKING:
    from substrate.agents.runtime.context import Agent, RunContext
    from substrate.agents.tools.invoker import InvokerSession, ToolInvoker


class _ToolMixin:
    """Journaled tool capability (``ctx.tool()``)."""

    if TYPE_CHECKING:
        run_id: str
        agent: Agent | None
        _tool_invoker: ToolInvoker | None
        _invoker_session: InvokerSession | None

        def _alloc_path(self) -> str: ...
        def _enter_scope(self) -> None: ...
        def _exit_scope(self) -> None: ...
        def _lookup_effect(self, effect_id: str) -> EffectResult | None: ...
        async def _log(self, kind: str, payload: JsonObject = ...) -> None: ...
        async def log_once(
            self, kind: str, payload: JsonObject | None = None
        ) -> None: ...
        async def _record_effect(
            self, effect_id: str, status: Literal["ok", "error"], value: JsonObject
        ) -> None: ...
        async def _resolve_effect_value(self, result: EffectResult) -> JsonObject: ...

    async def tool(self, name: str, **args: Any) -> InvocationResult:
        """Journaled tool call via ToolInvoker.  At-most-once: won't re-execute on replay."""
        from substrate.kernel.tools import ToolCallRequest

        if self._tool_invoker is None:
            raise RuntimeError(
                "No ToolInvoker injected into this context.  "
                "Set agent.tools before registering with the runtime."
            )
        tool_invoker = self._tool_invoker
        if self._invoker_session is None:
            self._invoker_session = tool_invoker.open_session()
        invoker_session = self._invoker_session

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

        async def _do_invoke() -> InvocationResult:
            # self is always a full RunContext at runtime (_ToolMixin is only
            # ever combined into RunContext) — this cast just tells the type
            # checker what's already true, since a bare mixin can't be typed
            # as its own composed subclass.
            return await tool_invoker.invoke(
                call, session=invoker_session, ctx=cast("RunContext", self)
            )

        try:
            # log_once, not _log: a tool that suspends internally (raises
            # SuspendInterrupt, e.g. ask_human) never lets this call's outer
            # effect_id get recorded (see class docstring / log_once), so
            # this exact line re-runs on every resume. A plain _log would
            # duplicate the tool.call entry — and the UI card built from
            # it — once per suspend/resume cycle.
            await self.log_once(
                "tool.call",
                {"call_id": effect_id, "tool_name": name, "args": args},
            )

            middleware = getattr(self.agent, "middleware", None)
            if middleware is not None:
                from substrate.agents.middleware._contracts import MiddlewareContext
                from substrate.kernel.agent.middleware import MiddlewareStage

                func_ctx = MiddlewareContext(
                    stage=MiddlewareStage.TOOL,
                    agent_name=str(self.agent.id) if self.agent else "unknown",
                    run_id=self.run_id,
                    function_name=name,
                    arguments=args,
                )

                async def _final(c: MiddlewareContext) -> None:
                    c.tool_result = await _do_invoke()

                await middleware.execute(func_ctx, _final)
                if func_ctx.tool_result is None:
                    raise RuntimeError(
                        "middleware pipeline completed without producing a tool_result"
                    )
                result = func_ctx.tool_result
            else:
                result = await _do_invoke()

            await self._record_effect(
                effect_id,
                "ok" if result.status == "ok" else "error",
                result.model_dump(mode="json"),
            )
            ok = result.status == "ok"
            # result.media (e.g. matplotlib charts, retrieved page images) as
            # attachments. This log entry is later mapped to a ToolResultEvent
            # by a pure function (protocol/from_log.py) with no I/O access, so
            # whatever goes here must already be resolvable without a lookup.
            #
            # An image that came from the file store carries its key, so the
            # entry can point at a durable, authenticated endpoint instead of
            # embedding the bytes. That matters because this log is permanent:
            # inlining meant every image was stored again, base64-inflated
            # ~1.37x, on every single tool call that returned it. Images with no
            # key (nothing durable behind them) still inline, so they survive in
            # the log as before.
            attachments = [
                {
                    "id": uuid.uuid4().hex,
                    "name": f"{name}-{i}.{(img.media_type or 'image/png').split('/')[-1]}",
                    "mime": img.media_type or "image/png",
                    "size": len(img.data or b""),
                    "url": _attachment_url(img),
                }
                for i, img in enumerate(result.media)
            ]
            await self._log(
                "tool.result",
                {
                    "call_id": effect_id,
                    "tool_name": name,
                    "ok": ok,
                    "output": result.text or "",
                    "error": None if ok else (result.text or "tool error"),
                    "structured_content": result.structured or {},
                    "attachments": attachments,
                },
            )
            return result
        except Exception as exc:
            await self._record_effect(effect_id, "error", {"error": str(exc)})
            raise
        finally:
            self._exit_scope()


__all__ = ["_ToolMixin"]
