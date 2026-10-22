from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.groups.groups.model import (
    GroupIn,
    GroupList,
    GroupOut,
    GroupPatch,
    MemberIn,
    MessageList,
    MessageOut,
    MessagePatch,
    MessageReactionIn,
    SendMessageIn,
)
from loamz.server.agents.features.groups.groups.service import GroupsService, groups

Service = Annotated[GroupsService, Depends(use(groups))]

router = APIRouter(prefix="/api/v1/groups", tags=["groups"])


@router.get("", response_model=GroupList)
async def list_my_groups(caller: CallerDep, service: Service) -> GroupList:
    return await service.list(caller)


@router.post("", response_model=GroupOut)
async def create_group(body: GroupIn, caller: CallerDep, service: Service) -> GroupOut:
    return await service.create(caller, body)


@router.get("/{group_id}", response_model=GroupOut)
async def get_group(group_id: str, caller: CallerDep, service: Service) -> GroupOut:
    return await service.get(caller, group_id)


@router.patch("/{group_id}", response_model=GroupOut)
async def rename_group(
    group_id: str, body: GroupPatch, caller: CallerDep, service: Service
) -> GroupOut:
    return await service.patch(caller, group_id, body)


@router.delete("/{group_id}", status_code=204)
async def delete_group(group_id: str, caller: CallerDep, service: Service) -> Response:
    await service.delete(caller, group_id)
    return Response(status_code=204)


@router.put("/{group_id}/avatar", response_model=GroupOut)
async def set_group_avatar(
    group_id: str, caller: CallerDep, service: Service, file: UploadFile = File(...)
) -> GroupOut:
    data = await file.read()
    return await service.set_avatar(caller, group_id, data)


@router.delete("/{group_id}/avatar", response_model=GroupOut)
async def clear_group_avatar(group_id: str, caller: CallerDep, service: Service) -> GroupOut:
    return await service.clear_avatar(caller, group_id)


@router.put("/{group_id}/pin", response_model=GroupOut)
async def pin_group(group_id: str, caller: CallerDep, service: Service) -> GroupOut:
    return await service.pin(caller, group_id, True)


@router.delete("/{group_id}/pin", response_model=GroupOut)
async def unpin_group(group_id: str, caller: CallerDep, service: Service) -> GroupOut:
    return await service.pin(caller, group_id, False)


@router.post("/{group_id}/members", response_model=GroupOut)
async def add_member(
    group_id: str, body: MemberIn, caller: CallerDep, service: Service
) -> GroupOut:
    return await service.add_member(caller, group_id, body.agent_id, body.mode)


@router.delete("/{group_id}/members/{agent_id}", response_model=GroupOut)
async def remove_member(
    group_id: str, agent_id: str, caller: CallerDep, service: Service
) -> GroupOut:
    return await service.remove_member(caller, group_id, agent_id)


@router.patch("/{group_id}/members/{agent_id}", response_model=GroupOut)
async def set_member_mode(
    group_id: str, agent_id: str, body: MemberIn, caller: CallerDep, service: Service
) -> GroupOut:
    return await service.set_member_mode(caller, group_id, agent_id, body.mode)


@router.get("/{group_id}/messages", response_model=MessageList)
async def read_messages(
    group_id: str,
    caller: CallerDep,
    service: Service,
    before: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> MessageList:
    return await service.read_messages(caller, group_id, before=before, limit=limit)


@router.post("/{group_id}/messages", response_model=MessageOut)
async def send_message(
    group_id: str, body: SendMessageIn, caller: CallerDep, service: Service
) -> MessageOut:
    return await service.send_message(caller, group_id, body.text, body.attachments)


@router.delete("/{group_id}/messages/{seq}", status_code=204)
async def delete_message(group_id: str, seq: int, caller: CallerDep, service: Service) -> Response:
    await service.delete_message(caller, group_id, seq)
    return Response(status_code=204)


@router.patch("/{group_id}/messages/{seq}", response_model=MessageOut)
async def edit_message(
    group_id: str, seq: int, body: MessagePatch, caller: CallerDep, service: Service
) -> MessageOut:
    return await service.edit_message(caller, group_id, seq, body.text)


@router.put("/{group_id}/messages/{seq}/reaction", status_code=204)
async def react_to_message(
    group_id: str, seq: int, body: MessageReactionIn, caller: CallerDep, service: Service
) -> Response:
    await service.react(caller, group_id, seq, body.reaction)
    return Response(status_code=204)


@router.post("/{group_id}/read", status_code=204)
async def mark_read(group_id: str, caller: CallerDep, service: Service) -> Response:
    await service.mark_read(caller, group_id)
    return Response(status_code=204)
