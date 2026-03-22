"""Visual Builder — REST + SSE API for pipeline CRUD and execution.

Endpoints:
  GET    /builder/registry               – available tools, skills, guardrail schemas
  POST   /builder/pipelines              – create pipeline
  GET    /builder/pipelines              – list pipelines
  GET    /builder/pipelines/{id}         – get full config
  PUT    /builder/pipelines/{id}         – update config
  DELETE /builder/pipelines/{id}         – delete pipeline
  GET    /builder/pipelines/{id}/export  – download generated Python code
  POST   /builder/pipelines/{id}/run     – SSE stream: build & run pipeline

All routes are conditionally mounted only when ``ENABLE_BUILDER=true``.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from agent_framework.core.pipelines.codegen import generate_code
from agent_framework.core.pipelines.runner import PipelineRunner
from agent_framework.core.pipelines.schema import PipelineConfig
from agent_framework.server.models import Pipeline, PipelineRun

logger = logging.getLogger("agent_framework.server.routes.builder")
router = APIRouter(prefix="/builder", tags=["builder"])


# ── Request / Response schemas ───────────────────────────────────────────────

class PipelineCreate(BaseModel):
    name: str = "Untitled Pipeline"
    description: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)


class PipelineUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None


class PipelineOut(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    config: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class PipelineRunRequest(BaseModel):
    input_text: str = "Hello!"
    session_id: Optional[str] = None


class RegistryTool(BaseModel):
    name: str
    description: str
    risk: str
    hitl_mode: str
    input_schema: Dict[str, Any]


class RegistrySkill(BaseModel):
    name: str
    description: str
    version: str = ""


class RegistryGuardrailSchema(BaseModel):
    name: str
    description: str
    fields: List[Dict[str, str]]


class RegistryMcpServer(BaseModel):
    id: str
    name: str
    url: str = ""
    transport: str = "sse"  # "sse" | "stdio"
    command: str = ""
    args: List[str] = Field(default_factory=list)
    enabled_tools: List[str] = Field(default_factory=list)


class RegistryResponse(BaseModel):
    tools: List[RegistryTool]
    skills: List[RegistrySkill]
    guardrail_schemas: List[RegistryGuardrailSchema]
    models: List[str]
    mcp_servers: List[RegistryMcpServer] = Field(default_factory=list)


# ── GET /builder/registry ────────────────────────────────────────────────────

@router.get("/registry", response_model=RegistryResponse)
async def get_registry(request: Request) -> RegistryResponse:
    """Return available tools, skills, and guardrail schemas for the builder."""

    # Tools from app.state
    tools_list: List[RegistryTool] = []
    for tool in getattr(request.app.state, "tools", []):
        schema = tool.get_schema()
        tools_list.append(RegistryTool(
            name=schema.name,
            description=schema.description,
            risk=schema.risk,
            hitl_mode=schema.hitl_mode,
            input_schema=schema.inputSchema,
        ))

    # Skills — discover from SkillManager
    skills_list: List[RegistrySkill] = []
    try:
        from agent_framework.extensions.skills import SkillManager
        mgr = SkillManager(auto_discover=True)
        for meta in mgr.list_available():
            skills_list.append(RegistrySkill(
                name=meta.name,
                description=meta.description,
                version=getattr(meta, "version", ""),
            ))
    except Exception:
        pass

    # Guardrail schemas
    guardrail_schemas = [
        RegistryGuardrailSchema(
            name="ContentSafetyJudge",
            description="Content safety moderation — checks for harmful content",
            fields=[
                {"name": "safe", "type": "bool", "description": "True if safe"},
                {"name": "reasoning", "type": "str", "description": "Step-by-step reasoning"},
                {"name": "violated_categories", "type": "List[str]", "description": "Violated categories"},
            ],
        ),
        RegistryGuardrailSchema(
            name="RelevanceJudge",
            description="Answer relevance — checks if response is on-topic",
            fields=[
                {"name": "relevant", "type": "bool", "description": "True if relevant"},
                {"name": "score", "type": "float", "description": "Relevance score 0-1"},
                {"name": "reasoning", "type": "str", "description": "Explanation"},
            ],
        ),
    ]

    # Available models
    models = ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "o3-mini"]

    # MCP servers from app.state
    mcp_servers_list: List[RegistryMcpServer] = [
        RegistryMcpServer(**srv)
        for srv in getattr(request.app.state, "mcp_servers", {}).values()
    ]

    return RegistryResponse(
        tools=tools_list,
        skills=skills_list,
        guardrail_schemas=guardrail_schemas,
        models=models,
        mcp_servers=mcp_servers_list,
    )


# ── POST /builder/pipelines ─────────────────────────────────────────────────

@router.post("/pipelines", response_model=PipelineOut, status_code=201)
async def create_pipeline(body: PipelineCreate, request: Request) -> PipelineOut:
    """Create a new pipeline."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        pipeline = Pipeline(
            name=body.name,
            description=body.description,
            config=body.config,
        )
        session.add(pipeline)
        await session.commit()
        await session.refresh(pipeline)
        return PipelineOut(
            id=pipeline.id,
            name=pipeline.name,
            description=pipeline.description,
            config=pipeline.config,
            created_at=pipeline.created_at,
            updated_at=pipeline.updated_at,
        )


# ── GET /builder/pipelines ──────────────────────────────────────────────────

@router.get("/pipelines", response_model=List[PipelineOut])
async def list_pipelines(request: Request) -> List[PipelineOut]:
    """List all pipelines, newest first."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(Pipeline).order_by(Pipeline.created_at.desc())
        )
        pipelines = result.scalars().all()
        return [
            PipelineOut(
                id=p.id,
                name=p.name,
                description=p.description,
                config=p.config,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
            for p in pipelines
        ]


# ── GET /builder/pipelines/{id} ─────────────────────────────────────────────

@router.get("/pipelines/{pipeline_id}", response_model=PipelineOut)
async def get_pipeline(pipeline_id: uuid.UUID, request: Request) -> PipelineOut:
    """Get a single pipeline by ID."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(Pipeline).where(Pipeline.id == pipeline_id)
        )
        pipeline = result.scalar_one_or_none()
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        return PipelineOut(
            id=pipeline.id,
            name=pipeline.name,
            description=pipeline.description,
            config=pipeline.config,
            created_at=pipeline.created_at,
            updated_at=pipeline.updated_at,
        )


# ── PUT /builder/pipelines/{id} ─────────────────────────────────────────────

@router.put("/pipelines/{pipeline_id}", response_model=PipelineOut)
async def update_pipeline(
    pipeline_id: uuid.UUID, body: PipelineUpdate, request: Request
) -> PipelineOut:
    """Update pipeline config / metadata."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(Pipeline).where(Pipeline.id == pipeline_id)
        )
        pipeline = result.scalar_one_or_none()
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")

        if body.name is not None:
            pipeline.name = body.name
        if body.description is not None:
            pipeline.description = body.description
        if body.config is not None:
            pipeline.config = body.config

        await session.commit()
        await session.refresh(pipeline)
        return PipelineOut(
            id=pipeline.id,
            name=pipeline.name,
            description=pipeline.description,
            config=pipeline.config,
            created_at=pipeline.created_at,
            updated_at=pipeline.updated_at,
        )


# ── DELETE /builder/pipelines/{id} ──────────────────────────────────────────

@router.delete("/pipelines/{pipeline_id}", status_code=204)
async def delete_pipeline(pipeline_id: uuid.UUID, request: Request) -> Response:
    """Delete a pipeline and all its runs."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(Pipeline).where(Pipeline.id == pipeline_id)
        )
        pipeline = result.scalar_one_or_none()
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        await session.delete(pipeline)
        await session.commit()
    return Response(status_code=204)


# ── GET /builder/pipelines/{id}/export ───────────────────────────────────────

@router.get("/pipelines/{pipeline_id}/export")
async def export_pipeline(pipeline_id: uuid.UUID, request: Request) -> Response:
    """Download a generated Python module for this pipeline."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(Pipeline).where(Pipeline.id == pipeline_id)
        )
        pipeline = result.scalar_one_or_none()
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")

    pipeline_config = PipelineConfig.model_validate(pipeline.config)
    pipeline_config.name = pipeline.name

    code = generate_code(pipeline_config)
    filename = pipeline.name.lower().replace(" ", "_")[:40] + ".py"

    return Response(
        content=code,
        media_type="text/x-python",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── POST /builder/pipelines/{id}/run ────────────────────────────────────────

@router.post("/pipelines/{pipeline_id}/run")
async def run_pipeline(
    pipeline_id: uuid.UUID,
    body: PipelineRunRequest,
    request: Request,
) -> StreamingResponse:
    """Build the pipeline from config and run it, streaming SSE events."""
    session_factory = request.app.state.session_factory

    # Load pipeline config
    async with session_factory() as session:
        result = await session.execute(
            select(Pipeline).where(Pipeline.id == pipeline_id)
        )
        pipeline = result.scalar_one_or_none()
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        config_dict = pipeline.config

        # Create a run record
        run = PipelineRun(
            pipeline_id=pipeline.id,
            status="running",
            input_text=body.input_text,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    pipeline_config = PipelineConfig.model_validate(config_dict)

    async def sse_stream():
        """Build and execute the pipeline, yielding SSE events."""
        try:
            yield _sse_event("status", {"status": "building", "run_id": str(run_id)})

            runner = PipelineRunner()
            runnable = await runner.build(
                pipeline_config,
                tools_registry=getattr(request.app.state, "tools", []),
                model_client=request.app.state.model_client,
                redis_memory=getattr(request.app.state, "redis_memory", None),
                hitl_bridge=getattr(request.app.state, "bridge", None),
                session_id=body.session_id,
            )

            yield _sse_event("status", {"status": "running"})

            # Execute the pipeline
            if hasattr(runnable, "run_stream"):
                # Agent — stream chunks
                from agent_framework.core.messages._types import (
                    StreamChunk, TextDeltaChunk, ReasoningDeltaChunk, CompletionChunk,
                )
                async for chunk in runnable.run_stream(body.input_text):
                    if isinstance(chunk, TextDeltaChunk):
                        yield _sse_event("text_delta", {"content": chunk.text})
                    elif isinstance(chunk, ReasoningDeltaChunk):
                        yield _sse_event("reasoning_delta", {"content": chunk.text})
                    elif isinstance(chunk, CompletionChunk):
                        # Extract the final text from the AssistantMessage
                        msg = chunk.message
                        content = ""
                        if msg and hasattr(msg, "content") and msg.content:
                            content = msg.content[0] if isinstance(msg.content, list) else str(msg.content)
                        yield _sse_event("completion", {"content": content})
                    elif isinstance(chunk, StreamChunk):
                        yield _sse_event(chunk.type, {"content": str(chunk.data)})
                    elif isinstance(chunk, dict):
                        yield _sse_event(chunk.get("type", "text_delta"), chunk)
                    else:
                        yield _sse_event("text_delta", {"content": str(chunk)})
            elif hasattr(runnable, "route"):
                # Router — single decision
                from agent_framework.core.messages.client_messages import UserMessage
                messages = [UserMessage(content=[{"type": "text", "text": body.input_text}])]
                decision, sub_result = await runnable.route(messages, input_text=body.input_text)
                yield _sse_event("router_decision", {
                    "parsed": str(decision.parsed) if decision.parsed else None,
                    "raw_text": decision.raw_text,
                })
                yield _sse_event("text_delta", {"content": str(sub_result)})
            else:
                # Fallback run
                result = await runnable.run(body.input_text)
                yield _sse_event("text_delta", {"content": result.output})

            # Mark run completed
            async with session_factory() as session:
                await session.execute(
                    update(PipelineRun)
                    .where(PipelineRun.id == run_id)
                    .values(
                        status="completed",
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()

            yield _sse_event("completion", {"message": "Pipeline run complete", "run_id": str(run_id)})

        except Exception as exc:
            logger.exception("Pipeline run failed: %s", exc)

            # Mark run as errored
            try:
                async with session_factory() as session:
                    await session.execute(
                        update(PipelineRun)
                        .where(PipelineRun.id == run_id)
                        .values(
                            status="error",
                            completed_at=datetime.now(timezone.utc),
                            result={"error": str(exc)},
                        )
                    )
                    await session.commit()
            except Exception:
                pass

            yield _sse_event("error", {"message": str(exc)})

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── MCP Server CRUD ──────────────────────────────────────────────────────────

class McpServerCreate(BaseModel):
    name: str
    url: str = ""
    transport: str = "sse"
    command: str = ""
    args: List[str] = Field(default_factory=list)
    enabled_tools: List[str] = Field(default_factory=list)


class McpServerUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    transport: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    enabled_tools: Optional[List[str]] = None


@router.get("/mcp-servers", response_model=List[RegistryMcpServer])
async def list_mcp_servers(request: Request) -> List[RegistryMcpServer]:
    """List all registered MCP server definitions."""
    store: Dict[str, Any] = getattr(request.app.state, "mcp_servers", {})
    return [RegistryMcpServer(**v) for v in store.values()]


@router.post("/mcp-servers", response_model=RegistryMcpServer, status_code=201)
async def create_mcp_server(body: McpServerCreate, request: Request) -> RegistryMcpServer:
    """Register a new MCP server."""
    if not hasattr(request.app.state, "mcp_servers"):
        request.app.state.mcp_servers = {}
    server_id = str(uuid.uuid4())
    entry = RegistryMcpServer(id=server_id, **body.model_dump())
    request.app.state.mcp_servers[server_id] = entry.model_dump()
    return entry


@router.put("/mcp-servers/{server_id}", response_model=RegistryMcpServer)
async def update_mcp_server(
    server_id: str, body: McpServerUpdate, request: Request
) -> RegistryMcpServer:
    """Update an existing MCP server definition."""
    store: Dict[str, Any] = getattr(request.app.state, "mcp_servers", {})
    entry = store.get(server_id)
    if not entry:
        raise HTTPException(status_code=404, detail="MCP server not found")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    entry.update(updates)
    store[server_id] = entry
    return RegistryMcpServer(**entry)


@router.delete("/mcp-servers/{server_id}", status_code=204)
async def delete_mcp_server(server_id: str, request: Request) -> Response:
    """Delete an MCP server definition."""
    store: Dict[str, Any] = getattr(request.app.state, "mcp_servers", {})
    if server_id not in store:
        raise HTTPException(status_code=404, detail="MCP server not found")
    del store[server_id]
    return Response(status_code=204)


# ── SSE helpers ──────────────────────────────────────────────────────────────

def _sse_event(event_type: str, data: Any) -> str:
    """Format a single SSE event."""
    payload = json.dumps({"type": event_type, **data} if isinstance(data, dict) else {"type": event_type, "data": data})
    return f"data: {payload}\n\n"
