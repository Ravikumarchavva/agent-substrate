"""``ask_agent``: one agent asks another of the user's agents to do a piece of work and gets its answer back.

An agent may ask only its contacts (the allow-list its owner sets); a plain conversation with no agent in it may ask any of the user's agents.
The other agent runs in a conversation of its own (kept in Archived, linked to the one that asked), with its own role, tools and workspace, so everything
it did can be read afterwards. It cannot delegate further: one level only, which is what stops two agents asking each other forever. Each agent a
conversation may call is capped per message, and the wait is bounded; an agent that is still working when the wait ends is reported as still running.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from substrate.runtime import ChatPayload, Message
from substrate.tools import ToolExecutionResult, ToolRisk
from substrate.types import Actor, RunLogKind, TextBlock, scope_of
from substrate.types import ChatMessage as KernelChatMessage
from substrate.types import Role
from substrate.types import TextBlock as KernelTextBlock
from substrate_cloud.monolith.database import system_session
from substrate_cloud.monolith.models import Agent, AgentContact, Thread
from substrate_cloud.monolith.services.agents import pairs
from substrate_cloud.monolith.services.agents.assembly import assemble_agent
from substrate_cloud.monolith.services.agents.service import AgentProfile
from substrate_cloud.monolith.services.groups.drives import drives_of, run_metadata

logger = logging.getLogger(__name__)

TOOL_NAME = "ask_agent"
MAX_CALLS_PER_MESSAGE = 5
WAIT_SECONDS = 180.0


@dataclass(frozen=True)
class AgentRef:
    """The few facts about an agent the asker needs, copied out of the database session they were read in."""

    id: uuid.UUID
    name: str
    role: str
    note: str = ""


def _text(
    message: str, *, error: bool = False, **structured: Any
) -> ToolExecutionResult:
    return ToolExecutionResult(
        content=[TextBlock(text=message)],
        is_error=error,
        structured_content=structured,
    )


class AskAgentTool:
    """Ask another of the caller's agents to do something. Built per message, with the agents it may name in its description."""

    name = TOOL_NAME
    risk = ToolRisk.SAFE
    idempotent = False

    def __init__(self, ctx: Any, others: list[AgentRef], tool_name_of: Any) -> None:
        self._ctx = ctx
        self._others = {a.name.lower(): a.id for a in others}
        self._tool_name_of = tool_name_of
        self._calls = 0
        roster = "; ".join(
            f"{a.name} ({a.role or 'no role set'}{': ' + a.note if a.note else ''})"
            for a in others
        )
        self.description = (
            "Ask one of your other agents to do a piece of work and get its answer back. It works in its own conversation with its own tools and "
            f"files. Agents you can ask: {roster}. Give it everything it needs in the request: it cannot see this conversation."
        )
        self.input_schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "The agent's name."},
                "request": {
                    "type": "string",
                    "description": "What you want it to do, with all the context it needs.",
                },
            },
            "required": ["agent", "request"],
            "additionalProperties": False,
        }

    async def execute(  # type: ignore[override]
        self, *, ctx: object = None, agent: str = "", request: str = "", **_: object
    ) -> ToolExecutionResult:
        scope = scope_of(ctx)
        if not scope.tenant_id or not scope.user_id:
            return _text(
                "Asking another agent needs a signed-in conversation.", error=True
            )
        agent_id = self._others.get(agent.strip().lower())
        if agent_id is None:
            return _text(
                f"There is no agent called {agent!r}. You can ask: {', '.join(sorted(self._others)) or 'nobody'}.",
                error=True,
            )
        if not request.strip():
            return _text("Say what you want it to do.", error=True)
        self._calls += 1
        if self._calls > MAX_CALLS_PER_MESSAGE:
            return _text(
                f"You have already asked other agents {MAX_CALLS_PER_MESSAGE} times for this message. Use what they gave you.",
                error=True,
            )
        try:
            return await self._run(
                agent_id,
                request.strip(),
                scope.tenant_id,
                scope.user_id,
                scope.thread_id,
            )
        except Exception as exc:  # noqa: BLE001 - a failed delegate is a result for the asker, not a crash
            logger.exception("delegation to %s failed", agent)
            return _text(f"{agent} could not do that: {exc}", error=True)

    async def _run(
        self,
        agent_id: uuid.UUID,
        request: str,
        tenant_id: str,
        user_id: str,
        parent_thread: str,
    ) -> ToolExecutionResult:
        ctx = self._ctx
        async with system_session(ctx.session_factory) as db:
            target = await db.get(Agent, agent_id)
            if (
                target is None
                or target.user_identifier != user_id
                or target.tenant_id != tenant_id
            ):
                return _text("That agent is no longer available.", error=True)
            asker = await _asker_of(db, parent_thread)
            thread = Thread(
                name=f"{target.name}: {request[:50]}",
                user_identifier=user_id,
                tenant_id=tenant_id,
                agent_id=target.id,
                tags=[],
                # Who asked, and what: the pair record is kept from these (see ``pairs``).
                metadata_={"delegated_from": parent_thread, "asker_agent_id": str(asker) if asker else None, "request": request[:2000]},
                archived_at=datetime.now(timezone.utc),
            )
            db.add(thread)
            await db.commit()
            thread_id = thread.id
            # Plain values from here on: the rows belong to this session.
            profile = AgentProfile.of(target)
            drives = await drives_of(db, target.id)

        delegate = await assemble_agent(
            ctx,
            profile,
            session_id=thread_id,
            drives=drives,
            drop_tools=(TOOL_NAME,),  # one level only: the delegate cannot delegate
        )
        msg = Message(
            target=delegate.id,
            sender=Actor(type="delegator"),
            payload=ChatPayload(
                message=KernelChatMessage(
                    role=Role.USER, content=[KernelTextBlock(text=request)]
                )
            ),
            correlation_id=str(thread_id),
            metadata={
                "user_id": user_id,
                "tenant_id": tenant_id,
                **run_metadata(profile.workspace_id, drives),
            },
        )

        async def say(speaker: uuid.UUID, text: str, part: str, status: str, *, asked: bool = False) -> None:
            """Keep what was said in the pair's record (see ``pairs``). Recording must never be why an ask fails."""
            if asker is None:
                return
            try:
                async with system_session(ctx.session_factory) as db:
                    pair = await pairs.get_or_create_pair(
                        db, ctx.runtime.store, tenant_id=tenant_id, user_id=user_id, asker=asker, target=agent_id
                    )
                    await pairs.record(db, ctx.runtime.store, pair, speaker=speaker, text=text, thread_id=str(thread_id), part=part, status=status, asked=asked)
                    await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("could not record the exchange between %s and %s", asker, agent_id)

        if asker is not None:
            await say(asker, request, "ask", "working", asked=True)
        await ctx.runtime.register(delegate)
        run_id = await ctx.runtime.submit(delegate.id, msg, thread_id=str(thread_id))

        try:
            outcome, answer, waiting = await asyncio.wait_for(_follow(ctx.runtime, run_id), timeout=WAIT_SECONDS)
        except asyncio.TimeoutError:
            outcome, answer, waiting = "running", "", ""
            if asker:
                await say(agent_id, f"{profile.name} is still working on it.", "note", "working")
                _later(_finish(ctx.runtime, run_id, say, agent_id))

        link = {"thread_id": str(thread_id), "agent": profile.name}
        if outcome == "done":
            await say(agent_id, answer.strip() or "(it answered with nothing)", "answer", "done")
            return _text(answer.strip() or "(it answered with nothing)", **link)
        if outcome == "waiting":
            await say(agent_id, f"Stopped to wait for {waiting}. {answer.strip()}".strip(), "answer", "waiting")
            return _text(
                f"{profile.name} stopped to wait for {waiting}. That needs you: open its conversation to answer. What it said so far: {answer.strip() or '(nothing)'}",
                **link,
            )
        if outcome == "running":
            return _text(
                f"{profile.name} is still working. Its conversation is {thread_id}. Partial answer so far: {answer.strip() or '(none yet)'}",
                **link,
            )
        await say(agent_id, f"{profile.name} {outcome}", "answer", "failed")
        return _text(f"{profile.name} {outcome}", error=True, **link)


async def _follow(runtime: Any, run_id: Any) -> tuple[str, str, str]:
    """Watch a delegate's run until it ends or stops for a person: ``(outcome, what it said, what it is waiting for)``. The outcome is ``done``,
    ``waiting`` or ``failed: why``."""
    answer = ""
    async for entry in runtime.tail(run_id):
        payload = entry.payload or {}
        if entry.kind == RunLogKind.ASSISTANT_MESSAGE:
            answer += payload.get("text", "")
        elif entry.kind == RunLogKind.RUN_COMPLETED:
            return "done", answer, ""
        elif entry.kind == RunLogKind.RUN_FAILED:
            return f"failed: {payload.get('error', 'the run failed')}", answer, ""
        elif entry.kind in (RunLogKind.APPROVAL_REQUESTED, RunLogKind.INPUT_REQUESTED):
            return "waiting", answer, str(payload.get("tool_name") or payload.get("question") or "your input")
    return "done", answer, ""


LATE_SECONDS = 3600.0
_background: set[asyncio.Task[None]] = set()


def _later(coro: Any) -> None:
    """Run something after the ask returned, keeping a reference so it is not collected half done."""
    task = asyncio.ensure_future(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _finish(runtime: Any, run_id: Any, say: Any, agent_id: uuid.UUID) -> None:
    """An answer that came after the asker stopped waiting still belongs in the pair's record: it is written when it arrives (for up to an hour)."""
    try:
        outcome, answer, waiting = await asyncio.wait_for(_follow(runtime, run_id), timeout=LATE_SECONDS)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001 - nothing to report to; the thread keeps the whole story
        return
    if outcome == "done":
        await say(agent_id, answer.strip() or "(it answered with nothing)", "answer", "done")
    elif outcome == "waiting":
        await say(agent_id, f"Stopped to wait for {waiting}. {answer.strip()}".strip(), "answer", "waiting")
    else:
        await say(agent_id, outcome, "answer", "failed")


async def _asker_of(db: Any, parent_thread: str) -> uuid.UUID | None:
    """The agent that is asking, from the conversation it asks in: an agent's own chat names it, and so does a group member's address
    (``<agent>@<group>``). A plain conversation with no agent has none."""
    try:
        parent = await db.get(Thread, uuid.UUID(parent_thread))
        return parent.agent_id if parent is not None else None
    except ValueError:
        try:
            return uuid.UUID(parent_thread.partition("@")[0])
        except ValueError:
            return None


async def other_agents(
    ctx: Any, tenant_id: str, user_id: str, exclude: uuid.UUID | None
) -> list[AgentRef]:
    """The user's agents that can be asked, besides the one asking."""
    async with system_session(ctx.session_factory) as db:
        rows = await db.execute(
            select(Agent)
            .where(Agent.user_identifier == user_id, Agent.tenant_id == tenant_id)
            .order_by(Agent.created_at)
        )
        return [
            AgentRef(a.id, a.name, a.role)
            for a in rows.scalars().all()
            if a.id != exclude
        ]


async def contacts_for(
    ctx: Any, tenant_id: str, user_id: str, asker: uuid.UUID
) -> list[AgentRef]:
    """The agents ``asker`` may message: its contacts, and only those. An agent is not handed the user's whole roster."""
    async with system_session(ctx.session_factory) as db:
        rows = await db.execute(
            select(Agent, AgentContact.note)
            .join(AgentContact, AgentContact.contact_id == Agent.id)
            .where(
                AgentContact.agent_id == asker,
                Agent.user_identifier == user_id,
                Agent.tenant_id == tenant_id,
            )
            .order_by(Agent.created_at)
        )
        return [AgentRef(a.id, a.name, a.role, note) for a, note in rows.all()]
