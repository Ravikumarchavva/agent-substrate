"""Tests for session-scoped history — verifying cross-turn memory for subagents."""

from __future__ import annotations

from typing import AsyncIterator

from ravi.agents.context import (
    ContextConfig,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
)
from ravi.agents.runtime.local import LocalRuntime
from ravi.kernel import (
    ChatMessage,
    ContentBlock,
    TextBlock,
)
from ravi.kernel.core.content import ToolUseBlock
from ravi.kernel.agent.supervision import HistoryRetention
from ravi.kernel.messaging.stream import CompletionEvent, TextDelta
from ravi.kernel.llm import GenerationOptions, LLMResponse, Usage
from ravi.agents.core import ReActAgent


# ---------------------------------------------------------------------------
# Minimal mock LLM (same pattern as test_assistant_agent.py)
# ---------------------------------------------------------------------------


class MockLLMClient:
    def __init__(self, responses: list[list[ContentBlock]]) -> None:
        self._queue = list(responses)

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        assert self._queue, "MockLLMClient: no more scripted responses"
        return LLMResponse(content=self._queue.pop(0), usage=Usage())

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        return self._do_stream(messages, options=options)

    async def _do_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions,
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        resp = await self.generate(messages, options=options)
        text = " ".join(
            b.text for b in resp.content if isinstance(b, TextBlock) and b.text
        )
        if text:
            yield TextDelta(text=text)
        yield CompletionEvent(content=resp.content, usage=resp.usage)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_standalone_session_accumulates_across_runs():
    """Standalone agent history accumulates across multiple run() calls (session = id.key)."""
    async with LocalRuntime() as rt:
        shared_history = InMemoryHistoryProvider()
        agent = ReActAgent(
            "bot",
            rt,
            model=MockLLMClient(
                [
                    [TextBlock(text="I am fine.")],
                    [TextBlock(text="You said hi earlier.")],
                ]
            ),
            context=ContextConfig(
                shared_history, SlidingWindowCompaction(max_messages=20)
            ),
            max_iterations=5,
        )

        r1 = await agent.run("Hi!")
        assert r1.status == "success"

        r2 = await agent.run("What did I say?")
        assert r2.status == "success"
        assert r2.output == "You said hi earlier."

        # All 4 messages (2 user + 2 assistant) are in the single session
        msgs = await shared_history.get_messages(agent.id, session_id=agent.id.key)
        assert len(msgs) == 4


async def test_permanent_retention_subagent_remembers_across_runs():
    """A PERMANENT-retention subagent's history persists across two runs in the same session."""
    async with LocalRuntime() as rt:
        # The "coder" subagent remembers across turns
        coder_history = InMemoryHistoryProvider()

        coder = ReActAgent(
            "coder",
            rt,
            model=MockLLMClient(
                [
                    [TextBlock(text="Noted: the secret is 42.")],  # run 1
                    [TextBlock(text="The secret I noted was 42.")],  # run 2
                ]
            ),
            context=ContextConfig(
                coder_history, SlidingWindowCompaction(max_messages=40)
            ),
            max_iterations=5,
        )

        session = "session-persistence-test"

        # Manually stamp supervision so coder has the right session
        from ravi.kernel.core.identity import AgentId
        from ravi.kernel.agent.supervision import Supervision

        orchestrator_id = AgentId(type="assistant", key="router")
        root_sv = Supervision.root(orchestrator_id, session_id=session)
        child_sv = root_sv.spawn_child(
            orchestrator_id, retention=HistoryRetention.PERMANENT
        )
        coder.supervision = child_sv

        # Run 1: store a fact
        r1 = await coder.run("Remember the secret is 42.", session_id=session)
        assert r1.status == "success"

        # History after run 1 — 2 messages
        msgs_after_run1 = await coder_history.get_messages(coder.id, session_id=session)
        assert len(msgs_after_run1) == 2

        # Run 2 (same session, different run_id): coder should see run 1's history
        from uuid import uuid4

        new_run_sv = Supervision(
            run_id=uuid4().hex,
            session_id=session,
            root_id=root_sv.root_id,
            parent_id=orchestrator_id,
            depth=1,
            spawn_budget=root_sv.spawn_budget,
            retention=HistoryRetention.PERMANENT,
        )
        coder.supervision = new_run_sv

        r2 = await coder.run("What was the secret?", session_id=session)
        assert r2.status == "success"
        assert "42" in r2.output

        # 4 messages total (run 1 + run 2) accumulated in the same session
        msgs_after_run2 = await coder_history.get_messages(coder.id, session_id=session)
        assert len(msgs_after_run2) == 4


async def test_run_retention_subagent_is_ephemeral():
    """A RUN-retention subagent starts with an empty history on each run."""
    async with LocalRuntime() as rt:
        scratch_history = InMemoryHistoryProvider()
        session = "session-ephemeral-test"

        from ravi.kernel.core.identity import AgentId
        from ravi.kernel.agent.supervision import Supervision
        from uuid import uuid4

        orchestrator_id = AgentId(type="assistant", key="router")

        scratch = ReActAgent(
            "scratch",
            rt,
            model=MockLLMClient(
                [
                    [TextBlock(text="Done run 1.")],
                    [TextBlock(text="Done run 2.")],
                ]
            ),
            context=ContextConfig(
                scratch_history, SlidingWindowCompaction(max_messages=40)
            ),
            max_iterations=5,
        )

        # Run 1
        sv1 = Supervision.root(orchestrator_id, session_id=session).spawn_child(
            orchestrator_id, retention=HistoryRetention.RUN
        )
        scratch.supervision = sv1
        r1 = await scratch.run("Task one.", session_id=session)
        assert r1.status == "success"

        # Simulate RUN-retention cleanup (what orchestrator does in finally)
        await scratch_history.clear(scratch.id, session_id=session)

        # Run 2 — history should be empty at start
        sv2 = Supervision(
            run_id=uuid4().hex,
            session_id=session,
            root_id=sv1.root_id,
            parent_id=orchestrator_id,
            depth=1,
            spawn_budget=sv1.spawn_budget,
            retention=HistoryRetention.RUN,
        )
        scratch.supervision = sv2

        msgs_before_run2 = await scratch_history.get_messages(
            scratch.id, session_id=session
        )
        assert len(msgs_before_run2) == 0, (
            "RUN-retention: history must be empty after clear"
        )

        r2 = await scratch.run("Task two.", session_id=session)
        assert r2.status == "success"

        # Only run 2's messages remain
        msgs_after_run2 = await scratch_history.get_messages(
            scratch.id, session_id=session
        )
        assert len(msgs_after_run2) == 2


async def test_session_isolation_across_different_sessions():
    """Two sessions of the same agent don't bleed into each other."""
    async with LocalRuntime() as rt:
        shared_history = InMemoryHistoryProvider()

        agent = ReActAgent(
            "agent",
            rt,
            model=MockLLMClient(
                [
                    [TextBlock(text="Session A response.")],
                    [TextBlock(text="Session B response.")],
                ]
            ),
            context=ContextConfig(
                shared_history, SlidingWindowCompaction(max_messages=20)
            ),
            max_iterations=5,
        )

        r_a = await agent.run("Hello session A.", session_id="session-A")
        assert r_a.status == "success"

        r_b = await agent.run("Hello session B.", session_id="session-B")
        assert r_b.status == "success"

        # Sessions are isolated
        msgs_a = await shared_history.get_messages(agent.id, session_id="session-A")
        msgs_b = await shared_history.get_messages(agent.id, session_id="session-B")
        assert len(msgs_a) == 2
        assert len(msgs_b) == 2


async def test_orchestrator_propagates_fresh_run_id_to_subagent_history():
    """A handoff run stamps the same fresh run_id on orchestrator and subagent work."""
    from ravi.agents.core.orchestrator import OrchestratorAgent, SubAgentConfig

    async with LocalRuntime() as rt:
        worker_history = InMemoryHistoryProvider()
        worker = ReActAgent(
            "worker",
            rt,
            model=MockLLMClient(
                [
                    [TextBlock(text="child output one")],
                    [TextBlock(text="child output two")],
                ]
            ),
            context=ContextConfig(
                worker_history, SlidingWindowCompaction(max_messages=40)
            ),
            max_iterations=3,
        )

        orchestrator = OrchestratorAgent(
            "router",
            rt,
            model=MockLLMClient(
                [
                    [
                        ToolUseBlock(
                            call_id="h1",
                            tool_name="handoff_worker",
                            arguments={"input": "child task one"},
                        )
                    ],
                    [TextBlock(text="final one")],
                    [
                        ToolUseBlock(
                            call_id="h2",
                            tool_name="handoff_worker",
                            arguments={"input": "child task two"},
                        )
                    ],
                    [TextBlock(text="final two")],
                ]
            ),
            sub_agents=[
                SubAgentConfig(worker, retention=HistoryRetention.PERMANENT),
            ],
            session_id="orch-session",
            max_iterations=3,
        )

        first = await orchestrator.run("route one")
        second = await orchestrator.run("route two")

        assert first.status == "success"
        assert second.status == "success"
        assert first.run_id != second.run_id

        msgs = await worker_history.get_messages(worker.id, session_id="orch-session")
        worker_run_ids = [msg.metadata["run_id"] for msg in msgs]
        assert worker_run_ids[:2] == [first.run_id, first.run_id]
        assert worker_run_ids[2:] == [second.run_id, second.run_id]
