"""Agent profiles: a saved role, instructions, tool permissions and persistent workspace that a conversation can be started with."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.models import Agent, Thread
from substrate_cloud.shared.auth.claims import AuthClaims

MAX_AGENTS_PER_USER = 20


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


def agent_instructions_block(agent: Agent) -> str:
    """What the model is told about the role it plays, appended to the base instructions."""
    lines = [f"\n\n---\nYou are acting as **{agent.name}**."]
    if agent.role.strip():
        lines.append(f"Your role: {agent.role.strip()}")
    if agent.instructions.strip():
        lines.append(f"Standing instructions:\n{agent.instructions.strip()}")
    lines.append(
        "Files you create or are given persist in your own workspace across all your conversations."
    )
    return "\n".join(lines) + "\n"


def narrow_tools(tools: list[Any], agent: Agent, name_of: Any) -> list[Any]:
    """The tools this agent may use: all of them unless it lists some."""
    if agent.allowed_tools is None:
        return tools
    allowed = set(agent.allowed_tools)
    return [t for t in tools if name_of(t) in allowed]
