"""substrate.serve — hand an already-built Agent object to a FastAPI app you
own, the same shape LangServe's ``add_routes(app, runnable)`` or LangGraph
Platform's compiled-graph manifest use. One function, no settings class, no
tenancy/auth/ORM — you build the agent, ``add_routes`` mounts a POST +
SSE-stream endpoint for it.

    from fastapi import FastAPI
    from substrate.agents.core.react import ReActAgent
    from substrate.agents.runtime.runtime import Runtime
    from substrate.serve import add_routes

    app = FastAPI()
    runtime = Runtime()
    agent = ReActAgent("my-agent", model=..., tools=[...])

    @app.on_event("startup")
    async def _start() -> None:
        await runtime.start()

    add_routes(app, runtime, agent, path="/chat")

Built directly on top of ``serving/protocol/`` (the wire-event types) and
``serving/stream/`` (``AgentStreamSession``) — both already free of any
tenant/auth/ORM coupling, confirmed by direct inspection before this module
was written. This is deliberately thinner than ``serving/monolith/routes/
chat.py``: no thread persistence beyond what the Runtime's own EventLog
already gives you, no HITL bridge (pass one yourself via
``AgentStreamSession`` directly if you need approvals), no auth — you add
FastAPI dependencies/middleware for whatever your own app needs, the same
way you would for any other FastAPI route.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    # Deferred, not because fastapi is optional (it's a core dependency —
    # see pyproject.toml), but because Runtime/Agent pull in the full
    # agents/kernel import chain, which this module has no need to force
    # just by being imported (only calling add_routes() does).
    from substrate.agents.runtime.runtime import Runtime
    from substrate.kernel.runtime.agent import Agent


class ChatRequest(BaseModel):
    """Minimal request body for the mounted endpoint."""

    message: str
    thread_id: str | None = None


def add_routes(
    app: FastAPI,
    runtime: Runtime,
    agent: Agent,
    *,
    path: str = "/chat",
) -> None:
    """Mount ``POST {path}`` on *app*: accepts ``ChatRequest``, streams the
    agent's run back as Server-Sent Events (the same wire protocol
    ``serving/protocol/`` defines — ``substrate.serving.protocol.WireEvent``
    JSON per ``data:`` line, terminated by ``data: [DONE]``).

    *agent* is registered with *runtime* lazily, once, on first request (via
    ``AgentStreamSession``/``Runtime.register``, which is itself idempotent)
    — *runtime* must already be started (``await runtime.start()``) before
    the first request arrives; this function does not manage that lifecycle
    for you, since it doesn't own your app's startup/shutdown.
    """
    from substrate.kernel.core.content import ChatMessage, Role, TextBlock
    from substrate.kernel.core.identity import Actor
    from substrate.kernel.messaging.message import ChatPayload, Message
    from substrate.serving.stream.session import AgentStreamSession, sse_lines

    async def _chat(body: ChatRequest, request: Request) -> StreamingResponse:
        message_kwargs: dict[str, Any] = {}
        if body.thread_id is not None:
            # correlation_id has no None-means-"generate one" case of its
            # own (Field(default_factory=...) only applies when the kwarg
            # is omitted entirely) -- omit it outright for a threadless
            # request so Message generates a real uuid4 hex instead.
            message_kwargs["correlation_id"] = body.thread_id
        msg = Message(
            target=agent.id,
            sender=Actor(type="http_proxy"),
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text=body.message)])
            ),
            **message_kwargs,
        )
        session = AgentStreamSession(
            runtime=runtime,
            agent=agent,
            msg=msg,
            is_disconnected=request.is_disconnected,
            thread_id=body.thread_id,
        )

        return StreamingResponse(
            sse_lines(session),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.add_api_route(path, _chat, methods=["POST"])


__all__ = ["add_routes", "ChatRequest"]
