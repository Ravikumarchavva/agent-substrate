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

from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.messaging.stream import (
    AgentProgress,
    AgentStep,
    CompletionEvent,
    StreamDone,
    TextDelta,
    ReasoningDelta,
)

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
) -> AsyncIterator[Any]:
    """Submit *task* and yield UI stream events as the run progresses."""
    msg = Message(
        target=agent.id,
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

        if kind == "text.delta":
            delta = p.get("text", "")
            final_text += delta
            yield TextDelta(text=delta)

        elif kind == "reasoning.delta":
            yield ReasoningDelta(text=p.get("text", ""))

        elif kind == "tool.call":
            yield AgentProgress(
                agent_id=agent.id,
                step=AgentStep.TOOL_CALL,
                content=p.get("tool_name", "tool"),
                run_id=run_id,
                seq=seq,
            )

        elif kind == "tool.result":
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
                boards = await _task_boards(correlation_id)
                if boards:
                    yield _TaskBoardUpdate(boards=boards)

        elif kind == "subagent.start":
            yield _subagent_progress(p, run_id, seq, AgentStep.THINKING)

        elif kind == "subagent.done":
            step = AgentStep.DONE if p.get("ok", True) else AgentStep.ERROR
            yield _subagent_progress(p, run_id, seq, step)

        elif kind == "run.completed":
            yield CompletionEvent(
                content=[TextBlock(text=final_text)],
                metadata={"finish_reason": "stop"},
            )
            yield StreamDone(reason="success")
            return

        elif kind == "run.failed":
            yield _RunFailed(
                message=str(p.get("error", "The run failed.")),
                status=str(p.get("status", "agent_crashed")),
            )
            yield StreamDone(reason="error")
            return

        elif kind == "run.cancelled":
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
        agent_id=AgentId(type="agent", key=agent_key),
        step=step,
        content=str(payload.get("task", "")),
        run_id=run_id,
        parent_id=AgentId(type="agent", key=str(parent_key)) if parent_key else None,
        depth=1,
        seq=seq,
    )


async def _task_boards(correlation_id: str) -> list[Any]:
    from substrate.agents.storage.tasks import GlobalTaskStore

    return await GlobalTaskStore.get().get_boards_by_conversation(correlation_id)
