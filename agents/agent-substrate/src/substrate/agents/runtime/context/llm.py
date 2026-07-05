"""RunContext LLM mixin — journaled model calls (replay never re-bills).

Split out of ``context/__init__.py`` (see that module's docstring for the full
suspend/resume/replay contract this all serves). Depends on
``_JournalMixin``'s path/log/effect helpers — see the ``TYPE_CHECKING``
stubs below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from substrate.kernel.core.content import ChatMessage, ContentBlock, JsonObject
from substrate.kernel.core.usage import Usage
from substrate.kernel.llm.llm import GenerationOptions, LLMResponse
from substrate.kernel.runtime.effects import Effect, EffectResult

if TYPE_CHECKING:
    from substrate.kernel.agent.runtime_context import RunMeta
    from substrate.kernel.llm.llm import LLMClient
    from substrate.agents.runtime.context import Agent


class _LLMMixin:
    """Journaled LLM capability (``ctx.llm()``)."""

    if TYPE_CHECKING:
        run_id: str
        _llm_client: LLMClient | None
        _meta: RunMeta
        agent: Agent | None

        def _alloc_path(self) -> str: ...
        def _lookup_effect(self, effect_id: str) -> EffectResult | None: ...
        async def _log(self, kind: str, payload: JsonObject = ...) -> None: ...
        async def _record_effect(
            self, effect_id: str, status: Literal["ok", "error"], value: JsonObject
        ) -> None: ...
        async def _resolve_effect_value(self, result: EffectResult) -> JsonObject: ...

    async def llm(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        """Journaled LLM call.  Replay returns cached response; never re-bills."""
        if self._llm_client is None:
            raise RuntimeError(
                "No LLM client injected into this context.  "
                "Set agent.model before registering with the runtime."
            )
        llm_client = self._llm_client

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
            from substrate.kernel.core.content import content_block_from_dict

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

        args: JsonObject = {"model": llm_client.model, "msg_count": len(messages)}
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "llm", args)
        cached = self._lookup_effect(effect_id)
        if cached:
            if cached.status == "error":
                value = await self._resolve_effect_value(cached)
                raise RuntimeError(value.get("error", "journaled llm error"))
            return _deserialize(await self._resolve_effect_value(cached))

        async def _do_generate(msgs: list[ChatMessage]) -> LLMResponse:
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
                stream = llm_client.generate_stream(
                    msgs, options=options, ctx=self._meta
                )
            except TypeError:
                stream = llm_client.generate_stream(msgs, options=options)

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

            return LLMResponse(content=final_content, usage=final_usage)

        try:
            middleware = getattr(self.agent, "middleware", None)
            if middleware is not None:
                from substrate.agents.middleware._contracts import MiddlewareContext
                from substrate.kernel.agent.middleware import MiddlewareStage

                chat_ctx = MiddlewareContext(
                    stage=MiddlewareStage.CHAT,
                    agent_name=str(self.agent.id) if self.agent else "unknown",
                    run_id=self.run_id,
                    messages=messages,
                    system_instructions=options.system_instructions or "",
                    tools=options.tools,
                )

                async def _final(c: MiddlewareContext) -> None:
                    c.chat_result = await _do_generate(c.messages or messages)

                await middleware.execute(chat_ctx, _final)
                if chat_ctx.chat_result is None:
                    raise RuntimeError(
                        "middleware pipeline completed without producing a chat_result"
                    )
                resp = chat_ctx.chat_result
            else:
                resp = await _do_generate(messages)

            await self._record_effect(effect_id, "ok", _serialize(resp))
            await self._log(
                "llm.call",
                {"model": llm_client.model, "tokens": resp.usage.total_tokens},
            )
            return resp
        except Exception as exc:
            await self._record_effect(effect_id, "error", {"error": str(exc)})
            raise


__all__ = ["_LLMMixin"]
