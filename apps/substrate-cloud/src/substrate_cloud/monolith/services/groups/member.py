"""The agent a group member is: built from the saved profile alone when a message in the group wakes it, with no request in the loop."""

from __future__ import annotations

import logging
from typing import Any, Callable

from substrate.types import Actor
from substrate_cloud.factory import CHANNEL_PASS as PASS
from substrate_cloud.factory import channel_member_config
from substrate_cloud.monolith.database import system_session
from substrate_cloud.monolith.models import Agent, Group, GroupMember
from substrate_cloud.monolith.routes.chat_intents import _tool_name
from substrate_cloud.monolith.services.agents.assembly import assemble_agent
from substrate_cloud.monolith.services.agents.service import AgentProfile
from substrate_cloud.monolith.services.agents.delegation import (
    TOOL_NAME,
    AskAgentTool,
    contacts_for,
)
from substrate_cloud.monolith.services.groups.service import (
    MEMBER_TYPE,
    parse_member,
    roster,
)

logger = logging.getLogger(__name__)


def group_instructions(group_name: str, me: str, names: dict[str, str]) -> str:
    others = ", ".join(n for n in names.values() if n != me)
    return (
        f'\n\n---\nYou are {me}, taking part in a group chat called "{group_name}" with: {others}.\n'
        "Everything said in the group is shown to you, and you decide whether to reply, as a person in a team chat would. "
        "Reply when you are asked something, are mentioned, or have something useful to add that no one else has said; "
        "otherwise stay quiet. When a message is addressed to someone else by name, leave it to them unless they would miss something only you know. "
        "Keep messages short and in your own voice.\n"
        "To address someone write @Name; @everyone addresses all of them. Do not repeat what another member already said.\n"
        f"When you have nothing to add, answer with exactly {PASS} and nothing else.\n"
        "You see only what was said in the group, and what you did elsewhere stays with you.\n"
    )


def register_member_factory(runtime: Any, get_deps: Callable[[], Any]) -> None:
    """Let the worker activate ``member/<agent>@<group>`` from its address alone: after a restart, an eviction, or a wake in a process
    that never built it."""

    async def activate(actor: Actor) -> Any:
        deps = get_deps()
        agent_id, group_id = parse_member(actor)
        async with system_session(deps.session_factory) as db:
            agent = await db.get(Agent, agent_id)
            group = await db.get(Group, group_id)
            if (
                agent is None
                or group is None
                or await db.get(GroupMember, (group_id, agent_id)) is None
            ):
                raise LookupError(f"{actor} is no longer in that group")
            profile = AgentProfile.of(agent)
            names = await roster(db, group)
            me = agent.name
            title = group.name
        contacts = await contacts_for(
            deps, profile.tenant_id, profile.user_id, agent_id
        )
        permitted = profile.allowed_tools is None or TOOL_NAME in profile.allowed_tools
        return await assemble_agent(
            deps,
            profile,
            session_id=actor.key,
            extra_instructions=group_instructions(title, me, names),
            extra_tools=[AskAgentTool(deps, contacts, _tool_name)]
            if contacts and permitted
            else [],
            register=False,
            name=MEMBER_TYPE,
            channel=channel_member_config(
                names,
                {
                    "user_id": profile.user_id,
                    "tenant_id": profile.tenant_id,
                    "workspace_id": profile.workspace_id,
                },
            ),
        )

    runtime.register_factory(MEMBER_TYPE, activate)
