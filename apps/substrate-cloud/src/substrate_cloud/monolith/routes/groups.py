"""Groups — ``/groups``: the user and several of their agents in one conversation, and ``/agents/{id}/contacts``: who an agent may message.

Every agent in a group sees every message and chooses whether to reply. ``@Name`` addresses one, ``@everyone`` all of them; a member set to
"mentions" only considers what is addressed to it, and a "muted" one only what names it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.types import Actor
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.models import Group
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.monolith.services.groups import service as groups
from substrate_cloud.monolith.services.agents.service import get_owned_agent

router = APIRouter(tags=["groups"])


class MemberIn(BaseModel):
    agent_id: uuid.UUID
    mode: str = "all"


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    members: List[MemberIn] = Field(min_length=1)


class GroupPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    token_cap: Optional[int] = Field(default=None, ge=1000, le=groups.MAX_TOKEN_CAP)


class MemberOut(BaseModel):
    agent_id: uuid.UUID
    name: str
    role: str
    mode: str


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime
    members: List[MemberOut]
    # The last thing said, one line, and how many entries the user has not seen.
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    unread: int = 0
    paused: bool = False
    # What the agents have used in this group and the most they may.
    tokens_used: int = 0
    token_cap: int = 0


class EntryOut(BaseModel):
    seq: int
    sender_id: str
    sender: str
    from_user: bool
    kind: str
    text: str
    mentions: List[str]
    reply_to: Optional[int] = None
    at: datetime


class MessagesOut(BaseModel):
    entries: List[EntryOut]
    latest: int
    # Members thinking about it right now, for "Scout is typing…".
    working: List[str] = []


class SendIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    reply_to: Optional[int] = None


class ModeIn(BaseModel):
    mode: str


class ReadIn(BaseModel):
    upto: int


class ContactIn(BaseModel):
    agent_id: uuid.UUID
    note: str = Field(default="", max_length=300)


class ContactsIn(BaseModel):
    contacts: List[ContactIn]


class ContactOut(BaseModel):
    agent_id: uuid.UUID
    name: str
    role: str
    note: str


def _check_mode(mode: str) -> str:
    if mode not in groups.MODES:
        raise HTTPException(
            422, f"Mode must be one of: {', '.join(sorted(groups.MODES))}."
        )
    return mode


def _store(ctx: ServerDependencies):
    if ctx.runtime is None:
        raise HTTPException(503, "The runtime is not available.")
    return ctx.runtime.store


async def _owned(db: AsyncSession, group_id: uuid.UUID, user: AuthClaims) -> Group:
    group = await groups.get_owned_group(db, group_id, user)
    if group is None:
        raise HTTPException(404, "Group not found.")
    return group


async def _out(db: AsyncSession, store, group: Group) -> GroupOut:
    members = await groups.group_members(db, group.id)
    names = await groups.roster(db, group)
    mine = str(groups.user_actor(group.user_identifier))
    (last,) = await store.channel_last(group.channel, 1) or [None]
    unseen = await store.channel_read(
        group.channel, after=group.user_read_seq, limit=200
    )
    return GroupOut(
        id=group.id,
        name=group.name,
        created_at=group.created_at,
        updated_at=group.updated_at,
        members=[
            MemberOut(agent_id=a.id, name=a.name, role=a.role, mode=m.mode)
            for m, a in members
        ],
        last_message=last.text[:140] if last else None,
        last_sender=names.get(str(last.sender), "Group") if last else None,
        unread=sum(1 for e in unseen if str(e.sender) != mine),
        paused=bool(last and last.kind.value == "system"),
        tokens_used=await groups.tokens_used(store, group),
        token_cap=group.token_cap,
    )


def _entry(e, names: dict[str, str], mine: str) -> EntryOut:
    return EntryOut(
        seq=e.seq,
        sender_id=str(e.sender),
        sender=names.get(str(e.sender), "Group"),
        from_user=str(e.sender) == mine,
        kind=e.kind.value,
        text=e.text,
        mentions=[names.get(m, m) for m in e.mentions],
        reply_to=e.reply_to,
        at=e.at,
    )


@router.get("/groups", response_model=List[GroupOut])
async def list_my_groups(
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    store = _store(ctx)
    return [await _out(db, store, g) for g in await groups.list_groups(db, user)]


@router.post("/groups", response_model=GroupOut, status_code=201)
async def create_group(
    body: GroupIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    store = _store(ctx)
    try:
        group = await groups.create_group(
            db,
            store,
            user,
            body.name.strip(),
            [(m.agent_id, _check_mode(m.mode)) for m in body.members],
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    return await _out(db, store, group)


@router.get("/groups/{group_id}", response_model=GroupOut)
async def get_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    return await _out(db, _store(ctx), await _owned(db, group_id, user))


@router.patch("/groups/{group_id}", response_model=GroupOut)
async def rename_group(
    group_id: uuid.UUID,
    body: GroupPatch,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    if body.name is not None:
        group.name = body.name.strip()
    if body.token_cap is not None:
        await groups.set_token_cap(_store(ctx), group, body.token_cap)
    await db.commit()
    await db.refresh(group)  # updated_at is set by the database on update
    await groups.refresh_members(db, ctx.runtime, group.id)
    return await _out(db, _store(ctx), group)


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    await _store(ctx).channel_delete(group.channel)
    await db.delete(group)
    await db.commit()


@router.get("/groups/{group_id}/messages", response_model=MessagesOut)
async def read_messages(
    group_id: uuid.UUID,
    after: int = -1,
    limit: int = 200,
    wait: float = 0,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    names = await groups.roster(db, group)
    mine = str(groups.user_actor(group.user_identifier))
    channel = group.channel
    store = _store(ctx)
    if wait > 0:
        # Hold the request open until something is said (or the wait ends) rather than make the client ask again and again. The
        # database connection goes back to the pool meanwhile.
        await db.commit()
        await store.channel_wait(channel, after, min(wait, 3.0))
    entries = await store.channel_read(channel, after=after, limit=min(max(limit, 1), 500))
    busy = await store.working([Actor.from_str(a) for a in names if a != mine])
    return MessagesOut(
        entries=[_entry(e, names, mine) for e in entries],
        latest=entries[-1].seq if entries else after,
        working=[names[str(a)] for a in busy],
    )


@router.post("/groups/{group_id}/messages", response_model=EntryOut, status_code=201)
async def send_message(
    group_id: uuid.UUID,
    body: SendIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    store = _store(ctx)
    result = await groups.post_as_user(
        db, store, group, user, body.text.strip(), body.reply_to
    )
    if result.seq is None:
        raise HTTPException(409, "The group could not take that message.")
    group.user_read_seq = max(group.user_read_seq, result.seq)
    await db.commit()
    names = await groups.roster(db, group)
    (entry,) = await store.channel_read(group.channel, after=result.seq - 1, limit=1)
    return _entry(entry, names, str(groups.user_actor(group.user_identifier)))


@router.post("/groups/{group_id}/read", status_code=204)
async def mark_read(
    group_id: uuid.UUID,
    body: ReadIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    group = await _owned(db, group_id, user)
    group.user_read_seq = max(group.user_read_seq, body.upto)
    await db.commit()


@router.post("/groups/{group_id}/members", response_model=GroupOut, status_code=201)
async def add_member(
    group_id: uuid.UUID,
    body: MemberIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    store = _store(ctx)
    try:
        await groups.add_member(
            db, store, group, user, body.agent_id, _check_mode(body.mode)
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    await groups.refresh_members(db, ctx.runtime, group.id)
    return await _out(db, store, group)


@router.patch("/groups/{group_id}/members/{agent_id}", response_model=GroupOut)
async def set_member_mode(
    group_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: ModeIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    store = _store(ctx)
    if not any(a.id == agent_id for _, a in await groups.group_members(db, group.id)):
        raise HTTPException(404, "That agent is not in this group.")
    await groups.add_member(db, store, group, user, agent_id, _check_mode(body.mode))
    await db.commit()
    await groups.refresh_members(db, ctx.runtime, group.id)
    return await _out(db, store, group)


@router.delete("/groups/{group_id}/members/{agent_id}", status_code=204)
async def remove_member(
    group_id: uuid.UUID,
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    if len(await groups.group_members(db, group.id)) <= 1:
        raise HTTPException(
            422, "A group needs at least one agent. Delete the group instead."
        )
    await groups.remove_member(db, _store(ctx), group, agent_id)
    await db.commit()
    await groups.refresh_members(db, ctx.runtime, group.id)


# -- contacts ------------------------------------------------------------------------------------------------------------------------


@router.get("/agents/{agent_id}/contacts", response_model=List[ContactOut])
async def get_contacts(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(404, "Agent not found.")
    return [
        ContactOut(agent_id=a.id, name=a.name, role=a.role, note=c.note)
        for c, a in await groups.contacts_of(db, agent.id)
    ]


@router.put("/agents/{agent_id}/contacts", response_model=List[ContactOut])
async def put_contacts(
    agent_id: uuid.UUID,
    body: ContactsIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(404, "Agent not found.")
    try:
        await groups.set_contacts(
            db, user, agent, [(c.agent_id, c.note) for c in body.contacts]
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    await db.commit()
    return [
        ContactOut(agent_id=a.id, name=a.name, role=a.role, note=c.note)
        for c, a in await groups.contacts_of(db, agent.id)
    ]
