"""Agent profiles — ``/agents``.

An agent is a saved role with standing instructions, the tools it may use and a workspace of its own that persists across its conversations.
Start a conversation with one by passing ``agent_id`` to ``POST /threads``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from substrate.types import Actor
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.models import Agent, Thread
from substrate_cloud.monolith.routes.chat_intents import _tool_name
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.stream.runs import LastWord, last_message
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.monolith.services import avatar, pins
from substrate_cloud.monolith.services.agents import pairs
from substrate_cloud.monolith.services.agents.delegation import TOOL_NAME
from substrate_cloud.monolith.services.agents.model import has_credentials, sees
from substrate_cloud.monolith.services.groups.drives import delete_workspace_files
from substrate_cloud.monolith.services.groups.service import (
    groups_of,
    leave_all_groups,
    refresh_agent,
    refresh_members,
)
from substrate_cloud.monolith.services.agents.service import (
    MAX_AGENTS_PER_USER,
    ensure_main_thread,
    get_owned_agent,
    list_agents,
    main_threads,
    rename_main_thread,
)

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    role: str = Field(default="", max_length=160)
    instructions: str = Field(default="", max_length=8000)
    # ``None``: every tool the deployment offers.
    allowed_tools: Optional[List[str]] = None
    # ``provider/name``; ``None``: the deployment's own.
    model: Optional[str] = Field(default=None, max_length=120)


class AgentPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    role: Optional[str] = Field(default=None, max_length=160)
    instructions: Optional[str] = Field(default=None, max_length=8000)
    allowed_tools: Optional[List[str]] = None
    # ``allowed_tools: null`` is "every tool", so clearing it needs its own flag.
    all_tools: bool = False
    model: Optional[str] = Field(default=None, max_length=120)
    # Likewise ``model: null`` leaves it as it is, so going back to the deployment's own needs a flag.
    default_model: bool = False


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    instructions: str
    allowed_tools: Optional[List[str]] = None
    workspace_id: str
    created_at: datetime
    # The agent's one conversation (absent until it is first opened) and when it last moved.
    thread_id: Optional[uuid.UUID] = None
    last_active: Optional[datetime] = None
    # A one-line preview of the last thing said in that conversation, for the sidebar.
    last_message: Optional[str] = None
    # Working on a reply to the user right now, for "typing…" in a list.
    working: bool = False
    # Its picture (an object key served by ``/files/object``) and when its chat was pinned.
    avatar: Optional[str] = None
    pinned_at: Optional[datetime] = None
    # Its own model, and whether that reads pictures (``None`` while it uses the deployment's).
    model: Optional[str] = None
    sees: Optional[bool] = None

    model_config = {"from_attributes": True}


class ThreadRef(BaseModel):
    id: uuid.UUID


def _out(
    agent: Agent,
    thread: Optional[Thread],
    last: Optional[LastWord] = None,
    working: bool = False,
) -> AgentOut:
    return AgentOut(
        id=agent.id,
        name=agent.name,
        role=agent.role,
        instructions=agent.instructions,
        allowed_tools=agent.allowed_tools,
        workspace_id=agent.workspace_id,
        created_at=agent.created_at,
        thread_id=thread.id if thread else None,
        # When it last said or heard something: the thread's own time only moves when it is renamed.
        last_active=(last.at if last and last.at else thread.updated_at if thread else None),
        last_message=last.text if last else None,
        working=working,
        avatar=agent.avatar_key,
        pinned_at=agent.pinned_at,
        model=agent.model,
        sees=sees(agent.model),
    )


class ToolInfo(BaseModel):
    name: str
    description: str = ""


def _known_tools(ctx: ServerDependencies) -> dict[str, str]:
    out: dict[str, str] = {}
    for tool in ctx.tools.all():
        name = _tool_name(tool)
        if name:
            out[name] = str(getattr(tool, "description", "") or "")[:200]
    out[TOOL_NAME] = (
        "Ask one of your other agents to do a piece of work and get its answer."
    )
    return out


def _check_model(ctx: ServerDependencies, model: Optional[str]) -> None:
    """A model the deployment cannot call (no key for its provider) is refused when chosen, not discovered when the agent is first asked something."""
    if model and not has_credentials(ctx, model):
        raise HTTPException(status_code=422, detail=f"This deployment has no credentials for {model}.")


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
    ctx: ServerDependencies = Depends(get_ctx),
):
    agents = await list_agents(db, user)
    threads = await main_threads(db, [a.id for a in agents])
    store = ctx.runtime.store if ctx.runtime is not None else None
    previews = {
        a.id: await last_message(store, str(threads[a.id].id))
        if store and a.id in threads
        else None
        for a in agents
    }
    busy = (
        {
            a.key
            for a in await store.working(
                [Actor("assistant", str(t.id)) for t in threads.values()]
            )
        }
        if store
        else set()
    )
    return [
        _out(
            a,
            threads.get(a.id),
            previews[a.id],
            a.id in threads and str(threads[a.id].id) in busy,
        )
        for a in agents
    ]


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
    _check_model(ctx, body.model)
    agent = Agent(
        tenant_id=user.tenant_id or "default",
        user_identifier=user.sub,
        name=body.name.strip(),
        role=body.role.strip(),
        instructions=body.instructions,
        allowed_tools=body.allowed_tools,
        model=body.model or None,
    )
    db.add(agent)
    await db.flush()
    await db.refresh(agent)
    return _out(agent, None)


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _out(agent, (await main_threads(db, [agent.id])).get(agent.id))


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
    _check_model(ctx, body.model)
    if body.name is not None:
        agent.name = body.name.strip()
        await rename_main_thread(db, agent)
    if body.role is not None:
        agent.role = body.role.strip()
    if body.instructions is not None:
        agent.instructions = body.instructions
    if body.default_model:
        agent.model = None
    elif body.model is not None:
        agent.model = body.model or None
    if body.all_tools:
        agent.allowed_tools = None
    elif body.allowed_tools is not None:
        agent.allowed_tools = body.allowed_tools
    await db.flush()
    await db.refresh(agent)
    out = _out(agent, (await main_threads(db, [agent.id])).get(agent.id))
    await db.commit()
    await refresh_agent(db, ctx.runtime, agent_id)
    return out


@router.post("/{agent_id}/thread", response_model=ThreadRef)
async def open_agent_thread(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    """The agent's conversation, made on first use. Open it to talk to the agent directly."""
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return ThreadRef(id=(await ensure_main_thread(db, agent, user)).id)


async def _agent_out(db: AsyncSession, agent: Agent) -> AgentOut:
    await db.refresh(agent)  # a commit expires it
    return _out(agent, (await main_threads(db, [agent.id])).get(agent.id))


@router.put("/{agent_id}/avatar", response_model=AgentOut)
async def set_avatar(
    agent_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    store = ctx.files_for(user.tenant_id)
    if store is None:
        raise HTTPException(503, "File storage is not configured.")
    try:
        agent.avatar_key = await avatar.replace(
            store, user.tenant_id or "default", user.sub, agent.workspace_id, agent.avatar_key, await file.read(avatar.MAX_UPLOAD_BYTES + 1)
        )
    except avatar.AvatarError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    return await _agent_out(db, agent)


@router.delete("/{agent_id}/avatar", response_model=AgentOut)
async def clear_avatar(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    store = ctx.files_for(user.tenant_id)
    if store is not None:
        await avatar.remove(store, agent.avatar_key)
    agent.avatar_key = None
    await db.commit()
    return await _agent_out(db, agent)


@router.put("/{agent_id}/pin", response_model=AgentOut)
async def pin_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    try:
        await pins.set_pinned(db, agent, True)
    except pins.TooManyPinned as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.commit()
    return await _agent_out(db, agent)


@router.delete("/{agent_id}/pin", response_model=AgentOut)
async def unpin_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    await pins.set_pinned(db, agent, False)
    await db.commit()
    return await _agent_out(db, agent)


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
    files = ctx.files_for(user.tenant_id)
    if files is not None:
        await delete_workspace_files(files, user.tenant_id or "default", user.sub, agent.workspace_id)
    group_ids = await groups_of(db, agent_id)
    if ctx.runtime is not None:
        await pairs.delete_pairs_of(db, ctx.runtime.store, agent_id)  # what it said to other agents goes with it
    await db.delete(agent)
    await db.commit()
    if ctx.runtime is not None:
        await leave_all_groups(ctx.runtime.store, ctx.runtime, agent_id, group_ids)
        for group_id in group_ids:
            await refresh_members(db, ctx.runtime, group_id)
