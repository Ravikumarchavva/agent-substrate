"""Invariant register — message delivery (row I12).

A transport that delivers at least once redelivers after the consumer has committed;
that is the normal case, not an edge case. The runtime store absorbs it: a message id
the agent has already processed is refused, not handed over a second time.

The full per-store matrix is the runtime-store conformance suite
(``kernel/testing/conformance/runtime_store.py``, run against SQLite and Postgres);
this row states the guarantee from the engine's side.
"""

from __future__ import annotations

from datetime import datetime, timezone

from substrate.types import Actor
from substrate.runtime import DataPayload, Message
from substrate.runtime import Commit, Complete, Delivery
from substrate.testing.runtime import runtime_store

AGENT = Actor("agent", "a")


def _message() -> Message:
    return Message(target=AGENT, sender=Actor.system("test"), payload=DataPayload(data={"n": 1}))


async def test_redelivery_before_ack_is_deduplicated() -> None:
    """While a message is in flight, redelivering it is a no-op rather than a duplicate."""
    store = runtime_store()
    await store.start()
    try:
        message = _message()
        assert (await store.deliver(Delivery(agent=AGENT, msg=message), wake=False)).accepted is True
        assert (await store.deliver(Delivery(agent=AGENT, msg=message), wake=False)).accepted is False
        assert len(await store.drain(AGENT)) == 1
    finally:
        await store.aclose()


async def test_i12_redelivery_after_ack_is_rejected() -> None:
    """The consumer committed (acked) the message; a later redelivery of the same id must
    not reach the agent again."""
    store = runtime_store()
    await store.start()
    try:
        message = _message()
        await store.deliver(Delivery(agent=AGENT, msg=message))
        (lease,) = await store.lease(worker_id="w", capacity=1, lease_s=30, now=datetime.now(timezone.utc))
        (drained,) = await store.drain(AGENT)
        await store.commit(lease, Commit(ack=(drained.id,), outcome=Complete()))

        redelivered = await store.deliver(Delivery(agent=AGENT, msg=message), wake=False)

        assert redelivered.accepted is False, "an already-processed message id was accepted again"
        assert await store.drain(AGENT) == [], "an already-processed message was handed to the agent a second time"
    finally:
        await store.aclose()
