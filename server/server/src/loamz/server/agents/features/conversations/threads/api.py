from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response

from loamz.server.agents.bootstrap.http import CallerDep, ContainerDep, use
from loamz.server.agents.features.conversations.threads.model import (
    MessageEvents,
    RunDetails,
    SearchResults,
    ThreadCreate,
    ThreadList,
    ThreadOut,
    ThreadUpdate,
)
from loamz.server.agents.features.conversations.threads.service import ThreadsService, threads

Service = Annotated[ThreadsService, Depends(use(threads))]

router = APIRouter(prefix="/api/v1/threads", tags=["threads"])


@router.post("", response_model=ThreadOut, status_code=status.HTTP_201_CREATED)
async def create_thread(
    body: ThreadCreate, caller: CallerDep, service: Service, container: ContainerDep
) -> ThreadOut:
    """A new conversation owned by the caller. With `agent_id` it is that agent's one conversation, opened."""
    open_agent_thread = None
    if body.agent_id is not None:
        from loamz.server.agents.features.directory.agents.service import agents

        open_agent_thread = container.get(agents).open_thread
    header = await service.create(caller, body, agent_thread=open_agent_thread)
    return service.out(header)


@router.get("", response_model=ThreadList)
async def list_threads(
    caller: CallerDep,
    service: Service,
    archived: bool = False,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    page_token: str | None = None,
) -> ThreadList:
    """The caller's threads, pinned first then newest first. `archived=true` lists the archive instead."""
    return await service.list(caller, archived=archived, page_size=page_size, page_token=page_token)


@router.get("/search", response_model=SearchResults)
async def search_threads(
    caller: CallerDep,
    service: Service,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SearchResults:
    """Messages containing `q` in the caller's own conversations (archived included), newest first."""
    return await service.search(caller, q, limit)


@router.get("/{thread_id}", response_model=ThreadOut)
async def get_thread(thread_id: str, caller: CallerDep, service: Service) -> ThreadOut:
    return await service.get(caller, thread_id)


@router.patch("/{thread_id}", response_model=ThreadOut)
async def update_thread(
    thread_id: str, body: ThreadUpdate, caller: CallerDep, service: Service
) -> ThreadOut:
    """Rename, tag, pin or archive. An agent's conversation keeps the agent's name."""
    return await service.update(caller, thread_id, body)


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(thread_id: str, caller: CallerDep, service: Service) -> Response:
    await service.delete(caller, thread_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{thread_id}/messages", response_model=MessageEvents)
async def get_thread_messages(thread_id: str, caller: CallerDep, service: Service) -> MessageEvents:
    """The conversation as wire events, projected from the run journal (the same vocabulary as the live stream)."""
    return await service.messages(caller, thread_id)


@router.get("/{thread_id}/export")
async def export_thread(
    thread_id: str,
    caller: CallerDep,
    service: Service,
    format: Annotated[str, Query(pattern="^(md|json)$")] = "md",
) -> Response:
    """Download the conversation as Markdown or JSON: only what was said, not tool traces."""
    body, media, name = await service.export(caller, thread_id, format)
    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{thread_id}/runs", response_model=RunDetails)
async def get_thread_runs(thread_id: str, caller: CallerDep, service: Service) -> RunDetails:
    """What the conversation's runs did: time, model calls, tokens, cost, tools and their outcome."""
    return RunDetails(items=await service.runs(caller, thread_id))
