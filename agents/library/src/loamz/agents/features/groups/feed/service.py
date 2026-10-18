from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.groups.feed.model import FeedIn
from loamz.agents.kernel.caller import Caller


class FeedService:
    def __init__(self, container: Container) -> None:
        self._c = container

    async def stream(self, caller: Caller, body: FeedIn) -> AsyncIterator[bytes]:
        # Initial greeting / sync
        init_event = {"type": "sync", "since": body.since}
        yield f"data: {json.dumps(init_event)}\n\n".encode()

        # Keepalive loop
        for _ in range(5):
            await asyncio.sleep(1.0)
            yield b": keepalive\n\n"


def feed(c: Container) -> FeedService:
    return FeedService(c)


FeedServiceDep = Annotated[FeedService, None]

