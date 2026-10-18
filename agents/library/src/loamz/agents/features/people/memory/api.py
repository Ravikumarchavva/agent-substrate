"""What the assistant remembers about the caller."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.people.memory.model import MemoryIn, MemoryList, MemoryOut
from loamz.agents.features.people.memory.service import MemoryService, memory

Service = Annotated[MemoryService, Depends(use(memory))]

router = APIRouter(prefix="/api/v1/me/memories", tags=["memory"])


@router.get("", response_model=MemoryList)
async def list_memories(caller: CallerDep, service: Service) -> MemoryList:
    return await service.list(caller)


@router.post("", response_model=MemoryOut, status_code=201)
async def add_memory(body: MemoryIn, caller: CallerDep, service: Service) -> MemoryOut:
    return await service.add(caller, body)


@router.patch("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: str, body: MemoryIn, caller: CallerDep, service: Service
) -> MemoryOut:
    return await service.update(caller, memory_id, body)


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(memory_id: str, caller: CallerDep, service: Service) -> Response:
    await service.delete(caller, memory_id)
    return Response(status_code=204)
