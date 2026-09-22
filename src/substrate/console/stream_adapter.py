"""Stream adapter — turn a Runtime event-log tail into typed UI events.

Submits a task to the runtime and yields the kernel stream events the renderer
consumes (``TextDelta``, ``ReasoningDelta``, ``AgentProgress``, ``CompletionEvent``,
``StreamDone``) plus a ``_TaskBoardUpdate`` after ``manage_tasks`` runs.

Two progress sources are merged onto one ``AgentProgress`` stream:
  * ``tool.call`` / ``tool.result``  → the main agent's tool cards (depth 0)
  * ``subagent.start`` / ``subagent.done`` → orchestrator subagent tree (depth 1)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator

from substrate.kernel.runtime.log_entry import RunLogKind
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.core.identity import Actor
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.messaging.stream import (
    AgentProgress,
    AgentStep,
    CompletionEvent,
    StreamDone,
    TextDelta,
    ReasoningDelta,
)

from substrate.integrations.tools.human_input import InputOption

from .hitl import _HITLRequest
from .taskboard import _TaskBoardUpdate


@dataclass
class _RunFailed:
    """Internal event: the run ended in failure / block / cancellation."""

    message: str
    status: str = "agent_crashed"


async def stream_events(
    runtime: Any,
    agent: Any,
    task: str,
    *,
    correlation_id: str,
    task_store: Any = None,
) -> AsyncIterator[Any]:
    """Submit *task* and yield UI stream events as the run progresses."""
    msg = Message(
        target=agent.id,
        sender=Actor(type="cli", key="stream_adapter"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=task)])
        ),
        correlation_id=correlation_id,
    )
    await runtime.register(agent)
    run_id = await runtime.submit(agent.id, msg)
    final_text = ""

    async for entry in runtime.event_log.tail(run_id):
        kind = entry.kind
        p = entry.payload or {}
        seq = int(getattr(entry, "seq", 0) or 0)

        if kind == RunLogKind.TEXT_DELTA:
            delta = p.get("text", "")
            final_text += delta
            yield TextDelta(text=delta)

        elif kind == RunLogKind.REASONING_DELTA:
            yield ReasoningDelta(text=p.get("text", ""))

        elif kind == RunLogKind.TOOL_CALL:
            yield AgentProgress(
                agent_id=agent.id,
                step=AgentStep.TOOL_CALL,
                content=p.get("tool_name", "tool"),
                run_id=run_id,
                seq=seq,
            )

        elif kind == RunLogKind.TOOL_RESULT:
            name = p.get("tool_name", "tool")
            content = name if p.get("ok", True) else f"{name} error"
            yield AgentProgress(
                agent_id=agent.id,
                step=AgentStep.TOOL_RESULT,
                content=content,
                run_id=run_id,
                seq=seq,
            )
            if name == "manage_tasks":
                boards = await _task_boards(correlation_id, task_store)
                if boards:
                    yield _TaskBoardUpdate(boards=boards)

        elif kind == RunLogKind.SUBAGENT_START:
            yield _subagent_progress(p, run_id, seq, AgentStep.THINKING)

        elif kind == RunLogKind.SUBAGENT_DONE:
            step = AgentStep.DONE if p.get("ok", True) else AgentStep.ERROR
            yield _subagent_progress(p, run_id, seq, step)

        elif kind == RunLogKind.RUN_COMPLETED:
            yield CompletionEvent(
                content=[TextBlock(text=final_text)],
                metadata={"finish_reason": "stop"},
            )
            yield StreamDone(reason="success")
            return

        elif kind == RunLogKind.RUN_FAILED:
            yield _RunFailed(
                message=str(p.get("error", "The run failed.")),
                status=str(p.get("status", "agent_crashed")),
            )
            yield StreamDone(reason="error")
            return

        elif kind == RunLogKind.INPUT_REQUESTED:
            opts = [
                InputOption(
                    key=str(o.get("key", "")),
                    label=str(o.get("label", "")),
                    description=str(o.get("description", "")),
                )
                for o in p.get("options", [])
                if isinstance(o, dict)
            ]
            yield _HITLRequest(
                request_id=str(p.get("request_id", "")),
                question=str(p.get("question", "")),
                context=str(p.get("context", "")),
                options=opts,
                allow_freeform=bool(p.get("allow_freeform", True)),
                run_id=str(p.get("run_id", "")),
            )

        elif kind == RunLogKind.RUN_CANCELLED:
            yield _RunFailed(message="The run was cancelled.", status="cancelled")
            yield StreamDone(reason="cancelled")
            return


def _subagent_progress(
    payload: dict[str, Any], run_id: str, seq: int, step: AgentStep
) -> AgentProgress:
    """Build a depth-1 progress event for an orchestrator subagent."""
    agent_key = str(payload.get("agent", "subagent"))
    parent_key = payload.get("parent")
    return AgentProgress(
        agent_id=Actor(type="agent", key=agent_key),
        step=step,
        content=str(payload.get("task", "")),
        run_id=run_id,
        parent_id=Actor(type="agent", key=str(parent_key)) if parent_key else None,
        depth=1,
        seq=seq,
    )


async def _task_boards(correlation_id: str, task_store: Any = None) -> list[Any]:
    from substrate.agents.storage.tasks import TaskStore

    store = task_store or TaskStore()
    return await store.get_boards_by_conversation(correlation_id)
