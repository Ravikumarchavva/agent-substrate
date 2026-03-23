"""Live Stream Service — SSE event assembly and delivery.

Subscribes to agent, HITL, and task events via the event bus,
merges them into a single ordered SSE stream per thread/run,
and delivers to connected clients.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Dict, Optional, Set

import redis.asyncio as aioredis

from agent_framework.shared.events.bus import EventBus

logger = logging.getLogger(__name__)


class StreamProjector:
    """Manages SSE streams for active runs.

    Each connected client subscribes to a (thread_id, run_id) pair.
    The projector listens to relevant event streams and fans events
    out to all subscribed clients.
    """

    def __init__(self, redis_client: aioredis.Redis, event_bus: EventBus):
        self._redis = redis_client
        self._event_bus = event_bus
        # thread_id → set of asyncio.Queue
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}

    def subscribe(self, thread_id: str) -> asyncio.Queue:
        """Subscribe to events for a thread. Returns a queue to poll."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        if thread_id not in self._subscribers:
            self._subscribers[thread_id] = set()
        self._subscribers[thread_id].add(queue)
        return queue

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue) -> None:
        """Unsubscribe from thread events."""
        subs = self._subscribers.get(thread_id)
        if subs:
            subs.discard(queue)
            if not subs:
                del self._subscribers[thread_id]

    async def broadcast(self, thread_id: str, event_data: dict) -> None:
        """Broadcast an event to all subscribers of a thread."""
        subs = self._subscribers.get(thread_id)
        if not subs:
            return

        dead: list = []
        for queue in subs:
            try:
                queue.put_nowait(event_data)
            except asyncio.QueueFull:
                dead.append(queue)
                logger.warning("Dropping slow subscriber for thread %s", thread_id)

        for q in dead:
            subs.discard(q)

    async def stream_events(
        self,
        thread_id: str,
        run_id: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """Yield SSE-formatted events for a thread.

        This is the SSE generator that the Live Stream route returns.
        """
        queue = self.subscribe(thread_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield ": keepalive\n\n"
                    continue

                if event is None:
                    # Sentinel: stream complete
                    break

                # Filter by run_id if specified
                if run_id and event.get("run_id") and event.get("run_id") != run_id:
                    continue

                event_type = event.get("type", "message")
                data = json.dumps(event)
                yield f"event: {event_type}\ndata: {data}\n\n"

                # Check for terminal events
                if event_type in (
                    "agent.run_completed",
                    "agent.run_failed",
                    "completion",
                ):
                    if event.get("complete"):
                        break

        finally:
            self.unsubscribe(thread_id, queue)

    async def run_event_listener(self) -> None:
        """Background task: listen to event bus and route events to subscribers.

        Subscribes to agent.*, hitl.*, task.* event streams.
        """
        event_types = [
            "agent.text_delta",
            "agent.reasoning_delta",
            "agent.completion",
            "agent.tool_result",
            "agent.run_completed",
            "agent.run_failed",
            "hitl.request_created",
            "hitl.request_resolved",
            "task.list_created",
            "task.updated",
            "task.added",
            "task.deleted",
        ]

        # Use Redis pub/sub for real-time event delivery
        pubsub = self._redis.pubsub()
        for event_type in event_types:
            await pubsub.subscribe(f"events:{event_type}")

        logger.info("Stream Projector listening on %d event channels", len(event_types))

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue

                try:
                    envelope = json.loads(message["data"])
                    # EventBus publishes EventEnvelope JSON; extract payload
                    payload = envelope.get("payload", envelope)
                    event_type = envelope.get("event_type", payload.get("type", ""))
                    thread_id = payload.get("thread_id", "")
                    if thread_id:
                        # Merge event_type into payload for downstream consumers
                        broadcast_data = {**payload, "type": event_type}
                        await self.broadcast(thread_id, broadcast_data)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON in event: %s", message["data"][:200])
                except Exception:
                    logger.exception("Error processing event")
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()
