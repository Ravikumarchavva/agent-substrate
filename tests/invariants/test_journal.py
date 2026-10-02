"""Invariant register — the journal is replay state, not a token stream.

A streamed token is live output, not replay state: the durable log holds the
finished message, and the tokens travel on an ephemeral channel a tail can see and a
replay never reads.

The row asserts the shape rather than a constant: journal growth must not track
token count.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from substrate.testing.runtime import ephemeral_runtime
from substrate.types import ChatMessage, Role, TextBlock
from substrate.types import Actor
from substrate.types import Usage
from substrate.models import GenerationOptions, ModelCapabilities
from substrate.runtime import DataPayload, Message
from substrate.types import CompletionEvent, TextDelta


class _StreamingLLM:
    """Streams *chunks* text deltas, then the assembled completion."""

    def __init__(self, chunks: int) -> None:
        self.model = "scripted"
        self.capabilities = ModelCapabilities(model_id="scripted")
        self._chunks = chunks

    async def generate(
        self, messages: list[ChatMessage], *, options: Any = None, ctx: Any = None
    ) -> Any:
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
    async with ephemeral_runtime() as runtime:
        await runtime.register(agent)
        run_id = await runtime.submit(
            agent.id,
            Message(
                target=agent.id,
                sender=Actor.system("test"),
                payload=DataPayload(data={}),
            ),
        )
        async for entry in runtime.tail(run_id):
            if entry.kind in ("run.completed", "run.failed"):
                break
        return len(await runtime.read(run_id))


@pytest.fixture(scope="module")
def rows_by_chunk_count() -> dict[int, int]:
    async def _collect() -> dict[int, int]:
        return {chunks: await _journal_rows(chunks) for chunks in (1, 200)}

    return asyncio.run(_collect())


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
