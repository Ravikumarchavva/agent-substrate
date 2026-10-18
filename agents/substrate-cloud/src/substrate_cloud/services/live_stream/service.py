"""Live Stream: a run's wire events, served from the run's durable journal.

This is the stream the monolith's ``POST /chat`` and ``GET /stream/{thread_id}`` write (``stream/session.py``): ``hello``, then one wire
event per journal entry (``text.delta``, ``tool.call``, ``input.requested``...), then ``run.completed|failed|cancelled`` and ``[DONE]``.
The journal is the source, so a client that connects late, reconnects, or finds this service restarted loses nothing: it tails
from wherever it wants to start.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from substrate.server.protocol import HelloEvent

from substrate_cloud.stream.session import tail_wire_events

logger = logging.getLogger(__name__)

PROTOCOL_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def sse(data: Any) -> str:
    return f"data: {json.dumps(data, default=str)}\n\n"


async def run_stream(
    runtime: Any, run_id: str, *, from_seq: int = 0
) -> AsyncIterator[str]:
    """SSE lines for one run, from ``from_seq`` to its end."""
    try:
        yield sse(HelloEvent().model_dump(mode="json"))
        async for wire in tail_wire_events(runtime, run_id, from_seq=from_seq):
            yield sse(wire.model_dump(mode="json"))
    except Exception as exc:  # the stream is already open: report in-band
        logger.exception("Stream of run %s failed", run_id)
        yield sse({"type": "error", "message": str(exc)})
    finally:
        yield "data: [DONE]\n\n"


async def done_only() -> AsyncIterator[str]:
    yield "data: [DONE]\n\n"
