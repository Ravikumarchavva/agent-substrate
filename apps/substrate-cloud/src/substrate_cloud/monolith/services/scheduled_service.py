"""Service layer for scheduled tasks execution and management."""

from __future__ import annotations

import logging

import time
import uuid
from datetime import datetime, timedelta, timezone
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from substrate.tools import ToolRisk
from substrate.types import RunLogKind
from substrate_cloud.shared.settings import settings
from substrate_cloud.monolith.database import system_session
from substrate_cloud.monolith.models import Agent, ScheduledTask, ScheduledTaskRun, Thread
from substrate_cloud.monolith.services.agents.assembly import assemble_agent
from substrate_cloud.monolith.services.agents.service import AgentProfile
from substrate_cloud.monolith.services.notification_service import notify, send_email
from substrate_cloud.factory import (
    build_agent_for_thread,
    build_chat_tools,
)
from substrate.runtime import Message, ChatPayload
from substrate.types import (
    ChatMessage as KernelChatMessage,
    Role,
    TextBlock as KernelTextBlock,
)
from substrate.types import Actor

logger = logging.getLogger(__name__)


def format_lookback_context(runs: list[ScheduledTaskRun]) -> str:
    """Format past runs into a preamble for lookback/self-learning context."""
    if not runs:
        return "This is the first execution of this task. No previous history."

    lines = ["## Previous Execution History (most recent first)\n"]
    lines.append("Review these past outputs. Avoid repeating the same information. ")
    lines.append("If you gave advice before, evaluate whether it was correct.\n")

    for run in runs:
        if run.was_silent:
            lines.append(
                f"- **{run.executed_at.strftime('%b %d, %H:%M')}**: [Silent check — condition not met]"
            )
        else:
            lines.append(
                f"### Run at {run.executed_at.strftime('%b %d, %Y %H:%M')} UTC"
            )
            lines.append(run.output_summary)
            lines.append("")

    return "\n".join(lines)


async def claim_firing(
    db: AsyncSession, task_id: uuid.UUID, *, min_gap_s: float
) -> bool:
    """Win the right to run this firing. Every replica of the server hears every tick; the one whose ``UPDATE`` finds the task not claimed
    within the last ``min_gap_s`` seconds runs it, and the rest see zero rows updated and skip. (Run now, by hand, does not claim.)"""
    now = datetime.now(timezone.utc)
    done = await db.execute(
        update(ScheduledTask)
        .where(
            ScheduledTask.id == task_id,
            (ScheduledTask.last_claimed_at.is_(None))
            | (ScheduledTask.last_claimed_at < now - timedelta(seconds=min_gap_s)),
        )
        .values(last_claimed_at=now)
    )
    await db.commit()
    return done.rowcount == 1


def firing_gap_seconds(task: ScheduledTask) -> float:
    """Shortest time that can honestly separate two firings of ``task``: half an interval, or 30 s for a cron (whose smallest step is a minute)."""
    if task.kind == "interval":
        try:
            return max(1.0, int(task.cron_expression) / 2)
        except ValueError:
            return 30.0
    return 30.0


def run_cost(entries: list[Any]) -> tuple[int, float]:
    """``(tokens, cost in USD)`` of the model calls in a run's journal."""
    tokens, cost = 0, 0.0
    for entry in entries:
        if entry.kind == RunLogKind.LLM_CALL:
            p = entry.payload or {}
            tokens += int(p.get("tokens") or 0)
            cost += float(p.get("cost_usd") or 0.0)
    return tokens, cost


def waiting_summary(kind: str, payload: dict[str, Any]) -> str:
    """What a suspended run is waiting for, in a line a person can read in a notification."""
    if kind == RunLogKind.APPROVAL_REQUESTED:
        return f"Wants to use {payload.get('tool_name', 'a tool')} and needs your approval."
    return str(payload.get("question") or "It has a question for you.")[:300]


async def execute_scheduled_task(
    task_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    app_state: Any,
    manual: bool = False,
) -> None:
    """Execute a single scheduled task run. ``manual`` (Run now) skips the replica claim a timed firing needs."""
    logger.info("Executing scheduled task: %s", task_id)
    async with system_session(session_factory) as db:
        start = time.monotonic()
        try:
            task = await db.get(ScheduledTask, task_id)
            if not task:
                logger.warning("Scheduled task not found: %s", task_id)
                return
            if not manual and not await claim_firing(
                db, task_id, min_gap_s=firing_gap_seconds(task)
            ):
                logger.info("Scheduled task %s was claimed by another replica", task_id)
                return
            if task.status != "active":
                logger.info(
                    "Scheduled task %s is not active (status: %s)", task_id, task.status
                )
                return

            # The owning thread may have been soft-deleted (user deleted the
            # conversation from the sidebar) without the task itself being
            # touched — without this check the task keeps running forever
            # against a conversation its owner believes is gone. Pause it
            # rather than silently skip-and-retry-next-time so it doesn't
            # burn compute on every future firing too.
            thread = await db.get(Thread, task.thread_id)
            if thread is None or thread.deleted_at is not None:
                logger.info(
                    "Scheduled task %s's thread %s was deleted; pausing task",
                    task_id,
                    task.thread_id,
                )
                task.status = "paused"
                await db.commit()
                return
            if thread.locked_at is not None:
                # A file this conversation depends on was deleted from
                # storage (routes/workspace.py/routes/files.py delete_file)
                # — same reason POST /chat 423s, applied here too so a
                # scheduled run doesn't silently keep acting on a thread
                # whose context is now missing a file.
                logger.info(
                    "Scheduled task %s's thread %s is locked (%s); pausing task",
                    task_id,
                    task.thread_id,
                    thread.locked_reason,
                )
                task.status = "paused"
                await db.commit()
                return

            # 1. Fetch recent runs for lookback
            stmt = (
                select(ScheduledTaskRun)
                .where(ScheduledTaskRun.task_id == task_id)
                .order_by(ScheduledTaskRun.executed_at.desc())
                .limit(task.lookback_runs)
            )
            result = await db.execute(stmt)
            recent_runs = list(result.scalars().all())

            # 2. Build lookback context
            lookback_block = format_lookback_context(recent_runs)

            # 3. The task's own instructions, with what previous runs did
            task_block = (
                f"---\n"
                f'**You are executing a scheduled task: "{task.name}"**\n'
                f"Current date/time: {datetime.now(timezone.utc).isoformat()}\n\n"
                f"Task instructions: {task.prompt}\n\n"
                f"{lookback_block}\n\n"
                f"IMPORTANT: Do NOT repeat information from previous runs. "
                f"Build on past context. If this is a monitoring task and the "
                f"condition is NOT met, respond with exactly: [SILENT_CHECK]\n"
            )
            # Off means high-risk tools run on their own; destructive ones still ask.
            approval_risk = None if task.ask_before_acting else ToolRisk.CRITICAL

            # 4-5. Build the agent. A task on an agent's conversation runs as that agent: its role, the tools it may use, its files.
            workspace_id = None
            agent_row = await db.get(Agent, thread.agent_id) if thread.agent_id else None
            if agent_row is not None:
                profile = AgentProfile.of(agent_row)
                workspace_id = profile.workspace_id
                agent = await assemble_agent(
                    app_state,
                    profile,
                    session_id=task.thread_id,
                    extra_instructions="\n\n" + task_block,
                    approval_required_risk=approval_risk,
                )
            else:
                bridge = await app_state.bridge_registry.acquire(str(task.thread_id))
                agent = await build_agent_for_thread(
                    task.thread_id,
                    model_client=app_state.model_client,
                    tools=build_chat_tools(app_state.tools, bridge),
                    system_instructions=f"{app_state.system_instructions}\n\n{task_block}",
                    cfg=settings,
                    history=app_state.history,
                    short_term_memory=app_state.short_term_memory,
                    long_term_memory=app_state.long_term_memory,
                    user_id=str(task.user_id) if task.user_id else None,
                    tenant_id=thread.tenant_id,
                    runtime=app_state.runtime,
                    safety_middleware=app_state.safety_middleware,
                    approval_required_risk=approval_risk,
                )

            # 6. Submit to runtime
            msg = Message(
                target=agent.id,
                sender=Actor(type="job_proxy"),
                payload=ChatPayload(
                    message=KernelChatMessage(
                        role=Role.USER, content=[KernelTextBlock(text=task.prompt)]
                    )
                ),
                correlation_id=str(task.thread_id),
                # Whose run this is, so tools that act for a person (memory) act for this one.
                metadata={
                    **({"user_id": str(task.user_id)} if task.user_id else {}),
                    **({"workspace_id": workspace_id} if workspace_id else {}),
                },
            )

            start = time.monotonic()
            await app_state.runtime.register(agent)
            # thread_id=: tags this run so it appears in the thread's history
            # via project_thread() (the EventLogProtocol is the single source of
            # truth for conversation history — see substrate_cloud/stream/history.py
            # — there's no separate steps-table write needed here anymore).
            run_id = await app_state.runtime.submit(
                agent.id, msg, thread_id=str(task.thread_id)
            )

            output_text = ""
            waiting_for: str | None = None
            async for entry in app_state.runtime.tail(run_id):
                kind = entry.kind
                p = entry.payload or {}
                if kind == RunLogKind.ASSISTANT_MESSAGE:
                    output_text += p.get("text", "")
                elif kind == RunLogKind.RUN_COMPLETED:
                    break
                elif kind == RunLogKind.RUN_FAILED:
                    error = p.get("error", "Agent run failed")
                    raise RuntimeError(error)
                elif kind in (
                    RunLogKind.APPROVAL_REQUESTED,
                    RunLogKind.INPUT_REQUESTED,
                ):
                    # The run is parked, costing nothing, until the user answers in the conversation. Do not hold this task (and its
                    # database session) open for however long that takes: record it, tell the user, and let the run resume later.
                    waiting_for = waiting_summary(kind, p)
                    break
            tokens, cost = run_cost(await app_state.runtime.read(run_id))

            duration_ms = int((time.monotonic() - start) * 1000)
            is_silent = "[SILENT_CHECK]" in output_text

            # 7. Persist run log
            run = ScheduledTaskRun(
                task_id=task.id,
                status=(
                    "waiting" if waiting_for else "silent" if is_silent else "success"
                ),
                output_summary=(waiting_for or output_text)[:500],
                duration_ms=duration_ms,
                was_silent=is_silent,
                tokens=tokens,
                cost_usd=cost,
            )
            db.add(run)
            if waiting_for:
                await _tell(
                    db,
                    task,
                    thread,
                    "approval",
                    f"“{task.name}” needs you",
                    waiting_for,
                    app_state,
                )
            elif not is_silent:
                await _tell(
                    db,
                    task,
                    thread,
                    "task_run",
                    f"“{task.name}” finished",
                    output_text,
                    app_state,
                )

            # The run's user.message/text.delta are already durably in the
            # EventLogProtocol (ReActAgent logs them unconditionally) and will show
            # up via project_thread() since the run is thread_id-tagged
            # above — no separate persistence needed. The old "don't show
            # silent monitoring checks in chat" filter is now a display-time
            # concern (skip an assistant turn whose text is exactly
            # "[SILENT_CHECK]") rather than a write-time one, since the
            # EventLogProtocol can't be filtered retroactively — see
            # substrate-ui's history-fold.ts.
            if not is_silent:
                # Auto-disable if one-shot
                if task.auto_disable:
                    task.status = "completed"

            task.updated_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info(
                "Successfully executed scheduled task %s (silent=%s)",
                task_id,
                is_silent,
            )

        except Exception as exc:
            logger.exception("Error executing scheduled task %s", task_id)
            try:
                duration_ms = int((time.monotonic() - start) * 1000)
                run = ScheduledTaskRun(
                    task_id=task_id,
                    status="failed",
                    output_summary="",
                    duration_ms=duration_ms,
                    was_silent=False,
                    error_message=str(exc),
                )
                db.add(run)
                failed = await db.get(ScheduledTask, task_id)
                failed_thread = (
                    await db.get(Thread, failed.thread_id) if failed else None
                )
                if failed is not None and failed_thread is not None:
                    await _tell(
                        db, failed, failed_thread, "task_failed", f"“{failed.name}” failed",
                        "The scheduled run did not complete. Open it to see what happened, or run it again.", app_state,
                    )  # fmt: skip
                await db.commit()
            except Exception as db_exc:
                logger.error(
                    "Failed to persist failed run log for task %s: %s", task_id, db_exc
                )


async def _tell(
    db: AsyncSession,
    task: ScheduledTask,
    thread: Thread,
    kind: str,
    title: str,
    body: str,
    app_state: Any,
) -> None:
    """A notification for the task's owner, and an email when they asked for results by email. Never raises: telling someone is secondary
    to the run having happened."""
    try:
        if thread.user_identifier:
            await notify(
                db,
                tenant_id=thread.tenant_id or "default",
                user_identifier=thread.user_identifier,
                kind=kind,
                title=title,
                body=body,
                thread_id=task.thread_id,
            )
        if task.email_results and task.notify_email:
            await send_email(
                to=task.notify_email,
                subject=title,
                text=f"{body}\n\nOpen the conversation in the app to read it in full.",
                api_key=getattr(settings, "RESEND_API_KEY", ""),
                sender=getattr(settings, "NOTIFY_FROM_EMAIL", ""),
            )
    except Exception:  # noqa: BLE001
        logger.exception("could not notify about scheduled task %s", task.id)


def missed_firing(
    kind: str, expression: str, since: datetime, now: datetime
) -> bool:
    """Did a schedule come due between ``since`` and ``now`` (so the server was down for it)?"""
    if kind == "interval":
        try:
            return now - since >= timedelta(seconds=int(expression))
        except ValueError:
            return False
    from apscheduler.triggers.cron import CronTrigger

    try:
        due = CronTrigger.from_crontab(expression, start_time=since).next()
    except Exception:  # noqa: BLE001 - a bad expression is reported where it is scheduled
        return False
    return due is not None and due <= now


async def catch_up_missed_tasks(
    session_factory: async_sessionmaker[AsyncSession],
    run: Callable[[uuid.UUID], Awaitable[None]],
    *,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    """Run, once each, the active tasks whose firing came due while the server was down. The scheduler lives in memory and only looks forward,
    so without this a restart silently skips a daily report. One catch-up per task however many firings were missed, one after another."""
    now = now or datetime.now(timezone.utc)
    async with system_session(session_factory) as db:
        last_run = {
            task_id: at
            for task_id, at in (
                await db.execute(
                    select(
                        ScheduledTaskRun.task_id, func.max(ScheduledTaskRun.executed_at)
                    ).group_by(ScheduledTaskRun.task_id)
                )
            ).all()
        }
        tasks = (
            (
                await db.execute(
                    select(ScheduledTask).where(ScheduledTask.status == "active")
                )
            )
            .scalars()
            .all()
        )
        overdue = []
        for task in tasks:
            seen = [
                t
                for t in (task.last_claimed_at, last_run.get(task.id), task.created_at)
                if t is not None
            ]
            if seen and missed_firing(task.kind, task.cron_expression, max(seen), now):
                overdue.append(task.id)
    for task_id in overdue:
        try:
            await run(task_id)
        except Exception:  # noqa: BLE001 - one bad task must not stop the rest
            logger.exception("catch-up run of scheduled task %s failed", task_id)
    return overdue


async def load_active_tasks_into_scheduler(
    scheduler: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Load active scheduled tasks from database and schedule them in TriggerScheduler."""
    logger.info("Loading active scheduled tasks into trigger scheduler...")
    async with system_session(session_factory) as db:
        stmt = select(ScheduledTask).where(ScheduledTask.status == "active")
        result = await db.execute(stmt)
        tasks = result.scalars().all()
        for task in tasks:
            try:
                await scheduler.add_scheduled_task(
                    task.id, task.cron_expression, task.kind
                )
                logger.info(
                    "Scheduled task %s ('%s') loaded successfully", task.id, task.name
                )
            except Exception as exc:
                logger.error(
                    "Failed to load scheduled task %s into scheduler: %s", task.id, exc
                )
