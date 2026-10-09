"""Which conversations a person's feed follows."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.security.deps import AuthClaims
from substrate_cloud.monolith.services.agents import pairs
from substrate_cloud.monolith.services.agents.service import get_owned_agent
from substrate_cloud.monolith.services.groups import service as groups
from substrate_cloud.realtime.feed import Chat

# The most pairs of a viewed agent followed live at once: the newest ones, which are the ones that change.
MAX_WATCHED = 200


async def chats_for(db: AsyncSession, user: AuthClaims, watch: uuid.UUID | None = None) -> dict[str, Chat]:
    """The person's groups, each by its channel; and, when they are viewing an agent's account (``watch``), that agent's pairs too, named ``pair-<id>``."""
    found: dict[str, Chat] = {}
    for group in await groups.list_groups(db, user):
        names = await groups.roster(db, group)
        agents = tuple(groups.member_actor(agent.id, group.id) for _, agent in await groups.group_members(db, group.id))
        found[group.channel] = Chat(group_id=str(group.id), names=names, agents=agents)
    if watch is not None:
        viewed = await get_owned_agent(db, watch, user)
        if viewed is not None:
            for pair, other in await pairs.pairs_of(db, user_id=user.sub, tenant_id=user.tenant_id or "default", agent_id=viewed.id, limit=MAX_WATCHED):
                names = {str(pairs.agent_actor(viewed.id)): viewed.name, str(pairs.agent_actor(other.id)): other.name}
                found[pair.channel] = Chat(group_id=f"pair-{pair.id}", names=names)
    return found
