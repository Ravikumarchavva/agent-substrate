"""Agent profiles — ``/agents``.

An agent is a saved role with standing instructions, the tools it may use and a workspace of its own that persists across its conversations.
Start a conversation with one by passing ``agent_id`` to ``POST /threads``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.models import Agent
from substrate_cloud.monolith.routes.chat_intents import _tool_name
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.monolith.services.delegation import TOOL_NAME
from substrate_cloud.monolith.services.agent_service import (
    MAX_AGENTS_PER_USER,
    get_owned_agent,
    list_agents,
)

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    role: str = Field(default="", max_length=160)
    instructions: str = Field(default="", max_length=8000)
    # ``None``: every tool the deployment offers.
    allowed_tools: Optional[List[str]] = None


class AgentPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    role: Optional[str] = Field(default=None, max_length=160)
    instructions: Optional[str] = Field(default=None, max_length=8000)
    allowed_tools: Optional[List[str]] = None
    # ``allowed_tools: null`` is "every tool", so clearing it needs its own flag.
    all_tools: bool = False


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    instructions: str
    allowed_tools: Optional[List[str]] = None
    workspace_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ToolInfo(BaseModel):
    name: str
    description: str = ""


def _known_tools(ctx: ServerDependencies) -> dict[str, str]:
    out: dict[str, str] = {}
    for tool in ctx.tools.all():
        name = _tool_name(tool)
        if name:
            out[name] = str(getattr(tool, "description", "") or "")[:200]
    out[TOOL_NAME] = "Ask one of your other agents to do a piece of work and get its answer."
    return out


def _check_tools(ctx: ServerDependencies, names: Optional[List[str]]) -> None:
    if names is None:
        return
    unknown = sorted(set(names) - set(_known_tools(ctx)))
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"Unknown tool(s): {', '.join(unknown)}."
        )


@router.get("/tools", response_model=List[ToolInfo])
async def available_tools(ctx: ServerDependencies = Depends(get_ctx)):
    """The tools an agent can be given, for the permissions picker."""
    return [
        ToolInfo(name=n, description=d) for n, d in sorted(_known_tools(ctx).items())
    ]


@router.get("", response_model=List[AgentOut])
async def list_my_agents(
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    return await list_agents(db, user)


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(
    body: AgentIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    if len(await list_agents(db, user)) >= MAX_AGENTS_PER_USER:
        raise HTTPException(
            status_code=409,
            detail=f"You can have {MAX_AGENTS_PER_USER} agents. Delete one to add another.",
        )
    _check_tools(ctx, body.allowed_tools)
    agent = Agent(
        tenant_id=user.tenant_id or "default",
        user_identifier=user.sub,
        name=body.name.strip(),
        role=body.role.strip(),
        instructions=body.instructions,
        allowed_tools=body.allowed_tools,
    )
    db.add(agent)
    await db.flush()
    return agent


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentPatch,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    _check_tools(ctx, body.allowed_tools)
    if body.name is not None:
        agent.name = body.name.strip()
    if body.role is not None:
        agent.role = body.role.strip()
    if body.instructions is not None:
        agent.instructions = body.instructions
    if body.all_tools:
        agent.allowed_tools = None
    elif body.allowed_tools is not None:
        agent.allowed_tools = body.allowed_tools
    await db.flush()
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Delete the profile and its workspace files. Its conversations stay, as ordinary ones."""
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    store = ctx.file_store
    if hasattr(store, "list_prefix") and hasattr(store, "delete"):
        from substrate.workspace.layout import conversation_prefix

        prefix = (
            conversation_prefix(user.tenant_id or "default", user.sub, agent.workspace_id)
            + "/"
        )
        for key, _size, _mtime in await store.list_prefix(prefix):
            await store.delete(key)
    await db.delete(agent)
    await db.flush()
