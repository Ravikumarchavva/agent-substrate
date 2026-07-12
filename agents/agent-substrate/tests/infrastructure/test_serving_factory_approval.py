"""End-to-end: a CRITICAL-risk tool call built through the real
infrastructure/serving_factory.py::build_agent_for_thread() construction
path actually pauses for human approval and resumes on the decision.

This is the test the kernel audit's tool-approval finding was missing: three
separate approval abstractions existed (kernel ApprovalHandler + ToolInvoker,
capabilities ToolApprovalHandler + WebHITLBridge, a bare tools_requiring_
approval name list) but none was ever actually wired into a ReActAgent built
by the real serving path — a CRITICAL tool call executed completely
unguarded. A test that hand-builds ``ReActAgent(approval_handler=...)``
directly would not have caught that: it proves the Protocol works in
isolation, not that it's reachable from a real chat request. This one goes
through ``build_agent_for_thread()`` itself.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from substrate.agents.context import InMemoryHistoryProvider
from substrate.agents.runtime import Runtime
from substrate.config import SubstrateConfig
from substrate.infrastructure.serving_factory import build_agent_for_thread
from substrate.kernel.core.content import ChatMessage, Role, TextBlock, ToolUseBlock
from substrate.kernel.core.usage import Usage
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.messaging.stream import CompletionEvent
from substrate.kernel.tools import ToolExecutionResult, ToolRisk
from substrate.serving.monolith.sse.bridge import WebHITLBridge


class _DropDatabaseTool:
    name = "drop_database"
    description = "Drops the production database."
    risk = ToolRisk.CRITICAL
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text="dropped")])


class _ScriptedLLMClient:
    """Emits a tool call once, then a plain text reply."""

    model = "mock-model"

    def __init__(self) -> None:
        self._calls = 0

    async def generate_stream(self, messages, *, options=None, ctx=None):  # type: ignore[override]
        self._calls += 1
        if self._calls == 1:
            yield CompletionEvent(
                content=[
                    ToolUseBlock(call_id="c1", tool_name="drop_database", arguments={})
                ],
                usage=Usage(input_tokens=10, output_tokens=10),
            )
        else:
            yield CompletionEvent(
                content=[TextBlock(text="done")],
                usage=Usage(input_tokens=5, output_tokens=5),
            )


async def test_critical_tool_call_pauses_for_approval_and_resumes():
    bridge = WebHITLBridge(response_timeout=10.0)
    llm = _ScriptedLLMClient()
    thread_id = uuid.uuid4()

    async with Runtime() as rt:
        agent = await build_agent_for_thread(
            thread_id,
            model_client=llm,
            tools=[_DropDatabaseTool()],
            system_instructions="",
            cfg=SubstrateConfig(),
            history=InMemoryHistoryProvider(),
            runtime=rt,
            bridge=bridge,
        )
        assert agent.approval_handler is not None, (
            "build_agent_for_thread() must wire an approval_handler when a "
            "bridge is given — otherwise a CRITICAL tool executes unguarded"
        )

        msg = Message(
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text="drop it")])
            ),
        )
        run_id = await rt.submit(agent.id, msg, max_retries=0)

        # The approval request must reach the bridge's outgoing SSE queue —
        # this is the ApprovalRequestedEvent the frontend renders as a card.
        event = await asyncio.wait_for(bridge.get_event(), timeout=5.0)
        assert event["type"] == "tool_approval_request"
        assert event["tool_name"] == "drop_database"
        request_id = event["request_id"]

        # Nothing has executed yet — the run is genuinely blocked pending
        # the human decision, not racing ahead.
        assert bridge.has_pending

        resolved = await bridge.resolve(request_id, {"action": "approve"})
        assert resolved

        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                assert entry.kind == "run.completed", entry.payload
                break


async def test_critical_tool_call_denied_does_not_execute():
    bridge = WebHITLBridge(response_timeout=10.0)
    llm = _ScriptedLLMClient()
    thread_id = uuid.uuid4()

    async with Runtime() as rt:
        agent = await build_agent_for_thread(
            thread_id,
            model_client=llm,
            tools=[_DropDatabaseTool()],
            system_instructions="",
            cfg=SubstrateConfig(),
            history=InMemoryHistoryProvider(),
            runtime=rt,
            bridge=bridge,
        )

        msg = Message(
            target=agent.id,
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text="drop it")])
            ),
        )
        run_id = await rt.submit(agent.id, msg, max_retries=0)

        event = await asyncio.wait_for(bridge.get_event(), timeout=5.0)
        request_id = event["request_id"]
        await bridge.resolve(request_id, {"action": "deny"})

        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                assert entry.kind == "run.completed"
                break

        # The tool never actually ran — only the denial-error result did.
        found_tool_result = False
        async for entry in rt.event_log.read(run_id):
            if entry.kind == "tool.result":
                found_tool_result = True
                assert entry.payload.get("ok") is False
                assert "denied" in (entry.payload.get("output") or "").lower()
        assert found_tool_result
