"""Kernel stream events → wire events.

This is the *only* module that knows both vocabularies: the kernel stream types
(`ravi.kernel.stream`) emitted by `agent.run_stream()`, and the wire protocol
(`ravi.serving.protocol`) consumed by the UI. Keeping the translation in one
mechanical function is what stops the two sides from drifting.

`map_kernel_event` returns a single `WireEvent`, a list (when one kernel event
fans out), or `None` for events the UI does not surface (thinking/started ticks,
`StreamDone` — the session decides run lifecycle from its `reason`).
"""

from __future__ import annotations

from ravi.kernel.content import TextBlock, ToolUseBlock
from ravi.kernel.stream import (
    AgentProgress,
    AgentStep,
    CompletionEvent,
    ReasoningDelta,
    TextDelta,
)
from ravi.serving.protocol import (
    ApprovalRequestedEvent,
    HandoffEvent,
    InputRequestedEvent,
    ReasoningDeltaEvent,
    TaskAddedEvent,
    TaskCreatedEvent,
    TaskDeletedEvent,
    TaskUpdatedEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolCallSummary,
    ToolResultEvent,
    TurnCompletedEvent,
    WireEvent,
)

_HANDOFF_PREFIX = "handoff_"


def _completion_to_turn(event: CompletionEvent) -> TurnCompletedEvent:
    text = "".join(b.text for b in event.content if isinstance(b, TextBlock))
    tool_calls = [
        ToolCallSummary(id=b.call_id, name=b.tool_name, args=dict(b.arguments))
        for b in event.content
        if isinstance(b, ToolUseBlock)
    ]
    return TurnCompletedEvent(
        text=text,
        tool_calls=tool_calls,
        finish_reason=event.metadata.get("finish_reason", "stop"),
    )


def _progress_to_wire(ev: AgentProgress) -> WireEvent | None:
    agent = ev.agent_id.key if ev.agent_id else ""
    call_id = ev.metadata.get("call_id", "")

    if ev.step == AgentStep.TOOL_CALL:
        name = ev.content
        if name.startswith(_HANDOFF_PREFIX):
            return HandoffEvent(
                source_agent=agent,
                target_agent=name[len(_HANDOFF_PREFIX):],
                depth=ev.depth,
            )
        return ToolCallEvent(
            call_id=call_id, tool_name=name, agent=agent, depth=ev.depth
        )

    if ev.step == AgentStep.TOOL_RESULT:
        # content is "tool_name: ok" or "tool_name: error"
        name, _, status = ev.content.rpartition(": ")
        name = name or ev.content
        if name.startswith(_HANDOFF_PREFIX):
            return None  # subagent events + the orchestrator's next turn convey this
        return ToolResultEvent(
            call_id=call_id,
            tool_name=name,
            ok=(status != "error"),
            error=None if status != "error" else "tool error",
            agent=agent,
            depth=ev.depth,
        )

    if ev.step == AgentStep.HANDOFF:
        # "→ target: reason"  (published to the topic; rarely reaches run_stream)
        body = ev.content.lstrip("→ ").strip()
        target, _, reason = body.partition(": ")
        return HandoffEvent(
            source_agent=agent, target_agent=target, reason=reason, depth=ev.depth
        )

    # THINKING / STARTED / DONE / PAUSED / ERROR — not surfaced as discrete UI events
    return None


def map_kernel_event(ev: object) -> WireEvent | None:
    """Translate one kernel stream event to a wire event (or None to drop it)."""
    if isinstance(ev, TextDelta):
        return TextDeltaEvent(text=ev.text)
    if isinstance(ev, ReasoningDelta):
        return ReasoningDeltaEvent(text=ev.text)
    if isinstance(ev, CompletionEvent):
        return _completion_to_turn(ev)
    if isinstance(ev, AgentProgress):
        return _progress_to_wire(ev)
    # StreamDone and anything else → session decides run lifecycle
    return None


def map_bridge_event(data: dict) -> WireEvent | None:
    """Translate an out-of-band HITL/task bridge dict to a wire event.

    The ``WebHITLBridge`` and ``TaskManagerTool`` emit legacy dict shapes; this
    is the single place that adapts them to the wire protocol.
    """
    kind = data.get("type")

    if kind == "tool_approval_request":
        return ApprovalRequestedEvent(
            request_id=data.get("request_id") or data.get("requestId", ""),
            tool_name=data.get("tool_name", ""),
            args=data.get("arguments") or data.get("input") or {},
        )
    if kind == "human_input_request":
        return InputRequestedEvent(
            request_id=data.get("request_id") or data.get("requestId", ""),
            prompt=data.get("prompt", ""),
            options=data.get("options"),
        )
    if kind == "task_list_created":
        return TaskCreatedEvent(task_list=data.get("task_list", data))
    if kind == "task_updated":
        return TaskUpdatedEvent(task=data.get("task", data))
    if kind == "task_added":
        return TaskAddedEvent(task=data.get("task", data))
    if kind == "task_deleted":
        return TaskDeletedEvent(task_id=data.get("task_id") or data.get("id", ""))
    return None


__all__ = ["map_kernel_event", "map_bridge_event"]
