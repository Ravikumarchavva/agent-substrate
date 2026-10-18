from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.directory.agents.model import (
    AgentIn,
    AgentList,
    AgentOut,
    AgentPatch,
    ContactList,
    ContactsIn,
    ThreadRef,
    ToolInfo,
    ToolList,
    ViewChats,
    ViewMessages,
)
from loamz.agents.features.directory.agents.service import AgentsService, agents

Service = Annotated[AgentsService, Depends(use(agents))]

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.get("", response_model=AgentList)
async def list_my_agents(caller: CallerDep, service: Service) -> AgentList:
    return await service.list(caller)


@router.post("", response_model=AgentOut)
async def create_agent(body: AgentIn, caller: CallerDep, service: Service) -> AgentOut:
    return await service.create(caller, body)


@router.get("/tools", response_model=ToolList)
async def available_tools(caller: CallerDep, service: Service) -> ToolList:
    tools = service.known_tools()
    return ToolList(items=[ToolInfo(name=k, description=v) for k, v in sorted(tools.items())])


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, caller: CallerDep, service: Service) -> AgentOut:
    return await service.get(caller, agent_id)


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str, body: AgentPatch, caller: CallerDep, service: Service
) -> AgentOut:
    return await service.patch(caller, agent_id, body)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, caller: CallerDep, service: Service) -> Response:
    await service.delete(caller, agent_id)
    return Response(status_code=204)


@router.put("/{agent_id}/avatar", response_model=AgentOut)
async def set_avatar(
    agent_id: str, caller: CallerDep, service: Service, file: UploadFile = File(...)
) -> AgentOut:
    data = await file.read()
    return await service.set_avatar(caller, agent_id, data)


@router.delete("/{agent_id}/avatar", response_model=AgentOut)
async def clear_avatar(agent_id: str, caller: CallerDep, service: Service) -> AgentOut:
    return await service.clear_avatar(caller, agent_id)


@router.put("/{agent_id}/pin", response_model=AgentOut)
async def pin_agent(agent_id: str, caller: CallerDep, service: Service) -> AgentOut:
    return await service.set_pinned(caller, agent_id, True)


@router.delete("/{agent_id}/pin", response_model=AgentOut)
async def unpin_agent(agent_id: str, caller: CallerDep, service: Service) -> AgentOut:
    return await service.set_pinned(caller, agent_id, False)


@router.get("/{agent_id}/contacts", response_model=ContactList)
async def get_contacts(agent_id: str, caller: CallerDep, service: Service) -> ContactList:
    return await service.get_contacts(caller, agent_id)


@router.put("/{agent_id}/contacts", response_model=ContactList)
async def put_contacts(
    agent_id: str, body: ContactsIn, caller: CallerDep, service: Service
) -> ContactList:
    return await service.put_contacts(caller, agent_id, body.contacts)


@router.post("/{agent_id}/thread", response_model=ThreadRef)
async def open_agent_thread(agent_id: str, caller: CallerDep, service: Service) -> ThreadRef:
    header = await service.open_thread(caller, agent_id)
    return ThreadRef(id=header.id)


@router.get("/{agent_id}/view/chats", response_model=ViewChats)
async def view_chats(
    agent_id: str,
    caller: CallerDep,
    service: Service,
    q: str = "",
    kind: str = "all",
    before: datetime | None = None,
    limit: int = Query(default=30, ge=1, le=100),
) -> ViewChats:
    return await service.view_chats(caller, agent_id, q=q, kind=kind, before=before, limit=limit)


@router.get("/{agent_id}/view/chats/{key}/messages", response_model=ViewMessages)
async def view_messages(
    agent_id: str,
    key: str,
    caller: CallerDep,
    service: Service,
    before: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> ViewMessages:
    return await service.view_messages(caller, agent_id, key, before=before, limit=limit)
