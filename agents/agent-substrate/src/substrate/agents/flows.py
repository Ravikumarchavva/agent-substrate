"""Multi-agent flows — kernel-native agent orchestration pipelines.

Each flow implements the kernel Agent protocol:
    id: Actor
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
    Starts all branches at once and merges their outputs via a
    configurable strategy (concat / vote / custom callable).

ConditionalFlow
    Evaluates a predicate against the current input and routes to one of two
    sub-agents (if_true / if_false).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING, Callable

from substrate.types.content import ChatMessage, Role, TextBlock, content_blocks_to_str
from substrate.types.identity import Actor
from substrate.runtime.message import ChatPayload, DataPayload, Message
from substrate.runtime.communication import AskOutcome
from substrate.agents.routed import RoutedAgent, handle

if TYPE_CHECKING:
    from substrate.runtime.supervisor import RunHandle
    from substrate.runtime.context import Agent, RunContext

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


def _make_step_message(target: Actor, text: str, *, sender: Actor) -> Message:
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
class SequentialFlow(RoutedAgent):
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
    def id(self) -> Actor:
        return Actor(type="flow", key=self.name)

    @handle(ChatPayload, DataPayload)
    async def _on_message(self, ctx: RunContext, msg: Message) -> None:
        accumulated = _text_from_message(msg)
        for step in self.steps:
            step_msg = _make_step_message(step.id, accumulated, sender=self.id)
            child: RunHandle = await ctx.spawn(step.id, boot=step_msg)
            outcome = await ctx.ask(child, step_msg, timeout=self.step_timeout)
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
class ParallelFlow(RoutedAgent):
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
    def id(self) -> Actor:
        return Actor(type="flow", key=self.name)

    def _merge_outputs(self, outputs: list[str]) -> str:
        if callable(self.merge):
            return self.merge(outputs)
        if self.merge == "vote":
            from collections import Counter

            return Counter(outputs).most_common(1)[0][0]
        return "\n\n".join(outputs)

    @handle(ChatPayload, DataPayload)
    async def _on_message(self, ctx: RunContext, msg: Message) -> None:
        text = _text_from_message(msg)
        pairs: list[tuple[Message, RunHandle]] = []
        for branch in self.branches:
            bm = _make_step_message(branch.id, text, sender=self.id)
            child: RunHandle = await ctx.spawn(branch.id, boot=bm)
            pairs.append((bm, child))
        # Every branch is already running, so waiting on them one after another costs
        # nothing in wall-clock time — and keeps each wait on its own journal path.
        # Waiting concurrently would let one suspended wait race the others for paths.
        outcomes: list[AskOutcome] = [
            await ctx.ask(child, bm, timeout=self.branch_timeout) for bm, child in pairs
        ]
        outputs = [
            _text_from_outcome(o) if o.kind == "replied" else "" for o in outcomes
        ]
        await ctx.reply(msg, {"text": self._merge_outputs(outputs)})


# ---------------------------------------------------------------------------
# ConditionalFlow
# ---------------------------------------------------------------------------


@dataclass
class ConditionalFlow(RoutedAgent):
    """Route to one of two sub-agents based on a predicate.

    If the predicate raises, ``if_false`` is taken as the safe fallback.
    """

    predicate: Callable[[str], bool]
    if_true: "Agent"
    if_false: "Agent"
    name: str = "conditional_flow"
    description: str = ""
    branch_timeout: float = 300.0

    @cached_property
    def id(self) -> Actor:
        return Actor(type="flow", key=self.name)

    @handle(ChatPayload, DataPayload)
    async def _on_message(self, ctx: RunContext, msg: Message) -> None:
        text = _text_from_message(msg)
        try:
            branch = self.if_true if self.predicate(text) else self.if_false
        except Exception as exc:
            logger.warning(
                "[%s] predicate raised %s — taking if_false", self.name, exc
            )
            branch = self.if_false
        bm = _make_step_message(branch.id, text, sender=self.id)
        child: RunHandle = await ctx.spawn(branch.id, boot=bm)
        outcome = await ctx.ask(child, bm, timeout=self.branch_timeout)
        text_out = _text_from_outcome(outcome) if outcome.kind == "replied" else ""
        await ctx.reply(msg, {"text": text_out})
