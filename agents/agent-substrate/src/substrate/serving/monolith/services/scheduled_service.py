"""Service layer for scheduled tasks execution and management."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from substrate.logger import setup_logging
from substrate.serving.shared.settings import settings
from substrate.serving.monolith.models import ScheduledTask, ScheduledTaskRun
from substrate.infrastructure.serving_factory import (
    build_agent_for_thread,
    build_chat_tools,
)
from substrate.kernel.messaging.message import Message, ChatPayload
from substrate.kernel.core.content import (
    ChatMessage as KernelChatMessage,
    Role,
    TextBlock as KernelTextBlock,
)
from substrate.kernel.core.identity import AgentId

logger = setup_logging()


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


async def execute_scheduled_task(
    task_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    app_state: Any,
) -> None:
    """Execute a single scheduled task run."""
    logger.info("Executing scheduled task: %s", task_id)
    async with session_factory() as db:
        start = time.monotonic()
        try:
            task = await db.get(ScheduledTask, task_id)
            if not task:
                logger.warning("Scheduled task not found: %s", task_id)
                return
            if task.status != "active":
                logger.info(
                    "Scheduled task %s is not active (status: %s)", task_id, task.status
                )
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

            # 3. Build system instructions with lookback preamble
            base_instructions = app_state.system_instructions
            scheduled_instructions = (
                f"{base_instructions}\n\n"
                f"---\n"
                f'**You are executing a scheduled task: "{task.name}"**\n'
                f"Current date/time: {datetime.now(timezone.utc).isoformat()}\n\n"
                f"Task instructions: {task.prompt}\n\n"
                f"{lookback_block}\n\n"
                f"IMPORTANT: Do NOT repeat information from previous runs. "
                f"Build on past context. If this is a monitoring task and the "
                f"condition is NOT met, respond with exactly: [SILENT_CHECK]\n"
            )

            # 4. Acquire bridge and build tools
            bridge = await app_state.bridge_registry.acquire(str(task.thread_id))
            tools = build_chat_tools(app_state.tools, bridge)

            # 5. Build agent for the thread
            agent = await build_agent_for_thread(
                task.thread_id,
                model_client=app_state.model_client,
                tools=tools,
                system_instructions=scheduled_instructions,
                cfg=settings,
                history=app_state.history,
                runtime=app_state.runtime,
            )

            # 6. Submit to runtime
            msg = Message(
                target=agent.id,
                sender=AgentId(type="proxy", key="job"),
                payload=ChatPayload(
                    message=KernelChatMessage(
                        role=Role.USER, content=[KernelTextBlock(text=task.prompt)]
                    )
                ),
                correlation_id=str(task.thread_id),
            )

            start = time.monotonic()
            await app_state.runtime.register(agent)
            # thread_id=: tags this run so it appears in the thread's history
            # via project_thread() (the EventLog is the single source of
            # truth for conversation history — see serving/stream/history.py
            # — there's no separate steps-table write needed here anymore).
            run_id = await app_state.runtime.submit(
                agent.id, msg, thread_id=str(task.thread_id)
            )

            output_text = ""
            async for entry in app_state.runtime.event_log.tail(run_id):
                kind = entry.kind
                p = entry.payload or {}
                if kind == "text.delta":
                    output_text += p.get("text", "")
                elif kind == "run.completed":
                    break
                elif kind == "run.failed":
                    error = p.get("error", "Agent run failed")
                    raise RuntimeError(error)

            duration_ms = int((time.monotonic() - start) * 1000)
            is_silent = "[SILENT_CHECK]" in output_text

            # 7. Persist run log
            run = ScheduledTaskRun(
                task_id=task.id,
                status="silent" if is_silent else "success",
                output_summary=output_text[:500],
                duration_ms=duration_ms,
                was_silent=is_silent,
            )
            db.add(run)

            # The run's user.message/text.delta are already durably in the
            # EventLog (ReActAgent logs them unconditionally) and will show
            # up via project_thread() since the run is thread_id-tagged
            # above — no separate persistence needed. The old "don't show
            # silent monitoring checks in chat" filter is now a display-time
            # concern (skip an assistant turn whose text is exactly
            # "[SILENT_CHECK]") rather than a write-time one, since the
            # EventLog can't be filtered retroactively — see
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
                await db.commit()
            except Exception as db_exc:
                logger.error(
                    "Failed to persist failed run log for task %s: %s", task_id, db_exc
                )


async def load_active_tasks_into_scheduler(
    scheduler: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Load active scheduled tasks from database and schedule them in TriggerScheduler."""
    logger.info("Loading active scheduled tasks into trigger scheduler...")
    async with session_factory() as db:
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
