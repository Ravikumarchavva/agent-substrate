"""Cached model client — transparent semantic caching decorator.

Wraps any ``LLMClient`` and checks a ``SemanticCache`` before calling the
underlying LLM.  Same interface — drop-in replacement.

Usage::

    from ravi.agents.llm.cached_client import CachedModelClient
    from ravi.agents.llm.cache import SemanticCache

    cache = SemanticCache(embedding_client=embed, redis_url=redis_url)
    await cache.connect()

    cached = CachedModelClient(inner=openai_client, cache=cache)
    resp = await cached.generate(messages)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from ravi.kernel.llm import GenerationOptions, LLMClient, LLMResponse, Usage
from ravi.kernel import TextBlock, ChatMessage
from ravi.kernel.stream import TextDelta, ReasoningDelta, CompletionEvent
from ravi.logger import setup_logging

if TYPE_CHECKING:
    from ravi.agents.llm.cache import SemanticCache

logger = setup_logging()


class CachedModelClient:
    """Decorator that adds semantic caching to any model client.

    For ``generate()`` calls without tools, checks the cache first.
    Streaming bypasses the cache.
    """

    def __init__(self, inner: LLMClient, cache: SemanticCache) -> None:
        self._inner = inner
        self._cache = cache

    @property
    def model(self) -> str:
        return self._inner.model

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        cacheable = not options.tools

        if cacheable:
            query_text = self._extract_query(messages)
            if query_text:
                cached = await self._cache.get(query_text)
                if cached is not None:
                    return LLMResponse(content=[TextBlock(text=cached)], usage=Usage())

        result = await self._inner.generate(messages, options=options)

        if cacheable and result.content:
            query_text = self._extract_query(messages)
            response_text = "".join(
                part.text for part in result.content if isinstance(part, TextBlock)
            )
            if query_text and response_text:
                await self._cache.put(query_text, response_text)

        return result

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
        return self._inner.generate_stream(messages, options=options)

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return await self._inner.count_tokens(messages)

    @staticmethod
    def _extract_query(messages: list[ChatMessage]) -> str:
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

    @property
    def supports_audio(self) -> bool:
        return getattr(self._inner, "supports_audio", False)

    @property
    def supports_s2s(self) -> bool:
        return getattr(self._inner, "supports_s2s", False)
