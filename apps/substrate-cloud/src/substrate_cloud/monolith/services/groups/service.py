"""Groups: the user and several of their agents in one conversation, and the agents' contacts.

What is said lives in the runtime's channel ``group/<id>``; the database holds who is in it. Each agent in a group is its own actor
(``member/<agent>@<group>``) with its own conversation memory, so it knows only what it was present for.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.runtime import AppendResult, Member, Mode, ParticipantKind, mentions_in
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
# What a new group may spend in all, however long it runs and however many agents talk: the bound that replaces a cap on replies.
DEFAULT_TOKEN_CAP = 1_000_000
# A new member listens for what is for it, and follows a conversation it is drawn into: quiet until addressed, as a person in a busy group is.
DEFAULT_MODE = Mode.MENTIONS.value
MAX_TOKEN_CAP = 100_000_000
MODES = {m.value for m in Mode}


def member_actor(agent_id: uuid.UUID | str, group_id: uuid.UUID | str) -> Actor:
    return Actor(MEMBER_TYPE, f"{agent_id}@{group_id}")


def parse_member(actor: Actor) -> tuple[uuid.UUID, uuid.UUID]:
    agent, _, group = actor.key.partition("@")
    return uuid.UUID(agent), uuid.UUID(group)


def user_actor(user_id: str) -> Actor:
    return Actor("user", user_id)


def user_member(user_id: str) -> Member:
    """The person in the group, as a member of its channel: they have a read position like everyone else, and are never woken."""
    return Member(agent=user_actor(user_id), kind=ParticipantKind.HUMAN)


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


async def read_by_all(store: Any, channel: str) -> int:
    """The latest entry every agent that follows everything has read: the person's messages up to here show as seen by all. A member that only
    listens for mentions never reads the rest, and the person's own position is not an agent's, so neither counts. ``-1`` before any has read."""
    return min(
        (
            m.cursor
            for m in await store.channel_members(channel)
            if m.mode.value == "all" and m.kind is ParticipantKind.AGENT
        ),
        default=-1,
    )


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
            user_member(claims.sub),
            *(
                Member(agent=member_actor(a, group.id), mode=Mode(m))
                for a, m in members
            ),
        ],
        breaker=group.breaker,
    )
    await apply_limits(store, group)
    return group


async def apply_limits(store: Any, group: Group) -> None:
    """The group's one account holds both limits: tokens (which bound a model with no price too) and dollars."""
    await store.account_limit(
        f"channel:{group.channel}",
        ExecutionBudget(max_tokens=group.token_cap, max_cost_usd=group.budget_usd),
    )


async def set_token_cap(store: Any, group: Group, cap: int) -> None:
    group.token_cap = cap
    await apply_limits(store, group)


async def set_budget(store: Any, group: Group, budget_usd: float | None) -> None:
    group.budget_usd = budget_usd
    await apply_limits(store, group)


async def spent(store: Any, account: str) -> Any:
    return await store.account_spend(account)


async def tokens_used(store: Any, group: Group) -> int:
    return (await store.account_spend(f"channel:{group.channel}")).tokens


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
    data: Optional[dict] = None,
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
        data=data,
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


async def refresh_members(db: AsyncSession, runtime: Any, group_id: uuid.UUID) -> None:
    """Make every member of a group rebuild from current data on its next message: who is in the group, what it is called, how each agent is
    defined. A member holds its roster and profile from when it was built, so this is called after any change to them. Each member is rebuilt
    in every group it is in, not just this one: it opens all of them as folders, so a change here changes what it sees there."""
    if runtime is None:
        return
    for member, _ in await group_members(db, group_id):
        for group in await groups_of(db, member.agent_id):
            runtime.forget(member_actor(member.agent_id, group))


async def groups_of(db: AsyncSession, agent_id: uuid.UUID) -> list[uuid.UUID]:
    rows = await db.execute(
        select(GroupMember.group_id).where(GroupMember.agent_id == agent_id)
    )
    return [g for (g,) in rows.all()]


async def refresh_agent(db: AsyncSession, runtime: Any, agent_id: uuid.UUID) -> None:
    """The same, for every group an agent is in, after its profile or contacts changed (the others know it by name too)."""
    for group_id in await groups_of(db, agent_id):
        await refresh_members(db, runtime, group_id)


async def leave_all_groups(
    store: Any, runtime: Any, agent_id: uuid.UUID, group_ids: list[uuid.UUID]
) -> None:
    """After an agent is deleted: it stops being woken in its groups, and the others rebuild without it."""
    for group_id in group_ids:
        actor = member_actor(agent_id, group_id)
        await store.channel_remove_member(f"group/{group_id}", actor)
        if runtime is not None:
            runtime.forget(actor)
