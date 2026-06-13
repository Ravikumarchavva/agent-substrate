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

import json

from ravi.kernel.core.content import TextBlock, ToolUseBlock, UIResourceBlock
from ravi.kernel.messaging.stream import (
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
    TextDeltaEvent,
    ToolCallEvent,
    ToolCallSummary,
    ToolResultEvent,
    TurnCompletedEvent,
    UIResourceEvent,
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


def _progress_to_wire(ev: AgentProgress) -> WireEvent | list[WireEvent] | None:
    agent = ev.agent_id.key if ev.agent_id else ""
    call_id = ev.metadata.get("call_id", "")

    if ev.step == AgentStep.TOOL_CALL:
        name = ev.content
        if name.startswith(_HANDOFF_PREFIX):
            return HandoffEvent(
                source_agent=agent,
                target_agent=name[len(_HANDOFF_PREFIX) :],
                depth=ev.depth,
            )
        args_raw = ev.metadata.get("tool_args", "{}")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else {}
        except (json.JSONDecodeError, TypeError):
            args = {}
        return ToolCallEvent(
            call_id=call_id, tool_name=name, agent=agent, depth=ev.depth, args=args
        )

    if ev.step == AgentStep.TOOL_RESULT:
        # content is "tool_name: ok" or "tool_name: error"
        name, _, status = ev.content.rpartition(": ")
        name = name or ev.content
        if name.startswith(_HANDOFF_PREFIX):
            return None  # subagent events + the orchestrator's next turn convey this
        result = ToolResultEvent(
            call_id=call_id,
            tool_name=name,
            ok=(status != "error"),
            error=None if status != "error" else "tool error",
            agent=agent,
            depth=ev.depth,
        )
        # A tool that renders a UI lowers a UIResourceBlock; react.py threads it
        # through progress metadata as JSON. Fan out a second wire event for it.
        ui_json = ev.metadata.get("ui")
        if ui_json:
            blk = UIResourceBlock.model_validate_json(ui_json)
            return [
                result,
                UIResourceEvent(
                    call_id=call_id,
                    uri=blk.uri,
                    mime_type=blk.mime_type,
                    structured_content=blk.structured_content,
                    render=blk.render,
                    text=blk.text,
                    agent=agent,
                    depth=ev.depth,
                ),
            ]
        return result

    if ev.step == AgentStep.HANDOFF:
        # "→ target: reason"  (published to the topic; rarely reaches run_stream)
        body = ev.content.lstrip("→ ").strip()
        target, _, reason = body.partition(": ")
        return HandoffEvent(
            source_agent=agent, target_agent=target, reason=reason, depth=ev.depth
        )

    # THINKING / STARTED / DONE / PAUSED / ERROR — not surfaced as discrete UI events
    return None


def map_kernel_event(ev: object) -> WireEvent | list[WireEvent] | None:
    """Translate one kernel stream event to a wire event (or None to drop it).

    Returns a list when one kernel event fans out (e.g. a tool result that also
    carries a UI resource → ``tool.result`` + ``ui.resource``)."""
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
    """Translate an out-of-band HITL bridge dict to a wire event.

    The ``WebHITLBridge`` emits legacy dict shapes for approval / input gates;
    this is the single place that adapts them to the wire protocol. Rich tool
    UIs (kanban, …) no longer travel this path — they flow inline as
    ``ui.resource`` via the tool result (see ``_progress_to_wire``).
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
            question=data.get("question") or data.get("prompt", ""),
            context=data.get("context", ""),
            options=data.get("options") or [],
            allow_freeform=data.get("allow_freeform", True),
        )
    return None


__all__ = ["map_kernel_event", "map_bridge_event"]
