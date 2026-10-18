from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.automation.pipelines.model import (
    PipelineDef,
    PipelineRunResult,
    PipelineValidationResult,
)
from loamz.agents.features.automation.pipelines.service import PipelinesService, pipelines

Service = Annotated[PipelinesService, Depends(use(pipelines))]

router = APIRouter(prefix="/api/v1/pipelines", tags=["pipelines"])


@router.get("", response_model=list[PipelineDef])
@router.get("/", response_model=list[PipelineDef], include_in_schema=False)
async def list_pipelines(caller: CallerDep, service: Service) -> list[PipelineDef]:
    return await service.list(caller)


@router.post("", response_model=dict[str, str])
@router.post("/", response_model=dict[str, str], include_in_schema=False)
async def save_pipeline(body: PipelineDef, caller: CallerDep, service: Service) -> dict[str, str]:
    return await service.save(caller, body)


@router.get("/{name}", response_model=PipelineDef)
async def get_pipeline(name: str, caller: CallerDep, service: Service) -> PipelineDef:
    return await service.get(caller, name)


@router.delete("/{name}", response_model=dict[str, str])
async def delete_pipeline(name: str, caller: CallerDep, service: Service) -> dict[str, str]:
    return await service.delete(caller, name)


@router.post("/{name}/run", response_model=PipelineRunResult)
async def run_pipeline(name: str, caller: CallerDep, service: Service) -> PipelineRunResult:
    return await service.run(caller, name)


@router.post("/{name}/validate", response_model=PipelineValidationResult)
async def validate_pipeline(
    name: str, caller: CallerDep, service: Service
) -> PipelineValidationResult:
    return await service.validate(caller, name)
