"""Redis Streams event backbone for async inter-service communication.

Concrete implementation of the legacy ``EventBus`` API.
Lives in ``integrations`` because it is a Redis-backed adapter.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Optional, cast

import redis.asyncio as aioredis
from opentelemetry import trace
from opentelemetry.propagate import extract, inject

from ravi.logger import setup_logging
from ravi.shared.events.envelope import EventEnvelope

logger = setup_logging()


class EventBus:
    """Redis Streams event bus for publishing and consuming domain events."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self._redis_url = redis_url
        self._client: Optional[aioredis.Redis] = None
        self._consumer_id = f"consumer-{id(self)}"

    async def connect(self) -> None:
        self._client = aioredis.from_url(
            self._redis_url,
            decode_responses=True,
        )

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def publish(self, event: EventEnvelope) -> str:
        """Publish an event to Redis Stream and fan out through pub/sub."""
        if not self._client:
            raise RuntimeError("EventBus not connected")

        carrier: dict[str, str] = {}
        inject(carrier)
        if carrier:
            event = event.model_copy(update={"trace_context": carrier})

        stream_key = event.stream_key()
        json_data = event.model_dump_json()
        data = cast(Any, {"envelope": json_data})
        msg_id: str = await self._client.xadd(stream_key, data)

        await self._client.publish(stream_key, json_data)

        logger.debug("Published %s to %s (id=%s)", event.event_type, stream_key, msg_id)
        return msg_id

    async def ensure_group(self, stream_key: str, group: str) -> None:
        """Create a consumer group if it doesn't exist."""
        if not self._client:
            raise RuntimeError("EventBus not connected")
        try:
            await self._client.xgroup_create(
                stream_key,
                group,
                id="0",
                mkstream=True,
            )
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def subscribe(
        self,
        event_type: str,
        group: str,
        batch_size: int = 10,
        block_ms: int = 1000,
    ) -> AsyncIterator[EventEnvelope]:
        """Consume events from a stream using a consumer group."""
        if not self._client:
            raise RuntimeError("EventBus not connected")

        stream_key = f"events:{event_type}"
        await self.ensure_group(stream_key, group)

        while True:
            try:
                messages = await self._client.xreadgroup(
                    groupname=group,
                    consumername=self._consumer_id,
                    streams={stream_key: ">"},
                    count=batch_size,
                    block=block_ms,
                )

                if not messages:
                    continue

                for stream, entries in messages:
                    for msg_id, data in entries:
                        try:
                            envelope = EventEnvelope.model_validate_json(
                                data["envelope"]
                            )
                            parent_ctx = extract(envelope.trace_context)
                            tracer = trace.get_tracer("ravi.events")
                            with tracer.start_as_current_span(
                                f"consume:{envelope.event_type}",
                                context=parent_ctx,
                                kind=trace.SpanKind.CONSUMER,
                                attributes={
                                    "messaging.event_type": envelope.event_type
                                },
                            ):
                                yield envelope
                            await self._client.xack(stream_key, group, msg_id)
                        except Exception:
                            logger.exception("Failed to process event %s", msg_id)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Event consumer error, retrying in 1s")
                await asyncio.sleep(1)

    async def publish_many(self, events: list[EventEnvelope]) -> list[str]:
        """Publish multiple events atomically via Redis pipeline."""
        if not self._client:
            raise RuntimeError("EventBus not connected")

        async with self._client.pipeline() as pipe:
            for event in events:
                pipe.xadd(event.stream_key(), {"envelope": event.model_dump_json()})
            results = await pipe.execute()
        return results
