"""Workflow routes — start, query, cancel Temporal workflows."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/workflows", tags=["workflows"])


class StartPipelineRequest(BaseModel):
    pipeline_name: str
    workflow_id: str | None = None


class StartChainRequest(BaseModel):
    code: str
    description: str = ""
    timeout: int = 120
    workflow_id: str | None = None


@router.post("/pipeline")
async def start_pipeline_workflow(
    body: StartPipelineRequest, request: Request
) -> dict[str, str]:
    """Start a durable pipeline workflow via Temporal."""
    temporal = _get_temporal(request)
    store = request.app.state.pipeline_store

    pipeline = await store.load(body.pipeline_name)
    if pipeline is None:
        raise HTTPException(
            status_code=404, detail=f"Pipeline '{body.pipeline_name}' not found"
        )

    wf_id = await temporal.start_pipeline_workflow(
        body.pipeline_name,
        pipeline.to_dict(),
        workflow_id=body.workflow_id,
    )
    return {"workflow_id": wf_id, "status": "started"}


@router.post("/chain")
async def start_chain_workflow(
    body: StartChainRequest, request: Request
) -> dict[str, str]:
    """Start a durable code-chain workflow via Temporal."""
    temporal = _get_temporal(request)
    wf_id = await temporal.start_chain_workflow(
        body.code,
        body.description,
        timeout=body.timeout,
        workflow_id=body.workflow_id,
    )
    return {"workflow_id": wf_id, "status": "started"}


@router.get("/{workflow_id}")
async def query_workflow(workflow_id: str, request: Request) -> dict[str, Any]:
    """Query the current status of a workflow."""
    temporal = _get_temporal(request)
    return await temporal.query_workflow(workflow_id)


@router.get("/{workflow_id}/result")
async def get_workflow_result(workflow_id: str, request: Request) -> Any:
    """Wait for and return the workflow result."""
    temporal = _get_temporal(request)
    return await temporal.get_result(workflow_id)


@router.post("/{workflow_id}/cancel")
async def cancel_workflow(workflow_id: str, request: Request) -> dict[str, str]:
    """Cancel a running workflow."""
    temporal = _get_temporal(request)
    await temporal.cancel_workflow(workflow_id)
    return {"workflow_id": workflow_id, "status": "cancelled"}


@router.post("/{workflow_id}/signal/{signal_name}")
async def signal_workflow(
    workflow_id: str, signal_name: str, request: Request
) -> dict[str, str]:
    """Send a signal to a running workflow."""
    temporal = _get_temporal(request)
    body = (
        await request.json()
        if request.headers.get("content-length", "0") != "0"
        else None
    )
    await temporal.signal_workflow(workflow_id, signal_name, body)
    return {"workflow_id": workflow_id, "signal": signal_name, "status": "sent"}


def _get_temporal(request: Request) -> Any:
    temporal = getattr(request.app.state, "temporal", None)
    if temporal is None:
        raise HTTPException(status_code=503, detail="Temporal client not configured")
    return temporal
