from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.workspace.artifacts.model import (
    ArtifactCreate,
    ArtifactList,
    ArtifactOut,
    ArtifactPatch,
)
from loamz.agents.features.workspace.artifacts.service import ArtifactsService, artifacts

Service = Annotated[ArtifactsService, Depends(use(artifacts))]

router = APIRouter(prefix="/api/v1/artifacts", tags=["artifacts"])


@router.get("", response_model=ArtifactList)
async def list_artifacts(
    caller: CallerDep,
    service: Service,
    scope: str = Query(default="global"),
    thread_id: str | None = Query(default=None),
    type: str | None = Query(default=None),
    tag: str | None = Query(default=None),
) -> ArtifactList:
    return await service.list(caller, scope=scope, thread_id=thread_id, type=type, tag=tag)


@router.post("", response_model=ArtifactOut)
async def create_artifact(
    body: ArtifactCreate,
    caller: CallerDep,
    service: Service,
    scope: str = Query(default="global"),
    thread_id: str | None = Query(default=None),
) -> ArtifactOut:
    return await service.create(caller, body, scope=scope, thread_id=thread_id)


@router.get("/{slug}", response_model=ArtifactOut)
async def get_artifact(
    slug: str,
    caller: CallerDep,
    service: Service,
    scope: str = Query(default="global"),
    thread_id: str | None = Query(default=None),
) -> ArtifactOut:
    return await service.get(caller, slug, scope=scope, thread_id=thread_id)


@router.patch("/{slug}", response_model=ArtifactOut)
async def update_artifact(
    slug: str,
    body: ArtifactPatch,
    caller: CallerDep,
    service: Service,
    scope: str = Query(default="global"),
    thread_id: str | None = Query(default=None),
) -> ArtifactOut:
    return await service.patch(caller, slug, body, scope=scope, thread_id=thread_id)


@router.delete("/{slug}", status_code=204)
async def delete_artifact(
    slug: str,
    caller: CallerDep,
    service: Service,
    scope: str = Query(default="global"),
    thread_id: str | None = Query(default=None),
) -> Response:
    await service.delete(caller, slug, scope=scope, thread_id=thread_id)
    return Response(status_code=204)


@router.post("/{slug}/promote", response_model=ArtifactOut)
async def promote_artifact(
    slug: str,
    caller: CallerDep,
    service: Service,
    thread_id: str = Query(...),
) -> ArtifactOut:
    return await service.promote(caller, slug, thread_id=thread_id)
