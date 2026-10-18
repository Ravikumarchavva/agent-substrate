"""Agent profiles: a saved role, instructions, tool permissions and persistent workspace that a conversation can be
started with, plus their contacts, picture, pin and the pairs of agents that talked.

Other features use this service: conversations opens an agent's thread (`open_thread`), narrows tools and puts the
role in the prompt (`narrow_tools`, `instructions_block`), builds the delegation tool (`ask_agent_tool`) and the
channel-member agent (`build_member`); groups checks ownership (`get_owned`).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.directory.agents.activity import (
    LastWord,
    RuntimeActivity,
    ThreadActivity,
)
from loamz.agents.features.directory.agents.model import (
    MAX_AGENTS_PER_USER,
    AgentIn,
    AgentList,
    AgentOut,
    AgentPatch,
    AgentProfile,
    ContactIn,
    ContactList,
    ContactOut,
    Drive,
    ToolInfo,
    ToolList,
    ViewChat,
    ViewChats,
    ViewMessage,
    ViewMessages,
    files_instructions,
)
from loamz.agents.features.directory.agents.store import AgentsStore
from loamz.agents.features.directory.avatars.service import AvatarService, avatars
from loamz.agents.features.directory.delegation.service import (
    TOOL_NAME,
    AgentRef,
    AskAgentTool,
    RunDelegate,
)
from loamz.agents.features.directory.pairs.service import PairsService, channels, pairs
from loamz.agents.features.directory.pins.service import PinsService, pins
from loamz.agents.kernel.agents.agent import Agent
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.ports.files import FileStore
from loamz.agents.kernel.tools.tool import Tool
from loamz.agents.kernel.types.ids import new_id
from loamz.agents.providers.llm.factory import (
    detect_provider,
    has_provider_api_key,
    model_supports_vision,
    resolve_model_for_available_credentials,
)
from loamz.agents.stores import AgentStore, ThreadHeader
from loamz.agents.stores.workspace.layout import conversation_prefix
from loamz.data.storage.errors import ConflictError, InvalidRequestError, NotFoundError

PIN_KIND = "agent"
DELEGATED = "delegated_from"
"""Header metadata that marks a conversation an agent was asked to do for someone else (not the agent's own chat)."""


class ToolCatalog(Protocol):
    def describe(self) -> dict[str, str]:
        """`{tool name: one-line description}` of every tool an agent can be given."""
        ...


class DiscoveredTools:
    """The tools the toolkit offers, found by convention (no tool is built to list them)."""

    def __init__(self) -> None:
        self._found: dict[str, str] | None = None

    def describe(self) -> dict[str, str]:
        if self._found is None:
            from loamz.agents.toolkit.discovery import CapabilityDiscovery

            found: dict[str, str] = {}
            for package in CapabilityDiscovery().discover():
                if "tool" not in package.components:
                    continue
                cls = package.tool_class
                name = getattr(cls, "name", None)
                description = getattr(cls, "description", "")
                found[name if isinstance(name, str) and name else package.name] = (
                    description if isinstance(description, str) else ""
                )
            self._found = found
        return self._found


def tool_catalog(container: Container) -> ToolCatalog:
    """Provider: which tools an agent can be given. TODO(integration): conversations supplies the live tool registry."""
    return DiscoveredTools()


class AgentHooks:
    """What other contexts want done when an agent goes or changes (groups drop it from their channels): they cannot be
    called from here (groups depend on directory, not the reverse), so they register themselves when they are built."""

    def __init__(self) -> None:
        self.deleted: list[Callable[[AgentProfile], Awaitable[None]]] = []
        self.changed: list[Callable[[AgentProfile], Awaitable[None]]] = []


def agent_hooks(container: Container) -> AgentHooks:
    return AgentHooks()


def narrow_tools[T](tools: list[T], profile: AgentProfile, name_of: Callable[[T], str]) -> list[T]:
    """The tools this agent may use: all of them unless it lists some."""
    if profile.allowed_tools is None:
        return tools
    allowed = set(profile.allowed_tools)
    return [t for t in tools if name_of(t) in allowed]


def instructions_block(profile: AgentProfile, drives: Sequence[Drive] = ()) -> str:
    """What the model is told about the role it plays and where its files are, appended to the base instructions."""
    lines = [f"\n\n---\nYou are acting as **{profile.name}**."]
    if profile.role.strip():
        lines.append(f"Your role: {profile.role.strip()}")
    if profile.instructions.strip():
        lines.append(f"Standing instructions:\n{profile.instructions.strip()}")
    lines.append(files_instructions(tuple(drives)))
    return "\n".join(lines) + "\n"


def _when(at: float | None) -> datetime | None:
    return None if at is None else datetime.fromtimestamp(at, UTC)


class AgentsService:
    def __init__(
        self,
        container: Container,
        store: AgentsStore,
        pin_service: PinsService,
        pair_service: PairsService,
        avatar_service: AvatarService,
        hooks: AgentHooks,
        catalog: ToolCatalog,
    ) -> None:
        self._c = container
        self._store = store
        self._pins = pin_service
        self.pairs = pair_service
        self._avatars = avatar_service
        self.hooks = hooks
        self._catalog = catalog

    # ── plumbing ────────────────────────────────────────────────────────────
    @property
    def _agent_store(self) -> AgentStore:
        return self._c.agent_store

    @property
    def _files(self) -> FileStore:
        return self._c.file_store

    def activity(self) -> ThreadActivity:
        return RuntimeActivity(self._c.get(channels))  # type: ignore[arg-type]

    def known_tools(self) -> dict[str, str]:
        return {
            **self._catalog.describe(),
            TOOL_NAME: "Ask one of your other agents to do a piece of work and get its answer.",
        }

    def _check_model(self, model: str | None) -> None:
        """A model the deployment cannot call (no key for its provider) is refused when chosen."""
        if model and not has_provider_api_key(
            detect_provider(model), self._c.settings.llm.api_keys()
        ):
            raise InvalidRequestError(f"This deployment has no credentials for {model}.")

    def _check_tools(self, names: list[str] | None) -> None:
        if names is None:
            return
        unknown = sorted(set(names) - set(self.known_tools()))
        if unknown:
            raise InvalidRequestError(f"Unknown tool(s): {', '.join(unknown)}.")

    # ── ownership ───────────────────────────────────────────────────────────
    async def get_owned(self, caller: Caller, agent_id: str) -> AgentProfile:
        """The caller's own agent; a missing one and someone else's are the same NotFoundError."""
        profile = await self._store.get(caller.tenant_id, caller.user_id, agent_id)
        if profile is None:
            raise NotFoundError("Agent not found")
        return profile

    async def list_profiles(self, caller: Caller) -> list[AgentProfile]:
        return await self._store.list(caller.tenant_id, caller.user_id)

    async def profiles(self, caller: Caller, ids: list[str]) -> list[AgentProfile]:
        """The caller's own agents among `ids`, oldest first (others are left out)."""
        return await self._store.many(caller.tenant_id, caller.user_id, ids)

    # ── conversations ───────────────────────────────────────────────────────
    async def _main_thread(self, caller: Caller, agent_id: str) -> ThreadHeader | None:
        """The agent's one conversation (the latest, should there ever be several): not one it was asked to do."""
        headers = self._agent_store.thread_headers
        token: str | None = None
        while True:
            page = await headers.list(
                caller.tenant_id,
                caller.user_id,
                agent_id=agent_id,
                page_size=200,
                page_token=token,
            )
            for header in page.items:
                if not header.metadata.get(DELEGATED):
                    return header
            if not page.next_page_token:
                return None
            token = page.next_page_token

    async def open_thread(self, caller: Caller, agent_id: str) -> ThreadHeader:
        """The agent's conversation, made on first use: one continuing chat, like a contact, not a pile of sessions."""
        profile = await self.get_owned(caller, agent_id)
        existing = await self._main_thread(caller, agent_id)
        headers = self._agent_store.thread_headers
        if existing is not None:
            if existing.name != profile.name:
                renamed = await headers.update(caller.tenant_id, existing.id, name=profile.name)
                return renamed or existing
            return existing
        return await headers.create(
            ThreadHeader(
                tenant_id=caller.tenant_id,
                user_id=caller.user_id,
                name=profile.name,
                agent_id=agent_id,
            )
        )

    # ── views ───────────────────────────────────────────────────────────────
    def _out(
        self,
        profile: AgentProfile,
        pinned: datetime | None,
        thread_id: str | None = None,
        last: LastWord | None = None,
        working: bool = False,
    ) -> AgentOut:
        return AgentOut(
            id=profile.id,
            name=profile.name,
            role=profile.role,
            instructions=profile.instructions,
            allowed_tools=None if profile.allowed_tools is None else list(profile.allowed_tools),
            workspace_id=profile.workspace_id,
            created_at=_when(profile.created_at),  # type: ignore[arg-type]
            thread_id=thread_id,
            last_active=last.at if last else None,
            last_message=last.text if last else None,
            working=working,
            avatar=profile.avatar_key,
            pinned_at=pinned,
            model=profile.model,
            sees=None if not profile.model else model_supports_vision(profile.model),
        )

    async def _one(self, caller: Caller, profile: AgentProfile) -> AgentOut:
        pinned = (await self._pins.pinned_at(caller, PIN_KIND)).get(profile.id)
        thread = await self._main_thread(caller, profile.id)
        return self._out(profile, pinned, thread.id if thread else None)

    async def list(self, caller: Caller) -> AgentList:
        profiles = await self._store.list(caller.tenant_id, caller.user_id)
        pinned = await self._pins.pinned_at(caller, PIN_KIND)
        threads = {p.id: await self._main_thread(caller, p.id) for p in profiles}
        activity = self.activity()
        ids = [t.id for t in threads.values() if t]
        busy = await activity.working(ids) if ids else set()
        items = []
        for p in profiles:
            thread = threads[p.id]
            last = await activity.last_word(thread.id) if thread else None
            items.append(
                self._out(
                    p,
                    pinned.get(p.id),
                    thread.id if thread else None,
                    last,
                    bool(thread and thread.id in busy),
                )
            )
        return AgentList(items=items)

    async def get(self, caller: Caller, agent_id: str) -> AgentOut:
        return await self._one(caller, await self.get_owned(caller, agent_id))

    async def create(self, caller: Caller, body: AgentIn) -> AgentOut:
        if len(await self._store.list(caller.tenant_id, caller.user_id)) >= MAX_AGENTS_PER_USER:
            raise ConflictError(
                f"You can have {MAX_AGENTS_PER_USER} agents. Delete one to add another."
            )
        self._check_tools(body.allowed_tools)
        self._check_model(body.model)
        profile = AgentProfile(
            id=new_id(),
            tenant_id=caller.tenant_id,
            user_id=caller.user_id,
            name=body.name.strip(),
            role=body.role.strip(),
            instructions=body.instructions,
            allowed_tools=None if body.allowed_tools is None else tuple(body.allowed_tools),
            model=body.model or None,
            created_at=time.time(),
        )
        await self._store.insert(profile)
        return self._out(profile, None)

    async def patch(self, caller: Caller, agent_id: str, body: AgentPatch) -> AgentOut:
        profile = await self.get_owned(caller, agent_id)
        self._check_tools(body.allowed_tools)
        self._check_model(body.model)
        change: dict[str, object] = {}
        if body.name is not None:
            change["name"] = body.name.strip()
        if body.role is not None:
            change["role"] = body.role.strip()
        if body.instructions is not None:
            change["instructions"] = body.instructions
        if body.default_model:
            change["model"] = None
        elif body.model is not None:
            change["model"] = body.model or None
        if body.all_tools:
            change["allowed_tools"] = None
        elif body.allowed_tools is not None:
            change["allowed_tools"] = tuple(body.allowed_tools)
        profile = profile.model_copy(update=change)
        await self._store.update(profile)
        if (
            "name" in change
        ):  # its chat is named after it; a rename is the only writer of that title
            thread = await self._main_thread(caller, agent_id)
            if thread is not None and thread.name != profile.name:
                await self._agent_store.thread_headers.update(
                    caller.tenant_id, thread.id, name=profile.name
                )
        for hook in self.hooks.changed:
            await hook(profile)
        return await self._one(caller, profile)

    async def delete(self, caller: Caller, agent_id: str) -> None:
        """Delete the profile and its workspace files. Its conversations stay, as ordinary ones."""
        profile = await self.get_owned(caller, agent_id)
        await self._files.delete_prefix(
            conversation_prefix(caller.tenant_id, caller.user_id, profile.workspace_id) + "/"
        )
        await self.pairs.delete_pairs_of(caller.tenant_id, agent_id)
        headers = self._agent_store.thread_headers
        token: str | None = None
        while True:
            page = await headers.list(
                caller.tenant_id, caller.user_id, agent_id=agent_id, page_size=200, page_token=token
            )
            for header in page.items:
                await headers.update(caller.tenant_id, header.id, agent_id=None)
            if not page.next_page_token:
                break
            token = page.next_page_token
        await self._pins.forget(caller.tenant_id, caller.user_id, PIN_KIND, agent_id)
        await self._store.delete(caller.tenant_id, caller.user_id, agent_id)
        for hook in self.hooks.deleted:
            await hook(profile)

    # ── picture and pin ─────────────────────────────────────────────────────
    async def set_avatar(self, caller: Caller, agent_id: str, data: bytes) -> AgentOut:
        profile = await self.get_owned(caller, agent_id)
        key = await self._avatars.replace(caller, profile.workspace_id, profile.avatar_key, data)
        profile = profile.model_copy(update={"avatar_key": key})
        await self._store.update(profile)
        return await self._one(caller, profile)

    async def clear_avatar(self, caller: Caller, agent_id: str) -> AgentOut:
        profile = await self.get_owned(caller, agent_id)
        await self._avatars.remove(profile.avatar_key)
        profile = profile.model_copy(update={"avatar_key": None})
        await self._store.update(profile)
        return await self._one(caller, profile)

    async def set_pinned(self, caller: Caller, agent_id: str, pinned: bool) -> AgentOut:
        profile = await self.get_owned(caller, agent_id)
        await self._pins.set_pinned(caller, PIN_KIND, agent_id, pinned)
        return await self._one(caller, profile)

    # ── tools and contacts ──────────────────────────────────────────────────
    def tools(self) -> ToolList:
        return ToolList(
            items=[
                ToolInfo(name=n, description=d[:200]) for n, d in sorted(self.known_tools().items())
            ]
        )

    async def get_contacts(self, caller: Caller, agent_id: str) -> ContactList:
        await self.get_owned(caller, agent_id)
        return await self._contact_list(caller, agent_id)

    async def _contact_list(self, caller: Caller, agent_id: str) -> ContactList:
        return ContactList(
            items=[
                ContactOut(agent_id=a.id, name=a.name, role=a.role, note=note)
                for a, note in await self._store.contacts(caller.tenant_id, agent_id)
            ]
        )

    async def put_contacts(
        self, caller: Caller, agent_id: str, contacts: list[ContactIn]
    ) -> ContactList:
        """Replace the agents `agent_id` may message directly. Only the caller's own agents, never itself."""
        profile = await self.get_owned(caller, agent_id)
        wanted: dict[str, str] = {}
        for contact in contacts:
            if contact.agent_id == agent_id:
                continue
            await self.get_owned(caller, contact.agent_id)
            wanted[contact.agent_id] = contact.note
        await self._store.set_contacts(caller.tenant_id, agent_id, wanted)
        for hook in self.hooks.changed:  # the agent knows its contacts by name
            await hook(profile)
        return await self._contact_list(caller, agent_id)

    async def contacts_for(self, caller: Caller, agent_id: str) -> list[AgentRef]:
        """The agents `agent_id` may message: its contacts, and only those. An agent is not handed the whole roster."""
        return [
            AgentRef(a.id, a.name, a.role, note)
            for a, note in await self._store.contacts(caller.tenant_id, agent_id)
            if a.user_id == caller.user_id
        ]

    async def other_agents(self, caller: Caller, exclude_id: str | None = None) -> list[AgentRef]:
        """The user's agents that can be asked, besides the one asking."""
        return [
            AgentRef(a.id, a.name, a.role)
            for a in await self._store.list(caller.tenant_id, caller.user_id)
            if a.id != exclude_id
        ]

    def ask_agent_tool(
        self,
        caller: Caller,
        others: list[AgentRef],
        name_of: Callable[[object], str],
        run_delegate: RunDelegate,
        *,
        asker: str | None = None,
    ) -> AskAgentTool:
        """The `ask_agent` tool for one message. `run_delegate(caller, profile, question) -> answer` is the caller's
        runner (it must not hand the delegate this tool). With `asker` (the asking agent's id) what is said is kept
        as their pair. `name_of` is accepted for symmetry with `narrow_tools` and unused: the tool names itself."""

        async def resolve(agent_id: str) -> AgentProfile | None:
            return await self._store.get(caller.tenant_id, caller.user_id, agent_id)

        async def record(
            target: str,
            speaker: str,
            text: str,
            part: str,
            status: str,
            delegation: str,
            asked: bool,
        ) -> None:
            assert asker is not None
            pair = await self.pairs.get_or_create(caller.tenant_id, caller.user_id, asker, target)
            await self.pairs.record(
                pair,
                speaker=speaker,
                text=text,
                thread_id=delegation,
                part=part,
                status=status,
                asked=asked,
            )

        return AskAgentTool(
            caller, others, run_delegate, resolve, asker=asker, record=record if asker else None
        )

    # ── member assembly ─────────────────────────────────────────────────────
    def build_member(
        self,
        caller: Caller,
        profile: AgentProfile,
        *,
        tools: list[Tool],
        name_of: Callable[[Tool], str],
        base_instructions: str = "",
        extra_instructions: str = "",
        drives: Sequence[Drive] = (),
        extra_tools: Sequence[Tool] = (),
        session_id: str,
        name: str,
        channel: object = None,
        drop_tools: tuple[str, ...] = (),
        **options: object,
    ) -> Agent:
        """The agent for `profile` (unregistered): its role, permitted tools plus `extra_tools`, its own model, keeping
        its conversation under `session_id`. With a `ChannelMemberConfig` as `channel` it is an agent that lives in
        channels. `options` go to `create_assistant_agent` (middleware, approval_required_risk…)."""
        from loamz.agents.kernel.agents.factory import create_assistant_agent

        llm = self._c.settings.llm
        wanted = profile.model or llm.model
        model = resolve_model_for_available_credentials(
            wanted, api_keys=llm.api_keys(), fallback_models=[llm.model]
        )
        allowed = narrow_tools([t for t in tools if name_of(t) not in drop_tools], profile, name_of)
        chat = self._c.settings.chat
        return create_assistant_agent(
            model_client=self._c.chat_model(model),
            tools=[*allowed, *extra_tools],
            system_instructions=base_instructions
            + instructions_block(profile, drives)
            + extra_instructions,
            history=self._agent_store.tenant(profile.tenant_id).threads,
            model_context_window=chat.context_window,
            max_iterations=chat.max_iterations,
            name=name,
            session_id=session_id,
            channel=channel,
            **options,
        )

    # ── views ───────────────────────────────────────────────────────────────
    async def view_chats(
        self,
        caller: Caller,
        agent_id: str,
        *,
        q: str = "",
        kind: str = "all",
        before: datetime | None = None,
        limit: int = 30,
    ) -> ViewChats:
        agent = await self.get_owned(caller, agent_id)
        rows: list[ViewChat] = []
        if kind in ("all", "agents"):
            before_ts = before.timestamp() if before else None
            pair_list = await self.pairs.of_agent(
                caller.tenant_id, caller.user_id, agent.id, q=q, before=before_ts, limit=limit
            )
            for pair, other_name, other_avatar in pair_list:
                rows.append(
                    ViewChat(
                        key=f"pair-{pair.id}",
                        kind="agent",
                        id=str(pair.id),
                        name=other_name,
                        avatar=other_avatar,
                        preview=pair.last_text,
                        last_sender=pair.last_sender,
                        at=datetime.fromtimestamp(pair.last_at, UTC) if pair.last_at else None,
                        count=pair.exchanges,
                    )
                )
        if kind in ("all", "you") and not q:
            thread = await self._main_thread(caller, agent.id)
            if thread is not None:
                activity = self.activity()
                last = await activity.last_word(thread.id)
                rows.insert(
                    0,
                    ViewChat(
                        key="you",
                        kind="you",
                        id=thread.id,
                        name=caller.name or "You",
                        preview=last.text if last else "",
                        at=last.at if last else None,
                    ),
                )
        return ViewChats(items=rows[:limit])

    async def view_messages(
        self,
        caller: Caller,
        agent_id: str,
        key: str,
        *,
        before: int | None = None,
        limit: int = 50,
    ) -> ViewMessages:
        agent = await self.get_owned(caller, agent_id)
        if key == "you":
            thread = await self._main_thread(caller, agent.id)
            if thread is None:
                return ViewMessages(items=[])
            activity = self.activity()
            said = await activity.said(thread.id)
            return ViewMessages(
                items=[
                    ViewMessage(
                        seq=i,
                        sender=s.role,
                        text=s.text,
                        at=s.at,
                    )
                    for i, s in enumerate(said)
                ]
            )
        if key.startswith("pair-"):
            pair_id = key.removeprefix("pair-")
            pair = await self.pairs.pair_for(caller.tenant_id, caller.user_id, agent.id, pair_id)
            if pair is None:
                raise NotFoundError("Chat not found")
            channel_store = self._c.get(channels)
            entries = (
                await channel_store.channel_last(pair.channel, limit)
                if before is None
                else await channel_store.channel_read_before(pair.channel, before, limit)
            )
            return ViewMessages(
                items=[
                    ViewMessage(
                        seq=e.seq,
                        sender=str(e.sender.id if e.sender else ""),
                        text=e.text or "",
                        at=e.created_at,
                        data=dict(e.data or {}),
                    )
                    for e in entries
                ]
            )
        raise NotFoundError(f"Unknown chat key '{key}'")

    # ── erasure ─────────────────────────────────────────────────────────────
    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        """Remove a user's (or the tenant's) agents with contacts, pairs and pins."""
        removed = await self.pairs.erase(tenant_id, user_id)
        removed += await self._pins.erase(tenant_id, user_id)
        return removed + await self._store.erase(tenant_id, user_id)


def agents(container: Container) -> AgentsService:
    return AgentsService(
        container,
        AgentsStore(container.database),
        container.get(pins),
        container.get(pairs),
        container.get(avatars),
        container.get(agent_hooks),
        container.get(tool_catalog),
    )
