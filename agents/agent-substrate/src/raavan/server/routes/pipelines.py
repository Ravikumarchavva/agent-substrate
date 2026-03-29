"""Pipeline CRUD routes for managing saved adapter pipelines."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.get("/")
async def list_pipelines(request: Request) -> list[dict[str, Any]]:
    """List all saved pipeline definitions."""
    store = request.app.state.pipeline_store
    pipelines = await store.list_all()
    return [p.to_dict() for p in pipelines]


@router.get("/{name}")
async def get_pipeline(name: str, request: Request) -> dict[str, Any]:
    """Get a pipeline definition by name."""
    store = request.app.state.pipeline_store
    pipeline = await store.load(name)
    if pipeline is None:
        raise HTTPException(status_code=404, detail=f"Pipeline '{name}' not found")
    return pipeline.to_dict()


@router.post("/")
async def save_pipeline(request: Request) -> dict[str, str]:
    """Save a pipeline definition."""
    body = await request.json()
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Pipeline 'name' is required")

    from raavan.catalog._pipeline import PipelineDef

    pipeline = PipelineDef.from_dict(body)
    store = request.app.state.pipeline_store
    await store.save(pipeline)
    return {"status": "saved", "name": name}


@router.post("/{name}/run")
async def run_pipeline(name: str, request: Request) -> dict[str, Any]:
    """Execute a saved pipeline by name."""
    store = request.app.state.pipeline_store
    pipeline = await store.load(name)
    if pipeline is None:
        raise HTTPException(status_code=404, detail=f"Pipeline '{name}' not found")

    engine = request.app.state.pipeline_engine
    result = await engine.execute(pipeline)
    return {
        "pipeline": name,
        "success": result.success,
        "step_count": len(result.step_results),
        "duration_ms": result.duration_ms,
        "error": result.error,
    }


@router.post("/{name}/validate")
async def validate_pipeline(name: str, request: Request) -> dict[str, Any]:
    """Validate a saved pipeline definition."""
    store = request.app.state.pipeline_store
    pipeline = await store.load(name)
    if pipeline is None:
        raise HTTPException(status_code=404, detail=f"Pipeline '{name}' not found")

    engine = request.app.state.pipeline_engine
    errors = engine.validate(pipeline)
    return {"pipeline": name, "valid": len(errors) == 0, "errors": errors}


@router.delete("/{name}")
async def delete_pipeline(name: str, request: Request) -> dict[str, str]:
    """Delete a pipeline by name."""
    store = request.app.state.pipeline_store
    deleted = await store.delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Pipeline '{name}' not found")
    return {"status": "deleted", "name": name}
