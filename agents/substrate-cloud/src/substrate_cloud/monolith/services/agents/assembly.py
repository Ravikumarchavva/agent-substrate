"""One way to build an agent from its saved profile, for anything that runs it without a request: a delegation, a group member, a wake.

Chat builds its agent from the request in hand; everything here starts from an ``AgentProfile`` and the application's shared dependencies
(``ServerDependencies`` or the app state, which carry the same fields).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from substrate.tools import ToolRisk
from substrate_cloud.factory import build_agent_for_thread, build_chat_tools
from substrate_cloud.monolith.routes.chat_intents import _tool_name
from substrate_cloud.monolith.services.agents.model import client_for
from substrate_cloud.monolith.services.agents.service import (
    AgentProfile,
    agent_instructions_block,
    narrow_tools,
)
from substrate_cloud.monolith.services.groups.drives import Drive
from substrate_cloud.shared.settings import settings


async def assemble_agent(
    deps: Any,
    profile: AgentProfile,
    *,
    session_id: str | uuid.UUID,
    drives: tuple[Drive, ...] = (),
    extra_instructions: str = "",
    drop_tools: tuple[str, ...] = (),
    extra_tools: Sequence[Any] = (),
    approval_required_risk: ToolRisk | None = None,
    register: bool = True,
    name: str = "assistant",
    channel: Any = None,
) -> Any:
    """The agent for ``profile``, with its role, its permitted tools and its owner's memory, keeping its conversation under ``session_id``.

    ``drop_tools`` removes tools by name before the permission filter (a delegate is never given ``ask_agent``); ``extra_tools`` are added
    after it, for tools the caller has already decided this agent gets (``ask_agent``, for its contacts). ``drives`` are the groups' folders its
    code can open beside its own (see ``drives.py``); the caller puts the same ones on the run's message. The caller registers nothing
    itself: ``register=False`` is for a runtime actor factory, which registers what it is handed. ``name`` and ``channel`` make it an
    agent that lives in channels (see ``ChannelMemberConfig``).
    """
    bridge = await deps.bridge_registry.acquire(str(session_id))
    tools = narrow_tools(
        [
            t
            for t in build_chat_tools(deps.tools, bridge)
            if _tool_name(t) not in drop_tools
        ],
        profile,
        _tool_name,
    )
    return await build_agent_for_thread(
        session_id,
        model_client=client_for(deps, profile.model),
        tools=tools,
        system_instructions=deps.system_instructions
        + agent_instructions_block(profile, drives)
        + extra_instructions,
        cfg=settings,
        history=deps.history,
        short_term_memory=deps.short_term_memory,
        long_term_memory=deps.long_term_memory,
        user_id=profile.user_id,
        tenant_id=profile.tenant_id,
        runtime=deps.runtime,
        safety_middleware=deps.safety_middleware,
        approval_required_risk=approval_required_risk,
        register=register,
        name=name,
        channel=channel,
    )
