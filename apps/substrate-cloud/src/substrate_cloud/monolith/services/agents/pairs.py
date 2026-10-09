"""Pairs: what two of a user's agents said to each other.

Each pair is a record in the runtime's channel ``pair/<id>``: the request, then the answer, for every time one asked the other. Nobody in the
channel is woken (it has no members); it is how the conversation is kept in order, paged and followed live, like any other. The ``agent_pairs``
row is what lists it (see ``models.AgentPair``): whose pairs there are and the latest line, so an agent with a hundred contacts is a page of
rows, not a hundred journals.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.runtime import EntryKind
from substrate.types import Actor
from substrate_cloud.monolith.models import Agent, AgentPair

# A pair's channel never pauses: what is said in it is a record of work, not a conversation that could run away.
PAIR_BREAKER = 1_000_000
PREVIEW = 140


def agent_actor(agent_id: uuid.UUID | str) -> Actor:
    """Who speaks in a pair: the agent itself (not one of its chats)."""
    return Actor("agent", str(agent_id))


def ordered(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if str(a) < str(b) else (b, a)


async def get_or_create_pair(
    db: AsyncSession, store: Any, *, tenant_id: str, user_id: str, asker: uuid.UUID, target: uuid.UUID
) -> AgentPair:
    """The pair for these two agents, made (and its channel opened) the first time they talk."""
    low, high = ordered(asker, target)
    found = (
        await db.execute(
            select(AgentPair).where(AgentPair.user_identifier == user_id, AgentPair.agent_a == low, AgentPair.agent_b == high)
        )
    ).scalar_one_or_none()
    if found is None:
        found = AgentPair(tenant_id=tenant_id, user_identifier=user_id, agent_a=low, agent_b=high)
        db.add(found)
        await db.flush()
    await store.channel_open(found.channel, tenant=tenant_id, breaker=PAIR_BREAKER)  # idempotent
    return found


def _line(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= PREVIEW else one[:PREVIEW] + "…"


async def record(
    db: AsyncSession,
    store: Any,
    pair: AgentPair,
    *,
    speaker: uuid.UUID,
    text: str,
    thread_id: str,
    part: str,
    status: str,
    asked: bool = False,
) -> int | None:
    """One line in the pair, said by ``speaker``: the request (``part="ask"``, ``asked`` counts it as one more time they have talked) or what came
    back (``"answer"``). Said once however many times it is recorded: the key is the delegation and the part."""
    result = await store.channel_append(
        pair.channel,
        sender=agent_actor(speaker),
        text=text,
        fresh=True,
        kind=EntryKind.MESSAGE,
        dedup_key=f"{thread_id}:{part}",
        data={"thread_id": thread_id, "status": status},
        caused_by=thread_id,
    )
    if result.seq is None:
        return None
    now = datetime.now(timezone.utc)
    pair.last_at = now
    pair.last_text = _line(text)
    pair.last_sender = speaker
    if asked:
        pair.exchanges = (pair.exchanges or 0) + 1
    await db.flush()
    return result.seq


async def pairs_of(
    db: AsyncSession,
    *,
    user_id: str,
    tenant_id: str,
    agent_id: uuid.UUID,
    q: str = "",
    before: datetime | None = None,
    limit: int = 30,
) -> list[tuple[AgentPair, Agent]]:
    """The agent's pairs, most recent first, each with the other agent. A page after ``before`` (keyset on time: the cost does not grow with how far
    down you are), optionally only those whose other agent's name starts with or contains ``q``."""
    other = Agent
    stmt = (
        select(AgentPair, other)
        .join(other, other.id == _other_id(agent_id))
        .where(
            AgentPair.user_identifier == user_id,
            AgentPair.tenant_id == tenant_id,
            or_(AgentPair.agent_a == agent_id, AgentPair.agent_b == agent_id),
        )
    )
    if before is not None:
        stmt = stmt.where(AgentPair.last_at < before)
    if q.strip():
        stmt = stmt.where(other.name.ilike(f"%{q.strip()}%"))
    rows = await db.execute(stmt.order_by(AgentPair.last_at.desc()).limit(limit))
    return [(p, a) for p, a in rows.all()]


def _other_id(agent_id: uuid.UUID):
    """SQL for 'the other agent of the pair', given this one."""
    return case((AgentPair.agent_a == agent_id, AgentPair.agent_b), else_=AgentPair.agent_a)


async def pair_for(db: AsyncSession, *, user_id: str, tenant_id: str, agent_id: uuid.UUID, pair_id: uuid.UUID) -> AgentPair | None:
    """A pair, if it is this user's and this agent is one of its two."""
    found = await db.get(AgentPair, pair_id)
    if (
        found is None
        or found.user_identifier != user_id
        or found.tenant_id != tenant_id
        or agent_id not in (found.agent_a, found.agent_b)
    ):
        return None
    return found


async def delete_pairs_of(db: AsyncSession, store: Any, agent_id: uuid.UUID) -> None:
    """After an agent is deleted: every pair it was in goes, with what was said in it."""
    rows = (await db.execute(select(AgentPair).where(or_(AgentPair.agent_a == agent_id, AgentPair.agent_b == agent_id)))).scalars().all()
    for pair in rows:
        await store.channel_delete(pair.channel)
    if rows:
        await db.execute(delete(AgentPair).where(AgentPair.id.in_([p.id for p in rows])))


__all__ = ["agent_actor", "delete_pairs_of", "get_or_create_pair", "ordered", "pair_for", "pairs_of", "record"]
