"""RunScope: who a message belongs to travels on ``ctx``, not in globals — and a
spawned sub-agent inherits it."""

from __future__ import annotations

from substrate.agents.context import ContextConfig
from substrate.agents.core.orchestrator import OrchestratorAgent, SubAgentConfig
from substrate.agents.core.react import ReActAgent
from substrate.agents.runtime.runtime import Runtime
from substrate.agents.storage.history import InMemoryHistoryProvider
from substrate.kernel.agent.runtime_context import RunScope, scope_of
from substrate.kernel.core.content import ChatMessage, Role, TextBlock, ToolUseBlock
from substrate.kernel.core.identity import Actor
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.tools import ToolExecutionResult

from tests.agents.test_react_harness import ScriptedLLM  # noqa: E402


class ProbeTool:
    """Records the scope it is called with."""

    name = "probe"
    description = "records ctx.scope"
    input_schema: dict[str, object] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.seen: list[RunScope] = []

    async def execute(self, *, ctx=None, **_kw: object) -> ToolExecutionResult:
        self.seen.append(scope_of(ctx))
        return ToolExecutionResult(name=self.name, content=[TextBlock(text="seen")])


def _isolated() -> ContextConfig:
    return ContextConfig(history=InMemoryHistoryProvider())


def _message(target: Actor, *, thread: str, metadata: dict[str, str]) -> Message:
    return Message(
        target=target,
        sender=Actor(type="proxy", key="user"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text="go")])
        ),
        correlation_id=thread,
        metadata=metadata,
    )


async def _finish(rt: Runtime, agent, msg: Message) -> None:
    await rt.register(agent)
    run_id = await rt.submit(agent.id, msg)
    async for entry in rt.event_log.tail(run_id):
        if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
            assert entry.kind == "run.completed", entry.payload
            return


def test_scope_round_trips_through_message_metadata():
    scope = RunScope.from_metadata(
        {"tenant_id": "t", "user_id": "u", "branch_id": "exp", "parent_agent_id": "boss"},
        thread_id="th",
        agent_id="a",
        agent_label="A",
    )
    assert (scope.tenant_id, scope.user_id, scope.branch_id, scope.parent_agent_id) == (
        "t", "u", "exp", "boss",
    )
    assert scope.thread_id == "th"

    # A child inherits identity and branch, and names this agent as its parent.
    assert scope.child_metadata() == {
        "tenant_id": "t",
        "user_id": "u",
        "thread_id": "th",
        "branch_id": "exp",
        "parent_agent_id": "a",
    }


def test_missing_metadata_means_no_identity_and_the_main_branch():
    scope = RunScope.from_metadata({}, thread_id="th", agent_id="a", agent_label="A")
    assert scope.tenant_id is None and scope.user_id is None
    assert scope.branch_id == "main"


async def test_a_tool_sees_the_scope_of_the_message_being_handled():
    probe = ProbeTool()
    llm = ScriptedLLM([[ToolUseBlock(call_id="c1", tool_name="probe", arguments={})], [TextBlock(text="ok")]])
    agent = ReActAgent("bot", model=llm, tools=[probe], context=_isolated())

    async with Runtime() as rt:
        await _finish(
            rt,
            agent,
            _message(
                agent.id,
                thread="thread-9",
                metadata={"tenant_id": "acme", "user_id": "ana", "branch_id": "exp-1"},
            ),
        )

    (seen,) = probe.seen
    assert (seen.tenant_id, seen.user_id, seen.thread_id, seen.branch_id) == (
        "acme", "ana", "thread-9", "exp-1",
    )
    assert seen.agent_label == "bot"


async def test_a_sub_agent_inherits_tenant_user_and_branch():
    """Regression: the orchestrator used to pass only user_id and parent to its
    children, so a sub-agent ran with no tenant, on the wrong branch, and in a
    conversation of its own instead of the parent's."""
    probe = ProbeTool()
    worker = ReActAgent(
        "worker",
        model=ScriptedLLM(
            [[ToolUseBlock(call_id="w1", tool_name="probe", arguments={})], [TextBlock(text="done")]]
        ),
        tools=[probe],
        context=_isolated(),
    )
    boss = OrchestratorAgent(
        "boss",
        model=ScriptedLLM(
            [
                [ToolUseBlock(call_id="b1", tool_name="handoff_worker", arguments={"task": "look"})],
                [TextBlock(text="all done")],
            ]
        ),
        sub_agents=[SubAgentConfig(agent=worker, description="does the work")],
        context=_isolated(),
    )

    async with Runtime() as rt:
        await rt.register(worker)
        await _finish(
            rt,
            boss,
            _message(
                boss.id,
                thread="thread-1",
                metadata={"tenant_id": "acme", "user_id": "ana", "branch_id": "exp-1"},
            ),
        )

    (seen,) = probe.seen
    assert seen.tenant_id == "acme"
    assert seen.user_id == "ana"
    assert seen.branch_id == "exp-1"
    # Same conversation as the parent — its files, boards and documents — not
    # the run-scoped session id its own message history lives under.
    assert seen.thread_id == "thread-1"
    assert seen.parent_agent_id == str(boss.id)
    assert seen.agent_label == "worker"
