"""Multi-agent flows — kernel-native agent orchestration pipelines.

Each flow implements the kernel Agent protocol:
    id: AgentId
    async def run(self, ctx: RunContext, inbox: list[Message]) -> None

Flows coordinate steps or branches via ctx.spawn() + ctx.ask(), replying to
their caller with ctx.reply().  Register all steps and the flow itself with
the same Runtime before submitting a message to the flow's id.

Built-in flow types
-------------------
SequentialFlow
    Executes steps in order; each step receives the accumulated output of all
    previous steps appended to the original input.

ParallelFlow
    Runs all branches concurrently with asyncio.gather; outputs merged via a
    configurable strategy (concat / vote / custom callable).

ConditionalFlow
    Evaluates a predicate against the current input and routes to one of two
    sub-agents (if_true / if_false).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Callable

from agent_substrate.kernel.core.content import ChatMessage, Role, TextBlock, content_blocks_to_str
from agent_substrate.kernel.core.identity import AgentId
from agent_substrate.kernel.messaging.message import ChatPayload, DataPayload, Message
from agent_substrate.kernel.runtime.communication import AskOutcome

if TYPE_CHECKING:
    from agent_substrate.agents.runtime.context import RunContext
    from agent_substrate.kernel.runtime.supervisor import RunHandle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _text_from_message(msg: Message) -> str:
    """Extract plain text from ChatPayload or DataPayload."""
    p = msg.payload
    if isinstance(p, ChatPayload):
        return content_blocks_to_str(p.message.content)
    if isinstance(p, DataPayload):
        return str(p.data.get("text", ""))
    return ""


def _make_step_message(target: AgentId, text: str, *, sender: AgentId) -> Message:
    """Build a ChatPayload Message with a fresh correlation_id for each step call."""
    return Message(
        target=target,
        sender=sender,
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])
        ),
    )


def _text_from_outcome(outcome: AskOutcome) -> str:
    """Extract reply text from AskOutcome.result.output (DataPayload or ChatPayload)."""
    if outcome.result is None:
        return ""
    out = outcome.result.output
    if isinstance(out, DataPayload):
        return str(out.data.get("text", ""))
    if isinstance(out, ChatPayload):
        return content_blocks_to_str(out.message.content)
    return ""


# ---------------------------------------------------------------------------
# SequentialFlow
# ---------------------------------------------------------------------------


@dataclass
class SequentialFlow:
    """Execute steps in order, piping accumulated output into each next step.

    Each step is a kernel Agent registered with the same Runtime as this flow.
    The flow replies to its caller with the fully accumulated output.
    """

    steps: list
    name: str = "sequential_flow"
    description: str = ""
    step_timeout: float = 300.0

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError("SequentialFlow requires at least one step")

    @cached_property
    def id(self) -> AgentId:
        return AgentId(type="flow", key=self.name)

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            ctx.check()
            accumulated = _text_from_message(msg)
            for step in self.steps:
                step_msg = _make_step_message(step.id, accumulated, sender=self.id)
                handle: RunHandle = await ctx.spawn(step.id, boot=step_msg)
                outcome = await ctx.ask(handle, step_msg, timeout=self.step_timeout)
                if outcome.kind != "replied":
                    await ctx.reply(msg, {"text": "", "error": outcome.kind})
                    return
                output = _text_from_outcome(outcome)
                if output:
                    accumulated = f"{accumulated}\n\n{output}"
            await ctx.reply(msg, {"text": accumulated})


# ---------------------------------------------------------------------------
# ParallelFlow
# ---------------------------------------------------------------------------


@dataclass
class ParallelFlow:
    """Run all branches concurrently and merge their outputs.

    Merge strategies
    ----------------
    ``"concat"`` (default)  — join outputs with ``\\n\\n`` in branch order.
    ``"vote"``              — majority vote; ties broken by branch order.
    ``Callable``            — custom ``(outputs: list[str]) -> str``.
    """

    branches: list
    name: str = "parallel_flow"
    description: str = ""
    merge: str | Callable[[list[str]], str] = "concat"
    branch_timeout: float = 300.0

    def __post_init__(self) -> None:
        if not self.branches:
            raise ValueError("ParallelFlow requires at least one branch")

    @cached_property
    def id(self) -> AgentId:
        return AgentId(type="flow", key=self.name)

    def _merge_outputs(self, outputs: list[str]) -> str:
        if callable(self.merge):
            return self.merge(outputs)
        if self.merge == "vote":
            from collections import Counter

            return Counter(outputs).most_common(1)[0][0]
        return "\n\n".join(outputs)

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            ctx.check()
            text = _text_from_message(msg)
            pairs: list[tuple[Message, RunHandle]] = []
            for branch in self.branches:
                bm = _make_step_message(branch.id, text, sender=self.id)
                handle: RunHandle = await ctx.spawn(branch.id, boot=bm)
                pairs.append((bm, handle))
            outcomes: list[AskOutcome] = await asyncio.gather(
                *[
                    ctx.ask(handle, bm, timeout=self.branch_timeout)
                    for bm, handle in pairs
                ]
            )
            outputs = [
                _text_from_outcome(o) if o.kind == "replied" else "" for o in outcomes
            ]
            await ctx.reply(msg, {"text": self._merge_outputs(outputs)})


# ---------------------------------------------------------------------------
# ConditionalFlow
# ---------------------------------------------------------------------------


@dataclass
class ConditionalFlow:
    """Route to one of two sub-agents based on a predicate.

    If the predicate raises, ``if_false`` is taken as the safe fallback.
    """

    predicate: Callable[[str], bool]
    if_true: object
    if_false: object
    name: str = "conditional_flow"
    description: str = ""
    branch_timeout: float = 300.0

    @cached_property
    def id(self) -> AgentId:
        return AgentId(type="flow", key=self.name)

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            ctx.check()
            text = _text_from_message(msg)
            try:
                branch = self.if_true if self.predicate(text) else self.if_false
            except Exception as exc:
                logger.warning(
                    "[%s] predicate raised %s — taking if_false", self.name, exc
                )
                branch = self.if_false
            bm = _make_step_message(branch.id, text, sender=self.id)
            handle: RunHandle = await ctx.spawn(branch.id, boot=bm)
            outcome = await ctx.ask(handle, bm, timeout=self.branch_timeout)
            text_out = _text_from_outcome(outcome) if outcome.kind == "replied" else ""
            await ctx.reply(msg, {"text": text_out})
