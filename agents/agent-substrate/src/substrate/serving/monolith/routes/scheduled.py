"""Scheduled tasks endpoints for the monolith chat server API.

Routes:
  POST   /scheduled              – create scheduled task
  GET    /scheduled              – list scheduled tasks
  GET    /scheduled/{id}         – get scheduled task detail
  GET    /scheduled/{id}/runs    – get run history for task (paginated)
  PATCH  /scheduled/{id}         – update scheduled task config/status
  DELETE /scheduled/{id}         – delete scheduled task + thread
  POST   /scheduled/{id}/run     – manually execute scheduled task now (async)
  POST   /scheduled/parse        – parse natural language scheduling request
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.logger import setup_logging
from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.models import ScheduledTask, ScheduledTaskRun, Thread
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.monolith.schemas import (
    ScheduledTaskCreate,
    ScheduledTaskUpdate,
    ScheduledTaskOut,
    ScheduledTaskRunOut,
    ScheduledTaskParseRequest,
    ScheduledTaskParseResponse,
    ScheduledTaskFeedbackRequest,
)
from substrate.serving.monolith.services.scheduled_service import execute_scheduled_task
from substrate.serving.monolith.services.thread_service import create_thread

logger = setup_logging()

router = APIRouter(
    prefix="/scheduled",
    tags=["scheduled"],
    dependencies=[Depends(get_current_user)],
)


def validate_schedule(cron_expression: str, kind: str) -> None:
    """Validate that the schedule expression is legal for APScheduler."""
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    try:
        if kind == "cron":
            CronTrigger.from_crontab(cron_expression)
        else:
            int(cron_expression)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid schedule expression '{cron_expression}' for kind '{kind}': {exc}",
        )


@router.post("", response_model=ScheduledTaskOut, status_code=status.HTTP_201_CREATED)
async def create_scheduled_task_endpoint(
    body: ScheduledTaskCreate,
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Create a new persistent scheduled task."""
    validate_schedule(body.cron_expression, body.kind)

    # 1. Create a dedicated thread for the scheduled task
    thread = await create_thread(
        db,
        name=body.name,
        tags=["scheduled_task"],
    )

    # 2. Save scheduled task definition
    task = ScheduledTask(
        name=body.name,
        prompt=body.prompt,
        cron_expression=body.cron_expression,
        kind=body.kind,
        thread_id=thread.id,
        status="active",
        lookback_runs=body.lookback_runs,
        task_type=body.task_type,
        auto_disable=body.auto_disable,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # 3. Schedule task job in APScheduler
    try:
        await ctx.trigger_scheduler.add_scheduled_task(
            task.id,
            task.cron_expression,
            task.kind,
        )
    except Exception as exc:
        logger.error("Failed to register scheduled task job: %s", exc)
        # We still return the task as active; the process restart will reload it

    next_run_at = await ctx.trigger_scheduler.get_next_run_time(task.id)

    return ScheduledTaskOut(
        id=task.id,
        user_id=task.user_id,
        name=task.name,
        prompt=task.prompt,
        cron_expression=task.cron_expression,
        kind=task.kind,
        thread_id=task.thread_id,
        status=task.status,
        lookback_runs=task.lookback_runs,
        task_type=task.task_type,
        auto_disable=task.auto_disable,
        created_at=task.created_at,
        updated_at=task.updated_at,
        next_run_at=next_run_at,
        recent_runs=[],
    )


@router.get("", response_model=List[ScheduledTaskOut])
async def list_scheduled_tasks(
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """List all scheduled tasks with execution status preview."""
    stmt = select(ScheduledTask)
    if status_filter:
        stmt = stmt.where(ScheduledTask.status == status_filter)
    stmt = stmt.order_by(ScheduledTask.created_at.desc())

    result = await db.execute(stmt)
    tasks = result.scalars().all()

    tasks_out = []
    for task in tasks:
        # Get next scheduled run time
        next_run_at = await ctx.trigger_scheduler.get_next_run_time(task.id)

        # Get last 3 runs for preview
        stmt_runs = (
            select(ScheduledTaskRun)
            .where(ScheduledTaskRun.task_id == task.id)
            .order_by(ScheduledTaskRun.executed_at.desc())
            .limit(3)
        )
        runs_res = await db.execute(stmt_runs)
        recent_runs = list(runs_res.scalars().all())

        tasks_out.append(
            ScheduledTaskOut(
                id=task.id,
                user_id=task.user_id,
                name=task.name,
                prompt=task.prompt,
                cron_expression=task.cron_expression,
                kind=task.kind,
                thread_id=task.thread_id,
                status=task.status,
                lookback_runs=task.lookback_runs,
                task_type=task.task_type,
                auto_disable=task.auto_disable,
                created_at=task.created_at,
                updated_at=task.updated_at,
                next_run_at=next_run_at,
                recent_runs=[ScheduledTaskRunOut.model_validate(r) for r in recent_runs],
            )
        )

    return tasks_out


@router.get("/{task_id}", response_model=ScheduledTaskOut)
async def get_scheduled_task(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Get details of a single scheduled task with last 5 runs."""
    task = await db.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    next_run_at = await ctx.trigger_scheduler.get_next_run_time(task.id)

    stmt_runs = (
        select(ScheduledTaskRun)
        .where(ScheduledTaskRun.task_id == task.id)
        .order_by(ScheduledTaskRun.executed_at.desc())
        .limit(5)
    )
    runs_res = await db.execute(stmt_runs)
    recent_runs = list(runs_res.scalars().all())

    return ScheduledTaskOut(
        id=task.id,
        user_id=task.user_id,
        name=task.name,
        prompt=task.prompt,
        cron_expression=task.cron_expression,
        kind=task.kind,
        thread_id=task.thread_id,
        status=task.status,
        lookback_runs=task.lookback_runs,
        task_type=task.task_type,
        auto_disable=task.auto_disable,
        created_at=task.created_at,
        updated_at=task.updated_at,
        next_run_at=next_run_at,
        recent_runs=[ScheduledTaskRunOut.model_validate(r) for r in recent_runs],
    )


@router.get("/{task_id}/runs", response_model=List[ScheduledTaskRunOut])
async def list_scheduled_task_runs(
    task_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    include_silent: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve run history log for a task (paginated)."""
    stmt = (
        select(ScheduledTaskRun)
        .where(ScheduledTaskRun.task_id == task_id)
    )
    if not include_silent:
        stmt = stmt.where(ScheduledTaskRun.was_silent == False)

    stmt = stmt.order_by(ScheduledTaskRun.executed_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    runs = result.scalars().all()
    return [ScheduledTaskRunOut.model_validate(r) for r in runs]


@router.patch("/{task_id}", response_model=ScheduledTaskOut)
async def update_scheduled_task_endpoint(
    task_id: uuid.UUID,
    body: ScheduledTaskUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Update a scheduled task's configuration, prompt, or status."""
    task = await db.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    # If updating schedule, validate first
    new_expr = body.cron_expression if body.cron_expression is not None else task.cron_expression
    new_kind = body.kind if body.kind is not None else task.kind
    if body.cron_expression is not None or body.kind is not None:
        validate_schedule(new_expr, new_kind)

    # Apply changes
    if body.name is not None:
        task.name = body.name
    if body.prompt is not None:
        task.prompt = body.prompt
    if body.cron_expression is not None:
        task.cron_expression = body.cron_expression
    if body.kind is not None:
        task.kind = body.kind
    if body.status is not None:
        task.status = body.status
    if body.lookback_runs is not None:
        task.lookback_runs = body.lookback_runs
    if body.auto_disable is not None:
        task.auto_disable = body.auto_disable

    task.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(task)

    # Dynamic scheduler sync
    await ctx.trigger_scheduler.remove_scheduled_task(task.id)
    if task.status == "active":
        try:
            await ctx.trigger_scheduler.add_scheduled_task(
                task.id,
                task.cron_expression,
                task.kind,
            )
        except Exception as exc:
            logger.error("Failed to re-register scheduled task: %s", exc)

    next_run_at = await ctx.trigger_scheduler.get_next_run_time(task.id)

    # Fetch last 3 runs for response
    stmt_runs = (
        select(ScheduledTaskRun)
        .where(ScheduledTaskRun.task_id == task.id)
        .order_by(ScheduledTaskRun.executed_at.desc())
        .limit(3)
    )
    runs_res = await db.execute(stmt_runs)
    recent_runs = list(runs_res.scalars().all())

    return ScheduledTaskOut(
        id=task.id,
        user_id=task.user_id,
        name=task.name,
        prompt=task.prompt,
        cron_expression=task.cron_expression,
        kind=task.kind,
        thread_id=task.thread_id,
        status=task.status,
        lookback_runs=task.lookback_runs,
        task_type=task.task_type,
        auto_disable=task.auto_disable,
        created_at=task.created_at,
        updated_at=task.updated_at,
        next_run_at=next_run_at,
        recent_runs=[ScheduledTaskRunOut.model_validate(r) for r in recent_runs],
    )


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scheduled_task_endpoint(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Delete a scheduled task and its associated thread/history."""
    task = await db.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    # 1. Unschedule APScheduler job
    await ctx.trigger_scheduler.remove_scheduled_task(task.id)

    # 2. Delete task (thread deletion will cascade delete steps/runs/etc. in SQLite/Postgres)
    # But since SQLite/PG cascade is set, deleting Thread is enough. Let's delete both:
    thread_id = task.thread_id
    await db.delete(task)
    
    # Check if thread exists, delete it
    thread = await db.get(Thread, thread_id)
    if thread:
        await db.delete(thread)

    await db.commit()


@router.post("/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_scheduled_task_now(
    task_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Manually trigger execution of a scheduled task immediately (async)."""
    task = await db.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    # Submit execution to asyncio background task to avoid blocking API response
    # We pass the app state from request.app.state
    # In FastAPI endpoints, ctx is a ServerDependencies object, which contains session_factory
    # Wait, we can get request.app.state from request directly, or from our ServerDependencies/db
    session_factory = ctx.session_factory
    if not session_factory:
        # Fallback to app.state via a workaround or direct inject
        # Since ServerDependencies has session_factory (which we verified in dependencies.py), we are good!
        pass

    async def _run_bg():
        # Retrieve a fresh session factory to execute the task run
        from fastapi import FastAPI
        # We need the app state. In fastapi endpoint, we can access it via fastapi.requests,
        # but since we already have the session_factory on ctx and the app_state is just request.app.state,
        # let's write a simple lambda/callable that gets app_state.
        # Wait, how do we get app_state? Let's check how ctx is initialized in app.py.
        # ctx is created from app.state attributes!
        # So we can pass ctx as app_state directly since it has all the attributes required:
        # model_client, tools, system_instructions, history, runtime, vector_store, etc.
        # Yes! ServerDependencies has EXACTLY the same attributes needed by execute_scheduled_task!
        # Let's double check. Yes, execute_scheduled_task needs:
        # app_state.system_instructions, app_state.model_client, app_state.tools, app_state.history, app_state.runtime, app_state.bridge_registry
        # ServerDependencies has all of these! This is amazingly clean!
        try:
            await execute_scheduled_task(
                task_id,
                session_factory=session_factory,
                app_state=ctx,
            )
        except Exception as bg_err:
            logger.error("Failed manual scheduled task run: %s", bg_err)

    asyncio.create_task(_run_bg())
    return {"status": "triggered"}


@router.post("/parse", response_model=ScheduledTaskParseResponse)
async def parse_schedule_endpoint(
    body: ScheduledTaskParseRequest,
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Parse a natural language query into scheduled task configuration."""
    from substrate.kernel import ChatMessage, TextBlock
    from substrate.kernel.llm import GenerationOptions

    system_instructions = (
        "You are an expert natural language scheduling assistant. "
        "You parse the user's intent to schedule a recurring or interval-based task "
        "into a clean JSON structure.\n\n"
        "Return ONLY a JSON object matching this schema:\n"
        "{\n"
        "  \"name\": \"A short, punchy name for the task (e.g. 'Daily AI News Brief')\",\n"
        "  \"prompt\": \"The detailed agent prompt or instructions. Instruct the agent what tools to run (e.g. web search) and what to produce.\",\n"
        "  \"cron_expression\": \"A valid cron expression (e.g. '0 8 * * *' for daily at 8am, '0 9 * * 1' for weekly monday at 9am, or '* * * * *' for every minute) or interval in seconds as a string (e.g. '3600' for hourly)\",\n"
        "  \"kind\": \"cron\" or \"interval\",\n"
        "  \"task_type\": \"report\" | \"monitor\" | \"reminder\" | \"learning\"\n"
        "}\n\n"
        "Do NOT write any preamble, explanation, or markdown block wrappers. Return raw JSON."
    )

    messages = [
        ChatMessage(role="user", content=[TextBlock(text=f"Parse this request: '{body.text}'")])
    ]

    try:
        resp = await ctx.model_client.generate(
            messages,
            options=GenerationOptions(system_instructions=system_instructions),
        )
        response_text = " ".join(
            b.text for b in resp.content if isinstance(b, TextBlock)
        ).strip()

        data = None
        try:
            data = json.loads(response_text)
        except Exception:
            pass

        if not data:
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
            else:
                json_match = re.search(r"(\{.*?\})", response_text, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group(1))

        if not data or not isinstance(data, dict):
            raise ValueError(f"Could not parse valid JSON from LLM: {response_text}")

        # Basic defaults/fallback validation
        cron_expr = data.get("cron_expression", "0 8 * * *")
        kind = data.get("kind", "cron")
        task_type = data.get("task_type", "report")

        # Basic canonicalization
        if kind not in ("cron", "interval"):
            kind = "cron"
        if task_type not in ("report", "monitor", "reminder", "learning"):
            task_type = "report"

        return ScheduledTaskParseResponse(
            name=data.get("name", "Scheduled Task"),
            prompt=data.get("prompt", body.text),
            cron_expression=cron_expr,
            kind=kind,
            task_type=task_type,
        )
    except Exception as exc:
        logger.exception("NLP schedule parsing failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse input: {exc}",
        )


@router.post("/{task_id}/feedback", status_code=status.HTTP_201_CREATED)
async def add_scheduled_task_feedback(
    task_id: uuid.UUID,
    body: ScheduledTaskFeedbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """Add user feedback/message directly to a scheduled task's thread for lookback context."""
    task = await db.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Scheduled task not found")

    from substrate.serving.monolith.services.agent_service import persist_user_message
    await persist_user_message(db, task.thread_id, body.content)
    await db.commit()
    return {"status": "success"}

