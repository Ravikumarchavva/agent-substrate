"""``/agents/{id}/view``: an agent's own account, read only — the conversations it has and what was said in them.

What you see is what that agent sees: its chat with you, each other agent it has talked to, each group it is in, most recent first. Nothing here
writes; nothing is marked read. Lists are keyset-paged by time and searched on the server, so an agent with a hundred contacts costs a page, not
a hundred reads (see ``services/agents/pairs`` for how the agent-to-agent conversations are kept).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.runtime import EntryKind
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.models import Agent, Group
from substrate_cloud.monolith.routes.groups import EntryOut, entry_out, preview_line
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.monolith.services.agents import pairs
from substrate_cloud.monolith.services.agents.service import get_owned_agent, main_threads
from substrate_cloud.monolith.services.groups import service as groups
from substrate_cloud.stream import project_thread_timed
from substrate_cloud.stream.runs import last_message

router = APIRouter(tags=["agent view"])

PAGE = 30
MESSAGES = 50


class ViewChat(BaseModel):
    """One row of the agent's chat list."""

    # ``you``, ``pair-<id>`` or ``group-<id>``: how to open it.
    key: str
    kind: Literal["you", "agent", "group"]
    id: str
    name: str
    avatar: Optional[str] = None
    preview: str = ""
    last_sender: Optional[str] = None
    at: Optional[datetime] = None
    # Times they have asked each other (agents), or members (groups).
    count: int = 0


class ViewChats(BaseModel):
    items: List[ViewChat]
    # Pass as ``before`` for the next page; none when that was the last.
    next: Optional[datetime] = None


class ViewMessages(BaseModel):
    entries: List[EntryOut]
    # There is more before the first of these.
    has_more: bool


async def _agent(db: AsyncSession, agent_id: uuid.UUID, user: AuthClaims) -> Agent:
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(404, "Agent not found.")
    return agent


def _store(ctx: ServerDependencies):
    if ctx.runtime is None:
        raise HTTPException(503, "The runtime is not available.")
    return ctx.runtime.store


def _aware(at: Optional[datetime]) -> Optional[datetime]:
    return at if at is None or at.tzinfo else at.replace(tzinfo=timezone.utc)


@router.get("/agents/{agent_id}/view/chats", response_model=ViewChats)
async def view_chats(
    agent_id: uuid.UUID,
    q: str = "",
    kind: Literal["all", "agents", "groups"] = "all",
    before: Optional[datetime] = None,
    limit: int = PAGE,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """The agent's chat list: a page of it, newest first, after ``before``."""
    agent = await _agent(db, agent_id, user)
    store = _store(ctx)
    limit = min(max(limit, 1), 100)
    tenant = user.tenant_id or "default"
    before = _aware(before)
    needle = q.strip().casefold()
    rows: list[ViewChat] = []

    fetched = 0
    if kind in ("all", "agents"):
        page = await pairs.pairs_of(db, user_id=user.sub, tenant_id=tenant, agent_id=agent.id, q=q, before=before, limit=limit)
        fetched = len(page)
        for pair, other in page:
            sender = agent.name if pair.last_sender == agent.id else other.name if pair.last_sender else None
            rows.append(
                ViewChat(key=f"pair-{pair.id}", kind="agent", id=str(other.id), name=other.name, avatar=other.avatar_key, preview=pair.last_text, last_sender=sender, at=_aware(pair.last_at), count=pair.exchanges)
            )

    if kind in ("all", "groups"):
        mine = [g for g in (await db.execute(select(Group).where(Group.id.in_(await groups.groups_of(db, agent.id)), Group.user_identifier == user.sub, Group.tenant_id == tenant))).scalars().all() if not needle or needle in g.name.casefold()]
        heads = {h.channel: h for h in await store.channel_heads([g.channel for g in mine], groups.user_actor(user.sub))}
        for g in mine:
            latest = heads[g.channel].latest if g.channel in heads else None
            at = _aware(latest.at if latest else g.created_at)
            if before is not None and at is not None and at >= before:
                continue
            rows.append(
                ViewChat(key=f"group-{g.id}", kind="group", id=str(g.id), name=g.name, avatar=g.avatar_key, preview=preview_line(latest)[:140] if latest else "", at=at, count=len(await groups.group_members(db, g.id)))
            )

    if kind == "all":
        thread = (await main_threads(db, [agent.id])).get(agent.id)
        owner = await groups.display_name(db, tenant, user.sub)
        if thread is not None and (not needle or needle in owner.casefold()):
            said = await last_message(store, str(thread.id), limit=140)
            at = _aware(said.at if said and said.at else thread.updated_at)
            if before is None or (at is not None and at < before):
                rows.append(ViewChat(key="you", kind="you", id=str(thread.id), name=owner, preview=said.text if said else "", at=at))

    rows.sort(key=lambda r: r.at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    page_rows = rows[:limit]
    more = len(rows) > limit or fetched == limit
    # Who said the last line of a group, for the rows that are shown (not for every group the agent is in).
    for row in page_rows:
        if row.kind == "group":
            group = await db.get(Group, uuid.UUID(row.id))
            head = (await store.channel_heads([group.channel], groups.user_actor(user.sub)))[0] if group else None
            if head and head.latest:
                row.last_sender = (await groups.roster(db, group)).get(str(head.latest.sender), "Group")
    return ViewChats(items=page_rows, next=page_rows[-1].at if more and page_rows else None)


async def _channel_page(store, channel: str, before: Optional[int], limit: int):
    entries = await store.channel_last(channel, limit) if before is None else await store.channel_read_before(channel, before, limit)
    return entries, bool(entries) and entries[0].seq > 0


@router.get("/agents/{agent_id}/view/chats/{key}/messages", response_model=ViewMessages)
async def view_messages(
    agent_id: uuid.UUID,
    key: str,
    before: Optional[int] = None,
    limit: int = MESSAGES,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """What was said in one of the agent's conversations: the latest page, then older ones with ``before`` (a ``seq``)."""
    agent = await _agent(db, agent_id, user)
    store = _store(ctx)
    limit = min(max(limit, 1), 200)
    tenant = user.tenant_id or "default"
    mine = str(pairs.agent_actor(agent.id))

    if key == "you":
        thread = (await main_threads(db, [agent.id])).get(agent.id)
        if thread is None:
            return ViewMessages(entries=[], has_more=False)
        owner = await groups.display_name(db, tenant, user.sub)
        said: list[EntryOut] = []
        for event, at in await project_thread_timed(store, str(thread.id)):
            text = str(getattr(event, "text", "") or "")
            if event.type == "user.message" and text.strip():
                who, own = str(groups.user_actor(user.sub)), False
            elif event.type == "text.delta" and text.strip():
                who, own = mine, True
            else:
                continue
            said.append(
                EntryOut(seq=len(said), id=f"{thread.id}:{len(said)}", sender_id=who, sender=agent.name if own else owner, from_user=own, kind="message", text=text, mentions=[], at=at)
            )
        end = len(said) if before is None else min(before, len(said))
        start = max(0, end - limit)
        return ViewMessages(entries=said[start:end], has_more=start > 0)

    kind, _, raw = key.partition("-")
    try:
        target = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(404, "That conversation is not here.") from None

    if kind == "pair":
        pair = await pairs.pair_for(db, user_id=user.sub, tenant_id=tenant, agent_id=agent.id, pair_id=target)
        if pair is None:
            raise HTTPException(404, "That conversation is not here.")
        other_id = pair.agent_b if pair.agent_a == agent.id else pair.agent_a
        other = await db.get(Agent, other_id)
        names = {mine: agent.name, str(pairs.agent_actor(other_id)): other.name if other else "An agent"}
        entries, more = await _channel_page(store, pair.channel, before, limit)
        return ViewMessages(entries=[entry_out(e, names, mine) for e in entries], has_more=more)

    if kind == "group":
        group = await groups.get_owned_group(db, target, user)
        if group is None or group.id not in await groups.groups_of(db, agent.id):
            raise HTTPException(404, "That conversation is not here.")
        names = await groups.roster(db, group)
        me = str(groups.member_actor(agent.id, group.id))
        entries, more = await _channel_page(store, group.channel, before, limit)
        reactions = await store.channel_reactions(group.channel, [e.seq for e in entries if e.kind is EntryKind.MESSAGE])
        return ViewMessages(entries=[entry_out(e, names, me, reactions.get(e.seq)) for e in entries], has_more=more)

    raise HTTPException(404, "That conversation is not here.")
