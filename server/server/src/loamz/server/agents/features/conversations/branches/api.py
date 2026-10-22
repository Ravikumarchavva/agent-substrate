from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.conversations.branches.model import (
    BranchForkRequest,
    BranchList,
    BranchMessages,
    BranchOut,
    BranchRenameRequest,
    CheckpointCreateRequest,
    CheckpointList,
    CheckpointOut,
)
from loamz.server.agents.features.conversations.branches.service import BranchesService, branches

Service = Annotated[BranchesService, Depends(use(branches))]

router = APIRouter(prefix="/api/v1/threads/{thread_id}", tags=["branches"])


@router.get("/branches", response_model=BranchList)
async def list_branches(thread_id: str, caller: CallerDep, service: Service) -> BranchList:
    return await service.list(caller, thread_id)


@router.post("/branches:fork", response_model=BranchOut, status_code=status.HTTP_201_CREATED)
async def fork_branch(
    thread_id: str, body: BranchForkRequest, caller: CallerDep, service: Service
) -> BranchOut:
    return await service.fork(caller, thread_id, body)


@router.get("/branches/{branch_id}", response_model=BranchOut)
async def get_branch(thread_id: str, branch_id: str, caller: CallerDep, service: Service):
    return await service.get(caller, thread_id, branch_id)


@router.patch("/branches/{branch_id}", response_model=BranchOut)
async def rename_branch(
    thread_id: str, branch_id: str, body: BranchRenameRequest, caller: CallerDep, service: Service
):
    return await service.rename(caller, thread_id, branch_id, body.name)


@router.delete("/branches/{branch_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_branch(
    thread_id: str, branch_id: str, caller: CallerDep, service: Service
) -> Response:
    await service.delete(caller, thread_id, branch_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/branches/{branch_id}/messages", response_model=BranchMessages)
async def get_branch_messages(thread_id: str, branch_id: str, caller: CallerDep, service: Service):
    return await service.messages(caller, thread_id, branch_id)


@router.get("/branches/{branch_id}/checkpoints", response_model=CheckpointList)
async def list_checkpoints(thread_id: str, branch_id: str, caller: CallerDep, service: Service):
    return await service.checkpoints(caller, thread_id)


@router.post(
    "/branches/{branch_id}/checkpoints",
    response_model=CheckpointOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_checkpoint(
    thread_id: str,
    branch_id: str,
    body: CheckpointCreateRequest,
    caller: CallerDep,
    service: Service,
):
    return await service.checkpoint(caller, thread_id, body)
