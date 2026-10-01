"""Invariant register — message delivery (row I12).

The inbox contract promises "exactly-once delivery tracking (dedup by
Message.id)" so that an at-least-once transport can redeliver freely. It holds
only until the message is acked: both backends delete the row on ack, so the
next redelivery of an id the agent has already processed is accepted and
handed to the agent a second time.

Parametrized over every implementation, which is the shape all the conformance
suites take from step 3 onward: one set of assertions, every backend.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from substrate.kernel.runtime.backends._inbox import InMemoryInbox
from substrate.kernel.runtime.backends._local_db import LocalRuntimeDB
from substrate.kernel.runtime.backends._local_inbox import LocalInbox
from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.messaging.message import DataPayload, Message

InboxFactory = Callable[[], AsyncIterator[object]]


@pytest.fixture(params=["in_memory", "local_sqlite"])
def inbox(request: pytest.FixtureRequest, tmp_path: Path) -> object:
    """Every InboxProtocol implementation the kernel ships."""
    if request.param == "in_memory":
        return InMemoryInbox()
    return LocalInbox(LocalRuntimeDB(tmp_path / "runtime.sqlite3"))


def _message(target: Actor) -> Message:
    return Message(target=target, sender=Actor.system("test"), payload=DataPayload(data={"n": 1}))


async def test_redelivery_before_ack_is_deduplicated(inbox: object) -> None:
    """The half that works: while a message is in flight, redelivering it is a
    no-op rather than a duplicate."""
    agent = Actor("agent", "a")
    message = _message(agent)

    assert await inbox.deliver(agent, message, notify=False) is True  # type: ignore[attr-defined]
    assert await inbox.deliver(agent, message, notify=False) is False  # type: ignore[attr-defined]
    drained = await inbox.drain(agent)  # type: ignore[attr-defined]
    assert len(drained) == 1


@pytest.mark.xfail(
    strict=True,
    reason="I12: ack deletes the dedup record, so a transport redelivering an "
    "already-processed message id gets it accepted and the agent handles it "
    "twice. Fixed in step 3 (processed watermark in the commit).",
)
async def test_i12_redelivery_after_ack_is_rejected(inbox: object) -> None:
    """At-least-once transports redeliver after the consumer has committed —
    that is the normal case, not an edge case. The inbox is what absorbs it."""
    agent = Actor("agent", "a")
    message = _message(agent)

    await inbox.deliver(agent, message, notify=False)  # type: ignore[attr-defined]
    drained = await inbox.drain(agent)  # type: ignore[attr-defined]
    await inbox.ack(agent, drained[0].id)  # type: ignore[attr-defined]

    accepted = await inbox.deliver(agent, message, notify=False)  # type: ignore[attr-defined]
    redrained = await inbox.drain(agent)  # type: ignore[attr-defined]

    assert accepted is False, "an already-processed message id was accepted again"
    assert redrained == [], (
        "an already-processed message was handed to the agent a second time"
    )
