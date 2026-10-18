from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.groups.drives.model import GroupFileList, GroupFileOut
from loamz.agents.features.groups.drives.service import DrivesService, drives

Service = Annotated[DrivesService, Depends(use(drives))]

router = APIRouter(prefix="/api/v1/groups", tags=["groups"])


@router.get("/{group_id}/files", response_model=GroupFileList)
async def list_group_files(group_id: str, caller: CallerDep, service: Service) -> GroupFileList:
    return await service.list_files(caller, group_id)


@router.post("/{group_id}/files", response_model=GroupFileOut)
async def upload_group_file(
    group_id: str,
    caller: CallerDep,
    service: Service,
    file: UploadFile = File(...),
) -> GroupFileOut:
    data = await file.read()
    return await service.upload_file(caller, group_id, file.filename or "file", data)
