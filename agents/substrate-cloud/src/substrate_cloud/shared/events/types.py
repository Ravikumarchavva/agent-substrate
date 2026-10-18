"""The domain events that cross a service boundary.

Each event type is one Redis Stream (``events:<event_type>``). Only an event with both a producer and a consumer is defined here:

    event                          produced by              consumed by
    conversation.thread_created    conversation             admin (audit)
    conversation.thread_deleted    conversation             admin (audit)
    workflow.run_started           job_controller           admin (audit)
    workflow.run_completed         agent_runtime            job_controller (complete_run), admin (audit)
    workflow.run_failed            agent_runtime, job_controller   job_controller (fail_run), admin (audit)
    workflow.run_cancelled         agent_runtime            job_controller (cancel_run), admin (audit)
    job.cancel_requested           job_controller           agent_runtime (cancels the run)

What a client watches while a run is going (text, tool calls, approvals) is not an event here: ``live_stream`` serves it from the
run's durable journal, the same record the monolith streams, so nothing is lost when a consumer is down.

Every payload names the ``tenant_id`` and ``user_id`` the event is about, taken from the verified token of the request that began it.
"""

from __future__ import annotations

from typing import Any

from substrate.integrations.events.envelope import EventEnvelope

THREAD_CREATED = "conversation.thread_created"
THREAD_DELETED = "conversation.thread_deleted"
RUN_STARTED = "workflow.run_started"
RUN_COMPLETED = "workflow.run_completed"
RUN_FAILED = "workflow.run_failed"
RUN_CANCELLED = "workflow.run_cancelled"
CANCEL_REQUESTED = "job.cancel_requested"

AUDITED = (
    THREAD_CREATED,
    THREAD_DELETED,
    RUN_STARTED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_CANCELLED,
)


def thread_created(
    thread_id: str, name: str, *, tenant_id: str, user_id: str
) -> EventEnvelope:
    return EventEnvelope(
        event_type=THREAD_CREATED,
        payload={
            "thread_id": thread_id,
            "name": name,
            "tenant_id": tenant_id,
            "user_id": user_id,
        },
    )


def thread_deleted(thread_id: str, *, tenant_id: str, user_id: str) -> EventEnvelope:
    return EventEnvelope(
        event_type=THREAD_DELETED,
        payload={"thread_id": thread_id, "tenant_id": tenant_id, "user_id": user_id},
    )


def _run(
    event_type: str,
    run_id: str,
    thread_id: str,
    tenant_id: str,
    user_id: str,
    **extra: Any,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        payload={
            "run_id": run_id,
            "thread_id": thread_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            **extra,
        },
    )


def workflow_started(
    run_id: str, thread_id: str, *, tenant_id: str, user_id: str
) -> EventEnvelope:
    return _run(RUN_STARTED, run_id, thread_id, tenant_id, user_id)


def workflow_completed(
    run_id: str, thread_id: str, *, tenant_id: str, user_id: str, steps: int = 0
) -> EventEnvelope:
    return _run(RUN_COMPLETED, run_id, thread_id, tenant_id, user_id, steps=steps)


def workflow_failed(
    run_id: str, thread_id: str, *, tenant_id: str, user_id: str, error: str = ""
) -> EventEnvelope:
    return _run(RUN_FAILED, run_id, thread_id, tenant_id, user_id, error=error)


def workflow_cancelled(
    run_id: str, thread_id: str, *, tenant_id: str, user_id: str
) -> EventEnvelope:
    return _run(RUN_CANCELLED, run_id, thread_id, tenant_id, user_id)


def job_cancel_requested(
    run_id: str,
    thread_id: str,
    *,
    tenant_id: str,
    user_id: str,
    runtime_run_id: str = "",
) -> EventEnvelope:
    return _run(
        CANCEL_REQUESTED,
        run_id,
        thread_id,
        tenant_id,
        user_id,
        runtime_run_id=runtime_run_id,
    )
