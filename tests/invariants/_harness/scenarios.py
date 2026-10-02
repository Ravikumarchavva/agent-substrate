"""Scenarios the crash matrix drives, and the doubles they are built from.

Each scenario builds a brand-new runtime, installs the injector before anything is
submitted, runs to quiescence and reports an ``Observation``. Nothing is shared
between runs, so crash point N says something about crash point N only.

``ChargeCard`` is deliberately not idempotent and deliberately not journaled: it is
the thing a durable runtime exists to protect, and the only honest way to see
whether it does.
"""

from __future__ import annotations

import asyncio
from typing import Any

from substrate.types import TextBlock
from substrate.types import Actor
from substrate.runtime import DataPayload, Message
from substrate.runtime import RunRetryPolicy
from substrate.tools import ToolExecutionResult, ToolRisk
from substrate.testing.runtime import ephemeral_runtime
from substrate.tools import Toolbox

from tests.invariants._harness.crash import CrashInjector, InjectedStoreFailure, Mode, Observation, WorkerDied, observe_run

# Retry promptly (the recovery path is what is under test) and expire leases quickly
# (a killed worker is only noticed when its lease runs out).
RETRY_NOW = RunRetryPolicy(max_retries=3, backoff_s=0.0)
RUNTIME_OPTIONS = {"lease_s": 0.6, "poll_interval_s": 0.02}


def boot_message(target: Actor) -> Message:
    return Message(target=target, sender=Actor.system("crash-matrix"), payload=DataPayload(data={}))


class ChargeCard:
    """A tool with a side effect nothing can undo, which is not safe to run twice."""

    name = "charge_card"
    description = "Charges a card. Running it twice charges twice."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"amount": {"type": "integer"}},
        "required": ["amount"],
    }
    risk = ToolRisk.SAFE
    idempotent = False

    def __init__(self, ledger: list[int]) -> None:
        self._ledger = ledger

    async def execute(self, *, ctx: object = None, amount: int = 0, **_: object) -> ToolExecutionResult:
        self._ledger.append(amount)
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=f"charged {amount}")])


class PayingAgent:
    """Handles one message with one journaled call to a non-idempotent tool."""

    def __init__(self, ledger: list[int]) -> None:
        self.id = Actor("agent", "payer")
        self.tools = Toolbox()
        self.tools.add(ChargeCard(ledger))

    async def run(self, ctx: Any, inbox: list[Message]) -> None:
        await ctx.tool("charge_card", {"amount": 100})


class ChildAgent:
    """A child that charges the card once. Only *journaled* effects are protected from
    running twice — an agent body re-runs on retry by design, so a ledger line written
    straight from the body would count that, not the guarantee under test."""

    def __init__(self, ledger: list[int]) -> None:
        self.id = Actor("agent", "child")
        self.tools = Toolbox()
        self.tools.add(ChargeCard(ledger))

    async def run(self, ctx: Any, inbox: list[Message]) -> None:
        await ctx.tool("charge_card", {"amount": 100})


class ParentAgent:
    """Spawns one child and waits for it: the suspend / resume / wake path."""

    def __init__(self, child: Actor) -> None:
        self.id = Actor("agent", "parent")
        self._child = child

    async def run(self, ctx: Any, inbox: list[Message]) -> None:
        handle = await ctx.spawn(self._child, boot=boot_message(self._child))
        await ctx.join(handle)


def _quiet_worker_deaths(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
    """A simulated death leaves a task that ended in ``WorkerDied`` and is never awaited;
    that is the point, so don't report it."""
    if not isinstance(context.get("exception"), WorkerDied):
        loop.default_exception_handler(context)


async def _drive(agents: list[Any], root: Any, ledger: list[Any], fail_at: int | None, mode: Mode) -> Observation:
    asyncio.get_running_loop().set_exception_handler(_quiet_worker_deaths)
    async with ephemeral_runtime(**RUNTIME_OPTIONS) as runtime:
        injector = CrashInjector(runtime, fail_at=fail_at, mode=mode)
        for agent in agents:
            await runtime.register(agent)
        terminal: str | None = None
        kinds: list[str] = []
        try:
            run_id = await runtime.submit(root.id, boot_message(root.id), retry_policy=RETRY_NOW)
        except (InjectedStoreFailure, WorkerDied):
            kinds = ["<submit-failed>"]
        else:
            terminal, kinds = await observe_run(runtime, run_id)
    return Observation(
        terminal=terminal,
        log_kinds=kinds,
        side_effects=list(ledger),
        calls=injector.calls,
        crashed_at=injector.injected,
        mode=mode,
        timed_out="<timed-out>" in kinds,
    )


async def one_tool_call(fail_at: int | None, mode: Mode = "blip") -> Observation:
    """One message, one journaled call to a non-idempotent tool."""
    ledger: list[int] = []
    agent = PayingAgent(ledger)
    return await _drive([agent], agent, ledger, fail_at, mode)


async def spawn_and_join(fail_at: int | None, mode: Mode = "blip") -> Observation:
    """A parent spawns a child and waits for it to finish."""
    ledger: list[int] = []
    child = ChildAgent(ledger)
    parent = ParentAgent(child.id)
    return await _drive([child, parent], parent, ledger, fail_at, mode)


__all__ = [
    "ChargeCard",
    "ChildAgent",
    "ParentAgent",
    "PayingAgent",
    "RETRY_NOW",
    "RUNTIME_OPTIONS",
    "boot_message",
    "one_tool_call",
    "spawn_and_join",
]
