"""Cached model client — transparent semantic caching decorator.

Wraps any ``BaseModelClient`` and checks a ``SemanticCache`` before
calling the underlying LLM.  Same interface — drop-in replacement.

Usage::

    from ravi.extensions.llm.cached_client import CachedModelClient
    from ravi.extensions.llm.cache import SemanticCache

    cache = SemanticCache(embedding_client=embed, redis_url=redis_url)
    await cache.connect()

    cached = CachedModelClient(inner=openai_client, cache=cache)
    resp = await cached.generate(messages)  # checks cache first
"""

from __future__ import annotations
from ravi.logger import setup_logging

from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from ravi.kernel.llm.base_client import (
    BaseModelClient,
    GenerateResult,
    ModelStreamEvent,
)
from ravi.kernel.messages.client_messages import AssistantMessage

if TYPE_CHECKING:
    from pydantic import BaseModel
    from ravi.extensions.llm.cache import SemanticCache
    from ravi.kernel.messages.base_message import BaseClientMessage

logger = setup_logging()


class CachedModelClient(BaseModelClient):
    """Decorator that adds semantic caching to any model client.

    For ``generate()`` calls *without* tools or response_format,
    checks the cache first.  On miss, calls the inner client and
    caches the response.

    Streaming (``generate_stream``) bypasses the cache.
    """

    def __init__(
        self,
        inner: BaseModelClient,
        cache: SemanticCache,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            model=inner.model,
            temperature=inner.temperature,
            max_tokens=inner.max_tokens,
            **kwargs,
        )
        self._inner = inner
        self._cache = cache

    async def generate(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        tool_choice: Optional[str | dict[str, Any]] = None,
        response_format: Optional[type[BaseModel]] = None,
        **kwargs: Any,
    ) -> GenerateResult:
        # Only cache simple text-only calls (no tools, no structured output)
        cacheable = tools is None and response_format is None

        if cacheable:
            query_text = self._extract_query(messages)
            if query_text:
                cached = await self._cache.get(query_text)
                if cached is not None:
                    return AssistantMessage(
                        role="assistant",
                        content=[cached],
                    )

        # Cache miss or non-cacheable — call inner client
        result = await self._inner.generate(
            messages,
            tools,
            tool_choice=tool_choice,
            response_format=response_format,
            **kwargs,
        )

        # Cache the response
        if cacheable and isinstance(result, AssistantMessage) and result.content:
            query_text = self._extract_query(messages)
            response_text = "".join(
                part for part in result.content if isinstance(part, str)
            )
            if query_text and response_text:
                await self._cache.put(query_text, response_text)

        return result

    async def generate_stream(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        response_format: Optional[type[BaseModel]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelStreamEvent]:
        # Streaming bypasses cache
        async for chunk in self._inner.generate_stream(
            messages, tools, response_format=response_format, **kwargs
        ):
            yield chunk

    async def count_tokens(self, messages: list[BaseClientMessage]) -> int:
        return await self._inner.count_tokens(messages)

    @staticmethod
    def _extract_query(messages: list[BaseClientMessage]) -> str:
        """Extract the last user message text for cache key."""
        for msg in reversed(messages):
            if getattr(msg, "role", None) == "user":
                content = getattr(msg, "content", None)
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    text_parts = [p for p in content if isinstance(p, str)]
                    if text_parts:
                        return " ".join(text_parts)
        return ""

    # ── Delegate optional capabilities ────────────────────────────────────────

    @property
    def supports_audio(self) -> bool:
        return getattr(self._inner, "supports_audio", False)

    @property
    def supports_s2s(self) -> bool:
        return getattr(self._inner, "supports_s2s", False)
