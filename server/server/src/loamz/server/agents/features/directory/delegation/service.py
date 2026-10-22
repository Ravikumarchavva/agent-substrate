"""`ask_agent`: one agent asks another of the user's agents to do a piece of work and gets its answer back.

An agent may ask only its contacts (the allow-list its owner sets); a plain conversation with no agent in it may ask any
of the user's agents. The tool does not run anything itself: it is built with `run_delegate(caller, profile, question)`,
the runner of whoever builds it (conversations), which runs the other agent in a conversation of its own with its own
role, tools and workspace, and answers with what it said. That runner must not give the delegate this tool: one level
only, which is what stops two agents asking each other forever. Each agent a conversation may call is capped per
message, and the wait is bounded; an agent that is still working when the wait ends is reported as still running.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.agents.kernel.tools import ToolExecutionResult, ToolRisk
from loamz.engine.agents.kernel.types.content import TextBlock
from loamz.engine.agents.kernel.types.ids import new_id
from loamz.server.agents.features.directory.agents.model import AgentProfile

logger = logging.getLogger(__name__)

TOOL_NAME = "ask_agent"
MAX_CALLS_PER_MESSAGE = 5
WAIT_SECONDS = 180.0
LATE_SECONDS = 3600.0

RunDelegate = Callable[[Caller, AgentProfile, str], Awaitable[str]]
Resolve = Callable[[str], Awaitable[AgentProfile | None]]
Record = Callable[[str, str, str, str, str, str, bool], Awaitable[None]]
"""`(target agent id, speaker agent id, text, part, status, delegation id, asked)`: keeps what was said in the pair's record."""


@dataclass(frozen=True)
class AgentRef:
    """The few facts about an agent the asker needs."""

    id: str
    name: str
    role: str
    note: str = ""


def _text(message: str, *, error: bool = False, **structured: Any) -> ToolExecutionResult:
    return ToolExecutionResult(
        name=TOOL_NAME,
        content=[TextBlock(text=message)],
        is_error=error,
        structured_content=structured,
    )


class AskAgentTool:
    """Ask another of the caller's agents to do something. Built per message, with the agents it may name."""

    name = TOOL_NAME
    risk = ToolRisk.SAFE
    idempotent = False

    def __init__(
        self,
        caller: Caller,
        others: list[AgentRef],
        run_delegate: RunDelegate,
        resolve: Resolve,
        *,
        asker: str | None = None,
        record: Record | None = None,
    ) -> None:
        self._caller = caller
        self._others = {a.name.lower(): a.id for a in others}
        self._run_delegate = run_delegate
        self._resolve = resolve
        self._asker = asker
        self._record = record
        self._calls = 0
        self._background: set[asyncio.Task[None]] = set()
        roster = "; ".join(
            f"{a.name} ({a.role or 'no role set'}{': ' + a.note if a.note else ''})" for a in others
        )
        self.description = (
            "Ask one of your other agents to do a piece of work and get its answer back. It works in its own "
            f"conversation with its own tools and files. Agents you can ask: {roster}. Give it everything it needs "
            "in the request: it cannot see this conversation."
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
        agent_id = self._others.get(agent.strip().lower())
        if agent_id is None:
            return _text(
                f"There is no agent called {agent!r}. You can ask: "
                f"{', '.join(sorted(self._others)) or 'nobody'}.",
                error=True,
            )
        if not request.strip():
            return _text("Say what you want it to do.", error=True)
        self._calls += 1
        if self._calls > MAX_CALLS_PER_MESSAGE:
            return _text(
                f"You have already asked other agents {MAX_CALLS_PER_MESSAGE} times for this message. "
                "Use what they gave you.",
                error=True,
            )
        try:
            return await self._ask(agent_id, request.strip())
        except Exception as exc:  # noqa: BLE001 - a failed delegate is a result for the asker, not a crash
            logger.exception("delegation to %s failed", agent)
            return _text(f"{agent} could not do that: {exc}", error=True)

    async def _say(
        self,
        delegation: str,
        target: str,
        speaker: str,
        text: str,
        part: str,
        status: str,
        asked: bool = False,
    ) -> None:
        """Recording must never be why an ask fails."""
        if self._record is None or self._asker is None:
            return
        try:
            await self._record(target, speaker, text, part, status, delegation, asked)
        except Exception:  # noqa: BLE001
            logger.exception("could not record the exchange with %s", speaker)

    async def _ask(self, agent_id: str, request: str) -> ToolExecutionResult:
        target = await self._resolve(agent_id)
        if target is None:
            return _text("That agent is no longer available.", error=True)
        delegation = new_id()
        await self._say(delegation, agent_id, self._asker or "", request, "ask", "working", True)
        job = asyncio.ensure_future(self._run_delegate(self._caller, target, request))
        done, _ = await asyncio.wait({job}, timeout=WAIT_SECONDS)
        link = {"agent": target.name}
        if not done:
            await self._say(
                delegation,
                agent_id,
                agent_id,
                f"{target.name} is still working on it.",
                "note",
                "working",
            )
            self._later(job, delegation, agent_id)
            return _text(
                f"{target.name} is still working. Ask again later; it will finish on its own.",
                **link,
            )
        try:
            answer = job.result().strip() or "(it answered with nothing)"
        except Exception as exc:  # noqa: BLE001
            await self._say(
                delegation, agent_id, agent_id, f"{target.name} failed: {exc}", "answer", "failed"
            )
            return _text(f"{target.name} failed: {exc}", error=True, **link)
        await self._say(delegation, agent_id, agent_id, answer, "answer", "done")
        return _text(answer, **link)

    def _later(self, job: asyncio.Future[str], delegation: str, agent_id: str) -> None:
        """An answer that came after the asker stopped waiting still belongs in the pair's record."""

        async def finish() -> None:
            try:
                answer = (await asyncio.wait_for(job, timeout=LATE_SECONDS)).strip()
            except Exception:  # noqa: BLE001 - nothing to report to
                return
            await self._say(
                delegation,
                agent_id,
                agent_id,
                answer or "(it answered with nothing)",
                "answer",
                "done",
            )

        task = asyncio.ensure_future(finish())
        self._background.add(task)
        task.add_done_callback(self._background.discard)


def ask_agent_tool(
    caller: Caller,
    others: list[AgentRef],
    run_delegate: RunDelegate,
    resolve: Resolve,
    *,
    asker: str | None = None,
    record: Record | None = None,
) -> AskAgentTool:
    return AskAgentTool(caller, others, run_delegate, resolve, asker=asker, record=record)
