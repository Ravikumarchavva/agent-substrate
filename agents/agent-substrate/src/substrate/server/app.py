"""``create_app(agent)`` — an agent object in, a FastAPI app out.

::

    from substrate.server import create_app

    app = create_app(my_agent, store="./.substrate")          # uvicorn my_module:app

You pass instances, as everywhere in the library: the agent (or a function that builds it), the store, and — if you
want them — the two small hooks that make it yours, ``tenant_of`` and ``identity_of``. There is no settings class and
nothing selects a backend by name. Auth is FastAPI's own: pass ``dependencies=[Depends(check_token)]``.

Routes:

=========================================  ===============================================================
``POST /chat``                             ``{message, thread_id?}`` → the run as SSE wire events
``POST /agui``                             an AG-UI ``RunAgentInput`` → the run as AG-UI events (``agui.py``)
``GET  /runs/{run_id}/events``             follow a run again — SSE, ``?from_seq=`` to resume where you were
``POST /runs/{run_id}/cancel``             cancel a run and everything it spawned
``GET  /runs/{run_id}/approvals``          the approvals the run is waiting on
``POST /runs/{run_id}/approvals/{id}``     ``{decision, reason?, modified_args?}`` — answer one
``GET  /health``                           liveness
=========================================  ===============================================================

A run belongs to the worker that leased it, not to the HTTP connection: closing the connection detaches, it does not
cancel, and ``/runs/{id}/events`` picks it up again. A thread has one active run at a time (a second ``/chat`` on it is a
409). Every ``/runs/...`` route is confined to the caller's tenant.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from substrate.runtime import Runtime
from substrate.server import agui, streaming
from substrate.server.protocol import (
    ApprovalRequestedEvent,
    HelloEvent,
    RunCancelledEvent,
    RunCompletedEvent,
    RunFailedEvent,
    WireEvent,
    wire_from_log,
)
from substrate.stores import Store
from substrate.tools import ApprovalDecision
from substrate.types import RunLogKind, ThreadBusyError

_SSE = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
KEEPALIVE_S = 15.0

AgentSource = Any  # an agent, or a function (sync or async) that builds one
Hook = Callable[[Request], str]


class ChatRequest(BaseModel):
    message: str
    thread_id: str | None = None


class DecisionRequest(BaseModel):
    decision: ApprovalDecision
    reason: str | None = None
    modified_args: dict[str, Any] | None = None


def _frame(event: WireEvent) -> str:
    return f"data: {event.model_dump_json(exclude_none=True)}\n\n"


def _terminal(entry_kind: str, payload: dict[str, Any]) -> WireEvent:
    if entry_kind == RunLogKind.RUN_COMPLETED:
        return RunCompletedEvent()
    if entry_kind == RunLogKind.RUN_CANCELLED:
        return RunCancelledEvent()
    return RunFailedEvent(error=payload.get("error", "agent run failed"))


def create_app(
    agent: AgentSource,
    *,
    store: Store | str | Path | None = None,
    runtime: Runtime | None = None,
    title: str = "Substrate agent",
    tenant_of: Hook | None = None,
    identity_of: Hook | None = None,
    dependencies: Sequence[Any] = (),
    cors_origins: Sequence[str] | None = None,
    chat_path: str = "/chat",
    agui_path: str | None = "/agui",
    keepalive_s: float = KEEPALIVE_S,
) -> FastAPI:
    """Build the app. ``agent`` is the agent, or a function (sync or async) called once at startup to build it.

    ``store`` is a ``Store`` or a folder (default ``./.substrate``); pass ``runtime`` instead to bring your own, in which
    case you own its lifecycle only if you started it already. ``tenant_of(request)`` names the tenant a request acts for
    (default ``"default"``); ``identity_of(request)`` names who is deciding an approval — journaled with the decision, and
    taken from here, never from the request body, so wire it to your auth. ``agui_path=None`` leaves AG-UI off.
    """
    if runtime is not None and store is not None:
        raise ValueError("pass a store or a runtime, not both")
    tenant = tenant_of or (lambda request: "default")
    identity = identity_of or (lambda request: "anonymous")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        rt = runtime or Runtime(store if isinstance(store, Store) else Store.at(store or "./.substrate"))
        built = agent
        if callable(built) and not hasattr(built, "id"):  # a factory (an agent has an ``id``)
            built = built()
            if inspect.isawaitable(built):
                built = await built
        owns = runtime is None
        if owns:
            await rt.start()
        await rt.register(built)
        app.state.runtime, app.state.agent = rt, built
        try:
            yield
        finally:
            if owns:
                await rt.stop()

    app = FastAPI(title=title, lifespan=lifespan)
    if cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(cors_origins), allow_methods=["*"], allow_headers=["*"])

    router = APIRouter(dependencies=list(dependencies))

    def rt_of(request: Request) -> Runtime:
        return request.app.state.runtime

    async def owned_run(request: Request, run_id: str) -> Any:
        run = await rt_of(request).get_run(run_id)
        if run is None or run.tenant != tenant(request):  # another tenant's run is indistinguishable from none
            raise HTTPException(404, "no such run")
        return run

    async def kept_alive(events: AsyncIterator[str]) -> AsyncIterator[str]:
        async for frame in streaming.with_keepalive(events, keepalive_s):
            yield ": keepalive\n\n" if frame is None else frame

    async def begin(request: Request, prompt: str, thread: str | None) -> str:
        try:
            return await streaming.start(rt_of(request), request.app.state.agent, prompt, thread=thread, tenant=tenant(request))
        except ThreadBusyError:
            raise HTTPException(409, f"thread {thread!r} already has an active run") from None

    # -- SSE wire protocol -----------------------------------------------------------------------------------------

    async def wire_frames(request: Request, run_id: str, from_seq: int, *, hello: bool) -> AsyncIterator[str]:
        if hello:
            yield _frame(HelloEvent())
        replay = await streaming.is_finished(rt_of(request), run_id)  # a finished run has no token deltas, only messages
        terminal = False
        async for entry in streaming.follow(rt_of(request), run_id, from_seq=from_seq):
            payload = entry.payload or {}
            if entry.kind in streaming.TERMINAL:
                terminal = True
                yield _frame(_terminal(entry.kind, payload))
                break
            wire = wire_from_log(entry.kind, payload, history=replay)
            if wire is not None:
                yield _frame(wire)
        if not terminal and replay:  # resumed past the end: say how it ended
            run = await rt_of(request).get_run(run_id)
            yield _frame(RunCompletedEvent() if run and run.status.value == "completed" else RunCancelledEvent() if run and run.status.value == "cancelled" else RunFailedEvent(error="run failed"))
        yield "data: [DONE]\n\n"

    @router.post(chat_path)
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        run_id = await begin(request, body.message, body.thread_id)
        return StreamingResponse(
            kept_alive(wire_frames(request, run_id, 0, hello=True)),
            media_type="text/event-stream",
            headers={**_SSE, "X-Run-Id": run_id},
        )

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request, from_seq: int = 0) -> StreamingResponse:
        await owned_run(request, run_id)
        return StreamingResponse(
            kept_alive(wire_frames(request, run_id, from_seq, hello=True)), media_type="text/event-stream", headers=_SSE
        )

    # -- AG-UI -----------------------------------------------------------------------------------------------------

    if agui_path is not None:

        @router.post(agui_path)
        async def agui_run(body: agui.RunAgentInput, request: Request) -> StreamingResponse:
            prompt = body.prompt()
            if prompt is None:
                raise HTTPException(422, "RunAgentInput has no user message to run")
            run_id = await begin(request, prompt, body.thread_id)
            translator = agui.Translator(thread_id=body.thread_id, run_id=body.run_id or run_id, engine_run_id=run_id)

            async def frames() -> AsyncIterator[str]:
                for event in translator.start():
                    yield agui.encode(event)
                async for entry in streaming.follow(rt_of(request), run_id):
                    for event in translator.translate(entry):
                        yield agui.encode(event)

            return StreamingResponse(
                kept_alive(frames()), media_type="text/event-stream", headers={**_SSE, "X-Run-Id": run_id}
            )

    # -- control and approvals -------------------------------------------------------------------------------------

    @router.post("/runs/{run_id}/cancel")
    async def cancel(run_id: str, request: Request) -> dict[str, Any]:
        await owned_run(request, run_id)
        return {"cancelled": [str(r) for r in await rt_of(request).cancel(run_id, reason="user_requested")]}

    @router.get("/runs/{run_id}/approvals")
    async def approvals(run_id: str, request: Request) -> list[dict[str, Any]]:
        await owned_run(request, run_id)
        return [
            ApprovalRequestedEvent(request_id=p.request_id, tool_name=p.tool_name, args=p.args, risk=p.risk, summary=p.summary).model_dump()
            for p in await rt_of(request).pending_approvals(run_id)
        ]

    @router.post("/runs/{run_id}/approvals/{request_id}")
    async def decide(run_id: str, request_id: str, body: DecisionRequest, request: Request) -> dict[str, str]:
        await owned_run(request, run_id)
        rt = rt_of(request)
        if request_id not in {p.request_id for p in await rt.pending_approvals(run_id)}:
            raise HTTPException(404, "no such pending approval")
        try:
            await rt.decide(
                run_id, request_id, body.decision, by=identity(request), reason=body.reason, modified_args=body.modified_args
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"status": "recorded", "decided_by": identity(request)}

    app.include_router(router)

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


__all__ = ["ChatRequest", "create_app"]
