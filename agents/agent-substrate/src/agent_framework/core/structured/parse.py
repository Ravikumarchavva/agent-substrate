"""Standalone ``parse()`` coroutine — the zero-ceremony entry point for
structured outputs when you don't need a full ReAct agent loop.

Example::

    from agent_framework.core.structured import parse, ClassificationResult
    from agent_framework.providers.llm.openai.openai_client import OpenAIClient
    from agent_framework.core.messages.client_messages import UserMessage

    client = OpenAIClient(model='gpt-4o-2024-08-06')

    result = await parse(
        client=client,
        messages=[UserMessage(content=[{'type': 'text', 'text': 'Great product!'}])],
        schema=ClassificationResult,
        system='Classify the sentiment. Labels: positive, negative, neutral.',
    )

    if result.ok:
        print(result.parsed.label, result.parsed.confidence)
    elif result.refused:
        print('Refused:', result.refusal)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Type, TypeVar

from agent_framework.core.messages.client_messages import SystemMessage

if TYPE_CHECKING:

    from agent_framework.core.messages.base_message import BaseClientMessage
    from agent_framework.core.structured.result import StructuredOutputResult
    from agent_framework.providers.llm.base_client import BaseModelClient

T = TypeVar("T")


async def parse(
    client: "BaseModelClient",
    messages: "List[BaseClientMessage]",
    schema: "Type[T]",
    *,
    system: Optional[str] = None,
) -> "StructuredOutputResult[T]":
    """Parse a list of messages into a typed Pydantic instance.

    This is the simplest entry point for structured outputs.  It wraps
    ``client.generate_structured()`` with an optional system-message
    prepend and type parameters.

    Args:
        client: Any ``BaseModelClient`` implementation.  The client must
            implement ``generate_structured()`` (all built-in providers do).
        messages: Conversation messages.  ``SystemMessage`` items are
            allowed; if ``system`` is *also* given a new ``SystemMessage``
            is prepended *before* the existing list.
        schema: A Pydantic ``BaseModel`` subclass that describes the
            desired output shape.  The class is passed directly to the
            underlying API; no manual JSON Schema conversion is needed.
        system: Optional system prompt to prepend as a ``SystemMessage``.
            Use this for brevity; alternatively just include a
            ``SystemMessage`` in ``messages``.

    Returns:
        ``StructuredOutputResult[T]`` with ``parsed``, ``raw_text``, and
        ``refusal`` fields.

    Raises:
        StructuredOutputError: If the model returns unparseable output
            (not a safety refusal — those surface as
            ``result.refused == True``).
    """

    full_messages: List[BaseClientMessage] = []
    if system:
        full_messages.append(SystemMessage(content=system))
    full_messages.extend(messages)

    return await client.generate_structured(full_messages, schema)
