"""MockLLMClient — a scriptable BaseModelClient for deterministic tests.

Usage::

    from tests.fixtures.mock_llm import MockLLMClient, Turn, tool_turn, text_turn

    llm = MockLLMClient(script=[
        tool_turn("calculator", {"expression": "2+2"}),
        text_turn("The answer is 4."),
    ])
    catalog.register_model("primary", llm)

``MockLLMClient`` pops turns in order on every ``generate()`` call.
When the script is exhausted it returns a plain "done" message so tests
that run one extra iteration don't crash.

For error injection pass ``error=SomeException(...)`` in a Turn.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Type
from uuid import uuid4

from pydantic import BaseModel

from ravi.kernel.llm.base_client import (
    BaseModelClient,
    GenerateResult,
    ModelStreamEvent,
)
from ravi.kernel.messages.base_message import BaseClientMessage
from ravi.kernel.messages.client_messages import AssistantMessage, ToolCallMessage
from ravi.kernel.messages._types import CompletionChunk


# ---------------------------------------------------------------------------
# Turn descriptors
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    """One scripted LLM response."""

    text: Optional[str] = None
    tool_name: Optional[str] = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_call_id: Optional[str] = None
    error: Optional[Exception] = None
    delay: float = 0.0


def text_turn(text: str, delay: float = 0.0) -> Turn:
    """Helper: create a plain-text response turn."""
    return Turn(text=text, delay=delay)


def tool_turn(name: str, args: dict[str, Any], delay: float = 0.0) -> Turn:
    """Helper: create a tool-call response turn."""
    return Turn(tool_name=name, tool_args=args, delay=delay)


def error_turn(exc: Exception) -> Turn:
    """Helper: create a turn that raises on generate()."""
    return Turn(error=exc)


# ---------------------------------------------------------------------------
# MockLLMClient
# ---------------------------------------------------------------------------


class MockLLMClient(BaseModelClient):
    """Scriptable stand-in for any real LLM.

    - Pops turns in order.
    - Tracks every call in ``self.calls``.
    - Thread-safe enough for single-async-loop tests.
    """

    def __init__(self, script: list[Turn] | None = None) -> None:
        super().__init__(model="mock-llm-v1", temperature=0.0)
        self._script: list[Turn] = list(script or [])
        self.calls: list[list[BaseClientMessage]] = []
        self.system_calls: list[str] = []

    # -- helpers -------------------------------------------------------------

    def _next_turn(self) -> Turn:
        if self._script:
            return self._script.pop(0)
        return Turn(text="[mock: script exhausted]")

    @staticmethod
    def _build_message(turn: Turn) -> AssistantMessage:
        if turn.tool_name:
            tc = ToolCallMessage(
                name=turn.tool_name,
                arguments=turn.tool_args,
                id=turn.tool_call_id or str(uuid4()),
            )
            return AssistantMessage(
                content=None,
                tool_calls=[tc],
                finish_reason="tool_calls",
            )
        return AssistantMessage(
            content=[turn.text or ""],
            tool_calls=None,
            finish_reason="stop",
        )

    # -- BaseModelClient abstract methods ------------------------------------

    async def generate(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        system_instructions: str = "",
        tool_choice: Optional[str | dict[str, Any]] = None,
        response_format: Optional[Type[BaseModel]] = None,
        **kwargs: Any,
    ) -> GenerateResult:
        self.calls.append(list(messages))
        self.system_calls.append(system_instructions)
        turn = self._next_turn()
        if turn.delay:
            await asyncio.sleep(turn.delay)
        if turn.error:
            raise turn.error
        return self._build_message(turn)

    async def generate_stream(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        system_instructions: str = "",
        response_format: Optional[Type[BaseModel]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.calls.append(list(messages))
        self.system_calls.append(system_instructions)
        turn = self._next_turn()
        if turn.error:
            raise turn.error
        msg = self._build_message(turn)
        yield CompletionChunk(
            finish_reason="stop",
            message=msg,
            input_tokens=0,
            output_tokens=0,
        )

    async def count_tokens(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> int:
        total = 0
        for m in messages:
            c = m.content
            if isinstance(c, str):
                total += len(c) // 4
            elif isinstance(c, list):
                for part in c:
                    total += len(str(part)) // 4
        return total
