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
from substrate_cloud.document_reader import document_reader
from substrate_cloud.monolith.services.groups.drives import drives_of, run_metadata
from substrate_cloud.monolith.services.groups.media import Sandbox, load_media, publish
from substrate_cloud.monolith.services.agents.model import client_for
from substrate_cloud.monolith.services.agents.service import main_threads
from substrate_cloud.monolith.services.groups.service import (
    MEMBER_TYPE,
    group_members,
    member_actor,
    parse_member,
    roster,
)
from substrate_cloud.shared.settings import settings

logger = logging.getLogger(__name__)


def group_instructions(group_name: str, me: str, names: dict[str, str], drive: str) -> str:
    others = ", ".join(n for n in names.values() if n != me)
    return (
        f'\n\n---\nYou are {me}, taking part in a group chat called "{group_name}" with: {others}.\n'
        "Everything said in the group is shown to you, and you decide whether to reply, as a person in a team chat would. "
        "Reply when you are asked something, are mentioned, or have something useful to add that no one else has said; "
        "otherwise stay quiet. When a message is addressed to someone else by name, leave it to them unless they would miss something only you know. "
        "Keep messages short and in your own voice.\n"
        "To address someone write @Name; @everyone addresses all of them. Do not repeat what another member already said.\n"
        f"When you have nothing to add, answer with exactly {PASS} and nothing else.\n"
        "You see only what was said in the group, and what you did elsewhere stays with you. "
        "When a file is shared you are shown the start of its text under the message; a picture is shown to you if your model can see, and if not you are told "
        "so and should say it rather than guess. A voice note is shown as what it says. "
        f"The whole of every shared file is in this group's folder, /groups/{drive}/uploads/: open it with the code tool when the start is not enough. "
        "To share a file you made, write it with the code tool and then use the attach tool with its path: it is attached to your reply.\n"
    )


def _attention(deps: Any, busy_elsewhere: list[Actor]) -> dict[str, Any]:
    """How the member attends: a cheap model for the quick look at what is not for it, and whether it is free to (it is not while another of its
    selves, in another group or in a chat with the user, is working on something)."""
    store = deps.runtime.store

    async def available() -> bool:
        return not await store.working(busy_elsewhere)

    return {"triage": client_for(deps, settings.GROUP_TRIAGE_MODEL or None), "availability": available}


def _senses(deps: Any, sandbox: Sandbox) -> dict[str, Any]:
    """How the member opens a picture shared in the group and shares a file it made: both through the owner's own storage."""
    files = deps.files_for(sandbox.tenant_id)
    if files is None:
        return {}

    async def media(attachment: Any) -> Any:
        return await load_media(files, sandbox, attachment)

    async def share(path: str) -> Any:
        return await publish(files, document_reader(), sandbox, path)

    return {"media": media, "publish": share}


async def build_member(deps: Any, actor: Actor) -> Any:
    """The agent a member actor is, from the saved profile alone: its own folder open as /workspace, and every group it is in beside it."""
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
        roles = {a.name: a.role for _, a in await group_members(db, group_id) if a.role}
        me = agent.name
        title = group.name
        drives = await drives_of(db, agent_id)
        this_drive = next(d.label for d in drives if d.workspace_id == group.workspace_id)
        # Its other selves: the same agent in its other groups, and in its chat with the user.
        elsewhere = [member_actor(agent_id, d.workspace_id.removeprefix("group-")) for d in drives if d.workspace_id != group.workspace_id]
        chat = (await main_threads(db, [agent_id])).get(agent_id)
        if chat is not None:
            elsewhere.append(Actor("assistant", str(chat.id)))
        sandbox = Sandbox(tenant_id=profile.tenant_id, user_id=profile.user_id, home=profile.workspace_id, group=group.workspace_id, drives=drives)
    contacts = await contacts_for(deps, profile.tenant_id, profile.user_id, agent_id)
    permitted = profile.allowed_tools is None or TOOL_NAME in profile.allowed_tools
    return await assemble_agent(
        deps,
        profile,
        session_id=actor.key,
        drives=drives,
        extra_instructions=group_instructions(title, me, names, this_drive),
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
                **run_metadata(profile.workspace_id, drives),
            },
            roles=roles,
            **_attention(deps, elsewhere),
            **_senses(deps, sandbox),
        ),
    )


def register_member_factory(runtime: Any, get_deps: Callable[[], Any]) -> None:
    """Let the worker activate ``member/<agent>@<group>`` from its address alone: after a restart, an eviction, or a wake in a process
    that never built it."""

    async def activate(actor: Actor) -> Any:
        return await build_member(get_deps(), actor)

    runtime.register_factory(MEMBER_TYPE, activate)
