"""In-process async pub/sub EventBus for agent-to-agent communication."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any

from ravi.logger import setup_logging

logger = setup_logging(mode='pretty', handler='console')


class EventBus:
    """Each pipeline run gets its own job_id namespace.

    Agents publish events which are fan-out to all registered subscribers
    for that topic, AND streamed to the SSE queue for the frontend.
    """

    def __init__(self) -> None:
        self._sse: dict[str, asyncio.Queue] = {}
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    # ── SSE stream ─────────────────────────────────────────────────────────

    def sse_queue(self, job_id: str) -> asyncio.Queue:
        if job_id not in self._sse:
            self._sse[job_id] = asyncio.Queue()
        return self._sse[job_id]

    async def emit(
        self,
        job_id: str,
        agent: str,
        event: str,
        data: dict[str, Any],
    ) -> None:
        payload = {
            "agent": agent,
            "event": event,
            "data": data,
            "ts": round(time.time() * 1000),
        }
        logger.debug("emit job=%s agent=%s event=%s", job_id, agent, event)
        await self.sse_queue(job_id).put(payload)

        topic = f"{job_id}::{event}"
        for q in self._subs.get(topic, []):
            await q.put(data)

    async def done(self, job_id: str) -> None:
        await self.sse_queue(job_id).put(None)

    # ── Pub/sub ────────────────────────────────────────────────────────────

    def subscribe(self, job_id: str, event: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[f"{job_id}::{event}"].append(q)
        return q

    def cleanup(self, job_id: str) -> None:
        self._sse.pop(job_id, None)
        for key in list(self._subs):
            if key.startswith(f"{job_id}::"):
                del self._subs[key]


bus = EventBus()
