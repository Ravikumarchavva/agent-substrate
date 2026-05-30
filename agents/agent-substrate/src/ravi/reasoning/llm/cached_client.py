"""Cached model client — transparent semantic caching decorator.

Wraps any ``BaseModelClient`` and checks a ``SemanticCache`` before
calling the underlying LLM.  Same interface — drop-in replacement.

Usage::

    from ravi.reasoning.llm.cached_client import CachedModelClient
    from ravi.reasoning.llm.cache import SemanticCache

    cache = SemanticCache(embedding_client=embed, redis_url=redis_url)
    await cache.connect()

    cached = CachedModelClient(inner=openai_client, cache=cache)
    resp = await cached.generate(messages)  # checks cache first
"""

from __future__ import annotations
from ravi.logger import setup_logging

from typing import TYPE_CHECKING, AsyncIterator

from ravi.fabric.llm.client import LLMClient
from ravi.kernel import ContentBlock, TextBlock, Tool, ChatMessage
from ravi.kernel.stream import TextDelta, ReasoningDelta, CompletionEvent

if TYPE_CHECKING:
    from ravi.reasoning.llm.cache import SemanticCache

logger = setup_logging()


class CachedModelClient:
    """Decorator that adds semantic caching to any model client.

    For ``generate()`` calls *without* tools,
    checks the cache first.  On miss, calls the inner client and
    caches the response.

    Streaming (``generate_stream``) bypasses the cache.
    """

    def __init__(
        self,
        inner: LLMClient,
        cache: SemanticCache,
    ) -> None:
        self._inner = inner
        self._cache = cache

    @property
    def model(self) -> str:
        return self._inner.model

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[Tool] | None = None,
        system: str = "",
        **kwargs: object,
    ) -> list[ContentBlock]:
        # Only cache simple text-only calls (no tools)
        cacheable = tools is None or len(tools) == 0

        if cacheable:
            query_text = self._extract_query(messages)
            if query_text:
                cached = await self._cache.get(query_text)
                if cached is not None:
                    return [TextBlock(text=cached)]

        # Cache miss or non-cacheable — call inner client
        result = await self._inner.generate(
            messages,
            tools=tools,
            system=system,
            **kwargs,
        )

        # Cache the response
        if cacheable and result:
            query_text = self._extract_query(messages)
            response_text = "".join(
                part.text for part in result if isinstance(part, TextBlock)
            )
            if query_text and response_text:
                await self._cache.put(query_text, response_text)

        return result

    async def generate_stream(
        self,
        messages: list[ChatMessage],
        **kwargs: object,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
        # Streaming bypasses cache
        async for chunk in self._inner.generate_stream(messages, **kwargs):
            yield chunk

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return await self._inner.count_tokens(messages)

    @staticmethod
    def _extract_query(messages: list[ChatMessage]) -> str:
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
