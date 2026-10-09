"""Agent profiles: a saved role, instructions, tool permissions and persistent workspace that a conversation can be started with."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.models import Agent, Thread
from substrate_cloud.monolith.services.groups.drives import Drive, files_instructions
from substrate_cloud.shared.auth.claims import AuthClaims

MAX_AGENTS_PER_USER = 20


@dataclass(frozen=True)
class AgentProfile:
    """An agent's saved definition as plain values, copied out of the database session it was read in: what anything that builds the
    agent (a chat, a delegation, a group) needs, with no request attached."""

    id: uuid.UUID
    name: str
    role: str
    instructions: str
    allowed_tools: Optional[tuple[str, ...]]
    workspace_id: str
    user_id: str
    tenant_id: str
    model: Optional[str] = None

    @classmethod
    def of(cls, agent: Agent) -> "AgentProfile":
        return cls(
            id=agent.id,  # type: ignore[arg-type]
            name=agent.name,  # type: ignore[arg-type]
            role=agent.role or "",  # type: ignore[arg-type]
            instructions=agent.instructions or "",  # type: ignore[arg-type]
            allowed_tools=tuple(agent.allowed_tools)
            if agent.allowed_tools is not None
            else None,  # type: ignore[arg-type]
            workspace_id=agent.workspace_id,  # type: ignore[arg-type]
            user_id=agent.user_identifier,  # type: ignore[arg-type]
            tenant_id=agent.tenant_id,  # type: ignore[arg-type]
            model=agent.model,  # type: ignore[arg-type]
        )


async def get_owned_agent(
    db: AsyncSession, agent_id: uuid.UUID, claims: AuthClaims
) -> Optional[Agent]:
    """The caller's own agent, or ``None`` for a missing one or someone else's (the same 404 either way)."""
    agent = await db.get(Agent, agent_id)
    if (
        agent is None
        or agent.user_identifier != claims.sub
        or agent.tenant_id != (claims.tenant_id or "default")
    ):
        return None
    return agent


def _is_main() -> Any:
    """SQL condition: a conversation the agent itself lives in, as opposed to one it was asked to do in someone else's name."""
    return Thread.metadata_["delegated_from"].astext.is_(None)


def is_agent_chat(thread: Thread) -> bool:
    """Whether this is the conversation an agent lives in (not a task it was asked to do for someone else): it is named after the agent."""
    return thread.agent_id is not None and not (thread.metadata_ or {}).get(
        "delegated_from"
    )


async def rename_main_thread(db: AsyncSession, agent: Agent) -> None:
    """Name the agent's conversation after the agent. Its rename is the only writer of that title, and it does not move the chat in the list."""
    await db.execute(
        update(Thread)
        .where(
            Thread.agent_id == agent.id,
            _is_main(),
            Thread.name.is_distinct_from(agent.name),
        )
        .values(name=agent.name, updated_at=Thread.updated_at)
    )


async def main_threads(
    db: AsyncSession, agent_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Thread]:
    """Each agent's one conversation (the latest, should there ever be several), keyed by agent id."""
    if not agent_ids:
        return {}
    rows = await db.execute(
        select(Thread)
        .where(
            Thread.agent_id.in_(agent_ids),
            Thread.deleted_at.is_(None),
            _is_main(),
        )
        .order_by(Thread.updated_at.desc())
    )
    found: dict[uuid.UUID, Thread] = {}
    for thread in rows.scalars().all():
        found.setdefault(thread.agent_id, thread)  # type: ignore[arg-type]
    return found


async def ensure_main_thread(
    db: AsyncSession, agent: Agent, claims: AuthClaims
) -> Thread:
    """The agent's conversation, made the first time it is opened: talking to an agent is one continuing chat, like a contact, not a pile of sessions."""
    existing = (await main_threads(db, [agent.id])).get(agent.id)
    if existing is not None:
        if (
            existing.name != agent.name
        ):  # one that was titled by a client before the agent owned its name
            await rename_main_thread(db, agent)
            await db.refresh(existing)
        return existing
    thread = Thread(
        name=agent.name,
        user_identifier=claims.sub,
        tenant_id=claims.tenant_id or "default",
        agent_id=agent.id,
        tags=[],
        metadata_={},
    )
    db.add(thread)
    await db.flush()
    return thread


async def list_agents(db: AsyncSession, claims: AuthClaims) -> list[Agent]:
    rows = await db.execute(
        select(Agent)
        .where(
            Agent.user_identifier == claims.sub,
            Agent.tenant_id == (claims.tenant_id or "default"),
        )
        .order_by(Agent.created_at)
    )
    return list(rows.scalars().all())


def workspace_id_for(thread: Thread, agent: Optional[Agent]) -> str:
    """The id a conversation's files are keyed by: its agent's own workspace when it has one, else the conversation's."""
    return agent.workspace_id if agent is not None else str(thread.id)


def agent_instructions_block(
    agent: Agent | AgentProfile, drives: tuple[Drive, ...] = ()
) -> str:
    """What the model is told about the role it plays and where its files are, appended to the base instructions."""
    lines = [f"\n\n---\nYou are acting as **{agent.name}**."]
    if agent.role.strip():
        lines.append(f"Your role: {agent.role.strip()}")
    if agent.instructions.strip():
        lines.append(f"Standing instructions:\n{agent.instructions.strip()}")
    lines.append(files_instructions(drives))
    return "\n".join(lines) + "\n"


def narrow_tools(
    tools: list[Any], agent: Agent | AgentProfile, name_of: Any
) -> list[Any]:
    """The tools this agent may use: all of them unless it lists some."""
    if agent.allowed_tools is None:
        return tools
    allowed = set(agent.allowed_tools)
    return [t for t in tools if name_of(t) in allowed]
