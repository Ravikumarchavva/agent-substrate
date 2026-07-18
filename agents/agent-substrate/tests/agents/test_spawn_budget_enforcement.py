"""SpawnBudget is enforced durably by SupervisorProtocol.spawn() itself, not only by
the in-process SpawnTracker convention OrchestratorAgent happens to follow —
these tests call spawn() directly, bypassing SpawnTracker entirely, to prove
the budget applies regardless of caller."""

from __future__ import annotations

import pytest

from substrate.agents.runtime.backends._event_log import InMemoryEventLog
from substrate.agents.runtime.backends._inbox import InMemoryInbox
from substrate.agents.runtime.backends._scheduler import InMemoryScheduler
from substrate.agents.runtime.backends._signal_bus import InMemorySignalBus
from substrate.agents.runtime.backends._supervisor import InMemorySupervisor
from substrate.kernel.agent.supervision import Supervision, SpawnBudget
from substrate.kernel.core.errors import BudgetExhaustedError
from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.core.content import ChatMessage, Role, TextBlock


def _boot(text: str = "hi") -> Message:
    return Message(
        target=AgentId(type="child", key="x"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])
        ),
    )


def _make_supervisor() -> InMemorySupervisor:
    event_log = InMemoryEventLog()
    inbox = InMemoryInbox()
    scheduler = InMemoryScheduler()
    signal_bus = InMemorySignalBus(scheduler)
    return InMemorySupervisor(event_log, inbox, scheduler, signal_bus)


async def test_spawn_denied_once_headcount_cap_reached_bypassing_spawn_tracker():
    """Direct ctx.spawn()-equivalent calls, not routed through
    OrchestratorAgent/SpawnTracker at all, still hit the cap."""
    supervisor = _make_supervisor()
    root_agent = AgentId(type="root", key="r1")
    # root counts as 1 -- max_agents=3 allows exactly 2 more spawns.
    root = Supervision.root(root_agent, spawn_budget=SpawnBudget(max_agents=3))

    for i in range(2):
        await supervisor.spawn(
            AgentId(type="child", key=f"c{i}"),
            parent=root.run_id,
            supervision=root,
            boot=_boot(),
            path=f"spawn-{i}",
            correlation_id=f"corr-{i}",
        )

    with pytest.raises(BudgetExhaustedError, match="headcount cap reached"):
        await supervisor.spawn(
            AgentId(type="child", key="c-over"),
            parent=root.run_id,
            supervision=root,
            boot=_boot(),
            path="spawn-over",
            correlation_id="corr-over",
        )


async def test_spawn_replay_of_already_recorded_spawn_never_rechecks_budget():
    """A replay (same path -> same effect_id) must return the cached child,
    not re-raise, even if siblings spawned since then exhausted the budget —
    otherwise a run that legitimately succeeded once could fail on replay."""
    supervisor = _make_supervisor()
    root_agent = AgentId(type="root", key="r2")
    root = Supervision.root(root_agent, spawn_budget=SpawnBudget(max_agents=2))

    first = await supervisor.spawn(
        AgentId(type="child", key="c0"),
        parent=root.run_id,
        supervision=root,
        boot=_boot(),
        path="spawn-0",
        correlation_id="corr-0",
    )

    # Budget is now fully consumed (1 root + 1 child == max_agents). A
    # replay of the SAME spawn (identical path) must still succeed.
    replayed = await supervisor.spawn(
        AgentId(type="child", key="c0"),
        parent=root.run_id,
        supervision=root,
        boot=_boot(),
        path="spawn-0",
        correlation_id="corr-0",
    )
    assert replayed.run_id == first.run_id

    # But a genuinely NEW spawn is still correctly denied.
    with pytest.raises(BudgetExhaustedError):
        await supervisor.spawn(
            AgentId(type="child", key="c1"),
            parent=root.run_id,
            supervision=root,
            boot=_boot(),
            path="spawn-1",
            correlation_id="corr-1",
        )
