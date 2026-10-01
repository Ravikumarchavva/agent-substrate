"""Invariant register — the journal is replay state, not a token stream.

Every streamed token is currently appended to the run's durable log as its own
row, so a 1000-token reply writes 1000 rows that every subsequent ``fold()``
reads back. Replay needs the finished message, not the keystrokes; live tokens
belong on an ephemeral channel.

The row asserts the shape rather than a constant: journal growth must not track
token count.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from substrate.kernel.runtime.runtime import Runtime
from substrate.kernel.abstractions.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.core.usage import Usage
from substrate.kernel.abstractions.llm import GenerationOptions, ModelCapabilities
from substrate.kernel.abstractions.messaging.message import DataPayload, Message
from substrate.kernel.abstractions.messaging.stream import CompletionEvent, TextDelta


class _StreamingLLM:
    """Streams *chunks* text deltas, then the assembled completion."""

    def __init__(self, chunks: int) -> None:
        self.model = "scripted"
        self.capabilities = ModelCapabilities(model_id="scripted")
        self._chunks = chunks

    async def generate(self, messages: list[ChatMessage], *, options: Any = None, ctx: Any = None) -> Any:
        raise NotImplementedError

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),  # noqa: B008
        ctx: Any = None,
    ) -> AsyncIterator[Any]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[Any]:
        for _ in range(self._chunks):
            yield TextDelta(text="tok ")
        yield CompletionEvent(
            content=[TextBlock(text="tok " * self._chunks)],
            usage=Usage(input_tokens=10, output_tokens=self._chunks),
        )

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return 0


class _ChattyAgent:
    def __init__(self, chunks: int) -> None:
        self.id = Actor("agent", "chatty")
        self.model = _StreamingLLM(chunks)

    async def run(self, ctx: Any, inbox: list[Message]) -> None:
        await ctx.llm([ChatMessage(role=Role.USER, content="hi")])


async def _journal_rows(chunks: int) -> int:
    agent = _ChattyAgent(chunks)
    async with Runtime() as runtime:
        await runtime.register(agent)
        run_id = await runtime.submit(
            agent.id,
            Message(target=agent.id, sender=Actor.system("test"), payload=DataPayload(data={})),
        )
        rows = 0
        async for entry in runtime.event_log.tail(run_id):
            rows += 1
            if entry.kind in ("run.completed", "run.failed"):
                break
        return rows


@pytest.fixture(scope="module")
def rows_by_chunk_count() -> dict[int, int]:
    async def _collect() -> dict[int, int]:
        return {chunks: await _journal_rows(chunks) for chunks in (1, 200)}

    return asyncio.run(_collect())


@pytest.mark.xfail(
    strict=True,
    reason="Journal/stream split: one durable row is appended per streamed "
    "token, so the log grows with reply length and every fold() re-reads it. "
    "Fixed in step 3 (ephemeral stream channel + append_many).",
)
def test_journal_size_does_not_grow_with_token_count(
    rows_by_chunk_count: dict[int, int],
) -> None:
    """Two replies differing only in length must journal the same number of
    rows: the durable record of a turn is the finished message."""
    short, long = rows_by_chunk_count[1], rows_by_chunk_count[200]
    assert long == short, (
        f"a 1-token reply journaled {short} rows and a 200-token reply "
        f"journaled {long} — the journal is carrying the token stream"
    )
