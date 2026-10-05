"""Groups: the user and several of their agents in one conversation, and the agents' contacts.

What is said lives in the runtime's channel ``group/<id>``; the database holds who is in it. Each agent in a group is its own actor
(``member/<agent>@<group>``) with its own conversation memory, so it knows only what it was present for.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.runtime import AppendResult, Member, Mode, mentions_in
from substrate.types import Actor, ExecutionBudget
from substrate_cloud.monolith.models import (
    Agent,
    AgentContact,
    Group,
    GroupMember,
    UserPreferences,
)
from substrate_cloud.monolith.services.agents.service import get_owned_agent
from substrate_cloud.shared.auth.claims import AuthClaims

MEMBER_TYPE = "member"
MAX_MEMBERS = 12
# What one group may spend in all, however long it runs and however many agents talk: the bound that replaces a cap on replies.
GROUP_TOKEN_CAP = 1_000_000
MODES = {m.value for m in Mode}


def member_actor(agent_id: uuid.UUID | str, group_id: uuid.UUID | str) -> Actor:
    return Actor(MEMBER_TYPE, f"{agent_id}@{group_id}")


def parse_member(actor: Actor) -> tuple[uuid.UUID, uuid.UUID]:
    agent, _, group = actor.key.partition("@")
    return uuid.UUID(agent), uuid.UUID(group)


def user_actor(user_id: str) -> Actor:
    return Actor("user", user_id)


async def get_owned_group(
    db: AsyncSession, group_id: uuid.UUID, claims: AuthClaims
) -> Optional[Group]:
    group = await db.get(Group, group_id)
    if (
        group is None
        or group.user_identifier != claims.sub
        or group.tenant_id != (claims.tenant_id or "default")
    ):
        return None
    return group


async def list_groups(db: AsyncSession, claims: AuthClaims) -> list[Group]:
    rows = await db.execute(
        select(Group)
        .where(
            Group.user_identifier == claims.sub,
            Group.tenant_id == (claims.tenant_id or "default"),
        )
        .order_by(Group.updated_at.desc())
    )
    return list(rows.scalars().all())


async def group_members(
    db: AsyncSession, group_id: uuid.UUID
) -> list[tuple[GroupMember, Agent]]:
    rows = await db.execute(
        select(GroupMember, Agent)
        .join(Agent, Agent.id == GroupMember.agent_id)
        .where(GroupMember.group_id == group_id)
        .order_by(Agent.created_at)
    )
    return [(m, a) for m, a in rows.all()]


async def display_name(db: AsyncSession, tenant_id: str, user_id: str) -> str:
    prefs = await db.get(UserPreferences, (tenant_id, user_id))
    return (prefs.display_name if prefs and prefs.display_name else None) or "You"


async def roster(db: AsyncSession, group: Group) -> dict[str, str]:
    """Everyone in the group by the address entries carry, to the name the others know them by."""
    names = {
        str(user_actor(group.user_identifier)): await display_name(
            db, group.tenant_id, group.user_identifier
        )
    }
    for _, agent in await group_members(db, group.id):
        names[str(member_actor(agent.id, group.id))] = agent.name
    return names


async def create_group(
    db: AsyncSession,
    store: Any,
    claims: AuthClaims,
    name: str,
    members: list[tuple[uuid.UUID, str]],
) -> Group:
    """Make a group of the caller's own agents (``members`` is ``(agent id, mode)``) and open its channel."""
    if not members:
        raise ValueError("A group needs at least one agent.")
    if len(members) > MAX_MEMBERS:
        raise ValueError(f"A group has at most {MAX_MEMBERS} agents.")
    group = Group(
        name=name, user_identifier=claims.sub, tenant_id=claims.tenant_id or "default"
    )
    db.add(group)
    await db.flush()
    seen: set[uuid.UUID] = set()
    for agent_id, mode in members:
        if agent_id in seen:
            continue
        seen.add(agent_id)
        if await get_owned_agent(db, agent_id, claims) is None:
            raise LookupError("One of those agents is not available.")
        db.add(GroupMember(group_id=group.id, agent_id=agent_id, mode=mode))
    await db.flush()
    await store.channel_open(
        group.channel,
        tenant=group.tenant_id,
        members=[
            Member(agent=member_actor(a, group.id), mode=Mode(m)) for a, m in members
        ],
        breaker=group.breaker,
    )
    await store.account_limit(
        f"channel:{group.channel}", ExecutionBudget(max_tokens=GROUP_TOKEN_CAP)
    )
    return group


async def add_member(
    db: AsyncSession,
    store: Any,
    group: Group,
    claims: AuthClaims,
    agent_id: uuid.UUID,
    mode: str,
) -> None:
    if await get_owned_agent(db, agent_id, claims) is None:
        raise LookupError("That agent is not available.")
    is_new = await db.get(GroupMember, (group.id, agent_id)) is None
    if is_new:
        if len(await group_members(db, group.id)) >= MAX_MEMBERS:
            raise ValueError(f"A group has at most {MAX_MEMBERS} agents.")
        db.add(GroupMember(group_id=group.id, agent_id=agent_id, mode=mode))
    else:
        (await db.get(GroupMember, (group.id, agent_id))).mode = mode  # type: ignore[union-attr]
    await db.flush()
    # A newcomer was not there for what came before: it starts reading from now.
    (latest,) = await store.channel_last(group.channel, 1) or [None]
    await store.channel_set_member(
        group.channel,
        Member(
            agent=member_actor(agent_id, group.id),
            mode=Mode(mode),
            cursor=latest.seq if latest and is_new else -1,
        ),
    )


async def remove_member(
    db: AsyncSession, store: Any, group: Group, agent_id: uuid.UUID
) -> None:
    member = await db.get(GroupMember, (group.id, agent_id))
    if member is not None:
        await db.delete(member)
        await db.flush()
    await store.channel_remove_member(group.channel, member_actor(agent_id, group.id))


async def post_as_user(
    db: AsyncSession,
    store: Any,
    group: Group,
    claims: AuthClaims,
    text: str,
    reply_to: Optional[int] = None,
) -> AppendResult:
    """The user speaks. ``@Name`` and ``@everyone`` in the text address agents; the entry wakes whoever it concerns."""
    names = await roster(db, group)
    mentions = mentions_in(text, names, exclude=str(user_actor(group.user_identifier)))
    return await store.channel_append(
        group.channel,
        sender=user_actor(claims.sub),
        text=text,
        mentions=mentions,
        reply_to=reply_to,
        caused_by="user",
    )


# -- contacts ------------------------------------------------------------------------------------------------------------------------


async def contacts_of(
    db: AsyncSession, agent_id: uuid.UUID
) -> list[tuple[AgentContact, Agent]]:
    rows = await db.execute(
        select(AgentContact, Agent)
        .join(Agent, Agent.id == AgentContact.contact_id)
        .where(AgentContact.agent_id == agent_id)
        .order_by(Agent.created_at)
    )
    return [(c, a) for c, a in rows.all()]


async def set_contacts(
    db: AsyncSession,
    claims: AuthClaims,
    agent: Agent,
    contacts: list[tuple[uuid.UUID, str]],
) -> None:
    """Replace the agents ``agent`` may message directly. Only the caller's own agents, and never itself."""
    wanted: dict[uuid.UUID, str] = {}
    for contact_id, note in contacts:
        if contact_id == agent.id:
            continue
        if await get_owned_agent(db, contact_id, claims) is None:
            raise LookupError("One of those agents is not available.")
        wanted[contact_id] = note
    existing = {c.contact_id: c for c, _ in await contacts_of(db, agent.id)}
    for contact_id, row in existing.items():
        if contact_id not in wanted:
            await db.delete(row)
    for contact_id, note in wanted.items():
        if contact_id in existing:
            existing[contact_id].note = note
        else:
            db.add(AgentContact(agent_id=agent.id, contact_id=contact_id, note=note))
    await db.flush()
