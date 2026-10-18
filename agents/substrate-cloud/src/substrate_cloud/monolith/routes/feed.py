"""``POST /feed``: one open stream with everything happening in the conversations you are in.

The client says where it is (``since``: per conversation, the last ``seq`` it holds) and gets the rest, then each new entry and who is typing as it
happens. It replaces asking again and again; see ``realtime/feed.py`` for what can be lost (nothing: the stream is a convenience over the record).
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncIterator, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from substrate_cloud.monolith.database import system_session
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.routes.groups import entry_out
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.services.groups import service as groups
from substrate_cloud.realtime.chats import chats_for
from substrate_cloud.realtime.feed import Chat, feed_events

router = APIRouter(tags=["feed"])


class FeedIn(BaseModel):
    # Each conversation's id, to the last entry the client has of it; the feed sends what came after.
    since: Dict[str, int] = Field(default_factory=dict)
    # An agent whose account you are viewing: what its pairs say reaches you live too (as ``pair-<id>``).
    watch: Optional[uuid.UUID] = None


@router.post("/feed")
async def open_feed(
    body: FeedIn,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    if ctx.runtime is None:
        raise HTTPException(503, "The runtime is not available.")
    store = ctx.runtime.store
    me = groups.user_actor(user.sub)
    mine = str(me)
    factory = request.app.state.session_factory

    async def load() -> dict[str, Chat]:
        # A short session of its own: a connection that stays open for as long as the person is here must not hold a database connection.
        async with system_session(factory) as db:
            return await chats_for(db, user, body.watch)

    async def stream() -> AsyncIterator[bytes]:
        async for event in feed_events(
            me=me,
            since={k: v for k, v in body.since.items()},
            store=store,
            hub=request.app.state.feed,
            load=load,
            render=lambda entry, chat: entry_out(entry, chat.names, mine).model_dump(
                mode="json"
            ),
            read_position=lambda channel: groups.read_by_all(store, channel),
            gone=request.is_disconnected,
        ):
            yield (
                b": keepalive\n\n"
                if event is None
                else f"data: {json.dumps(event)}\n\n".encode()
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
