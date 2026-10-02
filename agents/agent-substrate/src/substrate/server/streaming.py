"""Starting a run and following it as events — the part of serving that does not depend on any wire format.

``start`` puts a prompt on a thread and returns the run's id. ``follow`` yields what the run does, as the log entries
the engine wrote (token deltas included), ending at the run's terminal entry. Both the SSE wire protocol and AG-UI are
translations of that one stream, so each is a small pure function and neither owns a concurrency problem.

A client that disconnects detaches; it does not cancel. The run belongs to the worker that leased it, so it finishes
(and journals) whether anyone is watching, and a client that reconnects follows it again from where it was (``from_seq``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from substrate.runtime import ChatPayload, Message, Runtime
from substrate.types import Actor, ChatMessage, Role, RunLogEntry, RunLogKind, TextBlock

TERMINAL = frozenset({RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED, RunLogKind.RUN_CANCELLED})


async def start(runtime: Runtime, agent: Any, prompt: str, *, thread: str | None, tenant: str = "default") -> str:
    """Submit ``prompt`` to ``agent`` (on ``thread``, if given) and return the run's id.

    Raises ``ThreadBusyError`` if the thread already has an active run — before anything is written."""
    message = Message(
        target=agent.id,
        sender=Actor(type="http_proxy"),
        payload=ChatPayload(message=ChatMessage(role=Role.USER, content=[TextBlock(text=prompt)])),
    )
    if thread is not None:
        message = message.model_copy(update={"correlation_id": thread})
    run_id = await runtime.submit(agent.id, message, tenant=tenant, max_retries=0, thread_id=thread)
    return str(run_id)


async def is_finished(runtime: Runtime, run_id: str) -> bool:
    run = await runtime.get_run(run_id)
    return run is not None and run.status.is_terminal


async def follow(runtime: Runtime, run_id: str, *, from_seq: int = 0) -> AsyncIterator[RunLogEntry]:
    """The run's entries from ``from_seq`` on, ending after its terminal entry.

    A run that has already finished is replayed from its durable record (its live token deltas are gone by then — the
    assistant's whole message is there instead) and the stream ends, even when ``from_seq`` is past the end."""
    if await is_finished(runtime, run_id):
        for entry in await runtime.read(run_id, from_seq=from_seq):
            yield entry
        return
    async for entry in runtime.tail(run_id, from_seq=from_seq):
        yield entry
        if entry.kind in TERMINAL:
            return


async def with_keepalive(events: AsyncIterator[Any], interval: float) -> AsyncIterator[Any | None]:
    """``events``, with ``None`` yielded whenever ``interval`` seconds pass without one — so a run that is quietly waiting
    for a person (a suspended approval) still sends bytes and a proxy does not close the connection."""
    pending: asyncio.Task | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(events))
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield None
                continue
            task, pending = pending, None
            try:
                yield task.result()
            except StopAsyncIteration:
                return
    finally:
        if pending is not None:
            pending.cancel()
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            await aclose()


__all__ = ["TERMINAL", "follow", "is_finished", "start", "with_keepalive"]
