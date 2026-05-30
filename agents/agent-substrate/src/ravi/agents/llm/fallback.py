"""Fallback model client — automatic failover across providers.

Wraps multiple ``LLMClient`` instances and falls through to the next on
failure (rate limit, timeout, API error).

Usage::

    from ravi.agents.llm.fallback import FallbackClient
    from ravi.adapters.llm.factory import create_model_client

    primary = create_model_client("gpt-4o", api_keys=keys)
    backup = create_model_client("claude-sonnet-4-20250514", api_keys=keys)
    client = FallbackClient(clients=[primary, backup])

    resp = await client.generate(messages)
"""

from __future__ import annotations

from typing import AsyncIterator

from ravi.kernel.llm import LLMClient
from ravi.kernel import ContentBlock, Tool, ChatMessage
from ravi.kernel.stream import TextDelta, ReasoningDelta, CompletionEvent
from ravi.logger import setup_logging

logger = setup_logging()


class FallbackClient:
    """Model client with automatic failover.

    Tries each client in order.  On any exception, logs a warning and moves
    to the next.  If all clients fail, raises the last exception.
    """

    def __init__(self, clients: list[LLMClient]) -> None:
        if not clients:
            raise ValueError("FallbackClient requires at least one client")
        self._clients = clients

    @property
    def model(self) -> str:
        return self._clients[0].model

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[Tool] | None = None,
        system: str = "",
        **kwargs: object,
    ) -> list[ContentBlock]:
        last_exc: Exception | None = None
        for i, client in enumerate(self._clients):
            try:
                return await client.generate(messages, tools=tools, system=system, **kwargs)
            except Exception as exc:
                last_exc = exc
                next_msg = (
                    f"Trying client {i + 1}..." if i + 1 < len(self._clients) else "No more clients."
                )
                logger.warning("FallbackClient: client %d (%s) failed: %s. %s", i, client.model, exc, next_msg)
        raise last_exc  # type: ignore[misc]

    async def generate_stream(
        self,
        messages: list[ChatMessage],
        **kwargs: object,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
        last_exc: Exception | None = None
        for i, client in enumerate(self._clients):
            try:
                async for chunk in client.generate_stream(messages, **kwargs):
                    yield chunk
                return
            except Exception as exc:
                last_exc = exc
                logger.warning("FallbackClient: stream from client %d (%s) failed: %s", i, client.model, exc)
        if last_exc:
            raise last_exc

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return await self._clients[0].count_tokens(messages)
