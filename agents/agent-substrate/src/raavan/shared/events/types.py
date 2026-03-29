"""Domain event type definitions for inter-service communication.

Each event_type corresponds to a Redis Stream. Services declare which
event types they produce and consume in their service manifest.
"""

from __future__ import annotations

from typing import Any, Dict

from raavan.shared.events.envelope import EventEnvelope


# ── Identity & Auth Events ───────────────────────────────────────────────────


def user_created(
    user_id: str, email: str, role: str = "user", **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="identity.user_created",
        payload={"user_id": user_id, "email": email, "role": role, **kw},
    )


def user_updated(user_id: str, changes: Dict[str, Any], **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_type="identity.user_updated",
        payload={"user_id": user_id, "changes": changes, **kw},
    )


def session_started(user_id: str, session_id: str, **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_type="identity.session_started",
        payload={"user_id": user_id, "session_id": session_id, **kw},
    )


def session_ended(user_id: str, session_id: str, **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_type="identity.session_ended",
        payload={"user_id": user_id, "session_id": session_id, **kw},
    )


# ── Conversation Events ─────────────────────────────────────────────────────


def thread_created(
    thread_id: str, name: str, user_id: str = "", **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="conversation.thread_created",
        payload={"thread_id": thread_id, "name": name, "user_id": user_id, **kw},
    )


def thread_deleted(thread_id: str, **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_type="conversation.thread_deleted", payload={"thread_id": thread_id, **kw}
    )


def message_persisted(
    thread_id: str, step_id: str, step_type: str, **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="conversation.message_persisted",
        payload={
            "thread_id": thread_id,
            "step_id": step_id,
            "step_type": step_type,
            **kw,
        },
    )


# ── Workflow Events ──────────────────────────────────────────────────────────


def workflow_started(
    run_id: str, thread_id: str, user_content: str, **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="workflow.run_started",
        payload={
            "run_id": run_id,
            "thread_id": thread_id,
            "user_content": user_content,
            **kw,
        },
    )


def workflow_completed(
    run_id: str, thread_id: str, status: str = "completed", **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="workflow.run_completed",
        payload={"run_id": run_id, "thread_id": thread_id, "status": status, **kw},
    )


def workflow_failed(
    run_id: str, thread_id: str, error: str = "", **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="workflow.run_failed",
        payload={"run_id": run_id, "thread_id": thread_id, "error": error, **kw},
    )


def workflow_cancelled(run_id: str, thread_id: str, **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_type="workflow.run_cancelled",
        payload={"run_id": run_id, "thread_id": thread_id, **kw},
    )


# ── Agent Runtime Events ────────────────────────────────────────────────────


def agent_step_started(run_id: str, step: int, **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_type="agent.step_started", payload={"run_id": run_id, "step": step, **kw}
    )


def agent_text_delta(run_id: str, content: str, **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_type="agent.text_delta",
        payload={"run_id": run_id, "content": content, **kw},
    )


def agent_reasoning_delta(run_id: str, content: str, **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_type="agent.reasoning_delta",
        payload={"run_id": run_id, "content": content, **kw},
    )


def agent_completion(run_id: str, message: Dict[str, Any], **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_type="agent.completion",
        payload={"run_id": run_id, "message": message, **kw},
    )


# ── Tool Executor Events ────────────────────────────────────────────────────


def tool_call_requested(
    run_id: str, tool_name: str, tool_call_id: str, arguments: Dict[str, Any], **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="tool.call_requested",
        payload={
            "run_id": run_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
            **kw,
        },
    )


def tool_call_completed(
    run_id: str,
    tool_call_id: str,
    tool_name: str,
    result: str,
    is_error: bool = False,
    **kw: Any,
) -> EventEnvelope:
    return EventEnvelope(
        event_type="tool.call_completed",
        payload={
            "run_id": run_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "result": result,
            "is_error": is_error,
            **kw,
        },
    )


# ── HITL Events ──────────────────────────────────────────────────────────────


def hitl_approval_requested(
    run_id: str, request_id: str, tool_name: str, arguments: Dict[str, Any], **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="hitl.approval_requested",
        payload={
            "run_id": run_id,
            "request_id": request_id,
            "tool_name": tool_name,
            "arguments": arguments,
            **kw,
        },
    )


def hitl_approval_resolved(request_id: str, action: str, **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_type="hitl.approval_resolved",
        payload={"request_id": request_id, "action": action, **kw},
    )


def hitl_input_requested(
    run_id: str, request_id: str, prompt: str, **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="hitl.input_requested",
        payload={"run_id": run_id, "request_id": request_id, "prompt": prompt, **kw},
    )


def hitl_input_resolved(
    request_id: str, response: Dict[str, Any], **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="hitl.input_resolved",
        payload={"request_id": request_id, "response": response, **kw},
    )


# ── Task Board Events ───────────────────────────────────────────────────────


def task_list_created(
    thread_id: str, task_list: Dict[str, Any], **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="task.list_created",
        payload={"thread_id": thread_id, "task_list": task_list, **kw},
    )


def task_updated(
    thread_id: str, task_list_id: str, task: Dict[str, Any], **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="task.updated",
        payload={
            "thread_id": thread_id,
            "task_list_id": task_list_id,
            "task": task,
            **kw,
        },
    )


def task_added(
    thread_id: str, task_list_id: str, task: Dict[str, Any], **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="task.added",
        payload={
            "thread_id": thread_id,
            "task_list_id": task_list_id,
            "task": task,
            **kw,
        },
    )


# ── Artifact Events ─────────────────────────────────────────────────────────


def artifact_uploaded(
    file_id: str, thread_id: str, name: str, mime: str, size: int, **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="artifact.uploaded",
        payload={
            "file_id": file_id,
            "thread_id": thread_id,
            "name": name,
            "mime": mime,
            "size": size,
            **kw,
        },
    )


def artifact_deleted(file_id: str, thread_id: str, **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_type="artifact.deleted",
        payload={"file_id": file_id, "thread_id": thread_id, **kw},
    )


# ── Stream / SSE Events ─────────────────────────────────────────────────────


def stream_event(
    thread_id: str, event_data: Dict[str, Any], **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="stream.sse_event",
        payload={"thread_id": thread_id, "event_data": event_data, **kw},
    )


# ── Admin / Audit Events ────────────────────────────────────────────────────


def audit_action(
    actor_id: str, action: str, resource_type: str, resource_id: str, **kw: Any
) -> EventEnvelope:
    return EventEnvelope(
        event_type="admin.audit_action",
        payload={
            "actor_id": actor_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            **kw,
        },
    )
