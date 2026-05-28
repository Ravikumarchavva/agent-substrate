"""Fallback model client — automatic failover across providers.

Wraps multiple ``BaseModelClient`` instances and falls through to the
next one on failure (rate limit, timeout, API error).

Usage::

    from ravi.extensions.llm.fallback import FallbackClient
    from ravi.integrations.llm.factory import create_model_client

    primary = create_model_client("gpt-4o", api_keys=keys)
    backup = create_model_client("claude-sonnet-4-20250514", api_keys=keys)
    client = FallbackClient(clients=[primary, backup])

    # Uses primary; on failure → transparent retry with backup
    resp = await client.generate(messages)
"""

from __future__ import annotations
from ravi.logger import setup_logging

from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from ravi.kernel.llm.base_client import (
    BaseModelClient,
    GenerateResult,
    ModelStreamEvent,
)

if TYPE_CHECKING:
    from pydantic import BaseModel
    from ravi.kernel.messages.base_message import BaseClientMessage

logger = setup_logging()


class FallbackClient(BaseModelClient):
    """Model client with automatic failover.

    Tries each client in order.  On any exception, logs a warning and
    moves to the next.  If all clients fail, raises the **last** exception.
    """

    def __init__(
        self,
        clients: list[BaseModelClient],
        **kwargs: Any,
    ) -> None:
        if not clients:
            raise ValueError("FallbackClient requires at least one client")
        # Use the primary client's model/settings as the "face" of this client
        primary = clients[0]
        super().__init__(
            model=primary.model,
            temperature=primary.temperature,
            max_tokens=primary.max_tokens,
            **kwargs,
        )
        self._clients = clients

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def generate(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        tool_choice: Optional[str | dict[str, Any]] = None,
        response_format: Optional[type[BaseModel]] = None,
        **kwargs: Any,
    ) -> GenerateResult:
        last_exc: Exception | None = None
        for i, client in enumerate(self._clients):
            try:
                return await client.generate(
                    messages,
                    tools,
                    tool_choice=tool_choice,
                    response_format=response_format,
                    **kwargs,
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "FallbackClient: client %d (%s) failed: %s. %s",
                    i,
                    client.model,
                    exc,
                    f"Trying client {i + 1}..."
                    if i + 1 < len(self._clients)
                    else "No more clients.",
                )

        raise last_exc  # type: ignore[misc]

    async def generate_stream(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        response_format: Optional[type[BaseModel]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelStreamEvent]:
        last_exc: Exception | None = None
        for i, client in enumerate(self._clients):
            try:
                async for chunk in client.generate_stream(
                    messages,
                    tools,
                    response_format=response_format,
                    **kwargs,
                ):
                    yield chunk
                return  # Stream completed successfully
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "FallbackClient: stream from client %d (%s) failed: %s",
                    i,
                    client.model,
                    exc,
                )

        if last_exc:
            raise last_exc

    async def count_tokens(self, messages: list[BaseClientMessage]) -> int:
        """Count tokens using the primary client."""
        return await self._clients[0].count_tokens(messages)
